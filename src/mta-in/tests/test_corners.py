"""Corners of the server that no ordinary session reaches.

Each of these is a branch a sender cannot be made to take on demand: a
transport that stops accepting writes, a peer with no address, a shutdown that
lands on a connection in a particular state, a startup option nobody sets in
the test image. They are here so that every line of ``pymta`` runs under test.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import struct

import pytest
from prometheus_client import REGISTRY

from pymta import controller, logfmt, metrics, runtime, server, settings
from pymta.address import AddressError, validate_envelope_address
from pymta.limits import IPGate
from pymta.mda_async import MDAClient
from pymta.settings import _env_networks
from pymta.smtp_protocol import SMTPConnection, close_idle_sessions

from tests.harness import (
    LOOPBACK_PROXIES,
    Client,
    ScriptedMDA,
    capture_all,
    connected,
    counter,
    listening,
    protocol_factory,
    records,
    unlimited_gate,
)
from tests.test_handler import _tls_contexts

EHLO = b"EHLO mx.sender.example"
MAIL = b"MAIL FROM:<alice@sender.example>"
RCPT = b"RCPT TO:<bob@example.com>"


class _NullHandler:
    pass


@pytest.fixture
def clean_runtime():
    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_size_limit_none_is_offered_and_none_applied():
    async with listening(data_size_limit=0) as port, connected(port) as client:
        assert b"SIZE" not in await client.cmd(EHLO)
        assert (await client.cmd(MAIL + b" SIZE=99999999999"))[:3] == b"250"
        await client.cmd(RCPT)
        assert (await client.data(b"x" * 1000 + b"\r\n" + b"y" * 60000 + b"\r\n"))[:3] == b"250"


@pytest.mark.asyncio
async def test_a_message_with_several_overlong_lines_is_refused_once(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_LINE_LENGTH", 2000)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        for line in (EHLO, MAIL, RCPT):
            await client.cmd(line)
        body = (b"a" * 3000 + b"\r\n") * 3 + b"short\r\n"
        assert await client.data(body) == b"500 Line too long (see RFC5321 4.5.3.1.6)\r\n"
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"
    assert not mda.delivered


class _Captured:
    """A listener factory that keeps the connections it builds."""

    def __init__(self, handler=None, **kwargs):
        self._build = protocol_factory(handler or _NullHandler(), **kwargs)
        self.connections: list[SMTPConnection] = []

    def __call__(self) -> SMTPConnection:
        self.connections.append(self._build())
        return self.connections[-1]


@contextlib.asynccontextmanager
async def _capturing(**kwargs):
    factory = _Captured(**kwargs)
    server_ = await asyncio.get_running_loop().create_server(factory, host="127.0.0.1", port=0)
    try:
        yield server_.sockets[0].getsockname()[1], factory.connections
    finally:
        server_.close()


@pytest.mark.asyncio
async def test_plaintext_sent_while_the_starttls_reply_waits_is_dropped():
    """RFC 3207 section 4.2: nothing received in clear is read as if it had
    come through TLS. The reply waits when the peer is not reading, and that
    is when a command can arrive behind STARTTLS without being seen first."""
    server_ctx, client_ctx = _tls_contexts()
    async with (
        _capturing(tls_context=server_ctx) as (port, connections),
        connected(port) as client,
    ):
        await client.cmd(EHLO)
        (connection,) = connections
        connection.pause_writing()
        await client.send(b"STARTTLS\r\n")
        assert (await client.reply()).startswith(b"220")
        await client.send(b"NOOP\r\n")
        while not connection._reader.pending():
            await asyncio.sleep(0.01)
        connection.resume_writing()
        await client.writer.start_tls(client_ctx)
        # The first answer inside TLS is to the first command sent inside it.
        assert (await client.cmd(EHLO)).startswith(b"250-")
        assert await client.cmd(b"RSET") == b"250 OK\r\n"


@pytest.mark.asyncio
async def test_a_transport_that_stops_taking_writes_holds_the_session():
    """A peer that sends without reading its replies is not served further."""
    async with _capturing() as (port, connections), connected(port) as client:
        (connection,) = connections
        # What asyncio calls when the write buffer passes its high-water mark.
        connection.pause_writing()
        connection.pause_writing()
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"
        await client.send(b"RSET\r\n")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.reader.readline(), timeout=0.2)
        connection.resume_writing()
        assert await client.reply() == b"250 OK\r\n"
        # Resumed without having been paused: nothing to do.
        connection.resume_writing()
        assert await client.cmd(b"NOOP") == b"250 OK\r\n"


class _PeerlessTransport(asyncio.Transport):
    """A transport whose peer has no name, as after a reset right at accept."""

    def __init__(self):
        super().__init__()
        self.written = bytearray()
        self.closed = False

    def get_extra_info(self, name, default=None):
        return default

    def write(self, data):
        self.written += data

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass


@pytest.mark.asyncio
async def test_a_connection_without_a_peer_address_shares_one_bucket(caplog):
    before = counter("pymta_connections_total", result="no_wire_peer")
    gate = IPGate(max_total=4)
    connection = protocol_factory(_NullHandler(), gate=gate)()
    transport = _PeerlessTransport()
    with capture_all(caplog):
        connection.connection_made(transport)
        for _ in range(20):
            await asyncio.sleep(0)
        assert bytes(transport.written).startswith(b"220 ")
        assert gate._per_ip == {"unknown": 1}
        connection.connection_lost(None)
        for _ in range(20):
            await asyncio.sleep(0)
    assert gate._per_ip == {}
    assert counter("pymta_connections_total", result="no_wire_peer") == before + 1
    assert [r for r in caplog.records if r.getMessage() == "session_without_peer"]


@pytest.mark.asyncio
async def test_a_client_no_header_names_cannot_match_the_blocklist(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    monkeypatch.setenv("T", "0.0.0.0/0,::/0")
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_NETWORKS", _env_networks("T"))
    async with listening(gate=unlimited_gate()) as port:
        async with connected(port, preface=b"PROXY UNKNOWN\r\n") as client:
            assert client.greeting[:3] == b"220"
        named = b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n"
        async with connected(port, preface=named) as client:
            assert client.greeting == b"554 5.7.1 Access denied\r\n"


@pytest.mark.asyncio
async def test_shutdown_tells_an_idle_session_once(clean_runtime):
    async with listening(gate=unlimited_gate()) as port, connected(port) as client:
        await client.cmd(EHLO)
        runtime.request_shutdown()
        close_idle_sessions()
        # Already on its way out: the second sweep leaves it alone.
        close_idle_sessions()
        assert await client.reply() == b"421 4.3.2 Service not available, try another MX\r\n"
        assert await client.reader.read() == b""


@pytest.mark.asyncio
async def test_shutdown_leaves_a_connection_that_was_never_admitted(monkeypatch, clean_runtime):
    """It has no greeting yet: the PROXY timeout or its header settles it."""
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 5)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    async with listening(gate=unlimited_gate()) as port:
        client = await Client.connect(port, greeting=False)
        try:
            await asyncio.sleep(0.05)
            close_idle_sessions()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.reader.read(1), timeout=0.2)
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# Address, limits, logging, MDA client, settings
# ---------------------------------------------------------------------------


def test_a_quoted_local_part_ending_in_a_backslash_is_refused():
    with pytest.raises(AddressError):
        validate_envelope_address(
            '"alice\\', allow_empty=False, max_local=64, max_domain=255, allow_quoted=True
        )


def test_a_data_slot_is_given_back_one_at_a_time():
    gate = IPGate(max_total=0, max_data=4, max_share=0.5)
    assert gate.acquire_data("198.51.100.1")
    assert gate.acquire_data("198.51.100.1")
    gate.release_data("198.51.100.1")
    assert gate._data_per_ip == {"198.51.100.1": 1}
    gate.release_data("198.51.100.1")
    assert gate._data_per_ip == {}


def test_exc_info_asked_for_outside_an_exception_is_formatted_without_a_type():
    record = logging.LogRecord("pymta.x", logging.ERROR, __file__, 1, "event", None, (None,) * 3)
    line = logfmt.LogfmtFormatter().format(record)
    assert "error_type" not in line
    assert "error_stack_trace=" in line


@pytest.mark.asyncio
async def test_the_mda_client_starts_once_and_closes_twice():
    client = MDAClient(base_url="http://127.0.0.1:1/", secret="s" * 40)
    await client.close()
    first = await client.start()
    assert await client.start() is first
    await client.close()
    await client.close()


def test_a_network_list_with_a_bad_entry_is_refused(monkeypatch):
    monkeypatch.setenv("T", "192.0.2.0/24, not-a-network")
    with pytest.raises(ValueError, match="not-a-network"):
        _env_networks("T")


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------


def test_metrics_port_zero_starts_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="pymta.metrics"):
        assert metrics.start_metrics_server("127.0.0.1", 0) is None
    assert [r.getMessage() for r in caplog.records] == ["metrics_disabled"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_scraper_that_never_sends_its_request_is_dropped(monkeypatch):
    monkeypatch.setattr(metrics._QuietHandler, "timeout", 0.2)
    httpd = metrics.start_metrics_server("127.0.0.1", _free_port(), "k" * 32)
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as idle:
            # Closed by the server, without a traceback and without a reply.
            assert idle.recv(100) == b""
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_scrape_thread_that_cannot_start_gives_its_slot_back(monkeypatch):
    httpd = metrics.start_metrics_server("127.0.0.1", _free_port(), "k" * 32)
    try:

        def no_thread(self, request, client_address):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(metrics.socketserver.ThreadingMixIn, "process_request", no_thread)
        with socket.socket() as request, pytest.raises(RuntimeError):
            httpd.process_request(request, ("127.0.0.1", 1))
        assert httpd._scrapes == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def test_a_certificate_that_cannot_be_read_is_reported_without_an_expiry(monkeypatch, caplog):
    monkeypatch.setattr(
        settings, "PYMTA_TLS_CERT_PAIRS", [("/nonexistent.crt", "/nonexistent.key")]
    )
    assert controller.publish_certificate_expiry() == {"/nonexistent.crt": None}
    with caplog.at_level(logging.INFO, logger="pymta.server"):
        server._log_certificates()
    (record,) = caplog.records
    assert record.getMessage() == "tls_certificate"
    assert record.file == "/nonexistent.crt"
    assert not hasattr(record, "not_after")


def test_a_postfix_variable_still_in_use_is_named_at_startup(monkeypatch, caplog):
    monkeypatch.setattr(
        settings, "LEGACY_IN_USE", {("MESSAGE_SIZE_LIMIT", "PYMTA_MAX_INCOMING_EMAIL_SIZE")}
    )
    with caplog.at_level(logging.WARNING, logger="pymta.server"):
        server._check_legacy_names()
    (record,) = caplog.records
    assert record.getMessage() == "legacy_setting_in_use"
    assert (record.legacy, record.preferred) == (
        "MESSAGE_SIZE_LIMIT",
        "PYMTA_MAX_INCOMING_EMAIL_SIZE",
    )


def test_ctrl_c_ends_the_process_quietly(monkeypatch):
    async def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_configure_logging", lambda: None)
    monkeypatch.setattr(server.metrics, "start_metrics_server", lambda *args: None)
    monkeypatch.setattr(server, "_export_config_limits", lambda: None)
    monkeypatch.setattr(server, "_serve", interrupted)
    server.main()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_data", [0, 40])
async def test_the_server_starts_and_drains_in_process(
    monkeypatch, caplog, clean_runtime, concurrent_data
):
    """The whole of ``_serve``, with and without a bound on concurrent DATA."""
    monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_PORT", _free_port())
    monkeypatch.setattr(settings, "PYMTA_MAX_CONCURRENT_DATA", concurrent_data)
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 10 * 1024**2)
    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 2)
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    with caplog.at_level(logging.INFO, logger="pymta.server"):
        serving = asyncio.ensure_future(server._serve())
        try:
            await records(caplog, "memory_ceiling")
            async with connected(settings.PYMTA_SMTP_BIND_PORT) as client:
                assert client.greeting[:3] == b"220"
                assert await client.cmd(b"QUIT") == b"221 Bye\r\n"
            # Cancelled rather than signalled: the drain still runs on the way out.
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving
        finally:
            for sig in (server.signal.SIGHUP, server.signal.SIGINT, server.signal.SIGTERM):
                loop.remove_signal_handler(sig)
            loop.set_exception_handler(previous)
    (ceiling,) = [r for r in caplog.records if r.getMessage() == "memory_ceiling"]
    assert ceiling.bounded is bool(concurrent_data)
    if concurrent_data:
        # 40 messages of 10 MiB in flight, times 2.2.
        assert (ceiling.in_flight_gib, ceiling.required_gib) == (0.4, 0.9)
    else:
        assert "unlimited" in ceiling.detail
    assert "shutdown_complete" in [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_sigterm_itself_starts_the_drain(monkeypatch, caplog, clean_runtime):
    """Through the handler ``_serve`` registered, not by calling it."""
    monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_PORT", _free_port())
    monkeypatch.setattr(settings, "PYMTA_SHUTDOWN_TIMEOUT", 2)
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    # Without a handler of ours the signal would end the test run.
    unhandled = signal.signal(signal.SIGTERM, lambda *_: None)
    with caplog.at_level(logging.INFO, logger="pymta.server"):
        serving = asyncio.ensure_future(server._serve())
        try:
            await records(caplog, "memory_ceiling")
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(serving, timeout=5)
        finally:
            serving.cancel()
            for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            signal.signal(signal.SIGTERM, unhandled)
            loop.set_exception_handler(previous)
    assert runtime.is_shutting_down()
    events = [r.getMessage() for r in caplog.records]
    assert "shutdown_started" in events
    assert "shutdown_complete" in events


def test_logging_is_configured_from_the_settings(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "PYMTA_LOG_LEVEL", "WARNING")
    monkeypatch.setattr(settings, "PYMTA_LOG_VERBOSE_LIBRARIES", True)
    monkeypatch.setattr(
        logfmt, "configure", lambda level, **options: seen.append((level, options))
    )
    server._configure_logging()
    assert seen == [("WARNING", {"verbose_libraries": True})]


def test_the_limits_in_force_are_exported_as_gauges(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 77)
    server._export_config_limits()

    def limit(name):
        return REGISTRY.get_sample_value("pymta_config_limit", {"name": name})

    assert limit("max_recipients_per_envelope") == 77
    assert limit("command_timeout") == settings.PYMTA_COMMAND_TIMEOUT


# ---------------------------------------------------------------------------
# Smaller things the suite only reached through the standalone server.
# ---------------------------------------------------------------------------


def test_an_address_that_is_not_utf8_is_refused_as_such():
    with pytest.raises(AddressError) as refused:
        validate_envelope_address(
            "al\udce9ice@example.com", allow_empty=False, max_local=64, max_domain=255
        )
    assert (refused.value.reason, refused.value.smtp_code) == ("invalid_encoding", 553)


def test_a_long_list_is_logged_by_its_first_items_and_a_count():
    assert logfmt.clip_list(["a", "b", "c"], 3) == "a,b,c"
    assert logfmt.clip_list(["a", "b", "c", "d"], 2) == "a,b,+2 more"


@pytest.mark.asyncio
async def test_a_reset_by_the_peer_is_summarised_as_one(caplog):
    async with listening() as port:
        with capture_all(caplog):
            client = await Client.connect(port)
            await client.cmd(EHLO)
            # Linger 0: closing sends a RST where it would send a FIN.
            client.writer.get_extra_info("socket").setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            client.writer.transport.abort()
            (summary,) = await records(caplog, "session_closed")
    assert summary.closed_by == "peer_reset"
    assert summary.error_type == "ConnectionResetError"
