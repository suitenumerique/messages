"""PROXY protocol headers, v1 and v2, well-formed and not.

Behind a balancer every connection starts with one. A header we misread
attributes mail to the wrong address; one we wrongly refuse takes all inbound
mail down. Reference: haproxy's doc/proxy-protocol.txt.
"""

from __future__ import annotations

import ipaddress
import logging
import struct

import pytest

from pymta import settings

from tests.harness import (
    Client,
    ScriptedMDA,
    capture_all,
    connected,
    counter,
    listening,
    records,
    unlimited_gate,
)

_V2 = b"\r\n\r\n\x00\r\nQUIT\n"
_PROXY, _LOCAL = 0x21, 0x20
_TCP4, _TCP6, _UNIX, _UNSPEC = 0x11, 0x21, 0x31, 0x00


def _v2(command: int, family: int, addresses: bytes = b"", *, length: int | None = None) -> bytes:
    size = len(addresses) if length is None else length
    return _V2 + struct.pack("!BBH", command, family, size) + addresses


def _tcp4(src: str, port: int, tail: bytes = b"") -> bytes:
    pair = ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address("192.0.2.1").packed
    return _v2(_PROXY, _TCP4, pair + struct.pack("!HH", port, 25) + tail)


def _tcp6(src: str, port: int) -> bytes:
    pair = ipaddress.IPv6Address(src).packed + ipaddress.IPv6Address("2001:db8::1").packed
    return _v2(_PROXY, _TCP6, pair + struct.pack("!HH", port, 25))


# One TLV: type 0x04 (NOOP), three bytes of value.
_TLV = b"\x04\x00\x03abc"


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 1)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])


async def _deliver(client) -> None:
    for line in (
        b"EHLO mx.sender.example",
        b"MAIL FROM:<alice@sender.example>",
        b"RCPT TO:<bob@example.com>",
    ):
        assert (await client.cmd(line))[:1] == b"2", line
    assert (await client.data())[:3] == b"250"


_WITH_ADDRESS = [
    (b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n", "203.0.113.9", "4321"),
    (b"PROXY TCP6 2001:db8::9 2001:db8::1 4321 25\r\n", "2001:db8::9", "4321"),
    (b"PROXY TCP4 203.0.113.9 192.0.2.1 0 25\r\n", "203.0.113.9", "0"),
    (b"PROXY TCP4 203.0.113.9 192.0.2.1 65535 65535\r\n", "203.0.113.9", "65535"),
    (_tcp4("203.0.113.9", 4321), "203.0.113.9", "4321"),
    (_tcp6("2001:db8::9", 4321), "2001:db8::9", "4321"),
    (_tcp6("2001:db8:0:0:0:0:0:9", 1), "2001:db8::9", "1"),
    # TLVs after the addresses are read and ignored.
    (_tcp4("203.0.113.9", 4321, _TLV), "203.0.113.9", "4321"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("header", "address", "port"), _WITH_ADDRESS)
async def test_the_client_named_by_the_header_reaches_the_mda(
    proxied, header, address, port, caplog
):
    mda = ScriptedMDA()
    async with listening(mda, gate=unlimited_gate()) as listener:
        with capture_all(caplog):
            async with connected(listener, preface=header) as client:
                assert client.greeting[:3] == b"220"
                await _deliver(client)
            closed = await records(caplog, "session_closed")
    assert mda.delivered[0]["client_address"] == address
    assert mda.delivered[0]["client_port"] == port
    assert closed[0].client_ip == address
    assert closed[0].wire_peer == "127.0.0.1"


_WITHOUT_ADDRESS = [
    b"PROXY UNKNOWN\r\n",
    b"PROXY UNKNOWN ffff::1 ffff::2 1 2\r\n",
    # LOCAL: the balancer talking for itself (a health check).
    _v2(_LOCAL, _UNSPEC),
    _v2(_PROXY, _UNSPEC),
    _v2(_LOCAL, _UNSPEC, _TLV),
    # A UNIX socket path is not an address we can attribute mail to.
    _v2(_PROXY, _UNIX, b"/run/a.sock".ljust(108, b"\x00") + b"/run/b.sock".ljust(108, b"\x00")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", _WITHOUT_ADDRESS)
async def test_a_header_without_an_address_names_no_client(proxied, header):
    """Not the balancer's address either: the MDA is told nothing."""
    mda = ScriptedMDA()
    async with (
        listening(mda, gate=unlimited_gate()) as listener,
        connected(listener, preface=header) as client,
    ):
        assert client.greeting[:3] == b"220"
        await _deliver(client)
    assert mda.delivered[0]["client_address"] is None
    assert mda.delivered[0]["client_port"] is None


_INVALID = [
    # No header at all.
    b"EHLO mx.sender.example\r\n",
    b"PROXYX TCP4 203.0.113.9 192.0.2.1 4321 25\r\n",
    b"PROXY TCPX 203.0.113.9 192.0.2.1 4321 25\r\n",
    b"PROXY UDP4 203.0.113.9 192.0.2.1 4321 25\r\n",
    b"PROXY TCP4 2001:db8::9 2001:db8::1 4321 25\r\n",
    b"PROXY TCP6 203.0.113.9 192.0.2.1 4321 25\r\n",
    b"PROXY TCP4 203.0.113 192.0.2.1 4321 25\r\n",
    b"PROXY TCP4 203.0.113.9 example.com 4321 25\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 70000 25\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 70000\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 04321 25\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 -1 25\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 4321\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25 more\r\n",
    b"PROXY TCP4  203.0.113.9 192.0.2.1 4321 25\r\n",
    b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\n",
    b"PROXY TCP4 " + b"1" * 120 + b"\r\n",
    _V2[:5] + b"xxxxxxx" + struct.pack("!BBH", _PROXY, _UNSPEC, 0),
    _v2(0x11, _UNSPEC),
    _v2(0x22, _UNSPEC),
    _v2(_PROXY, 0x41),
    _v2(_PROXY, 0x13),
    # TCP4 needs twelve bytes of addresses.
    _v2(_PROXY, _TCP4, b"\x01\x02\x03\x04"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", _INVALID)
async def test_a_malformed_header_ends_the_connection_unanswered(proxied, header, caplog):
    before = counter("pymta_connections_total", result="rejected_invalid_proxy")
    mda = ScriptedMDA()
    async with listening(mda, gate=unlimited_gate()) as listener:
        with capture_all(caplog):
            client = await Client.connect(listener, greeting=False, preface=header)
            try:
                assert await client.reply() == b""
            finally:
                await client.close()
            invalid = await records(caplog, "proxy_header_invalid")
    assert len(invalid) == 1
    assert invalid[0].levelno == logging.DEBUG
    assert invalid[0].wire_peer == "127.0.0.1"
    assert counter("pymta_connections_total", result="rejected_invalid_proxy") == before + 1
    faults = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not faults, [r.getMessage() for r in faults]


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [b"", b"PRO", _V2[:8], _tcp4("203.0.113.9", 1)[:20]])
async def test_a_peer_that_leaves_before_finishing_its_header_is_not_our_fault(
    proxied, sent, caplog
):
    async with listening(gate=unlimited_gate()) as listener:
        with capture_all(caplog):
            client = await Client.connect(listener, greeting=False, preface=sent)
            await client.close()
    faults = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not faults, [r.getMessage() for r in faults]


@pytest.mark.asyncio
async def test_a_peer_that_never_sends_its_header_is_hung_up_on(proxied, monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 0.3)
    async with listening(gate=unlimited_gate()) as listener:
        client = await Client.connect(listener, greeting=False)
        try:
            # No greeting, whatever else is said on the way out.
            assert not (await client.reply()).startswith(b"220")
            assert await client.closed()
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_a_header_split_across_segments_is_read_whole(proxied):
    header = _tcp6("2001:db8::9", 4321)
    mda = ScriptedMDA()
    async with listening(mda, gate=unlimited_gate()) as listener:
        client = await Client.connect(listener, greeting=False)
        try:
            for i in range(len(header)):
                await client.send(header[i : i + 1])
            assert (await client.reply())[:3] == b"220"
            await _deliver(client)
        finally:
            await client.close()
    assert mda.delivered[0]["client_address"] == "2001:db8::9"


@pytest.mark.asyncio
async def test_commands_sent_along_with_the_header_are_not_read_as_part_of_it(proxied):
    """And count as talking before the greeting."""
    header = b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n"
    async with listening(gate=unlimited_gate()) as listener:
        client = await Client.connect(
            listener, greeting=False, preface=header + b"EHLO mx.sender.example\r\n"
        )
        try:
            assert await client.reply() == b"554 5.5.0 Error: SMTP protocol synchronization\r\n"
            assert await client.closed()
        finally:
            await client.close()
