"""Export the messages classified as spam to the ``message-exports`` storage.

Keeps a copy of the spam corpus, for analysis and to train classifiers,
before spam becomes permanently deletable. See ``core.services.exporter.spam``
for the output layout.

Parts are encrypted to the age public keys of the
``STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS`` setting, never given on the command
line; without them, ``--no-encryption`` must be passed to write plaintext.
``--dry-run`` sizes the export without reading any blob. Rerunning with the
``--prefix`` of an interrupted export resumes it, with its filters and
recipients.
"""

from datetime import datetime, time
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.services.exporter.spam import (
    EXPORT_STORAGE,
    SpamExportEncryptionRequiredError,
    SpamExporter,
    SpamExportError,
    SpamExportFilters,
    compute_spam_export_stats,
    is_export_storage_configured,
)


def _parse_moment(value: str) -> datetime:
    """Parse an ISO date or datetime; a bare date or naive time is UTC."""
    # Malformed input parses to None; well-formed but impossible dates
    # (2026-02-30) raise ValueError instead.
    try:
        moment = parse_datetime(value)
        day = parse_date(value) if moment is None else None
    except ValueError:
        moment = day = None
    if moment is None:
        if day is None:
            raise CommandError(f"Invalid date: {value!r}")
        moment = datetime.combine(day, time.min)
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=dt_timezone.utc)
    return moment


class Command(BaseCommand):
    """Export spam messages (raw EML + metadata) to object storage."""

    help = "Export the messages classified as spam to the message-exports storage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            help="Key prefix of the export in the bucket "
            "(default: spam/<UTC timestamp>). Pass the prefix of an "
            "interrupted export to resume it.",
        )
        parser.add_argument(
            "--since",
            help="Only spam dated at or after this ISO date/datetime (UTC). "
            "Filters on the message date: the original date for imported "
            "mail, not the import date.",
        )
        parser.add_argument(
            "--until",
            help="Only spam dated before this ISO date/datetime (UTC), the "
            "message date as for --since. Defaults to the start of the export.",
        )
        parser.add_argument(
            "--maildomain",
            help="Only spam delivered to mailboxes of this mail domain.",
        )
        parser.add_argument(
            "--no-encryption",
            action="store_true",
            help="Write the parts in plaintext (local tests only). Refused "
            "while STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS is set.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Distinct EMLs per archive part (default: 1000).",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Blobs read concurrently (default: 4).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be exported without reading or writing.",
        )

    def handle(self, *args, **options):
        filters = None
        if options["since"] or options["until"] or options["maildomain"]:
            filters = self._filters(options)

        if options["dry_run"]:
            self._dry_run(filters or self._filters(options))
            return

        if not is_export_storage_configured():
            raise CommandError(
                f"The {EXPORT_STORAGE} storage has no bucket: "
                "set STORAGE_MESSAGE_EXPORTS_* first."
            )

        prefix = options["prefix"] or (
            f"spam/{timezone.now().strftime('%Y%m%dT%H%M%SZ')}"
        )
        try:
            exporter = SpamExporter(
                prefix,
                filters=filters,
                age_recipients=self._age_recipients(options),
                batch_size=options["batch_size"],
                workers=options["workers"],
                log=self.stdout.write,
            )
            state = exporter.run()
        except SpamExportEncryptionRequiredError as exc:
            raise CommandError(
                "Set STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS to the age public "
                "key(s) to encrypt the export to (or pass --no-encryption for "
                "a local test)."
            ) from exc
        except SpamExportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {state.emls} EMLs ({state.messages} messages, "
                f"{state.errors} unreadable) in {state.parts} parts to "
                f"s3://{exporter.bucket}/{exporter.prefix}/, "
                + (
                    f"encrypted to {len(state.age_recipients)} age recipient(s)"
                    if state.age_recipients
                    else "NOT encrypted"
                )
            )
        )
        if state.errors:
            self.stdout.write(
                self.style.WARNING(
                    "Unreadable EMLs are listed in the part manifests with their error."
                )
            )

    @staticmethod
    def _age_recipients(options) -> list[str] | None:
        """Recipients for a new export: the configured keys, [] for plaintext."""
        configured = settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS
        if options["no_encryption"]:
            if configured:
                raise CommandError(
                    "--no-encryption is refused while "
                    "STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS is set."
                )
            return []
        return configured or None

    @staticmethod
    def _filters(options) -> SpamExportFilters:
        until = _parse_moment(options["until"]) if options["until"] else None
        since = _parse_moment(options["since"]) if options["since"] else None
        filters = SpamExportFilters(
            until=until or timezone.now(),
            since=since,
            maildomain=options["maildomain"] or None,
        )
        if filters.since and filters.since >= filters.until:
            raise CommandError("--since must be before --until")
        return filters

    def _dry_run(self, filters: SpamExportFilters):
        stats = compute_spam_export_stats(filters)
        self.stdout.write(f"Filters: {filters.to_dict()}")
        self.stdout.write(f"Spam messages: {stats['messages']}")
        self.stdout.write(f"Distinct EMLs (by sha256): {stats['emls']}")
        self.stdout.write(f"Uncompressed size: {stats['bytes'] / 1024 / 1024:.1f} MiB")
        recipients = settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS
        self.stdout.write(
            f"Encryption: {len(recipients)} age recipient(s) configured"
            if recipients
            else "Encryption: no age recipient configured "
            "(STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS)"
        )
        self.stdout.write("Messages per mail domain:")
        for domain, count in stats["by_maildomain"].items():
            self.stdout.write(f"  {domain or '(no mailbox)'}: {count}")
