"""Connection-level admission control for the pymta server.

The :class:`IPGate` enforces two ceilings on inbound TCP sessions:

* a process-wide cap, defending against a generic flood;
* a per-IP cap, defending against a single remote opening thousands of
  half-idle connections (aiosmtpd does not enforce any per-IP cap).

Both caps are skipped when set to 0, matching the existing Postfix default
(``smtpd_client_event_limit_exceptions = static:all``) — useful in dev/test
where the whole load comes from the same loopback address.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class TooManyConnections(Exception):
    """Raised when the global or per-IP cap is hit."""

    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope


class IPGate:
    """Tracks live SMTP sessions per remote IP and globally.

    Acquisition does not block: if the cap is reached we raise immediately
    so the caller can close the socket and reply ``421`` instead of holding
    the connection open and amplifying the attack.

    The ``_try_acquire`` / ``_release`` pair is called from
    :class:`pymta.smtp_protocol.HardenedSMTP` (post-PROXY when applicable).
    """

    def __init__(self, *, max_total: int, max_per_ip: int):
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self._lock = asyncio.Lock()
        self._per_ip: dict[str, int] = {}
        self._total = 0

    async def _try_acquire(self, ip: str) -> None:
        async with self._lock:
            if self.max_total and self._total >= self.max_total:
                raise TooManyConnections("global")
            if self.max_per_ip and self._per_ip.get(ip, 0) >= self.max_per_ip:
                raise TooManyConnections("per_ip")
            self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
            self._total += 1

    async def _release(self, ip: str) -> None:
        async with self._lock:
            new = self._per_ip.get(ip, 0) - 1
            if new <= 0:
                self._per_ip.pop(ip, None)
            else:
                self._per_ip[ip] = new
            self._total = max(0, self._total - 1)
