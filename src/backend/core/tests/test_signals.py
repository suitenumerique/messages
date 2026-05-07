"""Test signal handlers for core models."""
# pylint: disable=too-many-lines

import contextlib
import logging
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings

import pytest

from core import enums, factories
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

pytestmark = pytest.mark.django_db


@pytest.fixture(name="coalescer_caplog")
def fixture_coalescer_caplog(caplog):
    """Capture log records emitted by the coalescer logger.

    The ``core`` logger sets ``propagate=False`` in ``messages.settings`` so
    records never reach the root logger that pytest's ``caplog`` listens on.
    Attaching the caplog handler directly to the coalescer logger restores
    visibility for the duration of the test.
    """
    coalescer_logger = logging.getLogger("core.services.search.coalescer")
    caplog.set_level(logging.ERROR, logger="core.services.search.coalescer")
    coalescer_logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        coalescer_logger.removeHandler(caplog.handler)


class TestUpdateThreadStatsOnDeliveryStatusChange:
    """Test the signal that updates thread stats when delivery status changes."""

    def test_signal_triggers_on_delivery_status_change(self):
        """Test that update_stats is called when delivery_status changes."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch.object(thread, "update_stats") as mock_update_stats:
            recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
            recipient.save(update_fields=["delivery_status"])

            mock_update_stats.assert_called_once()

    def test_signal_does_not_trigger_for_non_sender_message(self):
        """Test that update_stats is NOT called for inbound messages (is_sender=False)."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=False,  # Inbound message
            is_draft=False,
            is_trashed=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch.object(thread, "update_stats") as mock_update_stats:
            recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
            recipient.save(update_fields=["delivery_status"])

            mock_update_stats.assert_not_called()

    def test_signal_does_not_trigger_for_draft_message(self):
        """Test that update_stats is NOT called for draft messages."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=True,  # Draft
            is_trashed=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch.object(thread, "update_stats") as mock_update_stats:
            recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
            recipient.save(update_fields=["delivery_status"])

            mock_update_stats.assert_not_called()

    def test_signal_does_not_trigger_for_trashed_message(self):
        """Test that update_stats is NOT called for trashed messages."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=True,  # Trashed
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch.object(thread, "update_stats") as mock_update_stats:
            recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
            recipient.save(update_fields=["delivery_status"])

            mock_update_stats.assert_not_called()

    def test_signal_does_not_trigger_for_other_field_changes(self):
        """Test that update_stats is NOT called when other fields change."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT,
        )

        with patch.object(thread, "update_stats") as mock_update_stats:
            recipient.delivery_message = "Updated message"
            recipient.save(update_fields=["delivery_message"])

            mock_update_stats.assert_not_called()


class TestThreadStatsUpdateDeferrer:
    """Test the ThreadStatsUpdateDeferrer context manager."""

    def test_defers_update_until_context_exit(self):
        """Test that updates are deferred and called once at context exit."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient1 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )
        recipient2 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch("core.models.Thread.update_stats") as mock_update_stats:
            with ThreadStatsUpdateDeferrer.defer():
                recipient1.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                recipient1.save(update_fields=["delivery_status"])

                recipient2.delivery_status = enums.MessageDeliveryStatusChoices.FAILED
                recipient2.save(update_fields=["delivery_status"])

                # Should not have been called yet
                mock_update_stats.assert_not_called()

            # Should be called once after exiting context
            mock_update_stats.assert_called_once()

    def test_nested_contexts_only_update_once(self):
        """Test that nested contexts only trigger update at outermost exit."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=None,
        )

        with patch("core.models.Thread.update_stats") as mock_update_stats:
            with ThreadStatsUpdateDeferrer.defer():
                with ThreadStatsUpdateDeferrer.defer():
                    recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                    recipient.save(update_fields=["delivery_status"])

                # Inner context exited, should not have been called yet
                mock_update_stats.assert_not_called()

            # Outer context exited, should be called once
            mock_update_stats.assert_called_once()

    def test_multiple_threads_updated(self):
        """Test that multiple affected threads are all updated."""
        thread1 = factories.ThreadFactory()
        thread2 = factories.ThreadFactory()
        message1 = factories.MessageFactory(
            thread=thread1,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        message2 = factories.MessageFactory(
            thread=thread2,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient1 = factories.MessageRecipientFactory(
            message=message1,
            delivery_status=None,
        )
        recipient2 = factories.MessageRecipientFactory(
            message=message2,
            delivery_status=None,
        )

        with patch("core.models.Thread.update_stats") as mock_update_stats:
            with ThreadStatsUpdateDeferrer.defer():
                recipient1.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                recipient1.save(update_fields=["delivery_status"])

                recipient2.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                recipient2.save(update_fields=["delivery_status"])

            # Should be called twice, once per thread
            assert mock_update_stats.call_count == 2

    def test_update_stats_error_does_not_propagate(self):
        """Test that errors in update_stats() are caught and logged, not propagated."""
        thread1 = factories.ThreadFactory()
        thread2 = factories.ThreadFactory()
        message1 = factories.MessageFactory(
            thread=thread1,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        message2 = factories.MessageFactory(
            thread=thread2,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        recipient1 = factories.MessageRecipientFactory(
            message=message1,
            delivery_status=None,
        )
        recipient2 = factories.MessageRecipientFactory(
            message=message2,
            delivery_status=None,
        )

        # Make update_stats() raise an error on first call, succeed on second
        with patch(
            "core.models.Thread.update_stats",
            side_effect=[Exception("Test error"), None],
        ) as mock_update_stats:
            # Should not raise, error is caught and logged
            with ThreadStatsUpdateDeferrer.defer():
                recipient1.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                recipient1.save(update_fields=["delivery_status"])

                recipient2.delivery_status = enums.MessageDeliveryStatusChoices.SENT
                recipient2.save(update_fields=["delivery_status"])

            # Both should have been attempted
            assert mock_update_stats.call_count == 2

    def test_flush_empty_set_is_noop(self):
        """Calling _flush with no items must not touch the DB."""
        with patch("core.models.Thread.update_stats") as mock_update_stats:
            # pylint: disable=protected-access
            ThreadStatsUpdateDeferrer._flush(set())

        mock_update_stats.assert_not_called()

    def test_flush_chunks_input_across_batches(self):
        """IDs beyond STATS_FLUSH_BATCH_SIZE are processed across several chunks.

        Guards against an unbounded ``IN`` clause and an unbounded materialized
        QuerySet when a bulk import accumulates more thread IDs than the
        per-batch cap.
        """
        batch_size = ThreadStatsUpdateDeferrer.STATS_FLUSH_BATCH_SIZE
        threads = [factories.ThreadFactory() for _ in range(3)]
        ids = {str(t.id) for t in threads}

        # Force two SQL batches by lowering the cap for this test.
        with (
            patch.object(ThreadStatsUpdateDeferrer, "STATS_FLUSH_BATCH_SIZE", 2),
            patch("core.models.Thread.update_stats") as mock_update_stats,
            patch(
                "core.models.Thread.objects.filter",
                wraps=__import__(
                    "core.models", fromlist=["Thread"]
                ).Thread.objects.filter,
            ) as mock_filter,
        ):
            # pylint: disable=protected-access
            ThreadStatsUpdateDeferrer._flush(ids)

        # Sanity: the original cap is preserved at the class level.
        assert ThreadStatsUpdateDeferrer.STATS_FLUSH_BATCH_SIZE == batch_size

        # Two chunks of 2 + 1 → two filter() calls and three update_stats().
        assert mock_filter.call_count == 2
        assert mock_update_stats.call_count == 3

    def test_flush_skips_missing_threads(self):
        """Stale IDs in the deferred set must not raise — they are simply ignored.

        ``_flush`` may receive an ID for a thread that has been deleted
        between enqueue and flush. ``filter(id__in=...)`` simply omits it,
        and the live threads are still updated.
        """
        live = factories.ThreadFactory()
        ids = {str(live.id), "00000000-0000-0000-0000-000000000000"}

        with patch(
            "core.models.Thread.update_stats", return_value=None
        ) as mock_update_stats:
            # pylint: disable=protected-access
            ThreadStatsUpdateDeferrer._flush(ids)

        # Only the live thread is updated; the unknown UUID is silently skipped.
        assert mock_update_stats.call_count == 1


class TestThreadReindexDeferrer:
    """Test the ThreadReindexDeferrer context manager.

    The deferrer is the load-bearing piece that prevents Celery saturation
    during large mailbox imports: per-row reindex enqueues are replaced by a
    single ``bulk_reindex_threads_task`` at context exit.
    """

    @pytest.fixture(autouse=True)
    def _enable_opensearch_indexing(self, settings):
        # Test settings disable indexing by default — the deferrer only
        # short-circuits enqueues when indexing is enabled, so we re-enable
        # it here to exercise the real signal paths.
        settings.OPENSEARCH_INDEX_THREADS = True

    def test_redis_enqueue_skipped_inside_defer(self):
        """Inside defer(), signals collect thread IDs instead of pushing to Redis."""
        with (
            patch("core.signals.enqueue_thread_reindex") as mock_enqueue,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_bulk,
        ):
            with ThreadReindexDeferrer.defer():
                thread = factories.ThreadFactory()
                factories.MessageFactory(thread=thread)

            mock_enqueue.assert_not_called()
            mock_bulk.assert_called_once()

    def test_bulk_reindex_enqueued_once_at_exit(self):
        """A single bulk_reindex_threads_task.delay() is enqueued on exit."""
        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay"
        ) as mock_bulk:
            with ThreadReindexDeferrer.defer():
                thread1 = factories.ThreadFactory()
                thread2 = factories.ThreadFactory()
                factories.MessageFactory(thread=thread1)
                factories.MessageFactory(thread=thread2)

            mock_bulk.assert_called_once()
            called_with = mock_bulk.call_args[0][0]
            assert set(called_with) == {str(thread1.id), str(thread2.id)}

    def test_nested_contexts_enqueue_bulk_once(self):
        """Only the outermost context exit enqueues the bulk task."""
        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay"
        ) as mock_bulk:
            with ThreadReindexDeferrer.defer():
                with ThreadReindexDeferrer.defer():
                    thread = factories.ThreadFactory()
                    factories.MessageFactory(thread=thread)
                mock_bulk.assert_not_called()

            mock_bulk.assert_called_once()

    def test_empty_context_does_not_enqueue(self):
        """No threads collected → no bulk task enqueued."""
        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay"
        ) as mock_bulk:
            with ThreadReindexDeferrer.defer():
                pass

            mock_bulk.assert_not_called()

    def test_signals_push_to_redis_outside_defer(
        self, django_capture_on_commit_callbacks
    ):
        """Outside the context, signals coalesce via enqueue_thread_reindex.

        The enqueue now runs inside ``transaction.on_commit``, so we wrap the
        ORM writes in ``django_capture_on_commit_callbacks(execute=True)`` to
        fire the callbacks the rolling test transaction would otherwise swallow.
        """
        with (
            patch("core.signals.enqueue_thread_reindex") as mock_enqueue,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_bulk,
            django_capture_on_commit_callbacks(execute=True),
        ):
            thread = factories.ThreadFactory()
            factories.MessageFactory(thread=thread)

        mock_enqueue.assert_called()
        # Bulk task is only enqueued by the periodic flush, not per-signal.
        mock_bulk.assert_not_called()

    def test_bulk_enqueue_error_does_not_propagate(self):
        """If bulk enqueue fails, the import itself must not break."""
        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay",
            side_effect=Exception("broker down"),
        ):
            with ThreadReindexDeferrer.defer():
                thread = factories.ThreadFactory()
                factories.MessageFactory(thread=thread)
            # Exiting the context must not raise.

    def test_flush_chunks_large_batches(self):
        """IDs beyond ``SEARCH_FLUSH_BATCH_SIZE`` are sliced across several tasks.

        Guards against oversized Celery payloads when a bulk import collects
        more IDs than the cap enforced by ``process_pending_reindex``.
        Uses ``override_settings`` with a small batch so the test stays
        self-contained and cheap regardless of the production default.
        """
        batch_size = 10
        ids = {f"thread-{i:05d}" for i in range(batch_size + 5)}

        with (
            override_settings(SEARCH_FLUSH_BATCH_SIZE=batch_size),
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_bulk,
        ):
            # pylint: disable=protected-access
            ThreadReindexDeferrer._flush(ids)

        assert mock_bulk.call_count == 2
        sent = [call.args[0] for call in mock_bulk.call_args_list]
        assert len(sent[0]) == batch_size
        assert len(sent[1]) == 5
        assert set().union(*sent) == ids

    def test_flush_failed_chunk_falls_back_to_redis(self):
        """When bulk.delay() raises, IDs are returned to the pending set."""
        ids = {"thread-1", "thread-2"}

        with (
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay",
                side_effect=Exception("broker down"),
            ),
            patch(
                "core.services.search.coalescer.enqueue_thread_reindex"
            ) as mock_enqueue,
        ):
            # pylint: disable=protected-access
            ThreadReindexDeferrer._flush(ids)

        assert {call.args[0] for call in mock_enqueue.call_args_list} == ids

    @pytest.mark.django_db(transaction=True)
    def test_rollback_does_not_enqueue_reindex(self):
        """A rolled-back transaction must not leak a reindex enqueue.

        Covers the exact bug the ``on_commit`` wrapper was introduced to
        prevent: a phantom Message save whose transaction fails would push a
        thread ID that no longer exists onto the coalescing buffer.
        """
        with patch("core.signals.enqueue_thread_reindex") as mock_enqueue:
            with contextlib.suppress(RuntimeError), transaction.atomic():
                thread = factories.ThreadFactory()
                factories.MessageFactory(thread=thread)
                raise RuntimeError("force rollback")

            mock_enqueue.assert_not_called()

    def test_recipient_update_after_import_still_coalesces(
        self, django_capture_on_commit_callbacks
    ):
        """A MessageRecipient update after import coalesces the parent thread.

        Guards against over-reach: only ``created=True`` saves on
        MessageRecipient are intentionally skipped by the signal. The enqueue
        runs on ``transaction.on_commit``, so the capture fixture is required
        to flush it inside the test transaction.
        """
        message = factories.MessageFactory()
        recipient = factories.MessageRecipientFactory(
            message=message, delivery_status=None
        )

        with (
            patch("core.signals.enqueue_thread_reindex") as mock_enqueue,
            django_capture_on_commit_callbacks(execute=True),
        ):
            recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT
            recipient.save(update_fields=["delivery_status"])

        mock_enqueue.assert_called_once_with(message.thread_id)


class TestCoalescerRedisBackend:
    """Test the Redis path of the coalescing buffers (SADD/SPOP).

    Covers both the reindex and delete sets and the dedup logic that keeps a
    thread ID scheduled for deletion from also being reindexed in the same
    cycle.
    """

    @pytest.fixture(autouse=True)
    def _enable_opensearch_indexing(self, settings):
        settings.OPENSEARCH_INDEX_THREADS = True
        settings.CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache"}}

    def test_process_drains_reindex_set_and_enqueues_bulk(self):
        """Reindex IDs are handed off to bulk_reindex_threads_task."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            PENDING_REINDEX_KEY,
            process_pending_reindex,
        )

        ids = [
            b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_KEY:
                return []
            if key == PENDING_REINDEX_KEY:
                # Return the batch once, then empty so the loop terminates.
                spop_side_effect.calls += 1
                return ids if spop_side_effect.calls == 1 else []
            return []

        spop_side_effect.calls = 0

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_bulk,
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 2}
        mock_bulk.assert_called_once()
        (called_ids,) = mock_bulk.call_args[0]
        assert set(called_ids) == {tid.decode() for tid in ids}

    def test_process_drains_delete_set_and_enqueues_bulk_delete(self):
        """Delete IDs are handed off to bulk_delete_threads_task."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            process_pending_reindex,
        )

        ids = [b"dead-a", b"dead-b"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_KEY:
                spop_side_effect.calls += 1
                return ids if spop_side_effect.calls == 1 else []
            return []

        spop_side_effect.calls = 0

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {"deleted_threads": 2, "deleted_messages": 0, "reindexed": 0}
        mock_delete.assert_called_once()
        (called_ids,) = mock_delete.call_args[0]
        assert set(called_ids) == {tid.decode() for tid in ids}
        mock_reindex.assert_not_called()

    def test_process_does_not_consume_budget_on_fully_shadowed_reindex_batch(self):
        """A reindex batch fully shadowed by drained deletes does not cost budget.

        ``remaining_budget`` represents tasks actually handed off to the broker;
        a batch where every ID was filtered out before ``delay()`` enqueues
        nothing and must not count. The drain loop terminates when the reindex
        set empties, regardless of how many shadowed batches were skipped.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            PENDING_REINDEX_KEY,
            process_pending_reindex,
        )

        shadow_id = b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        counters = {"delete": 0, "reindex": 0}
        # More shadowed batches than the reindex budget would allow if the bug
        # came back: with max_batches=3 and 1 delete consumed, only 2 reindex
        # iterations would run before exhausting the budget. We feed 5.
        shadowed_batches = 5

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_KEY:
                counters["delete"] += 1
                # Single delete batch seeds drained_delete_ids, then empty.
                return [shadow_id] if counters["delete"] == 1 else []
            if key == PENDING_REINDEX_KEY:
                counters["reindex"] += 1
                if counters["reindex"] <= shadowed_batches:
                    return [shadow_id]
                return []
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex(max_batches=3)

        # All 5 shadowed batches are drained without consuming budget; the
        # loop only stops when SPOP returns empty (the 6th call).
        assert counters["reindex"] == shadowed_batches + 1
        assert result == {"deleted_threads": 1, "deleted_messages": 0, "reindexed": 0}
        mock_reindex.assert_not_called()

    def test_process_noop_when_both_sets_empty(self):
        """Empty buffers → no bulk task enqueued."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import process_pending_reindex

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
        ):
            mock_client.return_value.spop.return_value = []

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        mock_reindex.assert_not_called()
        mock_delete.assert_not_called()

    def test_enqueue_reindex_noop_for_none(self):
        """enqueue_thread_reindex(None) is a no-op and never touches Redis."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import enqueue_thread_reindex

        with patch("core.services.search.coalescer._redis_client") as mock_client:
            enqueue_thread_reindex(None)
            mock_client.assert_not_called()

    def test_enqueue_delete_noop_for_none(self):
        """enqueue_thread_delete(None) is a no-op and never touches Redis."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import enqueue_thread_delete

        with patch("core.services.search.coalescer._redis_client") as mock_client:
            enqueue_thread_delete(None)
            mock_client.assert_not_called()

    def test_enqueue_message_delete_encodes_pair(self):
        """``enqueue_message_delete`` SADDs ``thread_id:message_id`` into the dedicated set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            enqueue_message_delete,
        )

        with patch("core.services.search.coalescer._redis_client") as mock_client:
            enqueue_message_delete("thread-a", "msg-1")

        mock_client.return_value.sadd.assert_called_once_with(
            PENDING_DELETE_MESSAGES_KEY, "thread-a:msg-1"
        )

    def test_enqueue_message_delete_noop_when_either_arg_is_none(self):
        """A missing thread_id or message_id is a no-op (and never touches Redis)."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import enqueue_message_delete

        with patch("core.services.search.coalescer._redis_client") as mock_client:
            enqueue_message_delete(None, "msg-1")
            enqueue_message_delete("thread-a", None)
            enqueue_message_delete(None, None)

        mock_client.assert_not_called()

    def test_process_drains_message_delete_set(self):
        """Message-delete pairs are handed to bulk_delete_messages_task."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            process_pending_reindex,
        )

        pairs = [b"thread-a:msg-1", b"thread-b:msg-2"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_MESSAGES_KEY:
                spop_side_effect.calls += 1
                return pairs if spop_side_effect.calls == 1 else []
            return []

        spop_side_effect.calls = 0

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_delete_messages_task.delay"
            ) as mock_delete_messages,
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
            patch("core.services.search.tasks.bulk_reindex_threads_task.delay"),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {
            "deleted_threads": 0,
            "deleted_messages": 2,
            "reindexed": 0,
        }
        mock_delete_messages.assert_called_once()
        (called,) = mock_delete_messages.call_args[0]
        assert set(called) == {p.decode() for p in pairs}

    def test_process_restores_message_pairs_when_delay_fails(self):
        """Broker failure on the message delete task restores pairs to their set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            process_pending_reindex,
        )

        pairs = [b"thread-a:msg-1"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_MESSAGES_KEY:
                return pairs
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_delete_messages_task.delay",
                side_effect=Exception("broker down"),
            ),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {
            "deleted_threads": 0,
            "deleted_messages": 0,
            "reindexed": 0,
        }
        sadd_call = mock_client.return_value.sadd
        sadd_call.assert_called_once()
        args = sadd_call.call_args[0]
        assert args[0] == PENDING_DELETE_MESSAGES_KEY
        assert set(args[1:]) == {p.decode() for p in pairs}

    def test_enqueue_redis_error_swallowed(self):
        """Redis failure on enqueue never propagates to the caller."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            enqueue_thread_delete,
            enqueue_thread_reindex,
        )

        with patch(
            "core.services.search.coalescer._redis_client",
            side_effect=Exception("redis down"),
        ):
            # Must not raise.
            enqueue_thread_reindex("some-id")
            enqueue_thread_delete("some-id")

    def test_enqueue_redis_error_logs_explicit_message(self, coalescer_caplog):
        """A ``RedisError`` on enqueue is logged with a dedicated message.

        The generic ``except Exception`` catches everything, but only the
        ``RedisError`` branch surfaces the broker-level cause so it can be
        spotted at triage time instead of buried under a stack trace.
        """
        # pylint: disable-next=import-outside-toplevel
        from redis.exceptions import ConnectionError as RedisConnectionError

        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            enqueue_thread_reindex,
        )

        with patch("core.services.search.coalescer._redis_client") as mock_client:
            mock_client.return_value.sadd.side_effect = RedisConnectionError(
                "connection refused"
            )

            enqueue_thread_reindex("thread-x")

        assert any(
            "Redis unavailable while enqueuing thread-x" in rec.getMessage()
            and PENDING_REINDEX_KEY in rec.getMessage()
            for rec in coalescer_caplog.records
        )

    def test_drain_redis_error_returns_none_and_logs(self, coalescer_caplog):
        """A ``RedisError`` during ``SPOP`` returns ``None`` and stops the loop."""
        # pylint: disable-next=import-outside-toplevel
        from redis.exceptions import TimeoutError as RedisTimeoutError

        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            process_pending_reindex,
        )

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
        ):
            mock_client.return_value.spop.side_effect = RedisTimeoutError(
                "spop timeout"
            )

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        mock_delete.assert_not_called()
        mock_reindex.assert_not_called()
        assert any(
            "Redis unavailable while draining pending set" in rec.getMessage()
            and PENDING_DELETE_KEY in rec.getMessage()
            for rec in coalescer_caplog.records
        )

    def test_restore_redis_error_logs_explicit_message(self, coalescer_caplog):
        """A ``RedisError`` on the rescue ``SADD`` is logged explicitly."""
        # pylint: disable-next=import-outside-toplevel
        from redis.exceptions import ConnectionError as RedisConnectionError

        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            process_pending_reindex,
        )

        ids = [b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_REINDEX_KEY:
                return ids
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay",
                side_effect=Exception("broker down"),
            ),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect
            mock_client.return_value.sadd.side_effect = RedisConnectionError(
                "connection refused"
            )

            # Must not raise even when the rescue path also hits Redis down.
            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        assert any(
            "Redis unavailable while restoring" in rec.getMessage()
            and PENDING_REINDEX_KEY in rec.getMessage()
            for rec in coalescer_caplog.records
        )

    def test_process_restores_ids_when_reindex_delay_fails(self):
        """A broker failure after SPOP must not lose the drained IDs.

        ``SPOP count=N`` removes the IDs atomically before we know whether
        Celery accepted the task. If ``delay()`` raises, the coalescer
        re-``SADD``s them so the next cycle retries them.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            PENDING_REINDEX_KEY,
            process_pending_reindex,
        )

        ids = [
            b"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_KEY:
                return []
            if key == PENDING_REINDEX_KEY:
                return ids
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay",
                side_effect=Exception("broker down"),
            ),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        sadd_call = mock_client.return_value.sadd
        sadd_call.assert_called_once()
        args = sadd_call.call_args[0]
        assert args[0] == PENDING_REINDEX_KEY
        assert set(args[1:]) == {tid.decode() for tid in ids}

    def test_process_restores_ids_when_delete_delay_fails(self):
        """Broker failure on the delete task restores delete IDs to their set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            process_pending_reindex,
        )

        ids = [b"dead-a"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_DELETE_KEY:
                return ids
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay",
                side_effect=Exception("broker down"),
            ),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        sadd_call = mock_client.return_value.sadd
        sadd_call.assert_called_once()
        args = sadd_call.call_args[0]
        assert args[0] == PENDING_DELETE_KEY
        assert set(args[1:]) == {tid.decode() for tid in ids}

    def test_process_swallows_sadd_failure_after_delay_fails(self):
        """If both ``delay`` and the rescue ``SADD`` fail, we still don't raise."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            process_pending_reindex,
        )

        ids = [b"cccccccc-cccc-cccc-cccc-cccccccccccc"]

        def spop_side_effect(key, count=None):  # pylint: disable=unused-argument
            if key == PENDING_REINDEX_KEY:
                return ids
            return []

        with (
            patch("core.services.search.coalescer._redis_client") as mock_client,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay",
                side_effect=Exception("broker down"),
            ),
        ):
            mock_client.return_value.spop.side_effect = spop_side_effect
            mock_client.return_value.sadd.side_effect = Exception("redis down")

            # Must not raise.
            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}


@pytest.mark.redis
class TestCoalescerRedisRoundtrip:
    """End-to-end roundtrip tests against the real Redis service.

    Hits ``django_redis`` via the ``redis_cache`` fixture (per-worker
    DB isolation, ``flushdb`` setup/teardown). Complements
    ``TestCoalescerRedisBackend`` (mocked client, exercises failure
    paths) with real-server coverage of dedup, ordering, restore-on-
    broker-failure, and multi-batch chunking.
    """

    @pytest.fixture(autouse=True)
    def _enable_opensearch_indexing(self, settings):
        settings.OPENSEARCH_INDEX_THREADS = True

    def test_enqueue_and_process_roundtrip_with_dedup(self, redis_cache):  # pylint: disable=unused-argument
        """Enqueuing the same ID twice results in a single drain entry."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay"
        ) as mock_bulk:
            enqueue_thread_reindex("thread-a")
            enqueue_thread_reindex("thread-b")
            enqueue_thread_reindex("thread-a")  # dedup

            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 2}
        mock_bulk.assert_called_once()
        (called_ids,) = mock_bulk.call_args[0]
        assert set(called_ids) == {"thread-a", "thread-b"}

    def test_delete_wins_over_reindex_for_same_thread(self, redis_cache):  # pylint: disable=unused-argument
        """A thread present in both sets is deleted, not reindexed."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            enqueue_thread_delete,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        enqueue_thread_reindex("thread-a")
        enqueue_thread_reindex("thread-b")
        enqueue_thread_delete("thread-a")

        with (
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
        ):
            result = process_pending_reindex()

        assert result == {"deleted_threads": 1, "deleted_messages": 0, "reindexed": 1}
        mock_delete.assert_called_once()
        (called_delete_ids,) = mock_delete.call_args[0]
        assert set(called_delete_ids) == {"thread-a"}

        mock_reindex.assert_called_once()
        (called_reindex_ids,) = mock_reindex.call_args[0]
        assert set(called_reindex_ids) == {"thread-b"}

    def test_process_skips_reindex_handoff_when_fully_shadowed_by_delete(
        self,
        redis_cache,  # pylint: disable=unused-argument
    ):
        """If every reindex ID is already in the delete set, no reindex task is enqueued."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            enqueue_thread_delete,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        enqueue_thread_reindex("thread-a")
        enqueue_thread_delete("thread-a")

        with (
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
        ):
            result = process_pending_reindex()

        assert result == {"deleted_threads": 1, "deleted_messages": 0, "reindexed": 0}
        mock_reindex.assert_not_called()

    def test_process_noop_when_empty(self, redis_cache):  # pylint: disable=unused-argument
        """Empty buffers → no bulk task enqueued."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import process_pending_reindex

        with (
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
        ):
            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        mock_reindex.assert_not_called()
        mock_delete.assert_not_called()

    @staticmethod
    def _smembers(client, key):
        """Return the SET at ``key`` decoded as Python strings."""
        return {m.decode() if isinstance(m, bytes) else m for m in client.smembers(key)}

    def test_enqueue_noop_for_none(self, redis_cache):
        """``enqueue_thread_*(None)`` never writes to Redis."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            PENDING_REINDEX_KEY,
            enqueue_thread_delete,
            enqueue_thread_reindex,
        )

        enqueue_thread_reindex(None)
        enqueue_thread_delete(None)
        assert not redis_cache.exists(PENDING_REINDEX_KEY)
        assert not redis_cache.exists(PENDING_DELETE_KEY)

    def test_process_empties_keys_after_full_drain(self, redis_cache):
        """A complete drain leaves both pending sets empty."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_KEY,
            PENDING_REINDEX_KEY,
            enqueue_thread_delete,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        enqueue_thread_reindex("thread-a")
        enqueue_thread_delete("thread-b")

        with (
            patch("core.services.search.tasks.bulk_reindex_threads_task.delay"),
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
        ):
            process_pending_reindex()

        assert not redis_cache.exists(PENDING_REINDEX_KEY)
        assert not redis_cache.exists(PENDING_DELETE_KEY)

    def test_process_restores_reindex_ids_when_delay_fails(self, redis_cache):
        """A broker failure after drain must not lose the IDs."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        enqueue_thread_reindex("thread-a")
        enqueue_thread_reindex("thread-b")

        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay",
            side_effect=Exception("broker down"),
        ):
            result = process_pending_reindex()

        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 0}
        assert self._smembers(redis_cache, PENDING_REINDEX_KEY) == {
            "thread-a",
            "thread-b",
        }

    def test_enqueue_message_delete_encodes_pair(self, redis_cache):
        """``enqueue_message_delete`` SADDs ``thread_id:message_id`` into the pending set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            enqueue_message_delete,
        )

        enqueue_message_delete("thread-a", "msg-1")

        assert self._smembers(redis_cache, PENDING_DELETE_MESSAGES_KEY) == {
            "thread-a:msg-1"
        }

    def test_enqueue_message_delete_noop_when_either_arg_is_none(self, redis_cache):
        """A missing thread_id or message_id is a no-op (no SADD)."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            enqueue_message_delete,
        )

        enqueue_message_delete(None, "msg-1")
        enqueue_message_delete("thread-a", None)
        enqueue_message_delete(None, None)

        assert not redis_cache.exists(PENDING_DELETE_MESSAGES_KEY)

    def test_process_drains_message_delete_set(self, redis_cache):
        """Message-delete pairs are handed to bulk_delete_messages_task."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            enqueue_message_delete,
            process_pending_reindex,
        )

        enqueue_message_delete("thread-a", "msg-1")
        enqueue_message_delete("thread-b", "msg-2")

        with (
            patch(
                "core.services.search.tasks.bulk_delete_messages_task.delay"
            ) as mock_delete_messages,
            patch("core.services.search.tasks.bulk_delete_threads_task.delay"),
            patch("core.services.search.tasks.bulk_reindex_threads_task.delay"),
        ):
            result = process_pending_reindex()

        assert result == {
            "deleted_threads": 0,
            "deleted_messages": 2,
            "reindexed": 0,
        }
        mock_delete_messages.assert_called_once()
        (called,) = mock_delete_messages.call_args[0]
        assert set(called) == {"thread-a:msg-1", "thread-b:msg-2"}
        assert not redis_cache.exists(PENDING_DELETE_MESSAGES_KEY)

    def test_process_restores_message_pairs_when_delay_fails(self, redis_cache):
        """Broker failure on the message delete task restores pairs to the pending set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_DELETE_MESSAGES_KEY,
            enqueue_message_delete,
            process_pending_reindex,
        )

        enqueue_message_delete("thread-a", "msg-1")

        with patch(
            "core.services.search.tasks.bulk_delete_messages_task.delay",
            side_effect=Exception("broker down"),
        ):
            result = process_pending_reindex()

        assert result == {
            "deleted_threads": 0,
            "deleted_messages": 0,
            "reindexed": 0,
        }
        assert self._smembers(redis_cache, PENDING_DELETE_MESSAGES_KEY) == {
            "thread-a:msg-1"
        }

    def test_process_chunks_pending_into_multiple_tasks(self, redis_cache):
        """batch_size caps each Celery payload, process drains the whole set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        for i in range(5):
            enqueue_thread_reindex(f"thread-{i}")

        with patch(
            "core.services.search.tasks.bulk_reindex_threads_task.delay"
        ) as mock_bulk:
            result = process_pending_reindex(batch_size=3)

        # Single cycle drains everything, split into 3+2 across two tasks.
        assert result == {"deleted_threads": 0, "deleted_messages": 0, "reindexed": 5}
        assert mock_bulk.call_count == 2
        sent = [call.args[0] for call in mock_bulk.call_args_list]
        assert sorted(len(chunk) for chunk in sent) == [2, 3]
        assert not redis_cache.exists(PENDING_REINDEX_KEY)

    def test_process_max_batches_shared_across_delete_and_reindex(self, redis_cache):
        """``max_batches`` is a single budget shared across both handoffs.

        Delete batches are drained first so they consume the budget before
        reindex batches; any leftover in either set waits for the next cycle.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import (
            PENDING_REINDEX_KEY,
            enqueue_thread_delete,
            enqueue_thread_reindex,
            process_pending_reindex,
        )

        # Two delete batches at batch_size=1 exhaust max_batches=2 before
        # reindex gets any budget.
        enqueue_thread_delete("dead-a")
        enqueue_thread_delete("dead-b")
        enqueue_thread_reindex("thread-a")

        with (
            patch(
                "core.services.search.tasks.bulk_delete_threads_task.delay"
            ) as mock_delete,
            patch(
                "core.services.search.tasks.bulk_reindex_threads_task.delay"
            ) as mock_reindex,
        ):
            result = process_pending_reindex(batch_size=1, max_batches=2)

        assert result == {"deleted_threads": 2, "deleted_messages": 0, "reindexed": 0}
        assert mock_delete.call_count == 2
        mock_reindex.assert_not_called()
        # The reindex set is untouched — it waits for the next cycle.
        assert self._smembers(redis_cache, PENDING_REINDEX_KEY) == {"thread-a"}


class TestPostDeleteSignals:
    """Test that post_delete signals route to the right coalescing buffer."""

    @pytest.fixture(autouse=True)
    def _enable_opensearch_indexing(self, settings):
        settings.OPENSEARCH_INDEX_THREADS = True

    def test_message_delete_enqueues_message_delete(
        self, django_capture_on_commit_callbacks
    ):
        """Deleting a Message enqueues a targeted child-doc delete.

        We no longer schedule a thread reindex on message delete: that
        path used to rely on a ``delete_by_query`` orphan purge inside
        ``reindex_bulk_threads``, which was the source of the cluster
        503s. The signal now hands off ``(thread_id, message_id)`` to
        ``bulk_delete_messages_task`` for a cheap ``bulk delete by _id``.
        """
        message = factories.MessageFactory()
        thread_id = str(message.thread_id)
        message_id = str(message.id)

        with (
            patch("core.signals.enqueue_message_delete") as mock_enqueue,
            patch("core.signals.enqueue_thread_reindex") as mock_reindex,
            django_capture_on_commit_callbacks(execute=True),
        ):
            message.delete()

        mock_enqueue.assert_called_once_with(thread_id, message_id)
        mock_reindex.assert_not_called()

    def test_thread_delete_enqueues_delete_and_cascades_message_deletes(
        self, django_capture_on_commit_callbacks
    ):
        """Deleting a Thread enqueues the parent + every cascaded child message.

        ``bulk_delete_threads_task`` now only sweeps the thread parent
        doc, so child message docs must each ride the dedicated
        ``pending_delete_messages`` set. Django fires ``post_delete`` for
        cascaded rows, so the ``Message.post_delete`` handler picks them
        up automatically.
        """
        thread = factories.ThreadFactory()
        msg_a = factories.MessageFactory(thread=thread)
        msg_b = factories.MessageFactory(thread=thread)
        thread_id = str(thread.id)

        with (
            patch("core.signals.enqueue_thread_delete") as mock_enqueue_thread,
            patch("core.signals.enqueue_message_delete") as mock_enqueue_msg,
            django_capture_on_commit_callbacks(execute=True),
        ):
            thread.delete()

        mock_enqueue_thread.assert_called_once_with(thread_id)
        called_pairs = {
            (call.args[0], call.args[1]) for call in mock_enqueue_msg.call_args_list
        }
        assert called_pairs == {
            (thread_id, str(msg_a.id)),
            (thread_id, str(msg_b.id)),
        }

    def test_delete_respects_disabled_setting(
        self, settings, django_capture_on_commit_callbacks
    ):
        """With ``OPENSEARCH_INDEX_THREADS=False``, no enqueue fires."""
        settings.OPENSEARCH_INDEX_THREADS = False
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread)

        with (
            patch("core.signals.enqueue_thread_delete") as mock_enqueue_delete,
            patch("core.signals.enqueue_thread_reindex") as mock_enqueue_reindex,
            patch("core.signals.enqueue_message_delete") as mock_enqueue_msg,
            django_capture_on_commit_callbacks(execute=True),
        ):
            thread.delete()

        mock_enqueue_delete.assert_not_called()
        mock_enqueue_reindex.assert_not_called()
        mock_enqueue_msg.assert_not_called()

    @pytest.mark.django_db(transaction=True)
    def test_rollback_does_not_enqueue_delete(self):
        """A delete inside a rolled-back transaction must not enqueue anything."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread)

        with (
            patch("core.signals.enqueue_thread_delete") as mock_enqueue_delete,
            patch("core.signals.enqueue_thread_reindex") as mock_enqueue_reindex,
            patch("core.signals.enqueue_message_delete") as mock_enqueue_msg,
        ):
            with contextlib.suppress(RuntimeError), transaction.atomic():
                thread.delete()
                raise RuntimeError("force rollback")

            mock_enqueue_delete.assert_not_called()
            mock_enqueue_reindex.assert_not_called()
            mock_enqueue_msg.assert_not_called()


class TestCoalescerNonRedisGuard:
    """Any non-Redis cache backend must warn instead of silently dropping IDs.

    The coalescer requires ``django_redis`` for atomic SADD/SPOP semantics;
    other backends (Dummy, LocMem, FileBased, …) skip with a warning so a
    misconfigured environment fails loudly rather than letting the index
    drift silently from the database.
    """

    @pytest.fixture(autouse=True)
    def _enable_opensearch_indexing(self, settings):
        settings.OPENSEARCH_INDEX_THREADS = True

    @pytest.mark.parametrize(
        "backend",
        [
            "django.core.cache.backends.dummy.DummyCache",
            "django.core.cache.backends.locmem.LocMemCache",
        ],
    )
    def test_enqueue_warns_and_skips_redis_call(self, settings, backend):
        """``_enqueue`` logs a warning and never reaches ``_redis_client``."""
        settings.CACHES = {"default": {"BACKEND": backend}}
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import enqueue_thread_reindex

        with (
            patch("core.services.search.coalescer.logger") as mock_logger,
            patch("core.services.search.coalescer._redis_client") as mock_client,
        ):
            enqueue_thread_reindex("thread-a")

            mock_client.assert_not_called()
            mock_logger.warning.assert_called_once()
