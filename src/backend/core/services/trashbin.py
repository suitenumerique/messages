"""Trashbin service: permanent deletion of trashed and spam messages.

The **trashbin** is the union of trashed and spam messages —
``is_trashed OR is_spam``. The product treats the two the same way: both are
"in the bin", both are surfaced by their own sidebar folder (Trash / Spam), and
both are permanently removed from here.

Two things empty the bin:

- ``cleanup_trashbin_task`` — a daily sweep that hard-deletes items older than
  ``settings.TRASHBIN_CUTOFF_DAYS``.
- ``empty_trashbin`` — a manual, per-folder "empty now" triggered from the UI
  (gated by ``settings.TRASHBIN_ALLOW_EMPTY`` at the API layer).

Deleting a ``Message`` cascades to its attachments and schedules its blobs for
GC via ``post_delete`` signals (see ``core/signals.py``); the hourly blob GC
task reclaims the underlying storage. Nothing extra is needed here.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from celery.utils.log import get_task_logger

from core import models
from core.services.storage import (
    invalidate_mailbox_storage,
    invalidate_mailbox_storage_ids,
)

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)

# Message filters selecting each half of the trashbin. Kept per-folder ("trashed"
# vs "spam") so the manual empty can act on exactly the folder the user is in.
TRASHBIN_SCOPE_FILTERS = {
    "trashed": Q(is_trashed=True),
    "spam": Q(is_spam=True),
}
TRASHBIN_SCOPES = list(TRASHBIN_SCOPE_FILTERS)


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


def empty_trashbin(mailbox, scope, user, thread_ids=None, message_ids=None):
    """Permanently delete a mailbox's trashbin messages for one folder.

    ``scope`` is ``"trashed"`` or ``"spam"``. ``thread_ids`` / ``message_ids``
    narrow the deletion to specific items; both empty (the default) empties the
    whole folder. When both are given they intersect, matching
    ``ThreadViewSet.bulk_delete``.

    Deleting a hand-picked message and wiping the folder are the same
    privilege — an irreversible hard delete of trashbin content — so both run
    through here, behind the single ``CAN_EMPTY_TRASH`` gate the caller
    enforces. Splitting per-message deletion onto ``bulk_delete`` instead would
    have put it outside that gate entirely (that action has no mailbox context
    and no ability check), letting a ``TRASHBIN_ALLOW_EMPTY=never`` deployment
    be emptied one selection at a time.

    Only threads ``user`` can fully edit through this mailbox (EDITOR
    thread-access + CAN_EDIT mailbox role) are touched, so this can never
    hard-delete a shared thread the mailbox merely views. Who may do it at all
    is the separate gate above: ``settings.TRASHBIN_ALLOW_EMPTY`` / the mailbox
    ability, enforced by the caller.

    Returns the number of messages deleted.
    """
    accessible_thread_ids = models.ThreadAccess.objects.editable_by(
        user, mailbox_id=mailbox.id
    ).values_list("thread_id", flat=True)
    messages_qs = models.Message.objects.filter(
        TRASHBIN_SCOPE_FILTERS[scope],
        thread_id__in=accessible_thread_ids,
    )
    if thread_ids:
        messages_qs = messages_qs.filter(thread_id__in=thread_ids)
    if message_ids:
        messages_qs = messages_qs.filter(id__in=message_ids)

    deleted_count = permanently_delete_messages(messages_qs)
    # Freeing space is the one storage change a user actively watches, so drop
    # the cached usage now instead of waiting out the TTL — the gauge reflects
    # the emptied trashbin immediately on the next read.
    invalidate_mailbox_storage(mailbox)
    return deleted_count


# Trashbin items are aged by when they *entered the bin* (``trashed_at``, which
# the trash and spam flag paths both set), falling back to ``created_at`` for
# rows written before that convention or by the importer — so nothing is ever
# permanently exempt from the sweep, and a freshly-binned old message still gets
# its full grace period.
_BINNED_AT = Coalesce("trashed_at", "created_at")


@celery_app.task
def cleanup_trashbin_task(batch_size=1000):
    """Permanently delete trashbin items (is_trashed OR is_spam) whose bin
    entry is older than TRASHBIN_CUTOFF_DAYS.

    ``TRASHBIN_CUTOFF_DAYS = 0`` disables the sweep (see the setting): items are
    then kept until someone empties the folder by hand.

    Deletes in bounded batches: a global sweep must not hold one table-wide
    transaction or fire an unbounded per-thread ``update_stats`` reindex burst.
    Each batch is its own transaction (via ``permanently_delete_messages``); the
    filter re-runs each pass, so freshly deleted rows drop out naturally.
    """
    cutoff_days = settings.TRASHBIN_CUTOFF_DAYS
    if not cutoff_days:
        logger.info("cleanup_trashbin_task disabled (TRASHBIN_CUTOFF_DAYS=0)")
        return {"deleted_count": 0}

    cutoff = timezone.now() - timedelta(days=cutoff_days)
    base_qs = (
        models.Message.objects.filter(Q(is_trashed=True) | Q(is_spam=True))
        .annotate(binned_at=_BINNED_AT)
        .filter(binned_at__lt=cutoff)
        # Drop Message.Meta.ordering ("-created_at"): the sweep does not care in
        # what order it deletes, and keeping it would make every batch sort the
        # entire matching set just to take the first ``batch_size`` rows.
        .order_by()
    )

    deleted_count = 0
    # Mailboxes whose usage changed, so their cached storage figure can be
    # dropped at the end rather than waiting out STORAGE_USAGE_CACHE_TTL — the
    # sweep is the single largest space-freeing event there is.
    touched_mailbox_ids = set()
    while True:
        batch_ids = list(base_qs.values_list("pk", flat=True)[:batch_size])
        if not batch_ids:
            break
        # Collected before the delete: the ThreadAccess rows are unreachable
        # through the messages once they are gone.
        touched_mailbox_ids.update(
            models.ThreadAccess.objects.filter(
                thread__messages__pk__in=batch_ids
            ).values_list("mailbox_id", flat=True)
        )
        deleted_count += permanently_delete_messages(
            models.Message.objects.filter(pk__in=batch_ids)
        )

    invalidate_mailbox_storage_ids(touched_mailbox_ids)

    logger.info(
        "cleanup_trashbin_task deleted %d messages older than %d days "
        "across %d mailboxes",
        deleted_count,
        cutoff_days,
        len(touched_mailbox_ids),
    )
    return {"deleted_count": deleted_count}
