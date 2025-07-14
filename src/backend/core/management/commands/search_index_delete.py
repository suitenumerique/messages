"""Management command to delete OpenSearch index."""

import sys

from django.core.management.base import BaseCommand

from core.search import delete_index


class Command(BaseCommand):
    """Delete OpenSearch index."""

    help = "Delete OpenSearch index"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force deletion without confirmation",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        if not options["force"]:
            confirm = input(
                "Are you sure you want to delete the OpenSearch index? This cannot be undone. [y/N] "
            )
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Operation cancelled"))
                return

        self.stdout.write("Deleting OpenSearch index...")

        result = delete_index()
        if result:
            self.stdout.write(
                self.style.SUCCESS("OpenSearch index deleted successfully")
            )
        else:
            self.stdout.write(
                self.style.WARNING("OpenSearch index not found or already deleted")
            )
            sys.exit(1)
