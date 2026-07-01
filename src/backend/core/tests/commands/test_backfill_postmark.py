"""Tests for the ``backfill_postmark`` management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

import pytest

from core import factories, models

_WITH_VERDICTS = (
    b"X-StMsg-Sender-Auth: fail\r\n"
    b"X-StMsg-Processing-Failed: true\r\n"
    b"From: s@example.com\r\nSubject: t\r\n\r\nbody"
)
_CLEAN = b"From: s@example.com\r\nSubject: t\r\n\r\nbody"


@pytest.mark.django_db
class TestBackfillPostmark:
    """Progressive backfill of ``Message.postmark`` from legacy X-StMsg bytes."""

    def test_verdicts_projected_from_legacy_headers(self):
        """Baked X-StMsg verdicts are projected onto postmark keys."""
        message = factories.MessageFactory(raw_mime=_WITH_VERDICTS)
        assert message.postmark is None

        call_command("backfill_postmark", stdout=StringIO())

        message.refresh_from_db()
        assert message.postmark == {"auth": "fail", "processing": "fail"}

    def test_clean_message_marked_scanned_as_empty(self):
        """A legacy message with no X-StMsg headers is set to ``{}`` so it is
        not re-read, and reads back the same as NULL."""
        message = factories.MessageFactory(raw_mime=_CLEAN)

        call_command("backfill_postmark", stdout=StringIO())

        message.refresh_from_db()
        assert message.postmark == {}
        assert message.get_stmsg_headers() == {}

    def test_backfill_does_not_reindex_threads(self):
        """Backfilling this un-indexed field must not schedule a thread reindex
        per message: ``bulk_update`` is used precisely because it emits no
        ``post_save`` signal (a per-row ``save()`` would hit OpenSearch once
        per message)."""
        for _ in range(3):
            factories.MessageFactory(raw_mime=_WITH_VERDICTS)

        # Patch only around the run, so the fixtures' own creation reindexes
        # (already fired above) don't count.
        with patch("core.signals._schedule_thread_reindex") as mock_reindex:
            call_command("backfill_postmark", stdout=StringIO())

        mock_reindex.assert_not_called()
        assert models.Message.objects.filter(postmark__isnull=True).count() == 0

    def test_dry_run_writes_nothing(self):
        """--dry-run reports but does not persist, and scans each row exactly
        once (no re-scan loop even though nothing leaves the NULL set)."""
        message = factories.MessageFactory(raw_mime=_WITH_VERDICTS)

        out = StringIO()
        call_command("backfill_postmark", "--dry-run", stdout=out)

        message.refresh_from_db()
        assert message.postmark is None
        # One input → scanned exactly once. A re-scan loop would report >1.
        assert "scanned=1 " in out.getvalue()

    def test_unreadable_blob_counts_error_and_skips(self):
        """A row whose blob read raises is counted as an error and left NULL —
        one bad message never aborts the run."""
        message = factories.MessageFactory(raw_mime=_WITH_VERDICTS)

        out = StringIO()
        with patch.object(
            models.Message,
            "get_stmsg_headers",
            side_effect=Exception("unreadable blob"),
        ):
            call_command("backfill_postmark", stdout=out)

        message.refresh_from_db()
        assert message.postmark is None
        summary = out.getvalue()
        assert "scanned=1 " in summary
        assert "errors=1" in summary

    def test_limit_bounds_the_batch(self):
        """--limit caps how many rows are processed per run."""
        for _ in range(3):
            factories.MessageFactory(raw_mime=_WITH_VERDICTS)

        call_command("backfill_postmark", "--limit", "2", stdout=StringIO())

        remaining = models.Message.objects.filter(postmark__isnull=True).count()
        assert remaining == 1

    def test_already_backfilled_rows_are_skipped(self):
        """A row with a non-NULL postmark is left untouched (idempotent)."""
        message = factories.MessageFactory(raw_mime=_WITH_VERDICTS)
        message.postmark = {"auth": "none"}
        message.save(update_fields=["postmark"])

        call_command("backfill_postmark", stdout=StringIO())

        message.refresh_from_db()
        assert message.postmark == {"auth": "none"}
