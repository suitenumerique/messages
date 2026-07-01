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
from django.db.models import Q
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

        base_qs = models.Message.objects.filter(postmark__isnull=True)
        if options["before"]:
            cutoff = parse_datetime(options["before"])
            if cutoff is None:
                # Bare date → midnight UTC.
                cutoff = datetime.datetime.fromisoformat(options["before"]).replace(
                    tzinfo=datetime.UTC
                )
            base_qs = base_qs.filter(created_at__lt=cutoff)

        scanned = 0
        populated = 0
        errors = 0
        # Keyset cursor over ``(created_at, id)``. Paging is driven by this
        # cursor, NOT by rows leaving the ``postmark__isnull=True`` set: in
        # --dry-run (no save) and on a read error (no save) a scanned row stays
        # NULL, so relying on the isnull filter to shrink would re-fetch the same
        # first batch every iteration and never make forward progress. Advancing
        # the cursor past every *scanned* row (written or not) keeps the run
        # bounded and resumable regardless of ``dry_run`` or read failures.
        last_ct = None
        last_id = None

        while scanned < limit:
            take = min(batch_size, limit - scanned)
            qs = base_qs
            if last_ct is not None:
                qs = qs.filter(
                    Q(created_at__gt=last_ct) | Q(created_at=last_ct, id__gt=last_id)
                )
            batch = list(qs.order_by("created_at", "id")[:take])
            if not batch:
                break

            to_update = []
            for message in batch:
                scanned += 1
                # Advance the cursor for *every* scanned row up front, so a
                # dry-run or a read failure below still moves us forward.
                last_ct = message.created_at
                last_id = message.id
                try:
                    postmark = _postmark_from_stmsg(message.get_stmsg_headers())
                except Exception:  # pylint: disable=broad-exception-caught
                    # A single unreadable/corrupt blob must not abort the run.
                    errors += 1
                    logger.exception("backfill_postmark: failed to read %s", message.id)
                    continue

                if postmark:
                    populated += 1
                # ``{}`` marks the row scanned (won't be re-read) and reads back
                # the same as NULL through ``get_stmsg_headers``.
                message.postmark = postmark
                to_update.append(message)

            # ``bulk_update`` in one write per batch, NOT per-row ``save()``:
            # ``Message``'s ``post_save`` signal schedules a thread reindex, so
            # saving each row would fire one OpenSearch reindex per message to
            # populate a field OpenSearch doesn't even index. ``bulk_update``
            # emits no signals, so the backlog is backfilled without touching
            # the search index.
            if not dry_run and to_update:
                models.Message.objects.bulk_update(to_update, ["postmark"])

        self.stdout.write(
            self.style.SUCCESS(
                f"backfill_postmark: scanned={scanned} populated={populated} "
                f"errors={errors}{' (dry-run)' if dry_run else ''}"
            )
        )
