"""Tests for ``core.utils``."""

import pytest

from core.mda.utils import SNIPPET_MAX_LENGTH, thread_snippet


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


if __name__ == "__main__":
    pytest.main()
