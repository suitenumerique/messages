"""Admission control for the pymta server.

:class:`IPGate` caps two things, each in total and per source:

* TCP sessions: ``PYMTA_MAX_SESSIONS_TOTAL`` against a flood, and each
  source's share of it against one remote opening thousands of idle
  connections.
* DATA phases: ``PYMTA_MAX_CONCURRENT_DATA`` and each source's share of it.
  This one bounds memory: messages are held in RAM, so messages in flight
  times the size cap is the heap.

A source is an IPv4 address or an IPv6 /64. Its share is at most
``PYMTA_MAX_SHARE_PER_SOURCE`` of the slots (0.1 by default, so filling them
takes ten sources), and less once more sources than that are present: see
:meth:`IPGate._share`.

A cap of 0 switches the refusals of that kind off, as the shipped Postfix
config does (``smtpd_client_event_limit_exceptions = static:all``). That suits
dev and tests, where all the load comes from one loopback address. Slots are
counted all the same: see :meth:`IPGate.acquire_data`.

``PYMTA_MAX_SESSIONS_TOTAL`` defaults to ``PYMTA_MAX_CONCURRENT_DATA * 3``, so
a zero DATA cap switches both off and one host can hold unlimited sessions.
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


# Seconds over which PYMTA_MAX_RCPT_MISSES_PER_SOURCE is counted.
MISS_WINDOW = 600.0


class MissBudget:
    """Unknown recipients each source has named, over all its sessions.

    A source that names ``PYMTA_MAX_RCPT_MISSES_PER_SOURCE`` of them within
    ``MISS_WINDOW`` seconds of its first is refused at connect until that
    window ends. What a session may collect is bounded by its own budget, but
    a peer that reconnects gets a new session: this is what bounds an address
    harvest over time.

    ``max_sources`` bounds the memory. With that many sources inside their
    window, a new one is not counted: a peer with that many addresses is not
    held by a per-source limit anyway.
    """

    def __init__(self, limit: int | None = None, *, max_sources: int = 65536):
        # None: the setting, read at each use. Tests pass explicit values.
        self._limit = limit
        self._max_sources = max_sources
        # source -> [end of its window, misses in it]
        self._seen: dict[str, list[float]] = {}

    @property
    def limit(self) -> int:
        if self._limit is None:
            return settings.PYMTA_MAX_RCPT_MISSES_PER_SOURCE
        return self._limit

    def record(self, source: str, now: float | None = None) -> None:
        """Count one unknown recipient named by ``source``."""
        now = time.monotonic() if now is None else now
        entry = self._seen.get(source)
        if entry is not None and now < entry[0]:
            entry[1] += 1
            return
        if entry is None and len(self._seen) >= self._max_sources:
            self._seen = {key: value for key, value in self._seen.items() if now < value[0]}
            if len(self._seen) >= self._max_sources:
                return
        self._seen[source] = [now + MISS_WINDOW, 1]

    def spent(self, source: str, now: float | None = None) -> bool:
        """True while ``source`` is to be refused."""
        limit = self.limit
        entry = self._seen.get(source)
        if not limit or entry is None:
            return False
        now = time.monotonic() if now is None else now
        return now < entry[0] and entry[1] >= limit


class IPGate:
    """Counts SMTP sessions and DATA phases, in total and per source.

    Nothing here waits: at a cap ``acquire`` raises, and the caller answers
    ``421`` and closes instead of holding the connection open. The caller may
    first free a slot by closing an idle session of a source over its share.

    ``acquire`` and ``release`` are called once per connection by
    :class:`pymta.smtp_protocol.SMTPConnection`, with the PROXY source when
    there is one. No lock: everything runs on the event loop.
    """

    def __init__(
        self,
        *,
        max_total: int | None = None,
        max_data: int | None = None,
        max_share: float | None = None,
        max_misses: int | None = None,
    ):
        # None: the setting, read at each use. Tests pass explicit values.
        self._max_total = max_total
        self._max_data = max_data
        self._max_share = max_share
        # Unknown recipients per source, across sessions.
        self.misses = MissBudget(max_misses)
        self._data_total = 0
        self._data_per_ip: dict[str, int] = {}
        self._per_ip: dict[str, int] = {}
        self._total = 0
        # The connections holding a session slot. Each adds and removes itself.
        self.sessions: set = set()

    @property
    def total(self) -> int:
        """Session slots taken."""
        return self._total

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
        fraction = self._max_share
        if fraction is None:
            fraction = settings.PYMTA_MAX_SHARE_PER_SOURCE
        # The epsilon: 0.29 * 100 is 28.999999999999996 in floating point.
        ceiling = math.floor(total * fraction + 1e-9)
        return max(1, min(ceiling, total // (sources + 1)))

    @property
    def max_total(self) -> int:
        if self._max_total is None:
            return settings.PYMTA_MAX_SESSIONS_TOTAL
        return self._max_total

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
        if self._max_data is None:
            return settings.PYMTA_MAX_CONCURRENT_DATA
        return self._max_data

    def data_under_stress(self) -> bool:
        """True once four fifths of the DATA slots are taken."""
        cap = self.max_data
        return bool(cap) and self._data_total * 5 >= cap * 4

    def acquire_data(self, ip: str | None) -> bool:
        """Take a slot for a message about to be held in memory.

        No waiting: no slot means 451, and the sender retries. A queue of
        waiters would be the denial of service the limit is there to prevent.

        Two limits: the total bounds memory, and the per-source share stops
        one host from holding every slot for a whole PYMTA_DATA_TIMEOUT by
        sending its bodies slowly.

        Slots are counted even with the limit off; only the refusal depends
        on it, so a release never has to look at the limit.
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
        """Give back a slot taken by :meth:`acquire_data`, whatever the limit is now."""
        key = ip or "unknown"
        remaining = self._data_per_ip.get(key, 0) - 1
        if remaining > 0:
            self._data_per_ip[key] = remaining
        else:
            # Removed at zero: keyed by source, the dict would grow without bound.
            self._data_per_ip.pop(key, None)
        self._data_total = max(0, self._data_total - 1)
        metrics.DATA_PHASES_ACTIVE.dec()
