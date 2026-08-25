# test_smtp_protocol.py

import logging
import os
import random
import smtplib
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText

import pytest

from pymta import settings

logger = logging.getLogger(__name__)
MTA_HOST = os.getenv("MTA_HOST")


def test_smtp_command_sequence():
    """Test proper SMTP command sequencing"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)

        # Read greeting
        response = s.recv(1024).decode()
        assert response.startswith("220")

        # Test HELO
        s.send(b"HELO example.com\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("250")

        # Test MAIL FROM with no prior RCPT TO (should fail)
        s.send(b"DATA\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("503")  # Bad sequence of commands


def test_malformed_commands():
    """Test handling of malformed SMTP commands"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Test invalid command
        s.send(b"INVALID\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("500")  # Unknown command

        # Test malformed MAIL FROM
        s.send(b"HELO example.com\r\n")
        s.recv(1024)
        s.send(b"MAIL FROM: <invalid@em ail>\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("501")  # Syntax error


def test_partial_writes():
    """Test handling of partial writes and interrupted transmissions"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Send HELO command in chunks
        s.send(b"HE")
        time.sleep(0.1)
        s.send(b"LO example")
        time.sleep(0.1)
        s.send(b".com\r\n")

        response = s.recv(1024).decode()
        assert response.startswith("250")


# Pipelining is deliberately NOT advertised in EHLO (see handle_EHLO) and
# command_call_limit enforces strict per-verb counts. A test that pipelines
# multiple commands at once would assert behaviour we *avoid* — leaving the
# placeholder skipped would only rot, so we don't keep one.


def _full_reply(sock) -> bytes:
    """Read one complete SMTP reply, following ``xxx-`` continuation lines.

    A single recv() can return a partial multiline reply, which silently turns
    "STARTTLS is not advertised" and "STARTTLS had not arrived yet" into the
    same test failure.
    """
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return buf
        buf += chunk
        lines = [ln for ln in buf.split(b"\r\n") if ln]
        if lines and len(lines[-1]) >= 4 and lines[-1][3:4] == b" ":
            return buf


def test_starttls_negotiation(mta_impl):
    """STARTTLS is advertised, the handshake completes, and mail flows after it.

    Only the pymta test image has a cert wired (baked into the runtime-dev stage
    of Dockerfile.pymta); the Postfix dev config has none, so it does not
    advertise STARTTLS at all.
    """
    if mta_impl == "postfix":
        pytest.skip("no cert wired in the Postfix dev image, so STARTTLS is off")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(10)
        assert s.recv(1024).startswith(b"220")

        s.send(b"EHLO example.com\r\n")
        assert b"STARTTLS" in _full_reply(s)

        s.send(b"STARTTLS\r\n")
        assert s.recv(1024).startswith(b"220")

        # Complete the handshake. The cert is self-signed and the name will not
        # match, so verification is off: we are testing our side of the
        # negotiation, not the trust chain.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(s, server_hostname="mta-in.test") as tls:
            tls.settimeout(10)
            # RFC 3207 §4.2: the server discards all knowledge from before TLS,
            # so EHLO has to be reissued and must be accepted.
            tls.send(b"EHLO example.com\r\n")
            reply = _full_reply(tls)
            assert reply.startswith(b"250"), reply
            # And STARTTLS must no longer be offered inside the session.
            assert b"STARTTLS" not in reply, reply

            tls.send(b"QUIT\r\n")
            assert tls.recv(1024).startswith(b"221")


def test_starttls_resets_envelope_state(mta_impl):
    """A transaction started before STARTTLS must not survive it.

    RFC 3207 §4.2. If MAIL FROM leaked across the handshake, a peer could set up
    an envelope in plaintext and have it accepted as though it had been given
    inside the encrypted session.
    """
    if mta_impl == "postfix":
        pytest.skip("no cert wired in the Postfix dev image, so STARTTLS is off")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(10)
        s.recv(1024)
        s.send(b"EHLO example.com\r\n")
        _full_reply(s)
        s.send(b"MAIL FROM:<before-tls@example.com>\r\n")
        assert s.recv(1024).startswith(b"250")

        s.send(b"STARTTLS\r\n")
        assert s.recv(1024).startswith(b"220")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(s, server_hostname="mta-in.test") as tls:
            tls.settimeout(10)
            tls.send(b"EHLO example.com\r\n")
            _full_reply(tls)
            # The pre-TLS MAIL FROM must be gone: RCPT now has no transaction.
            tls.send(b"RCPT TO:<someone@example.com>\r\n")
            reply = tls.recv(1024)
            assert reply.startswith(b"503"), reply


def test_connection_limits(mock_api_server):
    """Concurrent connections below the per-IP cap all succeed.

    Deliberately under PYMTA_MAX_SESSIONS_PER_IP: the whole suite connects from
    loopback, so every session buckets under one IP. Exceeding the cap is
    covered by test_per_ip_connection_cap below.
    """

    def make_connection():
        try:
            client = smtplib.SMTP(MTA_HOST, 25)
            client.helo("example.com")
            time.sleep(random.uniform(0.02, 0.06))
            client.quit()
            return True
        except (smtplib.SMTPException, socket.error) as e:
            logger.error(f"Connection failed: {str(e)}")
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(make_connection) for _ in range(60)]
        results = [f.result() for f in as_completed(futures)]

    assert all(results)


def test_per_ip_connection_cap(mta_impl):
    """One source cannot take every connection slot, and is refused politely.

    The share is a fraction of PYMTA_MAX_SESSIONS_TOTAL rather than a fixed
    number, so this asserts the shape (some refused, not all held) rather than
    an exact count.

    pymta-only: the shipped Postfix config disables per-client limits outright
    with ``smtpd_client_event_limit_exceptions = static:all``.

    The point is not just that excess connections fail, but that they get a 421
    and a close rather than a hang. A sender that hangs holds its own queue slot
    and retries slowly; one that gets 421 backs off and comes back.
    """
    if mta_impl != "pymta":
        pytest.skip("Postfix ships with per-client limits disabled")

    held = []
    refused = 0
    try:
        # Comfortably over the cap, held open so they stay concurrent.
        for _ in range(settings.PYMTA_MAX_SESSIONS_TOTAL + 20):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((MTA_HOST, 25))
            greeting = s.recv(1024)
            if greeting.startswith(b"421"):
                refused += 1
                s.close()
            else:
                assert greeting.startswith(b"220"), greeting
                held.append(s)
    finally:
        for s in held:
            s.close()

    assert refused > 0, "the per-source share never engaged"
    assert len(held) < settings.PYMTA_MAX_SESSIONS_TOTAL, (
        f"one source held {len(held)} of {settings.PYMTA_MAX_SESSIONS_TOTAL} slots; "
        "a single host must never be able to take them all"
    )


# The idle-command timeout is not tested here. It is read once at startup, so
# covering it against the running server would mean idling for the configured
# 120 s. tests/test_hardened_smtp.py drives the same code path against an
# in-process listener with a one-second deadline instead, and also checks that
# activity re-arms it.


@pytest.mark.parametrize(
    "n_recipients, will_fail",
    [
        (99, False),
        # Just over the 100 cap, not wildly over. Postfix sleeps about a second
        # per error once past its soft limit, so 1200 recipients cost 41s of
        # wall clock and proved nothing that 150 does not.
        (150, True),
    ],
)
def test_max_recipients(smtp_client, mock_api_server, n_recipients, will_fail):
    """Test maximum number of recipients handling"""
    msg = MIMEText("Test")
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test max recipients"

    # Try with a large number of recipients
    recipients = [f"test{i}@example.com" for i in range(n_recipients)]
    msg["To"] = ", ".join(recipients)

    # Add mailboxes
    for recipient in recipients:
        mock_api_server.add_mailbox(recipient)

    if will_fail:
        # smtplib raises only when EVERY recipient is refused. Past the cap the
        # first 100 are accepted, so the refusals come back as a dict instead.
        #
        # Keep the overshoot small. Enough rejections to spend
        # PYMTA_MAX_ERRORS_PER_SESSION would drop the session, which passes this
        # test for the wrong reason, and Postfix sleeps about a second per error
        # past its soft limit.
        refused = smtp_client.send_message(msg)
        assert refused, "nothing was refused past the recipient cap"
        assert len(refused) == n_recipients - 100, sorted(refused)[:5]
    else:
        smtp_client.send_message(msg)

        # Give MTA time to process
        logger.info("Waiting for email processing")
        mock_api_server.wait_for_email(n=1, timeout=20)

        # Check if our mock API received the email
        assert len(mock_api_server.received_emails) > 0
