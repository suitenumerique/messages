"""logfmt rendering and the level contract.

The rendering tests are about parseability: a line a collector cannot split
back into fields is worse than no line, because it looks like it worked.

The level tests are the security half. Every event an unauthenticated peer can
trigger has to sit at DEBUG, or opening TCP connections becomes a way to fill
somebody's disk or log bill.
"""

from __future__ import annotations

import io
import logging

import pytest

from pymta.logfmt import LogfmtFormatter, configure, quote


def _render(msg: str, level: int = logging.INFO, exc_info=None, **fields) -> str:
    record = logging.LogRecord(
        name="pymta.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    record.__dict__.update(fields)
    return LogfmtFormatter().format(record)


def _parse(line: str) -> dict[str, str]:
    """Minimal logfmt reader, so the tests decode rather than substring-match."""
    out, key, buf, in_quotes, escaped, on_key = {}, "", "", False, False, True
    for ch in line:
        if on_key:
            if ch == "=":
                key, on_key = buf, False
                buf = ""
            elif ch == " ":
                buf = ""
            else:
                buf += ch
            continue
        if escaped:
            buf += {"n": "\n", "r": "\r", "t": "\t"}.get(ch, ch)
            escaped = False
        elif ch == "\\" and in_quotes:
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch == " " and not in_quotes:
            out[key], buf, on_key = buf, "", True
        else:
            buf += ch
    if key:
        out[key] = buf
    return out


def test_line_carries_the_standard_keys():
    fields = _parse(_render("smtp_listening", bind="0.0.0.0:25"))
    assert fields["level"] == "info"
    assert fields["logger"] == "pymta.test"
    assert fields["event"] == "smtp_listening"
    assert fields["bind"] == "0.0.0.0:25"
    assert fields["ts"].endswith("Z")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("plain", "plain"),
        ("has space", '"has space"'),
        ("", '""'),
        ("a=b", '"a=b"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        (None, "null"),
        (True, "true"),
        (False, "false"),
        (42, "42"),
    ],
)
def test_quoting(value, expected):
    assert quote(value) == expected


def test_values_with_spaces_survive_a_round_trip():
    line = _render("mda_secret_weak", hint="short HS256 secrets are brute-forceable")
    assert _parse(line)["hint"] == "short HS256 secrets are brute-forceable"


def test_a_peer_controlled_value_cannot_forge_fields():
    """A HELO name is attacker-chosen and ends up in a log field.

    Unquoted it would let a peer inject ``level=info`` or a whole second event
    into the line, which is log forgery: the thing you later grep is no longer
    what happened.
    """
    line = _render("helo_control_chars", helo='x" level=error event=fake_alert other="')
    fields = _parse(line)
    assert fields["level"] == "info"
    assert "fake_alert" not in fields.values()
    assert fields["helo"] == 'x" level=error event=fake_alert other="'


def test_exception_stays_on_one_line():
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        line = _render("handler_error", level=logging.ERROR, exc_info=sys.exc_info())
    assert "\n" not in line, "a multi-line record splits one event into many"
    fields = _parse(line)
    assert fields["error_type"] == "ValueError"
    assert fields["error_message"] == "kaboom"
    assert "ValueError: kaboom" in fields["error_stack_trace"]


def test_configure_replaces_handlers_rather_than_adding():
    """Two handlers would print every line twice, in two formats."""
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        stream = io.StringIO()
        configure("INFO", stream)
        configure("INFO", stream)
        assert len(root.handlers) == 1
        logging.getLogger("pymta.test").info("smtp_listening", extra={"bind": "0.0.0.0:25"})
        assert stream.getvalue().count("event=smtp_listening") == 1
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in before:
            root.addHandler(h)


# ---------------------------------------------------------------------------
# The level contract.
# ---------------------------------------------------------------------------


PEER_TRIGGERABLE = {
    # event -> the module that emits it
    "session_timeout": "pymta.smtp_protocol",
    "command_timeout": "pymta.smtp_protocol",
    "connection_blocked": "pymta.smtp_protocol",
    "connection_capped": "pymta.smtp_protocol",
    "helo_control_chars": "pymta.handler",
    "bare_newline_normalised": "pymta.handler",
    "data_timeout": "pymta.handler",
    "proxy_header": "pymta.handler",
}


def test_peer_triggerable_events_are_debug():
    """Anything an unauthenticated peer can provoke must be DEBUG.

    Not a style rule. Each of these is already counted in a Prometheus metric,
    which is where volume belongs; logging them higher means anyone who can
    open a TCP connection can drive unbounded log output.

    Read out of the source rather than by calling each site, so a new call at
    the wrong level is caught even if no test exercises that path.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pymta"
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text()
        for event in PEER_TRIGGERABLE:
            for match in re.finditer(
                r"logger\.(debug|info|warning|error|exception)\(\s*\"" + event + r"\"", text
            ):
                if match.group(1) != "debug":
                    offenders.append(f"{path.name}: {event} at {match.group(1)}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Nothing but us reaches the log.
# ---------------------------------------------------------------------------


def _capture(verbose_libraries=False):
    root = logging.getLogger()
    before = list(root.handlers)
    stream = io.StringIO()
    configure("DEBUG", stream, verbose_libraries=verbose_libraries)
    return stream, root, before


def _restore(root, before):
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in before:
        root.addHandler(h)


def test_third_party_records_are_dropped():
    """aiosmtpd, httpx and httpcore must not reach the log at all.

    aiosmtpd logs every envelope address at INFO and every line of every message
    body at DEBUG, so this filter is what keeps message content out of the log.
    """
    stream, root, before = _capture()
    try:
        logging.getLogger("mail.log").error("Peer: ('10.0.0.1', 5)")
        logging.getLogger("httpcore.http11").debug("send_request_headers")
        logging.getLogger("httpx").info("HTTP Request: POST ...")
        logging.getLogger("asyncio").warning("task exception was never retrieved")
        logging.getLogger("pymta.handler").info("message_delivered", extra={"session_id": "abc"})
    finally:
        _restore(root, before)
    lines = [ln for ln in stream.getvalue().splitlines() if ln]
    assert len(lines) == 1, lines
    assert "event=message_delivered" in lines[0]


def test_an_unknown_future_dependency_is_silent_too():
    """The filter is an allowlist, so a dependency added later needs no action."""
    stream, root, before = _capture()
    try:
        logging.getLogger("some_new_library.client").error("chatty")
    finally:
        _restore(root, before)
    assert stream.getvalue() == ""


def test_a_logger_merely_starting_with_pymta_is_not_ours():
    stream, root, before = _capture()
    try:
        logging.getLogger("pymtaX.thing").error("impostor")
        logging.getLogger("pymta").error("root_event")
    finally:
        _restore(root, before)
    out = stream.getvalue()
    assert "impostor" not in out
    assert "event=root_event" in out


def test_verbose_libraries_lets_them_back_in():
    stream, root, before = _capture(verbose_libraries=True)
    try:
        logging.getLogger("mail.log").info("Peer: ('10.0.0.1', 5)")
    finally:
        _restore(root, before)
    assert "Peer" in stream.getvalue()


# ---------------------------------------------------------------------------
# event is the identifier, msg is optional prose. ECS event.action / message.
# ---------------------------------------------------------------------------


def test_prose_is_optional_and_absent_by_default():
    """Most lines say everything in the identifier and the fields."""
    line = _render("smtp_listening", bind="0.0.0.0:25")
    assert "msg=" not in line
    assert _parse(line)["event"] == "smtp_listening"


def test_prose_renders_as_msg_next_to_the_event():
    """``detail`` is the extras key because logging reserves ``msg`` itself.

    Passing ``extra={"msg": ...}`` raises KeyError inside logging, so the
    mapping is not a stylistic choice.
    """
    line = _render("metrics_unauthenticated", detail="set PYMTA_METRICS_API_KEY", bind="0:9100")
    fields = _parse(line)
    assert fields["msg"] == "set PYMTA_METRICS_API_KEY"
    assert fields["event"] == "metrics_unauthenticated"
    # Beside the identifier it explains, not trailing the machine fields.
    assert line.index("msg=") < line.index("bind=")
    assert "detail=" not in line


def test_reserved_name_really_is_reserved():
    """The reason for the indirection, asserted rather than assumed."""
    with pytest.raises(KeyError):
        logging.getLogger("pymta.test").info("x", extra={"msg": "boom"})
