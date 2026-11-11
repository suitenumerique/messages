"""Connection-level admission control for the pymta server.

The :class:`IPGate` enforces two things, each on two axes:

* **TCP sessions** — a process-wide cap (``PYMTA_MAX_SESSIONS_TOTAL``) against a
  generic flood, and each source's share of it against a single remote opening
  thousands of half-idle connections.
* **DATA phases** — a process-wide cap (``PYMTA_MAX_CONCURRENT_DATA``) and each
  source's share of that. This is the one that bounds memory: every message
  is held in RAM, so messages in flight times the size cap is the heap.

A source is an IPv4 address or an IPv6 /64. Its share is at most
``PYMTA_MAX_SHARE_PER_SOURCE`` of the slots (0.1 by default, so filling them
takes ten sources), and less once more sources than that are present: see
:meth:`IPGate._share`.

A cap of 0 disables the *refusal* on that axis, matching the shipped Postfix
config (``smtpd_client_event_limit_exceptions = static:all``) and useful in
dev/test where the whole load comes from one loopback address. Slots are still
counted when disabled, so the gauges stay honest and acquire/release stay
symmetric if the limit changes in between; see :meth:`IPGate.acquire_data`.

The axes are not independent at zero: ``PYMTA_MAX_SESSIONS_TOTAL`` is derived
as ``PYMTA_MAX_CONCURRENT_DATA * 3``, so a zero DATA cap disables both refusals
and one host can hold unlimited sessions.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time

from . import metrics, settings

logger = logging.getLogger(__name__)


def source_key(ip: str) -> str:
    """The bucket a client address is counted in for the per-source limits.

    IPv4 by address. IPv6 by /64: that is what one site or one host is
    assigned, so counting by full address would give a single machine 2^64
    buckets and leave it bound by the global caps only. Anything that does not
    parse (the "unknown" bucket) is returned unchanged.
    """
    addr = settings.parse_client_ip(ip)
    if addr is None:
        return ip
    if addr.version == 4:
        return str(addr)
    return str(ipaddress.ip_network((addr, 64), strict=False))


class TooManyConnections(Exception):
    """Raised when the global cap or a source's share of it is reached."""

    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope


class LogThrottle:
    """Lets one event through per key per ``interval`` seconds.

    For a log line a peer can provoke once per connection: the first one from
    an address is written, the rest of the interval is only counted, and the
    next line carries that count.

    ``max_keys`` bounds both the memory and what a spray of sources can write:
    once that many addresses are inside their interval, a new one gets nothing.
    """

    def __init__(self, interval: float, max_keys: int):
        self.interval = interval
        self.max_keys = max_keys
        # key -> [time of the last pass, events held back since]
        self._seen: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> int | None:
        """How many events were held back since the last pass, or None to hold this one."""
        now = time.monotonic() if now is None else now
        entry = self._seen.get(key)
        if entry is not None and now - entry[0] < self.interval:
            entry[1] += 1
            return None
        if entry is None and len(self._seen) >= self.max_keys:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.interval}
            if len(self._seen) >= self.max_keys:
                return None
        self._seen[key] = [now, 0]
        return int(entry[1]) if entry else 0


class IPGate:
    """Tracks live SMTP sessions per remote IP and globally.

    Acquisition does not block: if any cap is reached we raise immediately
    so the caller can close the socket and reply ``421`` instead of holding
    the connection open and amplifying the attack. The caller may first free
    a slot by closing an idle session of a source over its share.

    ``acquire`` / ``release`` are called by
    :class:`pymta.smtp_protocol.SMTPConnection`, once per connection, with the
    PROXY source when there is one. No lock: everything here runs on the
    event loop and nothing in it waits.
    """

    def __init__(
        self,
        *,
        max_total: int | None = None,
        max_data: int | None = None,
        max_share: float | None = None,
    ):
        self._max_data = max_data
        self._max_share = max_share
        self._data_total = 0
        self._data_per_ip: dict[str, int] = {}
        # None means "read the setting on every acquire", so a cap can be
        # moved under a running process. Safe because a cap is only
        # ever compared against a counter; it is not part of the bookkeeping,
        # so lowering one mid-flight just refuses new sessions until the live
        # count falls back under it. Tests pass explicit values so they do not
        # depend on the ambient environment.
        self._max_total = max_total
        self._per_ip: dict[str, int] = {}
        self._total = 0

    def _cap(self, override: float | None, name: str) -> float:
        return getattr(settings, name) if override is None else override

    def _share(self, total: int, sources: int) -> int:
        """Slots one source may hold out of ``total``, with ``sources`` present.

        The smaller of two bounds, and never less than one:

        * ``PYMTA_MAX_SHARE_PER_SOURCE`` of the slots, a fraction. No source
          gets more than this, so filling the slots takes at least one over
          it sources: ten at the default of 0.1 (eleven in practice).
        * an equal share among the sources present plus one, which is the
          smaller of the two once more sources than that are here. The
          ``+ 1`` keeps a share for a source that has not arrived yet.

        Checked when a slot is asked for, so sources that arrived first can
        be over a share that has shrunk since. A session slot is taken back
        from them when every slot is taken and a source under its share asks
        for one: see :meth:`crowding_sources`. DATA slots are not taken back.
        """
        fraction = self._cap(self._max_share, "PYMTA_MAX_SHARE_PER_SOURCE")
        # The epsilon: 0.29 * 100 is 28.999999999999996 in floating point.
        ceiling = math.floor(total * fraction + 1e-9)
        return max(1, min(ceiling, total // (sources + 1)))

    @property
    def max_total(self) -> int:
        return self._cap(self._max_total, "PYMTA_MAX_SESSIONS_TOTAL")

    def under_stress(self) -> bool:
        """True once four fifths of the session slots are taken.

        Postfix's stress mode switches on when every process is busy; this
        switches on a little before, so the short idle timeout it enables has
        freed slots by the time the cap would refuse a sender.
        """
        cap = self.max_total
        return bool(cap) and self._total * 5 >= cap * 4

    def acquire(self, ip: str) -> None:
        """Take a session slot for ``ip``, or raise :class:`TooManyConnections`."""
        if self.max_total and self._total >= self.max_total:
            raise TooManyConnections("global")
        share = self._share(self.max_total, len(self._per_ip))
        if self.max_total and self._per_ip.get(ip, 0) >= share:
            raise TooManyConnections("per_ip")
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        self._total += 1
        metrics.SESSIONS_ACTIVE.inc()
        metrics.SESSIONS_PER_IP.set(len(self._per_ip))

    def crowding_sources(self, ip: str) -> list[str]:
        """The sources ``ip`` may take a session slot from, the largest first.

        For when :meth:`acquire` has refused ``ip`` because every slot is
        taken: the sources holding more than a share, as ``acquire`` would
        count it for ``ip``. Empty when ``ip`` holds its own share already.
        Once a slot of one of them is released, ``acquire(ip)`` succeeds.

        Each slot taken back leaves its source at the share or above, so a
        source cannot take back what it has just lost.
        """
        share = self._share(self.max_total, len(self._per_ip))
        if self._per_ip.get(ip, 0) >= share:
            return []
        crowding = [source for source, held in self._per_ip.items() if held > share]
        return sorted(crowding, key=self._per_ip.__getitem__, reverse=True)

    def release(self, ip: str) -> None:
        """Give back a slot taken by :meth:`acquire`."""
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
        acquire and release symmetric: the limit is read at each use, so a
        phase can begin under one value and end under another, and a
        release that consulted the *current* limit would either skip a
        decrement it owed or make one it never earned.
        """
        total = self.max_data
        key = ip or "unknown"
        if total:
            if self._data_total >= total:
                return False
            if self._data_per_ip.get(key, 0) >= self._share(total, len(self._data_per_ip)):
                return False
        self._data_total += 1
        self._data_per_ip[key] = self._data_per_ip.get(key, 0) + 1
        metrics.DATA_PHASES_ACTIVE.inc()
        return True

    def release_data(self, ip: str | None) -> None:
        """Give back a slot taken by :meth:`acquire_data`.

        Unconditional by design; see the note there on a limit that moved.
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
