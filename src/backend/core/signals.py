"""Signal handlers for core models."""
# pylint: disable=unused-argument

import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from core import enums, models
from core.services.identity.keycloak import (
    sync_mailbox_to_keycloak_user,
    sync_maildomain_to_keycloak_group,
)
from core.services.search import MESSAGE_INDEX, get_opensearch_client
from core.services.search.tasks import (
    index_message_task,
    reindex_thread_task,
    update_threads_mailbox_flags_task,
)
from core.utils import ThreadStatsUpdateDeferrer

logger = logging.getLogger(__name__)


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
    """Index a message after it's saved."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    try:
        # Schedule the indexing task asynchronously
        index_message_task.delay(str(instance.id))
        # reindex_thread_task.delay(str(instance.thread.id))

    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error scheduling message indexing for message %s: %s",
            instance.id,
            e,
        )


@receiver(post_save, sender=models.MessageRecipient)
def index_message_recipient_post_save(sender, instance, created, **kwargs):
    """Index a message recipient after it's saved."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    try:
        # Schedule the indexing task asynchronously
        # TODO: deduplicate the indexing of the message!
        index_message_task.delay(str(instance.message.id))

    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error scheduling message indexing for message %s: %s",
            instance.message.id,
            e,
        )


@receiver(post_save, sender=models.MessageRecipient)
def update_thread_stats_on_delivery_status_change(sender, instance, **kwargs):
    """
    Update thread stats when a MessageRecipient delivery_status changes.

    Only triggers for outbound messages (is_sender=True) that are not drafts
    or trashed, since only those affect thread delivery stats.

    Supports batching via defer_thread_stats_update() context manager.
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
    if ThreadStatsUpdateDeferrer.defer_for(thread):
        return

    # Otherwise update immediately
    try:
        thread.update_stats()
    # pylint: disable=broad-exception-caught
    except Exception:
        logger.exception("Failed to update stats for thread %s", thread.id)


@receiver(post_save, sender=models.Thread)
def index_thread_post_save(sender, instance, created, **kwargs):
    """Index a thread after it's saved."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    try:
        # Schedule the indexing task asynchronously
        reindex_thread_task.delay(str(instance.id))

    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error scheduling thread indexing for thread %s: %s",
            instance.id,
            e,
        )


@receiver(pre_delete, sender=models.Message)
def delete_message_blobs(sender, instance, **kwargs):
    """Delete the blobs associated with a message."""
    if instance.blob:
        instance.blob.delete()
    if instance.draft_blob:
        instance.draft_blob.delete()


# @receiver(post_delete, sender=models.Attachment)
# def delete_attachments_blobs(sender, instance, **kwargs):
#     """Delete the blob associated with an attachment."""
#     if instance.blob:
#         instance.blob.delete()


@receiver(post_delete, sender=models.Message)
def delete_message_from_index(sender, instance, **kwargs):
    """Remove a message from the index after it's deleted."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    try:
        es = get_opensearch_client()
        # pylint: disable=unexpected-keyword-arg
        es.delete(
            index=MESSAGE_INDEX,
            id=str(instance.id),
            ignore=[404],  # Ignore if document doesn't exist or is already deleted
        )

    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error removing message %s from index: %s",
            instance.id,
            e,
        )


@receiver(post_delete, sender=models.Thread)
def delete_thread_from_index(sender, instance, **kwargs):
    """Remove a thread and its messages from the index after it's deleted."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    try:
        es = get_opensearch_client()

        # Delete the thread document
        # pylint: disable=unexpected-keyword-arg
        es.delete(
            index=MESSAGE_INDEX,
            id=str(instance.id),
            ignore=[404],  # Ignore if document doesn't exist
        )

        # Delete all child message documents using a query
        # pylint: disable=unexpected-keyword-arg
        es.delete_by_query(
            index=MESSAGE_INDEX,
            body={"query": {"term": {"thread_id": str(instance.id)}}},
            ignore=[404, 409],  # Ignore if no documents match
            conflicts="proceed",
        )
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error removing thread %s and its messages from index: %s",
            instance.id,
            e,
        )


@receiver(pre_delete, sender=models.Message)
def delete_orphan_draft_attachments(sender, instance, **kwargs):
    """Remove orphan attachments after a draft message is deleted."""

    # Get all attachments that are not used by any other message
    if instance.is_draft:
        attachments = models.Attachment.objects.filter(messages=instance)

        for attachment in attachments:
            if attachment.messages.count() == 1:
                attachment.blob.delete()  # this will cascade delete the attachment

        if instance.draft_blob and instance.draft_blob.pk:
            instance.draft_blob.delete()

    if instance.blob and instance.blob.pk:
        if instance.blob.messages.count() == 1:
            instance.blob.delete()


@receiver(post_save, sender=models.ThreadAccess)
def update_mailbox_flags_on_access_save(sender, instance, created, **kwargs):
    """Update mailbox flags in OpenSearch when ThreadAccess read/starred state changes."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not (
        {"read_at", "starred_at"} & set(update_fields)
    ):
        return

    thread_id = str(instance.thread_id)
    try:
        transaction.on_commit(
            lambda tid=thread_id: update_threads_mailbox_flags_task.delay([tid])
        )
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error scheduling unread_mailboxes update for thread %s: %s",
            instance.thread_id,
            e,
        )


@receiver(post_delete, sender=models.ThreadAccess)
def update_unread_mailboxes_on_access_delete(sender, instance, **kwargs):
    """Update unread_mailboxes in OpenSearch when a ThreadAccess is deleted."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        return

    thread_id = str(instance.thread_id)
    try:
        transaction.on_commit(
            lambda tid=thread_id: update_threads_mailbox_flags_task.delay([tid])
        )
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error scheduling unread_mailboxes update for thread %s: %s",
            instance.thread_id,
            e,
        )


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


def _validate_user_ids_with_access(thread_event, thread, users_data):
    """Validate and deduplicate user IDs, checking ThreadAccess.

    Shared validation logic for mentions. Parses UUIDs from
    the 'id' field of each entry, deduplicates, and batch-validates that each
    user has access to the thread via the MailboxAccess -> ThreadAccess chain.

    Args:
        thread_event: The ThreadEvent instance (for logging context).
        thread: The Thread instance.
        users_data: List of dicts with 'id' and 'name' keys.

    Returns:
        Set of valid user UUIDs that have access to the thread.
    """
    if not users_data:
        return set()

    seen_user_ids = set()
    unique_user_ids = []
    for entry in users_data:
        raw_id = entry.get("id")
        if not raw_id:
            continue
        try:
            user_id = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            logger.warning(
                "Skipping user with invalid UUID '%s' in ThreadEvent %s",
                raw_id,
                thread_event.id,
            )
            continue
        if user_id not in seen_user_ids:
            seen_user_ids.add(user_id)
            unique_user_ids.append(user_id)

    if not unique_user_ids:
        return set()

    # Batch validate: users who have access to this thread
    # Chain: User -> MailboxAccess.user -> MailboxAccess.mailbox -> ThreadAccess.mailbox
    valid_user_ids = set(
        models.ThreadAccess.objects.filter(
            thread=thread,
            mailbox__accesses__user_id__in=unique_user_ids,
        ).values_list("mailbox__accesses__user_id", flat=True)
    )

    for user_id in unique_user_ids:
        if user_id not in valid_user_ids:
            logger.warning(
                "Skipping user %s in ThreadEvent %s: "
                "user not found or no thread access",
                user_id,
                thread_event.id,
            )

    return valid_user_ids


def sync_mention_user_events(thread_event, thread, mentions_data):
    """Sync UserEvent MENTION records to match the current mentions payload.

    Diffs the currently mentioned users against the existing UserEvent MENTION
    records for this ThreadEvent and reconciles the two:
    - Creates UserEvent records for newly mentioned users.
    - Deletes UserEvent records for users who are no longer mentioned so that
      stale entries do not linger in the "Mentioned" folder after an edit.
    - Leaves existing records untouched when the user is still mentioned, which
      preserves their ``read_at`` state across edits.

    Invalid or unauthorized mentions are silently skipped with a warning log.

    Args:
        thread_event: The ThreadEvent instance containing mentions.
        thread: The Thread instance.
        mentions_data: List of mention dicts with 'id' and 'name' keys.
    """
    new_valid_user_ids = _validate_user_ids_with_access(
        thread_event, thread, mentions_data
    )

    existing_user_ids = set(
        models.UserEvent.objects.filter(
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
        ).values_list("user_id", flat=True)
    )

    to_add = new_valid_user_ids - existing_user_ids
    to_remove = existing_user_ids - new_valid_user_ids

    if to_remove:
        deleted_count, _ = models.UserEvent.objects.filter(
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
            user_id__in=to_remove,
        ).delete()
        if deleted_count:
            logger.info(
                "Deleted %d UserEvent MENTION(s) for ThreadEvent %s",
                deleted_count,
                thread_event.id,
            )

    if to_add:
        user_events = [
            models.UserEvent(
                user_id=user_id,
                thread=thread,
                thread_event=thread_event,
                type=enums.UserEventTypeChoices.MENTION,
            )
            for user_id in to_add
        ]
        # ignore_conflicts=True lets the UniqueConstraint on
        # (user, thread_event, type) absorb races between concurrent
        # post_save signals on the same ThreadEvent (e.g. two PATCH in flight).
        models.UserEvent.objects.bulk_create(user_events, ignore_conflicts=True)
        logger.info(
            "Created %d UserEvent MENTION(s) for ThreadEvent %s",
            len(user_events),
            thread_event.id,
        )


@receiver(post_save, sender=models.ThreadEvent)
def handle_thread_event_post_save(sender, instance, created, **kwargs):
    """Handle post-save signal for ThreadEvent to sync UserEvent records.

    Dispatches by ThreadEvent type:
    - IM: Syncs UserEvent MENTION records on both create and update so that
      edits to the mentions list add/remove notifications accordingly.
    """
    try:
        if instance.type == enums.ThreadEventTypeChoices.IM:
            sync_mention_user_events(
                thread_event=instance,
                thread=instance.thread,
                mentions_data=(instance.data or {}).get("mentions", []),
            )

    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception(
            "Error in ThreadEvent post_save handler for event %s: %s",
            instance.id,
            e,
        )
