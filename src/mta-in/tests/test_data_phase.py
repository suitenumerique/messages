"""Receiving a message: line endings, the limits, and the clocks that bound it.

Over real sockets, against the real handler.
"""

from __future__ import annotations

import asyncio
import time
import tracemalloc

import pytest

from pymta import runtime, settings, smtp_protocol
from pymta.limits import IPGate
from pymta.mda_async import MDAResult
from pymta.wire import LineReader, stored_line

from tests.harness import (
    LOOPBACK_PROXIES,
    Client,
    ScriptedMDA,
    connected,
    counter,
    listening,
    unlimited_gate,
)

EHLO = b"EHLO mx.sender.example"
MAIL = b"MAIL FROM:<alice@sender.example>"
RCPT = b"RCPT TO:<bob@example.com>"
OVERSIZE = b"552 5.3.4 Message size exceeds fixed maximum\r\n"
LINE_TOO_LONG = b"500 Line too long (see RFC5321 4.5.3.1.6)\r\n"


async def _transaction(client) -> None:
    for line in (EHLO, MAIL, RCPT):
        assert (await client.cmd(line))[:1] == b"2", line


@pytest.fixture(autouse=True)
def _clean_runtime():
    runtime._shutting_down = False
    yield
    runtime._shutting_down = False


# ---------------------------------------------------------------------------
# Line endings.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_message_with_lf_line_endings_only_is_kept_whatever_its_size():
    """Its lines are short: only the absence of CRLF made it one long line."""
    body = b"Subject: x\n\n" + (b"y" * 70 + b"\n") * 1400
    assert len(body) > settings.PYMTA_MAX_LINE_LENGTH
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        reply = await client.data(body + b"\r\n")
    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["message"] == body.replace(b"\n", b"\r\n") + b"\r\n"


@pytest.mark.parametrize(
    ("line", "accepted"),
    [
        (b"x" * 100 + b"\r\n", True),
        (b"x" * 100 + b"\n", True),
        (b"x" * 101 + b"\r\n", False),
        (b"x" * 101 + b"\n", False),
        # A bare CR is not where a line is measured from.
        (b"x" * 60 + b"\r" + b"x" * 60 + b"\r\n", False),
    ],
)
@pytest.mark.asyncio
async def test_a_line_is_measured_up_to_its_ending_of_either_kind(monkeypatch, line, accepted):
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 100)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        reply = await client.data(b"Subject: x\r\n" + line + b"\r\n")
        # Refused or not, the message was read to its end.
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"
    assert reply == LINE_TOO_LONG if not accepted else reply.startswith(b"250"), reply
    assert len(mda.delivered) == accepted


@pytest.mark.asyncio
async def test_bare_line_endings_cost_no_more_memory_than_the_message():
    """Half a megabyte of bare LF: a regex over the whole body took ninety
    times that, from any peer with one accepted recipient."""
    body = (b"\n" * 60_000 + b"\r\n") * 8
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        tracemalloc.start()
        try:
            reply = await client.data(body)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert reply.startswith(b"250"), reply
    stored = mda.delivered[0]["message"]
    assert stored == (b"\r\n" * 60_001) * 8
    # The blocks, the join ScriptedMDA makes of them, and the copy the client
    # above still holds.
    assert peak < 5 * len(stored), f"{peak / len(stored):.1f} times the message"


class _MeasuringMDA(ScriptedMDA):
    """Takes the message as the server hands it over, and keeps none of it."""

    async def deliver(self, **kwargs):
        self.size = sum(map(len, kwargs["message"]))
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)


@pytest.mark.asyncio
async def test_a_message_is_in_memory_once():
    """Its blocks joined into one object at the end of DATA, it was there
    twice for that moment."""
    body = (b"x" * 76 + b"\r\n") * 60_000
    mda = _MeasuringMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.cmd(b"DATA")).startswith(b"354")
        tracemalloc.start()
        try:
            # Not client.data: the copy of the body it sends would be counted.
            await client.send(body)
            reply = await client.cmd(b".")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert reply.startswith(b"250"), reply
    assert mda.size == len(body)
    assert peak < 1.25 * len(body), f"{peak / len(body):.2f} times the message"


# ---------------------------------------------------------------------------
# The size limit.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        (b"x" * 98 + b"\r\n") * 20,
        # 1200 octets on the wire, over once every LF has its CR.
        b"\n" * 600 + b"\r\n",
    ],
)
@pytest.mark.asyncio
async def test_a_message_over_the_size_limit_is_refused_and_counted(body):
    assert len(body) > 1000 or len(body.replace(b"\n", b"\r\n")) > 1000
    mda = ScriptedMDA()
    security = counter("pymta_security_rejections_total", reason="oversize_message")
    messages = counter("pymta_messages_total", result="rejected_perm")
    async with listening(mda, data_size_limit=1000) as port, connected(port) as client:
        await _transaction(client)
        assert await client.data(body) == OVERSIZE
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"
    assert mda.delivered == []
    assert counter("pymta_security_rejections_total", reason="oversize_message") == security + 1
    assert counter("pymta_messages_total", result="rejected_perm") == messages + 1


@pytest.mark.asyncio
async def test_the_size_limit_is_on_what_is_stored():
    """A stuffing dot is not part of the message (RFC 1870 section 5)."""
    body = (b"." + b"x" * 98 + b"\r\n") * 10
    assert len(body) > 1000
    mda = ScriptedMDA()
    async with listening(mda, data_size_limit=1000) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.data(body)).startswith(b"250")
    assert len(mda.delivered[0]["message"]) == 1000


@pytest.mark.asyncio
async def test_a_refused_message_is_read_to_twice_the_limit_and_no_further():
    """Past that the peer is cut: reading on costs CPU for nothing."""
    before = counter("pymta_security_rejections_total", reason="oversize_message")
    async with listening(data_size_limit=1000) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.cmd(b"DATA")).startswith(b"354")
        # No final dot: the reply comes on the size alone.
        await client.send((b"x" * 98 + b"\r\n") * 21)
        assert await client.reply() == OVERSIZE
        assert await client.closed()
    assert counter("pymta_security_rejections_total", reason="oversize_message") == before + 1


# ---------------------------------------------------------------------------
# Lines are read many at a time where that changes nothing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_message_reads_the_same_whatever_way_its_lines_are_taken(monkeypatch):
    """Plain lines in bulk, the others one by one, in every order."""
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 1000)
    kinds = [
        b"plain line\r\n",
        b"\r\n",
        b"..stuffed\r\n",
        b"bare lf\n",
        b"bare\rcr\r\n",
        b".\n",
        b"x" * 990 + b"\r\n",
    ]
    lines = [kinds[(i * i + i // 7) % len(kinds)] for i in range(4000)]
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        reply = await client.data(b"".join(lines) + b"\r\n")
    assert reply.startswith(b"250"), reply
    expected = b"".join(stored_line(line)[0] for line in lines) + b"\r\n"
    assert mda.delivered[0]["message"] == expected


@pytest.mark.asyncio
async def test_a_body_of_empty_lines_is_not_read_a_line_at_a_time(monkeypatch):
    """One at a time, they cost thirty times the CPU of ordinary lines."""
    calls = 0
    readline = LineReader.readline

    async def counting(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await readline(self, *args, **kwargs)

    body = b"\r\n" * 200_000
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        monkeypatch.setattr(LineReader, "readline", counting)
        reply = await client.data(body)
    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["message"] == body
    # The last line, and one each time the buffer ran dry.
    assert calls < 200, calls


# ---------------------------------------------------------------------------
# A message that arrives too slowly while the DATA slots are scarce.
# ---------------------------------------------------------------------------

TOO_SLOW = b"421 4.4.2 Message received too slowly, closing connection\r\n"


def _timeout_counters() -> list[float]:
    return [
        counter("pymta_security_rejections_total", reason="data_timeout"),
        counter("pymta_disconnects_421_total", reason="data_timeout"),
    ]


@pytest.mark.asyncio
async def test_when_data_slots_are_scarce_a_message_that_does_not_arrive_is_cut(monkeypatch):
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    gate = IPGate(max_total=0, max_data=1)
    before = _timeout_counters()
    async with listening(gate=gate) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.cmd(b"DATA")).startswith(b"354")
        assert gate.data_under_stress()
        started = time.monotonic()
        assert await client.reply() == TOO_SLOW
        assert time.monotonic() - started < 2
        assert await client.closed()
    assert _timeout_counters() == [count + 1 for count in before]
    assert gate._data_total == 0


@pytest.mark.asyncio
async def test_a_message_that_keeps_arriving_is_not_cut(monkeypatch):
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    mda = ScriptedMDA()
    async with listening(mda, gate=IPGate(max_total=0, max_data=1)) as port:
        async with connected(port) as client:
            await _transaction(client)
            assert (await client.cmd(b"DATA")).startswith(b"354")
            # 500 octets a second is 150 per check: three checks pass.
            for _ in range(7):
                await client.send(b"x" * 198 + b"\r\n")
                await asyncio.sleep(0.15)
            await client.send(b".\r\n")
            assert (await client.reply()).startswith(b"250")
    assert len(mda.delivered) == 1


@pytest.mark.asyncio
async def test_a_long_line_on_its_way_counts_as_arriving(monkeypatch):
    """Octets received, not lines completed: one line may take several checks."""
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    mda = ScriptedMDA()
    async with listening(mda, gate=IPGate(max_total=0, max_data=1)) as port:
        async with connected(port) as client:
            await _transaction(client)
            assert (await client.cmd(b"DATA")).startswith(b"354")
            for _ in range(7):
                await client.send(b"x" * 100)
                await asyncio.sleep(0.15)
            await client.send(b"\r\n.\r\n")
            assert (await client.reply()).startswith(b"250")
    assert mda.delivered[0]["message"] == b"x" * 700 + b"\r\n"


@pytest.mark.asyncio
async def test_a_message_that_stops_arriving_is_cut_at_a_later_check(monkeypatch):
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    async with listening(gate=IPGate(max_total=0, max_data=1)) as port:
        async with connected(port) as client:
            await _transaction(client)
            assert (await client.cmd(b"DATA")).startswith(b"354")
            # Enough for the check at 0.3 s, and nothing for the one at 0.6 s.
            await client.send(b"x" * 400 + b"\r\n")
            started = time.monotonic()
            assert await client.reply() == TOO_SLOW
            assert 0.4 < time.monotonic() - started < 3


@pytest.mark.asyncio
async def test_what_was_sent_early_buys_no_time_later(monkeypatch):
    """A burst and then silence: cut at the next check, not when the average runs out.

    Measured since the 354, 100 kB at 500 octets a second would hold a slot
    for 200 s, the whole of ``PYMTA_DATA_TIMEOUT``.
    """
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    before = _timeout_counters()
    gate = IPGate(max_total=0, max_data=1)
    async with listening(gate=gate) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.cmd(b"DATA")).startswith(b"354")
        await client.send((b"x" * 98 + b"\r\n") * 1000)
        started = time.monotonic()
        assert await client.reply() == TOO_SLOW
        assert time.monotonic() - started < 2
        assert await client.closed()
    assert _timeout_counters() == [count + 1 for count in before]
    assert gate._data_total == 0


@pytest.mark.asyncio
async def test_the_rate_is_that_of_the_receiving_not_of_the_delivery(monkeypatch):
    """A short message whose delivery takes a while has not arrived slowly."""
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)

    async def slowly(**_):
        await asyncio.sleep(0.8)
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)

    mda = ScriptedMDA(delivers=[slowly])
    async with listening(mda, gate=IPGate(max_total=0, max_data=1)) as port:
        async with connected(port) as client:
            await _transaction(client)
            assert (await client.data(b"Subject: x\r\n")).startswith(b"250")


@pytest.mark.asyncio
async def test_a_message_complete_when_its_deadline_fires_is_not_delivered(monkeypatch):
    """The sender is told 421 and sends it again: delivered now, it would arrive twice."""
    read = smtp_protocol.SMTPConnection._read_data

    async def read_then_expire(self):
        result = await read(self)
        # The deadline, firing while the last lines are read from the buffer.
        self._data_expired(settings.PYMTA_DATA_TIMEOUT)
        return result

    monkeypatch.setattr(smtp_protocol.SMTPConnection, "_read_data", read_then_expire)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        assert await client.data() == (
            b"421 4.4.2 Timeout receiving the message, closing connection\r\n"
        )
        assert await client.closed()
    assert not mda.delivered


@pytest.mark.asyncio
async def test_with_data_slots_to_spare_a_slow_message_is_left_alone(monkeypatch):
    monkeypatch.setattr(smtp_protocol, "_STRESS_IDLE_TIMEOUT", 0.3)
    gate = IPGate(max_total=0, max_data=10)
    assert not gate.data_under_stress()
    mda = ScriptedMDA()
    async with listening(mda, gate=gate) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.cmd(b"DATA")).startswith(b"354")
        await asyncio.sleep(0.7)
        await client.send(b"Subject: x\r\n.\r\n")
        assert (await client.reply()).startswith(b"250")
    assert len(mda.delivered) == 1


def test_data_stress_begins_at_four_fifths_of_the_slots():
    gate = IPGate(max_data=5, max_share=1)
    for held in range(1, 6):
        assert gate.acquire_data(f"198.51.100.{held}")
        assert gate.data_under_stress() is (held >= 4)
    assert IPGate(max_data=0).data_under_stress() is False


# ---------------------------------------------------------------------------
# The session timeout does not cut a delivery.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_session_timeout_waits_for_the_message_being_delivered(monkeypatch):
    """Cut there, the sender would resend a message the MDA may have stored."""
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 0.3)

    async def slowly(**_):
        await asyncio.sleep(0.6)
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)

    mda = ScriptedMDA(delivers=[slowly])
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        assert (await client.data()).startswith(b"250")
        # Then the session is over, as it was due to be.
        assert await client.reply() == b"421 4.4.2 Session too long, closing connection\r\n"
    assert len(mda.delivered) == 1


# ---------------------------------------------------------------------------
# A recipient given twice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_recipient_given_twice_is_checked_and_delivered_once():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _transaction(client)
        assert await client.cmd(RCPT) == b"250 2.1.5 OK\r\n"
        assert await client.cmd(b"RCPT TO:<carol@example.com>") == b"250 2.1.5 OK\r\n"
        assert (await client.data()).startswith(b"250")
    assert mda.checked == ["bob@example.com", "carol@example.com"]
    assert mda.delivered[0]["original_recipients"] == ["bob@example.com", "carol@example.com"]


# ---------------------------------------------------------------------------
# RSET or NOOP between the messages of one connection.
#
# Postfix, Exchange and Sendmail send RSET before each message on a reused
# connection. Counted over the session, the 21st message met the RSET limit
# long before PYMTA_MAX_ENVELOPES_PER_SESSION.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("between", [b"RSET", b"NOOP"])
async def test_an_accepted_message_starts_the_junk_command_counts_again(between):
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        assert (await client.cmd(EHLO))[:1] == b"2"
        for _ in range(45):
            assert await client.cmd(between) == b"250 OK\r\n"
            assert (await client.cmd(MAIL))[:1] == b"2"
            assert (await client.cmd(RCPT))[:1] == b"2"
            assert (await client.data()).startswith(b"250")
    assert len(mda.delivered) == 45


@pytest.mark.asyncio
async def test_a_message_that_was_not_accepted_does_not(monkeypatch):
    """Only mail that went through earns it, or a refused message would be a way to idle."""
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1000)
    mda = ScriptedMDA(delivers=["temp"] * 25)
    async with listening(mda) as port, connected(port) as client:
        assert (await client.cmd(EHLO))[:1] == b"2"
        for _ in range(20):
            assert await client.cmd(b"RSET") == b"250 OK\r\n"
            assert (await client.cmd(MAIL))[:1] == b"2"
            assert (await client.cmd(RCPT))[:1] == b"2"
            assert (await client.data()).startswith(b"451")
        assert await client.cmd(b"RSET") == b"421 4.7.0 RSET sent too many times\r\n"


# ---------------------------------------------------------------------------
# Every 421 that hangs up is counted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_command_sent_too_many_times_is_a_counted_421():
    before = counter("pymta_disconnects_421_total", reason="too_many_commands")
    async with listening() as port, connected(port) as client:
        await client.cmd(EHLO)
        for _ in range(20):
            assert await client.cmd(b"NOOP") == b"250 OK\r\n"
        assert await client.cmd(b"NOOP") == b"421 4.7.0 NOOP sent too many times\r\n"
    assert counter("pymta_disconnects_421_total", reason="too_many_commands") == before + 1


@pytest.mark.asyncio
async def test_the_421_of_a_shutdown_is_counted_on_both_paths():
    before = counter("pymta_disconnects_421_total", reason="shutdown")
    gate = unlimited_gate()
    async with listening(gate=gate) as port:
        async with connected(port) as busy, connected(port) as idle:
            await busy.cmd(EHLO)
            await idle.cmd(EHLO)
            runtime.request_shutdown()
            # One is told when it asks for something new, the other unprompted.
            assert (await busy.cmd(MAIL)).startswith(b"421 4.3.2")
            smtp_protocol.close_idle_sessions(gate)
            assert (await idle.reply()).startswith(b"421 4.3.2")
    assert counter("pymta_disconnects_421_total", reason="shutdown") == before + 2


# ---------------------------------------------------------------------------
# A balancer's peer that sends no PROXY header.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_proxy_header_that_does_not_come_is_not_an_idle_session(monkeypatch):
    """Nobody was admitted: no 421, and not a timeout of a session."""
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 0.3)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    invalid = counter("pymta_connections_total", result="rejected_invalid_proxy")
    idle = counter("pymta_security_rejections_total", reason="command_timeout")
    async with listening(gate=IPGate(max_total=0)) as port:
        client = await Client.connect(port, greeting=False)
        started = time.monotonic()
        assert await client.reply() == b""
        assert 0.2 < time.monotonic() - started < 2
        await client.close()
    assert counter("pymta_connections_total", result="rejected_invalid_proxy") == invalid + 1
    assert counter("pymta_security_rejections_total", reason="command_timeout") == idle
