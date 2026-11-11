"""A full server takes an idle session back from a source over its share.

Over a real socket. The sources come by PROXY header: the test client only
has 127.0.0.1.

Most tests use 4 slots and a share of one half. A takes two, B and C one
each, and the server is full. When D arrives four sources share the slots,
one each, and A holds one too many.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from pymta import settings
from pymta.handler import InboundHandler
from pymta.limits import IPGate

from tests.harness import ScriptedMDA, capture_all, connected, counter, listening, records

A, B, C, D = "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"
RECLAIMED = b"421 4.4.2 Server busy, closing idle connection\r\n"
CAPPED = b"421 4.7.0 Too many connections, try again later\r\n"


@pytest.fixture(autouse=True)
def _sources_by_proxy_header(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])


def _gate(slots: int = 4) -> IPGate:
    return IPGate(max_total=slots, max_share=0.5)


def _from(source: str) -> bytes:
    return f"PROXY TCP4 {source} 192.0.2.1 4321 25\r\n".encode()


@contextlib.asynccontextmanager
async def _sessions(port: int, *sources: str):
    """One session per source named, opened in that order."""
    async with contextlib.AsyncExitStack() as stack:
        opened = []
        for source in sources:
            client = await stack.enter_async_context(connected(port, preface=_from(source)))
            assert client.greeting[:3] == b"220", (source, client.greeting)
            opened.append(client)
        yield opened


@contextlib.asynccontextmanager
async def _full_server(gate, handler=None):
    """Yields the port and the four sessions that fill it: A, A, B, C."""
    async with listening(handler=handler, gate=gate) as port:
        async with _sessions(port, A, A, B, C) as sessions:
            yield port, sessions


def _reclaimed() -> float:
    return counter("pymta_disconnects_421_total", reason="reclaimed")


async def _open_transaction(client) -> None:
    assert (await client.cmd(b"EHLO mx.sender.example"))[:3] == b"250"
    assert (await client.cmd(b"MAIL FROM:<a@sender.example>"))[:3] == b"250"


@pytest.mark.asyncio
async def test_a_new_source_takes_the_slot_of_an_idle_session(caplog):
    gate = _gate()
    before = _reclaimed()
    with capture_all(caplog):
        async with _full_server(gate) as (port, (a1, a2, b, c)):
            async with connected(port, preface=_from(D)) as newcomer:
                assert newcomer.greeting[:3] == b"220"
                # The slot was free before the newcomer was greeted.
                assert gate._per_ip == {A: 1, B: 1, C: 1, D: 1}
                # The one idle the longest, told why.
                assert await a1.reply() == RECLAIMED
                assert await a1.closed()
                for kept in (a2, b, c, newcomer):
                    assert (await kept.cmd(b"NOOP"))[:3] == b"250"
            closed = await records(caplog, "session_closed", count=2)
    assert gate._total == 0 and gate._per_ip == {}
    assert _reclaimed() == before + 1
    (summary,) = [r for r in closed if r.closed_by == "reclaimed"]
    assert summary.client_ip == A
    # It had said nothing: its summary is not worth a line at INFO.
    assert summary.levelno == logging.DEBUG
    (line,) = [r for r in caplog.records if r.getMessage() == "session_reclaimed"]
    assert line.client_ip == A


@pytest.mark.asyncio
async def test_the_session_idle_the_longest_is_the_one_closed():
    async with _full_server(_gate()) as (port, (a1, a2, _b, _c)):
        # The older connection is the one that spoke last.
        assert (await a1.cmd(b"NOOP"))[:3] == b"250"
        async with connected(port, preface=_from(D)) as newcomer:
            assert newcomer.greeting[:3] == b"220"
            assert await a2.reply() == RECLAIMED
            assert (await a1.cmd(b"NOOP"))[:3] == b"250"


@pytest.mark.asyncio
async def test_the_largest_source_gives_first():
    """Not the one whose sessions have been idle the longest."""
    gate = _gate(12)
    async with listening(gate=gate) as port:
        async with _sessions(port, *[A] * 6, *[B] * 4, C, C) as sessions:
            assert gate._per_ip == {A: 6, B: 4, C: 2}
            # B's sessions are now the ones idle the longest.
            for session in sessions[:6]:
                assert (await session.cmd(b"NOOP"))[:3] == b"250"
            async with connected(port, preface=_from(D)) as newcomer:
                assert newcomer.greeting[:3] == b"220"
                assert await sessions[0].reply() == RECLAIMED
                assert gate._per_ip == {A: 5, B: 4, C: 2, D: 1}


@pytest.mark.asyncio
async def test_a_session_inside_a_transaction_is_not_closed():
    gate = _gate()
    async with _full_server(gate) as (port, (a1, a2, _b, _c)):
        await _open_transaction(a1)
        async with connected(port, preface=_from(D)) as newcomer:
            assert newcomer.greeting[:3] == b"220"
            assert await a2.reply() == RECLAIMED
            assert (await a1.cmd(b"RCPT TO:<b@recipient.example>"))[:3] == b"250"


class _SlowToGreet(InboundHandler):
    """Holds the EHLO of ``slow.example`` until released."""

    def __init__(self):
        super().__init__(ScriptedMDA())
        self.release = asyncio.Event()

    async def handle_HELO(self, server, session, envelope, hostname) -> None:
        if hostname == "slow.example":
            await self.release.wait()
        await super().handle_HELO(server, session, envelope, hostname)


@pytest.mark.asyncio
async def test_a_session_inside_a_command_is_not_closed():
    """Outside a transaction, so that only the command protects it."""
    handler = _SlowToGreet()
    async with _full_server(_gate(), handler=handler) as (port, (a1, a2, b, _c)):
        await a1.send(b"EHLO slow.example\r\n")
        # Answered after it, so the server has read that EHLO.
        assert (await b.cmd(b"NOOP"))[:3] == b"250"
        async with connected(port, preface=_from(D)) as newcomer:
            assert newcomer.greeting[:3] == b"220"
            assert await a2.reply() == RECLAIMED
            handler.release.set()
            assert (await a1.reply())[:3] == b"250"


@pytest.mark.asyncio
async def test_a_source_with_no_idle_session_keeps_its_slots():
    gate = _gate()
    before = _reclaimed()
    async with _full_server(gate) as (port, (a1, a2, b, c)):
        await _open_transaction(a1)
        await _open_transaction(a2)
        async with connected(port, preface=_from(D)) as newcomer:
            assert newcomer.greeting == CAPPED
        assert gate._per_ip == {A: 2, B: 1, C: 1}
        # B and C are idle, and hold no more than their share.
        for kept in (b, c):
            assert (await kept.cmd(b"NOOP"))[:3] == b"250"
    assert _reclaimed() == before


@pytest.mark.asyncio
async def test_a_source_cannot_take_back_the_slot_it_lost():
    gate = _gate()
    async with _full_server(gate) as (port, (a1, _a2, _b, _c)):
        async with connected(port, preface=_from(D)) as newcomer:
            assert await a1.reply() == RECLAIMED
            async with connected(port, preface=_from(A)) as back:
                assert back.greeting == CAPPED
            assert gate._per_ip == {A: 1, B: 1, C: 1, D: 1}
            assert (await newcomer.cmd(b"NOOP"))[:3] == b"250"


@pytest.mark.asyncio
async def test_a_source_holding_its_share_takes_nothing():
    gate = _gate()
    async with _full_server(gate) as (port, sessions):
        async with connected(port, preface=_from(B)) as second:
            assert second.greeting == CAPPED
        for kept in sessions:
            assert (await kept.cmd(b"NOOP"))[:3] == b"250"


@pytest.mark.asyncio
async def test_nothing_is_taken_when_no_source_is_over_its_share():
    gate = _gate()
    async with listening(gate=gate) as port:
        async with _sessions(port, *[f"203.0.113.{n}" for n in range(4)]) as sessions:
            async with connected(port, preface=_from(D)) as newcomer:
                assert newcomer.greeting == CAPPED
            for kept in sessions:
                assert (await kept.cmd(b"NOOP"))[:3] == b"250"


@pytest.mark.asyncio
async def test_the_sessions_of_another_listener_are_left_alone():
    """Each listener has its own gate, and they share one process."""
    other_gate, gate = _gate(), _gate()
    async with listening(gate=other_gate) as other_port, listening(gate=gate) as port:
        # The other listener's sessions are the older ones.
        async with (
            _sessions(other_port, A, A, B, C) as others,
            _sessions(port, A, A, B, C) as (a1, _a2, _b, _c),
            connected(port, preface=_from(D)) as newcomer,
        ):
            assert newcomer.greeting[:3] == b"220"
            assert await a1.reply() == RECLAIMED
            assert other_gate._per_ip == {A: 2, B: 1, C: 1}
            for kept in others:
                assert (await kept.cmd(b"NOOP"))[:3] == b"250"


def test_the_sources_a_slot_can_be_taken_from_are_the_largest_first():
    gate = IPGate(max_total=12, max_share=0.5)
    gate._per_ip = {A: 3, B: 6, "203.0.113.1": 2, "203.0.113.2": 1}
    gate._total = 12
    # Four sources present: a share is 12 // 5 = 2.
    assert gate.crowding_sources(C) == [B, A]
    assert gate.crowding_sources("203.0.113.2") == [B, A]
    # At its share: nothing.
    assert gate.crowding_sources("203.0.113.1") == []
    assert gate.crowding_sources(A) == []
