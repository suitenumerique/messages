"""Trashbin service: permanent deletion of trashed and spam messages.

The **trashbin** is the union of trashed and spam messages —
``is_trashed OR is_spam``. The product treats the two the same way: both are
"in the bin", both are surfaced by their own sidebar folder (Trash / Spam), and
both are permanently removed from here.

Trash is per message. Spam is flagged per message too, but the Spam folder
lists conversations: a thread is spam as soon as one of its messages is
(``Thread.update_stats``), so it is purged as a whole — its unflagged messages
included — and nothing is left behind to bring it back to the inbox. The sweep
therefore ages it by its most recent message, flagged or not.

Two things empty the bin:

- ``cleanup_trashbin_task`` — a daily sweep that hard-deletes items older than
  ``settings.TRASHBIN_CUTOFF_DAYS``.
- ``empty_trashbin`` — a manual, per-folder "empty now" triggered from the UI
  (gated by ``settings.TRASHBIN_ALLOW_EMPTY`` at the API layer). Up to
  ``EMPTY_TRASHBIN_SYNC_LIMIT`` messages it runs within the request; beyond,
  the API hands it to ``empty_trashbin_task``.

Deleting a ``Message`` cascades to its attachments and schedules its blobs for
GC via ``post_delete`` signals (see ``core/signals.py``); the hourly blob GC
task reclaims the underlying storage. Nothing extra is needed here.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import (
    Case,
    DateTimeField,
    Exists,
    F,
    Max,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from celery.utils.log import get_task_logger

from core import models
from core.entitlements import invalidate_mailbox_entitlements
from core.services.storage import (
    invalidate_mailbox_storage,
    invalidate_mailbox_storage_ids,
)

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)

TRASHBIN_SCOPES = ["trashed", "spam"]

# Above this many messages, emptying a folder leaves the request for a Celery
# task: deleting runs ``post_delete`` signals per message and recomputes the
# stats of every touched thread, which outgrows an HTTP request.
EMPTY_TRASHBIN_SYNC_LIMIT = 1000

# Threads deleted per transaction when emptying a folder, bounding its locks
# and the ``update_stats`` work it holds them for.
EMPTY_TRASHBIN_THREAD_BATCH_SIZE = 100

# The two message flags that make up the trashbin, and the other half of the
# pair — ``trashed_at`` belongs to the pair, not to either flag alone.
_TRASHBIN_FLAG_SIBLING = {"trashed": "is_spam", "spam": "is_trashed"}


def trashbin_scope_filter(scope):
    """Message filter selecting one folder of the trashbin.

    Kept per-folder ("trashed" vs "spam") so the manual empty can act on
    exactly the folder the user is in. The spam folder lists conversations, so
    it selects every message of a thread holding spam — not only the flagged
    ones — and emptying it never leaves a stray reply behind to resurface in
    the inbox.
    """
    if scope == "trashed":
        return Q(is_trashed=True)
    if scope == "spam":
        return Q(
            thread_id__in=models.Message.objects.filter(is_spam=True).values(
                "thread_id"
            )
        )
    raise ValueError(f"Unknown trashbin scope: {scope}")


def trashbin_timestamp(flag, value, current_time):
    """Build the ``trashed_at`` value for a trashbin flag toggle.

    ``trashed_at`` records when a message entered the *trashbin*, and the
    trashbin is ``is_trashed OR is_spam``. It therefore has to outlive either
    flag on its own, so toggling one half must not write the column
    unconditionally:

    - Setting a flag keeps any timestamp already there (``Coalesce``), so a
      message that has sat in the bin for 20 days does not restart its
      retention clock just because the other half got flipped — nor can a user
      postpone deletion indefinitely by toggling spam on and off.
    - Clearing a flag only clears the timestamp when the *other* half is not
      set. Otherwise the message stays in the bin with a NULL ``trashed_at``,
      the cutoff sweep falls back to ageing it by ``created_at``, and old mail
      is permanently deleted on the next nightly run instead of getting its
      full grace period.
    """
    sibling = _TRASHBIN_FLAG_SIBLING[flag]
    if value:
        return Coalesce(
            F("trashed_at"), Value(current_time), output_field=DateTimeField()
        )
    return Case(
        When(**{sibling: True}, then=F("trashed_at")),
        default=Value(None),
        output_field=DateTimeField(),
    )


def permanently_delete_messages(messages_qs):
    """Hard-delete the given messages and reconcile their threads.

    A thread emptied by the deletion is removed; a thread that still has
    messages has its denormalized stats recomputed so it drops out of the
    folder filters. Mirrors ``ThreadViewSet.bulk_delete``.

    Returns the number of messages deleted.
    """
    with transaction.atomic():
        affected_thread_ids = set(messages_qs.values_list("thread_id", flat=True))
        # Count before deletion: delete()'s return value also folds in cascaded
        # rows (recipients, attachments), not just the messages themselves.
        deleted_count = messages_qs.count()
        messages_qs.delete()

        for thread in models.Thread.objects.filter(pk__in=affected_thread_ids):
            if thread.messages.exists():
                thread.update_stats()
            else:
                thread.delete()

    return deleted_count


def trashbin_messages(mailbox, scope, user, thread_ids=None, message_ids=None):
    """Return the messages ``empty_trashbin`` would delete, as a queryset.

    ``scope`` is ``"trashed"`` or ``"spam"``. ``thread_ids`` / ``message_ids``
    narrow the selection to specific items; both empty (the default) selects
    the whole folder. When both are given they intersect, matching
    ``ThreadViewSet.bulk_delete``.

    Only threads ``user`` can fully edit through this mailbox (EDITOR
    thread-access + CAN_EDIT mailbox role) are selected, so emptying can never
    hard-delete a shared thread the mailbox merely views.
    """
    accessible_thread_ids = models.ThreadAccess.objects.editable_by(
        user, mailbox_id=mailbox.id
    ).values_list("thread_id", flat=True)
    messages_qs = models.Message.objects.filter(
        trashbin_scope_filter(scope),
        thread_id__in=accessible_thread_ids,
    )
    if thread_ids:
        messages_qs = messages_qs.filter(thread_id__in=thread_ids)
    if message_ids:
        messages_qs = messages_qs.filter(id__in=message_ids)
    return messages_qs.order_by()


def empty_trashbin(  # pylint: disable=too-many-arguments
    mailbox,
    scope,
    user,
    thread_ids=None,
    message_ids=None,
    batch_size=EMPTY_TRASHBIN_THREAD_BATCH_SIZE,
):
    """Permanently delete a mailbox's trashbin messages for one folder.

    Selects through ``trashbin_messages`` (see there for the arguments).

    Deleting a hand-picked message and wiping the folder are the same
    privilege — an irreversible hard delete of trashbin content — so both run
    through here, behind the single ``CAN_EMPTY_TRASH`` gate the caller
    enforces. Splitting per-message deletion onto ``bulk_delete`` instead would
    have put it outside that gate entirely (that action has no mailbox context
    and no ability check), letting a ``TRASHBIN_ALLOW_EMPTY=never`` deployment
    be emptied one selection at a time. Who may do it at all is that separate
    gate: ``settings.TRASHBIN_ALLOW_EMPTY`` / the mailbox ability.

    Deletes ``batch_size`` threads per transaction. Batches are whole threads
    for both folders: a spam batch splitting a conversation could delete its
    spam first, after which the thread no longer holds spam and its other
    messages would drop out of the selection — and back into the inbox.

    Returns the number of messages deleted.
    """
    messages_qs = trashbin_messages(
        mailbox, scope, user, thread_ids=thread_ids, message_ids=message_ids
    )
    deleted_count = 0
    while True:
        batch_thread_ids = list(
            messages_qs.values_list("thread_id", flat=True).distinct()[:batch_size]
        )
        if not batch_thread_ids:
            break
        deleted_count += permanently_delete_messages(
            messages_qs.filter(thread_id__in=batch_thread_ids)
        )

    # Freeing space is the one storage change a user actively watches, so drop
    # the cached usage now instead of waiting out the TTL — the gauge reflects
    # the emptied trashbin immediately on the next read. The entitlements cache
    # carries that usage too (DeployCenter echoes it back with the limits).
    invalidate_mailbox_storage(mailbox)
    invalidate_mailbox_entitlements(mailbox)
    return deleted_count


@celery_app.task
def empty_trashbin_task(  # pylint: disable=too-many-arguments
    mailbox_id, scope, user_id, thread_ids=None, message_ids=None
):
    """Run ``empty_trashbin`` outside the request, for large folders.

    The API checks the ``CAN_EMPTY_TRASH`` ability before dispatching; the
    deletion itself still only reaches threads ``user`` can edit. Its result
    is read back through the task status endpoint.
    """
    deleted_count = empty_trashbin(
        models.Mailbox.objects.get(pk=mailbox_id),
        scope,
        models.User.objects.get(pk=user_id),
        thread_ids=thread_ids,
        message_ids=message_ids,
    )
    return {"deleted_count": deleted_count}


# Trashbin items are aged by when they *entered the bin* (``trashed_at``, which
# the flag, inbound and import paths all set), falling back to ``created_at``
# for rows written before that convention — so nothing is ever permanently
# exempt from the sweep, and a freshly-binned old message still gets its full
# grace period.
_BINNED_AT = Coalesce("trashed_at", "created_at")


def _delete_expired(condition, touched_mailbox_ids):
    """Permanently delete the messages matching ``condition``.

    Records the mailboxes whose usage changes in ``touched_mailbox_ids`` —
    before the delete, as the ThreadAccess rows are unreachable through the
    messages once they are gone. Returns the number of messages deleted.
    """
    messages_qs = models.Message.objects.filter(condition)
    touched_mailbox_ids.update(
        models.ThreadAccess.objects.filter(
            thread_id__in=messages_qs.values("thread_id")
        ).values_list("mailbox_id", flat=True)
    )
    return permanently_delete_messages(messages_qs)


@celery_app.task
def cleanup_trashbin_task(batch_size=1000):
    """Permanently delete trashbin items whose bin entry is older than
    TRASHBIN_CUTOFF_DAYS.

    ``TRASHBIN_CUTOFF_DAYS = 0`` disables the sweep (see the setting): items are
    then kept until someone empties the folder by hand.

    Trashed messages expire one by one. Spam expires per conversation, as the
    manual "Empty spam" does, its unflagged messages included — expiring only
    the spam would bring the rest of the thread back to the inbox. A thread
    goes once *every* message is older than the cutoff: a clean reply that
    just landed in a spam conversation keeps it whole, so no recent mail is
    ever swept away along with old spam.

    Deletes in bounded batches: a global sweep must not hold one table-wide
    transaction or fire an unbounded per-thread ``update_stats`` reindex burst.
    Each batch is its own transaction (via ``permanently_delete_messages``); the
    filters re-run each pass, so freshly deleted rows drop out naturally. Spam
    batches are whole threads, so a thread is never left half-purged.
    """
    cutoff_days = settings.TRASHBIN_CUTOFF_DAYS
    if not cutoff_days:
        logger.info("cleanup_trashbin_task disabled (TRASHBIN_CUTOFF_DAYS=0)")
        return {"deleted_count": 0}

    cutoff = timezone.now() - timedelta(days=cutoff_days)
    # ``Thread.is_spam`` is denormalized from the message flags: requiring it
    # *and* a spam message means a thread whose stats failed to update is
    # kept, never swept on a flag that no longer holds.
    expired_spam_thread_ids = (
        models.Thread.objects.filter(
            Exists(models.Message.objects.filter(thread=OuterRef("pk"), is_spam=True)),
            is_spam=True,
        )
        .values("pk")
        .annotate(
            last_activity_at=Max(
                Coalesce("messages__trashed_at", "messages__created_at")
            )
        )
        .filter(last_activity_at__lt=cutoff)
        .values_list("pk", flat=True)
    )
    # ``order_by()`` drops Message.Meta.ordering ("-created_at"): the sweep does
    # not care in what order it deletes, and keeping it would make every batch
    # sort the entire matching set just to take the first ``batch_size`` rows.
    expired_trashed_ids = (
        models.Message.objects.filter(is_trashed=True)
        .annotate(binned_at=_BINNED_AT)
        .filter(binned_at__lt=cutoff)
        .order_by()
        .values_list("pk", flat=True)
    )

    deleted_count = 0
    # Mailboxes whose usage changed, so their cached storage figure can be
    # dropped at the end rather than waiting out STORAGE_USAGE_CACHE_TTL — the
    # sweep is the single largest space-freeing event there is.
    touched_mailbox_ids = set()
    while True:
        thread_ids = list(expired_spam_thread_ids[:batch_size])
        if not thread_ids:
            break
        deleted_count += _delete_expired(
            Q(thread_id__in=thread_ids), touched_mailbox_ids
        )
    while True:
        message_ids = list(expired_trashed_ids[:batch_size])
        if not message_ids:
            break
        deleted_count += _delete_expired(Q(pk__in=message_ids), touched_mailbox_ids)

    invalidate_mailbox_storage_ids(touched_mailbox_ids)

    logger.info(
        "cleanup_trashbin_task deleted %d messages older than %d days "
        "across %d mailboxes",
        deleted_count,
        cutoff_days,
        len(touched_mailbox_ids),
    )
    return {"deleted_count": deleted_count}
