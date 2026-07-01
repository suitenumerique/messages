"""Progressively backfill ``Message.postmark`` from legacy ``X-StMsg-*`` bytes.

Messages created before ``postmark`` existed carry their sender-auth /
processing-failed verdicts as ``X-StMsg-*`` headers baked into the stored MIME.
``Message.get_stmsg_headers()`` reads both sources during the transition, so
nothing is broken meanwhile — but to eventually drop the byte-reading branch we
need those verdicts moved into the structured field.

This command does that in bounded batches so it can be run repeatedly (e.g. from
cron) instead of one job that reads 100% of bodies at once. Each run scans up to
``--limit`` messages whose ``postmark`` is still NULL, oldest first, and sets it:
the extracted verdicts, or ``{}`` for a message that had none (which both marks
it scanned so it isn't re-read and reads back identically to NULL).

Usage:
    python manage.py backfill_postmark                 # one bounded run
    python manage.py backfill_postmark --limit 50000 --batch-size 1000
    python manage.py backfill_postmark --before 2026-07-01 --dry-run
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from core import models

logger = logging.getLogger(__name__)


def _postmark_from_stmsg(headers: dict) -> dict:
    """Project the legacy ``X-StMsg-*`` header dict onto ``postmark`` keys."""
    postmark = {}
    sender_auth = headers.get("sender-auth")
    if sender_auth in ("none", "fail"):
        postmark["auth"] = sender_auth
    if headers.get("processing-failed"):
        # Legacy value was the literal "true"; normalise to the new "fail".
        postmark["processing"] = "fail"
    return postmark


class Command(BaseCommand):
    """Backfill Message.postmark from legacy X-StMsg-* headers, in batches."""

    help = "Populate Message.postmark from legacy X-StMsg-* MIME headers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=10000,
            help="Max messages to process this run (default: 10000).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows fetched (and bodies read) per batch (default: 500).",
        )
        parser.add_argument(
            "--before",
            type=str,
            default=None,
            help=(
                "Only backfill messages created before this ISO date/datetime. "
                "Use to target the pre-deploy backlog and skip fresh mail."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        qs = models.Message.objects.filter(postmark__isnull=True)
        if options["before"]:
            cutoff = parse_datetime(options["before"])
            if cutoff is None:
                # Bare date → midnight UTC.
                cutoff = datetime.datetime.fromisoformat(options["before"]).replace(
                    tzinfo=datetime.UTC
                )
            qs = qs.filter(created_at__lt=cutoff)

        scanned = 0
        populated = 0
        errors = 0

        while scanned < limit:
            take = min(batch_size, limit - scanned)
            # Processed rows leave the ``postmark__isnull=True`` set, so the next
            # slice is always fresh work — no cursor/offset needed.
            batch = list(qs.order_by("created_at")[:take])
            if not batch:
                break

            for message in batch:
                scanned += 1
                try:
                    postmark = _postmark_from_stmsg(message.get_stmsg_headers())
                except Exception:  # pylint: disable=broad-exception-caught
                    # A single unreadable/corrupt blob must not abort the run.
                    errors += 1
                    logger.exception("backfill_postmark: failed to read %s", message.id)
                    continue

                if postmark:
                    populated += 1
                if dry_run:
                    continue
                # ``{}`` marks the row scanned (won't be re-read) and reads back
                # the same as NULL through ``get_stmsg_headers``.
                message.postmark = postmark
                message.save(update_fields=["postmark"])

        self.stdout.write(
            self.style.SUCCESS(
                f"backfill_postmark: scanned={scanned} populated={populated} "
                f"errors={errors}{' (dry-run)' if dry_run else ''}"
            )
        )
