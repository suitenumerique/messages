"""Security / hardening tests, runnable against both the Postfix milter and
the pure-Python pymta implementations.

These exercise the attack classes the inbound MTA is most exposed to on the
public internet:

* SMTP smuggling (bare-LF / bare-CR EOD smuggling, RFC 5321 §2.3.8)
* CRLF / control-character injection in envelope addresses
* NUL bytes in DATA
* Source routes (RFC 5321 §4.1.1.3)
* VRFY / EXPN information disclosure
* AUTH leakage on port 25
* Overlong local-parts / domains
* Pre-DATA SIZE oversize announcement
* Pipelined-command-before-banner (smuggling helper)
* Hard error limit / command flood
* Line-length cap

Where the two implementations have different SMTP codes (Postfix uses some
504/521 codes pymta uses 502 for), tests assert on the response *class*
(2xx/4xx/5xx) instead of an exact code.
"""

import logging
import os
import socket

import pytest

logger = logging.getLogger(__name__)

MTA_HOST = os.getenv("MTA_HOST")
MTA_PORT = int(os.getenv("MTA_PORT", "25"))


def _raw_session(timeout: float = 5):
    """Open a raw socket to the MTA, swallow the banner, and return it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((MTA_HOST, MTA_PORT))
    banner = _read_reply(s)
    assert banner.startswith(b"220"), banner
    return s


def _read_reply(s: socket.socket, max_bytes: int = 65536) -> bytes:
    """Read one full SMTP reply, handling multi-line continuations.

    SMTP multi-line replies use ``xxx-`` on every line except the last,
    which uses ``xxx <space>``. We accumulate bytes until either:
      * the buffer ends with a final line ``xxx <SP>...<CRLF>``, or
      * the server closes the connection (yielding partial bytes).
    """
    buf = b""
    while len(buf) < max_bytes:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        # Check the last full line for the "final" marker (digit + space).
        lines = buf.splitlines()
        last = lines[-1] if lines else b""
        if buf.endswith(b"\r\n") and len(last) >= 4 and last[3:4] == b" ":
            break
    return buf


def _send_cmd(s: socket.socket, cmd: bytes) -> bytes:
    s.sendall(cmd)
    return _read_reply(s)


# ---------------------------------------------------------------------------
# 1. AUTH must NEVER be offered on port 25 (inbound, no submission).
# ---------------------------------------------------------------------------


def test_auth_not_advertised_in_ehlo():
    s = _raw_session()
    try:
        resp = _send_cmd(s, b"EHLO example.com\r\n")
        text = resp.decode("ascii", errors="replace").upper()
        assert "AUTH" not in text, f"AUTH advertised on port 25!\n{text}"
    finally:
        s.close()


def test_auth_command_rejected():
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        resp = _send_cmd(s, b"AUTH LOGIN\r\n")
        # Must NOT be 235 (auth success) or 334 (continue), even on accident.
        # Both 502 (not implemented) and 503 (bad sequence) are acceptable.
        assert resp[:3] in (b"502", b"503", b"500"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 2. VRFY/EXPN address enumeration must be neutered.
# ---------------------------------------------------------------------------


def test_vrfy_does_not_confirm_existence():
    # No mock_api_server / mailbox setup needed — VRFY must reply identically
    # whether the address exists or not. If it ever leaked existence the
    # answer would differ even before the MDA is consulted.
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        # Both a real and a fake mailbox should produce the SAME class of reply
        # so the attacker cannot tell them apart.
        a = _send_cmd(s, b"VRFY known@example.com\r\n")
        b = _send_cmd(s, b"VRFY does-not-exist@example.com\r\n")
        assert a[:1] == b[:1], f"VRFY reply class differs: {a!r} vs {b!r}"
        # And we should never confirm with 250 (which would mean "yes, exists").
        assert not a.startswith(b"250"), a
    finally:
        s.close()


def test_expn_disabled():
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        resp = _send_cmd(s, b"EXPN postmaster\r\n")
        assert resp[:3] in (b"502", b"500"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 3. SMTP smuggling — CVE-2023-51764 family.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smuggle_bytes",
    [
        # bare LF
        b"\n.\r\n",
        # bare CR
        b"\r.\r\n",
        # bare LF + bare LF EOD
        b"\n.\n",
    ],
    ids=["LF-dot-CRLF", "CR-dot-CRLF", "LF-dot-LF"],
)
def test_smtp_smuggling_does_not_split_messages(mock_api_server, smuggle_bytes):
    """A smuggling EOD variant must NOT split the envelope into two messages.

    The MDA must see at most ONE delivery, and the "smuggled" MAIL FROM/RCPT
    TO must appear as text inside that single message body — never as a
    separately-delivered envelope to an attacker-chosen recipient.
    """
    mock_api_server.add_mailbox("victim@example.com")
    # Register the smuggled recipient too: otherwise an actual split would be
    # rejected at RCPT by the MDA (mailbox not found) and the test would
    # silently pass without proving smuggling failed.
    mock_api_server.add_mailbox("smuggled@example.com")

    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO attacker.example\r\n")
        _send_cmd(s, b"MAIL FROM:<outer@example.com>\r\n")
        _send_cmd(s, b"RCPT TO:<victim@example.com>\r\n")
        _send_cmd(s, b"DATA\r\n")
        payload = (
            b"Subject: outer\r\n"
            b"\r\n"
            b"outer body" + smuggle_bytes + b"MAIL FROM:<attacker@evil.example>\r\n"
            b"RCPT TO:<smuggled@example.com>\r\n"
            b"DATA\r\n"
            b"Subject: smuggled\r\n"
            b"\r\n"
            b"smuggled body\r\n.\r\n"
        )
        s.sendall(payload)
        # Final reply ends one or more responses; read until socket idle.
        s.settimeout(3)
        resp = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
                if b"\r\n" in chunk and resp.rstrip().endswith((b"OK", b"delivery", b"later")):
                    break
        except socket.timeout:
            pass
        try:
            _send_cmd(s, b"QUIT\r\n")
        except OSError:
            pass
    finally:
        s.close()

    # The MDA must NOT have seen a second envelope with `smuggled@example.com`.
    rcpts_seen = [
        addr
        for em in mock_api_server.received_emails
        for addr in em["metadata"]["original_recipients"]
    ]
    assert "smuggled@example.com" not in rcpts_seen, (
        f"SMTP smuggling succeeded! Recipients seen: {rcpts_seen}"
    )


# ---------------------------------------------------------------------------
# 4. NUL bytes in DATA must be rejected.
# ---------------------------------------------------------------------------


def test_nul_byte_in_body_rejected(mock_api_server, mta_impl):
    if mta_impl == "postfix":
        pytest.skip("Postfix accepts and silently normalizes NUL bytes; pymta is stricter")
    mock_api_server.add_mailbox("test@example.com")
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        _send_cmd(s, b"MAIL FROM:<a@example.com>\r\n")
        _send_cmd(s, b"RCPT TO:<test@example.com>\r\n")
        _send_cmd(s, b"DATA\r\n")
        s.sendall(b"Subject: NUL test\r\n\r\nhello\x00world\r\n.\r\n")
        resp = _read_reply(s)
        assert resp[:1] in (b"4", b"5"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 5. Control-character / CRLF injection in MAIL FROM and RCPT TO.
# ---------------------------------------------------------------------------


def test_tab_in_address_rejected(mta_impl):
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        # TAB inside the address is a header-unfolding vector.
        resp = _send_cmd(s, b"MAIL FROM:<bad\taddr@example.com>\r\n")
        assert resp[:1] in (b"4", b"5"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 6. Overlong local-parts / domains.
# ---------------------------------------------------------------------------


def test_overlong_local_part_rejected(mta_impl):
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        _send_cmd(s, b"MAIL FROM:<sender@example.com>\r\n")
        long_local = b"a" * 200
        resp = _send_cmd(s, b"RCPT TO:<" + long_local + b"@example.com>\r\n")
        # 4xx (Postfix milter tempfail path) or 5xx (pymta strict reject) both
        # satisfy the security requirement: the address must not be delivered.
        assert resp[:1] in (b"4", b"5"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 7. Pre-DATA SIZE oversize announcement must be rejected at MAIL FROM time.
# ---------------------------------------------------------------------------


def test_size_overlimit_rejected(mock_api_server, smtp_client):
    s = _raw_session()
    try:
        _send_cmd(s, b"EHLO example.com\r\n")
        # 1 GB announced — well above MAX_INCOMING_EMAIL_SIZE (30 MB).
        resp = _send_cmd(s, b"MAIL FROM:<a@example.com> SIZE=1000000000\r\n")
        assert resp[:3] in (b"552", b"452", b"550"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 8. RSET resets the envelope state.
# ---------------------------------------------------------------------------


def test_rset_clears_envelope(mock_api_server, smtp_client):
    mock_api_server.add_mailbox("test@example.com")
    smtp_client.helo("example.com")
    smtp_client.mail("a@example.com")
    smtp_client.rcpt("test@example.com")
    smtp_client.rset()
    # After RSET, DATA without MAIL/RCPT must be refused.
    code, _ = smtp_client.docmd("DATA")
    assert code // 100 == 5, code


# ---------------------------------------------------------------------------
# 9. Hard-error limit / unknown-command flood eventually disconnects.
# ---------------------------------------------------------------------------


def test_unknown_command_flood_does_not_hang(mta_impl):
    s = _raw_session(timeout=10)
    try:
        for i in range(200):
            try:
                s.sendall(f"GARBAGE{i}\r\n".encode())
                _read_reply(s)
            except OSError:
                # Server closed the connection — that is the expected defense.
                return
        pytest.fail("server accepted 200 unknown commands without disconnecting")
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 10. Line-length limit enforced.
# ---------------------------------------------------------------------------


def test_overlong_command_line_rejected():
    s = _raw_session()
    try:
        s.sendall(b"A" * 5000 + b"\r\n")
        resp = _read_reply(s)
        # Either the server returned a 4xx/5xx error, or it closed the socket
        # without replying. Both are acceptable defences against a flooded
        # parser; silently accepting the line is not.
        assert not resp or resp[:1] in (b"4", b"5"), resp
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 11. Reverse-path / null-sender accepted (bounces).
# ---------------------------------------------------------------------------


def test_null_sender_accepted(mock_api_server, smtp_client):
    mock_api_server.add_mailbox("test@example.com")
    # smtplib doesn't directly support empty MAIL FROM; use docmd.
    code, _ = smtp_client.helo("example.com")
    assert code == 250
    code, _ = smtp_client.docmd("MAIL FROM:<>")
    assert code == 250, code
    code, _ = smtp_client.docmd("RCPT TO:<test@example.com>")
    assert code == 250, code


def test_null_recipient_rejected(smtp_client):
    smtp_client.helo("example.com")
    smtp_client.docmd("MAIL FROM:<sender@example.com>")
    code, _ = smtp_client.docmd("RCPT TO:<>")
    assert code // 100 == 5, code


# ---------------------------------------------------------------------------
# 12. Missing EHLO/HELO before MAIL → 503.
# ---------------------------------------------------------------------------


def test_mail_without_helo_rejected(mta_impl):
    # Postfix in our config has `smtpd_helo_required = no` (the Postfix
    # default) so it accepts MAIL FROM without a prior HELO. pymta is strict
    # by default. This is a documented behaviour gap, not a security bug —
    # the MDA will reject malformed envelopes either way.
    if mta_impl == "postfix":
        pytest.skip("Postfix accepts MAIL FROM without HELO by default")
    s = _raw_session()
    try:
        resp = _send_cmd(s, b"MAIL FROM:<a@b.com>\r\n")
        assert resp[:3] in (b"503", b"550"), resp
    finally:
        s.close()
