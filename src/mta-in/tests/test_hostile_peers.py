"""Peers that try to hold a session, or the server's attention, past its limits.

Each of these was found by reviewing the protocol after it was rewritten, and
confirmed against it before being fixed. What they have in common: a limit
that was stated, and a way of behaving that it did not apply to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time

import pytest

from pymta import runtime, settings, smtp_protocol
from pymta.limits import IPGate
from pymta.mda_async import MDAResult
from pymta.smtp_protocol import Reply, close_idle_sessions

from tests.harness import (
    AcceptAll,
    ScriptedMDA,
    capture_all,
    connected,
    counter,
    listening,
    protocol_factory,
    records,
)
from tests.test_command_syntax import _tls_contexts

EHLO = b"EHLO mx.sender.example"
MAIL = b"MAIL FROM:<alice@sender.example>"
RCPT = b"RCPT TO:<bob@example.com>"


@contextlib.asynccontextmanager
async def _capturing(handler=None, **kwargs):
    """A listener that keeps the connections it builds. Yields (port, list)."""
    build = protocol_factory(handler or AcceptAll(), **kwargs)
    connections = []

    def factory():
        connections.append(build())
        return connections[-1]

    listener = await asyncio.get_running_loop().create_server(factory, host="127.0.0.1", port=0)
    try:
        yield listener.sockets[0].getsockname()[1], connections
    finally:
        listener.close()


class _LongReply(AcceptAll):
    """Answers RCPT with more than the peer's buffers will take unread."""

    async def handle_HELO(self, connection, hostname):
        # A send buffer too small for the reply below.
        connection.transport.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
        )
        return hostname

    async def handle_RCPT(self, connection, address):
        # Under asyncio's high-water mark: writing is not paused.
        return Reply("250 " + "x" * 60000)


@contextlib.asynccontextmanager
async def _never_reading(port: int, *lines: bytes):
    """A client with a small receive buffer that sends ``lines`` and reads nothing."""
    loop = asyncio.get_running_loop()
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.setblocking(False)
    await loop.sock_connect(sock, ("127.0.0.1", port))
    try:
        # It does wait for the greeting to have been sent.
        await asyncio.sleep(0.1)
        for line in lines:
            await loop.sock_sendall(sock, line + b"\r\n")
            await asyncio.sleep(0.05)
        yield
    finally:
        sock.close()


async def _until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


def _faults(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# A peer that does not read what we send.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_peer_that_stopped_reading_does_not_outlive_the_idle_timeout(monkeypatch, caplog):
    """The 421 would wait behind the unread replies for ever, and the session
    with it. It is dropped without one."""
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    gate = IPGate(max_total=4)
    async with _capturing(gate=gate) as (port, connections):
        with capture_all(caplog):
            async with connected(port) as client:
                await client.cmd(EHLO)
                # What asyncio calls once the write buffer is full.
                connections[0].pause_writing()
                await client.send(b"NOOP\r\n")
                assert await _until(lambda: gate._total == 0, timeout=3)
                (summary,) = await records(caplog, "session_closed")
    assert summary.closed_by == "command_timeout"
    assert not _faults(caplog)


@pytest.mark.asyncio
async def test_a_close_the_peer_does_not_take_is_forced(monkeypatch, caplog):
    """After our last reply the connection is closed, which waits for what is
    written to be sent. A peer that leaves it unread gets a few seconds."""
    monkeypatch.setattr(smtp_protocol, "_CLOSE_GRACE", 0.4)
    gate = IPGate(max_total=4)
    async with _capturing(_LongReply(), gate=gate) as (port, connections):
        with capture_all(caplog):
            async with _never_reading(port, EHLO, MAIL, RCPT, b"QUIT"):
                # The session is over at once: its slot and its summary do not
                # wait for the peer.
                assert await _until(lambda: gate._total == 0, timeout=2)
                (summary,) = await records(caplog, "session_closed")
                assert summary.closed_by == "quit"
                transport_socket = connections[0].transport.get_extra_info("socket")
                assert transport_socket.fileno() != -1, "nothing was left unsent"
                # And the socket itself goes once the grace period is over.
                assert await _until(lambda: transport_socket.fileno() == -1, timeout=3)
    assert not _faults(caplog)


@pytest.mark.asyncio
async def test_a_timeout_ends_the_session_even_while_its_close_is_pending(monkeypatch, caplog):
    """The session was waiting for a command when the timer closed it. It must
    not go on waiting until the peer takes the 421."""
    monkeypatch.setattr(smtp_protocol, "_CLOSE_GRACE", 30)
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.4)
    gate = IPGate(max_total=4)
    async with _capturing(_LongReply(), gate=gate) as (port, connections):
        with capture_all(caplog):
            async with _never_reading(port, EHLO, MAIL, RCPT):
                assert await _until(lambda: gate._total == 0, timeout=3)
                (summary,) = await records(caplog, "session_closed")
                assert summary.closed_by == "command_timeout"
                # The 421 is still unsent, and the socket open for its grace.
                assert connections[0].transport.get_extra_info("socket").fileno() != -1
                connections[0].transport.abort()
    assert not _faults(caplog)


# ---------------------------------------------------------------------------
# Lines refused before any command runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line",
    [b"NOOP " + b"x" * 600, b"x" * 70000, "NOOP é".encode()],
    ids=["over 512 octets", "over the read buffer", "not ASCII"],
)
async def test_lines_refused_unparsed_meet_the_error_budget(monkeypatch, caplog, line):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 3)
    monkeypatch.setattr(settings, "PYMTA_ENABLE_SMTPUTF8", False)
    before = counter("pymta_disconnects_421_total", reason="max_errors_per_session")
    async with listening() as port:
        with capture_all(caplog):
            async with connected(port) as client:
                for _ in range(3):
                    assert (await client.cmd(line))[:3] == b"500"
                assert await client.cmd(line) == b"421 4.7.0 Too many errors, goodbye\r\n"
                assert await client.closed()
            rejects = await records(caplog, "reject", count=4)
    assert rejects[-1].reason == "max_errors_per_session"
    assert counter("pymta_disconnects_421_total", reason="max_errors_per_session") == before + 1


# ---------------------------------------------------------------------------
# A timer that fires while a TLS handshake has the transport.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_handshake_that_never_starts_ends_at_the_session_deadline(monkeypatch, caplog):
    server_ctx, _ = _tls_contexts()
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 0.5)
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 30)
    gate = IPGate(max_total=4)
    async with listening(handler=AcceptAll(), gate=gate, tls_context=server_ctx) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                await client.cmd(EHLO)
                assert (await client.cmd(b"STARTTLS"))[:3] == b"220"
                # Says nothing more. No line in clear lands in the handshake.
                assert await asyncio.wait_for(client.reader.read(), timeout=3) == b""
            (summary,) = await records(caplog, "session_closed")
    assert summary.closed_by == "session_timeout"
    assert summary.tls is False
    assert gate._total == 0
    assert not _faults(caplog)


# ---------------------------------------------------------------------------
# A session that was busy when the shutdown began.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_busy_at_shutdown_is_told_to_go_when_it_is_done(caplog):
    released = asyncio.Event()

    async def slow(**_):
        await released.wait()
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)

    runtime._shutting_down = False
    try:
        gate = IPGate(max_total=4)
        async with listening(ScriptedMDA(delivers=[slow]), gate=gate) as port:
            with capture_all(caplog):
                async with connected(port) as client:
                    for line in (EHLO, MAIL, RCPT):
                        await client.cmd(line)
                    await client.cmd(b"DATA")
                    await client.send(b"Subject: x\r\n\r\nbody\r\n.\r\n")
                    await asyncio.sleep(0.05)
                    runtime.request_shutdown()
                    # Mid-delivery: left to finish.
                    close_idle_sessions(gate)
                    released.set()
                    assert (await client.reply())[:3] == b"250"
                    # Idle now, and not left to hold the drain.
                    assert await client.reply() == (
                        b"421 4.3.2 Service not available, try another MX\r\n"
                    )
                    assert await client.closed()
                (summary,) = await records(caplog, "session_closed")
        assert summary.closed_by == "shutdown"
    finally:
        runtime._shutting_down = False


@pytest.mark.asyncio
async def test_a_command_refused_for_the_shutdown_has_its_reject_line(caplog):
    runtime._shutting_down = False
    try:
        async with listening(gate=IPGate(max_total=4)) as port:
            with capture_all(caplog):
                async with connected(port) as client:
                    await client.cmd(EHLO)
                    runtime.request_shutdown()
                    assert await client.cmd(MAIL) == (
                        b"421 4.3.2 Service not available, try another MX\r\n"
                    )
                    assert await client.closed()
                (reject,) = await records(caplog, "reject")
                (summary,) = await records(caplog, "session_closed")
        assert (reject.verb, reject.reason, reject.code) == ("mail", "shutdown", 421)
        assert reject.sender == "alice@sender.example"
        assert summary.closed_by == "shutdown"
    finally:
        runtime._shutting_down = False


# ---------------------------------------------------------------------------
# A message made of lines too long to keep.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlong_lines_count_toward_the_message_size(monkeypatch, caplog):
    """They are skipped, not stored, but a body of nothing else would
    otherwise be read for as long as the DATA timeout allows."""
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 2000)
    mda = ScriptedMDA()
    async with listening(mda, data_size_limit=10000) as port:
        with capture_all(caplog):
            async with connected(port) as client:
                for line in (EHLO, MAIL, RCPT):
                    await client.cmd(line)
                assert (await client.cmd(b"DATA"))[:3] == b"354"
                # 15 kB in lines of 3 kB, and no end of data.
                await client.send((b"a" * 3000 + b"\r\n") * 5)
                assert await client.reply() == b"552 5.3.4 Message size exceeds fixed maximum\r\n"
                assert await client.closed()
            (reject,) = await records(caplog, "reject")
    assert reject.reason == "oversize_message"
    assert not mda.delivered


@pytest.mark.asyncio
async def test_a_few_overlong_lines_within_the_size_are_a_500_and_no_more(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 2000)
    async with listening(data_size_limit=10000) as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        body = (b"a" * 3000 + b"\r\n") * 2 + b"short\r\n"
        assert await client.data(body) == b"500 Line too long (see RFC5321 4.5.3.1.6)\r\n"
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"
