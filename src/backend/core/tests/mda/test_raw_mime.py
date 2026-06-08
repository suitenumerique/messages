"""Tests for ``core.mda.raw_mime.remove_mime_headers``."""

# pylint: disable=missing-function-docstring

import pytest

from core.mda.raw_mime import remove_mime_headers


class TestRemoveMimeHeaders:  # pylint: disable=too-many-public-methods
    """Strip selected headers from raw MIME bytes byte-faithfully."""

    BASE_HEAD = (
        b"Received: from mta.example.com (mta.example.com [10.0.0.1])\r\n"
        b"\tby relay.example.com with SMTP id abc123\r\n"
        b"From: sender@example.com\r\n"
        b"To: rcpt@example.com\r\n"
        b"Subject: Hi\r\n"
    )
    BODY = b"\r\n\r\nThis is the body.\r\n"

    # --- Empty filters -----------------------------------------------------

    def test_no_filters_returns_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        assert remove_mime_headers(raw) is raw

    def test_empty_iterables_return_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        assert remove_mime_headers(raw, prefixes=[], names=[]) is raw

    def test_no_match_returns_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-no-such-"], names=["x-also-no"])
        assert out is raw

    # --- Exact name matching ----------------------------------------------

    def test_exact_name_match(self):
        raw = b"From: a@b\r\nX-Custom: value\r\nSubject: hi\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["X-Custom"])
        assert b"X-Custom" not in out
        assert b"From: a@b" in out
        assert b"Subject: hi" in out

    def test_exact_name_case_insensitive(self):
        raw = b"X-CuStOm: value\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["x-custom"])
        assert b"X-CuStOm" not in out

    def test_exact_name_does_not_match_prefix(self):
        """``names=['x-foo']`` must not strip ``x-foo-bar``."""
        raw = b"X-Foo-Bar: keep\r\nX-Foo: drop\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["x-foo"])
        assert b"X-Foo-Bar: keep" in out
        assert b"X-Foo: drop" not in out

    def test_multiple_exact_names(self):
        raw = b"X-A: 1\r\nX-B: 2\r\nX-C: 3\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["X-A", "X-C"])
        assert b"X-A:" not in out
        assert b"X-C:" not in out
        assert b"X-B: 2" in out

    # --- Prefix matching ---------------------------------------------------

    def test_prefix_match(self):
        raw = (
            b"X-StMsg-Sender-Auth: pass\r\n"
            b"X-StMsg-Widget-Referer: evil.com\r\n"
            b"From: a@b\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-" not in out
        assert b"From: a@b" in out

    def test_prefix_case_insensitive(self):
        raw = b"x-STmsg-Foo: drop\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["X-StMsg-"])
        assert b"x-STmsg-Foo" not in out

    def test_multiple_prefixes(self):
        raw = b"X-Spam-Score: 5\r\nX-StMsg-Foo: 1\r\nX-Other: keep\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-spam-", "x-stmsg-"])
        assert b"X-Spam-Score" not in out
        assert b"X-StMsg-Foo" not in out
        assert b"X-Other: keep" in out

    def test_prefix_does_not_match_value_substring(self):
        """A prefix matches header NAMES only — never values."""
        raw = b"Subject: x-stmsg-not-a-header\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert out is raw

    # --- Combined filters --------------------------------------------------

    def test_prefixes_and_names_combined(self):
        raw = b"X-StMsg-Foo: 1\r\nDKIM-Signature: v=1; ...\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"], names=["DKIM-Signature"])
        assert b"X-StMsg-Foo" not in out
        assert b"DKIM-Signature" not in out
        assert b"From: a@b" in out

    # --- Folding (RFC 5322 §2.2.3) ----------------------------------------

    def test_folded_target_header_dropped_completely(self):
        raw = (
            b"X-StMsg-Widget-Referer: http://evil.com/\r\n"
            b" /some/very/long/path\r\n"
            b"\t?that_continues=yes\r\n"
            b"From: a@b\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"evil.com" not in out
        assert b"some/very/long/path" not in out
        assert b"that_continues" not in out
        assert b"From: a@b" in out

    def test_folded_unrelated_header_preserved(self):
        raw = (
            b"Subject: a very long\r\n"
            b" subject line with\r\n"
            b"\tmultiple folds\r\n"
            b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"a very long" in out
        assert b" subject line with" in out
        assert b"\tmultiple folds" in out
        assert b"X-StMsg-Sender-Auth" not in out

    def test_continuation_after_drop_then_real_header(self):
        """A real header following a dropped folded header is preserved."""
        raw = b"X-StMsg-Foo: a\r\n continued\r\nReal-Header: kept\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Foo" not in out
        assert b"continued" not in out
        assert b"Real-Header: kept" in out

    # --- Line endings ------------------------------------------------------

    def test_lf_only_line_endings(self):
        raw = b"X-StMsg-Sender-Auth: pass\nFrom: a@b\n\nbody"
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Sender-Auth" not in out
        assert b"From: a@b" in out
        assert out.endswith(b"\n\nbody")

    # --- Body / boundary semantics ----------------------------------------

    def test_body_left_byte_identical(self):
        body = b"\r\n\r\nLine1\r\nX-StMsg-Foo: looks like a header but it's body\r\n"
        raw = b"From: a@b\r\nX-StMsg-Foo: bar\r\n" + body
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert out.endswith(body)
        head = out.split(b"\r\n\r\n", 1)[0]
        assert b"X-StMsg-" not in head

    def test_retained_headers_byte_identical(self):
        raw = self.BASE_HEAD + b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        for line in self.BASE_HEAD.splitlines(keepends=True):
            assert line in out

    def test_no_body_separator(self):
        raw = b"X-StMsg-Sender-Auth: pass\r\nFrom: a@b\r\n"
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Sender-Auth" not in out
        assert b"From: a@b" in out

    def test_empty_input(self):
        assert remove_mime_headers(b"", prefixes=["x-stmsg-"]) == b""

    # --- Malformed input ---------------------------------------------------

    def test_malformed_line_without_colon_preserved(self):
        raw = (
            b"From: a@b\r\n"
            b"some-malformed-line-no-colon\r\n"
            b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"some-malformed-line-no-colon" in out
        assert b"X-StMsg-Sender-Auth" not in out

    def test_malformed_line_resets_drop_state(self):
        """A non-continuation line clears any in-progress drop, even if malformed."""
        raw = (
            b"X-StMsg-Foo: a\r\n"
            b"malformed-without-colon\r\n"
            b" indented-after-malformed\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Foo" not in out
        assert b"malformed-without-colon" in out
        # The indented line is a continuation of the malformed line — kept.
        assert b" indented-after-malformed" in out


if __name__ == "__main__":
    pytest.main()
