"""Hardened :class:`aiosmtpd.smtp.SMTP` subclass.

aiosmtpd's defaults are reasonable but its surface area still includes a few
verbs we never want exposed on a public, inbound-only port-25 endpoint:

* ``AUTH``: never offered (no authenticator wired) but we still reply 502 to
  reject any attempt explicitly, so a misconfiguration cannot quietly become a
  relay.
* ``VRFY``: RFC 5321 §3.5 lets us respond with a canned 252; we do so
  unconditionally to prevent address enumeration.
* ``EXPN``: explicit 502.

We also fold connection-admission control into :meth:`_handle_client`. The
gate is checked exactly once per accepted TCP session. When PROXY-protocol is
enabled, the check is deferred to the ``handle_PROXY`` hook so it sees the
real client address rather than the load-balancer's IP.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import time

from aiosmtpd.smtp import SMTP as BaseSMTP
from aiosmtpd.smtp import syntax

from . import metrics, runtime, settings
from .limits import IPGate, TooManyConnections

logger = logging.getLogger(__name__)


def _ip_of(peer) -> str | None:
    """Address out of an aiosmtpd peer tuple, so the field is comparable.

    Logging the raw ``('10.0.0.1', 51234)`` would make ``client_ip`` a different
    string on every connection from the same host, which is useless to group by.
    """
    return str(peer[0]) if peer else None


class HardenedSMTP(BaseSMTP):
    """SMTP subclass that locks down VRFY/EXPN/AUTH and applies the IP gate.

    Three of aiosmtpd's protections are class attributes rather than constructor
    arguments, so they are invisible in ``build_smtp_kwargs``. Recorded here
    because they matter and nothing else in the tree mentions them:

    * ``line_length_limit`` — RFC 5321 §4.5.3.1.6, aiosmtpd's 1001 replaced by
      ``PYMTA_MAX_LINE_LENGTH`` below. Enforced as the ``StreamReader`` limit,
      so an endless line is refused at the transport instead of buffering.
    * ``command_size_limit = 512`` — RFC 5321 §4.5.3.1.4, per command line.
      Inherited, and checked after the line is read, so it caps commands
      independently of how high ``line_length_limit`` goes.
    * ``BOGUS_LIMIT = 5`` (module constant) — unrecognised commands per session
      before aiosmtpd hangs up. Narrower and stricter than
      ``PYMTA_MAX_ERRORS_PER_SESSION``, which counts every 4xx/5xx we emit.
      Inherited.

    ``local_part_limit`` is aiosmtpd's own address check and stays at its
    default of 0 (disabled) on purpose: :mod:`pymta.address` enforces the same
    RFC limit, with a rejection reason and a metric label attached.

    ``line_length_limit`` is the one we override. It has to be a class attribute
    because ``SMTP.__init__`` reads it while constructing the ``StreamReader``,
    before any instance attribute of ours could exist.
    """

    line_length_limit = settings.PYMTA_MAX_LINE_LENGTH

    def __init__(self, *args, ip_gate: IPGate | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # Correlation id for every line this connection produces. Without one,
        # a log is a pile of independent facts: "mda_check_no_verdict" tells you
        # something went wrong and nothing about whose mail it was. Minted on
        # the protocol instance rather than the session so it survives the
        # STARTTLS rebuild, the same reason the abuse counters live there.
        #
        # Random rather than sequential: a counter would leak the server's
        # total traffic to anyone who connects twice.
        self.session_id = secrets.token_hex(4)
        self._ip_gate: IPGate | None = ip_gate
        # Track whether we are currently holding a slot in the gate so the
        # release path runs at most once.
        self._gate_held_ip: str | None = None
        self._gate_started: float | None = None
        # Set by request_disconnect(); consumed by push() once the reply that
        # announced the disconnect is on the wire.
        self._disconnect_after_reply = False
        # Monotonic timestamp of the 354 that opened the current DATA phase,
        # or None outside DATA. Read by the handler to size its own deadline,
        # together with the budget the transport was actually armed at.
        self.data_phase_started: float | None = None
        self.data_phase_budget: float | None = None
        # One-shot timer for the whole-session deadline. Armed on connect,
        # never re-armed. See _arm_session_deadline.
        self._session_deadline_handle = None

    # ----------------------------------------------------------- verb lockdown
    async def smtp_VRFY(self, arg: str) -> None:
        await self.push("252 2.1.5 Cannot VRFY user; try RCPT to verify")

    async def smtp_EXPN(self, arg: str) -> None:
        await self.push("502 5.7.0 EXPN disabled")

    async def smtp_AUTH(self, arg: str) -> None:
        # AUTH is never advertised (no authenticator wired, auth_require_tls
        # defaults to True), but reply explicitly anyway so a misconfigured
        # scanner cannot mistake an absent reply for acceptance.
        await self.push("502 5.7.0 AUTH not supported on inbound port 25")

    async def smtp_HELP(self, arg: str) -> None:
        # Default aiosmtpd HELP enumerates implemented verbs (mild info leak).
        await self.push("214 2.0.0 See https://www.rfc-editor.org/rfc/rfc5321")

    # ------------------------------------------------------ session deadline
    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        self._arm_session_deadline()

    def connection_lost(self, error) -> None:
        if self._session_deadline_handle is not None:
            self._session_deadline_handle.cancel()
            self._session_deadline_handle = None
        super().connection_lost(error)

    def _arm_session_deadline(self) -> None:
        """Start the one bound a peer cannot push back by staying busy.

        Every other deadline is reset by peer activity: aiosmtpd re-arms its
        idle timer on each accepted command line, so a peer that sends one
        command just under ``PYMTA_COMMAND_TIMEOUT`` keeps the session alive
        for as long as ``command_call_limit`` lets it issue commands, which is
        many hours on the default budgets.

        Armed once per connection. Not re-armed on the STARTTLS transport swap
        either: aiosmtpd calls ``connection_made`` a second time there, and
        resetting the deadline there would give the peer a second full budget.
        """
        if self._session_deadline_handle is not None:
            return
        limit = settings.PYMTA_SESSION_TIMEOUT
        if limit <= 0:
            return
        self._session_deadline_handle = self.loop.call_later(limit, self._session_expired)

    def _session_expired(self) -> None:
        self._session_deadline_handle = None
        peer = getattr(self.session, "peer", None) if self.session else None
        logger.debug(
            "session_timeout",
            extra={
                "session_id": self.session_id,
                "client_ip": _ip_of(peer),
                "limit_seconds": settings.PYMTA_SESSION_TIMEOUT,
            },
        )
        metrics.SECURITY_REJECTIONS.labels(reason="session_timeout").inc()
        metrics.DISCONNECTS_421.labels(reason="session_timeout").inc()
        # Announce before hanging up so the sender defers and retries rather
        # than reading a bare reset as a hard failure. Runs as a task because
        # push() is async and we are in a timer callback; writing from here is
        # safe even mid-DATA (StreamWriter.write only appends to a buffer) and
        # the connection is ending either way.
        # The task is not retained: nothing awaits it, and the transport close
        # it performs is what ends the connection.
        self.loop.create_task(
            self._close_with_notice("421 4.4.2 Session too long, closing connection")
        )

    def _timeout_cb(self) -> None:
        """Announce the idle timeout before hanging up.

        aiosmtpd's own callback closes the transport and says nothing, so the
        peer gets a bare TCP close and cannot tell a policy timeout from a
        network fault. Postfix answers 421 here, and so does our session
        deadline above, so this is the odd one out. Saying it also gives the
        timeout a metric, which it otherwise had no way to record.
        """
        peer = getattr(self.session, "peer", None) if self.session else None
        logger.debug(
            "command_timeout",
            extra={
                "session_id": self.session_id,
                "client_ip": _ip_of(peer),
                "limit_seconds": settings.PYMTA_COMMAND_TIMEOUT,
            },
        )
        metrics.SECURITY_REJECTIONS.labels(reason="command_timeout").inc()
        metrics.DISCONNECTS_421.labels(reason="command_timeout").inc()
        self.loop.create_task(
            self._close_with_notice("421 4.4.2 Idle timeout, closing connection")
        )

    async def _close_with_notice(self, reply: str) -> None:
        with contextlib.suppress(OSError, ConnectionError):
            await self.push(reply)
        if self.transport is not None:
            self.transport.close()

    # ------------------------------------------------------- forced disconnect
    def request_disconnect(self) -> None:
        """Close the connection once the reply now being sent has gone out.

        aiosmtpd pushes whatever a handler hook returns and then loops back for
        the next command. A ``421 ... goodbye`` from ``handle_RCPT`` is only a
        string to it, so without this the session stays open and an enumerator
        keeps probing until the much coarser per-verb ``command_call_limit``
        closes it. Closing from :meth:`push` rather than here means the 421 is
        on the wire before the FIN, so the peer gets a reason.
        """
        self._disconnect_after_reply = True

    async def push(self, status) -> None:
        await super().push(status)
        if self._disconnect_after_reply:
            self._disconnect_after_reply = False
            if self.transport is not None:
                self.transport.close()

    async def handle_exception(self, error: Exception) -> str:
        # The handler answers 421 ("closing transmission channel"); make that
        # true. aiosmtpd would otherwise push it and carry on with a session
        # whose state we no longer trust.
        status = await super().handle_exception(error)
        self.request_disconnect()
        return status

    # ------------------------------------------------------------ DATA budget
    @syntax("DATA")
    async def smtp_DATA(self, arg: str) -> None:
        """Run the DATA phase under a single total deadline.

        aiosmtpd arms its idle timer when a command line is dispatched and does
        not re-arm it while the handler runs, so the whole of DATA (body
        receive *and* the MDA deliver call) is charged to one
        ``PYMTA_COMMAND_TIMEOUT``. That conflates two very different budgets:
        120 s is right for "peer went quiet at the command prompt" and too
        tight for a 10 MB body plus a slow MDA, which would be torn down
        mid-handler with no reply at all.

        Swap in the DATA budget for the duration. The transport is armed at
        exactly ``PYMTA_DATA_TIMEOUT``, so nothing in a DATA phase outlives it;
        the handler takes its reply reserve out of the same budget so it can
        answer 451 first.
        """
        # Before the 354, so a refused peer never starts uploading and the
        # bytes are never allocated. Held until the handler returns, because the
        # body stays in memory for the whole MDA deliver call, not just while it
        # is being received.
        if self._ip_gate is not None and not self._ip_gate.acquire_data(self._gate_held_ip):
            metrics.SECURITY_REJECTIONS.labels(reason="max_concurrent_data").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            logger.debug(
                "data_slots_exhausted",
                extra={
                    "session_id": self.session_id,
                    "client_ip": _ip_of(getattr(self.session, "peer", None)),
                    "limit": self._ip_gate.max_data,
                },
            )
            await self.push("451 4.3.1 Insufficient resources, please retry")
            return

        # Read once and kept for the phase. PYMTA_DATA_TIMEOUT is reloadable, so
        # a SIGHUP between here and the deliver call would otherwise have the
        # handler size its budget against a value the transport was never armed
        # at: raise the setting mid-phase and it computes a budget longer than
        # the deadline already ticking, spending the reply reserve that exists
        # to answer 451 before the transport tears the session down.
        self.data_phase_started = time.monotonic()
        self.data_phase_budget = float(settings.PYMTA_DATA_TIMEOUT)
        self._reset_timeout(self.data_phase_budget)
        try:
            await super().smtp_DATA(arg)
        finally:
            self.data_phase_started = None
            self.data_phase_budget = None
            if self._ip_gate is not None:
                self._ip_gate.release_data(self._gate_held_ip)
            if self.transport is not None:
                self._reset_timeout()

    # ------------------------------------------------------------ gate wiring
    async def _handle_client(self) -> None:
        """Wrap aiosmtpd's per-connection dialogue with admission control.

        Two paths:

        * **No PROXY protocol**: the immediate TCP peer is the real client,
          so we gate before the SMTP dialogue starts.
        * **PROXY protocol enabled**: the gate is deferred to
          :meth:`acquire_gate_post_proxy`, called from the handler's
          ``handle_PROXY`` hook once the real client IP has been parsed off
          the PROXY header.
        """
        if self._ip_gate is not None and self._proxy_timeout is None:
            if not await self._acquire_gate(self._wire_peer_ip()):
                return
        try:
            await super()._handle_client()
        finally:
            await self._release_gate()

    async def acquire_gate_post_proxy(self, real_ip: str) -> bool:
        """Acquire the gate using the IP parsed from a PROXY-protocol header.

        Called from :meth:`pymta.handler.InboundHandler.handle_PROXY`. Returns
        ``True`` on success; on refusal sends 421 and closes the socket.
        """
        if self._ip_gate is None:
            return True
        return await self._acquire_gate(real_ip)

    def _is_blocked(self, ip: str) -> bool:
        if not settings.PYMTA_BLOCKED_NETWORKS:
            return False
        addr = settings.parse_client_ip(ip)
        if addr is None:
            # The "unknown" bucket, or a PROXY header claiming something
            # unparseable. Not matchable against a network, so not blockable;
            # the caps still apply to it.
            return False
        return any(addr in net for net in settings.PYMTA_BLOCKED_NETWORKS)

    async def _acquire_gate(self, ip: str) -> bool:
        assert self._ip_gate is not None  # noqa: S101 (narrowing only; checked above)
        # Before the caps and before the greeting: a blocked peer should cost us
        # one reply and a close. Sits here rather than in _handle_client so both
        # topologies get it from one place, with the address already resolved
        # (the PROXY source when that is enabled, the TCP peer when it is not).
        if runtime.is_draining():
            # Out of rotation, either because PYMTA_DRAIN says so or because a
            # SIGTERM started the shutdown. RFC 5321 §3.1 allows answering
            # with 421 instead of 220, and §5.1 has the sender move to the next
            # MX in preference order; Postfix and Exim both treat it as a
            # per-host defer, not a per-domain one. 4.3.2 is RFC 3463's "system
            # not accepting network messages", which is precisely this.
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_drain").inc()
            with contextlib.suppress(OSError, ConnectionError):
                await self.push("421 4.3.2 Service not available, try another MX")
                if self._writer is not None:
                    await self._writer.drain()
            if self.transport is not None:
                self.transport.close()
            return False
        if self._is_blocked(ip):
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_blocked").inc()
            metrics.SECURITY_REJECTIONS.labels(reason="blocked_network").inc()
            logger.debug(
                "connection_blocked", extra={"session_id": self.session_id, "client_ip": ip}
            )
            with contextlib.suppress(OSError, ConnectionError):
                # RFC 5321 §3.1 lets a server answer a connection with 554
                # instead of a 220 greeting. No banner, no dialogue.
                await self.push("554 5.7.1 Access denied")
                if self._writer is not None:
                    await self._writer.drain()
            if self.transport is not None:
                self.transport.close()
            return False
        try:
            await self._ip_gate._try_acquire(ip)  # noqa: SLF001
        except TooManyConnections as exc:
            metrics.CONNECTIONS_TOTAL.labels(result=f"rejected_{exc.scope}").inc()
            metrics.DISCONNECTS_421.labels(reason=f"gate_{exc.scope}").inc()
            logger.debug(
                "connection_capped",
                extra={"session_id": self.session_id, "client_ip": ip, "scope": exc.scope},
            )
            with contextlib.suppress(OSError, ConnectionError):
                await self.push("421 4.7.0 Too many connections, try again later")
                # Best-effort drain so the 421 actually makes it out before the
                # RST closes the socket.
                if self._writer is not None:
                    await self._writer.drain()
            if self.transport is not None:
                self.transport.close()
            return False
        self._gate_held_ip = ip
        self._gate_started = time.monotonic()
        metrics.CONNECTIONS_TOTAL.labels(result="accepted").inc()
        return True

    async def _release_gate(self) -> None:
        if self._gate_held_ip is None or self._ip_gate is None:
            return
        ip, self._gate_held_ip = self._gate_held_ip, None
        if self._gate_started is not None:
            metrics.SESSION_DURATION.observe(time.monotonic() - self._gate_started)
            self._gate_started = None
        # _handle_client's finally runs in the event loop, so awaiting the
        # release is safe and avoids the fire-and-forget bookkeeping leak we
        # would have with create_task during shutdown.
        await self._ip_gate._release(ip)  # noqa: SLF001

    def _wire_peer_ip(self) -> str:
        peer = getattr(self.session, "peer", None) if self.session else None
        if peer:
            return str(peer[0])
        # All sessions without a wire peer collapse into one bucket, so they
        # share a single source's slots. Counted rather than logged at WARNING:
        # a peer that resets the connection between accept() and here can race
        # peername into None, and that must not be a way to pick our log rate.
        metrics.CONNECTIONS_TOTAL.labels(result="no_wire_peer").inc()
        logger.debug("session_without_peer", extra={"session_id": self.session_id})
        return "unknown"
