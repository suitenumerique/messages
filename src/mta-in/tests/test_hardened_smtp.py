"""Unit tests for :class:`pymta.smtp_protocol.HardenedSMTP`.

These exercise the two places where we step outside aiosmtpd's own control
flow: the forced disconnect behind a 421, and the DATA-phase deadline. Both
are driven against a fake transport: no sockets, no Docker stack.
"""

from __future__ import annotations

import asyncio

import pytest

from pymta import settings
from pymta.smtp_protocol import HardenedSMTP


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


@pytest.mark.asyncio
async def test_session_deadline_uses_the_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_SESSION_SECONDS", 1800)
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
    monkeypatch.setattr(settings, "PYMTA_MAX_SESSION_SECONDS", 1800)
    smtp = _server()
    first = smtp._session_deadline_handle
    assert first is not None

    smtp._arm_session_deadline()
    assert smtp._session_deadline_handle is first


@pytest.mark.asyncio
async def test_session_deadline_disabled_by_zero(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_SESSION_SECONDS", 0)
    smtp = HardenedSMTP(_NullHandler(), hostname="mta.test", timeout=120)
    smtp._arm_session_deadline()
    assert smtp._session_deadline_handle is None


@pytest.mark.asyncio
async def test_expired_session_announces_then_closes(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_SESSION_SECONDS", 1800)
    smtp = _server()

    smtp._session_expired()
    await asyncio.sleep(0)  # let the close task run

    assert bytes(smtp._writer.written).startswith(b"421 4.4.2")
    assert smtp.transport.closed is True
