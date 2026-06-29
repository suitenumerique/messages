"""Hardened :class:`aiosmtpd.smtp.SMTP` subclass.

aiosmtpd's defaults are reasonable but its surface area still includes a few
verbs we never want exposed on a public, inbound-only port-25 endpoint:

* ``AUTH`` — never offered (no authenticator wired) but we still reply 502 to
  reject any attempt explicitly, so a misconfiguration cannot quietly become a
  relay.
* ``VRFY`` — RFC 5321 §3.5 lets us respond with a canned 252; we do so
  unconditionally to prevent address enumeration.
* ``EXPN`` — explicit 502.

We also fold connection-admission control into :meth:`_handle_client`. The
gate is checked exactly once per accepted TCP session. When PROXY-protocol is
enabled, the check is deferred to the ``handle_PROXY`` hook so it sees the
real client address rather than the load-balancer's IP.
"""

from __future__ import annotations

import contextlib
import logging
import time

from aiosmtpd.smtp import SMTP as BaseSMTP

from . import metrics
from .limits import IPGate, TooManyConnections

logger = logging.getLogger(__name__)


class HardenedSMTP(BaseSMTP):
    """SMTP subclass that locks down VRFY/EXPN/AUTH and applies the IP gate."""

    def __init__(self, *args, ip_gate: IPGate | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._ip_gate: IPGate | None = ip_gate
        # Track whether we are currently holding a slot in the gate so the
        # release path runs at most once.
        self._gate_held_ip: str | None = None
        self._gate_started: float | None = None

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

    # ------------------------------------------------------------ gate wiring
    async def _handle_client(self) -> None:
        """Wrap aiosmtpd's per-connection dialogue with admission control.

        Two paths:

        * **No PROXY protocol** — the immediate TCP peer is the real client,
          so we gate before the SMTP dialogue starts.
        * **PROXY protocol enabled** — gate is deferred to
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

    async def _acquire_gate(self, ip: str) -> bool:
        assert self._ip_gate is not None  # noqa: S101 — narrowing only; checked above
        try:
            await self._ip_gate._try_acquire(ip)  # noqa: SLF001
        except TooManyConnections as exc:
            metrics.CONNECTIONS_TOTAL.labels(result=f"rejected_{exc.scope}").inc()
            metrics.DISCONNECTS_421.labels(reason=f"gate_{exc.scope}").inc()
            logger.info("connection from %s refused: %s cap reached", ip, exc.scope)
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
        # All sessions without a wire peer collapse into one bucket; log so a
        # spike here doesn't go invisible.
        logger.warning("session has no transport peer; using 'unknown' bucket")
        return "unknown"
