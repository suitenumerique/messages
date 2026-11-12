"""PROXY protocol header, versions 1 and 2.

Reference: https://github.com/haproxy/haproxy/blob/master/doc/proxy-protocol.txt

Adapted from aiosmtpd's ``proxy_protocol.py``.
Copyright 2014-2021 The aiosmtpd Developers. Apache-2.0. See NOTICE.
Changes: reads from our own buffer, keeps the source address and port only
(no destination, no TLV decoding), returns None for any malformed header.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import re
import struct

from .wire import LineReader, LineTooLong

_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
_V1_SIGNATURE = b"PROXY"
# "PROXY TCP6 " + two 39-character addresses + two 5-digit ports + CRLF.
_V1_MAX_LENGTH = 107

_ADDRESS = re.compile(r"[0-9a-fA-F.:]+\Z")
_PORT = re.compile(r"(?:[1-9]\d{0,4}|0)\Z")

_LOCAL, _PROXY = 0x0, 0x1
_V2_COMMANDS = {_LOCAL, _PROXY}
_INET, _INET6, _UNIX = 0x1, 0x2, 0x3
_V2_FAMILIES = {0x0, _INET, _INET6, _UNIX}
_V2_PROTOCOLS = {0x0, 0x1, 0x2}  # UNSPEC, STREAM, DGRAM
_V2_ADDRESSES = {_INET: "!4s4sHH", _INET6: "!16s16sHH", _UNIX: "108s108s"}


@dataclasses.dataclass(frozen=True)
class ProxyHeader:
    """A well-formed header.

    ``src_addr`` is None when the header names no client: v1 ``UNKNOWN``, v2
    ``LOCAL`` (the balancer's own health check), ``UNSPEC`` or a UNIX socket.
    """

    version: int
    src_addr: str | None = None
    src_port: int | None = None


async def read_proxy_header(reader: LineReader) -> ProxyHeader | None:
    """Read one header off the start of a connection. None when malformed.

    Raises ``ConnectionClosed`` if the peer leaves first. The caller bounds
    the wait.
    """
    start = await reader.readexactly(5)
    if start == _V1_SIGNATURE:
        return await _read_v1(reader)
    if start == _V2_SIGNATURE[:5]:
        return await _read_v2(reader)
    return None


async def _read_v1(reader: LineReader) -> ProxyHeader | None:  # noqa: PLR0911
    try:
        line = await reader.readline(b"\n", limit=_V1_MAX_LENGTH - len(_V1_SIGNATURE) - 1)
    except LineTooLong:
        return None
    if not line.endswith(b"\r\n"):
        return None
    # Split on single spaces, so that two in a row leave an empty field.
    fields = line[:-2].decode("latin-1").split(" ")
    if fields[0] != "":
        # Characters between "PROXY" and the first space.
        return None
    if len(fields) > 1 and fields[1] == "UNKNOWN":
        return ProxyHeader(version=1)
    if len(fields) != 6 or fields[1] not in ("TCP4", "TCP6"):
        return None
    _, family, src, dst, src_port, dst_port = fields
    if not all(_ADDRESS.match(part) for part in (src, dst)):
        return None
    if not all(_PORT.match(part) for part in (src_port, dst_port)):
        return None
    try:
        addresses = ipaddress.ip_address(src), ipaddress.ip_address(dst)
    except ValueError:
        return None
    if any(address.version != int(family[-1]) for address in addresses):
        return None
    if int(src_port) > 65535 or int(dst_port) > 65535:
        return None
    return ProxyHeader(version=1, src_addr=str(addresses[0]), src_port=int(src_port))


async def _read_v2(reader: LineReader) -> ProxyHeader | None:
    if await reader.readexactly(len(_V2_SIGNATURE) - 5) != _V2_SIGNATURE[5:]:
        return None
    version_command, family_protocol, length = struct.unpack("!BBH", await reader.readexactly(4))
    family, protocol = family_protocol >> 4, family_protocol & 0x0F
    if (
        version_command >> 4 != 0x2
        or version_command & 0x0F not in _V2_COMMANDS
        or family not in _V2_FAMILIES
        or protocol not in _V2_PROTOCOLS
    ):
        return None
    # Read in full even when unused: what follows it is the SMTP dialogue.
    body = await reader.readexactly(length)
    layout = _V2_ADDRESSES.get(family) if protocol else None
    if layout is None or version_command & 0x0F == _LOCAL:
        # LOCAL: the spec has the receiver ignore any address that follows.
        return ProxyHeader(version=2)
    size = struct.calcsize(layout)
    if len(body) < size:
        return None
    if family == _UNIX:
        return ProxyHeader(version=2)
    src, _, src_port, _ = struct.unpack(layout, body[:size])
    return ProxyHeader(version=2, src_addr=str(ipaddress.ip_address(src)), src_port=src_port)
