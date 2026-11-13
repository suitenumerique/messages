"""The liveness probe: ``python -m pymta.healthcheck``.

It has to tell a server that answers from a process that is hung, which a TCP
connect cannot: the kernel completes the handshake either way.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading

import pytest

from pymta import healthcheck, settings

from tests.harness import LOOPBACK_PROXIES, counter, listening, unlimited_gate


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _answers(port: int, *, host: str = "127.0.0.1", proxies=None) -> bool:
    # In a thread: the probe blocks, and the server under test runs on this loop.
    return await asyncio.to_thread(healthcheck.answers, host, port, proxies=proxies, timeout=0.5)


@pytest.mark.asyncio
async def test_a_server_that_greets_is_alive():
    async with listening() as port:
        assert await _answers(port)


@pytest.mark.asyncio
async def test_a_wildcard_bind_is_probed_on_loopback():
    async with listening() as port:
        assert await _answers(port, host="0.0.0.0")


def test_nothing_listening_is_not_alive():
    assert not healthcheck.answers("127.0.0.1", _free_port(), proxies=None, timeout=0.5)


def test_a_process_that_accepts_and_says_nothing_is_not_alive():
    """What a hung event loop looks like: the kernel takes the connection."""
    with socket.socket() as hung:
        hung.bind(("127.0.0.1", 0))
        hung.listen(5)
        port = hung.getsockname()[1]
        assert not healthcheck.answers("127.0.0.1", port, proxies=None, timeout=0.2)
        # The header is sent into the void just the same.
        assert not healthcheck.answers("127.0.0.1", port, proxies=LOOPBACK_PROXIES, timeout=0.2)


def test_a_reset_is_not_an_answer():
    """The kernel resets on its own when the accept queue overflows."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def reset():
            conn, _ = server.accept()
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()

        thread = threading.Thread(target=reset, daemon=True)
        thread.start()
        port = server.getsockname()[1]
        assert not healthcheck.answers("127.0.0.1", port, proxies=None, timeout=2)
        thread.join(timeout=5)


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 5)
    return monkeypatch


@pytest.mark.asyncio
async def test_from_a_trusted_address_the_probe_is_a_health_check_of_a_balancer(proxied):
    """Its LOCAL header is read as one, and the probe is greeted."""
    proxied.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    accepted = counter("pymta_connections_total", result="accepted")
    invalid = counter("pymta_connections_total", result="rejected_invalid_proxy")
    async with listening(gate=unlimited_gate()) as port:
        assert await _answers(port, proxies=LOOPBACK_PROXIES)
        # Without the header the server waits for one, longer than the probe does.
        assert not await _answers(port, proxies=[])
    assert counter("pymta_connections_total", result="accepted") == accepted + 1
    assert counter("pymta_connections_total", result="rejected_invalid_proxy") == invalid


@pytest.mark.asyncio
async def test_from_any_other_address_it_sends_nothing_and_is_closed(proxied):
    """Closed at once, which is an answer. No header: unread, it would draw a reset."""
    proxied.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    untrusted = counter("pymta_connections_total", result="rejected_untrusted_proxy")
    async with listening(gate=unlimited_gate()) as port:
        for _ in range(20):
            assert await _answers(port, proxies=[])
    assert counter("pymta_connections_total", result="rejected_untrusted_proxy") == untrusted + 20


@pytest.mark.asyncio
async def test_main_probes_every_address_the_listener_binds(monkeypatch):
    async with listening() as port:
        monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_PORT", port)
        monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", False)
        monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_HOSTS", ("127.0.0.1", "0.0.0.0"))
        assert await asyncio.to_thread(healthcheck.main) == 0
        # One of them does not answer: not alive.
        monkeypatch.setattr(settings, "PYMTA_SMTP_BIND_HOSTS", ("127.0.0.1", "127.0.0.2"))
        assert await asyncio.to_thread(healthcheck.main) == 1


@pytest.mark.asyncio
async def test_main_reads_the_proxy_settings_of_the_server(proxied):
    proxied.setattr(settings, "PYMTA_TRUSTED_PROXIES", LOOPBACK_PROXIES)
    proxied.setattr(settings, "PYMTA_SMTP_BIND_HOSTS", ("127.0.0.1",))
    async with listening(gate=unlimited_gate()) as port:
        proxied.setattr(settings, "PYMTA_SMTP_BIND_PORT", port)
        assert await asyncio.to_thread(healthcheck.main) == 0
