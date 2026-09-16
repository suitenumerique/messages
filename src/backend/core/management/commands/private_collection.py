"""Get the files and create a private collection for the rag."""

import logging
import os
import glob

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef

from core import models
from core.mda.addresses import ascii_lower, split_address

from core.services.ai_service import AIService

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    """Lauch the files processing and create a private collection."""

    help = (
        "Lauch the files processing and create a private collection."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-directory",
            type=str,
            help=(
                "Source directory containing files to process. "),
            required=True
        )

    def handle(self, *args, **options):
        source_directory = options.get("source_directory")

        if not source_directory:
            self.stderr.write(self.style.ERROR("Source directory is required."))
            return

        print(f"Private collections used: {AIService().get_private_collections()}")

        # get all files path in the given source directory
        files = glob.glob(os.path.join(source_directory, '**'), recursive=True)
        for file in files:
            try:
                if os.path.isfile(file):
                    self.stdout.write(self.style.SUCCESS(f"Processing file: {file}"))
                    # Create a private collection for each file
                    collection_name = os.path.basename(file)
                    document_id = AIService().upload_document(file)
                    self.stdout.write(self.style.SUCCESS(f"Document imported: {document_id}"))
                else:
                    self.stdout.write(self.style.WARNING(f"Skipping non-file: {file}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error processing file {file}: {e}"))

