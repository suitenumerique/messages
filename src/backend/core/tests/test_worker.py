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

    def test_dramatiq_broker_configured(self):
        """Verify the Dramatiq broker is configured."""
        assert hasattr(settings, "DRAMATIQ_BROKER")
        assert settings.DRAMATIQ_BROKER["BROKER"] == "core.utils.EagerBroker"


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
            assert args.verbosity == 1
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

    def test_parse_args_with_verbosity(self):
        """Test parsing --verbosity argument."""
        import sys

        import worker

        original_argv = sys.argv
        try:
            sys.argv = ["worker.py", "--verbosity=2"]
            args = worker.parse_args()

            assert args.verbosity == 2
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
                "-v",
                "2",
            ]
            args = worker.parse_args()

            assert args.queues == "inbound"
            assert args.exclude == "reindex"
            assert args.concurrency == 2
            assert args.verbosity == 2
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


class TestCrontabConfiguration:
    """Test that crontab tasks are configured correctly."""

    def test_crontab_settings_configured(self):
        """Verify crontab settings are configured."""
        assert hasattr(settings, "DRAMATIQ_CRONTAB")
        assert "REDIS_URL" in settings.DRAMATIQ_CRONTAB

    def test_autodiscover_modules_finds_task_modules(self):
        """Verify that DRAMATIQ_AUTODISCOVER_MODULES values are discoverable.

        django_dramatiq's rundramatiq command uses Django's autodiscover_modules()
        which looks for '{app_name}.{module_name}' for each installed app.
        If DRAMATIQ_AUTODISCOVER_MODULES contains full paths like
        'core.mda.inbound_tasks', autodiscovery silently fails because it
        would look for 'core.core.mda.inbound_tasks' which doesn't exist.
        """
        from importlib import import_module

        from django.apps import apps

        autodiscover_modules = settings.DRAMATIQ_AUTODISCOVER_MODULES
        app_configs = apps.get_app_configs()

        for module_name in autodiscover_modules:
            found = False
            for app_config in app_configs:
                try:
                    import_module(f"{app_config.name}.{module_name}")
                    found = True
                    break
                except ImportError:
                    continue
            assert found, (
                f"DRAMATIQ_AUTODISCOVER_MODULES entry '{module_name}' is not "
                f"discoverable: no installed app contains a '{module_name}' "
                f"submodule. autodiscover_modules() will silently skip it. "
                f"Use simple module names like 'tasks' (not full paths)."
            )

    def test_all_task_actors_registered_on_broker(self):
        """Verify that all @register_task functions are registered on the broker.

        This catches missing imports in core/tasks.py that would cause the
        worker to silently ignore enqueued tasks.
        """
        import dramatiq

        broker = dramatiq.get_broker()
        registered_actors = set(broker.actors.keys())

        # Every @register_task function must be discoverable by the worker.
        # These are the actor names derived from each decorated function.
        expected_actors = {
            # core.mda.inbound_tasks
            "process_inbound_message_task",
            "process_inbound_messages_queue_task",
            # core.mda.outbound_tasks
            "send_message_task",
            "selfcheck_task",
            "retry_messages_task",
            # core.services.importer
            "process_eml_file_task",
            "import_imap_messages_task",
            "process_mbox_file_task",
            "process_pst_file_task",
            # core.services.search
            "reindex_all",
            "reindex_thread_task",
            "reindex_mailbox_task",
            "index_message_task",
            "reset_search_index",
            # core.services.exporter
            "export_mailbox_task",
        }

        missing = expected_actors - registered_actors
        assert not missing, (
            f"Task actors not registered on the broker: {missing}. "
            f"Check that core/tasks.py imports all task modules and that "
            f"DRAMATIQ_AUTODISCOVER_MODULES is set correctly."
        )


class TestWorkerE2E:
    """End-to-end tests for the worker process."""

    def test_worker_starts_successfully(self):
        """Test that the worker process starts without immediate errors."""
        import subprocess

        # Start worker with minimal config
        # pylint: disable=consider-using-with
        process = subprocess.Popen(
            [
                "python",
                "worker.py",
                "--queues=default",
                "-v", "2",
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
