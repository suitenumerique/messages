"""Management command to create OpenSearch index."""

import sys

from django.core.management.base import BaseCommand

from core.search import create_index_if_not_exists


class Command(BaseCommand):
    """Create OpenSearch index if it doesn't exist."""

    help = "Create OpenSearch index if it doesn't exist"

    def handle(self, *args, **options):
        """Execute the command."""
        self.stdout.write("Creating OpenSearch index...")

        result = create_index_if_not_exists()
        if result:
            self.stdout.write(
                self.style.SUCCESS("OpenSearch index created or already exists")
            )
        else:
            self.stdout.write(self.style.ERROR("Failed to create OpenSearch index"))
            sys.exit(1)
