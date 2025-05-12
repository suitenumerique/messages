"""Management command to reindex content in Elasticsearch."""

import sys
import uuid

from django.core.management.base import BaseCommand, CommandError

from core import models
from core.search import create_index_if_not_exists, index_thread


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
            self._reindex_all()
        elif options["thread"]:
            self._reindex_thread(options["thread"])
        elif options["mailbox"]:
            self._reindex_mailbox(options["mailbox"])

    def _reindex_all(self):
        """Reindex all threads and messages."""
        self.stdout.write("Reindexing all threads and messages...")

        threads = models.Thread.objects.all()
        total = threads.count()

        if total == 0:
            self.stdout.write(self.style.WARNING("No threads found to reindex"))
            return

        self.stdout.write(f"Found {total} threads to reindex")
        success_count = 0
        failure_count = 0

        for i, thread in enumerate(threads):
            try:
                if index_thread(thread):
                    success_count += 1
                else:
                    failure_count += 1
            # pylint: disable=broad-exception-caught
            except Exception as e:  # noqa: BLE001
                failure_count += 1
                self.stdout.write(
                    self.style.ERROR(f"Error indexing thread {thread.id}: {e}")
                )

            # Show progress
            if (i + 1) % 50 == 0 or (i + 1) == total:
                self.stdout.write(f"Processed {i + 1}/{total} threads")

        self.stdout.write(
            self.style.SUCCESS(
                f"Reindexing completed: {success_count} succeeded, {failure_count} failed"
            )
        )
        if failure_count > 0:
            sys.exit(1)

    def _reindex_thread(self, thread_id):
        """Reindex a specific thread and its messages."""
        try:
            thread_uuid = uuid.UUID(thread_id)
            thread = models.Thread.objects.get(id=thread_uuid)
        except ValueError as e:
            raise CommandError(f"Invalid thread ID: {thread_id}") from e
        except models.Thread.DoesNotExist as e:
            raise CommandError(f"Thread with ID {thread_id} does not exist") from e

        self.stdout.write(f"Reindexing thread {thread_id}...")

        if index_thread(thread):
            self.stdout.write(
                self.style.SUCCESS(f"Thread {thread_id} indexed successfully")
            )
        else:
            self.stdout.write(self.style.ERROR(f"Failed to index thread {thread_id}"))
            sys.exit(1)

    def _reindex_mailbox(self, mailbox_id):
        """Reindex all threads and messages in a specific mailbox."""
        try:
            mailbox_uuid = uuid.UUID(mailbox_id)
            mailbox = models.Mailbox.objects.get(id=mailbox_uuid)
        except ValueError as e:
            raise CommandError(f"Invalid mailbox ID: {mailbox_id}") from e
        except models.Mailbox.DoesNotExist as e:
            raise CommandError(f"Mailbox with ID {mailbox_id} does not exist") from e

        self.stdout.write(f"Reindexing threads for mailbox {mailbox}...")

        threads = mailbox.threads_viewer
        total = threads.count()

        if total == 0:
            self.stdout.write(
                self.style.WARNING(f"No threads found for mailbox {mailbox}")
            )
            return

        self.stdout.write(f"Found {total} threads to reindex for mailbox {mailbox}")
        success_count = 0
        failure_count = 0

        for i, thread in enumerate(threads):
            try:
                if index_thread(thread):
                    success_count += 1
                else:
                    failure_count += 1
            # pylint: disable=broad-exception-caught
            except Exception as e:  # noqa: BLE001
                failure_count += 1
                self.stdout.write(
                    self.style.ERROR(f"Error indexing thread {thread.id}: {e}")
                )

            # Show progress
            if (i + 1) % 20 == 0 or (i + 1) == total:
                self.stdout.write(f"Processed {i + 1}/{total} threads")

        self.stdout.write(
            self.style.SUCCESS(
                f"Reindexing completed: {success_count} succeeded, {failure_count} failed"
            )
        )
        if failure_count > 0:
            sys.exit(1)
