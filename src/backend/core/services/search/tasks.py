"""Search and indexing tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught

from django.conf import settings

import dramatiq

from core import models
from core.services.search import (
    create_index_if_not_exists,
    delete_index,
    index_message,
    index_thread,
)
from core.utils import register_task

logger = dramatiq.get_logger(__name__)


@register_task
def reindex_all_task():
    """Reindex all threads and messages."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    # Ensure index exists first
    create_index_if_not_exists()

    # Get all threads and index them
    threads = models.Thread.objects.all()
    total = threads.count()
    success_count = 0
    failure_count = 0

    for i, thread in enumerate(threads):
        try:
            if index_thread(thread):
                success_count += 1
            else:
                failure_count += 1
        # pylint: disable=broad-exception-caught
        except Exception as e:
            failure_count += 1
            logger.exception("Error indexing thread %s: %s", thread.id, e)

        # Update progress if callback provided
        if i % 100 == 0:
            logger.debug("Progress for all threads: %s of %s", i, total)

    return {
        "success": True,
        "total": total,
        "success_count": success_count,
        "failure_count": failure_count,
    }


@register_task
def reindex_thread_task(thread_id):
    """Reindex a specific thread and all its messages."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get the thread
        thread = models.Thread.objects.get(id=thread_id)

        # Index the thread
        success = index_thread(thread)

        return {
            "thread_id": str(thread_id),
            "success": success,
        }
    except models.Thread.DoesNotExist:
        logger.error("Thread %s does not exist", thread_id)
        return {
            "thread_id": str(thread_id),
            "success": False,
            "error": f"Thread {thread_id} does not exist",
        }
    except Exception as e:
        logger.exception("Error in reindex_thread_task for thread %s: %s", thread_id, e)
        raise


@register_task
def reindex_mailbox_task(mailbox_id):
    """Reindex all threads and messages in a specific mailbox."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    # Ensure index exists first
    create_index_if_not_exists()

    # Get all threads in the mailbox
    threads = models.Mailbox.objects.get(id=mailbox_id).threads_viewer
    total = threads.count()
    success_count = 0
    failure_count = 0

    for i, thread in enumerate(threads):
        try:
            if index_thread(thread):
                success_count += 1
            else:
                failure_count += 1
        # pylint: disable=broad-exception-caught
        except Exception as e:
            failure_count += 1
            logger.exception("Error indexing thread %s: %s", thread.id, e)

        # Update progress every 50 threads
        if i % 50 == 0:
            logger.debug(
                "Updating progress for mailbox %s: %s of %s", mailbox_id, i, total
            )

    return {
        "mailbox_id": str(mailbox_id),
        "success": True,
        "total": total,
        "success_count": success_count,
        "failure_count": failure_count,
    }


@register_task
def index_message_task(message_id):
    """Index a single message."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch message indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get the message
        message = (
            models.Message.objects.select_related("thread", "sender")
            .prefetch_related("recipients__contact")
            .get(id=message_id)
        )

        # Index the message
        success = index_message(message)

        return {
            "message_id": str(message_id),
            "thread_id": str(message.thread_id),
            "success": success,
        }
    except models.Message.DoesNotExist:
        logger.error("Message %s does not exist", message_id)
        return {
            "message_id": str(message_id),
            "success": False,
            "error": f"Message {message_id} does not exist",
        }
    except Exception as e:
        logger.exception(
            "Error in index_message_task for message %s: %s", message_id, e
        )
        raise


@register_task
def reset_search_index():
    """Delete and recreate the OpenSearch index."""
    delete_index()
    create_index_if_not_exists()
    return {"success": True}
