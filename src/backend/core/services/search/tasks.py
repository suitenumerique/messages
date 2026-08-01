"""Search and indexing tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught

import logging
from datetime import datetime

from django.conf import settings

from core import models
from core.services.search import (
    create_index_if_not_exists,
    delete_index,
    ensure_index_exists,
    index_message,
    index_thread,
    update_thread_mailbox_flags,
)
from core.services.search import (
    reindex_all as _reindex_all_impl,
)
from core.services.search import (
    reindex_mailbox as _reindex_mailbox_impl,
)
from core.services.search.coalescer import (
    MESSAGE_PAIR_SEPARATOR,
    process_pending_reindex,
)
from core.services.search.exceptions import RETRYABLE_EXCEPTIONS
from core.services.search.index import (
    bulk_delete_documents,
    reindex_bulk_threads,
)
from core.services.search.mapping import MESSAGE_INDEX
from core.task_utils import cron_task, register_task, set_task_progress

logger = logging.getLogger(__name__)

# A full or per-mailbox reindex walks every thread in scope, so it needs far
# more than the queue library's 10-minute default. Both are operator-triggered
# (admin action or ``manage.py search_reindex``), never on a schedule.
_REINDEX_TASK_TIME_LIMIT = 12 * 3600  # seconds


def _reindex_all_base(update_progress=None, from_date=None):
    """Base function for reindexing all threads and messages.

    Delegates to the bulk ``reindex_all`` implementation in the search index
    module.

    Args:
        update_progress: Optional callback function to update progress
        from_date: Optional ``datetime`` — when set, only threads with
            ``updated_at >= from_date`` are reindexed.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    result = _reindex_all_impl(progress_callback=update_progress, from_date=from_date)

    return {
        "success": True,
        "total": result["total"],
        "success_count": result["indexed_threads"],
        "failure_count": result["failure_count"],
    }


@register_task(queue="reindex", time_limit=_REINDEX_TASK_TIME_LIMIT)
def reindex_all(from_date_iso=None):
    """Reindex every thread and message.

    Args:
        from_date_iso: Optional ISO-8601 datetime string — forwarded as a
            ``datetime`` filter on ``Thread.updated_at``. Stringified at the
            edge because task payloads are serialized through JSON.
    """

    def update_progress(current, total, success_count, failure_count):
        """Update task progress."""
        set_task_progress(
            100 * current // total if total else 100,
            {
                "current": current,
                "total": total,
                "success_count": success_count,
                "failure_count": failure_count,
            },
        )

    from_date = datetime.fromisoformat(from_date_iso) if from_date_iso else None
    return _reindex_all_base(update_progress, from_date=from_date)


@register_task(queue="reindex")
def reindex_thread_task(thread_id):
    """Reindex a specific thread and all its messages."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    ensure_index_exists()

    try:
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


@register_task(
    queue="reindex",
    max_retries=5,
    retry_on=RETRYABLE_EXCEPTIONS,
    max_backoff=600,
)
def update_threads_mailbox_flags_task(thread_ids):
    """Update mailbox-scoped flags (unread_mailboxes, starred_mailboxes) in OpenSearch."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    ensure_index_exists()

    results = []
    for thread_id in thread_ids:
        try:
            thread = models.Thread.objects.get(id=thread_id)
            success = update_thread_mailbox_flags(thread)
            results.append({"thread_id": thread_id, "success": success})
        except models.Thread.DoesNotExist:
            logger.error("Thread %s does not exist", thread_id)
            results.append({"thread_id": thread_id, "success": False})

    return {"success": True, "results": results}


def _reindex_mailbox_base(mailbox_id, update_progress=None, from_date=None):
    """Base function for reindexing all threads in a mailbox.

    Delegates to the bulk ``reindex_mailbox`` implementation in the search
    index module.

    Args:
        mailbox_id: The mailbox ID to reindex.
        update_progress: Optional callback function to update progress.
        from_date: Optional ``datetime`` — when set, only threads with
            ``updated_at >= from_date`` are reindexed.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    result = _reindex_mailbox_impl(
        mailbox_id, progress_callback=update_progress, from_date=from_date
    )

    if result.get("status") == "error":
        return {"success": False, **result}

    return {
        "mailbox_id": str(mailbox_id),
        "success": True,
        "total": result["total"],
        "success_count": result["indexed_threads"],
        "failure_count": result["failure_count"],
    }


@register_task(queue="reindex", time_limit=_REINDEX_TASK_TIME_LIMIT)
def reindex_mailbox_task(mailbox_id, from_date_iso=None):
    """Reindex every thread in a mailbox.

    Args:
        mailbox_id: The mailbox UUID.
        from_date_iso: Optional ISO-8601 datetime string — forwarded as a
            ``datetime`` filter on ``Thread.updated_at``.
    """

    def update_progress(current, total, success_count, failure_count):
        """Update task progress."""
        set_task_progress(
            100 * current // total if total else 100,
            {
                "current": current,
                "total": total,
                "success_count": success_count,
                "failure_count": failure_count,
            },
        )

    from_date = datetime.fromisoformat(from_date_iso) if from_date_iso else None
    return _reindex_mailbox_base(mailbox_id, update_progress, from_date=from_date)


@register_task(queue="reindex")
def index_message_task(message_id):
    """Index a single message."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch message indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    ensure_index_exists()

    try:
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


@register_task(
    queue="reindex",
    max_retries=5,
    retry_on=RETRYABLE_EXCEPTIONS,
    max_backoff=600,
)
def bulk_reindex_threads_task(thread_ids):
    """Reindex a list of threads and all their messages in one bulk pass.

    Enqueued at the end of a scoped ``ThreadReindexDeferrer.defer()`` block
    (bulk import flows) and by the periodic ``process_pending_reindex_task``
    that drains the coalescing buffers used by non-import signals. Replaces
    the per-row ``index_message_task`` / ``reindex_thread_task`` /
    ``update_threads_mailbox_flags_task`` calls that post_save signals would
    otherwise trigger.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    if not thread_ids:
        return {"success": True, "total": 0, "success_count": 0, "failure_count": 0}

    ensure_index_exists()

    result = reindex_bulk_threads(models.Thread.objects.filter(id__in=thread_ids))

    return {
        "success": True,
        "total": result["total"],
        "success_count": result["indexed_threads"],
        "failure_count": result["failure_count"],
    }


@register_task(
    queue="reindex",
    max_retries=5,
    retry_on=RETRYABLE_EXCEPTIONS,
    max_backoff=600,
)
def bulk_delete_threads_task(thread_ids):
    """Remove thread parent documents from OpenSearch via bulk delete by ``_id``.

    Child message documents are removed by ``bulk_delete_messages_task``
    (cascaded ``Message.post_delete`` signals enqueue them in their own
    pending set). Splitting the two avoids the previous per-task
    ``delete_by_query`` on ``terms: {thread_id: [...]}`` — that call holds
    a scroll context, scans the index and refreshes per call, which under
    load triggered 503 / 429 responses on the cluster. ``bulk delete by
    _id`` is comparatively free.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return {"success": False, "reason": "disabled"}

    if not thread_ids:
        return {"success": True, "deleted_threads": 0}

    ensure_index_exists()

    actions = [
        {"_op_type": "delete", "_index": MESSAGE_INDEX, "_id": str(tid)}
        for tid in thread_ids
    ]
    bulk_delete_documents(actions)

    return {"success": True, "deleted_threads": len(actions)}


@register_task(
    queue="reindex",
    max_retries=5,
    retry_on=RETRYABLE_EXCEPTIONS,
    max_backoff=600,
)
def bulk_delete_messages_task(pairs):
    """Remove message child documents from OpenSearch via bulk delete by ``_id``.

    ``pairs`` is a list of ``"thread_id:message_id"`` strings as produced
    by ``enqueue_message_delete``. Child docs use ``thread_id`` as their
    routing key; passing it explicitly keeps the request hitting the right
    shard without OpenSearch having to broadcast.

    Replaces the per-chunk ``_purge_orphan_docs`` call that used
    ``delete_by_query`` with ``must_not.ids`` to sweep stale message docs
    after a thread reindex. We now know exactly which messages were
    deleted (the ``post_delete`` signal fires for direct *and* cascaded
    deletes), so a targeted bulk delete is both correct and far cheaper.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return {"success": False, "reason": "disabled"}

    if not pairs:
        return {"success": True, "deleted_messages": 0}

    ensure_index_exists()

    actions = []
    for pair in pairs:
        thread_id, _, message_id = pair.partition(MESSAGE_PAIR_SEPARATOR)
        if not thread_id or not message_id:
            logger.warning("Skipping malformed delete pair %r", pair)
            continue
        actions.append(
            {
                "_op_type": "delete",
                "_index": MESSAGE_INDEX,
                "_id": message_id,
                "_routing": thread_id,
            }
        )

    if not actions:
        return {"success": True, "deleted_messages": 0}

    bulk_delete_documents(actions)

    return {"success": True, "deleted_messages": len(actions)}


@register_task(queue="reindex")
def reset_search_index():
    """Delete and recreate the OpenSearch index."""

    delete_index()
    create_index_if_not_exists()
    return {"success": True}


@cron_task(interval=settings.SEARCH_REINDEX_TASKS_INTERVAL)
@register_task(queue="reindex")
def process_pending_reindex_task():
    """Drain the coalescing buffers and enqueue bulk delete/reindex tasks.

    Scheduled every ``SEARCH_REINDEX_TASKS_INTERVAL`` seconds.
    Consumes IDs accumulated by ``enqueue_thread_reindex`` /
    ``enqueue_thread_delete`` / ``enqueue_message_delete`` from signal
    handlers firing outside any ``ThreadReindexDeferrer.defer()`` scope and
    hands them off to ``bulk_reindex_threads_task`` /
    ``bulk_delete_threads_task`` / ``bulk_delete_messages_task``.
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        return {
            "success": False,
            "reason": "disabled",
            "deleted_threads": 0,
            "deleted_messages": 0,
            "reindexed": 0,
        }

    result = process_pending_reindex()
    return {"success": True, **result}
