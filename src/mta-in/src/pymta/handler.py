"""What to do with a sender, a recipient and a message: the queue-less flow.

Called by :class:`pymta.smtp_protocol.SMTPConnection` once a command is
well-formed and in order. Every hook gets the connection, which carries the
``session`` and the ``envelope`` of the transaction in progress. MAIL, RCPT
and DATA return a :class:`~pymta.smtp_protocol.Reply`; the connection sends
it, charges a refusal to the error budget and writes its ``reject`` line.

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
import time

from . import metrics, settings
from .address import AddressError, strip_brackets, validate_envelope_address
from .mda_async import MDAClient, MDAResult
from .session_log import address_field, recipients_fields
from .smtp_protocol import Reply

logger = logging.getLogger(__name__)


# Slice of PYMTA_DATA_TIMEOUT held back from the MDA deliver call, so there is
# room to send a 451 before the protocol's deadline closes the transport.
_REPLY_RESERVE_SECONDS = float(settings.PYMTA_REPLY_RESERVE_SECONDS)

# Never in an EHLO/HELO name we log or send to the MDA: CR, LF and NUL inject
# lines, TAB unfolds a header.
_FORBIDDEN_HOSTNAME_CHARS = frozenset({"\r", "\n", "\x00", "\t"})


# Unknown recipients a session may name while the server is close to full
# (see IPGate.under_stress): the sessions that collect refusals are the first
# to give their slot back.
_STRESS_MAX_RCPT_MISSES = 2


def rcpt_miss_budget(connection) -> int:
    """Unknown recipients a session may name before it is closed."""
    limit = settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION
    return min(limit, _STRESS_MAX_RCPT_MISSES) if connection.under_stress() else limit


def _trace(connection, **fields):
    """Fields every per-connection line carries, so lines can be joined up."""
    return {
        "session_id": connection.session_id,
        "client_ip": connection.session.client_ip,
        **fields,
    }


def _remaining_data_budget(connection) -> float:
    """Seconds still available for the MDA deliver call.

    ``PYMTA_DATA_TIMEOUT`` covers the whole DATA phase and the protocol's
    timer is armed at it, so the receive has spent part of it. We stop short
    of the rest by the reserve: expiring together with that timer would
    usually leave the peer a bare disconnect instead of the 451.

    Raises ``TimeoutError`` when the receive spent it all.
    """
    spent = time.monotonic() - connection.data_phase_started
    remaining = connection.data_phase_budget - spent - _REPLY_RESERVE_SECONDS
    if remaining <= 0:
        raise TimeoutError("DATA budget spent before the deliver call")
    return remaining


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


def _safe_hostname(raw: str, connection) -> str:
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
            extra=_trace(connection, helo=address_field(raw), forwarded=address_field(clean)),
        )
    return clean


def _refusal(reply: str, reason: str, **fields) -> Reply:
    """A refusal, with what its ``reject`` line says besides the reply.

    The line names the sender and the recipients accepted so far without
    being told. ``reason`` reuses the label of the metric counting the same
    refusal, so a spike on a counter and the lines explaining it group by the
    same word.
    """
    return Reply(reply, reason, fields)


def _message_fields(envelope, size: int) -> dict:
    """What a line about a whole message names: sender, recipients, size.

    Envelope addresses, as every MTA log has them and as tracing a delivery
    complaint requires. That makes the log a store of personal data, with the
    retention policy of the mail itself.
    """
    return {
        # ``<>`` for a bounce.
        "sender": address_field(envelope.mail_from) or "<>",
        **recipients_fields(envelope.rcpt_tos),
        "size_bytes": size,
    }


def _log_outcome(connection, started: float, outcome: str, size: int, **extra) -> None:
    """One line per message handed to the MDA, whatever became of it.

    The audit record, Postfix's ``status=sent``: the only way to answer "what
    became of the mail from X to Y" from the log. INFO, and bounded by the
    message rate: a connection that never delivers never reaches here.
    """
    logger.info(
        "message_" + outcome,
        extra=_trace(
            connection,
            **_message_fields(connection.envelope, size),
            duration_ms=int((time.monotonic() - started) * 1000),
            helo=connection.session.host_name,
            **extra,
        ),
    )


class InboundHandler:
    """One instance per server process; called concurrently from many sessions."""

    def __init__(self, mda_client: MDAClient):
        self.mda = mda_client

    # ------------------------------------------------------------------ EHLO
    async def handle_HELO(self, connection, hostname: str) -> str:
        """The name a peer greeted with, EHLO or HELO alike, as it is kept."""
        return _safe_hostname(hostname, connection)

    # ------------------------------------------------------------------ MAIL
    async def handle_MAIL(self, connection, address: str) -> Reply:
        # Here rather than after the body: the message over the budget would
        # otherwise be uploaded in full before being refused.
        if connection.envelopes >= settings.PYMTA_MAX_ENVELOPES_PER_SESSION:
            metrics.SECURITY_REJECTIONS.labels(reason="max_envelopes_per_session").inc()
            metrics.DISCONNECTS_421.labels(reason="max_envelopes_per_session").inc()
            connection.request_disconnect()
            return _refusal(
                "421 4.7.0 Too many messages this session, closing connection",
                "max_envelopes_per_session",
                envelopes=connection.envelopes,
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
            return _refusal(f"{err.smtp_code} {err.smtp_text}", err.reason)

        if clean and settings.PYMTA_BLOCKED_SENDER_DOMAINS:
            domain = clean.rpartition("@")[2].lower()
            if domain in settings.PYMTA_BLOCKED_SENDER_DOMAINS:
                metrics.SECURITY_REJECTIONS.labels(reason="blocked_sender_domain").inc()
                return _refusal(
                    "554 5.7.1 Sender domain not accepted",
                    "blocked_sender_domain",
                    sender=address_field(clean),
                )

        # "" for the null sender (MAIL FROM:<>): a bounce.
        connection.envelope.mail_from = clean
        return Reply("250 2.1.0 OK")

    # ------------------------------------------------------------------ RCPT
    async def handle_RCPT(self, connection, address: str) -> Reply:  # noqa: PLR0911
        envelope = connection.envelope

        # Before the MDA call: the switch is there to stop calling the MDA.
        if settings.PYMTA_DEFER_ALL:
            metrics.SECURITY_REJECTIONS.labels(reason="defer_all").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return _refusal(
                "451 4.3.2 Service temporarily unavailable, please retry",
                "defer_all",
                recipient=address_field(address),
            )

        # Per-envelope recipient cap.
        if len(envelope.rcpt_tos) >= settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE:
            metrics.SECURITY_REJECTIONS.labels(reason="max_recipients_per_envelope").inc()
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return _refusal(
                "452 4.5.3 Too many recipients",
                "max_recipients_per_envelope",
                recipient=address_field(address),
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
            return _refusal(
                f"{err.smtp_code} {err.smtp_text}", err.reason, recipient=address_field(address)
            )

        # Case-insensitive, with a wider fold than the MDA's: it ASCII-folds a
        # local part, this also folds the non-ASCII code points that lower
        # onto ASCII. The difference can only block spellings the MDA would
        # refuse anyway, never let one through.
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
            return _refusal(
                "550 5.1.1 Recipient not accepted",
                "blocked_recipient",
                recipient=address_field(clean),
            )

        if clean in envelope.rcpt_tos:
            # Given twice: accepted, and delivered once.
            metrics.RCPT_TOTAL.labels(result="accepted").inc()
            return Reply("250 2.1.5 OK")

        result = await self.mda.check_recipient(clean, session=connection.session_id)
        if not result.ok:
            # Whatever the status: no failed check is a verdict on the address.
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            return _refusal(
                "451 4.3.0 Recipient verification temporarily unavailable",
                "mda_temp_fail",
                recipient=address_field(clean),
                status=result.status_code,
            )

        # Only true or false for this address is a verdict. Anything else (an
        # empty body, a proxy's own 200 page, a changed response shape)
        # defers: read as "no such mailbox", it would bounce a working address.
        verdict = result.payload.get(clean)
        if not isinstance(verdict, bool):
            metrics.RCPT_TOTAL.labels(result="rejected_temp").inc()
            # A warning in place of the ``reject`` line: the MDA broke its
            # contract, the peer did nothing wrong.
            logger.warning(
                "mda_check_no_verdict",
                extra=_trace(
                    connection,
                    sender=address_field(envelope.mail_from) or "<>",
                    recipient=address_field(clean),
                    status=result.status_code,
                    verdict_type=type(verdict).__name__,
                ),
            )
            return Reply("451 4.3.0 Recipient verification temporarily unavailable", logged=True)

        if not verdict:
            metrics.RCPT_TOTAL.labels(result="rejected_perm").inc()
            connection.rcpt_misses += 1
            misses = connection.rcpt_misses
            if misses >= rcpt_miss_budget(connection):
                metrics.SECURITY_REJECTIONS.labels(reason="max_rcpt_misses_per_session").inc()
                metrics.DISCONNECTS_421.labels(reason="max_rcpt_misses_per_session").inc()
                connection.request_disconnect()
                # The address is unknown and it spent the session's last
                # miss: the reason that names the disconnect is the one kept.
                reply, reason = (
                    "421 4.7.0 Too many unknown recipients, goodbye",
                    "max_rcpt_misses_per_session",
                )
            else:
                reply, reason = "550 5.1.1 No such recipient", "unknown_recipient"
            return _refusal(reply, reason, recipient=address_field(clean), misses=misses)

        envelope.rcpt_tos.append(clean)
        metrics.RCPT_TOTAL.labels(result="accepted").inc()
        return Reply("250 2.1.5 OK")

    # ------------------------------------------------------------------ DATA
    async def handle_DATA(self, connection) -> Reply:
        session, envelope = connection.session, connection.envelope
        # Counted whatever becomes of it. handle_MAIL is where the budget is
        # applied, before the next message is uploaded.
        connection.envelopes += 1
        started = time.monotonic()
        # Line endings are CRLF only and the size is within the limit: the
        # protocol saw to both as it read the message.
        content: bytes = envelope.content

        # NUL bytes have no place in an RFC 5321 message and break downstream
        # C parsers.
        if b"\x00" in content:
            metrics.SECURITY_REJECTIONS.labels(reason="nul_byte").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            return _refusal(
                "554 5.6.0 NUL byte in message body",
                "nul_byte",
                **_message_fields(envelope, len(content)),
            )

        try:
            # Before building the coroutine: a budget already spent raises
            # here, and a coroutine never awaited would add a RuntimeWarning.
            budget = _remaining_data_budget(connection)
            result: MDAResult = await asyncio.wait_for(
                self.mda.deliver(
                    message=content,
                    sender=envelope.mail_from,
                    original_recipients=list(envelope.rcpt_tos),
                    # None when a PROXY header names no client: the balancer's
                    # address would attribute the mail to our own
                    # infrastructure.
                    client_address=session.client_ip,
                    client_port=None if session.client_port is None else str(session.client_port),
                    client_helo=session.host_name,
                    session=connection.session_id,
                    transport=_transport(session, envelope),
                ),
                timeout=budget,
            )
        except TimeoutError:
            # Its own reason: an MDA defer says "fix the MDA", this one says
            # "raise the budget".
            metrics.SECURITY_REJECTIONS.labels(reason="data_timeout").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            metrics.MESSAGE_BYTES.observe(len(content))
            return _refusal(
                "451 4.3.0 Delivery timed out, please retry",
                "data_timeout",
                **_message_fields(envelope, len(content)),
                limit_seconds=settings.PYMTA_DATA_TIMEOUT,
            )

        metrics.MESSAGE_BYTES.observe(len(content))

        if result.ok and result.payload.get("status") == "ok":
            metrics.MESSAGES_TOTAL.labels(result="delivered").inc()
            _log_outcome(connection, started, "delivered", len(content))
            return Reply("250 2.0.0 Message accepted for delivery")

        # Anything but a clear "ok" defers. On a 207 the MDA delivered to some
        # recipients only: a retry of the whole envelope reaches the rest,
        # with duplicates for the others. The Postfix milter does the same.
        # Permanent refusals: see ``mda_async._PERMANENT_STATUSES``.
        #
        # ``logged``: the outcome line stands in for the ``reject`` line.
        if result.temp_fail or result.ok:
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            _log_outcome(connection, started, "deferred", len(content), status=result.status_code)
            return Reply("451 4.3.0 Delivery temporarily unavailable", logged=True)
        metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
        _log_outcome(connection, started, "rejected", len(content), status=result.status_code)
        return Reply("554 5.6.0 Message rejected by delivery agent", logged=True)
