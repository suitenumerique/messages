"""Connection-level admission control for the pymta server.

The :class:`IPGate` enforces two things, each on two axes:

* **TCP sessions** — a process-wide cap (``PYMTA_MAX_SESSIONS_TOTAL``) against a
  generic flood, and each source's share of it against a single remote opening
  thousands of half-idle connections. aiosmtpd enforces neither.
* **DATA phases** — a process-wide cap (``PYMTA_MAX_CONCURRENT_DATA``) and each
  source's share of that. This is the one that bounds memory: aiosmtpd holds
  every message in RAM, so messages in flight times the size cap is the heap.

Neither share is a configured number. Both come from :meth:`IPGate._fair_share`,
which divides by the sources present rather than by a constant, so filling the
slots takes as many hosts as there are slots.

A cap of 0 disables the *refusal* on that axis, matching the shipped Postfix
config (``smtpd_client_event_limit_exceptions = static:all``) and useful in
dev/test where the whole load comes from one loopback address. Slots are still
counted when disabled, so the gauges stay honest and acquire/release stay
symmetric across a SIGHUP; see :meth:`IPGate.acquire_data`.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import metrics, settings

logger = logging.getLogger(__name__)


class TooManyConnections(Exception):
    """Raised when the global cap or a source's share of it is reached."""

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
        max_total: int | None = None,
        max_data: int | None = None,
        clock=time.monotonic,
    ):
        self._max_data = max_data
        self._data_total = 0
        self._data_per_ip: dict[str, int] = {}
        # None means "read the setting on every acquire", which is what lets
        # SIGHUP move these under a running process. Safe because a cap is only
        # ever compared against a counter; it is not part of the bookkeeping,
        # so lowering one mid-flight just refuses new sessions until the live
        # count falls back under it. Tests pass explicit values so they do not
        # depend on the ambient environment.
        self._max_total = max_total
        self._clock = clock
        self._lock = asyncio.Lock()
        self._per_ip: dict[str, int] = {}
        self._total = 0

    def _cap(self, override: int | None, name: str) -> int:
        return getattr(settings, name) if override is None else override

    @staticmethod
    def _fair_share(total: int, sources: int) -> int:
        """Slots one source may hold: an equal share, keeping one share spare.

        A fixed per-source cap divides the slots by a constant, which decides in
        advance how few hosts can take everything: 20 slots at 5 each is four
        hosts and no amount of tuning changes the shape. Sharing by the number
        of sources actually present removes that ceiling. Filling the slots then
        needs as many hosts as there are slots, which is the most any scheme can
        ask for without evicting work already in progress.

        The ``+ 1`` reserves a share for a source that has not arrived yet, so a
        newcomer is never locked out by the hosts already here. It also means a
        lone sender gets half rather than everything, which is the difference
        between one host delaying the others and one host excluding them.

        Self-adjusting in the useful direction: one sender alone gets a large
        share and is not throttled for company that never came, and the share
        shrinks as contention appears.
        """
        return max(1, total // (sources + 1))

    @property
    def max_total(self) -> int:
        return self._cap(self._max_total, "PYMTA_MAX_SESSIONS_TOTAL")

    async def _try_acquire(self, ip: str) -> None:
        async with self._lock:
            if self.max_total and self._total >= self.max_total:
                raise TooManyConnections("global")
            share = self._fair_share(self.max_total, len(self._per_ip))
            if self.max_total and self._per_ip.get(ip, 0) >= share:
                raise TooManyConnections("per_ip")
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

    @property
    def max_data(self) -> int:
        return self._cap(self._max_data, "PYMTA_MAX_CONCURRENT_DATA")

    def acquire_data(self, ip: str | None) -> bool:
        """Take a slot for a message about to be held in memory.

        Synchronous and immediate: no slot means 451 rather than a queue. A
        queue of waiters is the same denial of service the slot limit exists to
        prevent, rearranged, and the sender retries anyway.

        Two ceilings because they stop different things. The total is memory:
        messages in flight times the size cap is the heap. The per-IP one is
        monopolisation, which the total alone does not prevent, since one host
        can hold every slot for a whole PYMTA_DATA_TIMEOUT by dribbling bodies.

        Slots are counted even when the limit is disabled, and only the refusal
        is conditional. Counting on the same terms in both modes is what keeps
        acquire and release symmetric across a SIGHUP: the limit is reloadable,
        so a phase can begin under one value and end under another, and a
        release that consulted the *current* limit would either skip a
        decrement it owed or make one it never earned.
        """
        total = self.max_data
        key = ip or "unknown"
        if total:
            if self._data_total >= total:
                return False
            if self._data_per_ip.get(key, 0) >= self._fair_share(total, len(self._data_per_ip)):
                return False
        self._data_total += 1
        self._data_per_ip[key] = self._data_per_ip.get(key, 0) + 1
        metrics.DATA_PHASES_ACTIVE.inc()
        return True

    def release_data(self, ip: str | None) -> None:
        """Give back a slot taken by :meth:`acquire_data`.

        Unconditional by design; see the note there on reloads.
        """
        key = ip or "unknown"
        remaining = self._data_per_ip.get(key, 0) - 1
        if remaining > 0:
            self._data_per_ip[key] = remaining
        else:
            # Popped rather than left at zero: keyed by peer address, so spent
            # entries would grow the dict without bound under a spray of sources.
            self._data_per_ip.pop(key, None)
        self._data_total = max(0, self._data_total - 1)
        metrics.DATA_PHASES_ACTIVE.dec()
