"""Fill the private AI collection used by the RAG, from files or from Docs."""

import getpass
import glob
import logging
import os

from django.core.management.base import BaseCommand, CommandError

import requests

from core.services.ai_service import AIService
from core.services.docs import DocsClient, DocsError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Import local files or every Docs document into the private collection."""

    help = (
        "Import files from a directory, or every document readable by a Docs "
        "account (--from-docs), into the private AI collection. A Docs document "
        "is stored with its title as name and its Markdown content as file; an "
        "existing document with the same title is replaced."
    )

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument(
            "--source-directory",
            type=str,
            help="Source directory containing files to process.",
        )
        source.add_argument(
            "--from-docs",
            action="store_true",
            help=(
                "Import the Docs documents. Authentication: DOCS_SESSIONID "
                "(docs_sessionid cookie copied from a browser), or --email / "
                "DOCS_EMAIL with DOCS_PASSWORD (asked if not set)."
            ),
        )
        parser.add_argument(
            "--email",
            type=str,
            help="Docs account used with --from-docs (default: DOCS_EMAIL).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --from-docs, list the documents without importing them.",
        )

    def handle(self, *args, **options):
        if options["from_docs"]:
            self._import_docs(options.get("email"), options["dry_run"])
        else:
            self._import_directory(options["source_directory"])

    def _import_directory(self, source_directory: str) -> None:
        ai_service = AIService()
        files = glob.glob(os.path.join(source_directory, "**"), recursive=True)
        for file in files:
            if not os.path.isfile(file):
                self.stdout.write(self.style.WARNING(f"Skipping non-file: {file}"))
                continue
            try:
                self.stdout.write(self.style.SUCCESS(f"Processing file: {file}"))
                document_id = ai_service.upload_document(file)
                self.stdout.write(
                    self.style.SUCCESS(f"Document imported: {document_id}")
                )
            except (OSError, ValueError, requests.RequestException) as exc:
                self.stderr.write(
                    self.style.ERROR(f"Error processing file {file}: {exc}")
                )

    def _import_docs(self, email: str | None, dry_run: bool) -> None:
        client = self._authenticated_docs_client(email)
        ai_service = None if dry_run else AIService()
        imported_titles: set[str] = set()
        counts = {
            "to_import": 0,
            "imported": 0,
            "replaced": 0,
            "skipped": 0,
            "errors": 0,
        }

        try:
            for document_id in client.iter_document_ids():
                self._import_docs_document(
                    client, ai_service, document_id, imported_titles, counts
                )
        except DocsError as exc:
            raise CommandError(f"Cannot list the Docs documents: {exc}") from exc

        if dry_run:
            self.stdout.write(f"Documents to import: {counts['to_import']}")
        self.stdout.write(
            f"Imported: {counts['imported']}, replaced: {counts['replaced']}, "
            f"skipped (empty): {counts['skipped']}, errors: {counts['errors']}"
        )
        if counts["errors"]:
            raise CommandError(f"{counts['errors']} Docs document(s) not imported.")

    def _import_docs_document(
        self,
        client: DocsClient,
        ai_service: AIService | None,
        document_id: str,
        imported_titles: set[str],
        counts: dict[str, int],
    ) -> None:
        try:
            document = client.get_document(document_id)
            if not document.content.strip():
                counts["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(f"Skipping empty document: {document.title}")
                )
                return
            if ai_service is None:
                counts["to_import"] += 1
                self.stdout.write(f"Would import: {document.title}")
                return

            if document.title in imported_titles:
                # Keep the document imported earlier in this run under the same title.
                self.stdout.write(
                    self.style.WARNING(
                        f"Several Docs documents are titled: {document.title}"
                    )
                )
                previous_ids = []
            else:
                previous_ids = ai_service.find_document_ids_by_name(document.title)

            # Upload before deleting, so a failed upload keeps the previous version.
            ai_service.upload_text_document(document.title, document.content)
            for previous_id in previous_ids:
                ai_service.delete_document(previous_id)

            imported_titles.add(document.title)
            counts["imported"] += 1
            counts["replaced"] += bool(previous_ids)
            self.stdout.write(
                self.style.SUCCESS(f"Document imported: {document.title}")
            )
        except (DocsError, ValueError, requests.RequestException) as exc:
            counts["errors"] += 1
            self.stderr.write(
                self.style.ERROR(f"Error importing Docs document {document_id}: {exc}")
            )

    @staticmethod
    def _authenticated_docs_client(email: str | None) -> DocsClient:
        session_id = os.environ.get("DOCS_SESSIONID", "").strip()
        client = DocsClient(session_id=session_id or None)
        try:
            if not session_id:
                email = "user1@example.local"
                password = "user1"
                client.login(email, password)
            client.ensure_authenticated()
        except EOFError as exc:
            raise CommandError(
                "Docs credentials missing: set DOCS_SESSIONID, or DOCS_EMAIL and DOCS_PASSWORD."
            ) from exc
        except DocsError as exc:
            raise CommandError(str(exc)) from exc
        return client
