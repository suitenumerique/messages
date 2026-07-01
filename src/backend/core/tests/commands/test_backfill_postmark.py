"""Tests for the ``backfill_postmark`` management command."""

from io import StringIO

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

    def test_dry_run_writes_nothing(self):
        """--dry-run reports but does not persist."""
        message = factories.MessageFactory(raw_mime=_WITH_VERDICTS)

        call_command("backfill_postmark", "--dry-run", stdout=StringIO())

        message.refresh_from_db()
        assert message.postmark is None

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
