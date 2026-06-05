"""Connection-level admission control for the pymta server.

The :class:`IPGate` enforces three ceilings on inbound TCP sessions:

* a process-wide cap, defending against a generic flood;
* a per-IP concurrent cap, defending against a single remote opening thousands
  of half-idle connections (aiosmtpd does not enforce any per-IP cap);
* a per-IP new-session rate cap (rolling 60s window), defending against fast
  open/close churn from one IP that never exceeds the concurrent cap but
  still costs CPU/TLS handshakes/MDA RCPT checks.

All caps are skipped when set to 0, matching the existing Postfix default
(``smtpd_client_event_limit_exceptions = static:all``) — useful in dev/test
where the whole load comes from the same loopback address.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import metrics

logger = logging.getLogger(__name__)


# Rolling window used by the per-IP rate cap.
_RATE_WINDOW_SECONDS = 60.0
# Opportunistic prune cadence for the rate-tracking dict: walk and drop
# expired entries every Nth acquire. Bounds memory under PROXY-protocol with
# many distinct client IPs (each entry would otherwise live one full window
# beyond its last use).
_RATE_PRUNE_EVERY = 1000


class TooManyConnections(Exception):
    """Raised when the global, per-IP concurrent, or per-IP rate cap is hit."""

    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope


class IPGate:
    """Tracks live SMTP sessions per remote IP and globally.

    Acquisition does not block: if any cap is reached we raise immediately
    so the caller can close the socket and reply ``421`` instead of holding
    the connection open and amplifying the attack.

    The ``_try_acquire`` / ``_release`` pair is called from
    :class:`pymta.smtp_protocol.HardenedSMTP` (post-PROXY when applicable).
    """

    def __init__(
        self,
        *,
        max_total: int,
        max_per_ip: int,
        max_per_ip_per_minute: int = 0,
        clock=time.monotonic,
    ):
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self.max_per_ip_per_minute = max_per_ip_per_minute
        self._clock = clock
        self._lock = asyncio.Lock()
        self._per_ip: dict[str, int] = {}
        self._total = 0
        # (count_in_window, window_start_monotonic) per IP
        self._rate_per_ip: dict[str, tuple[int, float]] = {}
        self._acquires_since_prune = 0

    async def _try_acquire(self, ip: str) -> None:
        async with self._lock:
            if self.max_total and self._total >= self.max_total:
                raise TooManyConnections("global")
            if self.max_per_ip and self._per_ip.get(ip, 0) >= self.max_per_ip:
                raise TooManyConnections("per_ip")
            if self.max_per_ip_per_minute:
                now = self._clock()
                count, start = self._rate_per_ip.get(ip, (0, now))
                if now - start >= _RATE_WINDOW_SECONDS:
                    count, start = 0, now
                if count >= self.max_per_ip_per_minute:
                    raise TooManyConnections("per_ip_rate")
                self._rate_per_ip[ip] = (count + 1, start)
                self._acquires_since_prune += 1
                if self._acquires_since_prune >= _RATE_PRUNE_EVERY:
                    self._prune_expired_rates(now)
                    self._acquires_since_prune = 0
            self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
            self._total += 1
            metrics.SESSIONS_ACTIVE.inc()
            metrics.SESSIONS_PER_IP.set(len(self._per_ip))

    async def _release(self, ip: str) -> None:
        async with self._lock:
            new = self._per_ip.get(ip, 0) - 1
            if new <= 0:
                self._per_ip.pop(ip, None)
            else:
                self._per_ip[ip] = new
            self._total = max(0, self._total - 1)
            metrics.SESSIONS_ACTIVE.dec()
            metrics.SESSIONS_PER_IP.set(len(self._per_ip))

    def _prune_expired_rates(self, now: float) -> None:
        # Drop IPs whose window has fully elapsed; keeps the rate dict bounded
        # to the set of IPs seen in roughly the last minute.
        expired = [
            ip
            for ip, (_count, start) in self._rate_per_ip.items()
            if now - start >= _RATE_WINDOW_SECONDS
        ]
        for ip in expired:
            del self._rate_per_ip[ip]
