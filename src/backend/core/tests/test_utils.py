"""Tests for ``core.utils``."""

from datetime import datetime

from jmap_email import compose_email

from core.mda.utils import SNIPPET_MAX_LENGTH, current_sent_at, thread_snippet


class TestCurrentSentAt:
    """``current_sent_at`` is the single ``sentAt`` source for outbound
    composition — its output must round-trip through ``compose_email``
    and ``datetime.fromisoformat`` cleanly."""

    def test_round_trips_through_compose_email(self):
        """The string ``current_sent_at`` returns is accepted by the
        composer's strict ``sentAt`` validation — pin that contract so
        a future timezone / format change can't break outbound."""
        raw = compose_email(
            {
                "from": [{"email": "s@example.com"}],
                "to": [{"email": "r@example.com"}],
                "subject": "t",
                "sentAt": current_sent_at(),
                "textBody": [{"content": "body"}],
            }
        )
        assert raw.startswith(b"MIME-Version") or b"Date:" in raw

    def test_returns_parseable_iso_8601_with_offset(self):
        """The string contains a tz offset (``+`` / ``-`` / ``Z``) so
        ``datetime.fromisoformat`` returns a tz-aware datetime."""
        dt = datetime.fromisoformat(current_sent_at())
        assert dt.tzinfo is not None


class TestThreadSnippet:
    """``thread_snippet`` is the single thread-listing snippet source —
    it prefers the parser's ``preview``, falls back to ``textBody``,
    then to the caller-supplied default."""

    def test_uses_preview_when_present(self):
        """The library's ``preview`` field wins over textBody / fallback —
        it's already HTML-stripped and whitespace-normalised."""
        parsed = {
            "preview": "Hello from preview",
            "textBody": [{"partId": "1", "content": "raw text body"}],
        }
        assert thread_snippet(parsed, fallback="ignored") == "Hello from preview"

    def test_falls_back_to_text_body(self):
        """When ``parse_email`` was called with ``preview=False`` (or
        the caller hand-built the dict), the first text body part wins."""
        parsed = {
            "textBody": [{"partId": "1", "content": "From text body"}],
        }
        assert thread_snippet(parsed, fallback="ignored") == "From text body"

    def test_falls_back_to_body_values_projection(self):
        """body_values=True projection: textBody[i] has no inline
        ``content`` — the helper reads through to ``bodyValues``."""
        parsed = {
            "textBody": [{"partId": "p1"}],
            "bodyValues": {
                "p1": {
                    "value": "From bodyValues",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                }
            },
        }
        assert thread_snippet(parsed) == "From bodyValues"

    def test_falls_back_to_fallback_when_no_body(self):
        """Empty parsed dict (header-only / parse failure) returns the
        caller's fallback rather than empty."""
        assert thread_snippet({}, fallback="default text") == "default text"

    def test_falls_back_to_empty_when_nothing(self):
        """With neither parsed data nor fallback, returns the empty
        string rather than raising."""
        assert thread_snippet({}) == ""

    def test_truncates_to_snippet_max_length(self):
        """Any candidate longer than ``SNIPPET_MAX_LENGTH`` is sliced
        before return."""
        long_preview = "x" * (SNIPPET_MAX_LENGTH * 2)
        out = thread_snippet({"preview": long_preview})
        assert len(out) == SNIPPET_MAX_LENGTH

    def test_handles_none_parsed_email(self):
        """Defensive: callers passing ``None`` (e.g. parse_email
        returned ``{}`` on error and then the caller coerced) get the
        fallback rather than an AttributeError."""
        assert thread_snippet(None, fallback="fb") == "fb"

    def test_empty_text_body_list(self):
        """An empty ``textBody`` list (no parts at all) falls through
        to the caller-supplied fallback rather than crashing on
        ``textBody[0]``."""
        assert thread_snippet({"textBody": []}, fallback="fb") == "fb"
        assert thread_snippet({"textBody": []}) == ""

    def test_multiple_text_body_entries_uses_first(self):
        """When ``textBody`` carries several parts (multipart/alternative
        with text/plain + text/html copied to both arrays), only the
        first contributes — same behaviour the search-index and snippet
        consumers rely on."""
        parsed = {
            "textBody": [
                {"partId": "1", "content": "first"},
                {"partId": "2", "content": "second"},
            ],
        }
        assert thread_snippet(parsed) == "first"

    def test_missing_partid_in_body_values(self):
        """A truncated walk (M22 part-count cap) can emit body parts
        whose ``partId`` does not appear in ``bodyValues``. The helper
        falls through to the caller fallback rather than KeyError."""
        parsed = {
            "textBody": [{"partId": "p_missing"}],
            "bodyValues": {},
        }
        assert thread_snippet(parsed, fallback="fb") == "fb"
        assert thread_snippet(parsed) == ""

    def test_exact_snippet_max_length_boundary(self):
        """A candidate of exactly ``SNIPPET_MAX_LENGTH`` passes through
        unchanged — the truncation slice is inclusive of the cap."""
        content = "y" * SNIPPET_MAX_LENGTH
        out = thread_snippet({"preview": content})
        assert len(out) == SNIPPET_MAX_LENGTH
        assert out == content
