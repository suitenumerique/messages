"""Coalescing buffers for OpenSearch reindex and delete enqueues.

Signal handlers push thread IDs (and ``thread_id:message_id`` pairs for
deletes) into Redis sets instead of scheduling a Celery task per
``save()`` / ``delete()``. A periodic task (``process_pending_reindex_task``)
drains the buffers every ``SEARCH_REINDEX_TASKS_INTERVAL`` seconds, chunks
each set by ``SEARCH_FLUSH_BATCH_SIZE`` to keep each Celery payload
bounded, and enqueues ``bulk_delete_threads_task`` /
``bulk_delete_messages_task`` / ``bulk_reindex_threads_task`` per chunk —
up to ``SEARCH_FLUSH_MAX_BATCHES`` tasks per cycle, shared across the
three handoffs.

Three sets are tracked:

* ``search:pending_reindex_threads`` — thread IDs that need their
  OpenSearch documents rebuilt (upsert) from the DB.
* ``search:pending_delete_threads`` — thread IDs whose parent documents
  must be removed from the index.
* ``search:pending_delete_messages`` — ``thread_id:message_id`` pairs
  whose child documents must be removed from the index. Encoded as
  strings so the Redis SET dedup absorbs duplicate enqueues across the
  message ``post_delete`` and any cascade fan-out.

The two delete sets are deliberately split: deleting a parent thread doc
does **not** remove its message children in OpenSearch (parent/child join
docs are independent), so every cascaded ``Message.post_delete`` enqueues
its own pair. The bulk delete tasks then issue ``bulk delete by _id``
calls — much lighter than ``delete_by_query``, which holds a scroll
context and refreshes per call.

The buffer requires ``django_redis`` for ``CACHES['default']``: dedup
and drain rely on native Redis sets (``SADD`` + ``SPOP count=N``,
atomic since Redis 3.2) which Django's pluggable cache layer can't
provide race-free across workers. Other backends (Dummy, LocMem,
FileBased, …) skip with a warning — for OpenSearch the recovery path
is to set ``OPENSEARCH_INDEX_THREADS=False`` or run with Redis.
"""

import logging

from django.conf import settings

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

PENDING_REINDEX_KEY = "search:pending_reindex_threads"
PENDING_DELETE_KEY = "search:pending_delete_threads"
PENDING_DELETE_MESSAGES_KEY = "search:pending_delete_messages"

# Separator used to encode ``(thread_id, message_id)`` pairs as a single
# string in Redis. UUIDs never contain a colon, so the split is unambiguous.
MESSAGE_PAIR_SEPARATOR = ":"


def _is_redis_backend() -> bool:
    """Return True when the default cache is backed by django_redis."""
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    return "django_redis" in backend


def _redis_client():
    # pylint: disable-next=import-outside-toplevel
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def _enqueue(key: str, value) -> None:
    """Add ``value`` to the pending set at ``key``."""
    if value is None:
        return
    if not _is_redis_backend():
        logger.warning(
            "OpenSearch reindex coalescer requires Redis: %s for %s "
            "dropped. Configure django_redis or disable "
            "OPENSEARCH_INDEX_THREADS.",
            value,
            key,
        )
        return
    try:
        _redis_client().sadd(key, str(value))
    except RedisError as exc:
        logger.error(
            "Redis unavailable while enqueuing %s into %s (%s: %s); "
            "ID dropped — index will diverge until another signal fires "
            "on this thread",
            value,
            key,
            type(exc).__name__,
            exc,
        )
    # pylint: disable=broad-exception-caught
    except Exception:
        logger.exception("Failed to enqueue %s into %s", value, key)


def enqueue_thread_reindex(thread_id) -> None:
    """Add ``thread_id`` to the pending reindex set."""
    _enqueue(PENDING_REINDEX_KEY, thread_id)


def enqueue_thread_delete(thread_id) -> None:
    """Add ``thread_id`` to the pending thread delete set."""
    _enqueue(PENDING_DELETE_KEY, thread_id)


def enqueue_message_delete(thread_id, message_id) -> None:
    """Add a ``(thread_id, message_id)`` pair to the pending message delete set.

    The pair is encoded as ``f"{thread_id}:{message_id}"`` so it can ride
    the same Redis SET dedup as the thread sets. The receiving task splits
    on the colon to recover the routing (thread_id) it needs to delete the
    child document by ``_id``.
    """
    if thread_id is None or message_id is None:
        return
    _enqueue(
        PENDING_DELETE_MESSAGES_KEY,
        f"{thread_id}{MESSAGE_PAIR_SEPARATOR}{message_id}",
    )


def _drain_batch(key: str, batch_size: int) -> list | None:
    """Drain up to ``batch_size`` IDs from the pending set at ``key``.

    Returns the drained IDs as a list (possibly empty when the set is empty)
    or ``None`` if the drain itself failed — signalling the caller to stop.
    """
    try:
        drained = _redis_client().spop(key, count=batch_size)
        return [
            tid.decode() if isinstance(tid, bytes) else str(tid)
            for tid in (drained or [])
        ]
    except RedisError as exc:
        logger.error(
            "Redis unavailable while draining pending set %s (%s: %s); "
            "skipping this flush cycle — IDs already in the set are preserved",
            key,
            type(exc).__name__,
            exc,
        )
        return None
    # pylint: disable=broad-exception-caught
    except Exception:
        logger.exception("Failed to drain pending set %s", key)
        return None


def _restore_batch(key: str, thread_ids: list) -> None:
    """Push ``thread_ids`` back into the pending set at ``key``."""
    try:
        _redis_client().sadd(key, *thread_ids)
    except RedisError as exc:
        logger.error(
            "Redis unavailable while restoring %d drained IDs to %s (%s: %s); "
            "these entries will stay out of sync until another signal fires",
            len(thread_ids),
            key,
            type(exc).__name__,
            exc,
        )
    # pylint: disable=broad-exception-caught
    except Exception:
        logger.exception(
            "Failed to restore %d drained IDs to %s; "
            "these entries will stay out of sync until another signal fires",
            len(thread_ids),
            key,
        )


def _drain_and_dispatch(
    key: str,
    batch_size: int,
    remaining_budget: int,
    task,
    task_label: str,
) -> tuple[int, int, set[str]]:
    """Drain ``key`` and hand off batches to ``task.delay``.

    Returns ``(handed_off, budget_left, drained_ids)`` where ``handed_off`` is
    the count of IDs accepted by the broker, ``budget_left`` the remaining
    handoff budget after this drain, and ``drained_ids`` the IDs from batches
    successfully handed off before any broker failure (a rolled-back batch is
    not included since its IDs have been restored to the source set).

    On a broker failure the failing batch is restored to its set and the
    drain stops to avoid hammering a degraded broker; ``budget_left`` is
    returned as ``0`` so the calling loop exits as well.
    """
    handed_off = 0
    drained_ids: set[str] = set()

    while remaining_budget > 0:
        ids = _drain_batch(key, batch_size)
        if ids is None or not ids:
            break

        try:
            task.delay(ids)
        # pylint: disable=broad-exception-caught
        except Exception:
            logger.exception(
                "Failed to enqueue %s for %d drained IDs; "
                "returning IDs to the pending set for retry",
                task_label,
                len(ids),
            )
            _restore_batch(key, ids)
            return handed_off, 0, drained_ids

        handed_off += len(ids)
        drained_ids.update(ids)
        remaining_budget -= 1

    return handed_off, remaining_budget, drained_ids


def process_pending_reindex(
    batch_size: int | None = None,
    max_batches: int | None = None,
) -> dict:
    """Drain the three pending sets and hand off batches to bulk tasks.

    ``batch_size`` and ``max_batches`` default to
    ``settings.SEARCH_FLUSH_BATCH_SIZE`` and
    ``settings.SEARCH_FLUSH_MAX_BATCHES`` when omitted. Sentinel-based
    rather than ``= settings.X`` because the latter would freeze the
    value at module import time and break ``override_settings`` in tests.

    Each iteration atomically drains up to ``batch_size`` entries from one
    of the pending sets and enqueues the matching bulk task:

    1. ``pending_delete_threads``  → ``bulk_delete_threads_task``
    2. ``pending_delete_messages`` → ``bulk_delete_messages_task``
    3. ``pending_reindex_threads`` → ``bulk_reindex_threads_task``

    Drain order is strictly sequential — each set is fully drained (within
    the budget) before moving to the next. This guarantees that within a
    single cycle, a thread/message about to be removed is never reindexed:
    the reindex pass filters out IDs already drained from the thread-delete
    set during this cycle.

    The loop stops when every set is empty, a drain or handoff fails, or
    ``max_batches`` tasks have been enqueued in total (shared across the
    three handoffs). Because the order is sequential, a massive backlog of
    thread-deletes can consume the whole cycle's budget and defer message
    deletes and reindexes to subsequent beat ticks. Leftover IDs stay in
    their set (Redis SET dedup) so no work is lost — only deferred.

    The drain removes IDs from each buffer *before* we know whether the
    broker accepted the task. If ``delay()`` raises, the failing batch is
    pushed back into its own set and the loop stops so we don't hammer a
    degraded broker. Batches already accepted stay accepted. Without this
    rollback, a transient broker outage would silently desync the index
    from the database until another signal fired on those threads.

    Returns a dict ``{"deleted_threads": int, "deleted_messages": int,
    "reindexed": int}`` with the count of IDs successfully handed off to
    each task type.
    """
    if batch_size is None:
        batch_size = settings.SEARCH_FLUSH_BATCH_SIZE
    if max_batches is None:
        max_batches = settings.SEARCH_FLUSH_MAX_BATCHES

    if not _is_redis_backend():
        logger.warning(
            "OpenSearch reindex coalescer requires Redis; nothing to drain. "
            "Configure django_redis or disable OPENSEARCH_INDEX_THREADS."
        )
        return {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}

    # pylint: disable-next=import-outside-toplevel
    from core.services.search.tasks import (
        bulk_delete_messages_task,
        bulk_delete_threads_task,
        bulk_reindex_threads_task,
    )

    remaining_budget = max_batches

    deleted_threads, remaining_budget, drained_delete_thread_ids = _drain_and_dispatch(
        PENDING_DELETE_KEY,
        batch_size,
        remaining_budget,
        bulk_delete_threads_task,
        "bulk_delete_threads_task",
    )

    deleted_messages, remaining_budget, _ = _drain_and_dispatch(
        PENDING_DELETE_MESSAGES_KEY,
        batch_size,
        remaining_budget,
        bulk_delete_messages_task,
        "bulk_delete_messages_task",
    )

    reindexed_total = 0
    while remaining_budget > 0:
        thread_ids = _drain_batch(PENDING_REINDEX_KEY, batch_size)
        if thread_ids is None or not thread_ids:
            break

        # Drop IDs already scheduled for deletion in this cycle. The delete
        # task will remove those documents; reindexing them would be wasted
        # work and, if the delete runs first, recreate documents we just
        # dropped.
        if drained_delete_thread_ids:
            filtered = [
                tid for tid in thread_ids if tid not in drained_delete_thread_ids
            ]
        else:
            filtered = thread_ids

        if not filtered:
            continue

        try:
            bulk_reindex_threads_task.delay(filtered)
        # pylint: disable=broad-exception-caught
        except Exception:
            logger.exception(
                "Failed to enqueue bulk_reindex_threads_task for %d drained threads; "
                "returning IDs to the pending set for retry",
                len(filtered),
            )
            _restore_batch(PENDING_REINDEX_KEY, filtered)
            return {
                "deleted_threads": deleted_threads,
                "deleted_messages": deleted_messages,
                "reindexed": reindexed_total,
            }

        reindexed_total += len(filtered)
        remaining_budget -= 1

    return {
        "deleted_threads": deleted_threads,
        "deleted_messages": deleted_messages,
        "reindexed": reindexed_total,
    }
