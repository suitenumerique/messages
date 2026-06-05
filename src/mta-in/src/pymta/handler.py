"""aiosmtpd handler implementing the queue-less inbound delivery flow.

For each SMTP transaction the handler

1. validates EHLO syntax,
2. validates and stores MAIL FROM (allowing the null sender),
3. on RCPT TO: validates the address shape, then calls the MDA
   ``inbound/mta/check/`` endpoint synchronously — RCPT is rejected with a
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
import logging

from . import metrics, settings
from .address import AddressError, validate_envelope_address
from .mda_async import MDAClient, MDAResult

logger = logging.getLogger(__name__)


# Per-session counters live on the Session object (one per TCP connection).
# aiosmtpd resets ``envelope`` after each DATA so we cannot stash counters
# there; we attach to ``session`` via setattr instead.
_ENVELOPES_ATTR = "_pymta_envelopes"
_SOFT_ERRORS_ATTR = "_pymta_soft_errors"
_RCPT_MISSES_ATTR = "_pymta_rcpt_misses"

# Sentinel for the RFC 5321 null sender (MAIL FROM:<>). aiosmtpd's
# ``smtp_RCPT`` rejects with 503 when ``envelope.mail_from`` is falsy, which
# would block legitimate bounces. We keep the sentinel internally and rewrite
# it back to the empty string when calling the MDA, matching the Postfix
# milter's existing wire contract.
NULL_SENDER_SENTINEL = "<>"

# Control characters that must never appear in an EHLO/HELO hostname or
# anywhere else we'll log / pass into HTTP claims. CR, LF, NUL are the
# CRLF-injection vectors; TAB is a header-unfolding vector.
_FORBIDDEN_HOSTNAME_CHARS = frozenset({"\r", "\n", "\x00", "\t"})


def _envelopes_count(session) -> int:
    return getattr(session, _ENVELOPES_ATTR, 0)


def _bump_envelopes(session) -> int:
    n = _envelopes_count(session) + 1
    setattr(session, _ENVELOPES_ATTR, n)
    return n


def _bump_soft_errors(session) -> int:
    n = getattr(session, _SOFT_ERRORS_ATTR, 0) + 1
    setattr(session, _SOFT_ERRORS_ATTR, n)
    return n


def _bump_rcpt_misses(session) -> int:
    n = getattr(session, _RCPT_MISSES_ATTR, 0) + 1
    setattr(session, _RCPT_MISSES_ATTR, n)
    return n


def _peer_ip(session) -> str | None:
    proxy_data = getattr(session, "proxy_data", None)
    if proxy_data is not None and getattr(proxy_data, "src_addr", None):
        return str(proxy_data.src_addr)
    peer = getattr(session, "peer", None)
    if peer and len(peer) >= 1:
        return str(peer[0])
    return None


def _peer_port(session) -> str | None:
    proxy_data = getattr(session, "proxy_data", None)
    if proxy_data is not None and getattr(proxy_data, "src_port", None) is not None:
        return str(proxy_data.src_port)
    peer = getattr(session, "peer", None)
    if peer and len(peer) >= 2:
        return str(peer[1])
    return None


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
                    "stripping disallowed EHLO extension %r from %s — review the "
                    "SMTP configuration so it is not advertised in the first place",
                    verb,
                    _peer_ip(session),
                )
                continue
            clean.append(line)

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

        # Honour MAIL FROM:... SIZE=N if announced — fail fast before DATA.
        for opt in mail_options or []:
            if opt.upper().startswith("SIZE="):
                try:
                    announced = int(opt.split("=", 1)[1])
                except ValueError:
                    _bump_soft_errors(session)
                    return "501 5.5.4 Bad SIZE parameter"
                if announced > settings.MAX_INCOMING_EMAIL_SIZE:
                    metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
                    _bump_soft_errors(session)
                    return "552 5.3.4 Message size exceeds fixed maximum"

        envelope.mail_from = clean if clean else NULL_SENDER_SENTINEL
        envelope.mail_options.extend(mail_options or [])
        return "250 2.1.0 OK"

    # ------------------------------------------------------------------ RCPT
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: PLR0911
        # First gate: hard-error budget. Once the session has accumulated
        # ``PYMTA_HARD_ERROR_LIMIT`` 4xx/5xx replies, send 421 and close so
        # bulk address enumeration / dictionary attacks cannot keep hammering
        # this single TCP session.
        if getattr(session, _SOFT_ERRORS_ATTR, 0) >= settings.PYMTA_HARD_ERROR_LIMIT:
            metrics.SECURITY_REJECTIONS.labels(reason="hard_error_limit").inc()
            metrics.DISCONNECTS_421.labels(reason="hard_error_limit").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return "421 4.7.0 Too many errors, goodbye"

        # Per-envelope recipient cap.
        if len(envelope.rcpt_tos) >= settings.PYMTA_MAX_RECIPIENTS:
            metrics.SECURITY_REJECTIONS.labels(reason="max_recipients").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump_soft_errors(session)
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
            _bump_soft_errors(session)
            return f"{err.smtp_code} {err.smtp_text}"

        result = await self.mda.check_recipient(clean)
        if result.temp_fail:
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump_soft_errors(session)
            return "451 4.3.0 Recipient verification temporarily unavailable"
        if not result.ok:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump_soft_errors(session)
            return "550 5.1.1 Recipient verification failed"

        exists = bool(result.payload.get(clean, False))
        if not exists:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump_soft_errors(session)
            misses = _bump_rcpt_misses(session)
            if misses >= settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION:
                metrics.SECURITY_REJECTIONS.labels(reason="max_rcpt_misses").inc()
                metrics.DISCONNECTS_421.labels(reason="max_rcpt_misses").inc()
                return "421 4.7.0 Too many unknown recipients, goodbye"
            return "550 5.1.1 No such recipient"

        envelope.rcpt_tos.append(clean)
        envelope.rcpt_options.extend(rcpt_options or [])
        metrics.RCPT_TOTAL.labels(result="accepted").inc()
        return "250 2.1.5 OK"

    # ------------------------------------------------------------------ DATA
    async def handle_DATA(self, server, session, envelope):  # noqa: PLR0911
        envelopes = _bump_envelopes(session)
        if envelopes > settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION:
            metrics.SECURITY_REJECTIONS.labels(reason="max_envelopes").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _bump_soft_errors(session)
            return "451 4.7.0 Too many messages this session"

        content: bytes = envelope.content or b""

        # NUL bytes have no place in an RFC 5321 message and break downstream
        # C parsers — reject before we pay the cost of the deliver call.
        if b"\x00" in content:
            metrics.SECURITY_REJECTIONS.labels(reason="nul_byte").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump_soft_errors(session)
            return "554 5.6.0 NUL byte in message body"

        if len(content) > settings.MAX_INCOMING_EMAIL_SIZE:
            # aiosmtpd already replies 552 itself when the in-flight DATA
            # exceeds data_size_limit, so reaching here is defensive only.
            metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump_soft_errors(session)
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
                    client_address=_peer_ip(session),
                    client_port=_peer_port(session),
                    # We do not reverse-DNS ourselves: the MDA inserts its
                    # own Received header using metadata and can decide what
                    # to do with the missing hostname.
                    client_hostname=None,
                    client_helo=_safe_hostname(getattr(session, "host_name", None), session=session),
                ),
                timeout=settings.PYMTA_DATA_TIMEOUT,
            )
        except TimeoutError:
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            metrics.MESSAGE_BYTES.observe(len(content))
            _bump_soft_errors(session)
            logger.warning(
                "DATA deliver deadline exceeded (%ds) for peer %s",
                settings.PYMTA_DATA_TIMEOUT,
                _peer_ip(session),
            )
            return "451 4.3.0 Delivery timed out, please retry"

        metrics.MESSAGE_BYTES.observe(len(content))

        if result.ok and result.payload.get("status") == "ok":
            metrics.MESSAGES_TOTAL.labels(result="delivered").inc()
            return "250 2.0.0 Message accepted for delivery"
        if result.temp_fail:
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _bump_soft_errors(session)
            return "451 4.3.0 Delivery temporarily unavailable"
        metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
        _bump_soft_errors(session)
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
        """
        real_ip = "unknown"
        if proxy_data is not None and getattr(proxy_data, "src_addr", None):
            real_ip = str(proxy_data.src_addr)
        return await server.acquire_gate_post_proxy(real_ip)

    # ------------------------------------------------------------------ misc
    async def handle_exception(self, error: BaseException) -> str:
        # Never leak stack traces or internal hostnames in SMTP replies.
        metrics.SECURITY_REJECTIONS.labels(reason="internal_error").inc()
        metrics.DISCONNECTS_421.labels(reason="internal_error").inc()
        logger.exception("Unhandled error in SMTP handler")
        return "421 4.3.0 Internal error, please try again later"
