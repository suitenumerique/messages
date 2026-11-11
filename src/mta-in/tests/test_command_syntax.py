"""What the server answers to each malformed or misplaced command.

One row per reply the command parser can give. These are the replies a
sender's MTA acts on, so the exact code matters and the text is pinned too.
"""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import struct

import pytest

from pymta import settings
from pymta.mda_async import MDAResult

from tests.harness import ScriptedMDA, capture_all, connected, listening, records

EHLO = b"EHLO mx.sender.example"
HELO = b"HELO mx.sender.example"
MAIL = b"MAIL FROM:<alice@sender.example>"
RCPT = b"RCPT TO:<bob@example.com>"

_MAIL_SYNTAX = b"501 Syntax: MAIL FROM: <address>"
_RCPT_SYNTAX = b"501 Syntax: RCPT TO: <address>"
_EXTENDED = b" [SP <mail-parameters>]"

# (commands sent first, the command under test, its whole reply)
_REFUSALS = [
    ([], b"HELO", b"501 Syntax: HELO hostname\r\n"),
    ([], b"EHLO", b"501 Syntax: EHLO hostname\r\n"),
    ([], b"", b"500 Error: bad syntax\r\n"),
    ([], b"\xc3\xa9HLO x", b"500 Error: bad syntax\r\n"),
    ([], b"BOGUS", b"500 5.5.2 Error: command not recognized\r\n"),
    ([EHLO], b"RSET now", b"501 Syntax: RSET\r\n"),
    ([EHLO], b"QUIT now", b"501 Syntax: QUIT\r\n"),
    ([EHLO], b"STARTTLS", b"454 TLS not available\r\n"),
    # Sequence.
    ([], MAIL, b"503 Error: send HELO first\r\n"),
    ([], RCPT, b"503 Error: send HELO first\r\n"),
    ([], b"DATA", b"503 Error: send HELO first\r\n"),
    ([EHLO], RCPT, b"503 Error: need MAIL command\r\n"),
    ([EHLO, MAIL], b"DATA", b"503 Error: need RCPT command\r\n"),
    ([EHLO, MAIL], MAIL, b"503 Error: nested MAIL command\r\n"),
    ([EHLO, MAIL, RCPT], b"DATA now", b"501 Syntax: DATA\r\n"),
    # MAIL.
    ([EHLO], b"MAIL", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO], b"MAIL TO:<alice@sender.example>", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO], b"MAIL FROM:", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    ([HELO], b"MAIL", _MAIL_SYNTAX + b"\r\n"),
    # Parameters are an ESMTP feature: none after HELO.
    ([HELO], MAIL + b" SIZE=100", _MAIL_SYNTAX + b"\r\n"),
    ([EHLO], MAIL + b" SIZE=", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO], MAIL + b" SIZE=abc", b"501 5.5.4 Bad SIZE parameter\r\n"),
    ([EHLO], MAIL + b" SIZE", b"501 5.5.4 Bad SIZE parameter\r\n"),
    ([EHLO], MAIL + b" SIZE=1 SIZE=2", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO], MAIL + b" X-FOO=1", _MAIL_SYNTAX + _EXTENDED + b"\r\n"),
    (
        [EHLO],
        MAIL + b" XFOO=1",
        b"555 MAIL FROM parameters not recognized or not implemented\r\n",
    ),
    (
        [EHLO],
        MAIL + b" BODY=BINARYMIME",
        b"501 Error: BODY can only be one of 7BIT, 8BITMIME\r\n",
    ),
    ([EHLO], MAIL + b" SMTPUTF8=1", b"501 Error: SMTPUTF8 takes no arguments\r\n"),
    (
        [EHLO],
        MAIL + b" SIZE=99999999999",
        b"552 5.3.4 Message size exceeds fixed maximum\r\n",
    ),
    # RCPT.
    ([EHLO, MAIL], b"RCPT", _RCPT_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO, MAIL], b"RCPT FROM:<bob@example.com>", _RCPT_SYNTAX + _EXTENDED + b"\r\n"),
    ([EHLO, MAIL], b"RCPT TO:", _RCPT_SYNTAX + _EXTENDED + b"\r\n"),
    ([HELO, MAIL], b"RCPT", _RCPT_SYNTAX + b"\r\n"),
    ([HELO, MAIL], RCPT + b" XFOO=1", _RCPT_SYNTAX + b"\r\n"),
    ([EHLO, MAIL], RCPT + b" FOO=", _RCPT_SYNTAX + _EXTENDED + b"\r\n"),
    (
        [EHLO, MAIL],
        RCPT + b" XFOO=1",
        b"555 RCPT TO parameters not recognized or not implemented\r\n",
    ),
    # Brackets or quotes that never close.
    ([EHLO, MAIL], b"RCPT TO:<bob@example.com", b"553 5.1.3 Error: malformed address\r\n"),
    ([EHLO, MAIL], b'RCPT TO:<"bob@example.com>', b"553 5.1.3 Error: malformed address\r\n"),
    ([EHLO], b"MAIL FROM:<alice@sender.example SIZE=1", b"553 5.1.3 Error: malformed address\r\n"),
    # What RFC 5322 allows in a header address and RFC 5321 does not in a path.
    ([EHLO], b"MAIL FROM:<alice(home)@sender.example>", b"553 5.1.3 Malformed local part\r\n"),
    ([EHLO], b"MAIL FROM:<a\\lice@sender.example>", b"553 5.1.3 Malformed local part\r\n"),
    (
        [EHLO],
        b"MAIL FROM:<alice @sender.example>",
        b"501 5.1.7 Address contains forbidden control characters\r\n",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "command", "expected"), _REFUSALS)
async def test_refusal(before, command, expected, caplog):
    mda = ScriptedMDA()
    async with listening(mda) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                for line in before:
                    assert (await client.cmd(line))[:1] == b"2", line
                assert await client.cmd(command) == expected
                # Refused, and the session goes on.
                assert await client.cmd(b"NOOP") == b"250 OK\r\n"
            # Every refusal has its line (the logging invariant).
            rejects = await records(caplog, "reject")
    assert len(rejects) == 1, [r.__dict__ for r in rejects]
    assert rejects[0].code == int(expected[:3])
    assert not mda.delivered


_ACCEPTED = [
    ([], b"ehlo mx.sender.example", b"250"),
    ([], b"EhLo mx.sender.example", b"250"),
    ([EHLO], b"mail from:<alice@sender.example>", b"250"),
    ([EHLO], b"MAIL FROM: <alice@sender.example>", b"250"),
    ([EHLO], b"MAIL FROM:<alice@sender.example> BODY=8BITMIME", b"250"),
    ([EHLO], b"MAIL FROM:<alice@sender.example> body=7bit", b"250"),
    ([EHLO], b"MAIL FROM:<alice@sender.example> SMTPUTF8", b"250"),
    ([EHLO], b"MAIL FROM:<alice@sender.example> SIZE=100 BODY=8BITMIME SMTPUTF8", b"250"),
    ([EHLO], b"MAIL FROM:<>", b"250"),
    # RFC 5321 section 4.1.1.3: a source route is accepted and ignored.
    ([EHLO], b"MAIL FROM:<@a.example,@b.example:alice@sender.example>", b"250"),
    ([EHLO, MAIL], b'RCPT TO:<"bob"@example.com>', b"250"),
    ([EHLO, MAIL], b"rcpt to:<bob@example.com>", b"250"),
    ([EHLO, MAIL], b"RCPT TO: <bob@example.com>", b"250"),
    # The parameters we ignore are dropped before the HELO rule sees them.
    ([HELO, MAIL], RCPT + b" NOTIFY=NEVER", b"250"),
    ([HELO], MAIL + b" RET=HDRS", b"250"),
    ([EHLO], b"NOOP anything at all", b"250"),
    ([EHLO], b"RSET", b"250"),
    ([], b"HELP", b"214"),
    ([], b"HELP MAIL", b"214"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "command", "code"), _ACCEPTED)
async def test_accepted(before, command, code):
    async with listening() as port, connected(port) as client:
        for line in before:
            assert (await client.cmd(line))[:1] == b"2", line
        assert (await client.cmd(command))[:3] == code


@pytest.mark.asyncio
async def test_lines_that_are_not_commands_share_one_limit():
    """Unknown verbs, empty lines, bytes that are not ASCII: five in all."""
    async with listening() as port, connected(port) as client:
        assert await client.cmd(b"BOGUS") == b"500 5.5.2 Error: command not recognized\r\n"
        assert await client.cmd(b"") == b"500 Error: bad syntax\r\n"
        assert await client.cmd(b"\xff\xfe") == b"500 Error: bad syntax\r\n"
        assert await client.cmd(b" EHLO x") == b"500 Error: bad syntax\r\n"
        assert await client.cmd(b"") == b"502 5.5.1 Too many unrecognized commands, goodbye.\r\n"
        assert await client.closed()


@pytest.mark.asyncio
async def test_help_says_nothing_about_the_server():
    async with listening() as port, connected(port) as client:
        assert (
            await client.cmd(b"HELP")
            == b"214 2.0.0 See https://www.rfc-editor.org/rfc/rfc5321\r\n"
        )


@pytest.mark.asyncio
async def test_a_command_ending_in_a_bare_lf_is_read_as_a_command():
    """Postfix does the same. Only inside DATA does a bare LF need care."""
    async with listening() as port, connected(port) as client:
        await client.send(b"EHLO mx.sender.example\n")
        assert (await client.reply()).endswith(b"250 HELP\r\n")
        await client.send(b"NOOP\n")
        assert await client.reply() == b"250 OK\r\n"


@pytest.mark.asyncio
async def test_rset_ends_the_transaction():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        assert await client.cmd(b"RSET") == b"250 OK\r\n"
        assert await client.cmd(b"DATA") == b"503 Error: need RCPT command\r\n"
        assert await client.cmd(RCPT) == b"503 Error: need MAIL command\r\n"
        # And a new one can start.
        assert (await client.cmd(MAIL))[:3] == b"250"
    assert not mda.delivered


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting", [EHLO, HELO])
async def test_a_new_greeting_ends_the_transaction(greeting):
    async with listening() as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        assert (await client.cmd(greeting))[:3] == b"250"
        assert await client.cmd(b"DATA") == b"503 Error: need RCPT command\r\n"


@pytest.mark.asyncio
async def test_smtputf8_is_refused_when_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_SMTPUTF8", False)
    async with listening() as port, connected(port) as client:
        assert b"SMTPUTF8" not in await client.cmd(EHLO)
        assert await client.cmd(MAIL + b" SMTPUTF8") == b"501 Error: SMTPUTF8 disabled\r\n"
        assert (
            await client.cmd("MAIL FROM:<é@sender.example>".encode())
            == b"500 Error: strict ASCII mode\r\n"
        )
        assert (await client.cmd(MAIL))[:3] == b"250"


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [settings.PYMTA_MAX_LINE_LENGTH + 10, 400_000])
async def test_a_command_line_longer_than_the_read_buffer_is_refused_once(length, caplog):
    """The line is skipped to its end, answered once, and the session goes on."""
    async with listening() as port:
        with capture_all(caplog):
            async with connected(port) as client:
                await client.cmd(EHLO)
                await client.send(b"MAIL FROM:<" + b"a" * length + b"@sender.example>\r\n")
                assert await client.reply() == b"500 Command line too long\r\n"
                assert await client.cmd(b"NOOP") == b"250 OK\r\n"
                assert (await client.cmd(MAIL))[:3] == b"250"
            rejects = await records(caplog, "reject")
    assert [r.reason for r in rejects] == ["line_too_long"]


@pytest.mark.asyncio
async def test_a_command_line_over_512_octets_is_refused():
    async with listening() as port, connected(port) as client:
        await client.cmd(EHLO)
        assert await client.cmd(b"NOOP " + b"x" * 508) == b"500 Command line too long\r\n"
        assert await client.cmd(b"NOOP " + b"x" * 507) == b"250 OK\r\n"


# ---------------------------------------------------------------------------
# STARTTLS
# ---------------------------------------------------------------------------

_CERT = os.environ.get("PYMTA_TLS_CERT_FILE", "")
_KEY = os.environ.get("PYMTA_TLS_KEY_FILE", "")


def _tls_contexts():
    if not (os.path.exists(_CERT) and os.path.exists(_KEY)):
        pytest.skip("the dev image's self-signed certificate is not available")
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(_CERT, _KEY)
    client = ssl.create_default_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    return server, client


@pytest.mark.asyncio
async def test_starttls_takes_no_argument():
    server_ctx, _ = _tls_contexts()
    async with listening(tls_context=server_ctx) as port, connected(port) as client:
        assert b"STARTTLS" in await client.cmd(EHLO)
        assert await client.cmd(b"STARTTLS now") == b"501 Syntax: STARTTLS\r\n"
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"


@pytest.mark.asyncio
async def test_starttls_forgets_the_greeting_and_the_transaction():
    """RFC 3207 section 4.2: what was learnt in clear is discarded."""
    server_ctx, client_ctx = _tls_contexts()
    mda = ScriptedMDA()
    async with listening(mda, tls_context=server_ctx) as port, connected(port) as client:
        await client.cmd(EHLO)
        assert (await client.starttls(client_ctx))[:3] == b"220"
        assert await client.cmd(MAIL) == b"503 Error: send HELO first\r\n"
        ehlo = await client.cmd(b"EHLO second.sender.example")
        assert b"STARTTLS" not in ehlo
        await client.cmd(MAIL)
        await client.cmd(RCPT)
        assert (await client.data())[:3] == b"250"
    assert mda.delivered[0]["client_helo"] == "second.sender.example"
    assert mda.delivered[0]["transport"]["protocol"] == "ESMTPS"


@pytest.mark.asyncio
async def test_a_peer_that_leaves_during_the_handshake_is_not_our_fault(caplog):
    server_ctx, _ = _tls_contexts()
    async with listening(tls_context=server_ctx) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                await client.cmd(EHLO)
                assert (await client.cmd(b"STARTTLS"))[:3] == b"220"
            closed = await records(caplog, "session_closed")
    assert len(closed) == 1
    assert closed[0].tls is False
    faults = [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert not faults, [r.getMessage() for r in faults]


# ---------------------------------------------------------------------------
# A connection that goes away while a command is running.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_peer_that_closes_its_side_after_the_message_still_gets_its_answer():
    """A sender may shut down its writing side right after the final dot and
    wait for the reply. Dropping it there cancelled the delivery it was
    waiting to hear about, and it sent the message again."""
    released = asyncio.Event()

    async def slow(**_):
        await released.wait()
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)

    mda = ScriptedMDA(delivers=[slow])
    async with listening(mda) as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        await client.cmd(b"DATA")
        await client.send(b"Subject: x\r\n\r\nbody\r\n.\r\n")
        client.writer.write_eof()
        await asyncio.sleep(0.1)
        released.set()
        assert (await client.reply())[:3] == b"250"
        # Nothing more can come from this peer: the session is over.
        assert await client.closed()
    assert len(mda.delivered) == 1


@pytest.mark.asyncio
async def test_commands_sent_just_before_closing_are_answered():
    async with listening() as port, connected(port) as client:
        await client.cmd(EHLO)
        await client.send(b"NOOP\r\nQUIT\r\n")
        client.writer.write_eof()
        assert await client.reply() == b"250 OK\r\n"
        assert await client.reply() == b"221 Bye\r\n"
        assert await client.closed()


@pytest.mark.asyncio
async def test_a_message_cut_short_by_the_peer_closing_is_not_delivered():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        await client.cmd(b"DATA")
        await client.send(b"Subject: x\r\n\r\nhalf a bo")
        client.writer.write_eof()
        assert await client.closed()
    assert not mda.delivered


@pytest.mark.asyncio
async def test_a_delivery_in_flight_is_cancelled_when_the_peer_resets(caplog):
    """The MDA call does not outlive a connection that is gone for good."""
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hang(**_):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    mda = ScriptedMDA(delivers=[hang])
    async with listening(mda) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                for line in (EHLO, MAIL, RCPT):
                    await client.cmd(line)
                await client.cmd(b"DATA")
                await client.send(b"Subject: x\r\n\r\nbody\r\n.\r\n")
                await asyncio.wait_for(started.wait(), timeout=5)
                # Linger 0: closing sends a RST where it would send a FIN.
                client.writer.get_extra_info("socket").setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                client.writer.transport.abort()
            await asyncio.wait_for(cancelled.wait(), timeout=5)
            closed = await records(caplog, "session_closed")
    assert len(closed) == 1
    # The message was never answered: DATA is counted as attempted, not done.
    assert "data=0/1" in closed[0].commands
    assert closed[0].recipients == "bob@example.com"
    faults = [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert not faults, [r.getMessage() for r in faults]


# ---------------------------------------------------------------------------
# Failures of ours.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_exception_in_a_command_is_a_421_and_says_nothing_of_itself(caplog):
    async def explode(**_):
        raise RuntimeError("secret-internal-detail")

    mda = ScriptedMDA(delivers=[explode])
    async with listening(mda) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                for line in (EHLO, MAIL, RCPT):
                    await client.cmd(line)
                reply = await client.data()
                assert reply == b"421 4.3.0 Internal error, please try again later\r\n"
                assert await client.closed()
            errors = await records(caplog, "handler_error")
            closed = await records(caplog, "session_closed")
    assert len(errors) == 1
    assert errors[0].levelname == "ERROR"
    assert errors[0].sender == "alice@sender.example"
    assert errors[0].exc_info is not None
    assert closed[0].closed_by == "internal_error"


@pytest.mark.asyncio
async def test_a_check_refused_outright_by_the_mda_is_a_permanent_refusal(caplog):
    """No status maps to this today; the branch is where one would land."""
    refused = MDAResult(ok=False, temp_fail=False, payload={}, status_code=403)
    mda = ScriptedMDA(checks=[refused])
    async with listening(mda) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                await client.cmd(EHLO)
                await client.cmd(MAIL)
                assert await client.cmd(RCPT) == b"550 5.1.1 Recipient verification failed\r\n"
            rejects = await records(caplog, "reject")
    assert [(r.reason, r.status, r.recipient) for r in rejects] == [
        ("mda_verification_failed", 403, "bob@example.com")
    ]
