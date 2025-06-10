"""Management command to reindex content in Elasticsearch."""

import uuid

from django.core.management.base import BaseCommand, CommandError

from core import models
from core.search import create_index_if_not_exists
from core.tasks import (
    _reindex_all_base,
    reindex_all,
    reindex_mailbox_task,
    reindex_thread_task,
)


class Command(BaseCommand):
    """Reindex content in Elasticsearch."""

    help = "Reindex content in Elasticsearch"

    def add_arguments(self, parser):
        """Add command arguments."""
        # Define a mutually exclusive group for the reindex scope
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--all",
            action="store_true",
            help="Reindex all threads and messages",
        )
        group.add_argument(
            "--thread",
            type=str,
            help="Reindex a specific thread by ID",
        )
        group.add_argument(
            "--mailbox",
            type=str,
            help="Reindex all threads and messages in a specific mailbox by ID",
        )

        # Async option
        parser.add_argument(
            "--async",
            action="store_true",
            help="Run task asynchronously",
            dest="async_mode",
        )

        # Whether to recreate the index
        parser.add_argument(
            "--recreate-index",
            action="store_true",
            help="Recreate the index before reindexing",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        # Ensure index exists
        self.stdout.write("Ensuring Elasticsearch index exists...")
        create_index_if_not_exists()

        # Handle reindexing based on scope
        if options["all"]:
            self._reindex_all(options["async_mode"])
        elif options["thread"]:
            self._reindex_thread(options["thread"], options["async_mode"])
        elif options["mailbox"]:
            self._reindex_mailbox(options["mailbox"], options["async_mode"])

    def _reindex_all(self, async_mode):
        """Reindex all threads and messages."""
        self.stdout.write("Reindexing all threads and messages...")

        if async_mode:
            task = reindex_all.delay()
            self.stdout.write(
                self.style.SUCCESS(f"Reindexing task scheduled (ID: {task.id})")
            )
        else:
            # For synchronous execution, use the base function directly
            def update_progress(current, total, success_count, failure_count):
                """Update progress in the console."""
                self.stdout.write(
                    f"Progress: {current}/{total} threads processed "
                    f"({success_count} succeeded, {failure_count} failed)"
                )

            result = _reindex_all_base(update_progress)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reindexing completed: {result.get('success_count', 0)} succeeded, "
                    f"{result.get('failure_count', 0)} failed"
                )
            )
            if result.get("failure_count", 0) > 0:
                return 1

    def _reindex_thread(self, thread_id, async_mode):
        """Reindex a specific thread and its messages."""
        try:
            thread_uuid = uuid.UUID(thread_id)
            # Verify thread exists
            models.Thread.objects.get(id=thread_uuid)
        except ValueError as e:
            raise CommandError(f"Invalid thread ID: {thread_id}") from e
        except models.Thread.DoesNotExist as e:
            raise CommandError(f"Thread with ID {thread_id} does not exist") from e

        self.stdout.write(f"Reindexing thread {thread_id}...")

        if async_mode:
            task = reindex_thread_task.delay(thread_id)
            self.stdout.write(
                self.style.SUCCESS(f"Reindexing task scheduled (ID: {task.id})")
            )
        else:
            result = reindex_thread_task(thread_id)
            if result.get("success", False):
                self.stdout.write(
                    self.style.SUCCESS(f"Thread {thread_id} indexed successfully")
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Failed to index thread {thread_id}: {result.get('error', '')}"
                    )
                )
                return 1

    def _reindex_mailbox(self, mailbox_id, async_mode):
        """Reindex all threads and messages in a specific mailbox."""
        try:
            mailbox_uuid = uuid.UUID(mailbox_id)
            mailbox = models.Mailbox.objects.get(id=mailbox_uuid)
        except ValueError as e:
            raise CommandError(f"Invalid mailbox ID: {mailbox_id}") from e
        except models.Mailbox.DoesNotExist as e:
            raise CommandError(f"Mailbox with ID {mailbox_id} does not exist") from e

        self.stdout.write(f"Reindexing threads for mailbox {mailbox}...")

        if async_mode:
            task = reindex_mailbox_task.delay(mailbox_id)
            self.stdout.write(
                self.style.SUCCESS(f"Reindexing task scheduled (ID: {task.id})")
            )
        else:
            result = reindex_mailbox_task(mailbox_id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reindexing completed: {result.get('success_count', 0)} succeeded, "
                    f"{result.get('failure_count', 0)} failed"
                )
            )
            if result.get("failure_count", 0) > 0:
                return 1
