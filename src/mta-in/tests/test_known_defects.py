"""Defects found in other SMTP servers, replayed against this one.

Each test names where the lesson comes from. They are here because the same
class of bug is possible in a queue-less server that speaks SMTP itself,
whether or not it was ever present in this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import resource
import ssl
import tracemalloc

import pytest

from pymta import metrics, settings
from pymta.controller import build_smtp_kwargs
from pymta.handler import InboundHandler
from pymta.limits import IPGate
from pymta.smtp_protocol import SMTPConnection

from tests.harness import LOOPBACK_PROXIES
from tests.test_connection import (
    _BODY,
    _AcceptingMDA,
    _dialogue,
    _inbound_listener,
    _read_reply,
    _session_summaries,
    _talk,
)

_CERT, _KEY = "/certs/pymta-test.crt", "/certs/pymta-test.key"
_OUR_FAULTS = {"handler_error", "session_error", "unhandled_error"}


def _faults(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage() in _OUR_FAULTS]


async def _tls_listener(loop, mda):
    if not (os.path.exists(_CERT) and os.path.exists(_KEY)):
        pytest.skip("the dev image's self-signed certificate is not available")
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(_CERT, _KEY)
    kwargs = build_smtp_kwargs(tls_context=server_ctx)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=IPGate(max_total=0), **kwargs),
        host="127.0.0.1",
        port=0,
    )
    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    return server, server.sockets[0].getsockname()[1], client_ctx


async def _command(reader, writer, line: bytes) -> bytes:
    writer.write(line)
    await writer.drain()
    return await _read_reply(reader)


async def _close(server, writer=None):
    if writer is not None:
        writer.close()
        with contextlib.suppress(ConnectionError, ssl.SSLError):
            await writer.wait_closed()
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# STARTTLS.
#
# aiosmtpd CVE-2024-34083, Postfix CVE-2011-0411, the "NO STARTTLS" paper:
# plaintext sent behind STARTTLS in the same segment was executed after the
# handshake as if it had arrived encrypted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plaintext_sent_behind_starttls_is_not_executed_inside_tls():
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port, _ = await _tls_listener(loop, mda)
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        await _command(reader, writer, b"EHLO mx.sender.example\r\n")
        # One segment: the command, then what an attacker on the path injects.
        writer.write(b"STARTTLS\r\nEHLO injected.example\r\nMAIL FROM:<inj@evil.example>\r\n")
        await writer.drain()
        reply = await _read_reply(reader)
        rest = await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        await _close(server, writer)

    # No handshake at all: the injected commands are neither run in clear
    # nor carried into TLS.
    assert reply.startswith(b"554 5.5.0"), reply
    assert rest == b""
    assert mda.delivered == []


# ---------------------------------------------------------------------------
# A peer that does not wait for its replies.
#
# Postfix smtpd_forbid_unauth_pipelining ("554 5.5.0 Error: SMTP protocol
# synchronization"), postscreen's pregreet test, Sendmail 8.18.1. It is how
# smuggled transactions, HTTP requests aimed at port 25 (ALPACA) and most
# spam scripts look on the wire.
# ---------------------------------------------------------------------------


async def _burst(port, data: bytes, *, greeted: bool):
    """Send ``data`` in one write. Returns everything the server answers."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        banner = await reader.readline() if greeted else b""
        writer.write(data)
        await writer.drain()
        return banner + await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("greeted", "data"),
    [
        # Before the greeting.
        (False, b"EHLO mx.sender.example\r\n"),
        (False, b"GET / HTTP/1.1\r\nHost: mx.example\r\n\r\n"),
        # Behind EHLO.
        (True, b"EHLO mx.sender.example\r\nMAIL FROM:<a@sender.example>\r\n"),
        # The body, before the 354 that asks for it.
        (
            True,
            b"EHLO mx.sender.example\r\n",
        ),
    ],
)
async def test_a_peer_that_talks_out_of_turn_is_refused(caplog, greeted, data):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        with caplog.at_level(logging.INFO):
            if data == b"EHLO mx.sender.example\r\n" and greeted:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                await reader.readline()
                for line in (
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<a@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com>\r\n",
                ):
                    await _command(reader, writer, line)
                writer.write(b"DATA\r\nSubject: x\r\n\r\nbody\r\n.\r\n")
                await writer.drain()
                answer = await asyncio.wait_for(reader.read(), timeout=5)
                writer.close()
            else:
                answer = await _burst(port, data, greeted=greeted)
            (summary,) = await _session_summaries(caplog)
    finally:
        await _close(server)

    last = answer.splitlines()[-1]
    assert last == b"554 5.5.0 Error: SMTP protocol synchronization", answer
    # One refusal, then the connection is closed: not a reply per line.
    assert answer.count(b"554") == 1 and b"250 2.1.0" not in answer
    assert mda.delivered == []
    (reject,) = [r for r in caplog.records if r.getMessage() == "reject"]
    assert (reject.reason, reject.code, reject.levelno) == (
        "protocol_synchronization",
        554,
        logging.INFO,
    )
    assert reject.client_ip == "127.0.0.1"
    assert summary.closed_by == "server_closed"
    assert not _faults(caplog)


@pytest.mark.asyncio
async def test_commands_that_may_be_grouped_still_are():
    """MAIL and RCPT in one segment: what a pipelining sender does, and what
    Postfix accepts. Only the commands that must end a group are checked."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        await _command(reader, writer, b"EHLO mx.sender.example\r\n")
        writer.write(
            b"MAIL FROM:<alice@sender.example>\r\nRCPT TO:<bob@example.com>\r\n"
            b"RCPT TO:<carol@example.com>\r\n"
        )
        await writer.drain()
        replies = [await _read_reply(reader) for _ in range(3)]
        assert (await _command(reader, writer, b"DATA\r\n")).startswith(b"354")
        done = await _command(reader, writer, b"Subject: x\r\n\r\nb\r\n.\r\n")
    finally:
        await _close(server, writer)

    assert [r[:3] for r in replies] == [b"250"] * 3, replies
    assert done.startswith(b"250"), done
    assert len(mda.delivered) == 1


# ---------------------------------------------------------------------------
# Memory held for a message.
#
# Keeping one object per line (about 60 bytes each on top of the line) makes
# a body of empty lines cost about thirty times its size. The protocol
# collects lines in blocks instead.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_body_of_empty_lines_costs_its_size_not_thirty_times_it(monkeypatch):
    # 256,000 lines: enough for the per-line cost to dominate, and still fast
    # with allocation tracing on.
    size = 512 * 1024
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", size + 1024)
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    tracemalloc.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        writer.write(b"\r\n" * (size // 2))
        reply = await _command(reader, writer, b".\r\n")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        await _close(server, writer)

    assert reply.startswith(b"250"), reply
    assert len(mda.delivered[0]["message"]) == size
    # The message, its copy at the join, the client's own copy and slack.
    assert peak < 5 * size, f"peak {peak / size:.1f}x the message"


@pytest.mark.asyncio
async def test_a_transaction_opened_before_starttls_does_not_continue_inside_tls():
    """RFC 3207 section 4.2: the server discards what it learned before TLS."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port, client_ctx = await _tls_listener(loop, mda)
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        await _command(reader, writer, b"EHLO mx.sender.example\r\n")
        assert (await _command(reader, writer, b"MAIL FROM:<a@sender.example>\r\n")).startswith(
            b"250"
        )
        assert (await _command(reader, writer, b"STARTTLS\r\n")).startswith(b"220")
        await writer.start_tls(client_ctx)
        no_greeting = await _command(reader, writer, b"MAIL FROM:<a@sender.example>\r\n")
        ehlo = []
        writer.write(b"EHLO mx.sender.example\r\n")
        await writer.drain()
        while True:
            ehlo.append(await asyncio.wait_for(reader.readline(), timeout=5))
            if ehlo[-1][3:4] != b"-":
                break
        no_sender = await _command(reader, writer, b"RCPT TO:<bob@example.com>\r\n")
        with_argument = await _command(reader, writer, b"STARTTLS now\r\n")
    finally:
        await _close(server, writer)

    assert no_greeting.startswith(b"503"), no_greeting
    assert no_sender.startswith(b"503"), no_sender
    assert not any(b"STARTTLS" in line for line in ehlo), ehlo
    assert with_argument[:1] == b"5", with_argument


@pytest.mark.asyncio
async def test_with_tls_required_nothing_is_accepted_in_clear(monkeypatch, caplog):
    """PYMTA_REQUIRE_TLS: 530 until STARTTLS, then a normal session."""
    monkeypatch.setattr(settings, "PYMTA_REQUIRE_TLS", True)
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port, client_ctx = await _tls_listener(loop, mda)
    writer = None
    try:
        with caplog.at_level(logging.INFO):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            in_clear = {}
            for line in (
                b"EHLO mx.sender.example\r\n",
                b"NOOP\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                b"DATA\r\n",
                b"HELO mx.sender.example\r\n",
            ):
                in_clear[line.split()[0]] = (await _command(reader, writer, line))[:3]
            assert (await _command(reader, writer, b"STARTTLS\r\n")).startswith(b"220")
            await writer.start_tls(client_ctx)
            encrypted = [
                (await _command(reader, writer, line))[:3]
                for line in (
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com>\r\n",
                    b"DATA\r\n",
                    b"Subject: x\r\n\r\nbody\r\n.\r\n",
                )
            ]
    finally:
        await _close(server, writer)

    assert in_clear == {
        b"EHLO": b"250",
        b"NOOP": b"250",
        b"MAIL": b"530",
        b"RCPT": b"530",
        b"DATA": b"530",
        b"HELO": b"530",
    }
    assert encrypted == [b"250", b"250", b"250", b"354", b"250"]
    assert len(mda.delivered) == 1
    assert mda.delivered[0]["transport"]["protocol"] == "ESMTPS"
    refused = [r for r in caplog.records if r.getMessage() == "reject"]
    assert {r.reason for r in refused} == {"tls_required"}
    assert refused[0].sender == "alice@sender.example"


@pytest.mark.asyncio
async def test_without_tls_required_a_session_in_clear_delivers(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_REQUIRE_TLS", False)
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port, _ = await _tls_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                *_BODY,
            ],
        )
    finally:
        await _close(server)

    assert replies[-2].startswith(b"250"), replies
    assert mda.delivered[0]["transport"]["protocol"] == "ESMTP"


@pytest.mark.asyncio
async def test_plaintext_in_place_of_the_handshake_closes_the_connection(caplog):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port, _ = await _tls_listener(loop, mda)
    writer = None
    try:
        with caplog.at_level(logging.INFO):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            await _command(reader, writer, b"EHLO mx.sender.example\r\n")
            assert (await _command(reader, writer, b"STARTTLS\r\n")).startswith(b"220")
            writer.write(b"MAIL FROM:<a@sender.example>\r\n")
            await writer.drain()
            rest = await asyncio.wait_for(reader.read(), timeout=10)
            (summary,) = await _session_summaries(caplog)
    finally:
        await _close(server, writer)

    # Closed without a word in clear, and not reported as a fault of ours.
    assert b"250" not in rest
    assert summary.closed_by == "tls_failed"
    assert not _faults(caplog)


# ---------------------------------------------------------------------------
# The end of DATA, and what follows a refused body.
#
# SEC Consult's SMTP smuggling variants; Exim CVE-2020-28019 and Mailpit
# GHSA-fpxj-m5q8-fphw, where a body refused for its size left the server
# reading the rest of it as commands.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inside",
    [
        b"\n.\n",
        b"\n.\r\n",
        b"\r.\r",
        b"\r.\n",
        b"\r\n.\r",
        b"\r\n\x00.\r\n",
        b"\r\n.\x00\r\n",
        b"\r\n..\r\n",
        b"\n..\n",
    ],
)
async def test_no_lookalike_ends_data(inside):
    """One message in, one message out, and never a second transaction."""
    mda = _AcceptingMDA()
    smuggled = b"MAIL FROM:<evil@evil.example>\r\nRCPT TO:<bob@example.com>\r\nDATA\r\nx"
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                b"DATA\r\n",
                b"Subject: x\r\n\r\nbefore" + inside + smuggled + b"\r\n.\r\n",
            ],
        )
    finally:
        await _close(server)

    # 250 for a clean message, 554 for the ones carrying a NUL. Never two.
    assert replies[-2][:3] in (b"250", b"554"), replies
    assert len(mda.delivered) <= 1
    for delivery in mda.delivered:
        assert delivery["sender"] == "alice@sender.example"
        message = delivery["message"]
        assert b"evil@evil.example" in message, "the tail is content, not commands"
        # A line holding a single dot can legitimately remain (the sender's
        # stuffed ".." line). It is content here; anything that ever relays
        # these bytes over SMTP or LMTP has to dot-stuff them again
        # (Haraka GHSA-rp4q-8m6x-c43c).
        assert message.count(b"\n") == message.count(b"\r\n") == message.count(b"\r")


@pytest.mark.asyncio
async def test_dot_stuffing_is_undone_once_per_line():
    """RFC 5321 section 4.5.2, in our own DATA loop."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                b"DATA\r\n",
                b"Subject: x\r\n\r\n..one dot\r\n...two dots\r\n..\r\nno.dot.\r\n.\r\n",
            ],
        )
    finally:
        await _close(server)

    (delivery,) = mda.delivered
    assert delivery["message"] == b"Subject: x\r\n\r\n.one dot\r\n..two dots\r\n.\r\nno.dot.\r\n"


@pytest.mark.asyncio
async def test_the_end_of_an_overlong_line_is_not_the_end_of_data(monkeypatch):
    """A line over the limit arrives in pieces. If its last piece is exactly
    ``.<CRLF>`` it is still the end of that line, not the terminator: taking
    it for one would turn the rest of the body into commands."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        # Two bytes over the limit: the reader takes all but the last byte as
        # the first piece, so the dot is what is left when the CRLF arrives.
        writer.write(b"A" * (settings.PYMTA_MAX_LINE_LENGTH + 1) + b".")
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.write(b"\r\n")
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.write(b"MAIL FROM:<evil@evil.example>\r\n.\r\nQUIT\r\n")
        await writer.drain()
        rest = await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        await _close(server, writer)

    # One refusal for the whole message, then the QUIT.
    assert [line[:3] for line in rest.splitlines()] == [b"500", b"221"], rest
    assert mda.delivered == []


@pytest.mark.asyncio
async def test_a_lone_dot_right_after_354_is_an_empty_message():
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                b"DATA\r\n",
                b".\r\n",
            ],
        )
    finally:
        await _close(server)

    assert replies[-2].startswith(b"250"), replies
    assert [d["message"] for d in mda.delivered] == [b""]


@pytest.mark.asyncio
async def test_the_rest_of_an_oversize_body_is_not_read_as_commands(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 2000)
    mda = _AcceptingMDA()
    hidden = (
        b"\r\nRSET\r\nMAIL FROM:<evil@evil.example>\r\nRCPT TO:<bob@example.com>\r\n"
        b"DATA\r\nSubject: smuggled\r\n\r\nx"
    )
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com>\r\n",
                    b"DATA\r\n",
                    b"Subject: x\r\n\r\n" + b"y" * 3000 + hidden + b"\r\n.\r\n",
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        await _close(server)

    # One 552 for the whole body, then the QUIT: nothing in between.
    assert [r[:3] for r in replies[-2:]] == [b"552", b"221"], replies
    assert mda.delivered == []
    assert summary.commands == "ehlo=1 mail=1 rcpt=1 data=0/1 quit=1"


@pytest.mark.asyncio
async def test_a_body_line_without_an_end_is_not_buffered_without_limit(monkeypatch):
    """A line that never ends is cut once it outgrows the message size limit,
    not kept until a CRLF that will not come. Mailpit GHSA-r553-m4fv-5v97,
    qmail CVE-2005-1513: the same unbounded line.
    """
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 100_000)
    sent, chunk = 0, b"A" * 65536
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, _AcceptingMDA())
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    cut = False
    try:
        await reader.readline()
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        # 64 MiB against a 100 kB limit.
        try:
            for _ in range(1024):
                writer.write(chunk)
                await asyncio.wait_for(writer.drain(), timeout=10)
                sent += len(chunk)
        except ConnectionError, TimeoutError:
            cut = True
        reply = b""
        with contextlib.suppress(ConnectionError, TimeoutError):
            reply = await asyncio.wait_for(reader.read(200), timeout=5)
    finally:
        await _close(server, writer)

    grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    # Hung up on long before 64 MiB went through. The 552 that says why is
    # sent, but a peer still writing at that moment may lose it to the reset.
    assert cut and sent < 16 * 1024 * 1024, (reply, cut, sent)
    assert reply == b"" or reply.startswith(b"552"), reply
    assert grown < 32 * 1024, f"resident memory grew by {grown} kB"


@pytest.mark.asyncio
async def test_an_unterminated_body_line_over_the_limit_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 100_000)
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, _AcceptingMDA())
    try:
        with caplog.at_level(logging.INFO):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in (
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com>\r\n",
                b"DATA\r\n",
            ):
                await _command(reader, writer, line)
            writer.write(b"A" * 300_000)
            await writer.drain()
            # The 552 is sent, but the server hangs up with part of the line
            # still unread, and the reset that follows can beat the reply to
            # the peer. The log line is what cannot be lost.
            reply = b""
            with contextlib.suppress(ConnectionError):
                reply = await asyncio.wait_for(reader.read(), timeout=10)
            (summary,) = await _session_summaries(caplog)
    finally:
        await _close(server, writer)

    assert reply == b"" or reply.startswith(b"552 5.3.4"), reply
    (reject,) = [r for r in caplog.records if r.getMessage() == "reject"]
    assert (reject.verb, reject.reason, reject.code) == ("data", "oversize_message", 552)
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"
    assert summary.closed_by == "server_closed"
    assert not _faults(caplog)


# ---------------------------------------------------------------------------
# Arguments parsed by the protocol before any handler hook.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "code"),
    [
        ("0", b"250"),
        ("5", b"250"),
        ("00000000001", b"250"),
        ("999999999999999999999999999999", b"552"),
        ("-1", b"501"),
        ("+5", b"501"),
        ("1e3", b"501"),
        ("0x10", b"501"),
        # str.isdigit() is true for these and int() raises on the first.
        ("²", b"501"),
        ("١٢٣", b"501"),
    ],
)
async def test_size_is_a_plain_decimal_number_or_a_syntax_error(caplog, size, code):
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, _AcceptingMDA())
    try:
        with caplog.at_level(logging.INFO):
            replies, _ = await _talk(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    f"MAIL FROM:<alice@sender.example> SIZE={size}\r\n".encode(),
                ],
            )
            await _session_summaries(caplog)
    finally:
        await _close(server)

    assert replies[-1].startswith(code), replies
    assert not _faults(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "<" + "(" * 400,
        "<" + "(" * 400 + ")" * 400 + "a@b.example>",
        "<" + '"' * 401 + "@b.example>",
        "<" + "<" * 400 + "a@b.example" + ">" * 400,
        "<" + "@a.example," * 40 + ":a@b.example>",
        "<a@" + "[" * 400 + ">",
        "alice@example.org<bob@example.org>",
        "alice@example.com(bob@example.org",
        "<a@b.example> (comment)",
        "< a@b.example >",
        "<a@b@c.example>",
        "<a@[IPv6:::1]>",
        "<x!a%b@c.example>",
        "<>",
        "",
    ],
)
async def test_no_address_makes_the_parser_raise(caplog, path):
    """aiosmtpd #563: envelope addresses went through the stdlib header
    parser, which has raised RecursionError on nested input (CVE-2023-36632).
    Ours are split by ``pymta.command``. Whatever the verdict, it is an SMTP
    reply and not an exception."""
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, _AcceptingMDA())
    try:
        with caplog.at_level(logging.INFO):
            replies, _ = await _talk(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    f"MAIL FROM:{path}\r\n".encode(),
                    b"RSET\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    f"RCPT TO:{path}\r\n".encode(),
                ],
            )
            await _session_summaries(caplog)
    finally:
        await _close(server)

    assert not _faults(caplog)
    assert not any(reply.startswith(b"421") for reply in replies), replies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line",
    [
        b"RCPT TO:<a\rb@example.com>\r\n",
        b"RCPT TO:<a\x00b@example.com>\r\n",
        b"RCPT TO:<a\x1b[2Jb@example.com>\r\n",
        b"RCPT TO:<a\x7fb@example.com>\r\n",
        b"RCPT TO:<a\tb@example.com>\r\n",
        b'RCPT TO:<"a\rX-Injected: 1"@example.com>\r\n',
        b"RCPT TO:<../../admin@example.com>\r\n",
        b"RCPT TO:<a?x=1&y=2#z@example.com>\r\n",
        b"RCPT TO:<$(id)@example.com>\r\n",
    ],
)
async def test_control_characters_in_a_command_reach_nothing(caplog, line):
    """OpenSMTPD CVE-2020-7247 and Zimbra CVE-2024-45519 put the envelope in a
    shell; here the equivalent sinks are the MDA request and the log."""
    mda = _AcceptingMDA()
    checked = []
    real = mda.check_recipient

    async def record(address, session=None):
        checked.append(address)
        return await real(address, session=session)

    mda.check_recipient = record
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(
                port,
                [b"EHLO mx.sender.example\r\n", b"MAIL FROM:<alice@sender.example>\r\n", line],
            )
            await _session_summaries(caplog)
    finally:
        await _close(server)

    reply = replies[-2]
    # An address either reaches the MDA exactly as a clean string, or not at all.
    for address in checked:
        assert not set(address) & set('\r\n\x00\x1b\x7f\t" '), address
    if reply.startswith(b"250"):
        assert checked
    else:
        assert reply[:1] == b"5", reply
    # And the reply quotes nothing the peer sent.
    assert b"X-Injected" not in reply and b"\x1b" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("line", "code", "sender"),
    [
        # Whitespace outside quotes. Not repaired into "a b"@..., quotes
        # nobody sent.
        (b"MAIL FROM:<al ice@sender.example>\r\n", b"501", None),
        (b"MAIL FROM:<al\tice@sender.example>\r\n", b"501", None),
        (b"MAIL FROM:<alice@sender .example>\r\n", b"501", None),
        (b'MAIL FROM:<"al"  ice@sender.example>\r\n', b"501", None),
        # Quoted by the peer: valid.
        (b'MAIL FROM:<"al ice"@sender.example>\r\n', b"250", '"al ice"@sender.example'),
        (
            b'MAIL FROM:<"al\\" ice"@sender.example> SIZE=10\r\n',
            b"250",
            '"al\\" ice"@sender.example',
        ),
        (b'MAIL FROM:<"a>b c"@sender.example>\r\n', b"501", None),
        # Spaces just inside the brackets, tolerated as before.
        (b"MAIL FROM:< alice@sender.example >\r\n", b"250", "alice@sender.example"),
        (b"MAIL FROM: <alice@sender.example>\r\n", b"250", "alice@sender.example"),
    ],
)
async def test_a_space_in_a_sender_counts_only_inside_quotes_the_peer_sent(line, code, sender):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [b"EHLO mx.sender.example\r\n", line, b"RCPT TO:<bob@example.com>\r\n", *_BODY],
        )
    finally:
        await _close(server)

    mail_reply = replies[-5]
    assert mail_reply.startswith(code), replies
    assert [d["sender"] for d in mda.delivered] == ([sender] if sender else [])


# ---------------------------------------------------------------------------
# Replies never repeat what the peer sent.
#
# ALPACA (cross-protocol attacks): a browser made to POST to port 25 gets its
# own bytes back in an error. Postfix has smtpd_forbidden_commands for this.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines",
    [
        [b"GET /<script>alert(1)</script> HTTP/1.1\r\n", b"Host: mx.example\r\n", b"\r\n"],
        [b"<script>alert(1)</script>\r\n"],
        [b"POST / HTTP/1.1\r\n", b"Content-Type: text/html\r\n"],
        [b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03\r\n"],
    ],
)
async def test_an_error_reply_does_not_echo_the_command(lines):
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, _AcceptingMDA())
    try:
        replies, _ = await _talk(port, lines)
    finally:
        await _close(server)

    for reply in replies[1:]:
        assert reply[:1] == b"5", reply
        for echoed in (b"script", b"GET", b"POST", b"HTTP", b"HOST", b"CONTENT", b"\x16"):
            assert echoed not in reply.upper().replace(b"SMTP", b""), reply


# ---------------------------------------------------------------------------
# Transaction state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", [b"RSET\r\n", b"EHLO mx.sender.example\r\n"])
async def test_nothing_of_an_abandoned_transaction_reaches_the_next(reset):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<first@sender.example>\r\n",
                b"RCPT TO:<r1@example.com>\r\n",
                reset,
                b"MAIL FROM:<second@sender.example>\r\n",
                b"RCPT TO:<r2@example.com>\r\n",
                *_BODY,
            ],
        )
    finally:
        await _close(server)

    (delivery,) = mda.delivered
    assert delivery["sender"] == "second@sender.example"
    assert delivery["original_recipients"] == ["r2@example.com"]


@pytest.mark.asyncio
async def test_after_a_refused_message_the_next_one_starts_clean(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 2000)
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<first@sender.example>\r\n",
                b"RCPT TO:<r1@example.com>\r\n",
                b"DATA\r\n",
                b"Subject: x\r\n\r\n" + b"y" * 3000 + b"\r\n.\r\n",
                # The refused transaction is over: no recipients left.
                b"DATA\r\n",
                b"MAIL FROM:<second@sender.example>\r\n",
                b"RCPT TO:<r2@example.com>\r\n",
                *_BODY,
            ],
        )
    finally:
        await _close(server)

    assert [r[:3] for r in replies[-7:-1]] == [b"552", b"503", b"250", b"250", b"354", b"250"]
    (delivery,) = mda.delivered
    assert delivery["sender"] == "second@sender.example"
    assert delivery["original_recipients"] == ["r2@example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cut_at", [0, 5, 14, 20])
async def test_a_message_cut_before_its_end_is_never_delivered(cut_at):
    """Postfix: "lost connection after DATA" discards. A truncated body that
    was stored would be a corrupted message with a 250 nobody received."""
    mda = _AcceptingMDA()
    body = b"Subject: x\r\n\r\nbody body\r\n.\r\n"
    assert cut_at < len(body) - 1
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        writer.write(body[:cut_at])
        await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        await asyncio.sleep(0.2)
    finally:
        await _close(server)

    assert mda.delivered == []


# ---------------------------------------------------------------------------
# PROXY protocol.
#
# NDSS 2025 measurement study, go-guerrilla CVE-2025-31135 and Puma
# CVE-2026-47737 (a second header accepted later in the session),
# go-proxyproto CVE-2021-23351 (unbounded v1 line).
# ---------------------------------------------------------------------------

_V2 = b"\r\n\r\n\x00\r\nQUIT\n"


async def _proxied(loop, monkeypatch, mda):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 1)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "client_ip"),
    [
        (b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n", "203.0.113.9"),
        (b"PROXY TCP6 2001:db8::9 2001:db8::1 4321 25\r\n", "2001:db8::9"),
        # No address given: the sender is unknown, not the balancer.
        (b"PROXY UNKNOWN\r\n", None),
        (_V2 + b"\x20\x00\x00\x00", None),
        (
            _V2
            + b"\x21\x11\x00\x0c"
            + bytes([203, 0, 113, 9, 192, 0, 2, 1])
            + b"\x10\xe1\x00\x19",
            "203.0.113.9",
        ),
    ],
)
async def test_a_valid_proxy_header_names_the_client_or_nobody(monkeypatch, header, client_ip):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _proxied(loop, monkeypatch, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # One byte at a time: a balancer may split the header across segments.
        for byte in header:
            writer.write(bytes([byte]))
            await writer.drain()
        assert (await _read_reply(reader)).startswith(b"220")
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        assert (await _command(reader, writer, b"Subject: x\r\n\r\nb\r\n.\r\n")).startswith(b"250")
    finally:
        await _close(server, writer)

    (delivery,) = mda.delivered
    assert delivery["client_address"] == client_ip


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        b"EHLO mx.sender.example\r\n",
        b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\n",
        b"PROXY TCP4 203.0.113.9 192.0.2.1 65536 25\r\n",
        b"PROXY TCP4 203.0.113.9 192.0.2.1 -1 25\r\n",
        b"PROXY TCP4 2001:db8::9 192.0.2.1 4321 25\r\n",
        b"PROXY TCP4 203.0.113.9/8 192.0.2.1 4321 25\r\n",
        b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25 extra\r\n",
        b"PROXY TCP4 " + b"1" * 200 + b"\r\n",
        b"PROXY " + b"A" * 5000,
        _V2 + b"\x21\x11\x00\x04" + bytes([203, 0, 113, 9]),
        _V2 + b"\x31\x11\x00\x0c" + bytes(12),
        _V2 + b"\x22\x11\x00\x0c" + bytes(12),
        _V2[:6],
        b"",
    ],
)
async def test_a_bad_proxy_header_gets_no_greeting(monkeypatch, caplog, header):
    mda = _AcceptingMDA()
    active = metrics.active_sessions()
    loop = asyncio.get_running_loop()
    server, port = await _proxied(loop, monkeypatch, mda)
    try:
        with caplog.at_level(logging.INFO):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(header)
            with contextlib.suppress(ConnectionError):
                await writer.drain()
            # Closed, by the parser or by the PROXY timeout, without a banner.
            answer = await asyncio.wait_for(reader.read(), timeout=10)
            await asyncio.sleep(0.05)
    finally:
        await _close(server, writer)

    assert not answer.startswith(b"220"), answer
    assert mda.delivered == []
    assert metrics.active_sessions() == active
    # A balancer's health check does this all day: nothing above DEBUG.
    assert not [r for r in caplog.records if r.name.startswith("pymta")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        b"PROXY TCP4 198.51.100.66 192.0.2.1 1 25\r\n",
        b"proxy tcp4 198.51.100.66 192.0.2.1 1 25\r\n",
    ],
)
async def test_a_second_proxy_header_is_just_a_bad_command(monkeypatch, second):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _proxied(loop, monkeypatch, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n")
        await writer.drain()
        assert (await _read_reply(reader)).startswith(b"220")
        await _command(reader, writer, b"EHLO mx.sender.example\r\n")
        refused = await _command(reader, writer, second)
        for line in (
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        await _command(reader, writer, b"Subject: x\r\n\r\nb\r\n.\r\n")
    finally:
        await _close(server, writer)

    assert refused.startswith(b"500"), refused
    (delivery,) = mda.delivered
    assert delivery["client_address"] == "203.0.113.9"


# ---------------------------------------------------------------------------
# Things a strict server must still accept: Postfix and Exim do, on purpose.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines",
    [
        # HELO, lower case, a space after the colon, no brackets.
        [
            b"helo mx_1.sender.example\r\n",
            b"mail from: <alice@sender.example>\r\n",
            b"rcpt to: <bob@example.com>\r\n",
        ],
        # A bare address literal and an IP as greeting.
        [
            b"EHLO [192.0.2.7]\r\n",
            b"MAIL FROM:<alice@sender.example> SIZE=100 BODY=7BIT\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
        ],
        [
            b"EHLO 192.0.2.7\r\n",
            b"MAIL FROM:<> BODY=8BITMIME\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
        ],
        # BATV, SRS and friends in the local-part; an upper-case domain.
        [
            b"EHLO localhost\r\n",
            b"MAIL FROM:<prvs=0123abcdef=alice@Sender.EXAMPLE>\r\n",
            b"RCPT TO:<bob@EXAMPLE.com>\r\n",
        ],
        [
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<SRS0=HHH=TT=orig.example=alice@fwd.example>\r\n",
            b"RCPT TO:<bob+tag/x!y#z'q@example.com>\r\n",
        ],
        # Trailing spaces, and EHLO twice.
        [
            b"EHLO mx.sender.example\r\n",
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>   \r\n",
            b"RCPT TO:<bob@example.com>  \r\n",
        ],
    ],
)
async def test_what_real_senders_do_is_accepted(lines):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(port, [*lines, *_BODY], quit_=False)
    finally:
        await _close(server)

    assert replies[-1].startswith(b"250"), replies
    # The peer left without QUIT after its 250: the message stays delivered.
    assert len(mda.delivered) == 1


@pytest.mark.asyncio
async def test_quit_sent_right_behind_the_message_is_answered():
    """Senders do send ahead here without PIPELINING (Thunderbird, scripts)."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
        ):
            await _command(reader, writer, line)
        writer.write(b"Subject: x\r\n\r\nb\r\n.\r\nQUIT\r\n")
        await writer.drain()
        rest = await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        await _close(server, writer)

    assert [line[:3] for line in rest.splitlines()] == [b"250", b"221"], rest
    assert len(mda.delivered) == 1


@pytest.mark.asyncio
async def test_too_many_recipients_is_a_deferral_and_the_rest_are_delivered(monkeypatch):
    """RFC 5321 4.5.3.1.10: 452, so the sender resends the remainder."""
    monkeypatch.setattr(settings, "PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 2)
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<r1@example.com>\r\n",
                b"RCPT TO:<r2@example.com>\r\n",
                b"RCPT TO:<r3@example.com>\r\n",
                *_BODY,
            ],
        )
    finally:
        await _close(server)

    assert [r[:3] for r in replies[-5:-1]] == [b"250", b"452", b"354", b"250"], replies
    (delivery,) = mda.delivered
    assert delivery["original_recipients"] == ["r1@example.com", "r2@example.com"]
