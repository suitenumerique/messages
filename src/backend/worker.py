#!/usr/bin/env python
"""
Background task worker with sensible queue defaults.

Usage:
    python worker.py                    # Process all queues with default priority
    python worker.py --queues=inbound,default  # Process only specific queues
    python worker.py --exclude=reindex  # Process all queues except reindex
    python worker.py --concurrency=4    # Set worker concurrency
    python worker.py --disable-scheduler  # Disable the scheduler

Queue priority order (highest to lowest):
    1. management - Admin/management tasks (migrations, cleanup)
    2. inbound    - Inbound email processing (time-sensitive)
    3. outbound   - Outbound email sending
    4. default    - General tasks
    5. imports    - File import processing (can be delayed)
    6. reindex    - Search indexing (lowest priority)
"""

import argparse
import logging
import os
import sys

# Setup Django before importing the task runner
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "messages.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

from configurations.importer import install  # pylint: disable=wrong-import-position

install(check_options=True)

from messages.celery_app import app  # pylint: disable=wrong-import-position

# Queue definitions in priority order
ALL_QUEUES = ["management", "inbound", "outbound", "default", "imports", "reindex"]
DEFAULT_QUEUES = ALL_QUEUES  # By default, process all queues


def get_default_concurrency():
    """Get default concurrency from environment variables."""
    env_value = os.environ.get("WORKER_CONCURRENCY") or os.environ.get(
        "CELERY_CONCURRENCY"
    )
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            return None
    return None


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
        "--disable-scheduler",
        action="store_true",
        help="Disable the task scheduler (enabled by default).",
    )
    parser.add_argument(
        "--loglevel",
        "-l",
        type=str,
        default="INFO",
        help="Logging level. Default: INFO",
    )
    return parser.parse_args()


def main():
    """Start the background task worker."""
    logger = logging.getLogger(__name__)
    args = parse_args()

    # Determine which queues to process
    if args.queues:
        queues = [q.strip() for q in args.queues.split(",")]
        # Validate queues
        invalid = set(queues) - set(ALL_QUEUES)
        if invalid:
            sys.stderr.write(f"Error: Unknown queues: {', '.join(invalid)}\n")
            sys.stderr.write(f"Valid queues are: {', '.join(ALL_QUEUES)}\n")
            sys.exit(1)
    else:
        queues = DEFAULT_QUEUES.copy()

    # Apply exclusions
    if args.exclude:
        exclude = [q.strip() for q in args.exclude.split(",")]
        # Validate excluded queue names
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

    # Build worker arguments
    worker_args = [
        "worker",
        f"--queues={','.join(queues)}",
        f"--loglevel={args.loglevel}",
    ]

    if args.concurrency:
        worker_args.append(f"--concurrency={args.concurrency}")

    if not args.disable_scheduler:
        worker_args.append("--beat")

    # Always enable task events for monitoring
    worker_args.append("--task-events")

    logger.info("Starting worker with queues: %s", ", ".join(queues))
    app.worker_main(argv=worker_args)


if __name__ == "__main__":
    main()
