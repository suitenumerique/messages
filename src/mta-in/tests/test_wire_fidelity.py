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
        # An unterminated "250 O" also matches `xxx<SP>`, so drop a mid-line tail.
        lines = buf.split(b"\r\n") if buf.endswith(b"\r\n") else buf.split(b"\r\n")[:-1]
        # Last complete line is a final reply when it is `xxx<SP>`.
        complete = [ln for ln in lines if ln]
        if complete and len(complete[-1]) >= 4 and complete[-1][3:4] == b" ":
            return buf
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
            "for mail that lands today. pymta clears it because its line limit "
            "is PYMTA_MAX_LINE_LENGTH (64 KiB by default), so reaching "
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

    With SMTPUTF8 on, the command parser decodes arguments using
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


# ---------------------------------------------------------------------------
# 4. A return path past both RFC 5321 §4.5.3.1 caps (64 local, 256 path).
# ---------------------------------------------------------------------------


def test_long_srs_return_path_is_delivered_intact(mock_api_server, mta_impl):
    """A forwarder's SRS return path, 400 octets long.

    Past the 64-octet local-part and the 256-octet path the RFC suggests,
    which Postfix never enforced either. The command line carries every MAIL
    parameter we advertise, so this also proves it stays inside the 548-octet
    MAIL command limit and that the MDA receives the sender unaltered.
    """
    mock_api_server.add_mailbox("wire@example.com")
    domain = b"forwarder.example"
    prefix = b"SRS0=Ab3d=TT=bounces.mailer.example="
    local = prefix + b"b" * (400 - 1 - len(domain) - len(prefix))
    sender = local + b"@" + domain
    assert len(sender) == 400
    s = _session()
    try:
        assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
        reply = _cmd(s, b"MAIL FROM:<" + sender + b"> SIZE=1024 BODY=8BITMIME SMTPUTF8\r\n")
        assert reply.startswith(b"250"), reply
        assert _cmd(s, b"RCPT TO:<wire@example.com>\r\n").startswith(b"250")
        assert _cmd(s, b"DATA\r\n").startswith(b"354")
        body = (
            b"Subject: forwarded\r\n"
            b"From: sender@example.com\r\n"
            b"To: wire@example.com\r\n"
            b"\r\nbody\r\n"
        )
        reply = _cmd(s, body + b"\r\n.\r\n")
    finally:
        s.close()

    logger.info("400-octet return path on %s -> %r", mta_impl, reply[:80])
    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    assert mock_api_server.received_emails[-1]["metadata"]["sender"] == sender.decode()


def _send_one(ehlo: bytes, mail_from: bytes) -> bytes:
    """One message to wire@example.com. Returns the reply to the end of DATA."""
    s = _session()
    try:
        assert _cmd(s, ehlo).startswith(b"250")
        reply = _cmd(s, b"MAIL FROM:<" + mail_from + b">\r\n")
        assert reply.startswith(b"250"), reply
        assert _cmd(s, b"RCPT TO:<wire@example.com>\r\n").startswith(b"250")
        assert _cmd(s, b"DATA\r\n").startswith(b"354")
        return _cmd(s, b"Subject: x\r\nFrom: sender@example.com\r\n\r\nbody\r\n.\r\n")
    finally:
        s.close()


def test_a_verp_return_path_with_a_percent_is_delivered(mock_api_server):
    """VERP encodes the recipient into the return path, ``@`` as ``%40``."""
    mock_api_server.add_mailbox("wire@example.com")
    sender = b"bounce-first.last%40town.example@esp.example"
    reply = _send_one(b"EHLO probe.example.com\r\n", sender)
    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    assert mock_api_server.received_emails[-1]["metadata"]["sender"] == sender.decode()


def test_a_tagged_recipient_reaches_the_mda_unchanged(mock_api_server):
    """``user+tag@`` is passed as sent, to the check and in the delivery.

    Which mailbox it belongs to is the MDA's decision: the mock only knows the
    tagged spelling, so a tag stripped anywhere on the way would be a 550.
    """
    mock_api_server.add_mailbox("wire+Tag.2@example.com")
    s = _session()
    try:
        assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
        assert _cmd(s, b"MAIL FROM:<sender@example.com>\r\n").startswith(b"250")
        reply = _cmd(s, b"RCPT TO:<wire+Tag.2@example.com>\r\n")
        assert reply.startswith(b"250"), reply
        assert _cmd(s, b"DATA\r\n").startswith(b"354")
        reply = _cmd(s, b"Subject: x\r\nFrom: sender@example.com\r\n\r\nbody\r\n.\r\n")
    finally:
        s.close()
    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    metadata = mock_api_server.received_emails[-1]["metadata"]
    assert metadata["original_recipients"] == ["wire+Tag.2@example.com"]


def test_dsn_and_auth_parameters_do_not_bounce_the_message(mock_api_server):
    """Senders send these whether or not DSN and AUTH are offered."""
    mock_api_server.add_mailbox("wire@example.com")
    s = _session()
    try:
        assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
        reply = _cmd(s, b"MAIL FROM:<sender@example.com> AUTH=<> RET=HDRS ENVID=QQ314159\r\n")
        assert reply.startswith(b"250"), reply
        reply = _cmd(
            s,
            b"RCPT TO:<wire@example.com> NOTIFY=SUCCESS,FAILURE ORCPT=rfc822;wire@example.com\r\n",
        )
        assert reply.startswith(b"250"), reply
        assert _cmd(s, b"DATA\r\n").startswith(b"354")
        reply = _cmd(s, b"Subject: x\r\nFrom: sender@example.com\r\n\r\nbody\r\n.\r\n")
    finally:
        s.close()
    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    metadata = mock_api_server.received_emails[-1]["metadata"]
    assert metadata["sender"] == "sender@example.com"
    assert metadata["original_recipients"] == ["wire@example.com"]


def test_a_helo_that_is_not_utf8_does_not_cost_the_sender_its_mail(mock_api_server, mta_impl):
    """The greeting is the sender's own name for itself; mail does not hang on it.

    pymta only: Postfix answers ``500 5.5.2 Error: bad UTF-8 syntax`` to the
    EHLO itself.
    """
    if mta_impl != "pymta":
        pytest.skip("Postfix refuses the EHLO command")
    mock_api_server.add_mailbox("wire@example.com")
    reply = _send_one(b"EHLO mx\xe9.sender.example\r\n", b"sender@example.com")
    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    helo = mock_api_server.received_emails[-1]["metadata"].get("client_helo")
    assert helo == "mx?.sender.example"


# ---------------------------------------------------------------------------
# 5. Dot-stuffing on lines that end in a bare LF.
# ---------------------------------------------------------------------------


def test_dot_stuffing_is_undone_on_bare_lf_lines(mock_api_server, mta_impl):
    """A sender that ends lines with LF stuffs by its own line boundaries.

    The protocol only unstuffs lines that ended in CRLF, so without the
    handler's pass ``..hidden`` would reach the mailbox with both dots. Postfix's
    ``normalize`` undoes it too, so both implementations store the same bytes.
    """
    mock_api_server.add_mailbox("wire@example.com")
    s = _session()
    try:
        body = (
            b"Subject: dots\nFrom: sender@example.com\nTo: wire@example.com\n\n"
            b"line one\n..hidden.example\n...\nend"
        )
        reply = _deliver(s, b"wire@example.com", body)
    finally:
        s.close()

    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    stored = mock_api_server.received_emails[-1]["raw_email"]
    logger.info("bare-LF dot-stuffing on %s -> %r", mta_impl, stored[-60:])
    assert stored.endswith(b"\r\n\r\nline one\r\n.hidden.example\r\n..\r\nend\r\n"), stored


# ---------------------------------------------------------------------------
# 6. An envelope too large for an HTTP header.
# ---------------------------------------------------------------------------


def test_a_hundred_long_recipients_are_delivered(mock_api_server, mta_impl):
    """About 10 kB of recipient addresses.

    As JWT claims that is an Authorization header gunicorn refuses (8190
    bytes), which pymta would turn into a 451 on every retry. pymta sends the
    envelope in the body instead. The milter still uses the header.
    """
    if mta_impl == "postfix":
        pytest.skip("the milter sends the envelope as JWT claims")
    recipients = [f"{'r' * 56}{i:03d}@example.com" for i in range(100)]
    for rcpt in recipients:
        mock_api_server.add_mailbox(rcpt)
    s = _session()
    try:
        assert _cmd(s, b"EHLO probe.example.com\r\n").startswith(b"250")
        assert _cmd(s, b"MAIL FROM:<sender@example.com>\r\n").startswith(b"250")
        for rcpt in recipients:
            assert _cmd(s, b"RCPT TO:<" + rcpt.encode() + b">\r\n").startswith(b"250"), rcpt
        assert _cmd(s, b"DATA\r\n").startswith(b"354")
        message = b"Subject: many\r\nFrom: sender@example.com\r\n\r\nbody\r\n"
        reply = _cmd(s, message + b".\r\n")
    finally:
        s.close()

    assert reply.startswith(b"250"), reply
    mock_api_server.wait_for_email()
    received = mock_api_server.received_emails[-1]
    assert received["metadata"]["original_recipients"] == recipients
    assert received["metadata"]["sender"] == "sender@example.com"
    assert received["raw_email"] == message
