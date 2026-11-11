"""What is logged about one SMTP session, and when.

Two kinds of line come from here:

* ``reject``, at INFO, for a 4xx/5xx the handler did not log itself: a command
  refused for its syntax or its place, a line that is not a command, a limit
  of the protocol.
* ``session_closed``, once per connection: who connected, what they said, how
  it ended. This is Postfix's ``disconnect from`` line, and the only trace of
  a sender whose mail never became a message.

:class:`SessionLog` keeps the tally they need. The connection tells it what
happens (a command starts, a reply goes out, the session ends); it reads the
rest off the connection.
"""

from __future__ import annotations

import logging
import time

from . import logfmt, metrics, settings
from .command import split_path, strip_keyword

# The lines are the protocol's, whichever module writes them.
logger = logging.getLogger("pymta.smtp_protocol")

# Bounds on what the log quotes back from the peer.
MAX_LOGGED_REPLY = 200
MAX_LOGGED_ARG = 256
# Kept from the end of a clipped sender, so its domain stays searchable.
_LOGGED_SENDER_TAIL = 128
# Why we hung up on a peer that had not been refused anything.
_UNPROMPTED_CLOSES = ("command_timeout", "session_timeout", "reclaimed")
_MAX_LOGGED_RECIPIENTS = 10

# ``reason`` of a refusal that names none, by reply code.
_REASONS = {
    500: "bad_syntax",
    501: "bad_syntax",
    502: "not_implemented",
    503: "bad_sequence",
    530: "tls_required",
    552: "too_much_data",
    553: "malformed_address",
    555: "unknown_parameter",
}


def ip_of(peer) -> str | None:
    """Address out of a peer tuple, so the field is comparable.

    Logging the raw ``('10.0.0.1', 51234)`` would make ``client_ip`` a different
    string on every connection from the same host, which is useless to group by.
    """
    return str(peer[0]) if peer else None


def _sender_of(arg: str | None) -> str | None:
    """The address a MAIL argument names, or the raw argument if it has none."""
    split = split_path(strip_keyword("FROM:", arg) or "")
    if split is None:
        return logfmt.clip(arg, MAX_LOGGED_ARG)
    return logfmt.clip(split[0], MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL)


def _quotable(line: bytes) -> str:
    """A command line that was refused unparsed, fit for a log."""
    text = line.decode("utf-8", "replace").strip()
    if text[:4].upper() == "AUTH":
        return "AUTH [redacted]"
    # The tail too: on a too-long MAIL line that is where the domain is.
    return logfmt.clip(text, MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL)


class SessionLog:
    """The tally of one connection, and the lines written from it."""

    def __init__(self, connection):
        self._connection = connection
        self._started = time.monotonic()
        # verb -> [succeeded, attempted], in order of first use.
        self._commands: dict[str, list[int]] = {}
        # The command now running, and the code of its last reply.
        self._verb: str | None = None
        self._arg: str | None = None
        self._last_code: int | None = None
        self._error_replies = 0
        # (verb, reply, argument) of the last 4xx/5xx sent.
        self._last_error: tuple[str | None, str, str | None] | None = None
        self._sender: str | None = None
        self._greeted_name: str | None = None
        # A 4xx/5xx went out in answer to something the peer sent.
        self._peer_refused = False
        self._reject_lines = 0
        # Names of the MAIL/RCPT parameters that were accepted and ignored.
        self._ignored_parameters: set[str] = set()
        self._written = False
        # Why the STARTTLS handshake failed, when it did.
        self.tls_error: str | None = None

    # ----------------------------------------------------------- what happens
    def command_started(self, verb: str, arg: str | None) -> None:
        self._verb, self._arg, self._last_code = verb, arg, None
        if verb == "AUTH" and arg:
            # The argument can carry credentials (AUTH PLAIN <base64>), and
            # we refuse every AUTH, so it would land in last_error_arg.
            self._arg = "[redacted]"
        elif verb == "MAIL" and not self._connection.in_transaction():
            # Not while a transaction is open: a nested MAIL is refused and
            # the first sender stays.
            self._sender = _sender_of(arg)
        elif verb in ("EHLO", "HELO"):
            self._greeted_name = logfmt.clip(arg, MAX_LOGGED_ARG)

    def command_finished(self, failed: bool) -> None:
        """Count the command. ``failed``: it raised, or was cut off, unanswered."""
        if self._verb is None:
            # The summary already counted it: the connection went meanwhile.
            return
        # A command that did not fail has sent a reply.
        outcome = "error" if failed else f"{self._last_code // 100}xx"
        metrics.COMMANDS_TOTAL.labels(verb=self._verb, **{"class": outcome}).inc()
        counts = self._commands.setdefault(self._verb, [0, 0])
        counts[1] += 1
        # 3xx is not success: a 354 with nothing after it is a DATA phase the
        # connection did not survive.
        if outcome == "2xx":
            counts[0] += 1
        self._verb = self._arg = None

    def parameters_ignored(self, verb: str, parameters: list[str]) -> None:
        """``parameters``: each as ``NAME`` or ``NAME=value``."""
        for parameter in parameters:
            name = parameter.partition("=")[0]
            metrics.IGNORED_PARAMETERS.labels(parameter=name).inc()
            self._ignored_parameters.add(name)
        logger.debug(
            "parameters_ignored",
            extra={
                **self._who(),
                "verb": verb.lower(),
                "parameters": logfmt.clip(" ".join(parameters), MAX_LOGGED_ARG),
            },
        )

    def replied(self, text: str, reason: str | None, line: bytes | None) -> None:
        """Record a reply. ``text`` is its last line.

        ``reason`` names a refusal whose code alone does not. ``line`` is the
        raw line a refusal answers when no command was running.
        """
        code = int(text[:3])
        self._last_code = code
        if code < 400:
            return
        self._error_replies += 1
        metrics.REFUSALS_TOTAL.labels(verb=self._verb or "none", code=str(code)).inc()
        # A notice of ours answers nothing the peer sent.
        if self._connection.close_reason not in _UNPROMPTED_CLOSES:
            self._peer_refused = True
        reply = logfmt.clip(text, MAX_LOGGED_REPLY)
        if self._verb is not None:
            arg = logfmt.clip(self._arg, MAX_LOGGED_ARG)
        else:
            arg = _quotable(line) if line is not None else None
        self._last_error = (self._verb, reply, arg)
        self._reject(code, reply, arg, reason)

    # ------------------------------------------------------------------ lines
    def _who(self) -> dict:
        return {
            "session_id": self._connection.session_id,
            "client_ip": self._connection.client_address(),
        }

    def _transaction(self) -> tuple[str | None, list[str]]:
        """Sender and accepted recipients of the transaction now open, if any."""
        envelope = self._connection.envelope
        if envelope.mail_from is None:
            return None, []
        sender = logfmt.clip(envelope.mail_from or "<>", MAX_LOGGED_ARG, tail=_LOGGED_SENDER_TAIL)
        return sender, list(envelope.rcpt_tos)

    def open_sender(self) -> str | None:
        """The sender of the transaction now open, as logged."""
        return self._transaction()[0]

    def _reject(self, code: int, reply: str, arg: str | None, reason: str | None) -> None:
        """One ``reject`` line for a 4xx/5xx the handler did not log itself.

        ``session_closed`` keeps only the last error and the last sender. On a
        connection that carries several messages, the refusal of an earlier
        one would otherwise name neither.

        Not for timeouts or internal errors, which have their own line and
        whose open transaction ``session_closed`` reports. Not before the
        connection is admitted: those are counted, and are what a flood is
        made of. Capped per session, like the handler's refusals.
        """
        connection = self._connection
        if connection.refusal_logged or connection.close_reason is not None:
            return
        if not connection.admitted:
            return
        self._reject_lines += 1
        # The line that says why the session was closed is always written.
        if (
            self._reject_lines > settings.PYMTA_MAX_ERRORS_PER_SESSION
            and reason != "max_errors_per_session"
        ):
            return
        # Still the refused transaction: the envelope is reset after the reply.
        sender, recipients = self._transaction()
        if sender is None and self._verb == "MAIL":
            sender = self._sender
        if sender is None and self._verb is None and arg and arg[:10].upper() == "MAIL FROM:":
            # A MAIL refused before dispatch (530 without TLS, line too long):
            # the sender is in the line itself.
            sender = _sender_of(arg[5:])
            self._sender = self._sender or sender
        fields = {
            **self._who(),
            "verb": self._verb.lower() if self._verb else None,
            "reason": reason or _REASONS.get(code, "protocol"),
            "code": code,
            "reply": reply,
            "sender": sender,
            "arg": arg,
        }
        if recipients:
            fields["recipients"] = logfmt.clip_list(recipients, _MAX_LOGGED_RECIPIENTS)
            fields["recipient_count"] = len(recipients)
        logger.info("reject", extra=fields)

    def _commands_field(self) -> str:
        """Postfix's ``disconnect from`` tally: ``ehlo=1 mail=1 rcpt=0/1``.

        A bare count when every attempt succeeded, ``succeeded/attempted``
        otherwise, so the failing verb stands out at a glance.
        """
        return " ".join(
            f"{verb.lower()}={total}" if ok == total else f"{verb.lower()}={ok}/{total}"
            for verb, (ok, total) in self._commands.items()
        )

    def _closed_by(self, error) -> str:
        if self._connection.close_reason:
            return self._connection.close_reason
        # A QUIT that was answered 221, not one refused for its syntax.
        if self._commands.get("QUIT", (0, 0))[0]:
            return "quit"
        return "peer_closed" if error is None else "peer_reset"

    def _tls_fields(self) -> dict:
        if self.tls_error is not None:
            return {"tls": False, "tls_error": self.tls_error}
        tls = self._connection.session.ssl
        if tls is None:
            return {"tls": False}
        name, version, _ = tls["cipher"]
        return {"tls": True, "tls_cipher": name, "tls_version": version}

    def close(self, error) -> None:
        """Write ``session_closed``. Once, whoever asks first.

        INFO only for a session that was admitted and said something.
        Connections refused at the gate are counted by
        ``pymta_connections_total``; a connection that closes without a
        single command is a TCP health check.
        """
        if self._written:
            return
        self._written = True
        # Cut off mid-command, typically mid-DATA: the reply never went out.
        self.command_finished(failed=True)
        connection = self._connection
        if not connection.admitted:
            return
        closed_by = self._closed_by(error)
        fields = {
            **self._who(),
            "helo": self._greeted_name,
            **self._tls_fields(),
            "sender": self._sender,
            "commands": self._commands_field(),
            "errors": self._error_replies,
            "closed_by": closed_by,
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }
        if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
            fields["wire_peer"] = ip_of(connection.session.peer)
        if self._ignored_parameters:
            fields["ignored_parameters"] = ",".join(sorted(self._ignored_parameters))
        if self._last_error is not None:
            verb, reply, arg = self._last_error
            fields["last_error_verb"] = verb.lower() if verb else None
            fields["last_error_reply"] = reply
            fields["last_error_arg"] = arg
        # A transaction left open: these recipients never got their message.
        _, recipients = self._transaction()
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
