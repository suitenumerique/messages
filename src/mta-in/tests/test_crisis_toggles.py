"""The levers you pull during an incident.

Every one of these exists to be changed under pressure, which makes them the
settings most likely to be reached for by someone who has not read the code and
cannot afford a surprise. Each is driven through the path that actually serves
traffic rather than through the predicate that implements it.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from pymta import settings
from pymta.controller import build_smtp_kwargs
from pymta.handler import InboundHandler
from pymta.limits import IPGate
from pymta.mda_async import MDAResult
from pymta.smtp_protocol import HardenedSMTP


class _Envelope:
    def __init__(self):
        self.mail_from = None
        self.rcpt_tos = []
        self.mail_options = []
        self.rcpt_options = []
        self.content = b""


class _Session:
    peer = ("203.0.113.9", 4242)
    host_name = "client.test"


class _AlwaysYesMDA:
    """Says every mailbox exists, so a rejection can only come from our side."""

    async def check_recipient(self, addr, session=None):
        return MDAResult(ok=True, temp_fail=False, payload={addr: True}, status_code=200)

    async def deliver(self, **kwargs):
        return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)


def _handler():
    return InboundHandler(_AlwaysYesMDA())


# ---------------------------------------------------------------------------
# 1. PYMTA_DEFER_ALL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_all_refuses_every_recipient(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    reply = await _handler().handle_RCPT(None, _Session(), _Envelope(), "<anyone@example.com>", [])
    assert reply.startswith("451"), reply


@pytest.mark.asyncio
async def test_defer_all_does_not_consult_the_mda(monkeypatch):
    """The point is to stop touching the MDA, not to interrogate it and ignore it.

    If the MDA is the thing that is broken, a deferring node that still calls it
    on every RCPT keeps the load on and learns nothing.
    """
    calls = []

    class _Recording(_AlwaysYesMDA):
        async def check_recipient(self, addr, session=None):
            calls.append(addr)
            return await super().check_recipient(addr)

    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    handler = InboundHandler(_Recording())
    await handler.handle_RCPT(None, _Session(), _Envelope(), "<a@example.com>", [])
    assert calls == []


@pytest.mark.asyncio
async def test_off_by_default_accepts(monkeypatch):
    """The discriminating half: with the toggle off the same call succeeds."""
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", False)
    reply = await _handler().handle_RCPT(None, _Session(), _Envelope(), "<anyone@example.com>", [])
    assert reply.startswith("250"), reply


# ---------------------------------------------------------------------------
# 2. Sender-domain and recipient blocklists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_sender_domain_is_refused_permanently(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_SENDER_DOMAINS", frozenset({"spam.example"}))
    env = _Envelope()
    reply = await _handler().handle_MAIL(None, _Session(), env, "<joe@SPAM.example>", [])
    # Case-insensitive: the forged domain will not match the case you typed.
    assert reply.startswith("554"), reply
    assert env.mail_from is None, "sender was stored despite being refused"


@pytest.mark.asyncio
async def test_unblocked_sender_domain_passes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_SENDER_DOMAINS", frozenset({"spam.example"}))
    reply = await _handler().handle_MAIL(None, _Session(), _Envelope(), "<joe@good.example>", [])
    assert reply.startswith("250"), reply


@pytest.mark.asyncio
async def test_blocked_recipient_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    env = _Envelope()
    reply = await _handler().handle_RCPT(None, _Session(), env, "<VICTIM@example.com>", [])
    assert reply.startswith("550"), reply
    assert env.rcpt_tos == []


@pytest.mark.asyncio
async def test_unblocked_recipient_passes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    reply = await _handler().handle_RCPT(None, _Session(), _Envelope(), "<other@example.com>", [])
    assert reply.startswith("250"), reply


# ---------------------------------------------------------------------------
# 4. PYMTA_DRAIN, against a real listener
# ---------------------------------------------------------------------------


async def _listener(loop):
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: HardenedSMTP(_AlwaysYesMDA(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_drain_answers_421_instead_of_the_banner(monkeypatch):
    """421 in place of 220 is how a node leaves the rotation.

    RFC 5321 §3.1 allows it, and a sender with two MXes treats it as a per-host
    defer and moves to the other one rather than queueing. 4.3.2 is RFC 3463's
    "system not accepting network messages".
    """
    monkeypatch.setattr(settings, "PYMTA_DRAIN", True)
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
    monkeypatch.setattr(settings, "PYMTA_DRAIN", False)
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
# 5. SIGHUP reload
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_settings():
    keep = {name: getattr(settings, name) for name in settings._RELOADABLE}
    yield
    for name, value in keep.items():
        setattr(settings, name, value)


def test_reload_picks_up_a_changed_toggle(monkeypatch, restore_settings):
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", False)
    monkeypatch.setenv("PYMTA_DEFER_ALL", "true")
    changed = settings.reload_runtime_settings()
    assert changed["PYMTA_DEFER_ALL"] is True
    assert settings.PYMTA_DEFER_ALL is True


def test_reload_reports_only_what_moved(monkeypatch, restore_settings):
    monkeypatch.delenv("PYMTA_DEFER_ALL", raising=False)
    monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", False)
    assert settings.reload_runtime_settings() == {}


def test_reload_leaves_everything_alone_when_the_new_value_is_bad(monkeypatch, restore_settings):
    """A typo at 3am must not be worse than not reloading.

    Values are parsed and validated in full before any of them is rebound, so a
    single bad entry cannot leave half the configuration swapped.
    """
    monkeypatch.setenv("PYMTA_BLOCKED_NETWORKS", "10.0.0.0/8")
    settings.reload_runtime_settings()
    good = settings.PYMTA_BLOCKED_NETWORKS
    assert good

    monkeypatch.setenv("PYMTA_DEFER_ALL", "true")
    monkeypatch.setenv("PYMTA_BLOCKED_NETWORKS", "10.0.0.0/8,not-a-cidr")
    with pytest.raises(ValueError):
        settings.reload_runtime_settings()

    assert settings.PYMTA_BLOCKED_NETWORKS == good
    assert settings.PYMTA_DEFER_ALL is False, "a later setting was applied despite the failure"


def test_reload_logs_names_without_values(monkeypatch, restore_settings, caplog):
    """The reloadable set includes a blocklist of real addresses.

    Logging what changed must not mean logging who: an operator adding a
    recipient to PYMTA_BLOCKED_RECIPIENTS would otherwise publish that address
    to whatever ingests these logs, every time they touched the list.
    """
    from pymta import server

    caplog.set_level("INFO")
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", set())
    monkeypatch.setenv("PYMTA_BLOCKED_RECIPIENTS", "victim@example.com")
    server._reload_settings()

    record = next(r for r in caplog.records if r.message == "reload_applied")
    assert "PYMTA_BLOCKED_RECIPIENTS" in record.changed
    assert "victim@example.com" not in caplog.text
    assert "victim" not in record.changed


def test_reload_republishes_the_limit_gauges(monkeypatch, restore_settings):
    """Four of the exported limits are reloadable.

    A gauge still reporting the startup value would have a dashboard plotting
    usage against a ceiling no longer in force — worst at exactly the moment
    someone reloaded because they were watching that dashboard.
    """
    from pymta import metrics, server

    monkeypatch.setattr(settings, "PYMTA_MAX_CONCURRENT_DATA", 40)
    monkeypatch.setenv("PYMTA_MAX_CONCURRENT_DATA", "12")
    server._reload_settings()

    assert metrics.CONFIG_LIMIT.labels(name="max_concurrent_data")._value.get() == 12
    # The derived one has to follow, or the pair describes two configurations.
    assert metrics.CONFIG_LIMIT.labels(name="max_sessions_total")._value.get() == 36


# ---------------------------------------------------------------------------
# 7. SIGTERM runs the whole drain protocol.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_runtime():
    from pymta import runtime

    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


def test_sigterm_starts_draining_without_the_env_var():
    """One signal is the whole protocol: no PYMTA_DRAIN, no second command."""
    from pymta import runtime, settings

    assert settings.PYMTA_DRAIN is False
    assert runtime.is_draining() is False
    runtime.request_shutdown()
    assert runtime.is_draining() is True
    assert runtime.is_shutting_down() is True


def test_drain_env_var_does_not_imply_shutdown():
    """The env var is the "drain but keep running" case, and must stay that."""
    from pymta import runtime

    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "PYMTA_DRAIN", True)
        assert runtime.is_draining() is True
        assert runtime.is_shutting_down() is False


def test_shutdown_survives_a_reload_that_clears_the_env_var():
    """A SIGHUP mid-shutdown must not put the node back in rotation.

    It is about to stop serving, so advertising itself as open would accept
    mail it cannot deliver.
    """
    from pymta import runtime

    runtime.request_shutdown()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "PYMTA_DRAIN", False)
        assert runtime.is_draining() is True


@pytest.mark.asyncio
async def test_sigterm_waits_for_the_session_then_exits(monkeypatch):
    """End to end: a live session delays the exit, and its end releases it.

    Drives ``_drain_and_close`` against a real listener with a real session
    open, because the value of this path is entirely in the waiting.
    """
    from pymta import metrics, runtime, server

    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 10)
    loop = asyncio.get_running_loop()
    srv, port = await _listener(loop)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    assert (await asyncio.wait_for(reader.readline(), timeout=5)).startswith(b"220")

    live = metrics.active_sessions()
    assert live >= 1, "the open connection should be counted as a session"

    runtime.request_shutdown()
    drain = asyncio.create_task(server._drain_and_close(srv))

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
    assert metrics.active_sessions() == 0


@pytest.mark.asyncio
async def test_drain_gives_up_at_the_deadline(monkeypatch):
    """A session that never ends must not hold the process open forever."""
    from pymta import metrics, runtime, server

    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 1)
    monkeypatch.setattr(metrics, "active_sessions", lambda: 2)
    loop = asyncio.get_running_loop()
    srv, _ = await _listener(loop)

    runtime.request_shutdown()
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(server._drain_and_close(srv), timeout=10)
    elapsed = asyncio.get_running_loop().time() - started
    assert 1 <= elapsed < 5, elapsed


@pytest.mark.asyncio
async def test_drain_does_not_read_an_unknown_count_as_drained(monkeypatch):
    """``active_sessions()`` returns -1 when the gauge cannot be read; treating
    that as an empty server cuts live sessions. The elapsed time is how the
    test tells "waited for the deadline" from "left immediately"."""
    from pymta import metrics, runtime, server

    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 1)
    monkeypatch.setattr(metrics, "active_sessions", lambda: -1)
    loop = asyncio.get_running_loop()
    srv, _ = await _listener(loop)

    runtime.request_shutdown()
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(server._drain_and_close(srv), timeout=10)
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
    import ipaddress

    from pymta.settings import parse_client_ip

    addr = parse_client_ip(raw)
    assert (addr in ipaddress.ip_network(cidr)) is expected


@pytest.mark.parametrize("raw", [None, "", "not-an-ip", "unknown"])
def test_unparseable_client_ip_is_not_a_match(raw):
    from pymta.settings import parse_client_ip

    assert parse_client_ip(raw) is None


@pytest.mark.asyncio
async def test_blocklist_matches_a_mapped_ipv4_client(monkeypatch):
    """End to end: the blocklist must fire for a dual-stack IPv4 peer."""
    from pymta.settings import _env_networks
    from pymta.smtp_protocol import HardenedSMTP

    monkeypatch.setenv("T", "198.51.100.0/24")
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_NETWORKS", _env_networks("T"))
    smtp = HardenedSMTP(_AlwaysYesMDA(), hostname="mta.test", timeout=120)
    assert smtp._is_blocked("::ffff:198.51.100.7") is True
    assert smtp._is_blocked("::ffff:203.0.113.7") is False


# ---------------------------------------------------------------------------
# 9. DATA admission: the bound on bytes in flight, and on monopolisation.
# ---------------------------------------------------------------------------


def _gate(max_data):
    from pymta.limits import IPGate

    return IPGate(max_total=0, max_data=max_data)


def test_data_slots_are_bounded_globally():
    gate = _gate(2)
    assert gate.acquire_data("198.51.100.1") is True
    assert gate.acquire_data("198.51.100.2") is True
    # Refused rather than queued: the caller answers 451 and the sender retries.
    assert gate.acquire_data("198.51.100.3") is False
    gate.release_data("198.51.100.1")
    assert gate.acquire_data("198.51.100.3") is True


def test_one_source_cannot_take_every_slot():
    """A lone source gets a large share, never all of it.

    Half of 8, because the share always keeps one portion spare for a source
    that has not arrived. Otherwise the first host to show up owns the server
    until its messages finish.
    """
    gate = _gate(8)
    assert [gate.acquire_data("198.51.100.1") for _ in range(5)] == [
        True,
        True,
        True,
        True,
        False,
    ]
    # And a newcomer is served straight away rather than waiting them out.
    assert gate.acquire_data("203.0.113.9") is True


def test_the_share_shrinks_as_sources_arrive():
    """Contention, not a constant, decides how much one source may hold."""
    gate = _gate(20)
    assert gate.acquire_data("198.51.100.1") is True
    # Alone: half the slots. With three others present: a fifth.
    assert gate._fair_share(20, 1) == 10
    for n in (2, 3, 4):
        gate.acquire_data(f"198.51.100.{n}")
    assert gate._fair_share(20, 4) == 4


def test_saturating_needs_as_many_hosts_as_slots():
    """The property a fixed per-source cap cannot give.

    Dividing 20 slots by a constant 5 lets four hosts take everything, and no
    choice of constant avoids that shape. Sharing by the sources present means
    each newcomer shrinks everyone's share, so filling the slots takes one host
    per slot.
    """
    gate = _gate(20)
    hosts = 0
    while gate.acquire_data(f"198.51.100.{hosts}"):
        hosts += 1
        if hosts > 100:
            break
    assert hosts >= 19, f"only {hosts} hosts were needed to fill 20 slots"


def test_zero_disables_the_data_bound():
    gate = _gate(0)
    assert all(gate.acquire_data(f"198.51.100.{n}") for n in range(50))


@pytest.mark.parametrize("before, after", [(0, 4), (4, 0)])
def test_slots_survive_a_limit_change_mid_phase(before, after):
    """PYMTA_MAX_CONCURRENT_DATA is reloadable, so a phase can outlive its value.

    Acquire under one limit, SIGHUP, release under another. If release consulted
    the limit in force at the time it ran, turning the bound on would decrement a
    slot never taken and turning it off would strand one forever — the second
    losing capacity permanently, because the leak persists once it is back on.
    """
    from pymta import metrics

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
