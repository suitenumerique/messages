"""logfmt log formatting.

One line per event, ``key=value`` pairs:

    ts=2026-08-24T09:41:02.113Z level=info logger=pymta.server event=smtp_listening
    bind=0.0.0.0:25 hostname=mta-in size_limit=10485760

Field names follow Elastic Common Schema where it has one, with underscores
for its dots: Loki turns a dot into an underscore when a logfmt key becomes a
label, so writing ``client_ip`` keeps the line and the label identical.

An address field says whose address it is, since a proxy is usually involved:
``client_ip`` is the sender, ``wire_peer`` the load balancer that relayed it.

    ours                 ECS
    ts                   @timestamp
    level                log.level
    logger               log.logger
    event                event.action
    msg                  message
    client_ip            client.ip
    client_port          client.port
    wire_peer            source.ip
    session_id           (OTel session.id; ECS has none)
    sender               email.sender.address
    recipients           email.to.address
    error_message        error.message
    error_type           error.type
    error_stack_trace    error.stack_trace
    duration_ms          event.duration, which ECS counts in nanoseconds

``ts``, ``level``, ``logger`` and ``event`` keep the usual logfmt spellings:
readers grep for them, and ``@timestamp`` is not a legal label name. A
pipeline that wants strict ECS renames five keys.

Every record has ``event``, a stable snake_case identifier, and its detail as
separate fields. ``event`` is what is grouped and alerted on, so it holds no
value that varies: ``event=mda_unhealthy`` finds every occurrence whatever the
status or the endpoint.

``msg`` is optional prose, passed as ``extra={"detail": ...}`` because logging
reserves ``msg``. Present only where the identifier and the fields do not say
it: what to do, or why it happened. It repeats no value that has its own
field.

    ts=... level=warning logger=pymta.metrics event=metrics_unauthenticated
    msg="set PYMTA_METRICS_API_KEY or restrict the port" bind=0.0.0.0:9100

Levels follow who can cause the line:

``error``
    A fault in this process, which nobody outside can cause. Carries a
    traceback.
``warning``
    A problem for an operator: the MDA is unhealthy, the configuration is
    unsafe, a deadline was missed. Caused by our infrastructure, not a peer.
``info``
    Lifecycle: startup, shutdown, drain, reload. Low, bounded volume. And the
    mail audit trail, three exceptions to the rule below: ``message_*`` (one
    per message), ``reject`` (capped by the per-session error budget) and
    ``session_closed`` (one per admitted connection that issued a command).
    Nothing caps the connection rate, so ``PYMTA_LOG_SESSIONS`` can drop the
    last two to ``debug`` under a flood. Without them "the mail from X never
    arrived" has no answer in the log.
``debug``
    Anything an unauthenticated peer can cause at will: bad addresses,
    refused connections, blocked senders, timeouts on their side.

The last rule is for security: above ``debug``, those lines would let anyone
fill the disk or the log bill by opening TCP connections. Each is counted in a
Prometheus metric, which is where to read volumes; ``PYMTA_LOG_LEVEL=DEBUG``
shows the individual events.
"""

from __future__ import annotations

import contextlib
import datetime as _datetime
import functools
import logging
import logging.handlers
import queue
import sys
import threading
from collections.abc import Callable

from . import metrics

# Lines waiting for the writer thread: see :func:`configure`.
_QUEUE_SIZE = 10000
# Seconds the exit waits for the queued lines to be written.
_STOP_TIMEOUT = 2.0

# Attributes LogRecord always carries. Anything else on the record was put
# there by a caller through ``extra=`` and is emitted as a field.
_STANDARD = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)

_NEEDS_QUOTING = frozenset(' "=\\\n\r\t')

# Escapes a reader recognises. Anything else unprintable is written \uXXXX:
# see :func:`quote`.
_SIMPLE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

# Prose is passed as ``extra={"detail": ...}`` and written as ``msg``:
# ``logging`` reserves ``msg`` and ``message`` on LogRecord and raises KeyError
# when ``extra`` sets either.
_PROSE = "detail"


def _escape(char: str) -> str:
    simple = _SIMPLE_ESCAPES.get(char)
    if simple is not None:
        return simple
    if char.isprintable():
        return char
    # Four digits, or eight past the BMP: five would read back as a
    # four-digit escape followed by a literal character.
    return f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}"


def quote(value: object) -> str:
    """Render one value as a logfmt token.

    ``None`` and booleans are written lowercase, so a consumer can read them
    back as what they were.

    Everything unprintable is escaped, not only CR and LF: ``str.splitlines``
    also breaks on VT, FF, NEL, U+2028 and U+2029, and under SMTPUTF8 those
    arrive inside envelope addresses, where one MAIL FROM would forge a log
    line for a consumer written in Python. ESC too, so a value cannot repaint
    the terminal of someone tailing the log.

    Callers never have to sanitise a value themselves.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text == "" or any(c in _NEEDS_QUOTING or not c.isprintable() for c in text):
        return '"' + "".join(_escape(c) for c in text) + '"'
    return text


def clip(value: object, limit: int, *, tail: int = 0) -> str | None:
    """Bound a peer-supplied value before it becomes a log field.

    :func:`quote` makes a value safe but leaves its length, which the peer
    chooses. The ``...+N`` marker says how many characters were dropped.

    ``tail`` keeps that many characters from the end, out of ``limit``: for an
    address, the domain stays searchable when the local-part is the long half
    (an SRS return path).
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    if tail <= 0:
        return f"{text[:limit]}...+{dropped}"
    return f"{text[: limit - tail]}...+{dropped}...{text[-tail:]}"


def clip_list(items: list[str], limit: int) -> str:
    """Comma-join ``items``, naming at most ``limit`` of them."""
    if len(items) <= limit:
        return ",".join(items)
    return f"{','.join(items[:limit])},+{len(items) - limit} more"


class LogfmtFormatter(logging.Formatter):
    """Render records as logfmt, always on one line.

    A traceback goes into one escaped ``error_stack_trace`` field: a collector
    would read each of its lines as a separate record.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = _datetime.datetime.fromtimestamp(record.created, _datetime.timezone.utc)
        parts = [
            f"ts={ts.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
            f"event={quote(record.getMessage())}",
        ]
        # Prose first among the extras, next to the identifier it explains.
        if _PROSE in record.__dict__:
            parts.append(f"msg={quote(record.__dict__[_PROSE])}")
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key != _PROSE and not key.startswith("_"):
                parts.append(f"{key}={quote(value)}")
        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            if exc_type is not None:
                parts.append(f"error_type={quote(exc_type.__name__)}")
                parts.append(f"error_message={quote(exc_value)}")
            parts.append(f"error_stack_trace={quote(self.formatException(record.exc_info))}")
        return " ".join(parts)


# Only our own records reach the handler: httpcore and httpx trace every MDA
# request at DEBUG, headers included, about 60 lines per message. An
# allowlist, so a dependency added later is silent too. Useful third-party
# records go as well; PYMTA_LOG_VERBOSE_LIBRARIES brings them back. What
# asyncio reports about our own tasks and callbacks is logged by
# ``server._log_unhandled``.
_OURS = "pymta"


class OwnRecordsOnly(logging.Filter):
    """Drop every record that did not come from a ``pymta.*`` logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == _OURS or record.name.startswith(_OURS + ".")


class _QueueHandler(logging.handlers.QueueHandler):
    """Formats a record where it is logged and leaves the write to a thread.

    ``QueueHandler.prepare`` formats with this handler's formatter, so what
    is queued is the finished line.
    """

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            metrics.LOG_RECORDS_DROPPED.inc()


def _stop_writer(writer: logging.handlers.QueueListener) -> None:
    """Write what is queued, then end the thread. For ``_STOP_TIMEOUT`` at most.

    A stream that is not being read must not hold the exit: the writer is
    then stuck in a write, or there is no room for its stop marker
    (``queue.Full``). Both threads are daemons.
    """

    def stop() -> None:
        with contextlib.suppress(queue.Full):
            writer.stop()

    # ``stop`` joins the writer without a timeout, so it is waited for here.
    stopper = threading.Thread(target=stop, daemon=True)
    stopper.start()
    stopper.join(_STOP_TIMEOUT)


def configure(
    level: str, stream=None, verbose_libraries: bool = False, *, in_thread: bool = False
) -> Callable[[], None] | None:
    """Make the logfmt formatter the only handler on the root logger.

    Existing handlers are removed: two would print every line twice.

    ``verbose_libraries`` lets third-party records through: see
    :class:`OwnRecordsOnly`.

    With ``in_thread`` the lines are written by a thread, so the event loop
    never waits on a stream that is read slowly: every session would wait
    with it. Up to ``_QUEUE_SIZE`` lines are held for it and the rest are
    dropped and counted. Returns what to call before exiting, to have the
    queued lines written: see :func:`_stop_writer`.
    """
    handler: logging.Handler = logging.StreamHandler(stream or sys.stdout)
    stop = None
    if in_thread:
        records: queue.Queue = queue.Queue(_QUEUE_SIZE)
        writer = logging.handlers.QueueListener(records, handler)
        writer.start()
        stop = functools.partial(_stop_writer, writer)
        handler = _QueueHandler(records)
    handler.setFormatter(LogfmtFormatter())
    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level))
    if not verbose_libraries:
        # On the handler: a logger's filter is not applied to records that
        # propagate up from a child logger, which is how the dependencies'
        # records arrive.
        handler.addFilter(OwnRecordsOnly())
    return stop
