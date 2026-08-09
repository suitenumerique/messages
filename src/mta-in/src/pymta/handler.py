"""aiosmtpd handler implementing the queue-less inbound delivery flow.

For each SMTP transaction the handler

1. validates EHLO syntax,
2. validates and stores MAIL FROM (allowing the null sender),
3. on RCPT TO: validates the address shape, then calls the MDA
   ``inbound/mta/check/`` endpoint synchronously. RCPT is rejected with a
   permanent 5xx if the mailbox does not exist, a 4xx if the check itself
   fails or times out,
4. on DATA: forwards the full message bytes to ``inbound/mta/deliver/``
   and translates the MDA outcome back to a single SMTP reply line.

The handler keeps no on-disk queue and no persistent envelope log: a 250 to
the peer means the MDA has already accepted the message; a 4xx means the
peer should retry later.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

from . import metrics, settings
from .address import AddressError, validate_envelope_address
from .mda_async import MDAClient, MDAResult

logger = logging.getLogger(__name__)


# Abuse counters are keyed to the TCP connection, so they live on the *server*
# (the per-connection SMTP protocol instance) rather than on ``session``.
# aiosmtpd resets ``envelope`` after each DATA, and rebuilds ``session`` from
# scratch on STARTTLS, so stashing them on the session would let a peer wipe its
# own enumeration and error budgets by issuing STARTTLS mid-stream. The server
# instance survives both. See _PROXY_SRC_ATTR below for the same reasoning.
_ENVELOPES_ATTR = "_pymta_envelopes"
_SOFT_ERRORS_ATTR = "_pymta_soft_errors"
_RCPT_MISSES_ATTR = "_pymta_rcpt_misses"

# The PROXY-protocol source is stashed on the *server* (the per-connection SMTP
# protocol instance), NOT on the session. aiosmtpd rebuilds ``session`` from
# scratch when the client issues STARTTLS (connection_made -> _create_session),
# which drops ``session.proxy_data`` and resets ``session.peer`` to the raw TCP
# peer (the load balancer). The server instance survives that transport swap,
# so a value stashed there is the only copy of the real client IP that outlives
# STARTTLS. Holds a ``(addr, port)`` tuple; ``port`` may be None.
_PROXY_SRC_ATTR = "_pymta_proxy_src"

# Sentinel for the RFC 5321 null sender (MAIL FROM:<>). aiosmtpd's
# ``smtp_RCPT`` rejects with 503 when ``envelope.mail_from`` is falsy, which
# would block legitimate bounces. We keep the sentinel internally and rewrite
# it back to the empty string when calling the MDA, matching the Postfix
# milter's existing wire contract.
NULL_SENDER_SENTINEL = "<>"

# Slice of PYMTA_DATA_TIMEOUT the handler holds back from the MDA deliver call
# so it still has room to push a 451 before the protocol-level deadline closes
# the transport. Not configurable: it is a property of how the two deadlines
# nest, not something an operator sizes.
_REPLY_RESERVE_SECONDS = 10.0

# Control characters that must never appear in an EHLO/HELO hostname or
# anywhere else we'll log / pass into HTTP claims. CR, LF, NUL are the
# CRLF-injection vectors; TAB is a header-unfolding vector.
_FORBIDDEN_HOSTNAME_CHARS = frozenset({"\r", "\n", "\x00", "\t"})


def _counters(server, session):
    """Return the object the per-connection counters hang off.

    The server when we have one (the normal path, since it outlives the STARTTLS
    session rebuild); the session otherwise, which keeps the handler usable
    from unit tests that pass ``server=None``.
    """
    return session if server is None else server


def _count(holder, attr: str) -> int:
    return getattr(holder, attr, 0)


def _bump(holder, attr: str) -> int:
    n = _count(holder, attr) + 1
    setattr(holder, attr, n)
    return n


def _request_disconnect(server) -> None:
    """Ask the protocol to close once the reply we are returning is on the wire.

    A 421 is a promise to hang up; aiosmtpd on its own would push the string
    and keep serving the session. Silently a no-op for the unit-test fakes.
    """
    requester = getattr(server, "request_disconnect", None)
    if requester is not None:
        requester()


def _peer_ip(session, server=None) -> str | None:
    # Prefer the PROXY source captured at connect time and stashed on the
    # server: it is the only copy that survives the STARTTLS session rebuild
    # (see _PROXY_SRC_ATTR). Fall back to session.proxy_data for the pre-TLS
    # window, then to the raw TCP peer.
    #
    # That last fallback is only reachable with PROXY protocol off, where the
    # wire peer IS the client. With it on, the wire peer is the balancer, and
    # returning it would attribute the mail to our own infrastructure: a
    # plausible-looking but wrong IP in the Received header. Report nothing
    # instead; the MDA omits the trace when a part is missing.
    stashed = getattr(server, _PROXY_SRC_ATTR, None) if server is not None else None
    if stashed is not None and stashed[0]:
        return str(stashed[0])
    proxy_data = getattr(session, "proxy_data", None)
    if proxy_data is not None and getattr(proxy_data, "src_addr", None):
        return str(proxy_data.src_addr)
    if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        return None
    peer = getattr(session, "peer", None)
    if peer and len(peer) >= 1:
        return str(peer[0])
    return None


def _peer_port(session, server=None) -> str | None:
    stashed = getattr(server, _PROXY_SRC_ATTR, None) if server is not None else None
    if stashed is not None and stashed[1] is not None:
        return str(stashed[1])
    proxy_data = getattr(session, "proxy_data", None)
    if proxy_data is not None and getattr(proxy_data, "src_port", None) is not None:
        return str(proxy_data.src_port)
    if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        return None
    peer = getattr(session, "peer", None)
    if peer and len(peer) >= 2:
        return str(peer[1])
    return None


def _remaining_data_budget(server) -> float:
    """Seconds still available for the MDA deliver call.

    ``PYMTA_DATA_TIMEOUT`` is one budget covering the whole DATA phase, and the
    protocol arms the transport at exactly that value, so the body receive has
    already spent part of it, and we stop short of the rest. The reserve is
    what buys the 451 on the timeout path: expiring at the same instant as the
    transport would be a race we'd usually lose (its timer was scheduled first,
    and equal deadlines fire in scheduling order), leaving the peer with a bare
    disconnect instead of a reason to retry.

    Falls back to the full budget when the caller has no DATA start timestamp
    (unit-test fakes). The floor keeps a very slow receive from cancelling the
    deliver call before it is even issued; past that point the transport
    deadline is the backstop, as designed.
    """
    started = getattr(server, "data_phase_started", None)
    if started is None:
        return float(settings.PYMTA_DATA_TIMEOUT)
    spent = time.monotonic() - started
    return max(1.0, settings.PYMTA_DATA_TIMEOUT - spent - _REPLY_RESERVE_SECONDS)


def _proxy_header_is_trusted(session) -> bool:
    """True when the *wire* peer is allowed to speak PROXY protocol to us.

    aiosmtpd parses a PROXY header from whoever sent it and has no notion of
    a trusted upstream. Since the header dictates both the rate-limit key and
    the ``client_address`` the MDA records, an unfiltered one hands a direct
    connector a free pass past every per-IP cap and the ability to attribute
    its mail to any address it names.

    Fails closed by construction: an empty allowlist matches nothing, and
    ``server._check_proxy_trust_config`` refuses to start that combination in
    the first place.
    """
    peer = getattr(session, "peer", None)
    if not peer:
        return False
    try:
        wire_ip = ipaddress.ip_address(str(peer[0]))
    except ValueError:
        return False
    return any(wire_ip in network for network in settings.PYMTA_TRUSTED_PROXIES)


def _safe_hostname(raw: str | None, session=None) -> str | None:
    """Return ``raw`` only if it is free of CRLF/NUL/TAB; otherwise None.

    The MDA receives this through a JWT claim; downstream consumers may
    interpolate it into log lines or ``Received`` headers, so a control char
    here is a header-injection vector.

    When ``session`` is supplied, a rejected hostname is counted and logged so
    operators can spot floods of malformed HELO/EHLO greetings.
    """
    if raw is None:
        return None
    if _FORBIDDEN_HOSTNAME_CHARS & set(raw):
        metrics.SECURITY_REJECTIONS.labels(reason="bad_helo").inc()
        logger.info("dropping HELO/EHLO with forbidden control chars from %s", _peer_ip(session))
        return None
    return raw


class InboundHandler:
    """One instance per server process; called concurrently from many sessions."""

    def __init__(self, mda_client: MDAClient):
        self.mda = mda_client

    # ------------------------------------------------------------------ EHLO
    async def handle_EHLO(self, server, session, envelope, hostname, responses):
        """Customize the EHLO response list.

        Strips any extension keyword we've decided not to expose on inbound
        port 25: AUTH (would invite credential-stuffing or open relay if
        misconfigured), CHUNKING/BDAT (smuggling parser-confusion surface),
        and PIPELINING (announcing it advertises that we accept rapid command
        coalescing; the actual per-verb rate cap lives in
        ``controller.py:command_call_limit``). aiosmtpd already omits these
        by default; we keep the filter as a guard against future regressions
        or a contributor wiring an authenticator without re-reading the
        security rationale.
        """
        denied_verbs = {"AUTH", "CHUNKING", "BDAT", "PIPELINING"}
        clean: list[str] = []
        for line in responses:
            # line looks like "250-FOO bar" or "250 FOO bar".
            after = line[4:] if len(line) > 4 else ""
            verb = after.split(" ", 1)[0].upper()
            if verb in denied_verbs:
                metrics.SECURITY_REJECTIONS.labels(reason="auth_offered").inc()
                logger.warning(
                    "stripping disallowed EHLO extension %r from %s. Review the "
                    "SMTP configuration so it is not advertised in the first place",
                    verb,
                    _peer_ip(session),
                )
                continue
            clean.append(line)

        # Re-mark the terminator. aiosmtpd appends "250 HELP" last, so today
        # the line we drop is never the final one. But a multiline reply whose
        # last line still reads "250-" leaves clients waiting forever for a
        # continuation that will not come, and that is too sharp an edge to
        # leave resting on an upstream implementation detail.
        if clean:
            last = clean[-1]
            if last.startswith("250-"):
                clean[-1] = "250 " + last[4:]

        session.host_name = _safe_hostname(hostname, session=session)
        return clean

    async def handle_HELO(self, server, session, envelope, hostname):
        session.host_name = _safe_hostname(hostname, session=session)
        return f"250 {server.hostname}"

    # ------------------------------------------------------------------ MAIL
    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        try:
            clean = validate_envelope_address(
                address,
                allow_empty=True,
                max_local=settings.PYMTA_MAX_LOCAL_PART,
                max_domain=settings.PYMTA_MAX_DOMAIN,
            )
        except AddressError as err:
            metrics.SECURITY_REJECTIONS.labels(reason=err.reason).inc()
            return f"{err.smtp_code} {err.smtp_text}"

        counters = _counters(server, session)

        # Honour MAIL FROM:... SIZE=N if announced, to fail fast before DATA.
        for opt in mail_options or []:
            if opt.upper().startswith("SIZE="):
                try:
                    announced = int(opt.split("=", 1)[1])
                except ValueError:
                    _bump(counters, _SOFT_ERRORS_ATTR)
                    return "501 5.5.4 Bad SIZE parameter"
                if announced > settings.MAX_INCOMING_EMAIL_SIZE:
                    metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
                    _bump(counters, _SOFT_ERRORS_ATTR)
                    return "552 5.3.4 Message size exceeds fixed maximum"

        envelope.mail_from = clean if clean else NULL_SENDER_SENTINEL
        envelope.mail_options.extend(mail_options or [])
        return "250 2.1.0 OK"

    # ------------------------------------------------------------------ RCPT
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: PLR0911
        counters = _counters(server, session)

        # First gate: hard-error budget. Once the connection has accumulated
        # ``PYMTA_HARD_ERROR_LIMIT`` 4xx/5xx replies, send 421 and close so
        # bulk address enumeration / dictionary attacks cannot keep hammering
        # this single TCP session.
        if _count(counters, _SOFT_ERRORS_ATTR) >= settings.PYMTA_HARD_ERROR_LIMIT:
            metrics.SECURITY_REJECTIONS.labels(reason="hard_error_limit").inc()
            metrics.DISCONNECTS_421.labels(reason="hard_error_limit").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _request_disconnect(server)
            return "421 4.7.0 Too many errors, goodbye"

        # Per-envelope recipient cap.
        if len(envelope.rcpt_tos) >= settings.PYMTA_MAX_RECIPIENTS:
            metrics.SECURITY_REJECTIONS.labels(reason="max_recipients").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "452 4.5.3 Too many recipients"

        try:
            clean = validate_envelope_address(
                address,
                allow_empty=False,
                max_local=settings.PYMTA_MAX_LOCAL_PART,
                max_domain=settings.PYMTA_MAX_DOMAIN,
            )
        except AddressError as err:
            metrics.SECURITY_REJECTIONS.labels(reason=err.reason).inc()
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return f"{err.smtp_code} {err.smtp_text}"

        result = await self.mda.check_recipient(clean)
        if result.temp_fail:
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "451 4.3.0 Recipient verification temporarily unavailable"
        if not result.ok:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "550 5.1.1 Recipient verification failed"

        exists = bool(result.payload.get(clean, False))
        if not exists:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            misses = _bump(counters, _RCPT_MISSES_ATTR)
            if misses >= settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION:
                metrics.SECURITY_REJECTIONS.labels(reason="max_rcpt_misses").inc()
                metrics.DISCONNECTS_421.labels(reason="max_rcpt_misses").inc()
                _request_disconnect(server)
                return "421 4.7.0 Too many unknown recipients, goodbye"
            return "550 5.1.1 No such recipient"

        envelope.rcpt_tos.append(clean)
        envelope.rcpt_options.extend(rcpt_options or [])
        metrics.RCPT_TOTAL.labels(result="accepted").inc()
        return "250 2.1.5 OK"

    # ------------------------------------------------------------------ DATA
    async def handle_DATA(self, server, session, envelope):  # noqa: PLR0911
        counters = _counters(server, session)
        envelopes = _bump(counters, _ENVELOPES_ATTR)
        if envelopes > settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION:
            metrics.SECURITY_REJECTIONS.labels(reason="max_envelopes").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "451 4.7.0 Too many messages this session"

        content: bytes = envelope.content or b""

        # NUL bytes have no place in an RFC 5321 message and break downstream
        # C parsers. Reject before we pay the cost of the deliver call.
        if b"\x00" in content:
            metrics.SECURITY_REJECTIONS.labels(reason="nul_byte").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "554 5.6.0 NUL byte in message body"

        if len(content) > settings.MAX_INCOMING_EMAIL_SIZE:
            # aiosmtpd already replies 552 itself when the in-flight DATA
            # exceeds data_size_limit, so reaching here is defensive only.
            metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "552 5.3.4 Message size exceeds fixed maximum"

        try:
            sender = envelope.mail_from
            if sender == NULL_SENDER_SENTINEL:
                sender = ""
            result: MDAResult = await asyncio.wait_for(
                self.mda.deliver(
                    message=content,
                    sender=sender,
                    original_recipients=list(envelope.rcpt_tos),
                    client_address=_peer_ip(session, server),
                    client_port=_peer_port(session, server),
                    # We do not reverse-DNS ourselves: the MDA inserts its
                    # own Received header using metadata and can decide what
                    # to do with the missing hostname.
                    client_hostname=None,
                    client_helo=_safe_hostname(getattr(session, "host_name", None), session=session),
                ),
                timeout=_remaining_data_budget(server),
            )
        except TimeoutError:
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            metrics.MESSAGE_BYTES.observe(len(content))
            _bump(counters, _SOFT_ERRORS_ATTR)
            logger.warning(
                "DATA deadline exceeded (%ds total for receive + deliver) for peer %s",
                settings.PYMTA_DATA_TIMEOUT,
                _peer_ip(session, server),
            )
            return "451 4.3.0 Delivery timed out, please retry"

        metrics.MESSAGE_BYTES.observe(len(content))

        if result.ok and result.payload.get("status") == "ok":
            metrics.MESSAGES_TOTAL.labels(result="delivered").inc()
            return "250 2.0.0 Message accepted for delivery"

        # Anything short of an unambiguous "ok" defers. In particular the MDA
        # answers 207 when it delivered to some recipients and not others: it
        # cannot tell us to retry just the stragglers, so the only way they
        # ever arrive is a retry of the whole envelope. That duplicates for the
        # recipients already served, which is the right trade against losing the rest,
        # and the behaviour the Postfix milter has always had (anything but
        # 200 + status=ok is TEMPFAIL). Permanent rejection is reserved for the
        # statuses that mean *this message* is unacceptable; see
        # ``mda_async._PERMANENT_STATUSES``.
        if result.temp_fail or result.ok:
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            logger.warning(
                "deferring message: MDA deliver returned HTTP %d payload-status %r",
                result.status_code,
                result.payload.get("status"),
            )
            return "451 4.3.0 Delivery temporarily unavailable"
        metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
        _bump(counters, _SOFT_ERRORS_ATTR)
        return "554 5.6.0 Message rejected by delivery agent"

    # ------------------------------------------------------------------ PROXY
    async def handle_PROXY(self, server, session, envelope, proxy_data):
        """Apply admission control once PROXY-protocol parsing is done.

        Routing the gate through here (rather than at SMTP-connect time)
        means we count sessions against the real client IP carried in the
        PROXY header, not against the load-balancer's IP. Without this,
        every session behind HAProxy would be bucketed under one address
        and ``PYMTA_MAX_SESSIONS_PER_IP`` would silently turn into a global
        cap.

        Returning False makes aiosmtpd drop the connection without ever
        starting the SMTP dialogue.
        """
        if not _proxy_header_is_trusted(session):
            metrics.SECURITY_REJECTIONS.labels(reason="untrusted_proxy").inc()
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_untrusted_proxy").inc()
            logger.warning(
                "rejecting PROXY header from untrusted peer %r (claimed src=%s); "
                "add it to PYMTA_TRUSTED_PROXIES if this is a real balancer",
                getattr(session, "peer", None),
                getattr(proxy_data, "src_addr", None),
            )
            return False

        real_ip = "unknown"
        if proxy_data is not None and getattr(proxy_data, "src_addr", None):
            real_ip = str(proxy_data.src_addr)
            # Stash on the server so the real client IP outlives the STARTTLS
            # session rebuild that would otherwise drop session.proxy_data.
            setattr(
                server,
                _PROXY_SRC_ATTR,
                (str(proxy_data.src_addr), getattr(proxy_data, "src_port", None)),
            )
        if proxy_data is not None:
            # Permanent forensic record: ties the SMTP session to the real
            # origin IP carried in the PROXY header. Every other mail.log line
            # is keyed on session.peer (the load balancer), so this is the only
            # place the true client IP is recorded. Logging peer alongside src
            # also surfaces misconfigurations at a glance: src == peer means the
            # header is not carrying a real origin.
            logger.info(
                "PROXY header: src=%s:%s peer=%r version=%r protocol=%r",
                getattr(proxy_data, "src_addr", None),
                getattr(proxy_data, "src_port", None),
                getattr(session, "peer", None),
                getattr(proxy_data, "version", None),
                getattr(proxy_data, "protocol", None),
            )
        return await server.acquire_gate_post_proxy(real_ip)

    # ------------------------------------------------------------------ misc
    async def handle_exception(self, error: BaseException) -> str:
        # Never leak stack traces or internal hostnames in SMTP replies.
        metrics.SECURITY_REJECTIONS.labels(reason="internal_error").inc()
        metrics.DISCONNECTS_421.labels(reason="internal_error").inc()
        logger.exception("Unhandled error in SMTP handler")
        return "421 4.3.0 Internal error, please try again later"
