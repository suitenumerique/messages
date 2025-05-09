"""Signal handlers for core models."""

import logging

from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core import models
from core.tasks import index_message_task, reindex_thread_task

logger = logging.getLogger(__name__)


@receiver(post_save, sender=models.Message)
def index_message_post_save(sender, instance, created, **kwargs):
    """Index a message after it's saved."""
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", False):
        return

    try:
        # Schedule the indexing task asynchronously
        index_message_task.delay(str(instance.id))
        # reindex_thread_task.delay(str(instance.thread.id))
    except Exception as e:
        logger.exception(
            f"Error scheduling message indexing for message {instance.id}: {e}"
        )


@receiver(post_save, sender=models.MessageRecipient)
def index_message_recipient_post_save(sender, instance, created, **kwargs):
    """Index a message recipient after it's saved."""
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", False):
        return

    try:
        # Schedule the indexing task asynchronously
        # TODO: deduplicate the indexing of the message!
        index_message_task.delay(str(instance.message.id))
    except Exception as e:
        logger.exception(
            f"Error scheduling message indexing for message {instance.message.id}: {e}"
        )


@receiver(post_save, sender=models.Thread)
def index_thread_post_save(sender, instance, created, **kwargs):
    """Index a thread after it's saved."""
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", False):
        return

    try:
        # Schedule the indexing task asynchronously
        reindex_thread_task.delay(str(instance.id))
    except Exception as e:
        logger.exception(
            f"Error scheduling thread indexing for thread {instance.id}: {e}"
        )


@receiver(post_delete, sender=models.Message)
def delete_message_from_index(sender, instance, **kwargs):
    """Remove a message from the index after it's deleted."""
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", False):
        return

    try:
        # Use the search module directly for deletion
        from core.search import MESSAGE_INDEX, get_es_client

        es = get_es_client()
        es.delete(
            index=MESSAGE_INDEX,
            id=str(instance.id),
            ignore=[404],  # Ignore if document doesn't exist
        )
    except Exception as e:
        logger.exception(f"Error removing message {instance.id} from index: {e}")


@receiver(post_delete, sender=models.Thread)
def delete_thread_from_index(sender, instance, **kwargs):
    """Remove a thread and its messages from the index after it's deleted."""
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", False):
        return

    try:
        # Use the search module directly for deletion
        from core.search import MESSAGE_INDEX, get_es_client

        es = get_es_client()

        # Delete the thread document
        es.delete(
            index=MESSAGE_INDEX,
            id=str(instance.id),
            ignore=[404],  # Ignore if document doesn't exist
        )

        # Delete all child message documents using a query
        es.delete_by_query(
            index=MESSAGE_INDEX,
            body={"query": {"term": {"thread_id": str(instance.id)}}},
            ignore=[404],  # Ignore if no documents match
        )
    except Exception as e:
        logger.exception(
            f"Error removing thread {instance.id} and its messages from index: {e}"
        )
