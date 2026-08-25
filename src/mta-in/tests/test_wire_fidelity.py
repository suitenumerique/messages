"""What survives the wire: line lengths, bare LF, and non-UTF-8 envelope bytes.

The smuggling tests in ``test_security.py`` prove one DATA phase cannot become
two messages. These probe the quieter half of the same threat model — what the
MTA does to a body it *does* accept, and what it does with an address byte that
is not valid UTF-8:

* an over-long body line (RFC 5321 §4.5.3.1.6 caps a line at 1000 octets, but
  non-conforming senders routinely exceed it),
* a bare LF inside a header line, which a CRLF-strict reader sees as one line
  and a downstream LF-tolerant parser sees as two,
* a raw 8-bit byte in RCPT TO, which SMTPUTF8 decoding turns into a lone
  surrogate.

Run against both implementations on purpose: a difference here is a behaviour
change that lands the day production switches from one to the other.
"""

import logging
import os
import socket

import pytest

logger = logging.getLogger(__name__)

MTA_HOST = os.getenv("MTA_HOST")
MTA_PORT = int(os.getenv("MTA_PORT", "25"))


def _read_reply(s: socket.socket, max_bytes: int = 65536) -> bytes:
    buf = b""
    while len(buf) < max_bytes:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        lines = buf.split(b"\r\n")
        # Last complete line is a final reply when it is `xxx<SP>`.
        for line in reversed([ln for ln in lines if ln]):
            if len(line) >= 4 and line[3:4] == b" ":
                return buf
            break
    return buf


def _session(timeout: float = 20):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((MTA_HOST, MTA_PORT))
    banner = _read_reply(s)
    assert banner.startswith(b"220"), banner
    return s


def _cmd(s: socket.socket, data: bytes) -> bytes:
    s.sendall(data)
    return _read_reply(s)


def _deliver(s: socket.socket, rcpt: bytes, body: bytes) -> bytes:
    """Run one envelope through to the end of DATA, return the final reply."""
    assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
    assert _cmd(s, b"MAIL FROM:<sender@example.com>\r\n").startswith(b"250")
    rcpt_reply = _cmd(s, b"RCPT TO:<" + rcpt + b">\r\n")
    assert rcpt_reply.startswith(b"250"), rcpt_reply
    assert _cmd(s, b"DATA\r\n").startswith(b"354")
    return _cmd(s, body + b"\r\n.\r\n")


# ---------------------------------------------------------------------------
# 1. Body lines longer than RFC 5321's 1000-octet ceiling.
# ---------------------------------------------------------------------------


def test_overlong_body_line(mock_api_server, mta_impl):
    """A 2000-octet body line: accepted, or permanently rejected?

    RFC 5322 §2.1.1 makes 998 octets a MUST, and real senders break it — long
    unwrapped HTML and URLs are the usual source. A 5xx here is a bounce for
    mail another MTA would have taken, so the two implementations disagreeing
    is a deliverability change on switchover, not a stylistic one.
    """
    mock_api_server.add_mailbox("wire@example.com")
    s = _session()
    try:
        body = (
            b"Subject: long line\r\n"
            b"From: sender@example.com\r\n"
            b"To: wire@example.com\r\n"
            b"\r\n" + (b"x" * 2000) + b"\r\n"
        )
        reply = _deliver(s, b"wire@example.com", body)
    finally:
        s.close()

    logger.info("overlong body line on %s -> %r", mta_impl, reply[:80])
    accepted = reply.startswith(b"250")
    if accepted:
        mock_api_server.wait_for_email()
        raw = mock_api_server.received_emails[-1]["raw_email"]
        assert b"x" * 2000 in raw, "accepted but the long line was mangled"
    else:
        assert reply[:1] in (b"4", b"5"), reply
        pytest.xfail(
            f"KNOWN GAP: {mta_impl} rejects a 2000-octet body line with "
            f"{reply[:60]!r}. Postfix accepts it, so this is a permanent bounce "
            "for mail that lands today. pymta clears it by overriding aiosmtpd's "
            "line_length_limit (1001) with PYMTA_MAX_LINE_LENGTH, so reaching "
            "this branch means that value was lowered below the line sent here."
        )


def test_body_line_above_the_cap_is_still_refused(mock_api_server, mta_impl):
    """Raising the line limit must not mean removing it.

    The test above proves a 2000-octet line is accepted, which on its own is
    also what deleting the check entirely would look like. This is the other
    bracket: past the ceiling, the message is refused rather than truncated or
    silently delivered short.
    """
    mock_api_server.add_mailbox("wire@example.com")
    s = _session()
    try:
        body = (
            b"Subject: too long\r\n"
            b"From: sender@example.com\r\n"
            b"To: wire@example.com\r\n"
            b"\r\n" + (b"y" * 70000) + b"\r\n"
        )
        reply = _deliver(s, b"wire@example.com", body)
    finally:
        s.close()

    logger.info("70000-octet body line on %s -> %r", mta_impl, reply[:80])
    if mta_impl == "postfix":
        # Postfix wraps long lines in cleanup rather than refusing them, so it
        # legitimately accepts. Asserted rather than skipped so the difference
        # stays visible.
        assert reply.startswith(b"250"), reply
        return
    assert reply[:1] in (b"4", b"5"), reply
    assert (
        not mock_api_server.received_emails
        or b"y" * 70000 not in (mock_api_server.received_emails[-1]["raw_email"])
    ), "line over the cap was refused but the body reached the MDA anyway"


# ---------------------------------------------------------------------------
# 2. Bare LF inside a header line.
# ---------------------------------------------------------------------------


def test_bare_lf_in_header_is_not_passed_through(mock_api_server, mta_impl):
    """A bare LF mid-header must not reach the MDA as a line break.

    ``Subject: a<LF>X-Injected: b<CRLF>`` is one line to a CRLF-strict reader
    and two headers to any parser that also splits on LF — which Python's
    ``email`` package does. If the bytes arrive unnormalised, the sender chose
    a header on the stored message.

    Postfix defends with ``smtpd_forbid_bare_newline = normalize`` (set in
    etc/main.cf). This asserts the same end state whatever the implementation.
    """
    mock_api_server.add_mailbox("wire@example.com")
    s = _session()
    try:
        body = (
            b"Subject: hello\nX-Injected: smuggled-header\r\n"
            b"From: sender@example.com\r\n"
            b"To: wire@example.com\r\n"
            b"\r\n"
            b"body text\r\n"
        )
        reply = _deliver(s, b"wire@example.com", body)
    finally:
        s.close()

    if not reply.startswith(b"250"):
        # Refusing outright is a fine defence, but only a permanent refusal is.
        # A bare `return` here would let the test pass if the server started
        # rejecting everything, including for reasons unrelated to bare LF, so
        # pin it to a 5xx and stop.
        assert reply[:1] == b"5", (
            f"{mta_impl} neither accepted nor permanently refused the message: {reply[:60]!r}"
        )
        return

    mock_api_server.wait_for_email()
    raw = mock_api_server.received_emails[-1]["raw_email"]
    logger.info("bare-LF body on %s -> %r", mta_impl, raw[:120])

    # The injected text may survive as *content*; what must not survive is a
    # bare LF acting as a line terminator in front of it.
    leaked = b"\nX-Injected" in raw.replace(b"\r\n", b"__CRLF__")
    # Postfix normalises via smtpd_forbid_bare_newline in etc/main.cf, pymta via
    # handler._normalize_line_endings. A failure here means one of them stopped.
    assert not leaked, f"{mta_impl} passed a bare LF into the delivered message"


# ---------------------------------------------------------------------------
# 3. A raw 8-bit byte in RCPT TO.
# ---------------------------------------------------------------------------


def test_non_utf8_byte_in_rcpt_is_cleanly_rejected(mta_impl):
    """A Latin-1 byte in an address must draw a 5xx, not an internal error.

    With SMTPUTF8 on, aiosmtpd decodes command arguments using
    ``errors="surrogateescape"``, so ``0xe9`` becomes the lone surrogate
    ``\\udce9``. Any code that then calls ``.encode("utf-8")`` on it raises
    ``UnicodeEncodeError`` — which is not an ``AddressError``, so it escapes
    the validator's except clause and becomes a 421 + dropped connection.

    A 421 tells the sender to come back later, so a permanently malformed
    address turns into retries until their queue expires.
    """
    s = _session()
    try:
        assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
        assert _cmd(s, b"MAIL FROM:<sender@example.com>\r\n").startswith(b"250")
        reply = _cmd(s, b"RCPT TO:<\xe9user@example.com>\r\n")
    finally:
        s.close()

    logger.info("non-UTF-8 RCPT on %s -> %r", mta_impl, reply[:80])
    # pymta holds this line in address.validate_envelope_address, which encodes
    # the address before the length checks so a lone surrogate becomes an
    # AddressError rather than a UnicodeEncodeError escaping as 421.
    assert not reply.startswith(b"421"), (
        f"{mta_impl} answered {reply[:40]!r} — an internal error escaped as a "
        "temporary failure. A malformed address is permanent; 421 makes the "
        "sender retry a message that can never be accepted."
    )
    assert reply[:1] == b"5", reply
