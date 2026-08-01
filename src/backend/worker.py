#!/usr/bin/env python
"""
Background task worker with sensible queue defaults.

Usage:
    python worker.py                    # Process all queues, run the scheduler
    python worker.py --queues=inbound,default  # Process only specific queues
    python worker.py --exclude=reindex  # Process all queues except reindex
    python worker.py --concurrency=4    # Set worker concurrency
    python worker.py --disable-scheduler  # Don't run the periodic scheduler

The queues, what runs on each and their relative priority are defined once, in
``core.task_utils.QUEUE_PRIORITIES`` — not restated here, so the two can't
drift. ``docs/worker.md`` explains how to choose one for a new task.
"""

# pylint: disable=import-outside-toplevel, wrong-import-position

import argparse
import importlib
import logging
import multiprocessing
import os
import subprocess
import sys
import threading

# Dramatiq's Canteen shared-memory handshake breaks under "forkserver", which
# became the default start method in Python 3.14 — worker processes boot but
# never consume. See https://github.com/Bogdanp/dramatiq/issues/701. Must be
# set before dramatiq forks anything.
multiprocessing.set_start_method("fork", force=True)

# Reserve exactly one message per consumer thread instead of Dramatiq's
# default of two.
#
# The broker's recovery deadline for a message starts when it is *delivered* to
# a worker, not when the worker starts running it. With a prefetch of two, the
# second message sits reserved while the first one runs — so a task queued
# behind a long one (a multi-hour mailbox export, sharing the "default" queue
# with sub-second webhook and push deliveries) has its deadline expire
# untouched, gets reclaimed by another worker, and then runs *again* here once
# the long task finishes. Reserving one message at a time costs an extra
# round-trip per message and removes that window.
#
# It only removes it *within* a queue, though: a worker runs one consumer
# thread per queue, so a long task still strands one message per sibling queue.
# That is what LONG_RUNNING_QUEUES below is for.
os.environ.setdefault("dramatiq_queue_prefetch", "1")

# Setup Django before importing the task runner
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

# Override $APP if set by the host (e.g. Scalingo), which some CLIs read as an
# application module to import.
os.environ.pop("APP", None)

from configurations.importer import install

install(check_options=True)

import django

django.setup()

logger = logging.getLogger(__name__)

# The queue topology lives with the code that declares tasks against it, so
# there is one definition rather than two that can drift. Re-exported here
# because the CLI and its tests reach for ``worker.ALL_QUEUES``.
from core.task_utils import (
    ALL_QUEUES,
    DEFAULT_TIME_LIMIT_MS,
    LONG_RUNNING_QUEUES,
    QUEUE_PRIORITIES,
    SHARED_LONG_TASKS,
)

__all__ = [
    "ALL_QUEUES",
    "DEFAULT_TIME_LIMIT_MS",
    "LONG_RUNNING_QUEUES",
    "QUEUE_PRIORITIES",
    "SHARED_LONG_TASKS",
]

DEFAULT_QUEUES = ALL_QUEUES  # By default, process all queues

# Give a shutting-down worker long enough to finish the message in flight
# rather than killing it mid-delivery. Long-running tasks (imports, exports)
# are resumable, so this is a courtesy window, not a guarantee.
WORKER_SHUTDOWN_TIMEOUT_MS = 600_000


def get_default_concurrency():
    """Get default concurrency from environment variables."""
    env_value = os.environ.get("WORKER_CONCURRENCY")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            return None
    return None


def discover_tasks_modules():
    """Modules the worker must import for its actors to be registered.

    Mirrors what ``django_dramatiq`` does at startup: the first entry sets the
    global broker up (via ``django.setup()``), the rest declare the actors.
    """
    from django.apps import apps
    from django.conf import settings
    from django.utils.module_loading import module_has_submodule

    modules = ["django_dramatiq.setup"]

    for conf in apps.get_app_configs():
        for task_module in settings.DRAMATIQ_AUTODISCOVER_MODULES:
            if module_has_submodule(conf.module, task_module):
                module = f"{conf.name}.{task_module}"
                importlib.import_module(module)
                logger.info("Discovered tasks module: %r", module)
                modules.append(module)

    return modules


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Start a background task worker with sensible queue defaults.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--queues",
        "-Q",
        type=str,
        default=None,
        help=f"Comma-separated list of queues to process. Default: {','.join(DEFAULT_QUEUES)}",
    )
    parser.add_argument(
        "--exclude",
        "-X",
        type=str,
        default=None,
        help="Comma-separated list of queues to exclude from processing.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=get_default_concurrency(),
        help="Number of worker processes. Default: WORKER_CONCURRENCY env var or number of CPUs.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=int(os.environ.get("WORKER_THREADS", "1")),
        help=(
            "Threads per worker process. Default: WORKER_THREADS env var or 1, so "
            "--concurrency alone sets how many tasks run at once."
        ),
    )
    parser.add_argument(
        "--disable-scheduler",
        action="store_true",
        help="Disable the periodic task scheduler (enabled by default).",
    )
    parser.add_argument(
        "--loglevel",
        "-l",
        type=str,
        default="INFO",
        help="Logging level. Default: INFO",
    )
    return parser.parse_args()


def resolve_queues(args):
    """Turn --queues/--exclude into the ordered queue list, or exit on error."""
    if args.queues:
        queues = [q.strip() for q in args.queues.split(",")]
        invalid = set(queues) - set(ALL_QUEUES)
        if invalid:
            sys.stderr.write(f"Error: Unknown queues: {', '.join(invalid)}\n")
            sys.stderr.write(f"Valid queues are: {', '.join(ALL_QUEUES)}\n")
            sys.exit(1)
    else:
        queues = DEFAULT_QUEUES.copy()

    if args.exclude:
        exclude = [q.strip() for q in args.exclude.split(",")]
        invalid_exclude = set(exclude) - set(ALL_QUEUES)
        if invalid_exclude:
            sys.stderr.write(
                f"Error: Unknown queues to exclude: {', '.join(invalid_exclude)}\n"
            )
            sys.stderr.write(f"Valid queues are: {', '.join(ALL_QUEUES)}\n")
            sys.exit(1)
        queues = [q for q in queues if q not in exclude]

    if not queues:
        sys.stderr.write("Error: No queues to process after exclusions.\n")
        sys.exit(1)

    return queues


class SchedulerSupervisor:
    """Runs the periodic scheduler alongside this worker, as a child process.

    Every task decorated with ``@cron_task`` is dispatched from there. Keeping
    it inside the worker means one process type to deploy, and it is safe to
    leave enabled on every worker: the scheduler holds a Redis lock, so exactly
    one instance across the fleet is live and the rest block waiting for it.

    Supervised rather than fired-and-forgotten so the schedule survives its own
    process dying — including the ordinary case of a standby giving up on the
    lock after its blocking timeout, which must not leave this worker
    permanently unable to take over.
    """

    #: Delay before respawning an exited scheduler. Short, because the common
    #: exit is a standby timing out on the lock and needing to queue up again.
    RESTART_DELAY = 10  # seconds

    def __init__(self):
        self._stopping = threading.Event()
        # Serializes the spawn against ``stop()``, so a shutdown that lands
        # between two respawns can't leave an orphaned scheduler behind.
        self._lock = threading.Lock()
        self._process = None
        self._thread = None

    def start(self):
        """Spawn the scheduler, then watch it in a background thread.

        The first spawn happens here, on the caller's thread, deliberately:
        Dramatiq forks its worker processes moments later, and forking a
        process whose *other* threads hold locks is how children deadlock. By
        the time this returns, the watcher thread is parked in ``wait()`` on an
        already-running child, holding nothing. Later respawns are safe because
        Dramatiq forks only during boot — it never re-forks a dead worker, it
        shuts the whole process down and lets the supervisor restart it.
        """
        self._spawn()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _spawn(self):
        """Start one scheduler process. Returns False if it could not start."""
        try:
            with self._lock:
                if self._stopping.is_set():
                    return False
                # --no-heartbeat drops the library's own log-only minute task.
                # Fixed argv, no shell, no caller-supplied input.
                self._process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
                    [sys.executable, "manage.py", "crontab", "--no-heartbeat"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                )
        except OSError:
            logger.exception("Could not start the task scheduler")
            return False
        return True

    def _run(self):
        while not self._stopping.is_set():
            process = self._process
            if process is None:
                if not self._spawn():
                    self._stopping.wait(self.RESTART_DELAY)
                    continue
                process = self._process

            process.wait()
            if self._stopping.is_set():
                return
            logger.info("Task scheduler exited; restarting it shortly")
            self._process = None
            self._stopping.wait(self.RESTART_DELAY)

    def stop(self):
        """Shut the scheduler down and stop respawning it."""
        with self._lock:
            self._stopping.set()
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def main():
    """Start the background task worker."""
    args = parse_args()
    queues = resolve_queues(args)
    tasks_modules = discover_tasks_modules()

    dramatiq_args = [
        "--threads",
        str(args.threads),
        "--worker-shutdown-timeout",
        str(WORKER_SHUTDOWN_TIMEOUT_MS),
    ]
    if args.concurrency:
        dramatiq_args += ["--processes", str(args.concurrency)]
    if args.loglevel.upper() == "DEBUG":
        dramatiq_args.append("--verbose")
    dramatiq_args += tasks_modules
    # --queues takes a variable number of values, so it has to come last.
    dramatiq_args += ["--queues", *queues]

    scheduler = None
    if not args.disable_scheduler:
        scheduler = SchedulerSupervisor()
        scheduler.start()

    logger.info("Starting worker with queues: %s", ", ".join(queues))

    # Called in-process rather than through ``manage.py rundramatiq``, which
    # execvp's a fresh interpreter and would throw away the fork start method
    # set at the top of this file.
    from dramatiq.cli import main as dramatiq_main
    from dramatiq.cli import make_argument_parser

    try:
        return dramatiq_main(make_argument_parser().parse_args(dramatiq_args))
    finally:
        if scheduler is not None:
            scheduler.stop()


if __name__ == "__main__":
    sys.exit(main())
