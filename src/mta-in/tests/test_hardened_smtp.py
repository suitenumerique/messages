"""Unit tests for :class:`pymta.smtp_protocol.HardenedSMTP`.

These exercise the two places where we step outside aiosmtpd's own control
flow: the forced disconnect behind a 421, and the DATA-phase deadline. Both
are driven against a fake transport: no sockets, no Docker stack.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import ssl

import pytest
from prometheus_client import REGISTRY

from pymta import settings, smtp_protocol
from pymta.controller import build_smtp_kwargs
from pymta.limits import IPGate, LogThrottle
from pymta.logfmt import LogfmtFormatter
from pymta.smtp_protocol import HardenedSMTP, log_abandoned_sessions


class _FakeTransport:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, name, default=None):
        return ("203.0.113.5", 12345) if name == "peername" else default


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


class _NullHandler:
    """Handler with no hooks, so aiosmtpd falls back to its own replies."""


def _server() -> HardenedSMTP:
    """A connected-looking protocol instance without a real transport.

    Stands in for what ``connection_made`` would have built, minus the
    ``_handle_client`` task and the stream plumbing that needs a live socket.
    """
    smtp = HardenedSMTP(_NullHandler(), hostname="mta.test", timeout=120)
    smtp.transport = _FakeTransport()
    smtp._writer = _FakeWriter()
    smtp.session = smtp._create_session()
    smtp.envelope = smtp._create_envelope()
    smtp._arm_session_deadline()
    return smtp


# ---------------------------------------------------------------------------
# The 421 that actually hangs up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_waits_for_the_reply_to_go_out():
    # Closing eagerly would race the 421 off the wire and leave the peer with
    # a bare TCP reset instead of a reason.
    smtp = _server()
    smtp.request_disconnect()
    assert smtp.transport.closed is False

    await smtp.push("421 4.7.0 Too many errors, goodbye")

    assert bytes(smtp._writer.written).endswith(b"goodbye\r\n")
    assert smtp.transport.closed is True


@pytest.mark.asyncio
async def test_disconnect_is_not_sticky():
    smtp = _server()
    await smtp.push("250 2.1.0 OK")
    assert smtp.transport.closed is False


@pytest.mark.asyncio
async def test_handle_exception_closes_the_session():
    smtp = _server()
    status = await smtp.handle_exception(RuntimeError("boom"))
    await smtp.push(status)
    assert smtp.transport.closed is True


# ---------------------------------------------------------------------------
# DATA phase runs on its own budget.
#
# aiosmtpd arms its idle timer when a command line is dispatched and never
# re-arms it while the handler runs, so without this override the whole of
# DATA (body receive plus the MDA call) is charged to one
# PYMTA_COMMAND_TIMEOUT and the transport is torn down mid-handler.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_phase_swaps_in_the_data_deadline(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 300)

    smtp = _server()
    armed: list[float] = []
    loop = asyncio.get_running_loop()
    real_call_later = loop.call_later

    def _record(delay, callback, *args):
        armed.append(delay)
        handle = real_call_later(delay, callback, *args)
        handle.cancel()  # never actually fire during the test
        return handle

    monkeypatch.setattr(loop, "call_later", _record)

    # No RCPT recorded, so aiosmtpd's smtp_DATA bails out at "503 need RCPT"
    # immediately, which is enough to prove the deadline is swapped and restored.
    await smtp.smtp_DATA("")

    # PYMTA_DATA_TIMEOUT is the hard edge, armed verbatim. The handler's
    # reply reserve comes out of this budget, it does not extend it.
    assert armed[0] == 300, armed
    assert armed[-1] == 120, armed
    assert smtp.data_phase_started is None


@pytest.mark.asyncio
async def test_data_phase_start_is_published_for_the_handler(monkeypatch):
    seen: list[float | None] = []

    class _Recorder:
        async def handle_DATA(self, server, session, envelope):
            seen.append(server.data_phase_started)
            return "250 2.0.0 OK"

    smtp = HardenedSMTP(_Recorder(), hostname="mta.test", timeout=120)
    smtp.transport = _FakeTransport()
    smtp._writer = _FakeWriter()
    smtp.session = smtp._create_session()
    smtp.envelope = smtp._create_envelope()
    smtp.session.host_name = "client.test"
    smtp.envelope.rcpt_tos.append("a@example.com")

    # Feed a one-line body followed by the end-of-data dot.
    reader = asyncio.StreamReader()
    reader.feed_data(b"Subject: x\r\n\r\nbody\r\n.\r\n")
    reader.feed_eof()
    smtp._reader = reader

    await smtp.smtp_DATA("")

    assert seen and seen[0] is not None
    # Restored once DATA is over, so a stale timestamp cannot shrink the
    # budget of a later envelope on the same connection.
    assert smtp.data_phase_started is None


# ---------------------------------------------------------------------------
# Whole-session deadline.
#
# The one bound a peer cannot push back by staying busy: every other timeout
# is re-armed by activity, so a peer sending one command just under
# PYMTA_COMMAND_TIMEOUT rides command_call_limit for ~39 h on one connection.
# ---------------------------------------------------------------------------


async def _listener(loop, **overrides):
    """Start a listener built the way the real server builds one.

    Goes through ``build_smtp_kwargs`` rather than passing ``timeout=`` by hand.
    Handing the kwarg over directly would test aiosmtpd's timer and nothing
    else: delete ``"timeout": settings.PYMTA_COMMAND_TIMEOUT`` from the
    controller and aiosmtpd falls back to its own 300 s default, which a
    hand-built listener would never notice. Patch the setting, build the real
    kwargs, and the wiring is under test too.
    """
    kwargs = build_smtp_kwargs(tls_context=None)
    kwargs.update(overrides)
    server = await loop.create_server(
        lambda: HardenedSMTP(_NullHandler(), **kwargs), host="127.0.0.1", port=0
    )
    return server, server.sockets[0].getsockname()[1], kwargs


@pytest.mark.asyncio
async def test_idle_command_timeout_closes_the_session(monkeypatch):
    """A peer that connects and says nothing is hung up on, with a reason.

    The integration suite cannot cover this: PYMTA_COMMAND_TIMEOUT is read once
    at startup, so testing it there would mean idling for the configured 120 s.
    Patching the setting and running a listener in-process exercises the same
    chain, setting -> build_smtp_kwargs -> aiosmtpd's deadline -> our
    _timeout_cb override.
    """
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    loop = asyncio.get_running_loop()
    server, port, kwargs = await _listener(loop)
    assert kwargs["timeout"] == 0.3, (
        "PYMTA_COMMAND_TIMEOUT is no longer wired into the SMTP timeout kwarg, "
        "so aiosmtpd would silently use its own 300s default"
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        greeting = await asyncio.wait_for(reader.readline(), timeout=5)
        assert greeting.startswith(b"220"), greeting

        # Say nothing. HardenedSMTP overrides aiosmtpd's silent _timeout_cb so
        # the peer is told why, the way Postfix does and the way our own
        # session deadline already did.
        rest = await asyncio.wait_for(reader.read(), timeout=10)
        assert b"421" in rest, rest
        assert b"Idle timeout" in rest, rest
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_command_timeout_is_rearmed_by_activity(monkeypatch):
    """Staying active keeps the session alive past one timeout period.

    The counterpart to the test above, and the reason PYMTA_SESSION_TIMEOUT
    exists: this deadline is reset by every accepted command, so it bounds
    silence rather than session length.
    """
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 1.0)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.wait_for(reader.readline(), timeout=5)
        # Four NOOPs at 0.4s spacing spans 1.6s, well past the 1.0s deadline.
        # The absolute slack per interval (0.6s), not the ratio, is what has to
        # survive scheduler jitter on a loaded CI box.
        for _ in range(4):
            await asyncio.sleep(0.4)
            writer.write(b"NOOP\r\n")
            await writer.drain()
            reply = await asyncio.wait_for(reader.readline(), timeout=5)
            assert reply.startswith(b"250"), reply
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_session_deadline_uses_the_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 1800)
    smtp = HardenedSMTP(_NullHandler(), hostname="mta.test", timeout=120)

    armed: list[float] = []
    loop = asyncio.get_running_loop()
    real_call_later = loop.call_later

    def _record(delay, callback, *args):
        armed.append(delay)
        handle = real_call_later(delay, callback, *args)
        handle.cancel()
        return handle

    monkeypatch.setattr(loop, "call_later", _record)
    smtp._arm_session_deadline()

    assert armed == [1800]
    assert smtp._session_deadline_handle is not None


@pytest.mark.asyncio
async def test_starttls_transport_swap_does_not_extend_the_deadline(monkeypatch):
    # aiosmtpd calls connection_made a second time when STARTTLS replaces the
    # transport. Re-arming there would hand the peer a fresh budget for the
    # price of one STARTTLS.
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 1800)
    smtp = _server()
    first = smtp._session_deadline_handle
    assert first is not None

    smtp._arm_session_deadline()
    assert smtp._session_deadline_handle is first


@pytest.mark.asyncio
async def test_session_deadline_disabled_by_zero(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 0)
    smtp = HardenedSMTP(_NullHandler(), hostname="mta.test", timeout=120)
    smtp._arm_session_deadline()
    assert smtp._session_deadline_handle is None


@pytest.mark.asyncio
async def test_expired_session_announces_then_closes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_SESSION_TIMEOUT", 1800)
    smtp = _server()

    smtp._session_expired()
    await asyncio.sleep(0)  # let the close task run

    assert bytes(smtp._writer.written).startswith(b"421 4.4.2")
    assert smtp.transport.closed is True


# ---------------------------------------------------------------------------
# Line length. The integration suite proves a 2000-octet body line is accepted
# and a 70000-octet one is not, which brackets the limit but says nothing about
# where the number came from. This is the wiring.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_line_length_limit_reaches_the_stream_reader():
    """PYMTA_MAX_LINE_LENGTH must land on the object that enforces it.

    aiosmtpd reads ``line_length_limit`` while building its StreamReader, and
    the reader's limit is what actually raises LimitOverrunError. Asserting the
    class attribute alone would pass if that plumbing were removed, since the
    attribute would still hold the right number and mean nothing.
    """
    smtp = HardenedSMTP(_NullHandler(), hostname="mta.test", timeout=120)
    assert smtp.line_length_limit == settings.PYMTA_MAX_LINE_LENGTH
    # SMTP.__init__ passes the class attribute straight into the StreamReader it
    # hands to StreamReaderProtocol, and that reader's limit is what raises
    # LimitOverrunError. This is the end of the chain, in asyncio's hands.
    assert smtp._stream_reader._limit == settings.PYMTA_MAX_LINE_LENGTH


@pytest.mark.asyncio
async def test_line_length_limit_is_above_the_rfc_minimum():
    """Guard the deliverability decision, not just the plumbing.

    Dropping back to aiosmtpd's RFC-strict 1001 would permanently reject mail
    Postfix accepts, which is the regression PYMTA_MAX_LINE_LENGTH exists to
    prevent. Cheap to state, and it fails loudly if someone "restores the RFC
    default" without reading why it was raised.
    """
    assert settings.PYMTA_MAX_LINE_LENGTH > 1001


# ---------------------------------------------------------------------------
# Client blocklist. Driven against a real listener over a real socket: the
# matcher on its own would pass even if nothing ever called it.
# ---------------------------------------------------------------------------


async def _blocklist_listener(loop, networks: str, monkeypatch):
    from pymta.limits import IPGate
    from pymta.settings import _env_networks

    monkeypatch.setenv("PYMTA_TEST_BLOCKED", networks)
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_NETWORKS", _env_networks("PYMTA_TEST_BLOCKED"))
    kwargs = build_smtp_kwargs(tls_context=None)
    gate = IPGate(max_total=0)
    server = await loop.create_server(
        lambda: HardenedSMTP(_NullHandler(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_blocked_network_is_refused_before_the_greeting(monkeypatch):
    """A blocked client gets 554 instead of a banner, and no dialogue."""
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "127.0.0.0/8", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"554"), first
        assert b"220" not in first, first
        # And the connection is over: no command is entertained.
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b"", rest
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unblocked_network_still_gets_its_banner(monkeypatch):
    """The discriminating half: a list that does not match must not refuse.

    Without this, a blocklist that rejected everything would satisfy the test
    above and look correct.
    """
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "203.0.113.0/24", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"220"), first
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_empty_blocklist_blocks_nothing(monkeypatch):
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "", monkeypatch)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        assert first.startswith(b"220"), first
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Per-connection summary (``session_closed``).
#
# The only record of a sender that never reached a refusal or a message
# outcome of ours: TLS refused, answered by aiosmtpd on its own, timed out, or
# gone mid-DATA. Driven over a real socket because the interesting replies are
# the ones aiosmtpd sends without calling any hook.
# ---------------------------------------------------------------------------


async def _session_summaries(caplog, *, count=1, timeout=5.0):
    """The ``session_closed`` records, once ``count`` of them have arrived.

    connection_lost runs a loop iteration or two after the client hangs up, so
    the line is waited for rather than expected synchronously.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        found = [r for r in caplog.records if r.getMessage() == "session_closed"]
        if len(found) >= count or asyncio.get_running_loop().time() > deadline:
            return found
        await asyncio.sleep(0.02)


async def _dialogue(port, lines, *, quit_=True):
    """Send each line, read one reply per line (multi-line replies in full)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    replies = [await asyncio.wait_for(reader.readline(), timeout=5)]
    for line in lines:
        writer.write(line)
        await writer.drain()
        while True:
            reply = await asyncio.wait_for(reader.readline(), timeout=5)
            replies.append(reply)
            if reply[3:4] != b"-":
                break
    if quit_:
        writer.write(b"QUIT\r\n")
        await writer.drain()
        replies.append(await asyncio.wait_for(reader.readline(), timeout=5))
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return replies


_ENVELOPE = [
    b"EHLO mx.sender.example\r\n",
    b"MAIL FROM:<alice@sender.example>\r\n",
    b"RCPT TO:<bob@example.com>\r\n",
]


@pytest.mark.asyncio
async def test_a_delivered_session_is_summarised_at_info(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\nbody\r\n.\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.INFO
    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=1 quit=1"
    assert record.closed_by == "quit"
    assert record.sender == "alice@sender.example"
    assert record.helo == "mx.sender.example"
    assert record.client_ip == "127.0.0.1"
    assert record.errors == 0
    assert record.tls is False
    assert not hasattr(record, "last_error_reply")


@pytest.mark.asyncio
async def test_a_refusal_aiosmtpd_makes_on_its_own_is_recorded(caplog):
    """555 for an unknown RCPT parameter never reaches a hook of ours.

    It is exactly the kind of refusal that left no trace before: aiosmtpd
    answers it inside ``smtp_RCPT`` and the handler is never called.
    """
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"RCPT TO:<bob@example.com> NOTIFY=FAILURE\r\n",
                ],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"555"), replies
    assert record.commands == "ehlo=1 mail=1 rcpt=0/1 quit=1"
    assert record.errors == 1
    assert record.last_error_verb == "rcpt"
    assert record.last_error_reply.startswith("555")
    assert "NOTIFY=FAILURE" in record.last_error_arg


@pytest.mark.asyncio
async def test_an_oversize_body_refused_by_aiosmtpd_is_recorded(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, data_size_limit=64)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\n" + b"y" * 200 + b"\r\n.\r\n"]
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"552"), replies
    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=0/1 quit=1"
    assert record.last_error_verb == "data"
    assert record.last_error_reply.startswith("552")


@pytest.mark.asyncio
async def test_a_hang_up_mid_data_is_counted_as_a_failed_data(caplog):
    """The summary runs before the cut-off DATA would have counted itself."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in [*_ENVELOPE, b"DATA\r\n"]:
                writer.write(line)
                await writer.drain()
                while (await reader.readline())[3:4] == b"-":
                    pass
            writer.write(b"Subject: half a message\r\n")
            await writer.drain()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.commands == "ehlo=1 mail=1 rcpt=1 data=0/1"
    assert record.closed_by in ("peer_closed", "peer_reset")


@pytest.mark.asyncio
async def test_a_failed_starttls_handshake_is_summarised_not_reported_as_a_bug(caplog):
    """A sender that refuses our TLS shows up here and nowhere else.

    It used to land in ``handler_error`` with a traceback and in the
    internal_error counters, as if pymta had crashed, with no session id or
    client to tell whose mail it was.
    """
    cert, key = "/certs/pymta-test.crt", "/certs/pymta-test.key"
    if not (os.path.exists(cert) and os.path.exists(key)):
        pytest.skip("the dev image's self-signed certificate is not available")
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, tls_context=ctx)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in (b"EHLO mx.sender.example\r\n", b"STARTTLS\r\n"):
                writer.write(line)
                await writer.drain()
                while (await reader.readline())[3:4] == b"-":
                    pass
            # Not a ClientHello: the server-side handshake fails on it.
            writer.write(b"this is not TLS\r\n" * 4)
            await writer.drain()
            with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "tls_failed"
    assert record.tls is False
    assert record.tls_error
    assert record.commands == "ehlo=1 starttls=0/1"
    assert not [r for r in caplog.records if r.getMessage() == "handler_error"]


@pytest.mark.asyncio
async def test_a_silent_connection_stays_at_debug(caplog):
    """Connect and close without a command: a TCP health check, not a sender."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [], quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.DEBUG
    assert record.commands == ""


@pytest.mark.asyncio
async def test_log_sessions_off_drops_the_summary_to_debug(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_LOG_SESSIONS", False)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.levelno == logging.DEBUG


@pytest.mark.asyncio
async def test_a_connection_refused_at_the_gate_is_not_summarised(monkeypatch, caplog):
    """Gate refusals are what a flood is made of; the metric counts them."""
    loop = asyncio.get_running_loop()
    server, port = await _blocklist_listener(loop, "127.0.0.0/8", monkeypatch)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            assert await _session_summaries(caplog, timeout=0.5) == []
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_commands_are_counted_by_verb_and_reply_class():
    """``pymta_commands_total`` was declared and never incremented."""

    def sample(verb, cls):
        value = REGISTRY.get_sample_value("pymta_commands_total", {"verb": verb, "class": cls})
        return value or 0.0

    before_ok, before_refused = sample("MAIL", "2xx"), sample("RCPT", "5xx")
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        await _dialogue(
            port,
            [
                b"EHLO mx.sender.example\r\n",
                b"MAIL FROM:<alice@sender.example>\r\n",
                b"RCPT TO:<bob@example.com> NOTIFY=FAILURE\r\n",
            ],
        )
    finally:
        server.close()
        await server.wait_closed()

    assert sample("MAIL", "2xx") == before_ok + 1
    assert sample("RCPT", "5xx") == before_refused + 1


@pytest.mark.asyncio
async def test_an_auth_argument_never_reaches_the_summary(caplog):
    """``AUTH PLAIN <base64>`` is refused 502, and its argument is credentials."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(
                port,
                [b"EHLO mx.sender.example\r\n", b"AUTH PLAIN AGFsaWNlAHMzY3JldA==\r\n"],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.last_error_verb == "auth"
    assert record.last_error_arg == "[redacted]"
    assert "AGFsaWNl" not in LogfmtFormatter().format(record)


@pytest.mark.asyncio
async def test_a_refused_quit_is_not_what_closed_the_session(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port, [b"EHLO mx.sender.example\r\n", b"QUIT now\r\n"], quit_=False
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-1].startswith(b"501"), replies
    assert record.commands == "ehlo=1 quit=0/1"
    assert record.closed_by in ("peer_closed", "peer_reset")


@pytest.mark.asyncio
async def test_a_silent_connection_that_times_out_stays_at_debug(monkeypatch, caplog):
    """The 421 we send on the idle timeout is not the peer saying something."""
    monkeypatch.setattr(settings, "PYMTA_COMMAND_TIMEOUT", 0.3)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "command_timeout"
    assert record.levelno == logging.DEBUG


@pytest.mark.asyncio
async def test_a_line_refused_before_dispatch_still_reaches_info(caplog):
    """An unknown verb never enters ``commands``, but the peer did talk."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"FROBNICATE\r\n"], quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.commands == ""
    assert record.last_error_reply.startswith("500")
    assert record.levelno == logging.INFO


@pytest.mark.asyncio
async def test_a_refused_nested_mail_does_not_replace_the_sender(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    b"MAIL FROM:<mallory@other.example>\r\n",
                ],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"503"), replies
    assert record.sender == "alice@sender.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mail", "sender"),
    [
        (b"MAIL FROM:<>\r\n", "<>"),
        # No address at all: the raw argument, not a bounce.
        (b'MAIL FROM:"\r\n', 'FROM:"'),
    ],
)
async def test_only_a_real_null_sender_is_summarised_as_one(caplog, mail, sender):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, [b"EHLO mx.sender.example\r\n", mail])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.sender == sender


@pytest.mark.asyncio
async def test_a_long_sender_keeps_its_domain_in_the_summary(caplog):
    sender = "SRS0=Ab3d=TT=bounces.mailer.example=" + "b" * 400 + "@forwarder.example"
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            replies = await _dialogue(
                port,
                [b"EHLO mx.sender.example\r\n", b"MAIL FROM:<" + sender.encode() + b">\r\n"],
            )
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"250"), replies
    assert record.sender.startswith("SRS0=Ab3d=")
    assert record.sender.endswith("@forwarder.example")
    assert len(record.sender) < len(sender)


# ---------------------------------------------------------------------------
# MAIL command-line limit: 548 octets after EHLO, whatever else happened.
#
# aiosmtpd keeps the limit in a class-level dict: every EHLO on any connection
# adds 36 to it and every new connection resets it to 512.
# ---------------------------------------------------------------------------


def _mail_line(length: int) -> bytes:
    """A MAIL command of exactly ``length`` octets, CRLF excluded."""
    fixed = len(b"MAIL FROM:<@forwarder.example>")
    return b"MAIL FROM:<" + b"a" * (length - fixed) + b"@forwarder.example>\r\n"


@pytest.mark.asyncio
async def test_mail_line_limit_does_not_grow_with_repeated_ehlo():
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        ehlos = [b"EHLO mx.sender.example\r\n"] * 4
        over = await _dialogue(port, [*ehlos, _mail_line(549)])
        at = await _dialogue(port, [*ehlos, _mail_line(548)])
    finally:
        server.close()
        await server.wait_closed()

    assert over[-2].startswith(b"500"), over[-2]
    assert at[-2].startswith(b"250"), at[-2]


@pytest.mark.asyncio
async def test_mail_line_limit_is_not_shared_between_connections():
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        first = await asyncio.open_connection("127.0.0.1", port)
        others = [await asyncio.open_connection("127.0.0.1", port) for _ in range(3)]
        for reader, writer in [first, *others]:
            await reader.readline()
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            while (await asyncio.wait_for(reader.readline(), timeout=5))[3:4] == b"-":
                pass
        # A connection accepted after our EHLO: resets the shared limit to 512.
        late = await asyncio.open_connection("127.0.0.1", port)
        await late[0].readline()

        reader, writer = first
        replies = []
        for line in (_mail_line(549), _mail_line(548)):
            writer.write(line)
            await writer.drain()
            replies.append(await asyncio.wait_for(reader.readline(), timeout=5))
        for _, w in [first, *others, late]:
            w.close()
    finally:
        server.close()
        await server.wait_closed()

    assert replies[0].startswith(b"500"), replies
    assert replies[1].startswith(b"250"), replies


# ---------------------------------------------------------------------------
# One ``reject`` line per refusal the handler did not log.
#
# ``session_closed`` keeps the last error and the last sender only, and a
# sending platform reuses one connection for many messages.
# ---------------------------------------------------------------------------


def _rejects(caplog):
    return [r for r in caplog.records if r.getMessage() == "reject"]


@pytest.mark.asyncio
async def test_a_refusal_by_aiosmtpd_names_the_sender_of_its_own_envelope(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            await _dialogue(
                port,
                [
                    b"EHLO mx.platform.example\r\n",
                    b"MAIL FROM:<first@lost.example>\r\n",
                    b"RCPT TO:<bob@example.com> NOTIFY=FAILURE\r\n",
                    b"RSET\r\n",
                    b"MAIL FROM:<second@other.example>\r\n",
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    (reject,) = _rejects(caplog)
    assert reject.levelno == logging.INFO
    assert reject.sender == "first@lost.example"
    assert (reject.verb, reject.code, reject.reason) == ("rcpt", 555, "protocol")
    assert "NOTIFY=FAILURE" in reject.arg
    assert reject.session_id == summary.session_id
    assert reject.client_ip == "127.0.0.1"
    # The summary alone would have pointed at the wrong sender.
    assert summary.sender == "second@other.example"


@pytest.mark.asyncio
async def test_an_oversize_body_names_sender_and_recipients(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, data_size_limit=64)
    try:
        with caplog.at_level(logging.INFO):
            await _dialogue(
                port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\n" + b"y" * 200 + b"\r\n.\r\n"]
            )
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    (reject,) = _rejects(caplog)
    assert (reject.verb, reject.code) == ("data", 552)
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"
    assert reject.recipient_count == 1


@pytest.mark.asyncio
async def test_a_mail_line_refused_for_its_length_is_logged_with_its_domain(caplog):
    """Refused before dispatch: no verb ever sees the line."""
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [b"EHLO mx.sender.example\r\n", _mail_line(600)])
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"500"), replies
    (reject,) = _rejects(caplog)
    assert reject.verb is None
    assert reject.code == 500
    assert reject.arg.startswith("MAIL FROM:<aaa")
    assert reject.arg.endswith("@forwarder.example>")
    assert summary.last_error_arg == reject.arg


@pytest.mark.asyncio
async def test_a_refusal_the_handler_logged_gets_no_second_line(caplog):
    class Handler:
        async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
            server.refusal_logged = True
            return "550 5.1.1 No such recipient"

    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: HardenedSMTP(Handler(), **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [*_ENVELOPE, b"RCPT TO:<x@example.com> FOO=1\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-3].startswith(b"550"), replies
    # Only the second RCPT, which aiosmtpd refused: the flag does not stick.
    (reject,) = _rejects(caplog)
    assert reject.code == 555


@pytest.mark.asyncio
async def test_protocol_refusal_lines_stop_at_the_error_budget(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO):
            await _dialogue(
                port,
                [
                    b"EHLO mx.sender.example\r\n",
                    b"MAIL FROM:<alice@sender.example>\r\n",
                    *[b"RCPT TO:<bob@example.com> NOTIFY=FAILURE\r\n"] * 4,
                ],
            )
            (summary,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert len(_rejects(caplog)) == 2
    assert summary.errors == 4


@pytest.mark.asyncio
async def test_a_message_deferred_for_lack_of_a_data_slot_is_logged(caplog):
    gate = IPGate(max_total=0, max_data=1)
    assert gate.acquire_data("198.51.100.9") is True
    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: HardenedSMTP(_NullHandler(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with caplog.at_level(logging.INFO):
            replies = await _dialogue(port, [*_ENVELOPE, b"DATA\r\n"])
            await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert replies[-2].startswith(b"451"), replies
    (reject,) = _rejects(caplog)
    assert reject.reason == "max_concurrent_data"
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"


async def _refused_at_the_cap(port) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    reply = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("log_sessions", [True, False])
async def test_cap_refusals_reach_info_once_per_address(monkeypatch, caplog, log_sessions):
    """A sender held off by a cap is deferred mail; a flood at its cap is not news."""
    monkeypatch.setattr(smtp_protocol, "_cap_log_throttle", LogThrottle(interval=60, max_keys=8))
    monkeypatch.setattr(settings, "PYMTA_LOG_SESSIONS", log_sessions)
    gate = IPGate(max_total=1)
    loop = asyncio.get_running_loop()
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: HardenedSMTP(_NullHandler(), ip_gate=gate, **kwargs), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    holder = await asyncio.open_connection("127.0.0.1", port)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            assert (await holder[0].readline()).startswith(b"220")
            replies = [await _refused_at_the_cap(port) for _ in range(3)]
            # Let the interval pass for this address.
            for entry in smtp_protocol._cap_log_throttle._seen.values():
                entry[0] -= 60
            replies.append(await _refused_at_the_cap(port))
    finally:
        holder[1].close()
        server.close()
        await server.wait_closed()

    assert all(r.startswith(b"421") for r in replies), replies
    capped = [r for r in caplog.records if r.getMessage() == "connection_capped"]
    assert all(r.client_ip == "127.0.0.1" for r in capped)
    levels = [r.levelno for r in capped]
    if not log_sessions:
        assert levels == [logging.DEBUG] * 4
        return
    assert levels == [logging.INFO, logging.DEBUG, logging.DEBUG, logging.INFO]
    assert capped[0].suppressed == 0
    assert capped[3].suppressed == 2


def test_a_refused_auth_line_is_redacted():
    """The per-verb call limit refuses a line before dispatch, AUTH included."""
    smtp = _server()
    smtp._last_line = b"auth PLAIN AGFsaWNlAHMzY3JldA==\r\n"
    assert smtp._refused_line() == "AUTH [redacted]"


@pytest.mark.asyncio
async def test_a_hang_up_before_data_names_the_recipients_left_waiting(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, _ENVELOPE, quit_=False)
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert record.sender == "alice@sender.example"
    assert record.recipients == "bob@example.com"
    assert record.recipient_count == 1


@pytest.mark.asyncio
async def test_a_completed_session_names_no_pending_recipients(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            await _dialogue(port, [*_ENVELOPE, b"DATA\r\n", b"Subject: x\r\n\r\nbody\r\n.\r\n"])
            (record,) = await _session_summaries(caplog)
    finally:
        server.close()
        await server.wait_closed()

    assert not hasattr(record, "recipients")


@pytest.mark.asyncio
async def test_sessions_cut_by_the_shutdown_deadline_are_summarised(caplog):
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop)
    try:
        with caplog.at_level(logging.INFO, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await reader.readline()
            for line in _ENVELOPE:
                writer.write(line)
                await writer.drain()
                while (await asyncio.wait_for(reader.readline(), timeout=5))[3:4] == b"-":
                    pass
            log_abandoned_sessions()
            records = await _session_summaries(caplog, timeout=1)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            # The connection closing afterwards must not log it again.
            await asyncio.sleep(0.1)
            (record,) = await _session_summaries(caplog)
            assert records == [record]
    finally:
        server.close()
        await server.wait_closed()

    assert record.closed_by == "shutdown"
    assert record.sender == "alice@sender.example"
    assert record.recipients == "bob@example.com"


@pytest.mark.asyncio
async def test_a_connection_without_a_proxy_header_leaves_a_trace(caplog):
    """A balancer that stops sending PROXY takes all mail down, silently."""

    def refused():
        value = REGISTRY.get_sample_value(
            "pymta_connections_total", {"result": "rejected_invalid_proxy"}
        )
        return value or 0.0

    before = refused()
    loop = asyncio.get_running_loop()
    server, port, _ = await _listener(loop, proxy_protocol_timeout=2)
    try:
        with caplog.at_level(logging.DEBUG, logger="pymta.smtp_protocol"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"EHLO mx.sender.example\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    (record,) = [r for r in caplog.records if r.getMessage() == "proxy_header_invalid"]
    assert record.wire_peer == "127.0.0.1"
    assert refused() == before + 1
