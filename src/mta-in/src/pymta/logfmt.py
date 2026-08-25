"""logfmt log formatting.

One line per event, ``key=value`` pairs, machine-parseable without a grammar:

    ts=2026-08-24T09:41:02.113Z level=info logger=pymta.server event=smtp_listening
    bind=0.0.0.0:25 hostname=mta-in size_limit=10485760

Field names follow Elastic Common Schema where ECS has a name for the thing,
spelled with underscores instead of dots. logfmt itself standardises nothing but
the syntax, so the choice is between ECS and OpenTelemetry semantic conventions;
both are dotted, and Loki rewrites a dot to an underscore when it turns a
logfmt key into a label, so ``client_ip`` *is* ECS ``client.ip`` by the time it
is queryable. Writing the underscore ourselves keeps the line and the label
identical instead of leaving the reader to guess which spelling to search.

Both conventions insist a network address names whose it is. A bare ``ip`` is
ambiguous the moment a proxy is involved, which for an inbound MX is always:
``client_ip`` is the sender, ``wire_peer`` is the load balancer that relayed it.
OTel says this explicitly, that ``client.address`` should be the address behind
any intermediary, which is exactly what our PROXY-protocol handling produces.

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

The four short ones at the top keep their conventional logfmt spellings rather
than ``log_level`` and friends: they are what every logfmt reader already greps
for, ``@timestamp`` is not a legal label name anywhere, and a pipeline that
wants strict ECS renames five keys.

Every record carries ``event``, a short stable snake_case identifier, and its
detail as separate fields. ``event`` is what you group and alert on, so it never
embeds a value that varies: ``event=mda_unhealthy`` finds every occurrence
whatever the status code or endpoint was.

``msg`` is optional prose, written as ``extra={"detail": ...}`` because logging
reserves the name ``msg`` itself. It is present only where the identifier and
the fields do not already say it: what to do about it, or why it happened. Most lines do not
need one and do not carry one. It never repeats a value that is already its own
field, because a sentence with the status code baked in is a sentence nobody can
group by, and it drifts the moment someone rewords it.

    ts=... level=warning logger=pymta.metrics event=metrics_unauthenticated
    msg="set PYMTA_METRICS_API_KEY or restrict the port" bind=0.0.0.0:9100

That is ECS's split: ``event`` is ``event.action``, ``msg`` is ``message``.

Levels are assigned by *who can cause the line*, not by how alarming it reads:

``error``
    A fault in this process. Nobody outside can provoke it, so it always
    deserves attention. Carries a traceback.
``warning``
    An operational problem an operator should look at: the MDA is unhealthy,
    the configuration is unsafe, a deadline was missed. Caused by our
    infrastructure, not by a peer.
``info``
    Lifecycle. Startup, shutdown, drain, reload. Low, bounded volume.
``debug``
    Anything an unauthenticated peer can trigger at will: bad addresses,
    refused connections, blocked senders, timeouts on their side.

That last rule is a security property, not a style preference. Every one of
those events is already counted in a Prometheus metric, which is the right
place for volume. Logging them above ``debug`` hands anyone on the internet a
way to fill the disk or the log bill of whatever ships these lines, at the cost
of opening TCP connections. Reach for the metric to see how often something
happens, and ``PYMTA_LOG_LEVEL=DEBUG`` to see the individual events while you
are actually looking.
"""

from __future__ import annotations

import datetime as _datetime
import logging

# Attributes LogRecord always carries. Anything else on the record was put
# there by a caller through ``extra=`` and is emitted as a field.
_STANDARD = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)

_NEEDS_QUOTING = frozenset(' "=\\\n\r\t')

# Callers pass prose as ``extra={"detail": ...}`` and it is emitted as ``msg``.
# The obvious spellings are not available: ``logging`` reserves both ``msg`` and
# ``message`` on LogRecord and raises KeyError if ``extra`` tries to set either.
_PROSE = "detail"


def quote(value: object) -> str:
    """Render one value as a logfmt token.

    ``None`` and booleans get their lowercase spellings so a consumer can read
    them back as the types they were, rather than as Python's ``None``/``True``.
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
    if text == "" or any(c in _NEEDS_QUOTING for c in text):
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return text


class LogfmtFormatter(logging.Formatter):
    """Render records as logfmt. Never emits a multi-line record.

    A traceback is folded into a single escaped ``stack`` field. Newlines in a
    log line would split one event into many as far as any collector is
    concerned, which is how a traceback ends up looking like a dozen unparseable
    records.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = _datetime.datetime.fromtimestamp(record.created, _datetime.timezone.utc)
        parts = [
            f"ts={ts.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
            f"event={quote(record.getMessage())}",
        ]
        # Prose first among the extras, so it sits beside the identifier it
        # explains rather than after ten machine fields.
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


# Only our own records reach the handler.
#
# The dependencies are not merely noisy. aiosmtpd (``mail.log``) logs every
# command line at INFO, so one envelope address per RCPT, and the entire message
# body at DEBUG, one ``DATA readline`` record per line of mail; it logs an
# unrecognised command at WARNING, which lets a peer pick our log rate. httpcore
# and httpx trace every MDA request at DEBUG, headers included, at roughly 60
# lines per message.
#
# An allowlist rather than a list of offenders, so a dependency added later is
# silent without anyone remembering to gag it. The cost is that useful
# third-party records go too, asyncio's "task exception was never retrieved"
# being the one worth naming; PYMTA_LOG_VERBOSE_LIBRARIES brings them back.
_OURS = "pymta"


class OwnRecordsOnly(logging.Filter):
    """Drop every record that did not come from a ``pymta.*`` logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == _OURS or record.name.startswith(_OURS + ".")


def configure(level: str, stream=None, verbose_libraries: bool = False) -> None:
    """Install the logfmt formatter as the only handler on the root logger.

    Replaces existing handlers rather than adding to them: two handlers would
    print every line twice, once in each format, which defeats the point of
    committing to a parseable one.

    ``verbose_libraries`` lifts the filter that keeps third-party records out.
    See :class:`OwnRecordsOnly` for what that lets back in, and why you would
    not leave it on.
    """
    import sys

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(LogfmtFormatter())
    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level))
    if not verbose_libraries:
        # On the handler, not the root logger: a filter on a logger is not
        # consulted for records that propagate up from a child, which is
        # exactly how every dependency's output arrives here.
        handler.addFilter(OwnRecordsOnly())
