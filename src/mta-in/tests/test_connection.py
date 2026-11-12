"""Tests of :class:`pymta.smtp_protocol.SMTPConnection` over a real socket.

Timeouts, the forced disconnect behind a 421, the session summary, the gate,
the error budget and the shutdown. Each test runs a listener in-process, with
the settings it needs patched: no Docker stack.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import ssl
import time

import pytest
from prometheus_client import REGISTRY

from pymta import metrics, runtime, settings, smtp_protocol
from pymta import server as server_module
from pymta.controller import build_smtp_kwargs
from pymta.handler import InboundHandler
from pymta.limits import IPGate, LogThrottle
from pymta.logfmt import LogfmtFormatter
from pymta.mda_async import MDAResult
from pymta.session_log import SessionLog
from pymta.settings import _env_networks
from pymta.smtp_protocol import (
    Reply,
    SMTPConnection,
    close_idle_sessions,
    log_abandoned_sessions,
)

from tests.harness import (
    LOOPBACK_PROXIES,
    AcceptAll,
    ScriptedMDA,
    connected,
    listening,
)

_TIMEOUT_COUNTERS = ("pymta_security_rejections_total", "pymta_disconnects_421_total")


# ---------------------------------------------------------------------------
# The 421 that actually hangs up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reply_that_announces_a_disconnect_goes_out_before_it():
    """Closing eagerly would race the reply off the wire and leave the peer
    with a bare TCP reset instead of a reason. And only that reply closes."""
    async with listening(handler=AcceptAll()) as port, connected(port) as client:
        for _ in range(4):
            assert await client.cmd(b"BOGUS") == b"500 5.5.2 Error: command not recognized\r\n"
        assert (
            await client.cmd(b"BOGUS") == b"502 5.5.1 Too many unrecognized commands, goodbye.\r\n"
        )
        assert await client.closed()


# ---------------------------------------------------------------------------
# DATA phase runs on its own budget.
#
# The idle timer is armed when a command is dispatched and not again while it
# runs. Left at PYMTA_COMMAND_TIMEOUT, the whole of DATA (body receive plus
# the MDA call) would be charged to it and cut mid-delivery.
# ---------------------------------------------------------------------------

_TRANSACTION = (
    b"EHLO mx.sender.example",
    b"MAIL FROM:<alice@sender.example>",
    b"RCPT TO:<bob@example.com>",
)


@pytest.mark.asyncio
async def test_a_delivery_may_outlast_the_command_timeout(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 30)

    async def slow(**_):
        await asyncio.sleep(0.9)
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)

    async with listening(ScriptedMDA(delivers=[slow])) as port, connected(port) as client:
        for line in _TRANSACTION:
            await client.cmd(line)
        assert (await client.data())[:3] == b"250"
        # Back on the command timeout once the message is answered.
        started = time.monotonic()
        assert await client.reply() == b"421 4.4.2 Idle timeout, closing connection\r\n"
        assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_a_stalled_data_phase_is_cut_at_the_data_timeout(monkeypatch):
    """PYMTA_DATA_TIMEOUT is the hard edge of the phase, armed as it is. The
    handler's reply reserve comes out of this budget, it does not extend it."""
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 30)
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 0.3)
    mda = ScriptedMDA()
    before = [_counter(name, reason="data_timeout") for name in _TIMEOUT_COUNTERS]
    idle = [_counter(name, reason="command_timeout") for name in _TIMEOUT_COUNTERS]
    async with listening(mda) as port, connected(port) as client:
        for line in _TRANSACTION:
            await client.cmd(line)
        assert (await client.cmd(b"DATA"))[:3] == b"354"
        await client.send(b"Subject: x\r\n")
        started = time.monotonic()
        reply = await client.reply()
        assert reply == b"421 4.4.2 Timeout receiving the message, closing connection\r\n"
        assert time.monotonic() - started < 2
        assert await client.closed()
    assert not mda.delivered
    # Counted as what it is, not as an idle session.
    assert [_counter(name, reason="data_timeout") for name in _TIMEOUT_COUNTERS] == [
        count + 1 for count in before
    ]
    assert [_counter(name, reason="command_timeout") for name in _TIMEOUT_COUNTERS] == idle


@pytest.mark.asyncio
async def test_data_phase_start_is_published_for_the_handler():
    seen: list = []

    class _Recorder(AcceptAll):
        async def handle_DATA(self, server):
            seen.append((server, server.data_phase_started, server.data_phase_budget))
            return Reply("250 2.0.0 OK")

    async with listening(handler=_Recorder()) as port, connected(port) as client:
        for line in _TRANSACTION:
            await client.cmd(line)
        assert (await client.data())[:3] == b"250"

    server, started, budget = seen[0]
    assert started is not None
    assert budget == float(settings.PYMTA_DATA_TIMEOUT)
    # Cleared once DATA is over, so a stale timestamp cannot shrink the
    # budget of a later envelope on the same connection.
    assert server.data_phase_started is None


# ---------------------------------------------------------------------------
# Whole-session deadline.
#
# The one bound a peer cannot push back by staying busy: every other timeout
# is re-armed by activity, so a peer sending one command just under
# PYMTA_COMMAND_TIMEOUT rides command_call_limit for ~39 h on one connection.
# ---------------------------------------------------------------------------


async def _listener(loop, gate=None, **overrides):
    """Start a listener built the way the real server builds one.

    Goes through ``build_smtp_kwargs`` rather than passing ``timeout=`` by
    hand: patch the setting, and the wiring is under test too.
    """
    kwargs = build_smtp_kwargs(tls_context=None)
    kwargs.update(overrides)
    gate = gate or IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    return server, server.sockets[0].getsockname()[1], kwargs


@pytest.mark.asyncio
async def test_idle_command_timeout_closes_the_session(monkeypatch):
    """A peer that connects and says nothing is hung up on, with a reason.

    The integration suite cannot cover this: PYMTA_COMMAND_TIMEOUT is read once
    at startup, so testing it there would mean idling for the configured 120 s.
    Patching the setting and running a listener in-process exercises the same
    chain, setting -> build_smtp_kwargs -> the idle timer -> the 421 it sends.
    """
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    loop = asyncio.get_running_loop()
    server, port, kwargs = await _listener(loop)
    assert kwargs["timeout"] == 0.3, (
        "PYMTA_COMMAND_TIMEOUT is no longer wired into the SMTP timeout kwarg, "
        "so SMTPConnection would silently use its own 300s default"
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        greeting = await asyncio.wait_for(reader.readline(), timeout=5)
        assert greeting.startswith(b"220"), greeting

        # Say nothing. The peer is told why before the hang-up, the way
        # Postfix does and the way the session deadline does.
        rest = await asyncio.wait_for(reader.read(), timeout=10)
        assert b"421" in rest, rest
        assert b"Idle timeout" in rest, rest
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_command_timeout_is_rearmed_by_activity(monkeypatch):
    """Staying active keeps the session alive past one timeout period.

    The counterpart to the test above, and the reason PYMTA_SESSION_TIMEOUT
    exists: this deadline is reset by every accepted command, so it bounds
    silence rather than session length.
    """
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 1.0)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.wait_for(reader.readline(), timeout=5)
        # Four NOOPs at 0.4s spacing spans 1.6s, well past the 1.0s deadline.
        # The absolute slack per interval (0.6s), not the ratio, is what has to
        # survive scheduler jitter on a loaded CI box.
        for _ in range(4):
            await asyncio.sleep(0.4)
            writer.write(b"NOOP\r\n")
            await writer.drain()
            reply = await asyncio.wait_for(reader.readline(), timeout=5)
            assert reply.startswith(b"250"), reply
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


async def _until_421(client, command: bytes, every: float) -> bytes:
    """Keep the session busy with ``command`` until the server says 421."""
    for _ in range(40):
        reply = await client.cmd(command)
        if not reply.startswith(b"250"):
            return reply
        await asyncio.sleep(every)
    return b""


@pytest.mark.asyncio
async def test_a_busy_session_is_closed_at_the_session_deadline(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 0.6)
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 30)
    async with listening(handler=AcceptAll()) as port:
        started = time.monotonic()
        async with connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            # RSET every 0.15 s: never idle, so only the deadline can end it.
            reply = await _until_421(client, b"RSET", 0.15)
            assert reply == b"421 4.4.2 Session too long, closing connection\r\n"
            assert await client.closed()
        assert 0.5 < time.monotonic() - started < 3


@pytest.mark.asyncio
async def test_starttls_does_not_extend_the_session_deadline(monkeypatch):
    """Or a peer gets a fresh budget for the price of one STARTTLS."""
    cert = os.environ.get("PYMTA_TLS_CERT_FILE", "")
    key = os.environ.get("PYMTA_TLS_KEY_FILE", "")
    if not (os.path.exists(cert) and os.path.exists(key)):
        pytest.skip("the dev image's self-signed certificate is not available")
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 2)
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 30)

    async with listening(handler=AcceptAll(), tls_context=server_ctx) as port:
        started = time.monotonic()
        async with connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            await asyncio.sleep(1.2)
            assert (await client.starttls(client_ctx))[:3] == b"220"
            assert await client.reply() == b"421 4.4.2 Session too long, closing connection\r\n"
        # 2 s from the connection, not from the handshake (3.2 s).
        assert time.monotonic() - started < 2.8


@pytest.mark.asyncio
async def test_session_deadline_disabled_by_zero(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 0)
    async with listening(handler=AcceptAll()) as port, connected(port) as client:
        await asyncio.sleep(0.3)
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"


# ---------------------------------------------------------------------------
# Line length. The integration suite proves a 2000-octet body line is accepted
# and a 70000-octet one is not, which brackets the limit but says nothing about
# where the number came from. This is the wiring.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("length", "code"), [(2000, b"250"), (2001, b"500")])
async def test_the_line_length_setting_is_what_bounds_a_message_line(monkeypatch, length, code):
    """PYMTA_MAX_LINE_LENGTH is the limit, to the octet, line ending excluded."""
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 2000)
    async with listening(handler=AcceptAll()) as port, connected(port) as client:
        for line in _TRANSACTION:
            await client.cmd(line)
        reply = await client.data(b"Subject: x\r\n\r\n" + b"a" * length + b"\r\n")
        assert reply[:3] == code, reply
        if code == b"500":
            assert reply == b"500 Line too long (see RFC5321 4.5.3.1.6)\r\n"
        # Either way the session goes on.
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"


@pytest.mark.asyncio
async def test_line_length_limit_is_above_the_rfc_minimum():
    """Guard the deliverability decision, not just the plumbing.

    Dropping back to the RFC-strict 1001 would permanently reject mail
    Postfix accepts, which is the regression PYMTA_MAX_LINE_LENGTH exists to
    prevent. Cheap to state, and it fails loudly if someone "restores the RFC
    default" without reading why it was raised.
    """
    assert settings.PYMTA_MAX_LINE_LENGTH > 1001


# ---------------------------------------------------------------------------
# Client blocklist. Driven against a real listener over a real socket: the
# matcher on its own would pass even if nothing ever called it.
# ---------------------------------------------------------------------------


async def _blocklist_listener(loop, networks: str, monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_BLOCKED", networks)
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_NETWORKS", _env_networks("PYMTA_TEST_BLOCKED"))
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_blocked_network_is_refused_before_the_greeting(monkeypatch):
    """A blocked client gets 554 instead of a banner, and no dialogue."""
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "127.0.0.0/8", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"554"), first
        assert b"220" not in first, first
        # And the connection is over: no command is entertained.
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b"", rest
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unblocked_network_still_gets_its_banner(monkeypatch):
    """The discriminating half: a list that does not match must not refuse.

    Without this, a blocklist that rejected everything would satisfy the test
    above and look correct.
    """
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "203.0.113.0/24", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"220"), first
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_empty_blocklist_blocks_nothing(monkeypatch):
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"220"), first
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Per-connection summary (``session_closed``).
#
# The only record of a sender that never reached a refusal or a message
# outcome from the handler: TLS refused, answered by the protocol on its own,
# timed out, or gone mid-DATA. Driven over a real socket because the
# interesting replies are the ones the protocol sends without calling any hook.
# ---------------------------------------------------------------------------


async def _session_summaries(caplog, *, count=1, timeout=5.0):
    """The ``session_closed`` records, once ``count`` of them have arrived.

    connection_lost runs a loop iteration or two after the client hangs up, so
    the line is waited for rather than expected synchronously.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        found = [r for r in caplog.records if r.getMessage() == "session_closed"]
        if len(found) >= count or asyncio.get_running_loop().time() > deadline:
            return found
        await asyncio.sleep(0.02)


async def _dialogue(port, lines, *, quit_=True):
    """Send each line, read one reply per line (multi-line replies in full)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    replies = [await asyncio.wait_for(reader.readline(), timeout=5)]
    for line in lines:
        writer.write(line)
        await writer.drain()
        while True:
            reply = await asyncio.wait_for(reader.readline(), timeout=5)
            replies.append(reply)
            if reply[3:4] != b"-":
                break
    if quit_:
        writer.write(b"QUIT\r\n")
        await writer.drain()
        replies.append(await asyncio.wait_for(reader.readline(), timeout=5))
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return replies


_ENVELOPE = [
    b"EHLO mx.sender.example\r\n",
    b"MAIL FROM:<alice@sender.example>\r\n",
    b"RCPT TO:<bob@example.com>\r\n",
]


@pytest.mark.asyncio
async def test_a_delivered_session_is_summarised_at_info(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\nbody\r\n.\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.INFO
    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=1 quit=1"
    assert record.closed_by == "quit"
    assert record.sender == "alice@sender.example"
    assert record.helo == "mx.sender.example"
    assert record.client_ip == "127.0.0.1"
    assert record.errors == 0
    assert record.tls is False
    assert not hasattr(record, "last_error_reply")


@pytest.mark.asyncio
async def test_a_refusal_the_protocol_makes_on_its_own_is_recorded(caplog):
    """555 for an unknown RCPT parameter never reaches a hook of ours.

    The protocol answers it while parsing the command and the handler is
    never called, so the summary is where it has to show.
    """
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com> XFOO=BAR\r\n",
                ],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"555"), replies
    assert record.commands == "ehlo=1 mail=1 rcpt=0/1 quit=1"
    assert record.errors == 1
    assert record.last_error_verb == "rcpt"
    assert record.last_error_reply.startswith("555")
    assert "XFOO=BAR" in record.last_error_arg


@pytest.mark.asyncio
async def test_an_oversize_body_refused_by_the_protocol_is_recorded(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, data_size_limit=64)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\n" + b"y" * 100 + b"\r\n.\r\n"]
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"552"), replies
    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=0/1 quit=1"
    assert record.last_error_verb == "data"
    assert record.last_error_reply.startswith("552")


@pytest.mark.asyncio
async def test_a_hang_up_mid_data_is_counted_as_a_failed_data(caplog):
    """The summary runs before the cut-off DATA would have counted itself."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in [*_ENVELOPE, b"DATA\r\n"]:
                writer.write(line)
                await writer.drain()
                while (await reader.readline())[3:4] == b"-":
                    pass
            writer.write(b"Subject: half a message\r\n")
            await writer.drain()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=0/1"
    assert record.closed_by in ("peer_closed", "peer_reset")


@pytest.mark.asyncio
async def test_a_failed_starttls_handshake_is_summarised_not_reported_as_a_bug(caplog):
    """A sender that refuses our TLS shows up here and nowhere else.

    It used to land in ``handler_error`` with a traceback and in the
    internal_error counters, as if pymta had crashed, with no session id or
    client to tell whose mail it was.
    """
    cert, key = "/certs/pymta-test.crt", "/certs/pymta-test.key"
    if not (os.path.exists(cert) and os.path.exists(key)):
        pytest.skip("the dev image's self-signed certificate is not available")
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, tls_context=ctx)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in (b"EHLO mx.sender.example\r\n", b"STARTTLS\r\n"):
                writer.write(line)
                await writer.drain()
                while (await reader.readline())[3:4] == b"-":
                    pass
            # Not a ClientHello: the server-side handshake fails on it.
            writer.write(b"this is not TLS\r\n" * 4)
            await writer.drain()
            with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "tls_failed"
    assert record.tls is False
    assert record.tls_error
    assert record.commands == "ehlo=1 starttls=0/1"
    assert not [r for r in caplog.records if r.getMessage() == "handler_error"]


@pytest.mark.asyncio
async def test_a_silent_connection_stays_at_debug(caplog):
    """Connect and close without a command: a TCP health check, not a sender."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [], quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.DEBUG
    assert record.commands == ""


@pytest.mark.asyncio
async def test_log_sessions_off_drops_the_summary_to_debug(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_LOG_SESSIONS", False)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.DEBUG


@pytest.mark.parametrize(("log_sessions", "level"), [(True, logging.INFO), (False, logging.DEBUG)])
@pytest.mark.asyncio
async def test_log_sessions_off_drops_the_refusals_to_debug(
    monkeypatch, caplog, log_sessions, level
):
    """A peer can have fifty of them written per connection."""
    monkeypatch.setattr(settings, "PYMTA_LOG_SESSIONS", log_sessions)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n", b"RCPT TO:<a@b.example>\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert [r.levelno for r in caplog.records if r.getMessage() == "reject"] == [level]


@pytest.mark.asyncio
async def test_a_connection_refused_at_the_gate_is_not_summarised(monkeypatch, caplog):
    """Gate refusals are what a flood is made of; the metric counts them."""
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "127.0.0.0/8", monkeypatch)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            assert await _session_summaries(caplog, timeout=0.5) == []
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_commands_are_counted_by_verb_and_reply_class():
    """``pymta_commands_total`` was declared and never incremented."""

    def sample(verb, cls):
        value = REGISTRY.get_sample_value("pymta_commands_total", {"verb": verb, "class": cls})
        return value or 0.0

    before_ok, before_refused = sample("MAIL", "2xx"), sample("RCPT", "5xx")
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com> XFOO=BAR\r\n",
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert sample("MAIL", "2xx") == before_ok + 1
    assert sample("RCPT", "5xx") == before_refused + 1


@pytest.mark.asyncio
async def test_an_auth_argument_never_reaches_the_summary(caplog):
    """``AUTH PLAIN <base64>`` is refused 502, and its argument is credentials."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(
                port,
                [b"EHLO mx.sender.example\r\n", b"AUTH PLAIN AGFsaWNlAHMzY3JldA==\r\n"],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.last_error_verb == "auth"
    assert record.last_error_arg == "[redacted]"
    assert "AGFsaWNl" not in LogfmtFormatter().format(record)


@pytest.mark.asyncio
async def test_a_refused_quit_is_not_what_closed_the_session(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port, [b"EHLO mx.sender.example\r\n", b"QUIT now\r\n"], quit_=False
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-1].startswith(b"501"), replies
    assert record.commands == "ehlo=1 quit=0/1"
    assert record.closed_by in ("peer_closed", "peer_reset")


@pytest.mark.asyncio
async def test_a_silent_connection_that_times_out_stays_at_debug(monkeypatch, caplog):
    """The 421 we send on the idle timeout is not the peer saying something."""
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "command_timeout"
    assert record.levelno == logging.DEBUG


@pytest.mark.asyncio
async def test_a_line_refused_before_dispatch_still_reaches_info(caplog):
    """An unknown verb never enters ``commands``, but the peer did talk."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"FROBNICATE\r\n"], quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.commands == ""
    assert record.last_error_reply.startswith("500")
    assert record.levelno == logging.INFO


@pytest.mark.asyncio
async def test_a_refused_nested_mail_does_not_replace_the_sender(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"MAIL FROM:<mallory@other.example>\r\n",
                ],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"503"), replies
    assert record.sender == "alice@sender.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mail", "sender"),
    [
        (b"MAIL FROM:<>\r\n", "<>"),
        # No address at all: the raw argument, not a bounce.
        (b'MAIL FROM:"\r\n', 'FROM:"'),
    ],
)
async def test_only_a_real_null_sender_is_summarised_as_one(caplog, mail, sender):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n", mail])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.sender == sender


@pytest.mark.asyncio
async def test_a_long_sender_keeps_its_domain_in_the_summary(caplog):
    sender = "SRS0=Ab3d=TT=bounces.mailer.example=" + "b" * 400 + "@forwarder.example"
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [b"EHLO mx.sender.example\r\n", b"MAIL FROM:<" + sender.encode() + b">\r\n"],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"250"), replies
    assert record.sender.startswith("SRS0=Ab3d=")
    assert record.sender.endswith("@forwarder.example")
    assert len(record.sender) < len(sender)


# ---------------------------------------------------------------------------
# MAIL command-line limit: 548 octets after EHLO, whatever else happened.
#
# The limit is worked out per connection from what EHLO offered (512, plus 26
# for SIZE and 10 for SMTPUTF8). It is not a running total and not shared.
# ---------------------------------------------------------------------------


def _mail_line(length: int) -> bytes:
    """A MAIL command of exactly ``length`` octets, CRLF excluded."""
    fixed = len(b"MAIL FROM:<@forwarder.example>")
    return b"MAIL FROM:<" + b"a" * (length - fixed) + b"@forwarder.example>\r\n"


@pytest.mark.asyncio
async def test_mail_line_limit_does_not_grow_with_repeated_ehlo():
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        ehlos = [b"EHLO mx.sender.example\r\n"] * 4
        over = await _dialogue(port, [*ehlos, _mail_line(549)])
        at = await _dialogue(port, [*ehlos, _mail_line(548)])
    finally:
        server.close()
        await server.wait_closed()

    assert over[-2].startswith(b"500"), over[-2]
    assert at[-2].startswith(b"250"), at[-2]


@pytest.mark.asyncio
async def test_mail_line_limit_is_not_shared_between_connections():
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        first = await asyncio.open_connection("127.0.0.1", port)
        others = [await asyncio.open_connection("127.0.0.1", port) for _ in range(3)]
        for reader, writer in [first, *others]:
            await reader.readline()
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            while (await asyncio.wait_for(reader.readline(), timeout=5))[3:4] == b"-":
                pass
        # A connection accepted after our EHLO: resets the shared limit to 512.
        late = await asyncio.open_connection("127.0.0.1", port)
        await late[0].readline()

        reader, writer = first
        replies = []
        for line in (_mail_line(549), _mail_line(548)):
            writer.write(line)
            await writer.drain()
            replies.append(await asyncio.wait_for(reader.readline(), timeout=5))
        for _, w in [first, *others, late]:
            w.close()
    finally:
        server.close()
        await server.wait_closed()

    assert replies[0].startswith(b"500"), replies
    assert replies[1].startswith(b"250"), replies


# ---------------------------------------------------------------------------
# One ``reject`` line per refusal.
#
# ``session_closed`` keeps the last error and the last sender only, and a
# sending platform reuses one connection for many messages.
# ---------------------------------------------------------------------------


def _rejects(caplog):
    return [r for r in caplog.records if r.getMessage() == "reject"]


@pytest.mark.asyncio
async def test_a_refusal_by_the_protocol_names_the_sender_of_its_own_envelope(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            await _dialogue(
                port,
                [
                    b"EHLO mx.platform.example\r\n",
                    b"MAIL FROM:<first@lost.example>\r\n",
                    b"RCPT TO:<bob@example.com> XFOO=BAR\r\n",
                    b"RSET\r\n",
                    b"MAIL FROM:<second@other.example>\r\n",
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    (reject,) = _rejects(caplog)
    assert reject.levelno == logging.INFO
    assert reject.sender == "first@lost.example"
    assert (reject.verb, reject.code, reject.reason) == ("rcpt", 555, "unknown_parameter")
    assert "XFOO=BAR" in reject.arg
    assert reject.session_id == summary.session_id
    assert reject.client_ip == "127.0.0.1"
    # The summary alone would have pointed at the wrong sender.
    assert summary.sender == "second@other.example"


@pytest.mark.asyncio
async def test_an_oversize_body_names_sender_and_recipients(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, data_size_limit=64)
    try:
        with caplog.at_level(logging.INFO):
            await _dialogue(
                port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\n" + b"y" * 200 + b"\r\n.\r\n"]
            )
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    (reject,) = _rejects(caplog)
    assert (reject.verb, reject.code) == ("data", 552)
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"
    assert reject.recipient_count == 1


@pytest.mark.asyncio
async def test_a_mail_line_refused_for_its_length_is_logged_with_its_domain(caplog):
    """Refused before dispatch: no verb ever sees the line."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [b"EHLO mx.sender.example\r\n", _mail_line(600)])
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"500"), replies
    (reject,) = _rejects(caplog)
    assert reject.verb is None
    assert reject.code == 500
    assert reject.arg.startswith("MAIL FROM:<aaa")
    assert reject.arg.endswith("@forwarder.example>")
    assert summary.last_error_arg == reject.arg


@pytest.mark.asyncio
async def test_a_refusal_the_handler_logged_gets_no_second_line(caplog):
    class Handler(AcceptAll):
        async def handle_RCPT(self, connection, address):
            return Reply("550 5.1.1 No such recipient", logged=True)

    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(Handler(), ip_gate=IPGate(max_total=0), **kwargs),
        host="127.0.0.1",
        port=0,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [*_ENVELOPE, b"RCPT TO:<x@example.com> FOO=1\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-3].startswith(b"550"), replies
    # Only the second RCPT, which the protocol refused.
    (reject,) = _rejects(caplog)
    assert reject.code == 555


@pytest.mark.asyncio
async def test_a_refusal_of_the_handler_gets_one_line_with_its_fields(caplog):
    class Handler(AcceptAll):
        async def handle_RCPT(self, connection, address):
            return Reply("550 5.1.1 No such recipient", "unknown_recipient", {"misses": 1})

    async with listening(handler=Handler()) as port:
        with caplog.at_level(logging.INFO):
            await _dialogue(port, _ENVELOPE)
            await _session_summaries(caplog)

    (reject,) = _rejects(caplog)
    assert (reject.verb, reject.code, reject.reason) == ("rcpt", 550, "unknown_recipient")
    assert reject.reply == "550 5.1.1 No such recipient"
    assert reject.sender == "alice@sender.example"
    assert reject.misses == 1


@pytest.mark.asyncio
async def test_protocol_refusal_lines_stop_at_the_error_budget(monkeypatch, caplog):
    """DATA is answered whatever the budget, so it is the verb that can still
    be refused past it: here with no transaction open, 503 each time."""
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [b"EHLO mx.sender.example\r\n", *[b"DATA\r\n"] * 3])
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[-4:-1]] == [b"503"] * 3, replies
    assert len(_rejects(caplog)) == 1
    assert summary.errors == 3


@pytest.mark.asyncio
async def test_a_message_deferred_for_lack_of_a_data_slot_is_logged(caplog):
    gate = IPGate(max_total=0, max_data=1)
    assert gate.acquire_data("198.51.100.9") is True
    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [*_ENVELOPE, b"DATA\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"451"), replies
    (reject,) = _rejects(caplog)
    assert reject.reason == "max_concurrent_data"
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"


async def _refused_at_the_cap(port) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    reply = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("log_sessions", [True, False])
async def test_cap_refusals_reach_info_once_per_address(monkeypatch, caplog, log_sessions):
    """A sender held off by a cap is deferred mail; a flood at its cap is not news."""
    monkeypatch.setattr(smtp_protocol, "_cap_log_throttle", LogThrottle(interval=60, max_keys=8))
    monkeypatch.setattr(settings, "PYMTA_LOG_SESSIONS", log_sessions)
    gate = IPGate(max_total=1)
    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    holder = await asyncio.open_connection("127.0.0.1", port)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            assert (await holder[0].readline()).startswith(b"220")
            replies = [await _refused_at_the_cap(port) for _ in range(3)]
            # Let the interval pass for this address.
            for entry in smtp_protocol._cap_log_throttle._seen.values():
                entry[0] -= 60
            replies.append(await _refused_at_the_cap(port))
    finally:
        holder[1].close()
        server.close()
        await server.wait_closed()

    assert all(r.startswith(b"421") for r in replies), replies
    capped = [r for r in caplog.records if r.getMessage() == "connection_capped"]
    assert all(r.client_ip == "127.0.0.1" for r in capped)
    levels = [r.levelno for r in capped]
    if not log_sessions:
        assert levels == [logging.DEBUG] * 4
        return
    assert levels == [logging.INFO, logging.DEBUG, logging.DEBUG, logging.INFO]
    assert capped[0].suppressed == 0
    assert capped[3].suppressed == 2


@pytest.mark.asyncio
async def test_a_refused_auth_line_is_redacted(caplog):
    """The per-verb call limit refuses a line before dispatch, AUTH included."""
    async with listening(handler=AcceptAll(), command_call_limit={"AUTH": 0, "*": 25}) as port:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            async with connected(port) as client:
                reply = await client.cmd(b"auth PLAIN AGFsaWNlAHMzY3JldA==")
                assert reply == b"421 4.7.0 AUTH sent too many times\r\n"
            (summary,) = await _session_summaries(caplog)
    (reject,) = _rejects(caplog)
    assert reject.arg == "AUTH [redacted]"
    assert summary.last_error_arg == "AUTH [redacted]"
    assert "AGFs" not in LogfmtFormatter().format(reject) + LogfmtFormatter().format(summary)


@pytest.mark.asyncio
async def test_a_hang_up_before_data_names_the_recipients_left_waiting(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, _ENVELOPE, quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.sender == "alice@sender.example"
    assert record.recipients == "bob@example.com"
    assert record.recipient_count == 1


@pytest.mark.asyncio
async def test_a_completed_session_names_no_pending_recipients(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\nbody\r\n.\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert not hasattr(record, "recipients")


@pytest.mark.asyncio
async def test_sessions_cut_by_the_shutdown_deadline_are_summarised(caplog):
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=0)
    server, port, _ = await _listener(loop, gate)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in _ENVELOPE:
                writer.write(line)
                await writer.drain()
                while (await asyncio.wait_for(reader.readline(), timeout=5))[3:4] == b"-":
                    pass
            log_abandoned_sessions(gate)
            records = await _session_summaries(caplog, timeout=1)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            # The connection closing afterwards must not log it again.
            await asyncio.sleep(0.1)
            (record,) = await _session_summaries(caplog)
            assert records == [record]
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "shutdown"
    assert record.sender == "alice@sender.example"
    assert record.recipients == "bob@example.com"


@pytest.mark.asyncio
async def test_a_connection_without_a_proxy_header_leaves_a_trace(monkeypatch, caplog):
    """A balancer that stops sending PROXY takes all mail down, silently."""
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)

    def refused():
        value = REGISTRY.get_sample_value(
            "pymta_connections_total", {"result": "rejected_invalid_proxy"}
        )
        return value or 0.0

    before = refused()
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, proxy_protocol_timeout=2)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    (record,) = [r for r in caplog.records if r.getMessage() == "proxy_header_invalid"]
    assert record.wire_peer == "127.0.0.1"
    assert refused() == before + 1


# ---------------------------------------------------------------------------
# STARTTLS once only.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_starttls_inside_tls_is_refused():
    """Answering 220 would start a TLS layer inside the TLS layer."""
    cert, key = "/certs/pymta-test.crt", "/certs/pymta-test.key"
    if not (os.path.exists(cert) and os.path.exists(key)):
        pytest.skip("the dev image's self-signed certificate is not available")
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, tls_context=server_ctx)

    async def command(reader, writer, line):
        writer.write(line)
        await writer.drain()
        while True:
            reply = await asyncio.wait_for(reader.readline(), timeout=5)
            if reply[3:4] != b"-":
                return reply

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        await command(reader, writer, b"EHLO mx.sender.example\r\n")
        assert (await command(reader, writer, b"STARTTLS\r\n")).startswith(b"220")
        await writer.start_tls(client_ctx)
        await command(reader, writer, b"EHLO mx.sender.example\r\n")
        second = await command(reader, writer, b"STARTTLS\r\n")
        # The session is still usable, in the TLS it already has.
        after = await command(reader, writer, b"MAIL FROM:<alice@sender.example>\r\n")
        writer.close()
        with contextlib.suppress(ConnectionError, ssl.SSLError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert second.startswith(b"503"), second
    assert after.startswith(b"250"), after


# ---------------------------------------------------------------------------
# Per-source limits count an IPv6 /64 as one source.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_addresses_of_one_ipv6_64_share_a_source(monkeypatch, caplog):
    """Or a single host has 2^64 sources and only the global cap binds it."""
    monkeypatch.setattr(smtp_protocol, "_cap_log_throttle", LogThrottle(interval=60, max_keys=8))
    # The test client only has 127.0.0.1: IPv6 sources come by PROXY header.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)

    def header(source: str) -> bytes:
        return f"PROXY TCP6 {source} 2001:db8::1 4321 25\r\n".encode()

    # 4 slots, half of them for one source.
    gate = IPGate(max_total=4, max_share=0.5)
    async with listening(handler=InboundHandler(ScriptedMDA()), gate=gate) as port:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            async with (
                connected(port, preface=header("2001:db8:1:2::1")) as first,
                connected(port, preface=header("2001:db8:1:2:ffff:ffff:ffff:ffff")) as second,
            ):
                assert first.greeting[:3] == second.greeting[:3] == b"220"
                # Same /64 again: over its share, though it is a third address.
                async with connected(port, preface=header("2001:db8:1:2::3")) as third:
                    assert third.greeting == b"421 4.7.0 Too many connections, try again later\r\n"
                # Another /64 is another source.
                async with connected(port, preface=header("2001:db8:1:3::1")) as other:
                    assert other.greeting[:3] == b"220"
                    # The gate holds the /64.
                    assert gate._per_ip == {"2001:db8:1:2::/64": 2, "2001:db8:1:3::/64": 1}
    # And gives it back.
    assert gate._per_ip == {}
    assert gate._total == 0
    # Logs keep the full address.
    (capped,) = [r for r in caplog.records if r.getMessage() == "connection_capped"]
    assert capped.client_ip == "2001:db8:1:2::3"


# ---------------------------------------------------------------------------
# Shutdown: idle sessions leave at once, open transactions finish.
# ---------------------------------------------------------------------------


@pytest.fixture
def shutting_down(monkeypatch):
    """Flip the process into shutdown inside the test, and back afterwards.

    With a short idle timeout: if the session is not released as expected, the
    test fails on the idle 421 within seconds, not after the 300 s default.
    """
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 2)
    yield runtime.request_shutdown
    runtime._shutting_down = False


async def _read_reply(reader) -> bytes:
    while True:
        reply = await asyncio.wait_for(reader.readline(), timeout=5)
        if reply[3:4] != b"-":
            return reply


@pytest.mark.asyncio
async def test_shutdown_releases_an_idle_session_with_a_421(shutting_down, caplog):
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=0)
    server, port, _ = await _listener(loop, gate)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            await _read_reply(reader)

            shutting_down()
            close_idle_sessions(gate)

            notice = await asyncio.wait_for(reader.readline(), timeout=5)
            rest = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert notice.startswith(b"421 4.3.2"), notice
    assert rest == b""
    assert record.closed_by == "shutdown"


@pytest.mark.asyncio
async def test_shutdown_lets_an_open_transaction_finish(shutting_down):
    """Cutting it would only make the sender resend the same message."""
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=0)
    server, port, _ = await _listener(loop, gate)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (b"EHLO mx.sender.example\r\n", b"MAIL FROM:<alice@sender.example>\r\n"):
            writer.write(line)
            await writer.drain()
            await _read_reply(reader)

        shutting_down()
        close_idle_sessions(gate)

        replies = []
        for line in (
            b"RCPT TO:<bob@example.com>\r\n",
            b"DATA\r\n",
            b"Subject: x\r\n\r\nbody\r\n.\r\n",
            # The transaction is over: this one would start new work.
            b"MAIL FROM:<alice@sender.example>\r\n",
        ):
            writer.write(line)
            await writer.drain()
            replies.append(await _read_reply(reader))
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies] == [b"250", b"354", b"250", b"421"], replies
    assert rest == b""


@pytest.mark.asyncio
async def test_without_a_shutdown_idle_sessions_are_left_alone():
    """``close_idle_sessions`` is only ever called from the SIGTERM handler,
    but a session must also not be refused work unless one is under way."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        replies = await _dialogue(
            port, [b"EHLO mx.sender.example\r\n", b"MAIL FROM:<alice@sender.example>\r\n"]
        )
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"250"), replies


# ---------------------------------------------------------------------------
# The error budget counts every 4xx/5xx, whoever sent it.
#
# The protocol refuses a malformed command before any handler hook runs, so
# it is the protocol that charges those and that applies the budget.
# ---------------------------------------------------------------------------


async def _talk(port, lines):
    """Send lines until the server hangs up. Returns (replies, closed)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    replies = [await _read_reply(reader)]
    closed = False
    try:
        for line in lines:
            writer.write(line)
            await writer.drain()
            reply = await _read_reply(reader)
            replies.append(reply)
            if reply.startswith(b"421"):
                closed = await asyncio.wait_for(reader.read(), timeout=5) == b""
                break
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    return replies, closed


def _counter(name, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.asyncio
async def test_refusals_made_by_the_protocol_spend_the_error_budget(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    disconnects = _counter("pymta_disconnects_421_total", reason="max_errors_per_session")
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            replies, closed = await _talk(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    *[b"RCPT TO:<bob@example.com> XFOO=BAR\r\n"] * 4,
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[3:]] == [b"555", b"555", b"421"], replies
    assert replies[-1].startswith(b"421 4.7.0 Too many errors"), replies
    assert closed is True
    assert summary.closed_by == "server_closed"
    assert [(r.code, r.reason) for r in _rejects(caplog)] == [
        (555, "unknown_parameter"),
        (555, "unknown_parameter"),
        (421, "max_errors_per_session"),
    ]
    assert _rejects(caplog)[-1].sender == "alice@sender.example"
    assert (
        _counter("pymta_disconnects_421_total", reason="max_errors_per_session") == disconnects + 1
    )


@pytest.mark.asyncio
async def test_the_closing_line_is_written_even_past_the_line_cap(monkeypatch, caplog):
    """With every refusal logged by the protocol, the 421 is line budget + 1."""
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            await _talk(port, [b"EHLO mx.sender.example\r\n", b"EXPN staff\r\n", b"NOOP\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert [r.reason for r in _rejects(caplog)] == ["not_implemented", "max_errors_per_session"]


@pytest.mark.asyncio
async def test_a_line_refused_before_dispatch_spends_the_error_budget(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        replies, closed = await _talk(port, [b"FROBNICATE\r\n", b"FROBNICATE\r\n", b"NOOP\r\n"])
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[1:]] == [b"500", b"500", b"421"], replies
    assert closed is True


@pytest.mark.asyncio
async def test_every_refusal_is_charged_once_whoever_makes_it(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 50)
    protocols = []

    class Handler(AcceptAll):
        async def handle_RCPT(self, connection, address):
            protocols.append(connection)
            return Reply("550 5.1.1 No such recipient", "unknown_recipient")

    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(Handler(), ip_gate=IPGate(max_total=0), **kwargs),
        host="127.0.0.1",
        port=0,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        await _dialogue(
            port,
            [
                *_ENVELOPE,
                b"RCPT TO:<carol@example.com>\r\n",
                # Refused before the handler is asked.
                b"RCPT TO:<dave@example.com> XFOO=BAR\r\n",
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert protocols[0].errors == 3


@pytest.mark.asyncio
async def test_data_and_quit_are_still_answered_once_the_budget_is_spent(monkeypatch):
    """The recipients were accepted before the budget ran out: refusing DATA
    would only delay their message."""
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        replies = await _dialogue(
            port,
            [
                *_ENVELOPE,
                b"RCPT TO:<carol@example.com> XFOO=BAR\r\n",
                b"DATA\r\n",
                b"Subject: x\r\n\r\nbody\r\n.\r\n",
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[-4:]] == [b"555", b"354", b"250", b"221"], replies


# ---------------------------------------------------------------------------
# Conformance choices, pinned.
# ---------------------------------------------------------------------------


class _AcceptingMDA:
    """Every mailbox exists and every delivery succeeds."""

    def __init__(self):
        self.delivered = []

    async def check_recipient(self, address, session=None):
        return MDAResult(ok=True, temp_fail=False, payload={address: True}, status_code=200)

    async def deliver(self, **kwargs):
        self.delivered.append(kwargs)
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)


async def _inbound_listener(loop, mda):
    """A listener running the real handler against ``mda``."""
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=IPGate(max_total=0), **kwargs),
        host="127.0.0.1",
        port=0,
    )
    return server, server.sockets[0].getsockname()[1]


_BODY = [b"DATA\r\n", b"Subject: x\r\n\r\nbody\r\n.\r\n"]


@pytest.mark.asyncio
async def test_addresses_without_angle_brackets_are_accepted():
    """RFC 5321 requires the brackets. Postfix accepts their absence by
    default and so do we: refusing would bounce mail that arrives today."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:alice@sender.example\r\n",
                b"RCPT TO:bob@example.com\r\n",
                *_BODY,
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[-5:-1]] == [b"250", b"250", b"354", b"250"], replies
    (delivery,) = mda.delivered
    assert delivery["sender"] == "alice@sender.example"
    assert delivery["original_recipients"] == ["bob@example.com"]


@pytest.mark.asyncio
async def test_non_ascii_addresses_are_accepted_without_the_smtputf8_parameter():
    """RFC 6531 wants ``SMTPUTF8`` on the MAIL command. The command parser
    decodes UTF-8 arguments either way, and we do not refuse a sender for
    omitting it."""
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        replies = await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                "MAIL FROM:<élise@sender.example>\r\n".encode(),
                "RCPT TO:<bjørn@example.com>\r\n".encode(),
                *_BODY,
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert [r[:3] for r in replies[-5:-1]] == [b"250", b"250", b"354", b"250"], replies
    (delivery,) = mda.delivered
    assert delivery["sender"] == "élise@sender.example"
    assert delivery["original_recipients"] == ["bjørn@example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "code"),
    [
        # Accepted and ignored, as Postfix does on the same port.
        (b"MAIL FROM:<alice@sender.example> RET=HDRS\r\n", b"250"),
        (b"MAIL FROM:<alice@sender.example> ENVID=QQ314159\r\n", b"250"),
        (b"MAIL FROM:<alice@sender.example> AUTH=<>\r\n", b"250"),
        (b"MAIL FROM:<alice@sender.example> auth=someone@sender.example ret=full\r\n", b"250"),
        (b"MAIL FROM:alice@sender.example RET=HDRS\r\n", b"250"),
        (b"RCPT TO:<bob@example.com> NOTIFY=SUCCESS,FAILURE\r\n", b"250"),
        (b"RCPT TO:<bob@example.com> ORCPT=rfc822;bob@example.com\r\n", b"250"),
        # The parameters we act on are still read: this SIZE is over the limit.
        (b"MAIL FROM:<alice@sender.example> AUTH=<> SIZE=99999999999\r\n", b"552"),
        (b"MAIL FROM:<alice@sender.example> SIZE=100 RET=HDRS BODY=8BITMIME\r\n", b"250"),
        # Everything else keeps the 555, next to an ignored one or not.
        (b"MAIL FROM:<alice@sender.example> REQUIRETLS\r\n", b"555"),
        (b"MAIL FROM:<alice@sender.example> RET=HDRS HOLDFOR=60\r\n", b"555"),
        (b"MAIL FROM:<alice@sender.example> NOTIFY=NEVER\r\n", b"555"),
        (b"RCPT TO:<bob@example.com> RET=HDRS\r\n", b"555"),
        (b"RCPT TO:<bob@example.com> NOTIFY=NEVER XFOO=BAR\r\n", b"555"),
    ],
)
async def test_dsn_and_auth_parameters_are_ignored_and_the_rest_refused(command, code):
    """This server never sends a bounce, so it does not offer DSN (RFC 3461),
    and RFC 5321 §4.1.1.11 would have an unoffered parameter answered 555.
    Senders send the DSN ones and AUTH= regardless, and a 555 is a bounce."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    lines = [b"EHLO mx.sender.example\r\n"]
    if command.startswith(b"RCPT"):
        lines.append(b"MAIL FROM:<alice@sender.example>\r\n")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        writer.write(lines[0])
        await writer.drain()
        ehlo = []
        while True:
            ehlo.append(await asyncio.wait_for(reader.readline(), timeout=5))
            if ehlo[-1][3:4] != b"-":
                break
        for line in (*lines[1:], command):
            writer.write(line)
            await writer.drain()
            reply = await _read_reply(reader)
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert not any(b"DSN" in line.upper() for line in ehlo), ehlo
    assert reply.startswith(code), reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        # With RET=HDRS taken out, the stray ">" would close the address.
        b"MAIL FROM:<a@b.example RET=HDRS >",
        # An address that does not parse, with a parameter behind it.
        b"MAIL FROM:0@\r RET=HDRS",
        b"MAIL FROM:<a@b.example\r> RET=HDRS",
    ],
)
async def test_an_ignored_parameter_does_not_make_a_malformed_command_acceptable(command):
    """All three were found by the fuzz suite on the previous implementation,
    which took the ignored parameters out before the address was parsed."""
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        assert (await client.cmd(command))[:2] == b"50"
        assert (
            await client.cmd(b"RCPT TO:<bob@example.com>") == b"503 Error: need MAIL command\r\n"
        )


@pytest.mark.asyncio
async def test_ignored_parameters_are_recorded(caplog):
    mda = _AcceptingMDA()
    before = _counter("pymta_ignored_parameters_total", parameter="NOTIFY")
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        with caplog.at_level(logging.DEBUG):
            await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example> RET=HDRS ENVID=QQ1\r\n",
                    b"RCPT TO:<bob@example.com> NOTIFY=SUCCESS ORCPT=rfc822;bob@example.com\r\n",
                    *_BODY,
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    # The address the handler saw is the address, nothing of the parameters.
    (delivery,) = mda.delivered
    assert delivery["sender"] == "alice@sender.example"
    assert delivery["original_recipients"] == ["bob@example.com"]
    assert summary.ignored_parameters == "ENVID,NOTIFY,ORCPT,RET"
    assert summary.errors == 0
    assert _counter("pymta_ignored_parameters_total", parameter="NOTIFY") == before + 1
    lines = [r for r in caplog.records if r.getMessage() == "parameters_ignored"]
    assert [(r.verb, r.parameters, r.levelno) for r in lines] == [
        ("mail", "RET=HDRS ENVID=QQ1", logging.DEBUG),
        ("rcpt", "NOTIFY=SUCCESS ORCPT=rfc822;bob@example.com", logging.DEBUG),
    ]


# ---------------------------------------------------------------------------
# A refusal the protocol makes on its own is named and counted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lines", "verb", "code", "reason"),
    [
        ([b"MAIL FROM:<a@s.example> XFOO=BAR\r\n"], "MAIL", 555, "unknown_parameter"),
        ([b"MAIL FROM:<a@s.example\r\n"], "MAIL", 553, "malformed_address"),
        ([b"MAIL\r\n"], "MAIL", 501, "bad_syntax"),
        ([b"MAIL FROM:<a@s.example> SIZE=x\r\n"], "MAIL", 501, "bad_size_parameter"),
        ([b"MAIL FROM:<a@s.example> SIZE=99999999999\r\n"], "MAIL", 552, "oversize_announced"),
        ([b"RCPT TO:<b@example.com>\r\n"], "RCPT", 503, "bad_sequence"),
        ([b"EXPN staff\r\n"], "EXPN", 502, "not_implemented"),
        # No certificate on this listener. A code with no reason of its own.
        ([b"STARTTLS\r\n"], "STARTTLS", 454, "protocol"),
        ([b"FROBNICATE\r\n"], "none", 500, "unknown_command"),
        ([b"\r\n"], "none", 500, "bad_syntax"),
        ([b"NOOP " + b"x" * 600 + b"\r\n"], "none", 500, "line_too_long"),
        ([b"NOOP\r\n"] * 21, "none", 421, "too_many_commands"),
        ([b"FROBNICATE\r\n"] * 5, "none", 502, "too_many_unknown_commands"),
    ],
)
async def test_a_protocol_refusal_has_its_own_reason_and_counter(
    caplog, lines, verb, code, reason
):
    before = _counter("pymta_refusals_total", verb=verb, code=str(code))
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            await _talk(port, [b"EHLO mx.sender.example\r\n", *lines])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    reject = _rejects(caplog)[-1]
    assert (reject.code, reject.reason) == (code, reason)
    assert _counter("pymta_refusals_total", verb=verb, code=str(code)) == before + 1


# ---------------------------------------------------------------------------
# Refused during a drain: DEBUG, with the client.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_connection_refused_by_the_drain_is_logged_at_debug(shutting_down, caplog):
    shutting_down()
    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            banner = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert banner.startswith(b"421 4.3.2"), banner
    (record,) = [r for r in caplog.records if r.getMessage() == "connection_draining"]
    assert (record.levelno, record.client_ip) == (logging.DEBUG, "127.0.0.1")
    assert record.session_id
    # Not a session: no summary at any level.
    assert not [r for r in caplog.records if r.getMessage() == "session_closed"]


# ---------------------------------------------------------------------------
# The reply to an internal error never carries the exception.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_exception_in_a_hook_answers_a_plain_421_and_counts_it(caplog):
    class Handler(AcceptAll):
        async def handle_RCPT(self, connection, address):
            raise RuntimeError("secret-internal-detail")

    rejections = _counter("pymta_security_rejections_total", reason="internal_error")
    disconnects = _counter("pymta_disconnects_421_total", reason="internal_error")
    async with listening(handler=Handler()) as port:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies, closed = await _talk(port, _ENVELOPE)
            await _session_summaries(caplog)

    assert replies[-1] == b"421 4.3.0 Internal error, please try again later\r\n", replies
    assert closed is True
    assert not any(b"internal-detail" in r or b"Error" in r for r in replies), replies
    errors = [r for r in caplog.records if r.getMessage() == "handler_error"]
    assert [r.exc_info[0] for r in errors] == [RuntimeError]
    assert _counter("pymta_security_rejections_total", reason="internal_error") == rejections + 1
    assert _counter("pymta_disconnects_421_total", reason="internal_error") == disconnects + 1


# ---------------------------------------------------------------------------
# A HELO that is not UTF-8.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_helo_that_is_not_utf8_does_not_cost_the_sender_its_mail(caplog):
    mda = _AcceptingMDA()
    loop = asyncio.get_running_loop()
    server, port = await _inbound_listener(loop, mda)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx\xe9.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com>\r\n",
                    *_BODY,
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"250"), replies
    (delivery,) = mda.delivered
    assert delivery["client_helo"] == "mx?.sender.example"

    # What the peer sent stays in the session line, in a form that can be
    # written out.
    assert summary.helo == "mx\udce9.sender.example"
    line = LogfmtFormatter().format(summary)
    assert 'helo="mx\\udce9.sender.example"' in line
    line.encode("utf-8")
    (delivered,) = [r for r in caplog.records if r.getMessage() == "message_delivered"]
    assert delivered.helo == "mx?.sender.example"
    assert delivered.session_id == summary.session_id


# ---------------------------------------------------------------------------
# An exception of ours never ends a session without a line.
#
# One raised inside a command is answered 421. One raised outside a command,
# in the gate or in the loop, is logged as ``session_error`` and the
# connection is closed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_exception_in_the_gate_is_logged_and_closes_the_connection(monkeypatch, caplog):
    async def broken(self):
        raise RuntimeError("gate bug")

    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            with monkeypatch.context() as patched:
                patched.setattr(SMTPConnection, "_admit", broken)
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    # Closed at once, not left open until the idle timeout.
                    assert await asyncio.wait_for(reader.read(), timeout=5) == b""
                finally:
                    writer.close()
            # The listener is unaffected.
            replies = await _dialogue(port, [b"EHLO mx.sender.example\r\n"])
    finally:
        server.close()
        await server.wait_closed()

    (record,) = [r for r in caplog.records if r.getMessage() == "session_error"]
    assert record.levelno == logging.ERROR
    assert record.exc_info[0] is RuntimeError
    assert record.session_id
    assert replies[0].startswith(b"220"), replies


@pytest.mark.asyncio
async def test_an_exception_in_the_session_summary_does_not_skip_the_teardown(monkeypatch, caplog):
    def broken(self, error):
        raise RuntimeError("summary bug")

    monkeypatch.setattr(SessionLog, "close", broken)
    gate = IPGate(max_total=4)
    async with listening(handler=AcceptAll(), gate=gate) as port:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n"], quit_=False)
            deadline = asyncio.get_running_loop().time() + 5
            while gate._total and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)

    # The slot came back, and the bug in the summary is in the log.
    assert gate._total == 0
    (record,) = [r for r in caplog.records if r.getMessage() == "session_error"]
    assert record.exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_every_command_is_in_the_tally(caplog):
    """One dispatch path for all of them, so none can go uncounted."""
    commands = [
        b"HELO a.example",
        b"EHLO a.example",
        b"MAIL FROM:<a@b.example>",
        b"RCPT TO:<c@d.example>",
        b"RSET",
        b"NOOP",
        b"VRFY c",
        b"EXPN c",
        b"AUTH PLAIN",
        b"HELP",
        b"STARTTLS",
    ]
    async with listening(handler=AcceptAll()) as port:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            async with connected(port) as client:
                for command in commands:
                    await client.cmd(command)
                await client.cmd(b"MAIL FROM:<a@b.example>")
                await client.cmd(b"RCPT TO:<c@d.example>")
                await client.data()
                await client.cmd(b"QUIT")
            (summary,) = await _session_summaries(caplog)
    assert summary.commands == (
        "helo=1 ehlo=1 mail=2 rcpt=2 rset=1 noop=1 vrfy=1 expn=0/1 auth=0/1 help=1 "
        "starttls=0/1 data=1 quit=1"
    )


# ---------------------------------------------------------------------------
# Idle sessions give their slot back first when the server is close to full.
#
# Postfix's stress mode: a short timeout between commands, for sessions that
# hold no transaction. The full one otherwise.
# ---------------------------------------------------------------------------


def test_the_gate_is_under_stress_from_four_fifths_of_its_cap():
    gate = IPGate(max_total=10)
    gate._total = 7
    assert gate.under_stress() is False
    gate._total = 8
    assert gate.under_stress() is True
    assert IPGate(max_total=0).under_stress() is False


def test_the_server_exports_the_stress_state():
    assert "metrics.UNDER_STRESS.set_function(" in inspect.getsource(server_module._serve)
    gate = IPGate(max_total=10)
    metrics.UNDER_STRESS.set_function(lambda: 1.0 if gate.under_stress() else 0.0)
    assert REGISTRY.get_sample_value("pymta_under_stress") == 0.0
    gate._total = 9
    assert REGISTRY.get_sample_value("pymta_under_stress") == 1.0


async def _stressed_listener(loop, monkeypatch, handler=None, *, stressed=True):
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.4)
    gate = IPGate(max_total=10)
    # Seven sessions elsewhere: ours is the eighth of ten.
    gate._total = 7 if stressed else 0
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(handler or AcceptAll(), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(("stressed", "refused_before_421"), [(True, 5), (False, 8)])
async def test_under_stress_a_session_gets_a_smaller_error_budget(
    monkeypatch, stressed, refused_before_421
):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 8)
    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(loop, monkeypatch, stressed=stressed)
    try:
        replies, closed = await _talk(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                *[b"RCPT TO:<bob@example.com> XFOO=BAR\r\n"] * 12,
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    codes = [r[:3] for r in replies[3:]]
    assert codes == [b"555"] * refused_before_421 + [b"421"], replies
    assert closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("stressed", "misses_allowed"), [(True, 2), (False, 4)])
async def test_under_stress_fewer_unknown_recipients_are_tolerated(
    monkeypatch, stressed, misses_allowed
):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 4)

    class NobodyHome:
        async def check_recipient(self, address, session=None):
            return MDAResult(ok=True, temp_fail=False, payload={address: False}, status_code=200)

    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(
        loop, monkeypatch, InboundHandler(NobodyHome()), stressed=stressed
    )
    try:
        replies, closed = await _talk(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                *[b"RCPT TO:<nobody@example.com>\r\n"] * 6,
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    codes = [r[:3] for r in replies[3:]]
    # The miss that spends the budget is the one answered 421.
    assert codes == [b"550"] * (misses_allowed - 1) + [b"421"], replies
    assert closed is True


@pytest.mark.asyncio
async def test_under_stress_an_idle_session_is_released_early(monkeypatch, caplog):
    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(loop, monkeypatch)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            await _read_reply(reader)
            started = loop.time()
            rest = await asyncio.wait_for(reader.read(), timeout=5)
            waited = loop.time() - started
            writer.close()
            await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert rest.startswith(b"421 4.4.2 Idle timeout"), rest
    assert 0.3 < waited < 2, waited
    (record,) = [r for r in caplog.records if r.getMessage() == "command_timeout"]
    assert record.limit_seconds == 0.4


@pytest.mark.asyncio
async def test_under_stress_a_session_waiting_inside_a_transaction_is_released_too(monkeypatch):
    """MAIL and then nothing would otherwise keep the slot."""
    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(loop, monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        for line in (b"EHLO mx.sender.example\r\n", b"MAIL FROM:<alice@sender.example>\r\n"):
            writer.write(line)
            await writer.drain()
            await _read_reply(reader)
        started = time.monotonic()
        reply = await asyncio.wait_for(_read_reply(reader), timeout=5)
        elapsed = time.monotonic() - started
        writer.close()
    finally:
        server.close()
        await server.wait_closed()

    assert reply == b"421 4.4.2 Idle timeout, closing connection\r\n"
    assert 0.3 < elapsed < 2


@pytest.mark.asyncio
async def test_a_session_idle_before_the_stress_began_is_released_once_it_does(monkeypatch):
    """Its timer was armed at the full timeout, when there was room."""
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.4)
    gate = IPGate(max_total=10, max_share=1)
    async with listening(gate=gate) as port, connected(port) as early:
        # Six sessions of another source, without the sockets.
        gate._per_ip["198.51.100.9"] = 6
        gate._total += 6
        assert not gate.under_stress()
        started = time.monotonic()
        # The eighth of ten slots: four fifths.
        async with connected(port):
            assert gate.under_stress()
            reply = await asyncio.wait_for(early.reply(), timeout=5)
            elapsed = time.monotonic() - started
    assert reply == b"421 4.4.2 Idle timeout, closing connection\r\n"
    assert 0.3 < elapsed < 2


@pytest.mark.asyncio
async def test_a_command_running_when_the_stress_begins_keeps_its_time(monkeypatch):
    """Only a session waiting for a command is hurried: this one waits for us."""
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)

    class _SlowCheck(ScriptedMDA):
        async def check_recipient(self, address, session=None):
            await asyncio.sleep(0.8)
            return await super().check_recipient(address, session)

    gate = IPGate(max_total=10, max_share=1)
    async with listening(_SlowCheck(), gate=gate) as port, connected(port) as busy:
        await busy.cmd(b"EHLO mx.sender.example")
        await busy.cmd(b"MAIL FROM:<alice@sender.example>")
        await busy.send(b"RCPT TO:<bob@example.com>\r\n")
        await asyncio.sleep(0.05)
        gate._per_ip["198.51.100.9"] = 6
        gate._total += 6
        async with connected(port):
            assert gate.under_stress()
            assert await busy.reply() == b"250 2.1.5 OK\r\n"


@pytest.mark.asyncio
async def test_without_stress_an_idle_session_keeps_the_full_timeout(monkeypatch):
    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(loop, monkeypatch, stressed=False)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        await asyncio.sleep(0.8)
        writer.write(b"EHLO mx.sender.example\r\n")
        await writer.drain()
        reply = await _read_reply(reader)
        writer.close()
    finally:
        server.close()
        await server.wait_closed()

    assert reply.startswith(b"250"), reply


@pytest.mark.asyncio
async def test_under_stress_a_slow_command_of_ours_is_not_an_idle_peer(monkeypatch):
    """The MDA can take longer than the stress timeout to answer a check."""

    class Slow(AcceptAll):
        # MAIL: no transaction is open yet when it is dispatched, so this is
        # the command the stress timeout would otherwise cut.
        async def handle_MAIL(self, connection, address):
            await asyncio.sleep(0.8)
            return await super().handle_MAIL(connection, address)

    loop = asyncio.get_running_loop()
    server, port = await _stressed_listener(loop, monkeypatch, Slow())
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await reader.readline()
        replies = []
        for line in (
            b"EHLO mx.sender.example\r\n",
            b"MAIL FROM:<alice@sender.example>\r\n",
            b"RCPT TO:<bob@example.com>\r\n",
        ):
            writer.write(line)
            await writer.drain()
            replies.append(await _read_reply(reader))
        writer.close()
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-1].startswith(b"250"), replies
