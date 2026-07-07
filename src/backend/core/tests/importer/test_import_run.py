"""Tests for the unified resumable import task + scheduler.

The per-source runners are mocked so these exercise the orchestration
(guards, lock, resume watermark hand-off, terminal transition, scheduler)
without S3/IMAP infrastructure.
"""

# pylint: disable=redefined-outer-name, unused-argument, protected-access, no-value-for-parameter

import contextvars
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

import pytest
from botocore.exceptions import ClientError

from core import enums, factories, models
from core.services.importer import channel as channel_module
from core.services.importer import tasks
from core.services.importer.channel import (
    acquire_run_lock,
    create_import_channel,
    record_progress,
)
from core.services.importer.tasks import (
    run_import_task,
    schedule_imports_task,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    return factories.MailboxFactory()


def _import(mailbox, user, source=enums.ImportSource.MBOX):
    return create_import_channel(
        recipient=mailbox, user=user, source_type=source.value, file_key="k"
    )


# The file sources' stuck budget, read through the public limits map (not the
# internal ``FILE_STUCK_RETRIES`` constant that feeds it).
FILE_BUDGET = tasks.STUCK_RETRY_LIMITS[enums.ImportSource.MBOX.value]


@pytest.mark.django_db
class TestRunImportTask:
    def test_runs_and_finalizes(self, mailbox, user):
        channel = _import(mailbox, user)
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.MBOX.value: lambda ch, st: (5, 1, 6)},
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "SUCCESS"
        channel.refresh_from_db()
        assert channel.is_active is False
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )
        assert channel.settings["import"]["success"] == 5

    def test_unknown_channel_returns_not_found(self, mailbox, user):
        result = run_import_task("00000000-0000-0000-0000-000000000000")
        assert result["status"] == "NOT_FOUND"

    def test_unknown_source_type_marks_failed(self, mailbox, user):
        """A channel with a bogus source_type fails loudly and terminally —
        it must not stay active for the scheduler to redispatch forever."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type="carrier-pigeon", file_key="k"
        )
        result = run_import_task(str(channel.id))
        assert result["status"] == "FAILURE"
        channel.refresh_from_db()
        assert channel.is_active is False
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.FAILED.value
        assert "unknown source_type" in run["error"]

    def test_inactive_channel_is_skipped(self, mailbox, user):
        channel = _import(mailbox, user)
        channel.is_active = False
        channel.save(update_fields=["is_active"])
        called = []
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.MBOX.value: lambda *a: called.append(1) or (0, 0, 0)},
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "INACTIVE"
        assert called == []

    def test_lock_prevents_concurrent_run(self, mailbox, user):
        channel = _import(mailbox, user)
        assert acquire_run_lock(channel.id)  # someone else holds it
        called = []
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.MBOX.value: lambda *a: called.append(1) or (0, 0, 0)},
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "ALREADY_RUNNING"
        assert called == []  # the runner never executed

    def test_stale_holder_cannot_release_a_re_dispatched_lock(self, mailbox, user):
        """A run whose lock expired (it stalled) and was re-dispatched to a
        second execution in the *same process* must not, on waking, release the
        new holder's lock. The ownership token is per-execution (ContextVar),
        so the stale holder sees its own token, not the new one — the guard
        this asserts would fail if the token store were a plain module dict a
        same-process re-acquire could overwrite.
        """
        channel = _import(mailbox, user)
        lock_key = channel_module._lock_key(channel.id)

        # Execution A acquires, then stalls: its lock TTL-expires.
        assert acquire_run_lock(str(channel.id))
        channel_module.cache.delete(lock_key)

        # Execution B (a separate context, as a greenlet/thread pool would give
        # it) picks up the re-dispatch and acquires a fresh token.
        ctx_b = contextvars.copy_context()
        assert ctx_b.run(acquire_run_lock, str(channel.id))
        token_b = channel_module.cache.get(lock_key)
        assert token_b is not None

        # Zombie A wakes and tries to renew/release: it must touch nothing,
        # because in its own context it still holds only its (now-dead) token.
        # Renew first — release clears A's token, so testing renew afterwards
        # would exercise the trivial "no token" path instead of the mismatch.
        channel_module.renew_run_lock(str(channel.id))
        assert channel_module.cache.get(lock_key) == token_b
        channel_module.release_run_lock(str(channel.id))
        assert channel_module.cache.get(lock_key) == token_b

        # B can still release its own lock from its own context.
        ctx_b.run(channel_module.release_run_lock, str(channel.id))
        assert channel_module.cache.get(lock_key) is None

    def test_resume_hands_watermark_to_runner(self, mailbox, user):
        """A re-dispatch passes the persisted Redis watermark to the runner so
        it can skip already-processed messages."""
        channel = _import(mailbox, user)
        record_progress(channel.id, success=3, failure=0, cursor=3)
        seen = {}

        def fake_runner(ch, state):
            seen.update(state)
            return state.get("success", 0), state.get("failure", 0), 10

        with patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: fake_runner}):
            result = run_import_task(str(channel.id))
        assert seen["cursor"] == 3
        assert seen["success"] == 3
        # ... and the runner's returned counts round-trip into the terminal
        # snapshot, not just into the task result.
        assert result["status"] == "SUCCESS"
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.COMPLETED.value
        assert (run["success"], run["total"]) == (3, 10)

    def test_failure_marks_failed(self, mailbox, user):
        channel = _import(mailbox, user)

        def boom(ch, state):
            raise RuntimeError("corrupt archive")

        with patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: boom}):
            result = run_import_task(str(channel.id))
        assert result["status"] == "FAILURE"
        channel.refresh_from_db()
        assert channel.is_active is False
        assert channel.settings["import"]["status"] == enums.ImportStatus.FAILED.value

    def test_run_advances_heartbeat(self, mailbox, user):
        """Even a run that imports nothing bumps last_used_at, so the scheduler
        doesn't re-fire a continuous channel every tick."""
        channel = _import(mailbox, user)
        assert channel.last_used_at is None
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.MBOX.value: lambda *a: (0, 0, 0)},
        ):
            run_import_task(str(channel.id))
        channel.refresh_from_db()
        assert channel.last_used_at is not None

    def test_cancel_mid_run_skips_completed_and_purges(self, mailbox, user):
        """A runner that unwinds via ImportCancelled must not overwrite the
        cancelled status with completed, and must purge what it delivered."""
        channel = _import(mailbox, user)

        def cancelling_runner(ch, state):
            # Real flow: the API cancels (durable status + Redis flag) and the
            # runner's next beat() unwinds via ImportCancelled.
            channel_module.mark_cancelled(ch)
            raise tasks.ImportCancelled()

        with (
            patch.dict(
                tasks._RUNNERS, {enums.ImportSource.MBOX.value: cancelling_runner}
            ),
            patch.object(tasks, "purge_import_messages") as purge,
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "CANCELLED"
        purge.assert_called_once()
        # The durable CANCELLED write had landed, so the worker also removed
        # the settled run's row — a cancelled import leaves the list entirely.
        assert not models.Channel.objects.filter(id=channel.id).exists()

    def test_cancel_requested_at_completion_purges(self, mailbox, user):
        """A cancel landing between the last flush and the runner returning is
        still honoured (no completed overwrite) — even when only the Redis flag
        made it (the durable CANCELLED write hasn't landed yet)."""
        channel = _import(mailbox, user)

        def fake_runner(ch, state):
            channel_module.request_cancel(ch.id)
            return (3, 0, 3)

        with (
            patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: fake_runner}),
            patch.object(tasks, "purge_import_messages") as purge,
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "CANCELLED"
        purge.assert_called_once()
        channel.refresh_from_db()
        # The flag-only path writes no terminal snapshot itself: the durable
        # status stays exactly as it was (pending), never COMPLETED — the
        # concurrent API cancel owns the CANCELLED write. For the same reason
        # the row is NOT deleted here: removal belongs to whoever lands the
        # durable CANCELLED (the API's cancel_import_task).
        assert channel.settings["import"]["status"] == enums.ImportStatus.PENDING.value

    def test_cancel_survives_redis_eviction_at_completion(self, mailbox, user):
        """If the Redis cancel flag is evicted before the runner returns, the
        durable CANCELLED status must still win — the run must not overwrite it
        with COMPLETED (C3)."""
        channel = _import(mailbox, user)

        def fake_runner(ch, state):
            # Cancel lands mid-run (sets durable CANCELLED + is_active=False +
            # Redis flag), then the whole Redis state is evicted under memory
            # pressure — losing the cancel flag.
            channel_module.mark_cancelled(ch)
            channel_module.clear_state(ch.id)
            return (3, 0, 3)

        with (
            patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: fake_runner}),
            patch.object(tasks, "purge_import_messages") as purge,
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "CANCELLED"
        purge.assert_called_once()
        # Durable CANCELLED had landed → the settled row is removed too.
        assert not models.Channel.objects.filter(id=channel.id).exists()

    def test_failure_after_cancel_stays_cancelled(self, mailbox, user):
        """A runner crash landing after a cancel (with the Redis flag already
        evicted) must not overwrite the durable CANCELLED status with FAILED —
        and must still purge what the run delivered."""
        channel = _import(mailbox, user)

        def crashing_runner(ch, state):
            channel_module.mark_cancelled(ch)
            channel_module.clear_state(ch.id)
            raise RuntimeError("boom")

        with (
            patch.dict(
                tasks._RUNNERS, {enums.ImportSource.MBOX.value: crashing_runner}
            ),
            patch.object(tasks, "purge_import_messages") as purge,
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "CANCELLED"
        purge.assert_called_once()
        assert not models.Channel.objects.filter(id=channel.id).exists()

    def test_rearmed_cancelled_import_completes_without_purge(self, mailbox, user):
        """A cancelled import that is re-armed must complete like a fresh run:
        the stale CANCELLED snapshot must not trigger the completion backstop
        (purge + row deletion) against the mail it just imported."""
        channel = _import(mailbox, user)
        channel_module.cancel_import(channel)
        channel_module.enable_continuous(channel)

        with (
            patch.dict(
                tasks._RUNNERS,
                {enums.ImportSource.MBOX.value: lambda ch, st: (3, 0, 3)},
            ),
            patch.object(tasks, "purge_import_messages") as purge,
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "SUCCESS"
        purge.assert_not_called()
        channel.refresh_from_db()  # row still exists
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )

    def test_transient_stalls_fail_after_budget(self, mailbox, user):
        """A 'transient' error that survives the file stuck budget
        (FILE_BUDGET re-dispatches with zero progress) is
        permanent: the run must end FAILED with a durable error instead of
        staying 'in progress' forever."""
        channel = _import(mailbox, user)

        def stalling_runner(ch, state):
            raise tasks.TransientImportError("IMAP fetch failed for uid 42")

        with patch.dict(
            tasks._RUNNERS, {enums.ImportSource.MBOX.value: stalling_runner}
        ):
            for _ in range(FILE_BUDGET - 1):
                assert run_import_task(str(channel.id))["status"] == "RETRY"
            result = run_import_task(str(channel.id))
        assert result["status"] == "FAILURE"
        channel.refresh_from_db()
        assert channel.is_active is False
        assert "uid 42" in channel.settings["import"]["error"]

    def test_transient_stall_budget_resets_on_progress(self, mailbox, user):
        """Progress between stalls proves the run is alive: the budget counts
        only *consecutive* stalls at the same watermark."""
        channel = _import(mailbox, user)

        def stalling_runner(ch, state):
            raise tasks.TransientImportError("blip")

        with patch.dict(
            tasks._RUNNERS, {enums.ImportSource.MBOX.value: stalling_runner}
        ):
            for _ in range(FILE_BUDGET - 1):
                assert run_import_task(str(channel.id))["status"] == "RETRY"
            # The next run makes progress before stalling again.
            record_progress(channel.id, success=1, failure=0)
            result = run_import_task(str(channel.id))
        assert result["status"] == "RETRY"
        channel.refresh_from_db()
        assert channel.is_active is True

    def test_transient_stall_budget_resets_after_successful_run(self, mailbox, user):
        """A successful pass clears the accumulated budget: a quiet continuous
        poller (whose stuck marker never changes) must not sum unrelated
        blips weeks apart into a permanent FAILED."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.MBOX.value,
            file_key="k",
            mode=enums.ImportMode.CONTINUOUS.value,
        )

        def stalling_runner(ch, state):
            raise tasks.TransientImportError("blip")

        with patch.dict(
            tasks._RUNNERS, {enums.ImportSource.MBOX.value: stalling_runner}
        ):
            for _ in range(FILE_BUDGET - 1):
                assert run_import_task(str(channel.id))["status"] == "RETRY"
        # One healthy poll in between...
        with patch.dict(
            tasks._RUNNERS, {enums.ImportSource.MBOX.value: lambda ch, st: (0, 0, 0)}
        ):
            assert run_import_task(str(channel.id))["status"] == "SUCCESS"
        # ...restores the full retry allowance (without the reset the very
        # first of these blips would already exhaust the budget).
        with patch.dict(
            tasks._RUNNERS, {enums.ImportSource.MBOX.value: stalling_runner}
        ):
            for _ in range(FILE_BUDGET - 1):
                assert run_import_task(str(channel.id))["status"] == "RETRY"
        channel.refresh_from_db()
        assert channel.is_active is True

    def test_imap_stuck_budget_far_exceeds_file(self, mailbox, user):
        """A down IMAP server must not permanently disable a continuous poller:
        its stuck budget (sized from the poll interval, ~5 days) is far
        larger than a file import's, so many consecutive failures keep RETRYing."""
        imap_limit = tasks.STUCK_RETRY_LIMITS[enums.ImportSource.IMAP.value]
        file_limit = tasks.STUCK_RETRY_LIMITS[enums.ImportSource.MBOX.value]
        assert imap_limit > file_limit + 3
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )

        def down_server(ch, state):
            raise tasks.TransientImportError("IMAP connection error: server down")

        with patch.dict(tasks._RUNNERS, {enums.ImportSource.IMAP.value: down_server}):
            for _ in range(file_limit + 3):  # well past the file budget
                assert run_import_task(str(channel.id))["status"] == "RETRY"
        channel.refresh_from_db()
        assert channel.is_active is True

    def test_file_s3_error_is_resumable(self, mailbox, user):
        """An S3 error during a file import leaves the run resumable (RETRY,
        still active) instead of terminally FAILED on the first hiccup."""
        channel = _import(mailbox, user)

        def s3_blip(ch, state):
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "GetObject")

        with patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: s3_blip}):
            result = run_import_task(str(channel.id))
        assert result["status"] == "RETRY"
        channel.refresh_from_db()
        assert channel.is_active is True

    def test_zero_success_oneshot_marks_failed_with_error(self, mailbox, user):
        """A oneshot run where every message failed is a FAILED run with an
        explanation, not a quiet COMPLETED with error=null."""
        channel = _import(mailbox, user)
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.MBOX.value: lambda ch, st: (0, 3, 3)},
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "FAILURE"
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.FAILED.value
        assert "could be imported" in run["error"]
        assert channel.is_active is False

    def test_zero_success_continuous_poll_stays_active(self, mailbox, user):
        """A continuous poll with a bad batch must not be terminally failed —
        that would silently disable the poller."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )
        with patch.dict(
            tasks._RUNNERS,
            {enums.ImportSource.IMAP.value: lambda ch, st: (0, 2, 2)},
        ):
            result = run_import_task(str(channel.id))
        assert result["status"] == "SUCCESS"
        channel.refresh_from_db()
        assert channel.is_active is True
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )

    def test_transient_error_leaves_channel_resumable(self, mailbox, user):
        """A TransientImportError must NOT terminally disable the channel — the
        scheduler needs to be able to re-dispatch and resume it."""
        channel = _import(mailbox, user)

        def flaky(ch, state):
            raise tasks.TransientImportError("fetch timeout")

        with patch.dict(tasks._RUNNERS, {enums.ImportSource.MBOX.value: flaky}):
            result = run_import_task(str(channel.id))
        assert result["status"] == "RETRY"
        channel.refresh_from_db()
        assert channel.is_active is True
        assert (
            channel.settings["import"]["status"] not in channel_module.TERMINAL_STATUSES
        )
        # The run lock was released, so the scheduler's re-dispatch can
        # actually acquire it and resume.
        assert acquire_run_lock(channel.id)


def _continuous(mailbox, user):
    return create_import_channel(
        recipient=mailbox,
        user=user,
        source_type=enums.ImportSource.IMAP.value,
        mode=enums.ImportMode.CONTINUOUS.value,
        imap_credentials={"username": "u", "password": "p"},
    )


@pytest.mark.django_db
class TestScheduleImports:
    def test_redispatches_stale_active_import(self, mailbox, user):
        channel = _import(mailbox, user)
        # Stale heartbeat, still active -> looks crashed mid-run.
        channel.last_used_at = timezone.now() - timedelta(hours=1)
        channel.save(update_fields=["last_used_at"])
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        assert result["redispatched"] == 1
        mock_delay.assert_called_once_with(str(channel.id))

    def test_skips_fresh_import(self, mailbox, user):
        channel = _import(mailbox, user)
        channel.last_used_at = timezone.now()
        channel.save(update_fields=["last_used_at"])
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        assert result["redispatched"] == 0
        mock_delay.assert_not_called()

    def test_skips_finished_import(self, mailbox, user):
        channel = _import(mailbox, user)
        channel.is_active = False
        channel.last_used_at = timezone.now() - timedelta(hours=1)
        channel.save(update_fields=["is_active", "last_used_at"])
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        mock_delay.assert_not_called()
        assert result["redispatched"] == 0

    def test_continuous_polled_when_interval_elapsed(self, mailbox, user, settings):
        settings.MESSAGES_IMPORT_IMAP_POLL_INTERVAL = 600  # seconds
        channel = _continuous(mailbox, user)
        channel.last_used_at = timezone.now() - timedelta(minutes=11)
        channel.save(update_fields=["last_used_at"])
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        assert result["redispatched"] == 1
        mock_delay.assert_called_once_with(str(channel.id))

    def test_continuous_skipped_within_interval(self, mailbox, user, settings):
        settings.MESSAGES_IMPORT_IMAP_POLL_INTERVAL = 600  # seconds
        channel = _continuous(mailbox, user)
        channel.last_used_at = timezone.now() - timedelta(minutes=5)
        channel.save(update_fields=["last_used_at"])
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        mock_delay.assert_not_called()
        assert result["redispatched"] == 0

    def test_never_run_channel_with_null_heartbeat_is_dispatched(self, mailbox, user):
        """A just-created (or just re-armed) channel has last_used_at=None —
        the due-check's NULL branch must dispatch it promptly."""
        channel = _import(mailbox, user)
        assert channel.last_used_at is None
        with patch.object(run_import_task, "delay") as mock_delay:
            result = schedule_imports_task()
        assert result["redispatched"] == 1
        mock_delay.assert_called_once_with(str(channel.id))

    def test_cancel_task_with_missing_channel_is_a_noop(self, mailbox, user):
        result = tasks.cancel_import_task("00000000-0000-0000-0000-000000000000")
        assert result == {
            "messages_deleted": 0,
            "messages_kept": 0,
            "threads_deleted": 0,
        }

    def test_cancel_task_purges_and_removes_the_row(self, mailbox, user):
        """The background cancel deletes the run's messages AND its row: a
        cancelled import disappears from /imports/ entirely."""
        channel = _import(mailbox, user)
        tasks.cancel_import_task(str(channel.id))
        assert not models.Channel.objects.filter(id=channel.id).exists()

    def test_cancel_task_keeps_the_row_while_a_worker_holds_the_lock(
        self, mailbox, user
    ):
        """A worker mid-abort still needs the row (its late deliveries would be
        orphaned by SET_NULL): the background cancel purges but leaves the
        removal to that worker's own post-cancel handling."""
        channel = _import(mailbox, user)
        assert acquire_run_lock(str(channel.id))
        try:
            tasks.cancel_import_task(str(channel.id))
        finally:
            channel_module.release_run_lock(str(channel.id))
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_broker_error_on_one_dispatch_does_not_abort_scan(self, mailbox, user):
        """A dispatch failure for one due channel must not swallow the rest of
        the scan — crash-recovery and polling keep going for other tenants."""
        first = _import(mailbox, user)
        first.last_used_at = timezone.now() - timedelta(hours=1)
        first.save(update_fields=["last_used_at"])
        second = _import(mailbox, user)
        second.last_used_at = timezone.now() - timedelta(hours=1)
        second.save(update_fields=["last_used_at"])
        with patch.object(
            run_import_task, "delay", side_effect=[RuntimeError("broker down"), None]
        ) as mock_delay:
            result = schedule_imports_task()
        assert mock_delay.call_count == 2
        assert result["redispatched"] == 1
