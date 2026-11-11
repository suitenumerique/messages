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

import collections
import contextlib
import functools
import logging
import secrets
import time
import weakref

from aiosmtpd.smtp import SMTP as BaseSMTP
from aiosmtpd.smtp import TLSSetupException, syntax

from . import logfmt, metrics, runtime, settings
from .limits import IPGate, LogThrottle, TooManyConnections

logger = logging.getLogger(__name__)

# Bounds on what the session summary quotes back from the peer. A reply is ours
# and short; an argument is the peer's, capped by aiosmtpd at 512 octets per
# command line (548 for MAIL), which is still more than a log line should carry.
_MAX_LOGGED_REPLY = 200
_MAX_LOGGED_ARG = 256
# Kept from the end of a clipped sender, so its domain stays searchable.
_LOGGED_SENDER_TAIL = 128
_MAX_LOGGED_RECIPIENTS = 10

# ``connection_capped`` at INFO: one line a minute per address, for at most
# this many addresses at a time.
_cap_log_throttle = LogThrottle(interval=60.0, max_keys=1024)

# Connections currently open, for log_abandoned_sessions.
_live_sessions: weakref.WeakSet[HardenedSMTP] = weakref.WeakSet()


def log_abandoned_sessions() -> None:
    """Write the summary of every session the shutdown deadline is cutting.

    The process exits without their ``connection_lost`` ever running, so a
    message in flight at that point would otherwise leave no line.
    """
    for session in list(_live_sessions):
        session._close_reason = session._close_reason or "shutdown"  # noqa: SLF001
        session._log_session(None)  # noqa: SLF001


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
      independently of how high ``line_length_limit`` goes. MAIL gets 548
      after EHLO: 26 more for SIZE and 10 for SMTPUTF8. See ``__init__`` and
      :meth:`smtp_EHLO` for what keeps that number fixed.
    * ``BOGUS_LIMIT = 5`` (module constant) — unrecognised commands per session
      before aiosmtpd hangs up. Narrower and stricter than
      ``PYMTA_MAX_ERRORS_PER_SESSION``, which counts every 4xx/5xx we emit.
      Inherited.

    ``local_part_limit`` is aiosmtpd's own address check and stays at its
    default of 0 (disabled) on purpose: :mod:`pymta.address` enforces the same
    RFC limit on RCPT, with a rejection reason and a metric label attached,
    and deliberately not on MAIL FROM, where SRS return paths exceed it.

    ``line_length_limit`` is the one we override. It has to be a class attribute
    because ``SMTP.__init__`` reads it while constructing the ``StreamReader``,
    before any instance attribute of ours could exist.
    """

    line_length_limit = settings.PYMTA_MAX_LINE_LENGTH

    def __init__(self, *args, ip_gate: IPGate | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # aiosmtpd keeps this dict on the class: every EHLO from any connection
        # adds to the shared MAIL limit and every new connection resets it.
        # Shadowed per instance so one session's limit does not depend on the
        # others.
        self.command_size_limits = collections.defaultdict(lambda: self.command_size_limit)
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

        # Session accounting, reported once by _log_session when the connection
        # ends. Kept on the protocol instance for the same reason as the
        # session id: aiosmtpd rebuilds ``session`` on STARTTLS.
        self._accepted_at = time.monotonic()
        # The client as the gate keyed it: PROXY source or TCP peer.
        self._client_ip: str | None = None
        # verb -> [succeeded, attempted], in order of first use.
        self._commands: dict[str, list[int]] = {}
        self._current_verb: str | None = None
        self._current_arg: str | None = None
        self._last_code: int | None = None
        self._error_replies = 0
        # (verb, reply, argument) of the last 4xx/5xx sent, whoever produced
        # it, for the session summary.
        self._last_error: tuple[str | None, str, str | None] | None = None
        self._sender: str | None = None
        self._helo: str | None = None
        self._tls_error: str | None = None
        self._close_reason: str | None = None
        self._session_logged = False
        # A 4xx/5xx went out in answer to something the peer sent, including
        # lines aiosmtpd refuses before dispatch (unknown verb, line too long).
        self._peer_refused = False
        # Set by the handler once it has logged the refusal it is about to
        # return, so _log_refusal does not add a second line for it.
        self.refusal_logged = False
        # Names a refusal of ours whose reply does not go through the handler.
        self._refusal_reason: str | None = None
        self._refusal_lines = 0
        # Last command line read. aiosmtpd refuses some lines before dispatch
        # (too long, unknown verb, call limit) and keeps nothing of them.
        self._last_line: bytes | None = None
        # Set once the gate lets the connection in, never cleared: unlike the
        # gate's own bookkeeping, it must still be true when the summary runs.
        self._admitted = False
        self._smtp_methods = {
            verb: self._accounted(verb, method) for verb, method in self._smtp_methods.items()
        }

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

    @syntax("EHLO hostname")
    async def smtp_EHLO(self, hostname: str) -> None:
        # aiosmtpd adds the SIZE and SMTPUTF8 allowances to the MAIL limit on
        # every EHLO without resetting it, so a repeated EHLO would raise it
        # each time.
        self.command_size_limits.clear()
        await super().smtp_EHLO(hostname)

    async def smtp_HELP(self, arg: str) -> None:
        # Default aiosmtpd HELP enumerates implemented verbs (mild info leak).
        await self.push("214 2.0.0 See https://www.rfc-editor.org/rfc/rfc5321")

    # ------------------------------------------------------ session deadline
    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        _live_sessions.add(self)
        self._arm_session_deadline()

    def connection_lost(self, error) -> None:
        if self._session_deadline_handle is not None:
            self._session_deadline_handle.cancel()
            self._session_deadline_handle = None
        _live_sessions.discard(self)
        self._log_session(error)
        super().connection_lost(error)

    def _cb_client_connected(self, reader, writer) -> None:
        super()._cb_client_connected(reader, writer)
        readuntil = reader.readuntil

        async def remember(*args, **kwargs):
            self._last_line = None
            line = await readuntil(*args, **kwargs)
            # Command lines only: inside DATA these are message content.
            if self._current_verb is None:
                self._last_line = line
            return line

        reader.readuntil = remember

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
        self._close_reason = "session_timeout"
        logger.debug(
            "session_timeout",
            extra={
                "session_id": self.session_id,
                "client_ip": self._client_address(),
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
        self._close_reason = "command_timeout"
        logger.debug(
            "command_timeout",
            extra={
                "session_id": self.session_id,
                "client_ip": self._client_address(),
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
        self._note_reply(status)
        await super().push(status)
        if self._disconnect_after_reply:
            self._disconnect_after_reply = False
            self._close_reason = self._close_reason or "server_closed"
            if self.transport is not None:
                self.transport.close()

    async def handle_exception(self, error: Exception) -> str:
        if isinstance(error, TLSSetupException):
            # The peer's handshake failed: no certificate it would accept, no
            # common protocol or cipher, or plaintext where TLS was due. Not a
            # fault of ours, so neither handler_error nor the internal_error
            # counters, which is where it used to land. aiosmtpd closes the
            # transport itself on this path and never sends the reply.
            #
            # The cause is the one useful fact: a sender that enforces TLS and
            # rejects our certificate shows up here and nowhere else, as a
            # session that said STARTTLS and was never heard from again.
            cause = error.__cause__ or error
            self._tls_error = logfmt.clip(f"{type(cause).__name__}: {cause}", _MAX_LOGGED_REPLY)
            self._close_reason = "tls_failed"
            return "454 4.7.0 TLS not available due to temporary reason"
        self._close_reason = "internal_error"
        logger.error(
            "handler_error",
            exc_info=error,
            extra={
                "session_id": self.session_id,
                "client_ip": self._client_address(),
                "sender": self._envelope_state()[0],
            },
        )
        # The handler answers 421 ("closing transmission channel"); make that
        # true. aiosmtpd would otherwise push it and carry on with a session
        # whose state we no longer trust.
        status = await super().handle_exception(error)
        self.request_disconnect()
        return status

    # ------------------------------------------------------ session summary
    def _accounted(self, verb: str, method):
        """Wrap one ``smtp_<VERB>`` so its outcome is counted.

        aiosmtpd dispatches every recognised command through ``_smtp_methods``,
        and a good share of them answer without reaching any handler hook. This
        is the one place every verb passes, whoever ends up replying.

        ``functools.wraps`` carries over the ``@syntax`` attributes aiosmtpd
        reads back off the method.
        """

        @functools.wraps(method)
        async def run(arg):
            self._current_verb, self._current_arg, self._last_code = verb, arg, None
            self.refusal_logged = False
            self._refusal_reason = None
            if verb == "AUTH" and arg:
                # The argument can carry credentials (AUTH PLAIN <base64>), and
                # we refuse every AUTH, so it would land in last_error_arg.
                self._current_arg = "[redacted]"
            elif verb == "MAIL":
                # Not while a transaction is open: aiosmtpd answers 503 to a
                # nested MAIL and keeps the first sender.
                if not getattr(self.envelope, "mail_from", None):
                    self._sender = self._sender_of(arg)
            elif verb in ("EHLO", "HELO"):
                self._helo = logfmt.clip(arg, _MAX_LOGGED_ARG)
            failed = True
            try:
                await method(arg)
                failed = False
            finally:
                # None when _log_session already settled it: the connection
                # went while this command was still running.
                if self._current_verb is not None:
                    self._count_command(verb, failed)
                self._current_verb = self._current_arg = None

        return run

    def _sender_of(self, arg: str | None) -> str | None:
        """The address a MAIL command names, as the handler's lines spell it.

        Parsed with aiosmtpd's own helpers so it reads the same way the command
        will, and falls back to the raw argument when it does not parse: that
        is the case worth seeing, and aiosmtpd is about to refuse it.
        """
        path = self._strip_command_keyword("FROM:", arg) if arg else None
        # None when it does not parse, "" when there is no address at all; the
        # null sender comes back as "<>".
        address = self._getaddr(path)[0] if path else None
        if not address:
            return logfmt.clip(arg, _MAX_LOGGED_ARG)
        return logfmt.clip(address, _MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL)

    def _envelope_state(self) -> tuple[str | None, list[str]]:
        """Sender and accepted recipients of the transaction now open, if any."""
        envelope = getattr(self, "envelope", None)
        sender = getattr(envelope, "mail_from", None)
        if not sender:
            return None, []
        return (
            logfmt.clip(sender, _MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL),
            list(getattr(envelope, "rcpt_tos", None) or []),
        )

    def _refused_line(self) -> str | None:
        """The command line a pre-dispatch refusal answered, fit for a log."""
        if self._last_line is None:
            return None
        text = self._last_line.decode("utf-8", "replace").strip()
        if text[:4].upper() == "AUTH":
            return "AUTH [redacted]"
        # The tail too: on a too-long MAIL line that is where the domain is.
        return logfmt.clip(text, _MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL)

    def _log_refusal(self, code: int, reply: str | None, arg: str | None) -> None:
        """One ``reject`` line for a 4xx/5xx the handler did not log itself.

        aiosmtpd answers a whole class of commands without calling a hook (552
        too much data, 555 unknown parameter, 553 malformed address, 503 bad
        sequence, 500 line too long), and ``session_closed`` keeps only the
        last error and the last sender. On a connection that carries several
        messages, the refusal of an earlier one would otherwise name neither.

        Not for timeouts or internal errors, which have their own line and
        whose open transaction ``session_closed`` reports. Capped per session,
        like the handler's refusals.
        """
        if self.refusal_logged or self._close_reason is not None:
            return
        if self._ip_gate is not None and not self._admitted:
            return
        self._refusal_lines += 1
        if self._refusal_lines > settings.PYMTA_MAX_ERRORS_PER_SESSION:
            return
        verb = self._current_verb
        # Still the refused transaction: aiosmtpd resets the envelope only
        # after it has pushed the reply.
        sender, recipients = self._envelope_state()
        if sender is None and verb == "MAIL":
            sender = self._sender
        fields = {
            "session_id": self.session_id,
            "client_ip": self._client_address(),
            "verb": verb.lower() if verb else None,
            "reason": self._refusal_reason or "protocol",
            "code": code,
            "reply": reply,
            "sender": sender,
            "arg": arg,
        }
        if recipients:
            fields["recipients"] = logfmt.clip_list(recipients, _MAX_LOGGED_RECIPIENTS)
            fields["recipient_count"] = len(recipients)
        logger.info("reject", extra=fields)

    def _count_command(self, verb: str, failed: bool) -> None:
        code = self._last_code
        if failed:
            outcome = "error"
        elif code is None:
            outcome = "none"
        else:
            outcome = f"{code // 100}xx"
        metrics.COMMANDS_TOTAL.labels(verb=verb, **{"class": outcome}).inc()
        counts = self._commands.setdefault(verb, [0, 0])
        counts[1] += 1
        # 3xx is not success: a 354 with nothing after it is a DATA phase the
        # connection did not survive.
        if outcome == "2xx":
            counts[0] += 1

    def _note_reply(self, status) -> None:
        text = status.decode("utf-8", "replace") if isinstance(status, bytes) else str(status)
        try:
            code = int(text[:3])
        except ValueError:
            return
        self._last_code = code
        if code >= 400:
            self._error_replies += 1
            # A timeout notice answers nothing the peer sent.
            if self._close_reason not in ("command_timeout", "session_timeout"):
                self._peer_refused = True
            reply = logfmt.clip(text, _MAX_LOGGED_REPLY)
            if self._current_verb is not None:
                arg = logfmt.clip(self._current_arg, _MAX_LOGGED_ARG)
            elif self._close_reason is None:
                # Refused before dispatch: the line itself is the argument.
                arg = self._refused_line()
            else:
                arg = None
            self._last_error = (self._current_verb, reply, arg)
            self._log_refusal(code, reply, arg)
            self.refusal_logged = False

    def _client_address(self) -> str | None:
        """The client: what the gate keyed on, else the TCP peer.

        The TCP peer is the balancer when PROXY protocol is on, and naming it
        would attribute the session to our own infrastructure.
        """
        if self._client_ip is not None:
            return self._client_ip
        if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
            return None
        return _ip_of(getattr(self.session, "peer", None) if self.session else None)

    def _commands_field(self) -> str:
        """Postfix's ``disconnect from`` tally: ``ehlo=1 mail=1 rcpt=0/1``.

        A bare count when every attempt succeeded, ``succeeded/attempted``
        otherwise, so the failing verb stands out at a glance.
        """
        parts = []
        for verb, (ok, total) in self._commands.items():
            parts.append(
                f"{verb.lower()}={total}" if ok == total else f"{verb.lower()}={ok}/{total}"
            )
        return " ".join(parts)

    def _closed_by(self, error) -> str:
        if self._close_reason:
            return self._close_reason
        # A QUIT that was answered 221, not one refused for its syntax.
        if self._commands.get("QUIT", (0, 0))[0]:
            return "quit"
        # aiosmtpd closes on its own after these two, without a hook: the
        # per-verb call limit (421) and too many unknown commands (502 ...
        # goodbye).
        if self._last_code == 421 or (self._last_error and "goodbye" in self._last_error[1]):
            return "server_closed"
        if error is not None:
            return "peer_reset"
        return "peer_closed"

    def _tls_fields(self) -> dict:
        if self._tls_error is not None:
            return {"tls": False, "tls_error": self._tls_error}
        info = getattr(self.session, "ssl", None) if self.session else None
        if not info:
            return {"tls": False}
        cipher = info.get("cipher") if isinstance(info, dict) else None
        fields: dict = {"tls": True}
        if cipher:
            fields["tls_cipher"], fields["tls_version"] = cipher[0], cipher[1]
        return fields

    def _log_session(self, error) -> None:
        """One line per connection, whatever became of it.

        The message lines (``message_*``) and refusals (``reject``) only exist
        for a sender that got far enough to be refused by *us*. A sender whose
        handshake failed, who was answered by aiosmtpd itself, who timed out or
        simply hung up mid-DATA left nothing at all, which is the "no trace of
        the mail in the log" case. This is the Postfix ``disconnect from`` line.

        INFO only for a session that was admitted and said something.
        Connections refused at the gate are counted by
        ``pymta_connections_total`` and are what a flood is made of; a
        connection that closes without a single command is a TCP health check.
        """
        if self._session_logged:
            return
        self._session_logged = True
        if self._current_verb is not None:
            # Cut off mid-command, typically mid-DATA: the reply never went out.
            self._count_command(self._current_verb, failed=True)
            self._current_verb = None
        if self._ip_gate is not None and not self._admitted:
            return
        closed_by = self._closed_by(error)
        fields = {
            "session_id": self.session_id,
            "client_ip": self._client_address(),
            "helo": self._helo,
            **self._tls_fields(),
            "sender": self._sender,
            "commands": self._commands_field(),
            "errors": self._error_replies,
            "closed_by": closed_by,
            "duration_ms": int((time.monotonic() - self._accepted_at) * 1000),
        }
        if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
            fields["wire_peer"] = _ip_of(getattr(self.session, "peer", None))
        if self._last_error is not None:
            verb, reply, arg = self._last_error
            fields["last_error_verb"] = verb.lower() if verb else None
            fields["last_error_reply"] = reply
            fields["last_error_arg"] = arg
        # A transaction left open: these recipients never got their message.
        _, recipients = self._envelope_state()
        if recipients:
            fields["recipients"] = logfmt.clip_list(recipients, _MAX_LOGGED_RECIPIENTS)
            fields["recipient_count"] = len(recipients)
        # Only where it is the explanation. After a QUIT or our own hang-up a
        # TLS peer routinely resets instead of closing, which is noise.
        if closed_by == "peer_reset":
            fields["error_type"] = type(error).__name__
        talked = bool(self._commands) or self._peer_refused
        if settings.PYMTA_LOG_SESSIONS and talked:
            logger.info("session_closed", extra=fields)
        else:
            logger.debug("session_closed", extra=fields)

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
            # A message with a sender and recipients is being deferred: logged
            # as a ``reject`` line by _log_refusal, under this reason.
            self._refusal_reason = "max_concurrent_data"
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
            if (
                self._proxy_timeout is not None
                and self._close_reason is None
                and not getattr(self.session, "proxy_data", None)
            ):
                # aiosmtpd hangs up on a missing or malformed PROXY header
                # without a hook. A balancer that stops sending it takes all
                # inbound mail down, and this is the only trace.
                metrics.CONNECTIONS_TOTAL.labels(result="rejected_invalid_proxy").inc()
                logger.debug(
                    "proxy_header_invalid",
                    extra={
                        "session_id": self.session_id,
                        "wire_peer": _ip_of(getattr(self.session, "peer", None)),
                    },
                )
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
        self._client_ip = ip if ip != "unknown" else None
        if runtime.is_draining():
            # Out of rotation, either because PYMTA_DRAIN says so or because a
            # SIGTERM started the shutdown. RFC 5321 §3.1 allows answering
            # with 421 instead of 220, and §5.1 has the sender move to the next
            # MX in preference order; Postfix and Exim both treat it as a
            # per-host defer, not a per-domain one. 4.3.2 is RFC 3463's "system
            # not accepting network messages", which is precisely this.
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_drain").inc()
            logger.debug(
                "connection_draining", extra={"session_id": self.session_id, "client_ip": ip}
            )
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
            # A sender held off by a cap is deferred mail, so it gets a line at
            # INFO, but a source sitting at its cap is refused on every
            # connection: one line per address per minute, the rest at DEBUG.
            fields = {"session_id": self.session_id, "client_ip": ip, "scope": exc.scope}
            suppressed = _cap_log_throttle.allow(ip) if settings.PYMTA_LOG_SESSIONS else None
            if suppressed is None:
                logger.debug("connection_capped", extra=fields)
            else:
                logger.info("connection_capped", extra={**fields, "suppressed": suppressed})
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
        self._admitted = True
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
