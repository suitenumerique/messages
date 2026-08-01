"""Tests for the background task worker configuration."""

# pylint: disable=import-outside-toplevel

import pytest


class TestWorkerQueueConfiguration:
    """Test worker queue definitions and routing."""

    def test_all_queues_defined_in_priority_order(self):
        """Verify all queues are defined in the expected priority order."""
        # Import here to avoid import issues during test collection
        import worker

        expected_queues = [
            "inbound",
            "outbound",
            "default",
            "imports",
            "blobs",
            "reindex",
        ]
        assert worker.ALL_QUEUES == expected_queues

    def test_default_queues_includes_all(self):
        """Verify default queues includes all defined queues."""
        import worker

        assert worker.DEFAULT_QUEUES == worker.ALL_QUEUES

    def test_queue_order_is_priority_order(self):
        """``ALL_QUEUES`` is ordered by the priority its tasks actually get.

        The order is documentation on its own — Dramatiq prioritises per actor,
        not per queue — so it has to agree with the priorities stamped on tasks
        or the docs would describe an ordering nothing implements.
        """
        from core.task_utils import ALL_QUEUES, QUEUE_PRIORITIES

        ranks = [QUEUE_PRIORITIES[q] for q in ALL_QUEUES]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks), "two queues share a priority"

    def test_tasks_inherit_their_queue_priority(self):
        """Lower number wins, so inbound work outranks reindex work."""
        import dramatiq

        import core.tasks  # noqa: F401  # pylint: disable=unused-import
        from core.task_utils import QUEUE_PRIORITIES

        for actor in dramatiq.get_broker().actors.values():
            expected = QUEUE_PRIORITIES.get(actor.queue_name)
            if expected is None:
                continue  # third-party actor on a queue we don't define
            assert actor.priority == expected, (
                f"{actor.actor_name} on {actor.queue_name} has priority "
                f"{actor.priority}, expected {expected}"
            )

    def test_an_unknown_queue_is_refused_at_import(self):
        """A typo'd queue would declare work no worker consumes."""
        import pytest as _pytest

        from core.task_utils import register_task

        with _pytest.raises(ValueError, match="unknown queue"):

            @register_task(queue="inbund")
            def _typo():
                pass

    @pytest.mark.parametrize(
        "task_path,queue",
        [
            # Inbound email processing — highest priority, time-sensitive.
            ("core.mda.inbound_tasks.process_inbound_message_task", "inbound"),
            ("core.mda.inbound_tasks.process_inbound_messages_queue_task", "inbound"),
            # Outbound email sending — high priority.
            ("core.mda.outbound_tasks.send_message_task", "outbound"),
            ("core.mda.outbound_tasks.selfcheck_task", "outbound"),
            ("core.mda.outbound_tasks.retry_messages_task", "outbound"),
            # An import run is long and sequential; the scheduler that
            # dispatches it and the cancellation cleanup must stay off that
            # worker so neither is stuck behind a run in progress.
            ("core.services.importer.tasks.run_import_task", "imports"),
            ("core.services.importer.tasks.schedule_imports_task", "default"),
            ("core.services.importer.tasks.cancel_import_task", "default"),
            # Search indexing — lowest priority, can be delayed.
            ("core.services.search.tasks.bulk_reindex_threads_task", "reindex"),
            ("core.services.search.tasks.process_pending_reindex_task", "reindex"),
            # Best-effort side effects, short enough to share a worker.
            ("core.mda.dispatch_webhooks.dispatch_webhook_task", "default"),
            ("core.services.push.tasks.send_push_notification", "default"),
            (
                "core.mda.inbound_tasks.purge_abandoned_inbound_messages_task",
                "default",
            ),
            ("core.services.exporter.tasks.export_mailbox_task", "default"),
            # The hourly blob sweeps, on the queue a worker consumes alone.
            ("core.services.blob_gc.gc_orphan_blobs_task", "blobs"),
            ("core.services.tiered_storage_tasks.offload_blobs_task", "blobs"),
        ],
    )
    def test_tasks_are_routed_to_the_expected_queue(self, task_path, queue):
        """Each task declares the queue it runs on."""
        from django.utils.module_loading import import_string

        assert import_string(task_path).queue_name == queue

    def test_long_tasks_stay_off_the_shared_queues(self):
        """A raised time limit is only allowed on a queue consumed on its own.

        A message's recovery deadline starts when the broker *delivers* it, not
        when it starts running. A long task on a queue that shares a worker
        with short ones therefore strands whatever is reserved beside it: those
        messages age past their own deadline, get reclaimed by another worker,
        and then run a second time here. Long work belongs on a queue a worker
        consumes alone — see ``worker.LONG_RUNNING_QUEUES``.
        """
        import dramatiq

        import core.tasks  # noqa: F401  # pylint: disable=unused-import

        import worker

        offenders = {
            actor.actor_name: (actor.queue_name, actor.options["time_limit"])
            for actor in dramatiq.get_broker().actors.values()
            if actor.queue_name not in worker.LONG_RUNNING_QUEUES
            and actor.actor_name not in worker.SHARED_LONG_TASKS
            and actor.options.get("time_limit", worker.DEFAULT_TIME_LIMIT_MS)
            > worker.DEFAULT_TIME_LIMIT_MS
        }
        assert not offenders, (
            f"tasks with a raised time limit on a shared queue: {offenders}. "
            f"Move them to one of {worker.LONG_RUNNING_QUEUES}, or add them to "
            "worker.SHARED_LONG_TASKS with a reason."
        )

    def test_shared_long_tasks_allowlist_is_not_stale(self):
        """Every documented exception must still be a real, still-long task."""
        import dramatiq

        import core.tasks  # noqa: F401  # pylint: disable=unused-import

        import worker

        actors = {a.actor_name: a for a in dramatiq.get_broker().actors.values()}
        for name in worker.SHARED_LONG_TASKS:
            assert name in actors, f"{name} is allowlisted but no longer exists"
            actor = actors[name]
            assert actor.options.get("time_limit", 0) > worker.DEFAULT_TIME_LIMIT_MS, (
                f"{name} no longer has a raised time limit — drop it from "
                "worker.SHARED_LONG_TASKS"
            )

    def test_every_task_queue_is_one_a_worker_consumes(self):
        """No task can be declared on a queue no worker ever reads."""
        import dramatiq

        import core.tasks  # noqa: F401  # pylint: disable=unused-import

        import worker

        broker = dramatiq.get_broker()
        declared = {actor.queue_name for actor in broker.actors.values()}
        assert declared <= set(worker.ALL_QUEUES)


class TestWorkerCLIParsing:
    """Test worker CLI argument parsing."""

    @staticmethod
    def _parse(*argv):
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", *argv]
            return worker.parse_args()
        finally:
            sys.argv = original_argv

    def test_parse_args_defaults(self):
        """Test default argument values."""
        args = self._parse()

        assert args.queues is None
        assert args.exclude is None
        assert args.disable_scheduler is False
        assert args.loglevel == "INFO"
        # One task at a time per process, so --concurrency alone says how many
        # tasks run in parallel.
        assert args.threads == 1

    def test_parse_args_with_queues(self):
        """Test parsing --queues argument."""
        assert self._parse("--queues=inbound,outbound").queues == "inbound,outbound"

    def test_parse_args_with_exclude(self):
        """Test parsing --exclude argument."""
        assert self._parse("--exclude=reindex,imports").exclude == "reindex,imports"

    def test_parse_args_with_disable_scheduler(self):
        """Test parsing --disable-scheduler flag."""
        assert self._parse("--disable-scheduler").disable_scheduler is True

    def test_parse_args_with_concurrency(self):
        """Test parsing --concurrency argument."""
        assert self._parse("--concurrency=4").concurrency == 4

    def test_parse_args_with_loglevel(self):
        """Test parsing --loglevel argument."""
        assert self._parse("--loglevel=DEBUG").loglevel == "DEBUG"

    def test_parse_args_short_flags(self):
        """Test parsing short flag versions."""
        args = self._parse("-Q", "inbound", "-X", "reindex", "-c", "2", "-l", "WARNING")

        assert args.queues == "inbound"
        assert args.exclude == "reindex"
        assert args.concurrency == 2
        assert args.loglevel == "WARNING"


class TestWorkerQueueValidation:
    """Test queue resolution and validation logic."""

    @staticmethod
    def _resolve(queues=None, exclude=None):
        import argparse

        import worker

        return worker.resolve_queues(argparse.Namespace(queues=queues, exclude=exclude))

    def test_all_queues_by_default(self):
        """With no flags, a worker consumes every queue."""
        import worker

        assert self._resolve() == worker.ALL_QUEUES

    def test_queue_exclusion_logic(self):
        """Test that queue exclusion works correctly."""
        result = self._resolve(exclude="reindex,imports")

        assert "reindex" not in result
        assert "imports" not in result
        assert {"inbound", "outbound", "default"} <= set(result)

    def test_queue_order_preserved_after_exclusion(self):
        """Test that queue priority order is preserved after exclusion."""
        assert self._resolve(exclude="outbound,imports") == [
            "inbound",
            "default",
            "blobs",
            "reindex",
        ]

    def test_unknown_queue_exits(self):
        """An unknown --queues value is a startup error, not a silent no-op."""
        with pytest.raises(SystemExit):
            self._resolve(queues="nope")

    def test_unknown_excluded_queue_exits(self):
        """Likewise for --exclude: a typo must not silently widen the worker."""
        with pytest.raises(SystemExit):
            self._resolve(exclude="nope")

    def test_excluding_everything_exits(self):
        """A worker with nothing left to consume refuses to start."""
        with pytest.raises(SystemExit):
            self._resolve(queues="inbound", exclude="inbound")


class TestScheduledTasks:
    """Test that periodic tasks are registered on the right queues."""

    @pytest.mark.parametrize(
        "task_path,queue",
        [
            ("core.mda.outbound_tasks.retry_messages_task", "outbound"),
            ("core.mda.outbound_tasks.selfcheck_task", "outbound"),
            ("core.mda.inbound_tasks.process_inbound_messages_queue_task", "inbound"),
            ("core.services.search.tasks.process_pending_reindex_task", "reindex"),
            ("core.services.importer.tasks.schedule_imports_task", "default"),
        ],
    )
    def test_scheduled_tasks_use_correct_queues(self, task_path, queue):
        """Verify scheduled tasks are routed to appropriate queues."""
        from django.utils.module_loading import import_string

        assert import_string(task_path).queue_name == queue

    def test_cron_task_requires_exactly_one_schedule(self):
        """``cron_task`` takes a crontab or an interval, never both or neither."""
        from core.task_utils import cron_task

        with pytest.raises(ValueError):
            cron_task()
        with pytest.raises(ValueError):
            cron_task(crontab="* * * * *", interval=60)


class TestWorkerE2E:
    """End-to-end tests for the worker process."""

    def test_worker_starts_successfully(self):
        """Test that the worker process starts without immediate errors."""
        import subprocess

        # Start worker with minimal config, disable scheduler to avoid side effects
        # pylint: disable=consider-using-with
        process = subprocess.Popen(
            [
                "python",
                "worker.py",
                "--queues=default",
                "--disable-scheduler",
                "--loglevel=INFO",
                "--concurrency=1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            # Wait briefly for startup - if it crashes immediately, we'll know
            # Use communicate with timeout to capture output
            try:
                stdout, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                # Worker is still running after 10 seconds - this is expected
                stdout = ""

            # Check if process exited with an error
            exit_code = process.poll()
            if exit_code is not None and exit_code != 0:
                pytest.fail(
                    f"Worker process exited with code {exit_code}. Output: {stdout}"
                )

            # If still running or exited cleanly, the test passes
            # Worker starting without crashing is the success criterion
        finally:
            # Cleanup: terminate the worker if still running
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def test_worker_rejects_invalid_queues(self):
        """Test that the worker rejects invalid queue names."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "worker.py",
                "--queues=invalid_queue_name",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode != 0
        assert (
            "Unknown queues" in result.stderr or "invalid_queue_name" in result.stderr
        )

    def test_worker_rejects_invalid_exclude_queues(self):
        """Test that the worker rejects invalid queue names in --exclude."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "worker.py",
                "--exclude=invalid_queue_name",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode != 0
        assert "Unknown queues to exclude" in result.stderr
