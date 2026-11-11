"""Helpers for tests that talk to an in-process server over a real socket.

Everything here goes through the wire, the MDA stand-in and the log, so a test
written with it does not depend on how the server is built. ``protocol_factory``
is the one place that knows.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from prometheus_client import REGISTRY

from pymta import metrics
from pymta.controller import build_smtp_kwargs
from pymta.handler import InboundHandler
from pymta.limits import IPGate
from pymta.mda_async import MDAResult
from pymta.smtp_protocol import SMTPConnection

TIMEOUT = 5.0


def protocol_factory(handler, *, gate=None, tls_context=None, **overrides):
    """What the real server hands to ``loop.create_server``.

    Settings are read here, so patch them before calling.
    """
    kwargs = build_smtp_kwargs(tls_context=tls_context)
    kwargs.update(overrides)
    # One gate for the listener, as on the real server. Without limits unless
    # the test brings its own.
    gate = gate or unlimited_gate()
    return lambda: SMTPConnection(handler, ip_gate=gate, **kwargs)


class ScriptedMDA:
    """MDA stand-in. Answers come from two queues, then default to success.

    ``checks`` takes ``"yes"``, ``"no"``, ``"temp"``, ``"no_verdict"``,
    ``"garbage"`` or an ``MDAResult``; ``delivers`` takes ``"ok"``, ``"temp"``,
    ``"partial"``, ``"perm"``, ``"odd_200"``, an ``MDAResult``, or a callable
    awaited with the deliver keyword arguments.
    """

    def __init__(self, checks=(), delivers=()):
        self.checks, self.delivers = list(checks), list(delivers)
        self.checked: list[str] = []
        self.check_sessions: list[str | None] = []
        self.delivered: list[dict] = []

    async def check_recipient(self, address, session=None):
        self.checked.append(address)
        self.check_sessions.append(session)
        answer = self.checks.pop(0) if self.checks else "yes"
        if isinstance(answer, MDAResult):
            return answer
        if answer == "temp":
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=503)
        payload = {
            "yes": {address: True},
            "no": {address: False},
            "no_verdict": {},
            "garbage": {address: "true"},
        }[answer]
        return MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)

    async def deliver(self, **kwargs):
        self.delivered.append(kwargs)
        answer = self.delivers.pop(0) if self.delivers else "ok"
        if isinstance(answer, MDAResult):
            return answer
        if callable(answer):
            return await answer(**kwargs)
        if answer == "ok":
            return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)
        if answer == "odd_200":
            return MDAResult(ok=True, temp_fail=False, payload={}, status_code=200)
        if answer == "perm":
            return MDAResult(ok=False, temp_fail=False, payload={}, status_code=400)
        status = 207 if answer == "partial" else 503
        return MDAResult(ok=False, temp_fail=True, payload={}, status_code=status)


@contextlib.asynccontextmanager
async def listening(mda=None, *, handler=None, gate=None, tls_context=None, **overrides):
    """Run a listener on a free local port for the block. Yields the port.

    ``mda`` is wrapped in the real ``InboundHandler``; pass ``handler`` to use
    another one. With PROXY protocol patched on, pass a ``gate``: admission
    then happens once the header has been read.
    """
    if handler is None:
        handler = InboundHandler(mda if mda is not None else ScriptedMDA())
    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        protocol_factory(handler, gate=gate, tls_context=tls_context, **overrides),
        host="127.0.0.1",
        port=0,
    )
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await sessions_ended()


async def sessions_ended(timeout: float = TIMEOUT) -> None:
    """Wait until no gated session is left, so its counters are settled."""
    deadline = asyncio.get_running_loop().time() + timeout
    while metrics.active_sessions() > 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)


class Client:
    """One SMTP connection, driven a command at a time."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, port: int, *, greeting: bool = True, preface: bytes = b"") -> Client:
        """Open a connection. ``preface`` is sent first (a PROXY header)."""
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client = cls(reader, writer)
        if preface:
            await client.send(preface)
        if greeting:
            client.greeting = await client.reply()
        return client

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        with contextlib.suppress(ConnectionError):
            await self.writer.drain()

    async def reply(self) -> bytes:
        """One whole reply, all its lines. ``b""`` once the server hung up."""
        lines = []
        while True:
            try:
                line = await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
            except ConnectionError:
                line = b""
            lines.append(line)
            if not line or line[3:4] != b"-":
                return b"".join(lines)

    async def cmd(self, line: bytes) -> bytes:
        """Send one command line (CRLF added) and return its reply."""
        await self.send(line + b"\r\n")
        return await self.reply()

    async def data(self, body: bytes = b"Subject: x\r\n\r\nbody\r\n") -> bytes:
        """DATA, then ``body`` and the final dot. Returns the last reply."""
        reply = await self.cmd(b"DATA")
        if not reply.startswith(b"354"):
            return reply
        await self.send(body + b".\r\n")
        return await self.reply()

    async def starttls(self, context) -> bytes:
        reply = await self.cmd(b"STARTTLS")
        if reply.startswith(b"220"):
            await self.writer.start_tls(context)
        return reply

    async def closed(self) -> bool:
        """True when the server has closed its side."""
        try:
            return await asyncio.wait_for(self.reader.read(), timeout=TIMEOUT) == b""
        except ConnectionError:
            return True

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


@contextlib.asynccontextmanager
async def connected(port: int, **kwargs):
    client = await Client.connect(port, **kwargs)
    try:
        yield client
    finally:
        await client.close()


async def records(caplog, event: str, *, count: int = 1, timeout: float = TIMEOUT):
    """The log records named ``event``, once ``count`` of them have arrived."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        found = [r for r in caplog.records if r.getMessage() == event]
        if len(found) >= count or asyncio.get_running_loop().time() > deadline:
            return found
        await asyncio.sleep(0.02)


def counter(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def unlimited_gate() -> IPGate:
    """A gate that counts sessions and refuses none."""
    return IPGate(max_total=0)


def capture_all(caplog):
    """Capture every pymta log line, DEBUG included, for the block."""
    return caplog.at_level(logging.DEBUG, logger="pymta")
