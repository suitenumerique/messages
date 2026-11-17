"""Liveness probe: ``python -m pymta.healthcheck`` exits 0 when the server answers.

It connects to each address the SMTP listener binds and waits for the server
to do something with the connection: greet it, refuse it, or close it. A
connect alone proves nothing, since the kernel completes it while the process
is hung.

With PROXY protocol on, the server reads a header from the addresses in
``PYMTA_TRUSTED_PROXIES`` and closes every other connection at once. The probe
knows which of the two it is: from a trusted address it sends what a balancer
sends for its own health checks, a v2 header with the LOCAL command, and is
greeted; from any other it sends nothing and is closed. Both are answers.

A reset is not one: the kernel sends it without the process (a full accept
queue with ``tcp_abort_on_overflow``).
"""

from __future__ import annotations

import socket
import sys
from concurrent.futures import ThreadPoolExecutor

from . import settings

# Seconds for the connect, and again for the answer. The addresses are probed
# at the same time, so the probe takes twice this at most whatever their
# number. The image's HEALTHCHECK allows 5 s, interpreter start included.
_TIMEOUT = 1.0

# PROXY protocol v2: signature, version 2 + LOCAL, UNSPEC, no address.
_PROXY_LOCAL = b"\r\n\r\n\x00\r\nQUIT\n\x20\x00\x00\x00"

# Where a listener bound to every address of a family is reached.
_LOOPBACK = {"0.0.0.0": "127.0.0.1", "::": "::1"}  # noqa: S104


def answers(host: str, port: int, *, proxies: list | None, timeout: float = _TIMEOUT) -> bool:
    """Whether the listener bound to ``host`` reacts to a new connection.

    ``proxies``: the networks a PROXY header is read from, None with PROXY
    protocol off.
    """
    try:
        with socket.create_connection((_LOOPBACK.get(host, host), port), timeout=timeout) as sock:
            if proxies is not None:
                # The address the server sees us come from.
                source = settings.parse_client_ip(sock.getsockname()[0])
                if any(source in network for network in proxies):
                    sock.sendall(_PROXY_LOCAL)
            # A reply, or an empty read when the server closed.
            sock.recv(1)
    except OSError:
        return False
    return True


def main() -> int:
    proxies = settings.PYMTA_TRUSTED_PROXIES if settings.PYMTA_ENABLE_PROXY_PROTOCOL else None
    hosts = settings.PYMTA_SMTP_BIND_HOSTS
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        alive = all(
            pool.map(
                lambda host: answers(host, settings.PYMTA_SMTP_BIND_PORT, proxies=proxies), hosts
            )
        )
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
