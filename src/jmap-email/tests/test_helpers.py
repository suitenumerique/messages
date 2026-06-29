"""Tests for the null-safe JMAP shape accessors in
:mod:`jmap_email.helpers`.

These helpers are the documented consumer-facing affordance over a
``parse_email`` output dict; their contract (never raises, returns
defaults on absence, accepts only well-typed input) is what
downstream callers rely on.
"""

from datetime import datetime, timezone

import pytest

from jmap_email.helpers import (
    body_part_text,
    body_text_joined,
    find_header,
    find_headers,
    first_address,
    first_address_email,
    first_address_name,
    first_msgid,
    has_header,
    msgid_chain,
    sent_at_to_datetime,
)


class TestFirstAddress:
    def test_picks_first_with_email(self):
        addrs = [
            {"name": "Alice", "email": "alice@x.com"},
            {"name": "Bob", "email": "bob@x.com"},
        ]
        assert first_address(addrs) == addrs[0]

    def test_skips_entries_without_email(self):
        """A leading entry with no ``email`` field is skipped — the
        first usable mailbox wins."""
        addrs = [{"name": "no email"}, {"email": "real@x.com"}]
        assert first_address(addrs) == {"email": "real@x.com"}

    def test_empty_input_returns_none(self):
        assert first_address(None) is None
        assert first_address([]) is None

    def test_first_address_email_and_name(self):
        addrs = [{"name": "Alice", "email": "alice@x.com"}]
        assert first_address_email(addrs) == "alice@x.com"
        assert first_address_name(addrs) == "Alice"

    def test_first_address_email_empty_when_absent(self):
        assert first_address_email(None) == ""
        assert first_address_name(None) == ""


class TestMsgIds:
    def test_first_msgid_picks_first_non_empty(self):
        assert first_msgid(["", "a@x", "b@x"]) == "a@x"

    def test_first_msgid_rejects_scalar(self):
        """JMAP ``MessageIds`` is ``String[]``; a scalar string is
        treated as malformed input, NOT iterated character-by-character."""
        assert first_msgid("a@x") == ""

    def test_first_msgid_empty_list_returns_empty(self):
        assert first_msgid([]) == ""
        assert first_msgid(None) == ""

    def test_msgid_chain_wraps_in_angle_brackets(self):
        """The JMAP wire shape strips angle brackets; the chain helper
        re-adds them to produce the RFC 5322 ``References`` form."""
        assert msgid_chain(["a@x", "b@x"]) == "<a@x> <b@x>"

    def test_msgid_chain_does_not_double_wrap(self):
        """Already-wrapped ids stay single-wrapped."""
        assert msgid_chain(["<a@x>", "b@x"]) == "<a@x> <b@x>"

    def test_msgid_chain_drops_empty_entries(self):
        assert msgid_chain(["", "a@x", None, "b@x"]) == "<a@x> <b@x>"


class TestSentAt:
    def test_iso_string_parses(self):
        dt = sent_at_to_datetime("2026-06-08T12:00:00+00:00")
        assert dt == datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    def test_datetime_passes_through(self):
        original = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        assert sent_at_to_datetime(original) is original

    def test_returns_none_on_garbage(self):
        assert sent_at_to_datetime("not a date") is None
        assert sent_at_to_datetime("") is None
        assert sent_at_to_datetime(None) is None

    def test_returns_none_on_unsupported_type(self):
        assert sent_at_to_datetime(12345) is None


class TestHeaderLookup:
    def _parsed(self):
        return {
            "headers": [
                {"name": "From", "value": "a@x"},
                {"name": "Received", "value": "first"},
                {"name": "Received", "value": "second"},
                {"name": "X-Custom", "value": "foo"},
            ]
        }

    def test_find_header_is_case_insensitive(self):
        parsed = self._parsed()
        assert find_header(parsed, "received") == "first"
        assert find_header(parsed, "RECEIVED") == "first"
        assert find_header(parsed, "from") == "a@x"

    def test_find_header_returns_empty_when_absent(self):
        assert find_header(self._parsed(), "Subject") == ""
        assert find_header({}, "Subject") == ""

    def test_find_headers_returns_all_in_document_order(self):
        parsed = self._parsed()
        assert find_headers(parsed, "received") == ["first", "second"]

    def test_find_headers_returns_empty_list_when_absent(self):
        assert find_headers({}, "Subject") == []

    def test_has_header_true_and_false(self):
        parsed = self._parsed()
        assert has_header(parsed, "Received") is True
        assert has_header(parsed, "DKIM-Signature") is False
        assert has_header({}, "Subject") is False


class TestBodyAccess:
    """Pin the projection-transparent body accessor."""

    def test_inline_content_path(self):
        """``body_values=False`` projection: read ``content`` inline."""
        parsed = {"textBody": [{"partId": "1", "content": "hello"}]}
        assert body_part_text(parsed, parsed["textBody"][0]) == "hello"

    def test_body_values_path(self):
        """``body_values=True`` (spec-default) projection: read from
        ``bodyValues[partId]``."""
        parsed = {
            "textBody": [{"partId": "p7"}],
            "bodyValues": {
                "p7": {
                    "value": "from side table",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                }
            },
        }
        assert body_part_text(parsed, parsed["textBody"][0]) == "from side table"

    def test_inline_wins_over_body_values(self):
        """Mixed-projection input: the inline ``content`` field is
        authoritative. ``bodyValues`` is the side-table fallback."""
        parsed = {
            "textBody": [{"partId": "1", "content": "inline"}],
            "bodyValues": {
                "1": {"value": "side", "isEncodingProblem": False, "isTruncated": False}
            },
        }
        assert body_part_text(parsed, parsed["textBody"][0]) == "inline"

    def test_returns_empty_for_truncated_part(self):
        """A M22-truncated part may carry neither ``content`` nor a
        ``partId`` — the helper returns empty rather than raising."""
        assert body_part_text({}, {"type": "text/plain"}) == ""

    def test_returns_empty_on_non_dict_part(self):
        assert body_part_text({}, None) == ""
        assert body_part_text({}, "garbage") == ""  # type: ignore[arg-type]

    def test_bytes_content_returns_empty(self):
        """Attachments carry ``bytes`` in ``content``; this helper is
        text-only and returns empty for those — call sites use a
        different path for binary bodies."""
        att = {"partId": "2", "content": b"\x89PNG"}
        assert body_part_text({}, att) == ""

    def test_body_text_joined_concatenates_textBody(self):
        parsed = {
            "textBody": [
                {"partId": "1", "content": "alpha"},
                {"partId": "2", "content": "beta"},
            ]
        }
        assert body_text_joined(parsed) == "alphabeta"

    def test_body_text_joined_supports_htmlBody(self):
        parsed = {"htmlBody": [{"partId": "1", "content": "<p>x</p>"}]}
        assert body_text_joined(parsed, "htmlBody") == "<p>x</p>"

    def test_body_text_joined_empty_when_key_absent(self):
        """``parse_headers`` output has no ``textBody`` at all — must
        return empty string rather than raising KeyError."""
        assert body_text_joined({}) == ""

    def test_body_text_joined_walks_through_body_values(self):
        parsed = {
            "textBody": [{"partId": "1"}, {"partId": "2"}],
            "bodyValues": {
                "1": {
                    "value": "alpha",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                },
                "2": {
                    "value": "beta",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                },
            },
        }
        assert body_text_joined(parsed) == "alphabeta"


if __name__ == "__main__":
    pytest.main()
