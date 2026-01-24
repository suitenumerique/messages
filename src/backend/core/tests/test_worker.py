"""Tests for the background task worker configuration."""

# pylint: disable=import-outside-toplevel

from django.conf import settings

import pytest


class TestWorkerQueueConfiguration:
    """Test worker queue definitions and routing."""

    def test_all_queues_defined_in_priority_order(self):
        """Verify all queues are defined in the expected priority order."""
        # Import here to avoid import issues during test collection
        import worker

        expected_queues = [
            "management",
            "inbound",
            "outbound",
            "default",
            "imports",
            "reindex",
        ]
        assert worker.ALL_QUEUES == expected_queues

    def test_default_queues_includes_all(self):
        """Verify default queues includes all defined queues."""
        import worker

        assert worker.DEFAULT_QUEUES == worker.ALL_QUEUES

    def test_celery_default_queue_is_default(self):
        """Verify the celery default queue is set to 'default'."""
        assert settings.CELERY_TASK_DEFAULT_QUEUE == "default"

    def test_task_routes_configured(self):
        """Verify task routes are configured for all expected modules."""
        routes = settings.CELERY_TASK_ROUTES

        assert "core.mda.inbound_tasks.*" in routes
        assert routes["core.mda.inbound_tasks.*"]["queue"] == "inbound"

        assert "core.mda.outbound_tasks.*" in routes
        assert routes["core.mda.outbound_tasks.*"]["queue"] == "outbound"

        assert "core.services.importer.tasks.*" in routes
        assert routes["core.services.importer.tasks.*"]["queue"] == "imports"

        assert "core.services.search.tasks.*" in routes
        assert routes["core.services.search.tasks.*"]["queue"] == "reindex"


class TestWorkerCLIParsing:
    """Test worker CLI argument parsing."""

    def test_parse_args_defaults(self):
        """Test default argument values."""
        import sys

        import worker

        # Save original argv
        original_argv = sys.argv
        try:
            sys.argv = ["worker.py"]
            args = worker.parse_args()

            assert args.queues is None
            assert args.exclude is None
            assert args.disable_scheduler is False
            assert args.loglevel == "INFO"
        finally:
            sys.argv = original_argv

    def test_parse_args_with_queues(self):
        """Test parsing --queues argument."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--queues=inbound,outbound"]
            args = worker.parse_args()

            assert args.queues == "inbound,outbound"
        finally:
            sys.argv = original_argv

    def test_parse_args_with_exclude(self):
        """Test parsing --exclude argument."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--exclude=reindex,imports"]
            args = worker.parse_args()

            assert args.exclude == "reindex,imports"
        finally:
            sys.argv = original_argv

    def test_parse_args_with_disable_scheduler(self):
        """Test parsing --disable-scheduler flag."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--disable-scheduler"]
            args = worker.parse_args()

            assert args.disable_scheduler is True
        finally:
            sys.argv = original_argv

    def test_parse_args_with_concurrency(self):
        """Test parsing --concurrency argument."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--concurrency=4"]
            args = worker.parse_args()

            assert args.concurrency == 4
        finally:
            sys.argv = original_argv

    def test_parse_args_with_loglevel(self):
        """Test parsing --loglevel argument."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--loglevel=DEBUG"]
            args = worker.parse_args()

            assert args.loglevel == "DEBUG"
        finally:
            sys.argv = original_argv

    def test_parse_args_short_flags(self):
        """Test parsing short flag versions."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = [
                "worker.py",
                "-Q",
                "inbound",
                "-X",
                "reindex",
                "-c",
                "2",
                "-l",
                "WARNING",
            ]
            args = worker.parse_args()

            assert args.queues == "inbound"
            assert args.exclude == "reindex"
            assert args.concurrency == 2
            assert args.loglevel == "WARNING"
        finally:
            sys.argv = original_argv


class TestWorkerQueueValidation:
    """Test queue validation logic."""

    def test_valid_queues_accepted(self):
        """Test that valid queue names are accepted."""
        import worker

        valid_queues = [
            "management",
            "inbound",
            "outbound",
            "default",
            "imports",
            "reindex",
        ]
        for queue in valid_queues:
            assert queue in worker.ALL_QUEUES

    def test_queue_exclusion_logic(self):
        """Test that queue exclusion works correctly."""
        import worker

        queues = worker.ALL_QUEUES.copy()
        exclude = ["reindex", "imports"]
        result = [q for q in queues if q not in exclude]

        assert "reindex" not in result
        assert "imports" not in result
        assert "inbound" in result
        assert "outbound" in result
        assert "default" in result
        assert "management" in result

    def test_queue_order_preserved_after_exclusion(self):
        """Test that queue priority order is preserved after exclusion."""
        import worker

        queues = worker.ALL_QUEUES.copy()
        exclude = ["outbound", "imports"]
        result = [q for q in queues if q not in exclude]

        expected = ["management", "inbound", "default", "reindex"]
        assert result == expected


class TestBeatScheduleQueues:
    """Test that beat schedule tasks use correct queues."""

    def test_beat_schedule_uses_correct_queues(self):
        """Verify scheduled tasks are routed to appropriate queues."""
        from messages.celery_app import app

        if not hasattr(app.conf, "beat_schedule") or not app.conf.beat_schedule:
            pytest.skip("Beat schedule is disabled")

        schedule = app.conf.beat_schedule

        # Check retry-pending-messages uses outbound queue
        if "retry-pending-messages" in schedule:
            assert schedule["retry-pending-messages"]["options"]["queue"] == "outbound"

        # Check selfcheck uses outbound queue
        if "selfcheck" in schedule:
            assert schedule["selfcheck"]["options"]["queue"] == "outbound"

        # Check process-inbound-messages-queue uses inbound queue
        if "process-inbound-messages-queue" in schedule:
            assert (
                schedule["process-inbound-messages-queue"]["options"]["queue"]
                == "inbound"
            )


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
                stdout, _ = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                # Worker is still running after 3 seconds - this is expected
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
            timeout=10,
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
            timeout=10,
            check=False,
        )

        assert result.returncode != 0
        assert "Unknown queues to exclude" in result.stderr
