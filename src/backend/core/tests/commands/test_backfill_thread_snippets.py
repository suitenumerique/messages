"""Tests for the ``backfill_thread_snippets`` management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from core import factories, models

_BODY = b"From: s@example.com\r\nSubject: t\r\n\r\nBackfilled body content"
_MARKDOWN = b"From: s@example.com\r\nSubject: t\r\n\r\n**Bold** backfill"


def _dormant_thread(raw_mime=_BODY):
    """A thread with one visible message but an empty (purged) snippet."""
    thread = factories.ThreadFactory()
    factories.MessageFactory(thread=thread, raw_mime=raw_mime)
    thread.update_stats()
    # Simulate the --reset purge: stats are fine, the snippet is gone.
    models.Thread.objects.filter(id=thread.id).update(snippet="")
    thread.refresh_from_db()
    return thread


@pytest.mark.django_db
class TestBackfillThreadSnippets:
    """Progressive backfill of ``Thread.snippet`` from the latest visible
    message."""

    def test_snippet_rebuilt_from_latest_message(self):
        """A purged thread gets its snippet back, markdown-stripped."""
        thread = _dormant_thread(raw_mime=_MARKDOWN)

        call_command("backfill_thread_snippets", stdout=StringIO())

        thread.refresh_from_db()
        assert thread.snippet == "Bold backfill"

    def test_threads_with_snippet_are_skipped(self):
        """Only ``snippet=""`` threads are scanned — already-derived ones are
        left alone."""
        fresh = factories.ThreadFactory()
        factories.MessageFactory(thread=fresh, raw_mime=_BODY)
        fresh.update_stats()
        assert fresh.snippet == "Backfilled body content"

        out = StringIO()
        call_command("backfill_thread_snippets", stdout=out)
        assert "scanned=0 " in out.getvalue()

    def test_draft_only_thread_is_skipped(self):
        """A thread whose only message is a draft has no visible message to
        derive from."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, is_draft=True)
        thread.update_stats()

        call_command("backfill_thread_snippets", stdout=StringIO())

        thread.refresh_from_db()
        assert thread.snippet == ""

    def test_dry_run_writes_nothing(self):
        """--dry-run reports but does not persist, and scans each row exactly
        once (no re-scan loop even though nothing leaves the empty set)."""
        thread = _dormant_thread()

        out = StringIO()
        call_command("backfill_thread_snippets", "--dry-run", stdout=out)

        thread.refresh_from_db()
        assert thread.snippet == ""
        assert "scanned=1 " in out.getvalue()
        assert "populated=1 " in out.getvalue()

    def test_unreadable_blob_counts_error_and_continues(self):
        """A blob read failure is counted and skipped — one bad thread never
        aborts the run."""
        broken = _dormant_thread()
        healthy = _dormant_thread()

        out = StringIO()
        original = models.Message.get_parsed_data

        def flaky(self):
            if self.thread_id == broken.id:
                raise ValueError("corrupt blob")
            return original(self)

        with patch.object(models.Message, "get_parsed_data", flaky):
            call_command("backfill_thread_snippets", stdout=out)

        broken.refresh_from_db()
        healthy.refresh_from_db()
        assert broken.snippet == ""
        assert healthy.snippet == "Backfilled body content"
        assert "errors=1" in out.getvalue()

    def test_limit_bounds_the_run(self):
        """--limit caps the number of scanned threads."""
        for _ in range(3):
            _dormant_thread()

        out = StringIO()
        call_command("backfill_thread_snippets", "--limit", "2", stdout=out)
        assert "scanned=2 " in out.getvalue()

    @pytest.mark.filterwarnings("error::RuntimeWarning")
    def test_before_naive_datetime_is_treated_as_utc(self):
        """--before without offset is coerced to UTC — no naive-datetime
        warning from the ``created_at`` filter."""
        thread = _dormant_thread()

        call_command(
            "backfill_thread_snippets",
            "--before",
            "2100-01-01T00:00:00",
            stdout=StringIO(),
        )

        thread.refresh_from_db()
        assert thread.snippet == "Backfilled body content"

    @pytest.mark.filterwarnings("error::RuntimeWarning")
    def test_before_bare_date_fences_recent_threads(self):
        """A bare-date cutoff in the past excludes threads created after it."""
        _dormant_thread()

        out = StringIO()
        call_command("backfill_thread_snippets", "--before", "2000-01-01", stdout=out)
        assert "scanned=0 " in out.getvalue()

    def test_before_invalid_value_raises(self):
        """An unparseable --before aborts with an explicit error."""
        with pytest.raises(CommandError, match="not a valid ISO date/datetime"):
            call_command(
                "backfill_thread_snippets", "--before", "not-a-date", stdout=StringIO()
            )

    def test_concurrency_derives_the_same_snippets(self):
        """A pooled run is equivalent to a sequential one — the worker only
        touches already-preloaded data, never the ORM.

        The threads deliberately share one body: blobs are deduplicated by
        content hash, so all five messages point at a single ``Blob`` row while
        ``select_related`` still builds one instance each. Preloading only one
        of them left the rest deferred, and they faulted back to the database
        from a worker thread — where the test transaction is invisible.
        """
        shared = [_dormant_thread() for _ in range(5)]
        distinct = _dormant_thread(
            raw_mime=b"From: s@example.com\r\nSubject: t\r\n\r\nOwn body"
        )

        out = StringIO()
        call_command("backfill_thread_snippets", "--concurrency", "4", stdout=out)

        for thread in shared:
            thread.refresh_from_db()
            assert thread.snippet == "Backfilled body content"
        distinct.refresh_from_db()
        assert distinct.snippet == "Own body"
        assert "scanned=6 " in out.getvalue()
        assert "errors=0 " in out.getvalue()

    def test_newest_threads_are_filled_first(self):
        """A bounded run covers the most recent threads first — the ones at
        the top of the lists users look at, so the visible payoff comes with
        the first run rather than the last."""
        older = _dormant_thread()
        newer = _dormant_thread()

        call_command("backfill_thread_snippets", "--limit", "1", stdout=StringIO())

        older.refresh_from_db()
        newer.refresh_from_db()
        assert newer.snippet == "Backfilled body content"
        assert older.snippet == ""

    def test_resume_cursor_is_printed_and_skips_past(self):
        """The cursor a run reports resumes the next one past the rows it
        already scanned — the only way a re-run avoids re-reading threads that
        legitimately keep an empty snippet."""
        older = _dormant_thread()
        newer = _dormant_thread()

        out = StringIO()
        call_command("backfill_thread_snippets", "--limit", "1", stdout=out)
        cursor = out.getvalue().rsplit("resume-from: '", maxsplit=1)[1].rstrip("'\n")
        # Newest-first: the first run consumed the newer thread.
        assert str(newer.id) in cursor

        # Re-open the newer thread's backlog: without the cursor it would be
        # picked up again, with it the run continues at the older thread.
        models.Thread.objects.filter(id=newer.id).update(snippet="")

        out = StringIO()
        call_command("backfill_thread_snippets", "--resume-from", cursor, stdout=out)

        older.refresh_from_db()
        newer.refresh_from_db()
        assert newer.snippet == ""
        assert older.snippet == "Backfilled body content"

    def test_resume_from_invalid_value_raises(self):
        """A malformed --resume-from aborts rather than silently restarting."""
        with pytest.raises(CommandError, match="is not a UUID"):
            call_command(
                "backfill_thread_snippets",
                "--resume-from",
                "2026-07-01T00:00:00+00:00,nope",
                stdout=StringIO(),
            )

    def test_max_blob_size_skips_oversized_messages(self):
        """--max-blob-size leaves huge bodies alone: they cost megabytes to
        fetch and parse for the same 140 characters."""
        oversized = _dormant_thread(
            raw_mime=b"From: s@example.com\r\nSubject: t\r\n\r\n" + b"x" * (2 << 20)
        )
        normal = _dormant_thread()

        out = StringIO()
        call_command("backfill_thread_snippets", "--max-blob-size", "1", stdout=out)

        oversized.refresh_from_db()
        normal.refresh_from_db()
        assert oversized.snippet == ""
        assert normal.snippet == "Backfilled body content"
        assert "skipped=1" in out.getvalue()

    def test_reset_clears_without_rebuilding(self):
        """--reset drops snippets the old derivation produced and exits: the
        purge is table-wide while a rebuild is bounded by --limit, so chaining
        them would silently leave everything past the limit empty. The refill
        is a separate run without --reset."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_BODY)
        thread.update_stats()
        models.Thread.objects.filter(id=thread.id).update(
            snippet="<p>stale **legacy** value</p>"
        )

        out = StringIO()
        call_command("backfill_thread_snippets", "--reset", stdout=out)

        thread.refresh_from_db()
        assert thread.snippet == ""
        assert "reset 1 snippet(s)" in out.getvalue()
        # No rebuild happened in the same run — no scan report at all.
        assert "scanned=" not in out.getvalue()

        call_command("backfill_thread_snippets", stdout=StringIO())
        thread.refresh_from_db()
        assert thread.snippet == "Backfilled body content"

    def test_reset_dry_run_reports_without_clearing(self):
        """--reset is destructive, so --dry-run must count without touching."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_BODY)
        thread.update_stats()
        assert thread.snippet

        out = StringIO()
        call_command("backfill_thread_snippets", "--reset", "--dry-run", stdout=out)

        thread.refresh_from_db()
        assert thread.snippet == "Backfilled body content"
        assert "reset 1 snippet(s) (dry-run)" in out.getvalue()

    def test_reset_honours_before(self):
        """--before fences the reset to the same population it fences the
        rebuild to, so a targeted rebuild cannot wipe fresher threads."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_BODY)
        thread.update_stats()

        out = StringIO()
        call_command(
            "backfill_thread_snippets", "--reset", "--before", "2000-01-01", stdout=out
        )

        thread.refresh_from_db()
        assert thread.snippet == "Backfilled body content"
        assert "reset 0 snippet(s)" in out.getvalue()

    def test_batch_query_never_selects_blob_contents(self):
        """The batched message lookup must join blob *metadata* only.

        Regression: selecting ``raw_content`` there materialised every body of
        the batch at once — hundreds of whole MIME messages — before
        ``--max-blob-size`` could reject any, which OOM-killed the command on a
        deployment whose blobs still live in PostgreSQL.
        """
        for _ in range(3):
            _dormant_thread()

        with CaptureQueriesContext(connection) as captured:
            call_command("backfill_thread_snippets", stdout=StringIO())

        joined = [
            query["sql"]
            for query in captured.captured_queries
            if "messages_message" in query["sql"] and "messages_blob" in query["sql"]
        ]
        assert joined, "expected the batched message+blob lookup"
        assert all("raw_content" not in sql for sql in joined)

    def test_latest_message_wins_on_equal_created_at(self):
        """Ties on ``created_at`` are broken deterministically by id, so the
        backfill picks the same message ``update_stats`` would."""
        thread = factories.ThreadFactory()
        stamp = timezone.now()
        messages = [
            factories.MessageFactory(
                thread=thread,
                raw_mime=b"From: s@example.com\r\nSubject: t\r\n\r\nBody A",
            ),
            factories.MessageFactory(
                thread=thread,
                raw_mime=b"From: s@example.com\r\nSubject: t\r\n\r\nBody B",
            ),
        ]
        models.Message.objects.filter(
            id__in=[message.id for message in messages]
        ).update(created_at=stamp)
        thread.update_stats()
        models.Thread.objects.filter(id=thread.id).update(snippet="")

        call_command("backfill_thread_snippets", stdout=StringIO())

        thread.refresh_from_db()
        expected = max(messages, key=lambda message: message.id)
        assert thread.snippet == (
            "Body A" if expected.id == messages[0].id else "Body B"
        )
