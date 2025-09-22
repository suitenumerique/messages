"""Management command to update the size of the compressed blobs

Usage:
    python manage.py update_blob_size_compressed

This command will update the size_compressed field.
"""

from django.core.management.base import BaseCommand

from core import models


class Command(BaseCommand):
    """Management command to calculate the size of the compressed blobs"""

    help = "Calculate the size of the compressed blobs"

    def handle(self, *args, **options):
        """Handle the command"""
        blobs = models.Blob.objects.only("id", "raw_content", "size_compressed").filter(
            size_compressed=0
        )
        for blob in blobs.iterator():
            blob.size_compressed = len(blob.raw_content)
            blob.save(update_fields=["size_compressed"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully calculated the size of the compressed blob {blob.id}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Successfully calculated the size of the compressed blobs!"
            )
        )
