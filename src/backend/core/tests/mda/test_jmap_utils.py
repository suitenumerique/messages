"""Tests for the JMAP shape helpers in ``core.mda.jmap_utils``.

Most helpers (``first_address`` / ``first_msgid`` / ``find_header`` etc.)
are null-safe one-liners and don't need their own tests beyond the
integration coverage they get through the parser-driven backend tests.

The ``body_part_text`` / ``body_text_joined`` pair is new in 0.1.0 and
spans the projection-flip the ``jmap-email`` library performed
(``body_values=True`` default). They are the consumer-facing affordance
that keeps the rest of the backend transparent to which projection the
parser emits, so they need pinned behavior.
"""

import pytest

from jmap_email import (
    body_part_text,
    body_text_joined,
)


class TestBodyPartText:
    """``body_part_text`` reads body text from either projection."""

    def test_returns_inline_content_when_present(self):
        """``body_values=False`` projection: ``content`` lives on the
        part itself; the helper returns it as-is without touching
        ``bodyValues``."""
        parsed = {
            "textBody": [{"partId": "1", "content": "hello"}],
            # bodyValues absent — non-default projection
        }
        assert body_part_text(parsed, parsed["textBody"][0]) == "hello"

    def test_returns_bodyvalues_value_when_content_stripped(self):
        """``body_values=True`` projection (jmap-email spec default):
        ``content`` is stripped from the part and the text lives in
        ``bodyValues[partId]["value"]``."""
        parsed = {
            "textBody": [{"partId": "p7"}],
            "bodyValues": {"p7": {"value": "spec default", "isEncodingProblem": False, "isTruncated": False}},
        }
        assert body_part_text(parsed, parsed["textBody"][0]) == "spec default"

    def test_returns_empty_when_part_id_missing(self):
        """A truncated part (M22 cap) may have neither ``content`` nor a
        ``partId``. The helper must NOT raise — return empty string."""
        parsed = {"bodyValues": {}}
        assert body_part_text(parsed, {"type": "text/plain"}) == ""

    def test_returns_empty_when_body_values_absent(self):
        """``body_values=True`` projection with no ``bodyValues`` map at
        all (e.g. ``parse_headers`` output): empty string, no KeyError."""
        parsed = {"textBody": [{"partId": "1"}]}
        assert body_part_text(parsed, parsed["textBody"][0]) == ""

    def test_inline_content_wins_over_body_values(self):
        """When both shapes are present (mixed-projection input), the
        inline ``content`` field is the authoritative source — it's the
        explicit per-part value, ``bodyValues`` is the side-table."""
        parsed = {
            "textBody": [{"partId": "1", "content": "inline wins"}],
            "bodyValues": {"1": {"value": "side table", "isEncodingProblem": False, "isTruncated": False}},
        }
        assert body_part_text(parsed, parsed["textBody"][0]) == "inline wins"

    def test_non_dict_part_returns_empty(self):
        """Pathological / malformed parsed data: a non-dict in the body
        list must not crash the helper."""
        assert body_part_text({}, None) == ""
        assert body_part_text({}, "not a dict") == ""  # type: ignore[arg-type]

    def test_bytes_content_on_attachment_is_not_returned(self):
        """Attachments carry ``bytes`` in their ``content`` field, but
        ``body_part_text`` is a text helper. Calling it on an attachment
        shape returns empty rather than coercing bytes to repr."""
        attachment = {"partId": "2", "type": "image/png", "content": b"\x89PNG\r\n"}
        assert body_part_text({"attachments": [attachment]}, attachment) == ""


class TestBodyTextJoined:
    """``body_text_joined`` concatenates every part under the named key."""

    def test_joins_all_text_body_parts(self):
        """A multi-part text body returns the concatenation in order.

        The helper does not add a separator — callers that need one
        should apply their own delimiter (consistent with
        ``str.join``)."""
        parsed = {
            "textBody": [
                {"partId": "1", "content": "alpha"},
                {"partId": "2", "content": "beta"},
            ],
        }
        assert body_text_joined(parsed, "textBody") == "alphabeta"

    def test_joins_under_body_values_projection(self):
        """Under ``body_values=True`` (spec default), the helper walks
        ``textBody`` for the partIds and reads each value from
        ``bodyValues``."""
        parsed = {
            "textBody": [{"partId": "1"}, {"partId": "2"}],
            "bodyValues": {
                "1": {"value": "alpha", "isEncodingProblem": False, "isTruncated": False},
                "2": {"value": "beta", "isEncodingProblem": False, "isTruncated": False},
            },
        }
        assert body_text_joined(parsed, "textBody") == "alphabeta"

    def test_html_key_is_supported(self):
        """``key=`` lets callers target ``htmlBody`` (or any future
        body-array field) without a second helper."""
        parsed = {
            "htmlBody": [{"partId": "1", "content": "<p>x</p>"}],
        }
        assert body_text_joined(parsed, "htmlBody") == "<p>x</p>"

    def test_missing_key_returns_empty(self):
        """Header-only parses (``parse_headers``) have no ``textBody``
        at all. The helper returns ``""`` instead of raising."""
        assert body_text_joined({}, "textBody") == ""

    def test_default_key_is_text_body(self):
        """The most common use is text-body extraction; the default
        kwarg makes the call site terser."""
        parsed = {"textBody": [{"partId": "1", "content": "x"}]}
        assert body_text_joined(parsed) == "x"


if __name__ == "__main__":
    pytest.main()
