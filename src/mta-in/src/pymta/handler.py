"""What to do with a sender, a recipient and a message: the queue-less flow.

Called by :class:`pymta.smtp_protocol.SMTPConnection` once a command is
well-formed and in order. Every hook gets the connection (``server``), its
``session`` and the ``envelope`` of the transaction in progress, and returns
the SMTP reply.

For each SMTP transaction the handler

1. records the EHLO/HELO name in a form that can be stored,
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

from . import logfmt, metrics, settings
from .address import AddressError, strip_brackets, validate_envelope_address
from .mda_async import MDAClient, MDAResult

logger = logging.getLogger(__name__)


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


# Unknown recipients a session may name while the server is close to full
# (see IPGate.under_stress): the sessions that collect refusals are the first
# to give their slot back.
_STRESS_MAX_RCPT_MISSES = 2


def rcpt_miss_budget(server) -> int:
    """Unknown recipients a session may name before it is closed."""
    limit = settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION
    return min(limit, _STRESS_MAX_RCPT_MISSES) if server.under_stress() else limit


def _trace(server, session, **fields):
    """Fields every per-connection line carries, so lines can be joined up."""
    return {"session_id": server.session_id, "client_ip": _peer_ip(session), **fields}


def _wire_ip(session) -> str | None:
    """The TCP peer's address, which behind a balancer is the balancer."""
    return str(session.peer[0]) if session.peer else None


def _peer_ip(session) -> str | None:
    """The client's address: the one a PROXY header named, else the TCP peer.

    With PROXY protocol on, the TCP peer is the balancer, and returning it
    would attribute the mail to our own infrastructure: a plausible-looking
    but wrong IP in the Received header. A header that names no client
    therefore gives None; the MDA omits the trace when a part is missing.
    """
    if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        return session.proxy_data.src_addr if session.proxy_data else None
    return _wire_ip(session)


def _peer_port(session) -> str | None:
    if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        port = session.proxy_data.src_port if session.proxy_data else None
        return None if port is None else str(port)
    return str(session.peer[1]) if session.peer else None


def _remaining_data_budget(server) -> float:
    """Seconds still available for the MDA deliver call.

    ``PYMTA_DATA_TIMEOUT`` is one budget covering the whole DATA phase, and the
    protocol arms its timer at exactly that value, so the body receive has
    already spent part of it, and we stop short of the rest. The reserve is
    what buys the 451 on the timeout path: expiring at the same instant as the
    protocol's timer would be a race we'd usually lose (its timer was scheduled
    first, and equal deadlines fire in scheduling order), leaving the peer with
    a bare disconnect instead of a reason to retry.

    The budget comes from the protocol, which recorded the value it armed the
    timer at, rather than from settings, so the deliver call is sized against
    the deadline that is actually ticking.

    Raises ``TimeoutError`` when the receive already spent the whole budget:
    issuing the deliver call anyway would overrun the deadline the reserve
    exists to stay clear of, and the caller already turns that exception into
    the 451.
    """
    spent = time.monotonic() - server.data_phase_started
    remaining = server.data_phase_budget - spent - _REPLY_RESERVE_SECONDS
    if remaining <= 0:
        raise TimeoutError("DATA budget spent before the deliver call")
    return remaining


# Any line ending, CRLF first so a well-formed one is consumed whole rather
# than as a bare CR followed by a bare LF.
#
# ``\n\.`` is a bare LF followed by a dot: the LF is bare because a CRLF would
# have been consumed by the first alternative. Replacing the pair with CRLF
# drops that dot, which is the dot-stuffing the protocol left in place: it
# only unstuffs lines that ended in CRLF on the wire.
_LINE_ENDING_RE = re.compile(rb"\r\n|\n\.|\r|\n")


def _normalize_line_endings(content: bytes) -> tuple[bytes, bool]:
    """Rewrite every bare LF and bare CR to CRLF. Returns (content, changed).

    One rule for the whole message, headers included, as Postfix does. A bare
    LF inside a header therefore becomes a line break. Turning it into a space
    there instead was tried and dropped: it cannot be told from the line
    breaks of a sender that ends every line with LF without guessing, and a
    guess is something a sender can steer.

    The protocol ends DATA only on ``\\r\\n.\\r\\n``, so a bare LF cannot split
    one message into two (SMTP smuggling). What it can do is split a line: the
    protocol reads ``Subject: a<LF>X-Evil: b`` as one line and hands it to us
    intact, while any parser that also breaks on LF reads two headers.
    Python's ``email`` package does, and the MDA uses it, so the sender gets
    to choose a header on the stored message.

    Postfix closes this with ``smtpd_forbid_bare_newline = normalize`` (pinned in
    etc/main.cf). This is the same defence.

    Normalising rather than rejecting matches Postfix and keeps the mail: a
    message is not spam for having been assembled by a sloppy mailer. It does
    rewrite bytes, so a DKIM body hash over the original would break. But a body
    carrying bare LF has already failed DKIM canonicalisation, which is defined
    over CRLF, so there is no intact signature left to preserve.

    A line that followed a bare LF also loses one leading dot. A sender that
    ends its lines with LF dot-stuffs by its own line boundaries, and the
    protocol undoes the stuffing only at CRLF ones, so those dots would otherwise reach
    the mailbox doubled. Postfix's ``normalize`` does the same, and the same
    way: a lone ``.`` after a bare LF is not the end of DATA, it becomes an
    empty line.

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


def _transport(session, envelope) -> dict:
    """How this message arrived, for the ``Received`` header the MDA writes.

    The MDA writes that header, not this server: it already prepends
    ``Return-Path``, one writer keeps the two in a fixed order, and the body
    is not copied here a second time to put a line in front of it.

    ``protocol`` is the ``with`` keyword: RFC 3848 (ESMTP, ESMTPS) and
    RFC 6531 (UTF8SMTP, UTF8SMTPS). The trailing S is what says the session
    was encrypted; the TLS version and cipher say how well.
    """
    cipher = session.ssl["cipher"] if session.ssl else None
    if envelope.smtp_utf8:
        protocol = "UTF8SMTP"
    elif session.extended_smtp:
        protocol = "ESMTP"
    else:
        protocol = "SMTP"
    if session.ssl and protocol != "SMTP":
        protocol += "S"
    return {
        "protocol": protocol,
        "tls_version": cipher[1] if cipher else None,
        "tls_cipher": cipher[0] if cipher else None,
        "server_name": settings.PYMTA_SMTP_HOSTNAME,
    }


def _untagged(address: str) -> str:
    """``user+tag@domain`` as ``user@domain``. Unchanged without a delimiter."""
    delimiter = settings.PYMTA_RECIPIENT_DELIMITER
    local, at, domain = address.rpartition("@")
    if not delimiter or delimiter not in local:
        return address
    return local.split(delimiter, 1)[0] + at + domain


def _proxy_header_is_trusted(session) -> bool:
    """True when the *wire* peer is allowed to speak PROXY protocol to us.

    The protocol reads a PROXY header from whoever sent it. Since the header
    dictates both the rate-limit key and the ``client_address`` the MDA
    records, an unfiltered one hands a direct connector a free pass past
    every per-IP cap and the ability to attribute its mail to any address it
    names.

    An *empty* allowlist means "no upstream named", and there is nothing left
    to filter on: every header is trusted, exactly as if the allowlist were
    0.0.0.0/0. That is a deliberate escape hatch for deployments whose balancer
    addresses are not known at boot, and it puts the whole trust boundary on
    the network isolation; ``server._check_proxy_trust_config`` warns loudly
    about it at startup. Naming the balancer is the safe configuration.
    """
    if not settings.PYMTA_TRUSTED_PROXIES:
        return True
    wire_ip = settings.parse_client_ip(_wire_ip(session))
    # None for a peer without an address: a member of no network.
    return wire_ip is not None and any(
        wire_ip in network for network in settings.PYMTA_TRUSTED_PROXIES
    )


def _safe_hostname(raw: str, session, server) -> str:
    """Return ``raw`` in a form the MDA can use: each offending character as ``?``.

    Replaced: CRLF/NUL/TAB, since the MDA writes this into a ``Received``
    header, and bytes that are not UTF-8. The command parser decodes those
    as lone surrogates, which cannot be stored by the MDA (PostgreSQL refuses
    them in JSON), so the delivery would fail on every retry.

    Replaced, not dropped: MAIL is answered ``503 send HELO first`` while
    ``session.host_name`` is empty, which would bounce the mail of a sender
    whose only fault is its greeting. ``session_closed`` still carries what
    the peer sent, escaped, in ``helo``.
    """
    clean = "".join("?" if char in _FORBIDDEN_HOSTNAME_CHARS else char for char in raw)
    clean = clean.encode("utf-8", "replace").decode("utf-8")
    if clean != raw:
        metrics.SECURITY_REJECTIONS.labels(reason="bad_helo").inc()
        logger.debug(
            "helo_sanitised",
            extra=_trace(
                server,
                session,
                helo=logfmt.clip(raw, _MAX_LOGGED_ADDRESS),
                forwarded=logfmt.clip(clean, _MAX_LOGGED_ADDRESS),
            ),
        )
    return clean


# Recipients named in full up to this many; past it the line would be several
# kilobytes of one field, and a log with 100-address lines is one nobody reads.
# recipient_count carries the true number either way.
_MAX_LOGGED_RECIPIENTS = 10

# RFC 5321 §4.5.3.1.3 caps a forward/reverse path at 256 octets. A refusal can
# name an address that never passed validation, and MAIL FROM is not held to
# that cap, so bound what reaches the log. The tail is kept so the domain of a
# long SRS return path stays searchable.
_MAX_LOGGED_ADDRESS = 256
_LOGGED_ADDRESS_TAIL = 128


def _recipients_field(rcpts: list[str]) -> str:
    return logfmt.clip_list(rcpts, _MAX_LOGGED_RECIPIENTS)


def _address_field(address) -> str:
    """A peer-supplied address, bounded, for a log field."""
    return logfmt.clip(address or "", _MAX_LOGGED_ADDRESS, tail=_LOGGED_ADDRESS_TAIL)


def _sender_field(envelope) -> str:
    """The envelope sender as logged: ``<>`` for bounces.

    Carried on every RCPT refusal so that searching the log for the sending
    domain finds them. Without it a refusal names only our own mailbox, and
    "why did mail from X never arrive?" has to be answered by joining lines.
    """
    return _address_field(envelope.mail_from) or "<>"


def _log_reject(server, session, verb, reason, **extra) -> None:
    """One line per refused command, at INFO.

    The counterpart to :func:`_log_outcome`: that one records what became of a
    message, this one records why a command never became part of any. Without
    it the most common failure of all, an unknown recipient, leaves nothing
    behind but a counter, and "why did mail from X bounce?" has no answer in
    the log at all.

    Bounded per connection by ``PYMTA_MAX_ERRORS_PER_SESSION`` (50): every
    caller either spends a unit of that budget or asks for the disconnect
    outright, so a peer cannot spin more of these than the budget allows.
    Unknown recipients stop even sooner, at
    ``PYMTA_MAX_RCPT_MISSES_PER_SESSION`` (10).

    One ``event=reject`` for every refusal, with the verb and the reason as
    fields rather than baked into the name, so ``event=reject`` groups them
    all and ``reason=`` splits them. ``reason`` reuses the metrics label of
    the same rejection wherever one exists, so a spike on a counter and the
    lines explaining it group by the same word.

    Names the address, as :func:`_log_outcome` does and as every MTA's reject
    log does; the same retention consequence applies.
    """
    logger.info("reject", extra=_trace(server, session, verb=verb, reason=reason, **extra))
    # The protocol writes a ``reject`` line of its own for every 4xx/5xx
    # that has none. This one has.
    server.refusal_logged = True


def _refuse(server, session, verb, reason, reply, **extra) -> str:
    """Spend one unit of the error budget, log the refusal, return the reply."""
    server.errors += 1
    _log_reject(server, session, verb, reason, code=int(reply[:3]), **extra)
    return reply


def _log_outcome(ctx, envelope, outcome, size, **extra) -> None:
    """One line per message, whatever happened to it.

    ``ctx`` is ``(server, session, started)``.

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
    server.refusal_logged = True
    logger.info(
        "message_" + outcome,
        extra=_trace(
            server,
            session,
            sender=_sender_field(envelope),
            recipients=_recipients_field(envelope.rcpt_tos),
            recipient_count=len(envelope.rcpt_tos),
            size_bytes=size,
            duration_ms=int((time.monotonic() - started) * 1000),
            helo=session.host_name,
            **extra,
        ),
    )


class InboundHandler:
    """One instance per server process; called concurrently from many sessions."""

    def __init__(self, mda_client: MDAClient):
        self.mda = mda_client

    # ------------------------------------------------------------------ EHLO
    async def handle_HELO(self, server, session, envelope, hostname) -> None:
        """Record the name a peer greeted with, EHLO or HELO alike."""
        session.host_name = _safe_hostname(hostname, session, server)

    # ------------------------------------------------------------------ MAIL
    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        # Here rather than after the body: the message over the budget would
        # otherwise be uploaded in full before being refused.
        if server.envelopes >= settings.PYMTA_MAX_ENVELOPES_PER_SESSION:
            metrics.SECURITY_REJECTIONS.labels(reason="max_envelopes_per_session").inc()
            metrics.DISCONNECTS_421.labels(reason="max_envelopes_per_session").inc()
            server.request_disconnect()
            return _refuse(
                server,
                session,
                "mail",
                "max_envelopes_per_session",
                "421 4.7.0 Too many messages this session, closing connection",
                sender=_address_field(address),
                envelopes=server.envelopes,
            )

        try:
            clean = validate_envelope_address(
                address,
                allow_empty=True,
                max_local=None,
                max_domain=settings.PYMTA_MAX_DOMAIN,
                allow_percent=True,
                require_fqdn=settings.PYMTA_REQUIRE_FQDN_SENDER,
                allow_quoted=True,
                # An address literal is not a fully-qualified domain either.
                allow_literal=not settings.PYMTA_REQUIRE_FQDN_SENDER,
            )
        except AddressError as err:
            metrics.SECURITY_REJECTIONS.labels(reason=err.reason).inc()
            return _refuse(
                server,
                session,
                "mail",
                err.reason,
                f"{err.smtp_code} {err.smtp_text}",
                sender=_address_field(address),
            )

        if clean and settings.PYMTA_BLOCKED_SENDER_DOMAINS:
            domain = clean.rpartition("@")[2].lower()
            if domain in settings.PYMTA_BLOCKED_SENDER_DOMAINS:
                metrics.SECURITY_REJECTIONS.labels(reason="blocked_sender_domain").inc()
                return _refuse(
                    server,
                    session,
                    "mail",
                    "blocked_sender_domain",
                    "554 5.7.1 Sender domain not accepted",
                    sender=_address_field(clean),
                )

        # "" for the null sender (MAIL FROM:<>): a bounce.
        envelope.mail_from = clean
        envelope.mail_options.extend(mail_options)
        return "250 2.1.0 OK"

    # ------------------------------------------------------------------ RCPT
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: PLR0911
        sender = _sender_field(envelope)

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
            return _refuse(
                server,
                session,
                "rcpt",
                "defer_all",
                "451 4.3.2 Service temporarily unavailable, please retry",
                sender=sender,
                recipient=_address_field(address),
            )

        # Per-envelope recipient cap.
        if len(envelope.rcpt_tos) >= settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE:
            metrics.SECURITY_REJECTIONS.labels(reason="max_recipients_per_envelope").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return _refuse(
                server,
                session,
                "rcpt",
                "max_recipients_per_envelope",
                "452 4.5.3 Too many recipients",
                sender=sender,
                recipient=_address_field(address),
                recipient_count=len(envelope.rcpt_tos),
            )

        # RFC 5321 §4.5.1: the bare ``postmaster``, with no domain, must be
        # accepted. It goes to the configured mailbox and is then checked with
        # the MDA like any other recipient. Unset, it is refused below for
        # having no domain.
        if (
            settings.PYMTA_POSTMASTER_ADDRESS
            and strip_brackets(address or "").lower() == "postmaster"
        ):
            address = settings.PYMTA_POSTMASTER_ADDRESS

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
            return _refuse(
                server,
                session,
                "rcpt",
                err.reason,
                f"{err.smtp_code} {err.smtp_text}",
                sender=sender,
                recipient=_address_field(address),
            )

        # Case-insensitive both sides, and deliberately a wider fold than the
        # MDA's: it ASCII-folds a local part, this also folds the code points
        # that are not ASCII but lower onto it. The difference can only block
        # spellings the MDA would refuse anyway, never let one through, so it
        # stays on the blocking side of the denylist.
        #
        # Also matched without its tag, so blocking ``victim@`` covers
        # ``victim+anything@``.
        folded = clean.lower()
        if settings.PYMTA_BLOCKED_RECIPIENTS and (
            folded in settings.PYMTA_BLOCKED_RECIPIENTS
            or _untagged(folded) in settings.PYMTA_BLOCKED_RECIPIENTS
        ):
            metrics.SECURITY_REJECTIONS.labels(reason="blocked_recipient").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            return _refuse(
                server,
                session,
                "rcpt",
                "blocked_recipient",
                "550 5.1.1 Recipient not accepted",
                sender=sender,
                recipient=_address_field(clean),
            )

        result = await self.mda.check_recipient(clean, session=server.session_id)
        if result.temp_fail:
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return _refuse(
                server,
                session,
                "rcpt",
                "mda_temp_fail",
                "451 4.3.0 Recipient verification temporarily unavailable",
                sender=sender,
                recipient=_address_field(clean),
                status=result.status_code,
            )
        # Unreachable while ``check_recipient`` names no permanent statuses:
        # every non-200 there comes back as a temp_fail above. Kept as the
        # landing spot if one is ever added.
        if not result.ok:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            return _refuse(
                server,
                session,
                "rcpt",
                "mda_verification_failed",
                "550 5.1.1 Recipient verification failed",
                sender=sender,
                recipient=_address_field(clean),
                status=result.status_code,
            )

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
            server.errors += 1
            # Stays a warning rather than becoming a ``_log_reject`` line: it
            # reports the MDA breaking its contract, not the peer doing
            # anything wrong, and that deserves the higher level. It is still
            # this refusal's one line, so it carries the recipient too.
            server.refusal_logged = True
            logger.warning(
                "mda_check_no_verdict",
                extra=_trace(
                    server,
                    session,
                    sender=sender,
                    recipient=_address_field(clean),
                    status=result.status_code,
                    verdict_type=type(verdict).__name__,
                ),
            )
            return "451 4.3.0 Recipient verification temporarily unavailable"

        if not verdict:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            server.rcpt_misses += 1
            misses = server.rcpt_misses
            if misses >= rcpt_miss_budget(server):
                metrics.SECURITY_REJECTIONS.labels(reason="max_rcpt_misses_per_session").inc()
                metrics.DISCONNECTS_421.labels(reason="max_rcpt_misses_per_session").inc()
                server.request_disconnect()
                # One line per refusal: this address is unknown *and* it spent
                # the session's last miss, so the reason that names the
                # disconnect is the one worth having.
                reason, reply = (
                    "max_rcpt_misses_per_session",
                    "421 4.7.0 Too many unknown recipients, goodbye",
                )
            else:
                reason, reply = "unknown_recipient", "550 5.1.1 No such recipient"
            return _refuse(
                server,
                session,
                "rcpt",
                reason,
                reply,
                sender=sender,
                recipient=_address_field(clean),
                misses=misses,
            )

        envelope.rcpt_tos.append(clean)
        envelope.rcpt_options.extend(rcpt_options)
        metrics.RCPT_TOTAL.labels(result="accepted").inc()
        return "250 2.1.5 OK"

    # ------------------------------------------------------------------ DATA
    async def handle_DATA(self, server, session, envelope):
        # Counted whatever becomes of it. handle_MAIL is where the budget is
        # applied, before the next message is uploaded.
        server.envelopes += 1
        started = time.monotonic()
        content: bytes = envelope.content

        # Before the size check: normalising can only grow the body (each bare
        # LF gains a CR), and the cap has to apply to the bytes we actually send.
        content, normalized = _normalize_line_endings(content)
        if normalized:
            metrics.BARE_NEWLINE_MESSAGES.inc()
            # Drop the pre-normalisation copy now. Otherwise both buffers stay
            # reachable for the whole deliver call, up to PYMTA_DATA_TIMEOUT,
            # which would make a rewritten message cost twice the memory of a
            # clean one for as long as the MDA takes to answer. The protocol
            # discards this envelope the moment this hook returns.
            envelope.content = None
            logger.debug("bare_newline_normalised", extra=_trace(server, session))

        # NUL bytes have no place in an RFC 5321 message and break downstream
        # C parsers. Reject before we pay the cost of the deliver call.
        if b"\x00" in content:
            metrics.SECURITY_REJECTIONS.labels(reason="nul_byte").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            return _refuse(
                server,
                session,
                "data",
                "nul_byte",
                "554 5.6.0 NUL byte in message body",
                sender=_sender_field(envelope),
                recipients=_recipients_field(envelope.rcpt_tos),
                recipient_count=len(envelope.rcpt_tos),
                size_bytes=len(content),
            )

        if len(content) > settings.PYMTA_MAX_INCOMING_EMAIL_SIZE:
            # The protocol refuses a body over the cap as it comes in. This is
            # for one that was under it on the wire and is over it once every
            # bare LF has gained its CR.
            metrics.SECURITY_REJECTIONS.labels(reason="oversize_message").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            return _refuse(
                server,
                session,
                "data",
                "oversize_message",
                "552 5.3.4 Message size exceeds fixed maximum",
                sender=_sender_field(envelope),
                recipients=_recipients_field(envelope.rcpt_tos),
                recipient_count=len(envelope.rcpt_tos),
                size_bytes=len(content),
            )

        try:
            # Before building the coroutine: a budget already spent raises
            # here, and a coroutine created but never awaited would only add a
            # RuntimeWarning to the log on the way to the same 451.
            budget = _remaining_data_budget(server)
            result: MDAResult = await asyncio.wait_for(
                self.mda.deliver(
                    message=content,
                    sender=envelope.mail_from,
                    original_recipients=list(envelope.rcpt_tos),
                    client_address=_peer_ip(session),
                    client_port=_peer_port(session),
                    # We do not reverse-DNS ourselves: the MDA inserts its
                    # own Received header using metadata and can decide what
                    # to do with the missing hostname.
                    client_hostname=None,
                    client_helo=session.host_name,
                    session=server.session_id,
                    transport=_transport(session, envelope),
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
            return _refuse(
                server,
                session,
                "data",
                "data_timeout",
                "451 4.3.0 Delivery timed out, please retry",
                sender=_sender_field(envelope),
                recipients=_recipients_field(envelope.rcpt_tos),
                limit_seconds=settings.PYMTA_DATA_TIMEOUT,
                size_bytes=len(content),
            )

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
            server.errors += 1
            _log_outcome(
                (server, session, started),
                envelope,
                "deferred",
                len(content),
                status=result.status_code,
            )
            return "451 4.3.0 Delivery temporarily unavailable"
        metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
        server.errors += 1
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

        Returning False makes the protocol drop the connection without ever
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
                    "session_id": server.session_id,
                    "wire_peer": _wire_ip(session),
                    "claimed_src": proxy_data.src_addr,
                    "detail": "add to PYMTA_TRUSTED_PROXIES if this is a real balancer",
                },
            )
            return False

        # Ties the session to the origin the header names. Logging the wire
        # peer alongside also shows a misconfiguration at a glance: the two
        # being equal means the header is not carrying a real origin.
        logger.debug(
            "proxy_header",
            extra={
                "session_id": server.session_id,
                "client_ip": proxy_data.src_addr,
                "client_port": proxy_data.src_port,
                "wire_peer": _wire_ip(session),
                "version": proxy_data.version,
            },
        )
        # A header that names no client (a health check, a UNIX socket) shares
        # one bucket: it costs the sender capacity rather than winning it any.
        return await server.admit(proxy_data.src_addr or "unknown")
