"""Tests for the null-safe JMAP shape accessors in
:mod:`jmap_email.helpers`.

These helpers are the documented consumer-facing affordance over a
``parse_email`` output dict; their contract (never raises, returns
defaults on absence, accepts only well-typed input) is what
downstream callers rely on.
"""

from datetime import datetime, timedelta, timezone

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


class TestAccessorsAreNullSafe:
    """The module's documented guarantee: no accessor ever raises.

    Regression: ``find_header``/``find_headers``/``has_header``/
    ``body_text_joined`` called ``.get`` on their first argument
    unguarded, so they raised ``AttributeError`` on ``None`` — which is
    exactly what ``parse_email`` returns for unparseable input, making
    ``find_header(parse_email(raw), "Subject")`` crash on the one input
    the null-safe accessors exist to survive.
    """

    def test_header_accessors_on_none(self):
        assert find_header(None, "Subject") == ""
        assert find_headers(None, "Subject") == []
        assert has_header(None, "Subject") is False

    def test_body_accessors_on_none(self):
        assert body_text_joined(None) == ""
        assert body_part_text(None, {"partId": "1"}) == ""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="none"),
            pytest.param("", id="str"),
            pytest.param(0, id="int"),
            pytest.param([], id="list"),
            pytest.param([{"name": "Subject"}], id="list-of-dicts"),
            # Truthy non-iterables: caught by an ``isinstance`` guard, not
            # by a falsiness check. ``first_address`` iterated these and
            # raised TypeError.
            pytest.param(5, id="truthy-int"),
            pytest.param(True, id="truthy-bool"),
            pytest.param(3.5, id="truthy-float"),
            pytest.param("a@b.co", id="truthy-str"),
        ],
    )
    def test_no_accessor_raises_on_wrong_shape(self, value):
        assert find_header(value, "Subject") == ""
        assert find_headers(value, "Subject") == []
        assert has_header(value, "Subject") is False
        assert body_text_joined(value) == ""
        assert first_address(value) is None
        assert first_msgid(value) == ""

    def test_body_text_joined_with_non_list_key(self):
        assert body_text_joined({"textBody": "not-a-list"}) == ""


class TestMsgidChainIsHeaderSafe:
    """``msgid_chain`` exists to be written straight into a header.

    That makes it the one accessor that must not hand back something
    unwritable. It is the same reasoning ``is_valid_msg_id`` gives for
    being strict: the caller keeps what it got and may put it somewhere
    other than ``compose_email``.
    """

    @pytest.mark.parametrize(
        ("ids", "reason"),
        [
            pytest.param(["a@x\r\nBcc: evil@x.co"], "crlf", id="crlf-injection"),
            pytest.param(["a@x\nBcc: evil@x.co"], "lf", id="lf-injection"),
            pytest.param(["a b@x"], "folds mid-id", id="internal-space"),
            pytest.param(["a\tb@x"], "folds mid-id", id="internal-tab"),
            pytest.param(["a<b@x"], "ambiguous token", id="nested-open"),
            pytest.param(["a>b@x"], "ambiguous token", id="nested-close"),
            pytest.param(["a\x00b@x"], "control char", id="nul"),
        ],
    )
    def test_unwritable_entries_are_dropped(self, ids, reason):
        assert msgid_chain(ids) == "", reason

    def test_good_entries_survive_a_dropped_neighbour(self):
        assert msgid_chain(["a@x", "b\r\nc@x", "d@x"]) == "<a@x> <d@x>"

    @pytest.mark.parametrize(
        ("ids", "expected"),
        [
            pytest.param(["a@x"], "<a@x>", id="bare"),
            pytest.param(["<a@x>"], "<a@x>", id="already-wrapped"),
            pytest.param(["a@x", "b@x"], "<a@x> <b@x>", id="chain"),
            # obs-id-left: real Outlook/MAPI ids carry several "@".
            pytest.param(["foo$@local@domain"], "<foo$@local@domain>", id="multi-at"),
            # No "@" at all is malformed but harmless; a reassembly helper
            # should not silently lose it.
            pytest.param(["12345"], "<12345>", id="no-at"),
        ],
    )
    def test_legitimate_ids_round_trip(self, ids, expected):
        assert msgid_chain(ids) == expected

    def test_output_can_be_written_into_a_header(self):
        """The end-to-end promise: whatever comes out is emittable."""
        from email.parser import BytesParser
        from email.policy import default as default_policy

        chain = msgid_chain(["a@x", "evil\r\nBcc: x@y.co", "b@x"])
        raw = f"References: {chain}\r\n\r\n".encode()
        parsed = BytesParser(policy=default_policy).parsebytes(raw)
        assert parsed["Bcc"] is None
        assert len(parsed.keys()) == 1


class TestSentAtToDatetimeIsAlwaysAware:
    """The docstring promises tz-aware; a naive return breaks callers.

    ``datetime.fromisoformat`` yields a naive object for an input with no
    offset, and mixing that into a comparison with an aware datetime
    raises ``TypeError`` at the call site rather than here.
    """

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("2026-01-01T00:00:00+00:00", id="with-offset"),
            pytest.param("2026-01-01T00:00:00", id="no-offset"),
            pytest.param("2026-01-01", id="bare-date"),
            pytest.param("2026-01-01T00:00:00+02:00", id="non-utc-offset"),
        ],
    )
    def test_result_is_always_comparable_to_an_aware_datetime(self, value):
        result = sent_at_to_datetime(value)
        assert result is not None
        assert result.utcoffset() is not None
        # The property that actually matters at the call site.
        assert isinstance(result < datetime.now(timezone.utc), bool)

    def test_naive_datetime_input_is_stamped_utc(self):
        assert sent_at_to_datetime(datetime(2026, 1, 1)) == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )

    def test_aware_datetime_input_keeps_its_offset(self):
        aware = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))
        assert sent_at_to_datetime(aware) == aware
        assert sent_at_to_datetime(aware).utcoffset() == timedelta(hours=2)

    def test_unparseable_still_returns_none(self):
        assert sent_at_to_datetime("not a date") is None
        assert sent_at_to_datetime(None) is None
