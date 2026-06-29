"""Tests for ``core.mda.replies`` — reply / forward template builders.

These tests originally lived in ``jmap-email`` but moved with the
``make_reply`` / ``make_forward`` builders when those were pulled out
of the library (they bake in Messages-specific UI choices — English
header strings, ``<blockquote data-type="quote-separator">`` markup,
incomplete output dicts the outbound flow finishes).
"""

import pytest

from core.mda.replies import (
    compute_reply_threading,
    forward_subject,
    make_forward,
    make_reply,
    reply_subject,
)


class TestReplySubject:
    """``reply_subject`` adds the ``Re: `` prefix, idempotently."""

    def test_adds_re_prefix(self):
        """A subject with no Re: prefix gets one."""
        assert reply_subject("Original Subject") == "Re: Original Subject"

    def test_does_not_double_prefix(self):
        """Existing Re: is left alone — no Re: Re:."""
        assert reply_subject("Re: Already a Reply") == "Re: Already a Reply"

    def test_handles_case_insensitive_existing_prefix(self):
        """Prefix detection is case-insensitive."""
        assert reply_subject("RE: shouting") == "RE: shouting"
        assert reply_subject("re: lowercase") == "re: lowercase"

    def test_empty_subject(self):
        """Empty input still gets the prefix."""
        assert reply_subject("") == "Re: "


class TestForwardSubject:
    """``forward_subject`` mirror of ``reply_subject``."""

    def test_adds_fwd_prefix(self):
        """A subject with no Fwd: prefix gets one."""
        assert forward_subject("Original") == "Fwd: Original"

    def test_does_not_double_prefix(self):
        """Existing Fwd: is left alone — no Fwd: Fwd:."""
        assert forward_subject("Fwd: Already forwarded") == "Fwd: Already forwarded"

    def test_handles_case_insensitive_existing_prefix(self):
        """Prefix detection is case-insensitive."""
        assert forward_subject("FWD: shouting") == "FWD: shouting"


class TestComputeReplyThreading:
    """``compute_reply_threading`` projects RFC 5322 §3.6.4 ``In-Reply-To``
    and ``References`` from the parent message."""

    def test_clean_message_id_produces_both_lists(self):
        """A clean Message-ID lands in both ``inReplyTo`` and ``references``."""
        in_reply_to, references = compute_reply_threading(
            {"messageId": ["parent@example.com"]}
        )
        assert in_reply_to == ["parent@example.com"]
        assert references == ["parent@example.com"]

    def test_references_chain_is_extended_with_parent_id(self):
        """Parent id is appended to the existing chain, in order."""
        in_reply_to, references = compute_reply_threading(
            {
                "messageId": ["parent@example.com"],
                "references": ["root@example.com", "middle@example.com"],
            }
        )
        assert in_reply_to == ["parent@example.com"]
        # Parent appended to the end of the chain per RFC convention.
        assert references == [
            "root@example.com",
            "middle@example.com",
            "parent@example.com",
        ]

    def test_malformed_parent_id_yields_no_threading(self):
        """A whitespace-tainted Message-ID is dropped; downstream gets
        ``None`` for both fields (better to lose threading than to
        corrupt the chain on the receiver)."""
        in_reply_to, references = compute_reply_threading(
            {"messageId": ["bad id@example.com"]}
        )
        assert in_reply_to is None
        assert references is None

    def test_missing_message_id_yields_no_threading(self):
        """No / empty Message-ID returns (None, None)."""
        assert compute_reply_threading({}) == (None, None)
        assert compute_reply_threading({"messageId": []}) == (None, None)

    def test_malformed_references_entries_are_dropped(self):
        """Bad entries in the chain are filtered, valid ones survive."""
        in_reply_to, references = compute_reply_threading(
            {
                "messageId": ["parent@example.com"],
                "references": [
                    "good@example.com",
                    "bad id@example.com",
                    "no-at-sign",
                    "also@example.com",
                ],
            }
        )
        assert in_reply_to == ["parent@example.com"]
        assert references == [
            "good@example.com",
            "also@example.com",
            "parent@example.com",
        ]


class TestMakeReply:
    """``make_reply`` produces the reply template dict."""

    def test_creates_simple_reply(self):
        """Sender becomes recipient; from/sentAt left None for the caller."""
        original = {
            "subject": "Original",
            "from": [{"name": "Bob", "email": "bob@example.com"}],
            "sentAt": "2026-01-01T11:00:00+00:00",
            "textBody": [{"partId": "1", "content": "How are you?"}],
            "messageId": ["orig@example.com"],
        }
        reply = make_reply(original, "Doing well!")
        assert reply["subject"] == "Re: Original"
        assert reply["to"] == [{"name": "Bob", "email": "bob@example.com"}]
        assert reply["from"] is None
        assert reply["inReplyTo"] == ["orig@example.com"]
        assert reply["references"] == ["orig@example.com"]
        text = reply["textBody"][0]["content"]
        assert text.startswith("Doing well!")
        assert "On" in text and "wrote:" in text
        assert "> How are you?" in text

    def test_skips_threading_when_parent_id_malformed(self):
        """A bad parent id means no In-Reply-To and no References."""
        reply = make_reply({"subject": "Hi", "messageId": ["bad id@example.com"]}, "x")
        assert reply.get("inReplyTo") is None
        assert reply.get("references") is None

    def test_include_original_false_drops_the_quote(self):
        """When ``include_original=False`` the quote block is omitted."""
        original = {
            "subject": "Original",
            "from": [{"name": "Bob", "email": "bob@example.com"}],
            "textBody": [{"partId": "1", "content": "How are you?"}],
        }
        reply = make_reply(original, "Just my reply.", include_original=False)
        text = reply["textBody"][0]["content"]
        assert text == "Just my reply."
        assert "wrote:" not in text


class TestMakeForward:
    """``make_forward`` produces the forward template dict."""

    def test_creates_simple_forward(self):
        """Forwarded header + original body land in the new text body."""
        original = {
            "subject": "Original",
            "from": [{"name": "Bob", "email": "bob@example.com"}],
            "to": [{"name": "Charlie", "email": "charlie@example.com"}],
            "sentAt": "2026-01-01T11:00:00+00:00",
            "textBody": [{"partId": "1", "content": "The body."}],
            "messageId": ["orig@example.com"],
        }
        fwd = make_forward(original, "FYI")
        assert fwd["subject"] == "Fwd: Original"
        # Caller fills these in.
        assert fwd["to"] is None
        assert fwd["from"] is None
        text = fwd["textBody"][0]["content"]
        assert text.startswith("FYI")
        assert "---------- Forwarded message ----------" in text
        assert "From: Bob <bob@example.com>" in text
        assert "The body." in text

    def test_does_not_double_fwd(self):
        """Existing Fwd: in subject stays single-prefixed."""
        fwd = make_forward({"subject": "Fwd: already"}, "")
        assert fwd["subject"] == "Fwd: already"

    def test_html_body_is_emitted_when_original_has_html(self):
        """An HTML quote block is emitted when the original has HTML."""
        original = {
            "subject": "Original",
            "from": [{"name": "Bob", "email": "bob@example.com"}],
            "htmlBody": [{"partId": "h", "content": "<p>body</p>"}],
        }
        fwd = make_forward(original, "FYI")
        assert any("blockquote" in p["content"] for p in fwd.get("htmlBody", []))


if __name__ == "__main__":
    pytest.main()
