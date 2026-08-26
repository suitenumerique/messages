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
import logging
import re
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
# the transport. Defined in settings beside the timeout it is subtracted from,
# which refuses to start a configuration where it would consume the whole
# budget.
_REPLY_RESERVE_SECONDS = float(settings.PYMTA_REPLY_RESERVE_SECONDS)

# Control characters that must never appear in an EHLO/HELO hostname or
# anywhere else we'll log / pass into HTTP claims. CR, LF, NUL are the
# CRLF-injection vectors; TAB is a header-unfolding vector.
_FORBIDDEN_HOSTNAME_CHARS = frozenset({"\r", "\n", "\x00", "\t"})


def _session_id(server) -> str | None:
    """Correlation id for this connection, or None outside a real server."""
    return getattr(server, "session_id", None)


def _trace(server, session, **fields):
    """Fields every per-connection line carries, so lines can be joined up."""
    return {"session_id": _session_id(server), "client_ip": _peer_ip(session, server), **fields}


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


def _hard_error_limit_reached(server, counters) -> bool:
    """True when this connection has spent its ``PYMTA_MAX_ERRORS_PER_SESSION``.

    Records the rejection and asks for the disconnect, so a caller only has to
    return the 421. Bulk address enumeration and dictionary attacks otherwise
    keep hammering a single TCP session.
    """
    if _count(counters, _SOFT_ERRORS_ATTR) < settings.PYMTA_MAX_ERRORS_PER_SESSION:
        return False
    metrics.SECURITY_REJECTIONS.labels(reason="max_errors_per_session").inc()
    metrics.DISCONNECTS_421.labels(reason="max_errors_per_session").inc()
    _request_disconnect(server)
    return True


def _request_disconnect(server) -> None:
    """Ask the protocol to close once the reply we are returning is on the wire.

    A 421 is a promise to hang up; aiosmtpd on its own would push the string
    and keep serving the session. Silently a no-op for the unit-test fakes.
    """
    requester = getattr(server, "request_disconnect", None)
    if requester is not None:
        requester()


def _wire_ip(session) -> str | None:
    """The TCP peer's address, which behind a balancer is the balancer."""
    peer = getattr(session, "peer", None)
    return str(peer[0]) if peer else None


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
    claimed = getattr(proxy_data, "src_addr", None) if proxy_data is not None else None
    if claimed:
        # Same gate handle_PROXY applies to the key: aiosmtpd takes AF_UNIX's
        # src_addr straight off the wire, so an unparseable claim is arbitrary
        # bytes. It earns nothing, not even the wire peer below (the balancer).
        return str(claimed) if settings.parse_client_ip(str(claimed)) is not None else None
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
        # Only next to an address we accepted: same rejected header.
        return str(proxy_data.src_port) if _peer_ip(session, server) is not None else None
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

    The budget comes from the protocol, which recorded the value it armed the
    transport at, rather than from settings: PYMTA_DATA_TIMEOUT is reloadable,
    and re-reading it here would size the deliver call against a deadline that
    was never set.

    Falls back to the full budget when the caller has no DATA start timestamp
    (unit-test fakes). Raises ``TimeoutError`` when the receive already spent
    the whole budget: issuing the deliver call anyway would overrun the
    transport deadline the reserve exists to stay clear of, and the caller
    already turns that exception into the 451.
    """
    started = getattr(server, "data_phase_started", None)
    if started is None:
        return float(settings.PYMTA_DATA_TIMEOUT)
    budget = getattr(server, "data_phase_budget", None) or float(settings.PYMTA_DATA_TIMEOUT)
    spent = time.monotonic() - started
    remaining = budget - spent - _REPLY_RESERVE_SECONDS
    if remaining <= 0:
        raise TimeoutError("DATA budget spent before the deliver call")
    return remaining


# Any line ending, CRLF first so a well-formed one is consumed whole rather
# than as a bare CR followed by a bare LF.
_LINE_ENDING_RE = re.compile(rb"\r\n|\r|\n")


def _normalize_line_endings(content: bytes) -> tuple[bytes, bool]:
    """Rewrite every bare LF and bare CR to CRLF. Returns (content, changed).

    aiosmtpd ends DATA only on ``\\r\\n.\\r\\n``, so a bare LF cannot split one
    message into two. That is CVE-2024-27305, fixed in 1.4.5. What it can do is
    split a line: ``readuntil(b"\\r\\n")`` treats ``Subject: a<LF>X-Evil: b`` as
    one line and hands it to us intact, while any parser that also breaks on LF
    reads two headers. Python's ``email`` package does, and the MDA uses it, so
    the sender gets to choose a header on the stored message.

    Postfix closes this with ``smtpd_forbid_bare_newline = normalize`` (pinned in
    etc/main.cf). aiosmtpd has no equivalent, so without this the defence
    disappears the day traffic moves to pymta.

    Normalising rather than rejecting matches Postfix and keeps the mail: a
    message is not spam for having been assembled by a sloppy mailer. It does
    rewrite bytes, so a DKIM body hash over the original would break. But a body
    carrying bare LF has already failed DKIM canonicalisation, which is defined
    over CRLF, so there is no intact signature left to preserve.

    Both steps are written for memory rather than speed, because this runs on
    the whole message body while up to PYMTA_MAX_CONCURRENT_DATA other messages
    are doing the same — that cap, not the session one, because only a session
    holding a DATA slot ever reaches here:

    * ``count`` walks the buffer without allocating, so a message that is
      already clean, which is nearly all of them, is returned as the same object
      and costs nothing.
    * the rewrite is one regex pass producing one new buffer. Chained
      ``replace`` calls would read better but allocate an intermediate copy per
      call, putting three copies of a 10 MiB body in flight at once.
    """
    crlf = content.count(b"\r\n")
    if content.count(b"\n") == crlf and content.count(b"\r") == crlf:
        return content, False
    return _LINE_ENDING_RE.sub(b"\r\n", content), True


def _proxy_header_is_trusted(session) -> bool:
    """True when the *wire* peer is allowed to speak PROXY protocol to us.

    aiosmtpd parses a PROXY header from whoever sent it and has no notion of
    a trusted upstream. Since the header dictates both the rate-limit key and
    the ``client_address`` the MDA records, an unfiltered one hands a direct
    connector a free pass past every per-IP cap and the ability to attribute
    its mail to any address it names.

    An *empty* allowlist means "no upstream named", and there is nothing left
    to filter on: every header is trusted, exactly as if the allowlist were
    0.0.0.0/0. That is a deliberate escape hatch for deployments whose balancer
    addresses are not known at boot, and it puts the whole trust boundary on
    the network isolation; ``server._check_proxy_trust_config`` warns loudly
    about it at startup. Naming the balancer is the safe configuration.
    """
    if not settings.PYMTA_TRUSTED_PROXIES:
        return True
    peer = getattr(session, "peer", None)
    if not peer:
        return False
    wire_ip = settings.parse_client_ip(peer[0])
    if wire_ip is None:
        return False
    return any(wire_ip in network for network in settings.PYMTA_TRUSTED_PROXIES)


def _safe_hostname(raw: str | None, session=None, server=None) -> str | None:
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
        logger.debug("helo_control_chars", extra=_trace(server, session))
        return None
    return raw


# Recipients named in full up to this many; past it the line would be several
# kilobytes of one field, and a log with 100-address lines is one nobody reads.
# recipient_count carries the true number either way.
_MAX_LOGGED_RECIPIENTS = 10


def _recipients_field(rcpts: list[str]) -> str:
    if len(rcpts) <= _MAX_LOGGED_RECIPIENTS:
        return ",".join(rcpts)
    shown = ",".join(rcpts[:_MAX_LOGGED_RECIPIENTS])
    return f"{shown},+{len(rcpts) - _MAX_LOGGED_RECIPIENTS} more"


def _log_outcome(ctx, envelope, outcome, size, **extra) -> None:
    """One line per message, whatever happened to it.

    ``ctx`` is ``(server, session, started)``, bundled because the three always
    travel together and splitting them out pushes the signature past the
    argument-count budget for no gain in clarity.

    This is the audit record: the Postfix ``status=sent`` equivalent, and the
    only way to answer "what became of the mail from X to Y" from the log. The
    metrics count outcomes; they cannot tell you about *this* message.

    INFO, and bounded by the message rate rather than by anything a peer can
    spin: a connection that never delivers never reaches here.

    It names envelope addresses, as every MTA log does and as tracing a
    delivery complaint requires. That makes the log a store of personal data,
    so it inherits whatever retention policy the mail itself has.
    """
    server, session, started = ctx
    logger.info(
        "message_" + outcome,
        extra=_trace(
            server,
            session,
            sender=envelope.mail_from if envelope.mail_from != NULL_SENDER_SENTINEL else "<>",
            recipients=_recipients_field(envelope.rcpt_tos),
            recipient_count=len(envelope.rcpt_tos),
            size_bytes=size,
            duration_ms=int((time.monotonic() - started) * 1000),
            helo=getattr(session, "host_name", None),
            **extra,
        ),
    )


# EHLO extensions already reported by ``handle_EHLO``. Process-wide, so the
# warning is emitted once rather than once per connection.
_EXTENSIONS_REPORTED: set[str] = set()


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
        coalescing; the per-verb call ceiling lives in
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
                # Per verb: one shared reason could not tell AUTH on a public MX
                # from PIPELINING, and the log below fires once per process.
                metrics.SECURITY_REJECTIONS.labels(reason=f"ehlo_{verb.lower()}_stripped").inc()
                # Once per verb per process, not once per EHLO. The condition is
                # a misconfiguration that persists, so repeating it adds nothing
                # after the first line and would otherwise let a peer choose how
                # often we say it. Loud once beats quiet forever: demoting this
                # to DEBUG to bound the volume would hide AUTH being advertised
                # on a public MX. The counter carries the rate.
                if verb not in _EXTENSIONS_REPORTED:
                    _EXTENSIONS_REPORTED.add(verb)
                    logger.warning(
                        "ehlo_extension_stripped",
                        extra={
                            "verb": verb,
                            "detail": "the SMTP configuration should not advertise this",
                        },
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

        session.host_name = _safe_hostname(hostname, session=session, server=server)
        return clean

    async def handle_HELO(self, server, session, envelope, hostname):
        session.host_name = _safe_hostname(hostname, session=session, server=server)
        return f"250 {server.hostname}"

    # ------------------------------------------------------------------ MAIL
    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        counters = _counters(server, session)

        # Same hard-error budget as handle_RCPT: a peer can burn errors on
        # MAIL alone (malformed senders, bad SIZE), so the guard has to sit on
        # this verb too or the budget is trivially sidestepped.
        if _hard_error_limit_reached(server, counters):
            return "421 4.7.0 Too many errors, goodbye"

        try:
            clean = validate_envelope_address(
                address,
                allow_empty=True,
                max_local=settings.PYMTA_MAX_LOCAL_PART,
                max_domain=settings.PYMTA_MAX_DOMAIN,
            )
        except AddressError as err:
            metrics.SECURITY_REJECTIONS.labels(reason=err.reason).inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return f"{err.smtp_code} {err.smtp_text}"

        # Honour MAIL FROM:... SIZE=N if announced, to fail fast before DATA.
        for opt in mail_options or []:
            if opt.upper().startswith("SIZE="):
                try:
                    announced = int(opt.split("=", 1)[1])
                except ValueError:
                    _bump(counters, _SOFT_ERRORS_ATTR)
                    return "501 5.5.4 Bad SIZE parameter"
                if announced > settings.PYMTA_MAX_INCOMING_EMAIL_SIZE:
                    metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
                    _bump(counters, _SOFT_ERRORS_ATTR)
                    return "552 5.3.4 Message size exceeds fixed maximum"

        if clean and settings.PYMTA_BLOCKED_SENDER_DOMAINS:
            domain = clean.rpartition("@")[2].lower()
            if domain in settings.PYMTA_BLOCKED_SENDER_DOMAINS:
                metrics.SECURITY_REJECTIONS.labels(reason="blocked_sender_domain").inc()
                _bump(counters, _SOFT_ERRORS_ATTR)
                return "554 5.7.1 Sender domain not accepted"

        envelope.mail_from = clean if clean else NULL_SENDER_SENTINEL
        envelope.mail_options.extend(mail_options or [])
        return "250 2.1.0 OK"

    # ------------------------------------------------------------------ RCPT
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: PLR0911
        counters = _counters(server, session)

        # First gate: hard-error budget, shared with handle_MAIL.
        if _hard_error_limit_reached(server, counters):
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return "421 4.7.0 Too many errors, goodbye"

        # Panic switch. Checked before the MDA call so a deferring node costs
        # nothing upstream: the point is to stop touching the MDA, not to keep
        # asking it questions while refusing its answers.
        if settings.PYMTA_DEFER_ALL:
            metrics.SECURITY_REJECTIONS.labels(reason="defer_all").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            # Counts against the session's error budget like every other
            # refusal. Without this a sender could sit on one connection
            # issuing RCPTs for as long as command_call_limit allowed, which is
            # exactly the load a deferring node is trying to shed.
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "451 4.3.2 Service temporarily unavailable, please retry"

        # Per-envelope recipient cap.
        if len(envelope.rcpt_tos) >= settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE:
            metrics.SECURITY_REJECTIONS.labels(reason="max_recipients_per_envelope").inc()
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

        # Case-insensitive both sides. Stricter than the MDA's mailbox lookup,
        # which matches local_part case-sensitively; blocking more spellings
        # than exist is the safe direction. Fixing the MDA half is a follow-up.
        if clean.lower() in settings.PYMTA_BLOCKED_RECIPIENTS:
            metrics.SECURITY_REJECTIONS.labels(reason="blocked_recipient").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "550 5.1.1 Recipient not accepted"

        result = await self.mda.check_recipient(clean, session=_session_id(server))
        if result.temp_fail:
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "451 4.3.0 Recipient verification temporarily unavailable"
        # Unreachable while ``check_recipient`` names no permanent statuses:
        # every non-200 there comes back as a temp_fail above. Kept as the
        # landing spot if one is ever added.
        if not result.ok:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "550 5.1.1 Recipient verification failed"

        # Only an explicit true/false for this exact address is a verdict. The
        # MDA answers with one boolean per address it was asked about, so a
        # missing key or any other type means the body is not the answer we
        # asked for: an empty or unparseable body, a proxy's own 200 page, or a
        # response shape that has drifted. Reading that as "no such mailbox"
        # would bounce a working address on someone else's bug, so it defers
        # like every other unusable check response.
        verdict = result.payload.get(clean)
        if not isinstance(verdict, bool):
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            logger.warning(
                "mda_check_no_verdict",
                extra=_trace(
                    server,
                    session,
                    status=result.status_code,
                    verdict_type=type(verdict).__name__,
                ),
            )
            return "451 4.3.0 Recipient verification temporarily unavailable"

        if not verdict:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            misses = _bump(counters, _RCPT_MISSES_ATTR)
            if misses >= settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION:
                metrics.SECURITY_REJECTIONS.labels(reason="max_rcpt_misses_per_session").inc()
                metrics.DISCONNECTS_421.labels(reason="max_rcpt_misses_per_session").inc()
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
        if envelopes > settings.PYMTA_MAX_ENVELOPES_PER_SESSION:
            metrics.SECURITY_REJECTIONS.labels(reason="max_envelopes_per_session").inc()
            metrics.DISCONNECTS_421.labels(reason="max_envelopes_per_session").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            # 421 rather than 451: the session has spent its envelope budget and
            # nothing the peer does can win it back, so keeping the channel open
            # only invites more attempts that must all fail. 421 says "closing
            # transmission channel", which is precisely what happens next.
            _request_disconnect(server)
            return "421 4.7.0 Too many messages this session, closing connection"

        started = time.monotonic()
        content: bytes = envelope.content or b""

        # Before the size check: normalising can only grow the body (each bare
        # LF gains a CR), and the cap has to apply to the bytes we actually send.
        content, normalized = _normalize_line_endings(content)
        if normalized:
            metrics.BARE_NEWLINE_MESSAGES.inc()
            # Drop the pre-normalisation copy now. Otherwise both buffers stay
            # reachable for the whole deliver call, up to PYMTA_DATA_TIMEOUT,
            # which would make a rewritten message cost twice the memory of a
            # clean one for as long as the MDA takes to answer. aiosmtpd
            # discards this envelope wholesale in ``_set_post_data_state`` the
            # moment this hook returns and never reads it again.
            envelope.content = None
            envelope.original_content = None
            logger.debug("bare_newline_normalised", extra=_trace(server, session))

        # NUL bytes have no place in an RFC 5321 message and break downstream
        # C parsers. Reject before we pay the cost of the deliver call.
        if b"\x00" in content:
            metrics.SECURITY_REJECTIONS.labels(reason="nul_byte").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "554 5.6.0 NUL byte in message body"

        if len(content) > settings.PYMTA_MAX_INCOMING_EMAIL_SIZE:
            # aiosmtpd already replies 552 itself when the in-flight DATA
            # exceeds data_size_limit, so reaching here is defensive only.
            # Distinct from oversize_announced: nothing was announced here, the
            # body itself came in over the cap.
            metrics.SECURITY_REJECTIONS.labels(reason="oversize_message").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            _bump(counters, _SOFT_ERRORS_ATTR)
            return "552 5.3.4 Message size exceeds fixed maximum"

        try:
            # Before building the coroutine: a budget already spent raises
            # here, and a coroutine created but never awaited would only add a
            # RuntimeWarning to the log on the way to the same 451.
            budget = _remaining_data_budget(server)
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
                    client_helo=_safe_hostname(
                        getattr(session, "host_name", None), session=session, server=server
                    ),
                    session=_session_id(server),
                ),
                timeout=budget,
            )
        except TimeoutError:
            # Without its own reason this is indistinguishable from an MDA
            # defer, and the two call for opposite responses: raise the budget
            # versus go fix the MDA.
            metrics.SECURITY_REJECTIONS.labels(reason="data_timeout").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            metrics.MESSAGE_BYTES.observe(len(content))
            _bump(counters, _SOFT_ERRORS_ATTR)
            logger.debug(
                "data_timeout",
                extra=_trace(
                    server,
                    session,
                    limit_seconds=settings.PYMTA_DATA_TIMEOUT,
                    size_bytes=len(content),
                ),
            )
            return "451 4.3.0 Delivery timed out, please retry"

        metrics.MESSAGE_BYTES.observe(len(content))

        if result.ok and result.payload.get("status") == "ok":
            metrics.MESSAGES_TOTAL.labels(result="delivered").inc()
            _log_outcome((server, session, started), envelope, "delivered", len(content))
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
            _log_outcome(
                (server, session, started),
                envelope,
                "deferred",
                len(content),
                status=result.status_code,
            )
            return "451 4.3.0 Delivery temporarily unavailable"
        metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
        _bump(counters, _SOFT_ERRORS_ATTR)
        _log_outcome(
            (server, session, started),
            envelope,
            "rejected",
            len(content),
            status=result.status_code,
        )
        return "554 5.6.0 Message rejected by delivery agent"

    # ------------------------------------------------------------------ PROXY
    async def handle_PROXY(self, server, session, envelope, proxy_data):
        """Apply admission control once PROXY-protocol parsing is done.

        Routing the gate through here (rather than at SMTP-connect time)
        means we count sessions against the real client IP carried in the
        PROXY header, not against the load-balancer's IP. Without this,
        every session behind HAProxy would be bucketed under one address
        and the per-source share would silently turn into a global
        cap.

        Returning False makes aiosmtpd drop the connection without ever
        starting the SMTP dialogue.
        """
        if not _proxy_header_is_trusted(session):
            metrics.SECURITY_REJECTIONS.labels(reason="untrusted_proxy").inc()
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_untrusted_proxy").inc()
            # DEBUG, not WARNING: any host that can reach port 25 provokes this
            # once per connection, so a higher level hands the internet control
            # of our log volume. The two counters above are where the volume
            # belongs, and pymta_security_rejections_total{reason="untrusted_proxy"}
            # is what an alert should watch.
            logger.debug(
                "proxy_header_untrusted",
                extra={
                    "session_id": _session_id(server),
                    "wire_peer": _wire_ip(session),
                    "claimed_src": getattr(proxy_data, "src_addr", None),
                    "detail": "add to PYMTA_TRUSTED_PROXIES if this is a real balancer",
                },
            )
            return False

        real_ip = "unknown"
        claimed = getattr(proxy_data, "src_addr", None) if proxy_data is not None else None
        # Only an address that parses becomes the gate key. aiosmtpd validates
        # the INET families but assigns AF_UNIX's src_addr straight off the wire,
        # so a PROXY v2 UNIX header hands us an arbitrary 108-byte string. Used
        # as a key it is a fresh bucket per connection: the per-source share
        # stops applying and the dict grows without bound. Falling back to the
        # shared "unknown" bucket makes an unparseable claim cost the sender
        # capacity rather than win it.
        if claimed is not None and settings.parse_client_ip(str(claimed)) is not None:
            real_ip = str(claimed)
            # Stash on the server so the real client IP outlives the STARTTLS
            # session rebuild that would otherwise drop session.proxy_data.
            setattr(server, _PROXY_SRC_ATTR, (real_ip, getattr(proxy_data, "src_port", None)))
        if proxy_data is not None:
            # Permanent forensic record: ties the SMTP session to the real
            # origin IP carried in the PROXY header. Every other mail.log line
            # is keyed on session.peer (the load balancer), so this is the only
            # place the true client IP is recorded. Logging peer alongside src
            # also surfaces misconfigurations at a glance: src == peer means the
            # header is not carrying a real origin.
            logger.debug(
                "proxy_header",
                extra={
                    "session_id": _session_id(server),
                    "client_ip": getattr(proxy_data, "src_addr", None),
                    "client_port": getattr(proxy_data, "src_port", None),
                    "wire_peer": _wire_ip(session),
                    "version": getattr(proxy_data, "version", None),
                    "protocol": getattr(proxy_data, "protocol", None),
                },
            )
        return await server.acquire_gate_post_proxy(real_ip)

    # ------------------------------------------------------------------ misc
    async def handle_exception(self, error: BaseException) -> str:
        # Never leak stack traces or internal hostnames in SMTP replies.
        metrics.SECURITY_REJECTIONS.labels(reason="internal_error").inc()
        metrics.DISCONNECTS_421.labels(reason="internal_error").inc()
        logger.exception("handler_error")
        return "421 4.3.0 Internal error, please try again later"
