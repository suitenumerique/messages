"""The levers you pull during an incident.

Every one of these exists to be changed under pressure, which makes them the
settings most likely to be reached for by someone who has not read the code and
cannot afford a surprise. Each is driven through the path that actually serves
traffic rather than through the predicate that implements it.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import ipaddress

import pytest

from pymta import metrics, runtime, settings
from pymta import server as server_module
from pymta.controller import build_smtp_kwargs
from pymta.limits import IPGate
from pymta.settings import _env_networks, parse_client_ip
from pymta.smtp_protocol import SMTPConnection

from tests.harness import AcceptAll, ScriptedMDA, connected, listening, protocol_factory


async def _rcpt(port, recipient: bytes = b"<anyone@example.com>") -> bytes:
    """The reply to one RCPT in an otherwise ordinary transaction."""
    async with connected(port) as client:
        await client.cmd(b"EHLO client.test")
        assert (await client.cmd(b"MAIL FROM:<joe@good.example>"))[:3] == b"250"
        return await client.cmd(b"RCPT TO:" + recipient)


# ---------------------------------------------------------------------------
# 1. PYMTA_DEFER_ALL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_all_refuses_every_recipient(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    async with listening() as port:
        assert (await _rcpt(port))[:3] == b"451"


@pytest.mark.asyncio
async def test_defer_all_does_not_consult_the_mda(monkeypatch):
    """The point is to stop touching the MDA, not to interrogate it and ignore it.

    If the MDA is the thing that is broken, a deferring node that still calls it
    on every RCPT keeps the load on and learns nothing.
    """
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    mda = ScriptedMDA()
    async with listening(mda) as port:
        await _rcpt(port)
    assert mda.checked == []


@pytest.mark.asyncio
async def test_off_by_default_accepts(monkeypatch):
    """The discriminating half: with the toggle off the same call succeeds."""
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", False)
    async with listening() as port:
        assert (await _rcpt(port))[:3] == b"250"


# ---------------------------------------------------------------------------
# 2. Sender-domain and recipient blocklists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_sender_domain_is_refused_permanently(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_SENDER_DOMAINS", frozenset({"spam.example"}))
    async with listening() as port, connected(port) as client:
        await client.cmd(b"EHLO client.test")
        # Case-insensitive: the forged domain will not match the case you typed.
        assert (await client.cmd(b"MAIL FROM:<joe@SPAM.example>"))[:3] == b"554"
        # The sender was not stored: no transaction is open.
        assert await client.cmd(b"RCPT TO:<a@example.com>") == b"503 Error: need MAIL command\r\n"


@pytest.mark.asyncio
async def test_unblocked_sender_domain_passes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_SENDER_DOMAINS", frozenset({"spam.example"}))
    async with listening() as port:
        assert (await _rcpt(port))[:3] == b"250"


@pytest.mark.asyncio
async def test_blocked_recipient_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    async with listening() as port, connected(port) as client:
        await client.cmd(b"EHLO client.test")
        await client.cmd(b"MAIL FROM:<joe@good.example>")
        assert (await client.cmd(b"RCPT TO:<VICTIM@example.com>"))[:3] == b"550"
        # Not kept as a recipient.
        assert await client.cmd(b"DATA") == b"503 Error: need RCPT command\r\n"


@pytest.mark.asyncio
async def test_unblocked_recipient_passes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    async with listening() as port:
        assert (await _rcpt(port, b"<other@example.com>"))[:3] == b"250"


# ---------------------------------------------------------------------------
# 4. Drain on shutdown, against a real listener
# ---------------------------------------------------------------------------


async def _listener(loop, gate=None):
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = gate or IPGate(max_total=0)
    server = await loop.create_server(
        lambda: SMTPConnection(AcceptAll(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_drain_answers_421_instead_of_the_banner(monkeypatch):
    """421 in place of 220 is how a node leaves the rotation.

    RFC 5321 §3.1 allows it, and a sender with two MXes treats it as a per-host
    defer and moves to the other one rather than queueing. 4.3.2 is RFC 3463's
    "system not accepting network messages".
    """
    runtime.request_shutdown()
    loop = asyncio.get_running_loop()
    server, port = await _listener(loop)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"421"), first
        assert b"4.3.2" in first, first
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_not_draining_still_greets(monkeypatch):
    loop = asyncio.get_running_loop()
    server, port = await _listener(loop)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert (await asyncio.wait_for(reader.readline(), timeout=5)).startswith(b"220")
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# 5. SIGHUP re-reads the certificates and nothing else.
# ---------------------------------------------------------------------------


def test_sighup_is_wired_to_the_certificate_reload_only():
    source = inspect.getsource(server_module._serve)
    assert "add_signal_handler(signal.SIGHUP, _reload_tls, listener)" in source
    assert not hasattr(settings, "reload_runtime_settings")


# ---------------------------------------------------------------------------
# 7. SIGTERM runs the whole drain protocol.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_runtime():
    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


def test_sigterm_is_the_only_way_into_the_drain():
    """One signal is the whole protocol: no setting, no second command."""
    assert runtime.is_shutting_down() is False
    assert not hasattr(settings, "PYMTA_DRAIN")
    runtime.request_shutdown()
    assert runtime.is_shutting_down() is True


@pytest.mark.asyncio
async def test_sigterm_waits_for_the_session_then_exits(monkeypatch):
    """End to end: a live session delays the exit, and its end releases it.

    Drives ``_drain_and_close`` against a real listener with a real session
    open, because the value of this path is entirely in the waiting.
    """
    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 10)
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=0)
    srv, port = await _listener(loop, gate)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    assert (await asyncio.wait_for(reader.readline(), timeout=5)).startswith(b"220")

    assert gate.total == 1, "the open connection should be counted as a session"

    runtime.request_shutdown()
    drain = asyncio.create_task(server_module._drain_and_close(srv, gate))

    # Still holding the session: the drain must not finish.
    await asyncio.sleep(0.6)
    assert not drain.done(), "drained while a session was still open"

    # A new connection during the drain is turned away rather than accepted.
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    assert (await asyncio.wait_for(r2.readline(), timeout=5)).startswith(b"421")
    w2.close()
    with contextlib.suppress(ConnectionError):
        await w2.wait_closed()

    # Release the session; the drain should complete on its own.
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    await asyncio.wait_for(drain, timeout=10)
    assert gate.total == 0


@pytest.mark.asyncio
async def test_drain_gives_up_at_the_deadline(monkeypatch):
    """A session that never ends must not hold the process open forever."""
    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 1)
    gate = IPGate(max_total=0)
    # Two sessions that never end.
    gate.acquire("192.0.2.1")
    gate.acquire("192.0.2.2")
    loop = asyncio.get_running_loop()
    srv, _ = await _listener(loop, gate)

    runtime.request_shutdown()
    started = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(server_module._drain_and_close(srv, gate), timeout=10)
    finally:
        gate.release("192.0.2.1")
        gate.release("192.0.2.2")
    elapsed = asyncio.get_running_loop().time() - started
    assert 1 <= elapsed < 5, elapsed


# ---------------------------------------------------------------------------
# 8. IPv4-mapped IPv6, the form a dual-stack listener reports.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, cidr, expected",
    [
        # The case that silently failed: an IPv4 client on `PYMTA_SMTP_BIND_HOST=::`
        # arrives as ::ffff:a.b.c.d, and a plain `in` test against an IPv4 CIDR
        # is False, so the operator's block never fires.
        ("::ffff:198.51.100.7", "198.51.100.0/24", True),
        ("198.51.100.7", "198.51.100.0/24", True),
        ("::ffff:198.51.100.7", "203.0.113.0/24", False),
        ("2001:db8::1", "2001:db8::/32", True),
        ("2001:db8::1", "198.51.100.0/24", False),
    ],
)
def test_client_ip_matching_handles_the_mapped_form(raw, cidr, expected):
    addr = parse_client_ip(raw)
    assert (addr in ipaddress.ip_network(cidr)) is expected


@pytest.mark.parametrize("raw", [None, "", "not-an-ip", "unknown"])
def test_unparseable_client_ip_is_not_a_match(raw):
    assert parse_client_ip(raw) is None


@pytest.mark.asyncio
async def test_blocklist_matches_a_mapped_ipv4_client(monkeypatch):
    """End to end: the blocklist must fire for a dual-stack IPv4 peer."""
    monkeypatch.setenv("T", "198.51.100.0/24")
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_NETWORKS", _env_networks("T"))
    smtp = protocol_factory(AcceptAll())()
    assert smtp._is_blocked("::ffff:198.51.100.7") is True
    assert smtp._is_blocked("::ffff:203.0.113.7") is False


# ---------------------------------------------------------------------------
# 9. DATA admission: the bound on bytes in flight, and on monopolisation.
# ---------------------------------------------------------------------------


def _gate(max_data):
    return IPGate(max_total=0, max_data=max_data)


def test_data_slots_are_bounded_globally():
    gate = _gate(2)
    assert gate.acquire_data("198.51.100.1") is True
    assert gate.acquire_data("198.51.100.2") is True
    # Refused rather than queued: the caller answers 451 and the sender retries.
    assert gate.acquire_data("198.51.100.3") is False
    gate.release_data("198.51.100.1")
    assert gate.acquire_data("198.51.100.3") is True


def test_one_source_cannot_take_more_than_its_share():
    """A tenth of the slots by default, however alone the source is.

    Otherwise the first host to show up owns the server until its messages
    finish.
    """
    gate = _gate(40)
    assert [gate.acquire_data("198.51.100.1") for _ in range(5)] == [
        True,
        True,
        True,
        True,
        False,
    ]
    # And a newcomer is served straight away rather than waiting them out.
    assert gate.acquire_data("203.0.113.9") is True


def test_the_share_is_a_setting(monkeypatch):
    gate = _gate(40)
    monkeypatch.setattr(settings, "PYMTA_MAX_SHARE_PER_SOURCE", 0.25)
    assert [gate.acquire_data("198.51.100.1") for _ in range(11)] == [True] * 10 + [False]


@pytest.mark.parametrize(
    ("fraction", "slots", "expected"),
    [
        (0.1, 120, 12),
        (0.1, 40, 4),
        (0.5, 20, 10),
        (1, 20, 20),
        # Floating point: 0.29 * 100 is 28.999999999999996.
        (0.29, 100, 29),
        # Never less than one, or a small server would admit nobody.
        (0.1, 4, 1),
    ],
)
def test_the_share_of_a_lone_source(fraction, slots, expected):
    assert IPGate(max_share=fraction)._share(slots, 0) == expected


def test_the_share_shrinks_once_the_sources_outnumber_it():
    """With more sources present than the fraction allows for, they share equally."""
    gate = IPGate(max_share=0.1)
    assert gate._share(120, 5) == 12
    assert gate._share(120, 9) == 12
    assert gate._share(120, 11) == 10
    assert gate._share(120, 59) == 2


def _sources_needed_to_fill(gate, slots: int) -> int:
    """How many sources, each taking all it may, it takes to hold every slot."""
    # Bounded, so a gate that stops refusing fails the test instead of hanging it.
    for sources in range(1, slots + 2):
        taken = sum(gate.acquire_data(f"198.51.100.{sources}") for _ in range(slots + 1))
        assert taken <= slots
        if gate._data_total >= slots:
            return sources
    raise AssertionError(f"{slots} slots never filled")


@pytest.mark.parametrize(("fraction", "needed"), [(0.1, 11), (0.25, 5), (0.5, 3), (1, 3)])
def test_how_many_sources_it_takes_to_fill_the_server(fraction, needed):
    """Measured, not derived: message slots already held are never taken back.

    At least one over the fraction. Never fewer than three, since the equal
    share among the sources present applies too: 20, then 13, then the 7 left.
    """
    gate = IPGate(max_total=0, max_data=40, max_share=fraction)
    assert _sources_needed_to_fill(gate, 40) == needed


def test_zero_disables_the_data_bound():
    gate = _gate(0)
    assert all(gate.acquire_data(f"198.51.100.{n}") for n in range(50))


@pytest.mark.parametrize("before, after", [(0, 4), (4, 0)])
def test_slots_survive_a_limit_change_mid_phase(before, after):
    """PYMTA_MAX_CONCURRENT_DATA is read at each use, so a phase can outlive its value.

    Acquire under one limit, change it, release under another. If release consulted
    the limit in force at the time it ran, turning the bound on would decrement a
    slot never taken and turning it off would strand one forever — the second
    losing capacity permanently, because the leak persists once it is back on.
    """
    gauge = metrics.DATA_PHASES_ACTIVE
    before_value = gauge._value.get()
    gate = _gate(before)
    assert gate.acquire_data("198.51.100.1") is True
    gate._max_data = after
    gate.release_data("198.51.100.1")
    assert gate._data_total == 0
    assert gate._data_per_ip == {}
    # The gauge is the half that survives the process: a decrement without a
    # matching increment drives pymta_data_phases_active negative for good.
    assert gauge._value.get() == before_value


def test_slots_are_counted_while_the_bound_is_off():
    """The gauge has to mean the same thing in both modes.

    Counting only when the limit is set would make pymta_data_phases_active read
    zero under load for anyone who disabled the bound, which is exactly when an
    operator is watching it.
    """
    gate = _gate(0)
    for n in range(3):
        assert gate.acquire_data(f"198.51.100.{n}") is True
    assert gate._data_total == 3


def test_released_slots_do_not_leak_per_ip_entries():
    """Keyed by peer address, so a spray of sources must not grow the dict."""
    gate = _gate(4)
    for n in range(200):
        ip = f"198.51.100.{n % 250}"
        assert gate.acquire_data(ip) is True
        gate.release_data(ip)
    assert gate._data_per_ip == {}
    assert gate._data_total == 0
