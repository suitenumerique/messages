"""Signal handlers for core models."""
# pylint: disable=unused-argument

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from core import enums, models
from core.services.blob_gc import schedule_for_gc
from core.services.identity.keycloak import (
    sync_mailbox_to_keycloak_user,
    sync_maildomain_to_keycloak_group,
)
from core.services.search.coalescer import (
    enqueue_message_delete,
    enqueue_thread_delete,
    enqueue_thread_reindex,
)
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

logger = logging.getLogger(__name__)


def _schedule_thread_reindex(thread_id):
    """Route a thread reindex through the active deferrer, or the Redis queue.

    Thread reindexing has two modes depending on the call context:

    - Inside a `ThreadReindexDeferrer.defer()` scope (bulk flows like imports
      or migrations), the ID is collected and a single batched
      `bulk_reindex_threads_task` is enqueued at scope exit — avoiding one
      Celery task per row.
    - Outside that scope, the ID is pushed to the coalescing buffer drained
      by `process_pending_reindex_task`. The enqueue is wrapped in
      `transaction.on_commit` so a rolled-back save never leaves a phantom
      reindex pointing at a row that was never persisted.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    if ThreadReindexDeferrer.defer_item(thread_id):
        return

    transaction.on_commit(lambda tid=thread_id: enqueue_thread_reindex(tid))


@receiver(post_save, sender=models.MailDomain)
def create_dkim_key(sender, instance, created, **kwargs):
    """Create a DKIM key for a new MailDomain."""
    if created:
        instance.generate_dkim_key()


@receiver(post_save, sender=models.MailDomain)
def sync_maildomain_to_keycloak(sender, instance, created, **kwargs):
    """Sync MailDomain to Keycloak as a group when saved."""
    if not instance.identity_sync or settings.IDENTITY_PROVIDER != "keycloak":
        return
    sync_maildomain_to_keycloak_group(instance)


@receiver(post_save, sender=models.Mailbox)
def sync_mailbox_to_keycloak(sender, instance, created, **kwargs):
    """Sync Mailbox to Keycloak as a user when saved."""
    if not instance.domain.identity_sync or settings.IDENTITY_PROVIDER != "keycloak":
        return

    # Ensure the maildomain group exists first
    sync_maildomain_to_keycloak_group(instance.domain)
    # Then sync the mailbox as a user
    sync_mailbox_to_keycloak_user(instance)


@receiver(post_save, sender=models.Message)
def index_message_post_save(sender, instance, created, **kwargs):
    """Schedule a reindex for the parent thread when a message is saved."""
    _schedule_thread_reindex(instance.thread_id)


@receiver(post_save, sender=models.MessageRecipient)
def index_message_recipient_post_save(sender, instance, created, **kwargs):
    """Reindex the parent thread when a recipient is updated after creation.

    On create, ``index_message_post_save`` already covers the new message —
    triggering here would schedule a redundant reindex per recipient. On
    update (e.g. delivery_status change after send), recipient data is
    denormalized into the Message document, so the parent thread needs a
    refresh.
    """
    if created:
        return

    _schedule_thread_reindex(instance.message.thread_id)


@receiver(post_save, sender=models.MessageRecipient)
def update_thread_stats_on_delivery_status_change(sender, instance, **kwargs):
    """
    Update thread stats when a MessageRecipient delivery_status changes.

    Only triggers for outbound messages (is_sender=True) that are not drafts
    or trashed, since only those affect thread delivery stats.

    Supports batching via ThreadStatsUpdateDeferrer.defer() context manager.
    """
    update_fields = kwargs.get("update_fields")

    # Only proceed if delivery_status was updated (or if update_fields is None,
    # meaning all fields were saved)
    if update_fields is not None and "delivery_status" not in update_fields:
        return

    message = instance.message

    # Only update stats for outbound messages that are not drafts or trashed
    # (matches the filter in Thread.update_stats())
    if not message.is_sender or message.is_draft or message.is_trashed:
        return

    thread = message.thread

    # If deferring is active, mark thread for later update
    if ThreadStatsUpdateDeferrer.defer_item(thread.id):
        return

    # Otherwise update immediately
    try:
        thread.update_stats()
    # pylint: disable=broad-exception-caught
    except Exception:
        logger.exception("Failed to update stats for thread %s", thread.id)


@receiver(post_save, sender=models.Thread)
def index_thread_post_save(sender, instance, created, **kwargs):
    """Schedule a reindex for the thread after it's saved."""
    _schedule_thread_reindex(instance.id)


# When a row that FKs a Blob is deleted, push the blob_id into the
# GC candidate set; ``gc_orphan_blobs_task`` re-checks references
# under the per-sha advisory lock and deletes the Blob row + cleans
# up S3 inline if no references remain.


@receiver(post_delete, sender=models.Message)
def schedule_message_blobs_for_gc(sender, instance, **kwargs):
    """Push ``Message.blob`` and ``Message.draft_blob`` ids into the GC set."""
    schedule_for_gc(instance.blob_id)
    schedule_for_gc(instance.draft_blob_id)


@receiver(post_delete, sender=models.Attachment)
def schedule_attachment_blob_for_gc(sender, instance, **kwargs):
    """Push ``Attachment.blob`` id into the GC set."""
    schedule_for_gc(instance.blob_id)


@receiver(post_delete, sender=models.MessageTemplate)
def schedule_template_blob_for_gc(sender, instance, **kwargs):
    """Push ``MessageTemplate.blob`` id into the GC set."""
    schedule_for_gc(instance.blob_id)


@receiver(post_delete, sender=models.InboundMessage)
def schedule_inbound_message_blob_for_gc(sender, instance, **kwargs):
    """Push ``InboundMessage.blob`` id into the GC set.

    Internal mail references the sender's blob while in flight; once the
    task deletes the queue row the blob may have become collectable
    (no-op when ``blob_id`` is None, i.e. external inline-bytes rows).
    """
    schedule_for_gc(instance.blob_id)


@receiver(post_delete, sender=models.Message)
def delete_message_from_index(sender, instance, **kwargs):
    """Enqueue a targeted OpenSearch delete for the message child document.

    Pushes ``(thread_id, message_id)`` to the dedicated delete-message set
    so ``bulk_delete_messages_task`` can later issue a ``bulk delete by
    _id`` (with the parent ``thread_id`` as routing). Cheaper for the
    cluster than reindexing the thread and letting a ``delete_by_query``
    orphan purge catch up, and correct because Django fires ``post_delete``
    for both direct and cascaded message deletions.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    thread_id = str(instance.thread_id)
    message_id = str(instance.id)
    transaction.on_commit(
        lambda tid=thread_id, mid=message_id: enqueue_message_delete(tid, mid)
    )


@receiver(post_delete, sender=models.Thread)
def delete_thread_from_index(sender, instance, **kwargs):
    """Enqueue an async OpenSearch delete for the thread parent document.

    Only the parent doc is queued here — child message docs are picked up
    by ``delete_message_from_index`` via the cascaded ``post_delete``
    signal Django fires for each child. Splitting parent and children into
    two cheap ``bulk delete by _id`` requests avoids an all-in-one
    ``delete_by_query`` on ``thread_id``.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    thread_id = str(instance.id)
    transaction.on_commit(lambda tid=thread_id: enqueue_thread_delete(tid))


@receiver(post_save, sender=models.ThreadAccess)
def update_mailbox_flags_on_access_save(sender, instance, created, **kwargs):
    """Schedule a thread reindex when ThreadAccess read/starred state changes.

    The thread document carries ``unread_mailboxes`` / ``starred_mailboxes``
    fields derived from ``ThreadAccess`` rows; a full thread reindex via the
    coalescer keeps them consistent without a dedicated partial-update task.
    """
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not (
        {"read_at", "starred_at"} & set(update_fields)
    ):
        return

    _schedule_thread_reindex(instance.thread_id)


@receiver(post_delete, sender=models.ThreadAccess)
def update_unread_mailboxes_on_access_delete(sender, instance, **kwargs):
    """Schedule a thread reindex when a ThreadAccess is deleted."""
    _schedule_thread_reindex(instance.thread_id)


@receiver(pre_delete, sender=models.User)
def delete_user_scope_channels_on_user_delete(sender, instance, **kwargs):
    """Delete the user's personal (scope_level=user) Channels before the
    user row is removed.

    Channel.user uses on_delete=SET_NULL deliberately — the FK alone must
    not blanket-cascade, because a future relaxation of the
    channel_scope_level_targets check constraint could otherwise let a
    user delete silently sweep up unrelated channels. This handler is the
    *only* place where user-scope channels are removed in response to a
    user deletion. The query is filtered explicitly on
    ``scope_level=user``, never on the FK alone.

    If we did not delete these rows here, SET_NULL on the FK would null
    ``user_id`` on the user-scope rows, immediately violating the check
    constraint and aborting the user delete with an IntegrityError — so
    this signal is also load-bearing for user deletion to succeed at all.
    """
    models.Channel.objects.filter(
        user=instance,
        scope_level=enums.ChannelScopeLevel.USER,
    ).delete()
