"""The SMTP dialogue of one inbound connection.

One :class:`SMTPConnection` per accepted TCP connection, as an asyncio
``Protocol``. It owns the protocol: admission, the greeting, reading and
checking command lines, the order commands may come in, DATA, STARTTLS and
the timeouts.

What to do with a sender, a recipient or a message is the handler's
(:mod:`pymta.handler`), called once the command is well-formed and in order.
What is logged about the session is :mod:`pymta.session_log`'s.

The command set is what an inbound-only port 25 needs: HELO, EHLO, MAIL, RCPT,
DATA, RSET, NOOP, QUIT, STARTTLS, and canned answers to VRFY, EXPN, AUTH and
HELP. PIPELINING and CHUNKING are not offered.

Reading order: the asyncio callbacks, how a connection ends, the session
(``_run`` and the command loop), replies, the commands, then the timers,
the shutdown and the gate.

Parts of the command handling are adapted from aiosmtpd's ``smtp.py``
(Copyright 2014-2021 The aiosmtpd Developers, Apache-2.0, see NOTICE), which
this server ran on before; the reply texts are kept from it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import secrets
import ssl
import time
import weakref

from . import logfmt, metrics, runtime, settings
from .command import (
    ArgumentNotAscii,
    CommandSyntaxError,
    parse_parameters,
    split_command,
    split_path,
    strip_keyword,
)
from .limits import IPGate, LogThrottle, TooManyConnections, source_key
from .proxy_protocol import ProxyHeader, read_proxy_header
from .session_log import MAX_LOGGED_REPLY, SessionLog, ip_of
from .wire import ConnectionClosed, LineReader, LineTooLong, stored_line

logger = logging.getLogger(__name__)

# ``connection_capped`` at INFO: one line a minute per address, for at most
# this many addresses at a time.
_cap_log_throttle = LogThrottle(interval=60.0, max_keys=1024)

# Connections currently open: for the shutdown, and to find an idle one whose
# slot a new source can have.
_live_connections: weakref.WeakSet[SMTPConnection] = weakref.WeakSet()

# RFC 5321 section 4.5.3.1.4. MAIL gets more after EHLO: see _line_limit.
_COMMAND_LINE_LIMIT = 512
# The session is closed on this many lines that are not a command.
_MAX_UNKNOWN_COMMANDS = 5
# Times a command may be sent in one session when the listener names no limit.
_DEFAULT_CALL_LIMIT = 20

# RFC 3207 section 4: all a peer may send in clear when TLS is required.
_ALLOWED_BEFORE_STARTTLS = frozenset({"NOOP", "EHLO", "STARTTLS", "QUIT"})

# MAIL/RCPT parameters accepted and ignored: the DSN ones (RFC 3461) and
# AUTH= (RFC 4954). The set Postfix accepts on our port 25 configuration.
# Ignoring them is safe because nothing here acts on them either way: this
# server never sends a DSN, and never relays, so there is no later hop to pass
# AUTH= to. REQUIRETLS, BY=, HOLDFOR= and the like ask for a behaviour and keep
# their 555.
_IGNORED_PARAMETERS = {
    "MAIL": frozenset({"AUTH", "RET", "ENVID"}),
    "RCPT": frozenset({"NOTIFY", "ORCPT"}),
}

# While the server is close to full (see IPGate.under_stress), the sessions
# that do nothing or collect refusals are the first to give their slot back:
# a short idle timeout between commands (Postfix's value), and a small error
# budget.
_STRESS_IDLE_TIMEOUT = 10.0
_STRESS_MAX_ERRORS = 5
# Bytes per second a message has to arrive at while the DATA slots are close
# to full (see IPGate.data_under_stress). Postfix's smtpd_min_data_rate.
_MIN_DATA_RATE = 500

# Commands after which the peer has to wait for the reply even where
# PIPELINING is offered (RFC 2920 section 3.1): each changes what the next
# bytes mean.
_LAST_OF_A_GROUP = frozenset({"EHLO", "HELO", "STARTTLS", "DATA"})

# Message bytes are collected in blocks of this size. See _read_data.
_DATA_BLOCK = 65536
# Lines of a message read before the event loop gets a turn.
_DATA_LINES_PER_TURN = 1024

_SIZE_VALUE = re.compile(r"[0-9]+\Z")

# Seconds a closed connection gets to take what was written to it.
_CLOSE_GRACE = 5.0

_INTERNAL_ERROR = "421 4.3.0 Internal error, please try again later"
_SHUTDOWN = "421 4.3.2 Service not available, try another MX"
_RECLAIMED = "421 4.4.2 Server busy, closing idle connection"
_TOO_MANY_ERRORS = "421 4.7.0 Too many errors, goodbye"
_UNSYNCHRONIZED = "554 5.5.0 Error: SMTP protocol synchronization"
_SIZE_EXCEEDED = "552 5.3.4 Message size exceeds fixed maximum"
# (reply, reason) of a message refused once it has been read to its end.
_LINE_TOO_LONG = ("500 Line too long (see RFC5321 4.5.3.1.6)", "line_too_long")
_TOO_MUCH_DATA = (_SIZE_EXCEEDED, "oversize_message")


class _DataOverflow(Exception):
    """Lines too long to keep have used up the message size limit."""


class _TLSFailed(Exception):
    """The STARTTLS handshake did not complete. The transport is closed."""


@dataclasses.dataclass
class Session:
    """What is known about the peer. Lasts the connection."""

    peer: tuple | None = None
    # Set by EHLO/HELO, forgotten by STARTTLS.
    host_name: str | None = None
    extended_smtp: bool = False
    # ``{"cipher": (name, version, bits)}`` once TLS is up.
    ssl: dict | None = None
    proxy_data: ProxyHeader | None = None


@dataclasses.dataclass
class Envelope:
    """One mail transaction, from MAIL to the end of DATA or RSET."""

    # None until MAIL is accepted. The null sender is the empty string.
    mail_from: str | None = None
    mail_options: list[str] = dataclasses.field(default_factory=list)
    smtp_utf8: bool = False
    rcpt_tos: list[str] = dataclasses.field(default_factory=list)
    rcpt_options: list[str] = dataclasses.field(default_factory=list)
    content: bytes | None = None


def close_idle_sessions() -> None:
    """At the start of a shutdown, release the sessions that are doing nothing.

    A sender keeps a connection open between messages. Left alone it holds the
    drain until ``PYMTA_SHUTDOWN_TIMEOUT`` and is then cut without a word.
    Sessions inside a command or a transaction are left to finish, and are
    told to go as soon as they are done.
    """
    for connection in list(_live_connections):
        connection.close_if_idle()


def log_abandoned_sessions() -> None:
    """Write the summary of every session the shutdown deadline is cutting.

    The process exits without their ``connection_lost`` ever running, so a
    message in flight at that point would otherwise leave no line.
    """
    for connection in list(_live_connections):
        connection.log_abandoned()


def _parameter(name: str, value: str | None) -> str:
    return name if value is None else f"{name}={value}"


class SMTPConnection(asyncio.Protocol):
    """One inbound SMTP connection. See the module docstring.

    The handler's hooks are optional. Without one, the command is accepted as
    it stands, which is what the protocol tests run on.
    """

    def __init__(  # noqa: PLR0913
        self,
        handler,
        *,
        ip_gate: IPGate,
        hostname: str,
        ident: str = "ESMTP",
        data_size_limit: int = 0,
        enable_smtputf8: bool = False,
        timeout: float = 300,
        require_starttls: bool = False,
        command_call_limit: dict[str, int] | None = None,
        proxy_protocol_timeout: float | None = None,
        tls_context: ssl.SSLContext | None = None,
    ):
        self.handler = handler
        self.hostname = hostname
        self.ident = ident
        self.data_size_limit = data_size_limit
        self.enable_smtputf8 = enable_smtputf8
        self.tls_context = tls_context
        self.require_starttls = bool(tls_context and require_starttls)
        self._timeout = timeout
        self._proxy_timeout = proxy_protocol_timeout
        self._call_limits = command_call_limit or {}
        self._gate = ip_gate
        self._commands = {
            "HELO": self._helo,
            "EHLO": self._ehlo,
            "MAIL": self._mail,
            "RCPT": self._rcpt,
            "DATA": self._data,
            "RSET": self._rset,
            "NOOP": self._noop,
            "QUIT": self._quit,
            "STARTTLS": self._starttls,
            "VRFY": self._vrfy,
            "EXPN": self._expn,
            "AUTH": self._auth,
            "HELP": self._help,
        }

        # --- What the handler and the session log read.
        # Correlation id for every line this connection produces. Random
        # rather than sequential: a counter would leak the server's total
        # traffic to anyone who connects twice.
        self.session_id = secrets.token_hex(4)
        self.session = Session()
        self.envelope = Envelope()
        self.transport: asyncio.Transport | None = None
        # What the session has used of its budgets. The handler adds to them;
        # nothing a peer can do (RSET, EHLO, STARTTLS) takes anything back.
        self.errors = 0  # 4xx/5xx replies sent
        self.rcpt_misses = 0  # recipients the MDA does not know
        self.envelopes = 0  # messages that reached the end of DATA
        # Set by the handler once it has logged the refusal it is about to
        # return, so the session log does not add a second line for it.
        self.refusal_logged = False
        # Monotonic timestamp of the 354 that opened the current DATA phase,
        # and the deadline its timer was armed at. For the handler to size
        # its own deadline. None outside DATA.
        self.data_phase_started: float | None = None
        self.data_phase_budget: float | None = None
        # Set once the gate has let the connection in, never cleared.
        self.admitted = False
        # Why we hung up, when we did: ``command_timeout``, ``shutdown``...
        self.close_reason: str | None = None

        # --- The connection's own state.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: LineReader | None = None
        self._task: asyncio.Task | None = None
        self._log = SessionLog(self)
        # The command now running, if any.
        self._verb: str | None = None
        # ``errors`` as it stood when that command was dispatched.
        self._errors_at_dispatch = 0
        # Set once we have decided to hang up: no further command is read.
        self._closing = False
        # Set once the timers are stopped and the summary written.
        self._ended = False
        # Set while a TLS handshake has the transport.
        self._handshaking = False
        # Set while the transport's write buffer is full.
        self._write_resumed: asyncio.Future | None = None
        # Set by request_disconnect(), consumed by the next reply.
        self._disconnect_after_reply = False
        # The client as the gate keyed it: PROXY source or TCP peer.
        self._client_ip: str | None = None
        # The gate key we hold a slot under (see limits.source_key).
        self._slot: str | None = None
        self._slot_taken_at = 0.0
        # When the last command was answered, or the slot taken.
        self._idle_since = 0.0
        self._idle_timer: asyncio.TimerHandle | None = None
        # What the idle timer was last armed at, for the timeout's log line.
        self._idle_timeout: float | None = None
        self._session_timer: asyncio.TimerHandle | None = None
        # While a message is being read: see _check_data_rate. Stopped by _data.
        self._rate_timer: asyncio.TimerHandle | None = None
        # Octets of the message being read, as stored.
        self._data_received = 0
        # Armed by _close: aborts a close the peer does not take.
        self._abort_timer: asyncio.TimerHandle | None = None

    # ------------------------------------------------------ asyncio callbacks
    def connection_made(self, transport) -> None:
        self._loop = asyncio.get_running_loop()
        self.transport = transport
        self.session.peer = transport.get_extra_info("peername")
        self._reader = LineReader(transport, settings.PYMTA_MAX_LINE_LENGTH)
        _live_connections.add(self)
        self._arm_session_timer()
        self._arm_idle_timer()
        self._task = self._loop.create_task(self._run())

    def data_received(self, data: bytes) -> None:
        self._reader.feed(data)

    def eof_received(self) -> bool:
        """The peer has finished sending. What is in progress is still answered.

        A sender may close its side right after the final dot. Dropping the
        connection there would cancel a delivery it is still waiting to hear
        about, and it would send the message again.

        The session ends at its next read. Not over TLS, where closing one
        side closes both.
        """
        self._reader.close()
        return self.session.ssl is None

    def connection_lost(self, error) -> None:
        if self._abort_timer is not None:
            self._abort_timer.cancel()
        self._end(error)
        # Whatever the session was doing, an MDA call included.
        self._task.cancel()

    def pause_writing(self) -> None:
        if self._write_resumed is None:
            self._write_resumed = self._loop.create_future()

    def resume_writing(self) -> None:
        if self._write_resumed is not None and not self._write_resumed.done():
            self._write_resumed.set_result(None)
        self._write_resumed = None

    # ------------------------------------------------- how a connection ends
    def _close(self) -> None:
        """Hang up: no further command is read, and the timers are stopped.

        What is already written gets ``_CLOSE_GRACE`` seconds to reach the
        peer. One that is not reading does not get to keep the connection,
        and with it a session slot, by leaving our last reply unread.
        """
        if self.transport.is_closing():
            return
        self._stop_reading()
        self.transport.close()
        self._abort_timer = self._loop.call_later(_CLOSE_GRACE, self.transport.abort)

    def _abort(self) -> None:
        """Drop the connection at once, unsent replies included."""
        self._stop_reading()
        self.transport.abort()

    def _stop_reading(self) -> None:
        self._closing = True
        self._stop_timers()
        # Ends the session task wherever it is waiting for input.
        self._reader.close()

    def _stop_timers(self) -> None:
        for timer in (self._idle_timer, self._session_timer):
            if timer is not None:
                timer.cancel()
        self._idle_timer = self._session_timer = None

    def _end(self, error) -> None:
        """Stop everything and write the summary. Once, whoever calls first."""
        if self._ended:
            return
        self._ended = True
        self._stop_reading()
        _live_connections.discard(self)
        self.resume_writing()
        try:
            self._log.close(error)
        except Exception:
            logger.exception("session_error", extra={"session_id": self.session_id})

    async def _close_with_notice(self, reply: str) -> None:
        """Say why, then hang up. Without a word where one cannot be said."""
        if self._write_resumed is not None or self._handshaking:
            # The peer is not reading what we send, so the notice would wait
            # behind the rest for ever. Or a TLS handshake has the transport,
            # and a line in clear would land in the middle of it.
            self._abort()
            return
        # Writing to a peer that has just left raises nothing: it is dropped.
        await self._reply(reply)
        self._close()

    def request_disconnect(self) -> None:
        """Close the connection once the reply now being sent has gone out.

        A 421 is a promise to hang up. Closing after the reply rather than
        here means the peer gets the reason before the FIN.
        """
        self._disconnect_after_reply = True

    # ------------------------------------------------------------ the session
    async def _run(self) -> None:
        try:
            if await self._enter() and await self._greet():
                await self._command_loop()
        except ConnectionClosed, ConnectionError, _TLSFailed:
            # The peer left, or its handshake failed. The summary says which.
            pass
        except Exception:
            # An exception inside a command is answered 421 (_internal_error).
            # One raised outside any command, in the gate or in the loop
            # itself, would end this task with nothing in the log.
            self.close_reason = self.close_reason or "internal_error"
            logger.exception(
                "session_error",
                extra={"session_id": self.session_id, "client_ip": self.client_address()},
            )
            metrics.SECURITY_REJECTIONS.labels(reason="internal_error").inc()
        finally:
            try:
                self._leave_gate()
                self._close()
            finally:
                self._end(None)

    async def _enter(self) -> bool:
        """Decide whether this connection gets a greeting.

        Without PROXY protocol the TCP peer is the client and is admitted at
        once. With it, the header is read first and the handler's
        ``handle_PROXY`` admits the address it names.
        """
        if self._proxy_timeout is None:
            return await self.admit(self._wire_peer_ip())
        try:
            header = await asyncio.wait_for(
                read_proxy_header(self._reader), timeout=self._proxy_timeout
            )
        except TimeoutError:
            header = None
        if header is None:
            # A balancer that stops sending the header takes all inbound
            # mail down, and this is the only trace.
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_invalid_proxy").inc()
            logger.debug(
                "proxy_header_invalid",
                extra={"session_id": self.session_id, "wire_peer": ip_of(self.session.peer)},
            )
            return False
        self.session.proxy_data = header
        hook = getattr(self.handler, "handle_PROXY", None)
        return hook is not None and await hook(self, self.session, self.envelope, header)

    async def _greet(self) -> bool:
        # Two turns of the loop: what the peer sent along with its connection
        # is in the buffer by then, without a wait that every sender would pay.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if self._reader.pending():
            await self._refuse_unsynchronized()
            return False
        await self._reply(f"220 {self.hostname} {self.ident}")
        return True

    async def _command_loop(self) -> None:  # noqa: PLR0912
        unknown_left = _MAX_UNKNOWN_COMMANDS
        calls_left: dict[str, int] = {}
        while not self._closing:
            try:
                raw = await self._reader.readline()
            except LineTooLong:
                await self._reader.skip_line()
                await self._refuse("500 Command line too long", "line_too_long", None)
                continue
            line = raw.rstrip(b"\r\n")
            try:
                verb, arg = split_command(line, utf8=self.enable_smtputf8)
            except ArgumentNotAscii:
                await self._refuse("500 Error: strict ASCII mode", "bad_syntax", raw)
                continue
            except CommandSyntaxError:
                # No command word at all: counted with the unrecognised ones.
                verb, arg = "", None
            if len(line) > self._line_limit(verb):
                await self._refuse("500 Command line too long", "line_too_long", raw)
                continue
            if (
                self.require_starttls
                and not self.session.ssl
                and verb not in _ALLOWED_BEFORE_STARTTLS
            ):
                await self._refuse("530 Must issue a STARTTLS command first", "tls_required", raw)
                continue
            command = self._commands.get(verb)
            if command is None:
                unknown_left -= 1
                if unknown_left < 1:
                    self.request_disconnect()
                    await self._refuse(
                        "502 5.5.1 Too many unrecognized commands, goodbye.",
                        "too_many_unknown_commands",
                        raw,
                    )
                elif verb:
                    # Without the verb: a reply that repeats the peer's bytes
                    # is what cross-protocol attacks are built on.
                    await self._refuse(
                        "500 5.5.2 Error: command not recognized", "unknown_command", raw
                    )
                else:
                    await self._refuse("500 Error: bad syntax", "bad_syntax", raw)
                continue
            left = calls_left.get(verb, self._call_limit(verb))
            if left < 1:
                metrics.DISCONNECTS_421.labels(reason="too_many_commands").inc()
                self.request_disconnect()
                await self._refuse(
                    f"421 4.7.0 {verb} sent too many times", "too_many_commands", raw
                )
                continue
            calls_left[verb] = left - 1
            try:
                await self._dispatch(verb, arg, command)
            except ConnectionClosed, ConnectionError, _TLSFailed:
                raise
            except Exception as error:  # noqa: BLE001
                # Whatever it is: logged with its traceback, answered 421.
                await self._internal_error(error)

    def _line_limit(self, verb: str) -> int:
        """Longest command line accepted, line ending excluded.

        MAIL gets 26 octets more for SIZE and 10 for SMTPUTF8 once they have
        been offered, as their RFCs specify (1870 and 6531).
        """
        if verb != "MAIL" or not self.session.extended_smtp:
            return _COMMAND_LINE_LIMIT
        return (
            _COMMAND_LINE_LIMIT
            + (26 if self.data_size_limit else 0)
            + (10 if self.enable_smtputf8 else 0)
        )

    def _call_limit(self, verb: str) -> int:
        return self._call_limits.get(verb, self._call_limits.get("*", _DEFAULT_CALL_LIMIT))

    async def _dispatch(self, verb: str, arg: str | None, command) -> None:
        """Run one recognised command, with the checks every command gets."""
        self._verb = verb
        self._errors_at_dispatch = self.errors
        self._log.command_started(verb, arg)
        # The full idle timeout while the command runs: an MDA call can take
        # longer than the one used under stress.
        self._arm_idle_timer(self._timeout)
        failed = True
        try:
            if verb in _LAST_OF_A_GROUP and self._reader.pending():
                await self._refuse_unsynchronized()
            elif self._leaving(verb):
                metrics.DISCONNECTS_421.labels(reason="shutdown").inc()
                self.request_disconnect()
                await self._reply(_SHUTDOWN, reason="shutdown")
                self.close_reason = "shutdown"
            elif verb not in ("QUIT", "DATA") and self._error_budget_spent():
                # QUIT is always answered. DATA too: its recipients were
                # accepted before the budget ran out, and refusing it would
                # only delay their message.
                await self._too_many_errors()
            else:
                await command(arg)
            failed = False
        finally:
            self._verb = None
            self._idle_since = time.monotonic()
            self._log.command_finished(failed)
            # The handler's claim covers this command's reply only. It makes
            # one on a 250 too (message_delivered).
            self.refusal_logged = False
            # Idle time counts from the reply, in the state the command left:
            # inside a transaction or not.
            if not self._closing and self.close_reason is None:
                self._arm_idle_timer()
                if runtime.is_shutting_down():
                    # It was busy when the shutdown began. If it is idle now,
                    # it is told to go, not left to hold the drain.
                    self.close_if_idle()

    async def _internal_error(self, error: Exception) -> None:
        """Answer an exception of ours with a 421 and hang up.

        Carrying on would mean serving a session whose state we no longer
        trust. Nothing of the exception goes on the wire.
        """
        self.close_reason = "internal_error"
        self.request_disconnect()
        logger.error(
            "handler_error",
            exc_info=error,
            extra={
                "session_id": self.session_id,
                "client_ip": self.client_address(),
                "sender": self._log.open_sender(),
            },
        )
        metrics.SECURITY_REJECTIONS.labels(reason="internal_error").inc()
        metrics.DISCONNECTS_421.labels(reason="internal_error").inc()
        await self._reply(_INTERNAL_ERROR)

    # ------------------------------------------------------------------ replies
    async def _reply(
        self, *lines: str, reason: str | None = None, refused_line: bytes | None = None
    ) -> None:
        """Send one reply, of one line or several, and record it.

        ``reason`` names a refusal in the log where its code does not.
        ``refused_line`` is what it answers when no command was running.
        """
        if lines[-1][0] in "45":
            self._spend_error()
        self._log.replied(lines[-1], reason, refused_line)
        encoding = "utf-8" if self.enable_smtputf8 else "ascii"
        self.transport.write(b"".join(line.encode(encoding) + b"\r\n" for line in lines))
        if self._write_resumed is not None:
            await self._write_resumed
        if self._disconnect_after_reply:
            self._disconnect_after_reply = False
            self.close_reason = self.close_reason or "server_closed"
            self._close()

    async def _refuse(self, reply: str, reason: str, line: bytes | None) -> None:
        """Answer a line that was refused before any command ran.

        Held to the error budget like any command: a peer that sends nothing
        but lines of this kind would otherwise never meet it.
        """
        if self._error_budget_spent():
            await self._too_many_errors(line)
        else:
            await self._reply(reply, reason=reason, refused_line=line)

    async def _too_many_errors(self, line: bytes | None = None) -> None:
        metrics.SECURITY_REJECTIONS.labels(reason="max_errors_per_session").inc()
        metrics.DISCONNECTS_421.labels(reason="max_errors_per_session").inc()
        self.request_disconnect()
        await self._reply(_TOO_MANY_ERRORS, reason="max_errors_per_session", refused_line=line)

    async def _refuse_unsynchronized(self) -> None:
        """Answer a peer that did not wait for our reply, as Postfix does.

        PIPELINING is not offered, and even where it is, EHLO, STARTTLS and
        DATA must be the last command of a group (RFC 2920). Input already
        waiting behind one of them, or before the greeting, comes from a script
        that ignores replies, or from an attempt to smuggle commands past a
        point where state changes (the plaintext behind STARTTLS, a body sent
        before the 354).
        """
        metrics.SECURITY_REJECTIONS.labels(reason="protocol_synchronization").inc()
        self.request_disconnect()
        await self._reply(_UNSYNCHRONIZED, reason="protocol_synchronization")

    # ------------------------------------------------------------- error budget
    def _error_budget_spent(self) -> bool:
        """True once the session has collected all the 4xx/5xx it may."""
        budget = settings.PYMTA_MAX_ERRORS_PER_SESSION
        if self.under_stress():
            budget = min(budget, _STRESS_MAX_ERRORS)
        return self.errors >= budget

    def _spend_error(self) -> None:
        """Charge the error budget for a 4xx/5xx the handler did not charge.

        The handler counts the refusals it makes. Once per command; a line
        refused before dispatch has no command and always counts.
        """
        if self._verb is None or self.errors == self._errors_at_dispatch:
            self.errors += 1

    # ----------------------------------------------------------------- commands
    def in_transaction(self) -> bool:
        """True from an accepted MAIL to the end of DATA, RSET or a new greeting."""
        return self.envelope.mail_from is not None

    async def _needs_helo(self) -> bool:
        if self.session.host_name:
            return False
        await self._reply("503 Error: send HELO first")
        return True

    async def _helo(self, arg: str | None) -> None:
        if not arg:
            await self._reply("501 Syntax: HELO hostname")
            return
        await self._greeted_as(arg, extended=False)
        await self._reply(f"250 {self.hostname}")

    async def _ehlo(self, arg: str | None) -> None:
        if not arg:
            await self._reply("501 Syntax: EHLO hostname")
            return
        lines = [f"250-{self.hostname}"]
        if self.data_size_limit:
            lines.append(f"250-SIZE {self.data_size_limit}")
        lines.append("250-8BITMIME")
        if self.enable_smtputf8:
            lines.append("250-SMTPUTF8")
        if self.tls_context and not self.session.ssl:
            lines.append("250-STARTTLS")
        lines.append("250 HELP")
        await self._greeted_as(arg, extended=True)
        await self._reply(*lines)

    async def _greeted_as(self, name: str, *, extended: bool) -> None:
        """Start afresh under ``name``: any open transaction is dropped."""
        self.envelope = Envelope()
        self.session.extended_smtp = extended
        hook = getattr(self.handler, "handle_HELO", None)
        if hook is None:
            self.session.host_name = name
        else:
            await hook(self, self.session, self.envelope, name)

    async def _path_and_parameters(self, verb: str, keyword: str, arg: str | None):
        """Parse ``MAIL FROM:`` or ``RCPT TO:``. Returns (address, parameters).

        None once a refusal has been sent. The parameters we ignore are
        already taken out of what is returned.
        """
        syntax = f"501 Syntax: {verb} {keyword} <address>"
        if self.session.extended_smtp:
            syntax += " [SP <mail-parameters>]"
        text = strip_keyword(keyword, arg)
        if not text:
            await self._reply(syntax)
            return None
        split = split_path(text)
        if split is None:
            await self._reply("553 5.1.3 Error: malformed address")
            return None
        address, parameter_text = split
        parameters = parse_parameters(parameter_text)
        if parameters is None:
            await self._reply(syntax)
            return None
        ignored = [name for name in parameters if name in _IGNORED_PARAMETERS[verb]]
        if ignored:
            self._log.parameters_ignored(
                verb, [_parameter(name, parameters.pop(name)) for name in ignored]
            )
        if parameters and not self.session.extended_smtp:
            # Parameters came with ESMTP: none after HELO.
            await self._reply(syntax)
            return None
        return address, parameters

    async def _mail(self, arg: str | None) -> None:  # noqa: PLR0911
        if await self._needs_helo():
            return
        parsed = await self._path_and_parameters("MAIL", "FROM:", arg)
        if parsed is None:
            return
        address, parameters = parsed
        if self.in_transaction():
            await self._reply("503 Error: nested MAIL command")
            return
        options = [_parameter(name, value) for name, value in parameters.items()]
        if (parameters.pop("BODY", "7BIT") or "").upper() not in ("7BIT", "8BITMIME"):
            await self._reply("501 Error: BODY can only be one of 7BIT, 8BITMIME")
            return
        smtp_utf8 = "SMTPUTF8" in parameters
        if smtp_utf8 and parameters.pop("SMTPUTF8") is not None:
            await self._reply("501 Error: SMTPUTF8 takes no arguments")
            return
        if smtp_utf8 and not self.enable_smtputf8:
            await self._reply("501 Error: SMTPUTF8 disabled")
            return
        if "SIZE" in parameters:
            size = parameters.pop("SIZE") or ""
            if not _SIZE_VALUE.match(size):
                await self._reply("501 5.5.4 Bad SIZE parameter", reason="bad_size_parameter")
                return
            if self.data_size_limit and int(size) > self.data_size_limit:
                # Refused here, before the message is uploaded.
                metrics.SECURITY_REJECTIONS.labels(reason="oversize_announced").inc()
                await self._reply(_SIZE_EXCEEDED, reason="oversize_announced")
                return
        if parameters:
            await self._reply("555 MAIL FROM parameters not recognized or not implemented")
            return
        self.envelope.smtp_utf8 = smtp_utf8
        hook = getattr(self.handler, "handle_MAIL", None)
        if hook is None:
            self.envelope.mail_from = address
            self.envelope.mail_options.extend(options)
            await self._reply("250 OK")
        else:
            await self._reply(await hook(self, self.session, self.envelope, address, options))

    async def _rcpt(self, arg: str | None) -> None:
        if await self._needs_helo():
            return
        if not self.in_transaction():
            await self._reply("503 Error: need MAIL command")
            return
        parsed = await self._path_and_parameters("RCPT", "TO:", arg)
        if parsed is None:
            return
        address, parameters = parsed
        if parameters:
            await self._reply("555 RCPT TO parameters not recognized or not implemented")
            return
        hook = getattr(self.handler, "handle_RCPT", None)
        if hook is None:
            self.envelope.rcpt_tos.append(address)
            await self._reply("250 OK")
        else:
            await self._reply(await hook(self, self.session, self.envelope, address, []))

    async def _rset(self, arg: str | None) -> None:
        if arg:
            await self._reply("501 Syntax: RSET")
            return
        self.envelope = Envelope()
        await self._reply("250 OK")

    async def _noop(self, arg: str | None) -> None:
        await self._reply("250 OK")

    async def _quit(self, arg: str | None) -> None:
        if arg:
            await self._reply("501 Syntax: QUIT")
            return
        await self._reply("221 Bye")
        self._close()

    async def _vrfy(self, arg: str | None) -> None:
        # The same answer for every address, so that none can be probed.
        await self._reply("252 2.1.5 Cannot VRFY user; try RCPT to verify")

    async def _expn(self, arg: str | None) -> None:
        await self._reply("502 5.7.0 EXPN disabled")

    async def _auth(self, arg: str | None) -> None:
        # Never offered. Answered anyway, so that a scanner cannot take
        # silence for acceptance.
        await self._reply("502 5.7.0 AUTH not supported on inbound port 25")

    async def _help(self, arg: str | None) -> None:
        # No list of commands: it would only describe the server to a scanner.
        await self._reply("214 2.0.0 See https://www.rfc-editor.org/rfc/rfc5321")

    # ----------------------------------------------------------------- STARTTLS
    async def _starttls(self, arg: str | None) -> None:
        if self.session.ssl:
            await self._reply("503 5.5.1 TLS already active")
            return
        if arg:
            await self._reply("501 Syntax: STARTTLS")
            return
        if not self.tls_context:
            await self._reply("454 TLS not available")
            return
        await self._reply("220 Ready to start TLS")
        # No await between this line and the handshake taking the transport
        # over: nothing received in clear can end up read as if encrypted.
        self._reader.discard()
        self._handshaking = True
        try:
            transport = await self._loop.start_tls(
                self.transport, self, self.tls_context, server_side=True
            )
        except Exception as error:
            # The peer's handshake failed: no certificate it would accept, no
            # common protocol or cipher, or plaintext where TLS was due. Not a
            # fault of ours. The cause is the one useful fact: a sender that
            # enforces TLS and rejects our certificate shows up here and
            # nowhere else, as a session that said STARTTLS and went away.
            self._log.tls_error = logfmt.clip(f"{type(error).__name__}: {error}", MAX_LOGGED_REPLY)
            self.close_reason = self.close_reason or "tls_failed"
            raise _TLSFailed from error
        finally:
            self._handshaking = False
        if transport is None:
            # asyncio's answer when the connection went during the handshake
            # without an error of its own: one of our timers closed it.
            self.close_reason = self.close_reason or "tls_failed"
            raise _TLSFailed
        self.transport = transport
        self._reader.replace_transport(transport)
        # RFC 3207 section 4.2: what was learnt in clear is forgotten.
        self.session.host_name = None
        self.session.extended_smtp = False
        self.session.ssl = {"cipher": transport.get_extra_info("cipher")}
        self.envelope = Envelope()

    # --------------------------------------------------------------------- DATA
    async def _data(self, arg: str | None) -> None:
        """Receive one message and hand it to the handler.

        The whole phase, body receive and MDA call, runs under one deadline of
        ``PYMTA_DATA_TIMEOUT`` in place of the idle timeout. The handler takes
        its reply reserve out of the same budget so it can answer 451 first.
        """
        if await self._needs_helo():
            return
        if not self.envelope.rcpt_tos:
            await self._reply("503 Error: need RCPT command")
            return
        if arg:
            await self._reply("501 Syntax: DATA")
            return

        # Before the 354, so a refused peer never starts uploading. Held until
        # the handler returns: the body stays in memory for the whole MDA
        # call, not just while it is being received.
        if not self._gate.acquire_data(self._slot):
            metrics.SECURITY_REJECTIONS.labels(reason="max_concurrent_data").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_temp").inc()
            await self._reply(
                "451 4.3.1 Insufficient resources, please retry", reason="max_concurrent_data"
            )
            return

        # Read once and kept for the phase, so the handler sizes its budget
        # against the value the timer was armed at.
        self.data_phase_started = time.monotonic()
        self.data_phase_budget = float(settings.PYMTA_DATA_TIMEOUT)
        self._arm_idle_timer(self.data_phase_budget)
        try:
            await self._reply("354 End data with <CR><LF>.<CR><LF>")
            self._rate_timer = self._loop.call_later(_STRESS_IDLE_TIMEOUT, self._check_data_rate)
            try:
                content, refusal = await self._read_data()
            finally:
                self._rate_timer.cancel()
            if refusal is not None:
                if refusal is _TOO_MUCH_DATA:
                    metrics.SECURITY_REJECTIONS.labels(reason="oversize_message").inc()
                metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
                # Reset after the reply: its log line names the envelope.
                await self._reply(refusal[0], reason=refusal[1])
                self.envelope = Envelope()
                return
            self.envelope.content = content
            del content
            hook = getattr(self.handler, "handle_DATA", None)
            status = "250 OK" if hook is None else await hook(self, self.session, self.envelope)
            self.envelope = Envelope()
            await self._reply(status)
        except _DataOverflow:
            # Mid-line, with no way to find where the next command starts:
            # say why and hang up.
            metrics.SECURITY_REJECTIONS.labels(reason="oversize_message").inc()
            metrics.MESSAGES_TOTAL.labels(result="rejected_perm").inc()
            self.request_disconnect()
            await self._reply(_SIZE_EXCEEDED, reason="oversize_message")
        finally:
            self.data_phase_started = None
            self.data_phase_budget = None
            self._gate.release_data(self._slot)

    async def _read_data(self) -> tuple[bytes, tuple[str, str] | None]:
        """Read a message up to its terminating dot. Returns (content, refusal).

        ``refusal`` is None, or the (reply, reason) the message is to get.

        Only ``<CRLF>.<CRLF>`` ends the message, never a bare LF: that is what
        keeps one message from being split in two (SMTP smuggling).

        Lines are read up to LF, bare or not, and kept as ``stored_line``
        returns them, so the content has CRLF line endings only. The line and
        size limits apply to that form.

        Lines are appended to blocks of ``_DATA_BLOCK`` bytes, not kept as one
        object each, so a body of empty lines costs its size and not thirty
        times that. What is held is the message once, plus one more copy for
        the moment of the final join.

        A message over the size limit, or with a line over the line limit, is
        read to its end and then refused, so the session stays usable. Lines
        too long to keep are skipped, and no more of them is read than the
        size limit: past it there is no telling where the message ends.
        """
        limit = self.data_size_limit
        longest = settings.PYMTA_MAX_LINE_LENGTH
        blocks: list[bytearray] = []
        block = bytearray()
        self._data_received = 0
        lines = 0
        refusal = None
        rewritten = False
        # Whether the line before ended in CRLF: the dot that ends the
        # message only counts after one.
        after_crlf = True
        while True:
            try:
                # One more than the limit: the CR of a CRLF is in the line.
                line = await self._reader.readline(b"\n", limit=longest + 1)
            except LineTooLong:
                refusal = refusal or _LINE_TOO_LONG
                blocks, block = [], bytearray()
                room = max(limit - self._data_received, 0) if limit else None
                # Up to CRLF, so that what follows is known to follow one.
                skipped = await self._reader.skip_line(b"\r\n", max_bytes=room)
                if skipped is None or (limit and skipped > room):
                    raise _DataOverflow from None
                self._data_received += skipped
                after_crlf = True
                continue
            crlf = line.endswith(b"\r\n")
            if after_crlf and line == b".\r\n":
                break
            after_crlf = crlf
            lines += 1
            if lines % _DATA_LINES_PER_TURN == 0:
                # A buffer of very short lines is read without waiting: let
                # the other sessions run.
                await asyncio.sleep(0)
            if len(line) - (2 if crlf else 1) > longest:
                refusal = refusal or _LINE_TOO_LONG
            line, changed = stored_line(line)
            rewritten = rewritten or changed
            self._data_received += len(line)
            if limit and self._data_received > limit:
                refusal = refusal or _TOO_MUCH_DATA
            if refusal is not None:
                if blocks or block:
                    blocks, block = [], bytearray()
                continue
            block += line
            if len(block) >= _DATA_BLOCK:
                blocks.append(block)
                block = bytearray()
        if refusal is not None:
            return b"", refusal
        if rewritten:
            metrics.BARE_NEWLINE_MESSAGES.inc()
            logger.debug("bare_newline_normalised", extra={"session_id": self.session_id})
        blocks.append(block)
        return b"".join(blocks), None

    # ------------------------------------------------------------------- timers
    def _arm_idle_timer(self, duration: float | None = None) -> None:
        """Arm the idle timer, shorter when the server is close to full.

        Postfix's stress mode: 10 s instead of 300 s between commands, inside a
        transaction too, or a peer would keep its slot by sending MAIL and
        nothing else. A running command keeps the full timeout whatever the
        load: ``_dispatch`` arms it.
        """
        if duration is None:
            duration = self._timeout
            if self.under_stress():
                duration = min(_STRESS_IDLE_TIMEOUT, self._timeout)
        self._idle_timeout = duration
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = self._loop.call_later(duration, self._idle_expired)

    def under_stress(self) -> bool:
        """True while the server is close to its session cap."""
        return self._gate.under_stress()

    def _idle_expired(self) -> None:
        """Announce the timeout before hanging up.

        A bare TCP close cannot be told from a network fault; Postfix answers
        421 here too. During DATA the timer is the deadline of the whole
        phase, not an idle time.
        """
        if self.data_phase_started is not None:
            logger.debug("data_timeout", extra=self._timeout_fields(self._idle_timeout))
            self._timed_out("data_timeout", "Timeout receiving the message")
            return
        logger.debug("command_timeout", extra=self._timeout_fields(self._idle_timeout))
        self._timed_out("command_timeout", "Idle timeout")

    def _check_data_rate(self) -> None:
        """Cut a message that arrives too slowly while DATA slots are scarce.

        Without it a few sources hold every slot for ``PYMTA_DATA_TIMEOUT`` by
        sending nothing. Postfix does the same under overload.
        """
        elapsed = time.monotonic() - self.data_phase_started
        if self._gate.data_under_stress() and self._data_received < _MIN_DATA_RATE * elapsed:
            fields = self._timeout_fields(self._idle_timeout)
            logger.debug("data_timeout", extra={**fields, "received_bytes": self._data_received})
            self._timed_out("data_timeout", "Message received too slowly")
            return
        self._rate_timer = self._loop.call_later(_STRESS_IDLE_TIMEOUT, self._check_data_rate)

    def _arm_session_timer(self) -> None:
        """Start the one bound a peer cannot push back by staying busy.

        The idle timer is re-armed by every command, so a peer that sends one
        just under ``PYMTA_COMMAND_TIMEOUT`` could otherwise stay for as long
        as the call limits let it issue commands. Armed once per connection.
        """
        limit = settings.PYMTA_SESSION_TIMEOUT
        if limit > 0:
            self._session_timer = self._loop.call_later(limit, self._session_expired)

    def _session_expired(self) -> None:
        if self.data_phase_started is not None:
            # A message is being received or delivered: cutting it would have
            # the sender resend one that may already be stored. Its own
            # deadline bounds the wait.
            self._session_timer = self._loop.call_later(1, self._session_expired)
            return
        logger.debug("session_timeout", extra=self._timeout_fields(settings.PYMTA_SESSION_TIMEOUT))
        self._timed_out("session_timeout", "Session too long")

    def _timeout_fields(self, limit) -> dict:
        return {
            "session_id": self.session_id,
            "client_ip": self.client_address(),
            "limit_seconds": limit,
        }

    def _timed_out(self, reason: str, notice: str) -> None:
        self.close_reason = reason
        metrics.SECURITY_REJECTIONS.labels(reason=reason).inc()
        metrics.DISCONNECTS_421.labels(reason=reason).inc()
        # As a task because writing is async and this is a timer callback.
        # Not retained: the close it performs is what ends the connection.
        self._loop.create_task(self._close_with_notice(f"421 4.4.2 {notice}, closing connection"))

    # ----------------------------------------------------------------- shutdown
    def _leaving(self, verb: str) -> bool:
        """True when a shutdown is under way and this command starts new work.

        A transaction already open is allowed to finish (RCPT, DATA): cutting
        it would only make the sender resend it. Anything else is answered 421,
        so the sender moves to another MX now instead of holding this node's
        drain until the deadline.
        """
        return runtime.is_shutting_down() and verb != "QUIT" and not self.in_transaction()

    def _is_idle(self) -> bool:
        """True when no command and no transaction is in progress."""
        return self._awaits_command() and not self.in_transaction()

    def _awaits_command(self) -> bool:
        if self._verb is not None:
            return False
        return self.admitted and not self._closing and self.close_reason is None

    def stress_began(self, gate: IPGate) -> None:
        """Shorten the wait of a session that was already waiting for a command."""
        if self._gate is gate and self._awaits_command():
            self._arm_idle_timer()

    def close_if_idle(self) -> None:
        """Hang up, with a 421, if no command and no transaction is in progress."""
        if self._is_idle():
            self.close_reason = "shutdown"
            metrics.DISCONNECTS_421.labels(reason="shutdown").inc()
            self._loop.create_task(self._close_with_notice(_SHUTDOWN))

    def log_abandoned(self) -> None:
        """Write the summary of a session the shutdown deadline is cutting."""
        self.close_reason = self.close_reason or "shutdown"
        self._log.close(None)

    # --------------------------------------------------------------------- gate
    def client_address(self) -> str | None:
        """The client: what the gate keyed on, else the TCP peer.

        The TCP peer is the balancer when PROXY protocol is on, and naming it
        would attribute the session to our own infrastructure.
        """
        if self._client_ip is not None:
            return self._client_ip
        if settings.PYMTA_ENABLE_PROXY_PROTOCOL:
            return None
        return ip_of(self.session.peer)

    def _wire_peer_ip(self) -> str:
        if self.session.peer:
            return str(self.session.peer[0])
        # All sessions without a wire peer collapse into one bucket, so they
        # share a single source's slots. Counted rather than logged at WARNING:
        # a peer that resets the connection between accept() and here can race
        # peername into None, and that must not be a way to pick our log rate.
        metrics.CONNECTIONS_TOTAL.labels(result="no_wire_peer").inc()
        logger.debug("session_without_peer", extra={"session_id": self.session_id})
        return "unknown"

    def _is_blocked(self, ip: str) -> bool:
        if not settings.PYMTA_BLOCKED_NETWORKS:
            return False
        addr = settings.parse_client_ip(ip)
        if addr is None:
            # The "unknown" bucket. Not matchable against a network, so not
            # blockable; the caps still apply to it.
            return False
        return any(addr in net for net in settings.PYMTA_BLOCKED_NETWORKS)

    async def admit(self, ip: str) -> bool:
        """Let the connection in under ``ip``, or turn it away. Before the greeting.

        ``ip`` is the TCP peer, or the address a PROXY header named (the
        handler's ``handle_PROXY`` calls this), or ``"unknown"``. Returns True
        when a session slot was taken; otherwise the peer has been answered
        and the connection closed.
        """
        self._client_ip = ip if ip != "unknown" else None
        who = {"session_id": self.session_id, "client_ip": ip}
        if runtime.is_shutting_down():
            # RFC 5321 section 3.1 allows 421 in place of the greeting, and
            # the sender then moves to the next MX. 4.3.2 is RFC 3463's
            # "system not accepting network messages".
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_drain").inc()
            logger.debug("connection_draining", extra=who)
            return await self._turn_away(_SHUTDOWN)
        if self._is_blocked(ip):
            metrics.CONNECTIONS_TOTAL.labels(result="rejected_blocked").inc()
            metrics.SECURITY_REJECTIONS.labels(reason="blocked_network").inc()
            logger.debug("connection_blocked", extra=who)
            # RFC 5321 section 3.1 also allows 554 in place of the greeting.
            return await self._turn_away("554 5.7.1 Access denied")
        # What the limits count by: the address for IPv4, the /64 for IPv6.
        # Logs keep the full address.
        source = source_key(ip)
        was_under_stress = self._gate.under_stress()
        scope = self._take_slot(source)
        if scope is not None:
            metrics.CONNECTIONS_TOTAL.labels(result=f"rejected_{scope}").inc()
            metrics.DISCONNECTS_421.labels(reason=f"gate_{scope}").inc()
            # A sender held off by a cap is deferred mail, so it gets a line at
            # INFO, but a source sitting at its cap is refused on every
            # connection: one line per address per minute, the rest at DEBUG.
            fields = {**who, "scope": scope}
            suppressed = _cap_log_throttle.allow(source) if settings.PYMTA_LOG_SESSIONS else None
            if suppressed is None:
                logger.debug("connection_capped", extra=fields)
            else:
                logger.info("connection_capped", extra={**fields, "suppressed": suppressed})
            return await self._turn_away("421 4.7.0 Too many connections, try again later")
        self._slot = source
        self._slot_taken_at = self._idle_since = time.monotonic()
        self.admitted = True
        metrics.CONNECTIONS_TOTAL.labels(result="accepted").inc()
        if not was_under_stress and self._gate.under_stress():
            # The timers armed before this slot was taken are still long.
            for connection in _live_connections:
                connection.stress_began(self._gate)
        return True

    def _take_slot(self, source: str) -> str | None:
        """Take a session slot. Returns the cap that refused, None when taken."""
        try:
            self._gate.acquire(source)
        except TooManyConnections as refused:
            if refused.scope != "global" or not self._reclaim_slot_for(source):
                return refused.scope
            self._gate.acquire(source)
        return None

    def _reclaim_slot_for(self, source: str) -> bool:
        """Free a slot for ``source`` when every one is taken. True if one was.

        Taken from a source that holds more than its share, the largest
        first, by closing the session of its that has been idle the longest.
        A session inside a command or a transaction is never closed for this,
        so a server full of busy sessions still refuses.
        """
        crowding = self._gate.crowding_sources(source)
        ranked = [
            (rank, connection)
            for connection in _live_connections
            if (rank := connection.reclaim_rank(self._gate, crowding)) is not None
        ]
        if not ranked:
            return False
        min(ranked, key=lambda pair: pair[0])[1].reclaim()
        return True

    def reclaim_rank(self, gate: IPGate, crowding: list[str]) -> tuple[int, float] | None:
        """Where this session stands among those a slot can be taken from.

        Lowest first. None when its slot is not one of them: another gate,
        a source that is not in ``crowding``, or a session that is not idle.
        """
        if self._gate is not gate or self._slot not in crowding or not self._is_idle():
            return None
        return crowding.index(self._slot), self._idle_since

    def reclaim(self) -> None:
        """Give the slot back now, and hang up with a 421."""
        self.close_reason = "reclaimed"
        self._leave_gate()
        metrics.DISCONNECTS_421.labels(reason="reclaimed").inc()
        logger.debug(
            "session_reclaimed",
            extra={"session_id": self.session_id, "client_ip": self.client_address()},
        )
        self._loop.create_task(self._close_with_notice(_RECLAIMED))

    async def _turn_away(self, reply: str) -> bool:
        """Answer a connection that gets no greeting, and close it."""
        await self._reply(reply)
        self._close()
        return False

    def _leave_gate(self) -> None:
        if self._slot is None:
            return
        slot, self._slot = self._slot, None
        metrics.SESSION_DURATION.observe(time.monotonic() - self._slot_taken_at)
        self._gate.release(slot)
