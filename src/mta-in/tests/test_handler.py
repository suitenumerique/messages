"""Unit tests for :mod:`pymta.handler`.

These tests cover the *session-state* invariants of the handler (counter
bumps, gate paths). They run the handler against fake session/envelope/MDA
stand-ins: no Docker stack, no real SMTP traffic.
"""

from __future__ import annotations

import types
from ipaddress import ip_address, ip_network

import pytest

from pymta import settings
from pymta.handler import (
    _ENVELOPES_ATTR,
    _RCPT_MISSES_ATTR,
    _SOFT_ERRORS_ATTR,
    NULL_SENDER_SENTINEL,
    InboundHandler,
)
from pymta.mda_async import MDAResult


class _FakeMDA:
    """Stand-in for MDAClient. Returns whatever the test wires up."""

    def __init__(
        self,
        check_result: MDAResult | None = None,
        deliver_result: MDAResult | None = None,
    ):
        self.check_result = check_result or MDAResult(
            ok=True, temp_fail=False, payload={}, status_code=200
        )
        self.deliver_result = deliver_result or MDAResult(
            ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200
        )
        self.deliver_kwargs: dict | None = None

    async def check_recipient(self, address: str) -> MDAResult:
        return self.check_result

    async def deliver(self, **kwargs) -> MDAResult:
        self.deliver_kwargs = kwargs
        return self.deliver_result


def _session():
    return types.SimpleNamespace(host_name=None, peer=("203.0.113.5", 12345))


def _envelope():
    return types.SimpleNamespace(
        mail_from=None, rcpt_tos=[], mail_options=[], rcpt_options=[], content=b""
    )


def _handler(mda=None) -> InboundHandler:
    return InboundHandler(mda or _FakeMDA())


# ---------------------------------------------------------------------------
# MAIL SIZE= path bumps the soft-error counter on both rejection branches.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mail_size_bad_value_bumps_soft_errors():
    session, envelope = _session(), _envelope()
    reply = await _handler().handle_MAIL(
        None, session, envelope, "<a@example.com>", ["SIZE=not-a-number"]
    )
    assert reply.startswith("501")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


@pytest.mark.asyncio
async def test_mail_size_oversize_bumps_soft_errors():
    session, envelope = _session(), _envelope()
    too_big = settings.MAX_INCOMING_EMAIL_SIZE + 1
    reply = await _handler().handle_MAIL(
        None, session, envelope, "<a@example.com>", [f"SIZE={too_big}"]
    )
    assert reply.startswith("552")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


# ---------------------------------------------------------------------------
# DATA negative paths bump the soft-error counter.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_nul_byte_bumps_soft_errors():
    session, envelope = _session(), _envelope()
    envelope.content = b"Subject: x\r\n\r\nhello\x00world\r\n"
    reply = await _handler().handle_DATA(None, session, envelope)
    assert reply.startswith("554")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


@pytest.mark.asyncio
async def test_data_oversize_bumps_soft_errors():
    session, envelope = _session(), _envelope()
    envelope.content = b"x" * (settings.MAX_INCOMING_EMAIL_SIZE + 10)
    reply = await _handler().handle_DATA(None, session, envelope)
    assert reply.startswith("552")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


@pytest.mark.asyncio
async def test_data_max_envelopes_bumps_soft_errors():
    session, envelope = _session(), _envelope()
    setattr(session, _ENVELOPES_ATTR, settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION)
    reply = await _handler().handle_DATA(None, session, envelope)
    assert reply.startswith("451")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


# ---------------------------------------------------------------------------
# RCPT miss counter / dedicated cutoff (S3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_counter_triggers_421_at_limit(monkeypatch):
    # Tight limit so we don't have to do many round-trips.
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 3)
    mda = _FakeMDA(
        check_result=MDAResult(
            ok=True, temp_fail=False, payload={}, status_code=200
        )  # exists=False for every address
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()

    # First two misses get the normal 550.
    for i in range(2):
        reply = await handler.handle_RCPT(
            None, session, envelope, f"<miss{i}@example.com>", []
        )
        assert reply.startswith("550"), reply
    # Third miss hits the per-session cap and forces 421.
    reply = await handler.handle_RCPT(
        None, session, envelope, "<miss3@example.com>", []
    )
    assert reply.startswith("421")
    assert getattr(session, _RCPT_MISSES_ATTR) == 3


@pytest.mark.asyncio
async def test_rcpt_existence_does_not_increment_miss_counter():
    mda = _FakeMDA(
        check_result=MDAResult(
            ok=True,
            temp_fail=False,
            payload={"hit@example.com": True},
            status_code=200,
        )
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()
    reply = await handler.handle_RCPT(
        None, session, envelope, "<hit@example.com>", []
    )
    assert reply.startswith("250")
    assert getattr(session, _RCPT_MISSES_ATTR, 0) == 0


# ---------------------------------------------------------------------------
# Hard-error budget cutoff still fires from the existing gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_error_limit_blocks_further_rcpts(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_HARD_ERROR_LIMIT", 2)
    handler, session, envelope = _handler(), _session(), _envelope()
    setattr(session, _SOFT_ERRORS_ATTR, 2)
    reply = await handler.handle_RCPT(
        None, session, envelope, "<anyone@example.com>", []
    )
    assert reply.startswith("421")


# ---------------------------------------------------------------------------
# Null sender survives the round-trip via the sentinel.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_sender_round_trip_via_sentinel():
    session, envelope = _session(), _envelope()
    reply = await _handler().handle_MAIL(None, session, envelope, "<>", [])
    assert reply.startswith("250")
    assert envelope.mail_from == NULL_SENDER_SENTINEL


# ---------------------------------------------------------------------------
# PROXY-protocol source survives the STARTTLS session rebuild.
#
# aiosmtpd rebuilds ``session`` from scratch when the client issues STARTTLS,
# dropping ``session.proxy_data`` and resetting ``session.peer`` to the raw TCP
# peer (the load balancer). The real client IP must still reach the MDA on the
# post-TLS DATA command. Regression guard for the "client_ip == LB internal IP"
# bug seen in production behind HAProxy.
# ---------------------------------------------------------------------------


class _FakeServer:
    """Per-connection SMTP protocol stand-in (survives the STARTTLS swap)."""

    def __init__(self):
        self.disconnect_requested = False
        self.data_phase_started = None

    async def acquire_gate_post_proxy(self, ip: str) -> bool:
        return True

    def request_disconnect(self) -> None:
        self.disconnect_requested = True


@pytest.mark.asyncio
async def test_proxy_source_survives_starttls_and_reaches_mda(monkeypatch):
    real_client = ip_address("203.0.113.9")
    lb_peer = ("10.89.0.2", 43154)  # HAProxy/podman gateway, NOT the client

    # Mirror a real deployment: PROXY protocol on, balancer allowlisted.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")])

    server = _FakeServer()
    mda = _FakeMDA()
    handler = _handler(mda)

    # 1. PROXY header parsed on the plaintext connection, before STARTTLS.
    proxy_data = types.SimpleNamespace(
        src_addr=real_client, src_port=52000, version=2, protocol=1
    )
    session_pre_tls = types.SimpleNamespace(
        host_name=None, peer=lb_peer, proxy_data=proxy_data
    )
    gate = await handler.handle_PROXY(server, session_pre_tls, _envelope(), proxy_data)
    assert gate is True

    # 2. STARTTLS rebuilds the session: proxy_data gone, peer is the LB again.
    #    Same server instance carries over.
    session_post_tls = types.SimpleNamespace(
        host_name=None, peer=lb_peer, proxy_data=None
    )

    # 3. DATA delivers using the post-TLS session.
    envelope = _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["rcpt@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"
    reply = await handler.handle_DATA(server, session_post_tls, envelope)

    assert reply.startswith("250"), reply
    assert mda.deliver_kwargs is not None
    assert mda.deliver_kwargs["client_address"] == "203.0.113.9"
    assert mda.deliver_kwargs["client_port"] == "52000"


# ---------------------------------------------------------------------------
# Partial delivery (HTTP 207) must defer, never bounce.
#
# The MDA answers 207 when it delivered to some recipients and failed on
# others. It has no per-recipient reply channel back to us, so a permanent
# 554 tells the sending MTA to bounce the whole envelope and the stragglers
# are lost for good. 451 costs a duplicate for the recipients already served.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_delivery_defers_instead_of_bouncing():
    mda = _FakeMDA(
        deliver_result=MDAResult(
            ok=False,
            temp_fail=True,
            payload={"status": "partial_success", "delivered": 1, "failed": 1},
            status_code=207,
        )
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["a@example.com", "b@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"

    reply = await handler.handle_DATA(None, session, envelope)
    assert reply.startswith("451"), reply


@pytest.mark.asyncio
async def test_http_200_without_status_ok_defers():
    # A 200 whose body we don't recognise is not proof of delivery; deferring
    # keeps the message alive while someone works out what the MDA meant.
    mda = _FakeMDA(
        deliver_result=MDAResult(
            ok=True, temp_fail=False, payload={"status": "queued"}, status_code=200
        )
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["a@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"

    reply = await handler.handle_DATA(None, session, envelope)
    assert reply.startswith("451"), reply


@pytest.mark.asyncio
async def test_message_rejecting_status_still_bounces():
    # 400/413/415 keep their permanent reject: retrying sends the same bytes.
    mda = _FakeMDA(
        deliver_result=MDAResult(
            ok=False, temp_fail=False, payload={"detail": "unparseable"}, status_code=400
        )
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["a@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"

    reply = await handler.handle_DATA(None, session, envelope)
    assert reply.startswith("554"), reply


# ---------------------------------------------------------------------------
# A 421 must actually hang up.
#
# aiosmtpd pushes whatever a hook returns and loops back for the next command,
# so "goodbye" is only a promise until the handler asks for the disconnect.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_cutoff_requests_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 1)
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()

    reply = await handler.handle_RCPT(
        server, session, envelope, "<miss@example.com>", []
    )
    assert reply.startswith("421")
    assert server.disconnect_requested is True


@pytest.mark.asyncio
async def test_hard_error_cutoff_requests_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_HARD_ERROR_LIMIT", 2)
    server = _FakeServer()
    setattr(server, _SOFT_ERRORS_ATTR, 2)
    handler, session, envelope = _handler(), _session(), _envelope()

    reply = await handler.handle_RCPT(
        server, session, envelope, "<anyone@example.com>", []
    )
    assert reply.startswith("421")
    assert server.disconnect_requested is True


# ---------------------------------------------------------------------------
# Abuse counters are keyed to the TCP connection, not the session object.
#
# aiosmtpd rebuilds `session` from scratch on STARTTLS. Counters living there
# would hand a peer a free budget reset for the price of one STARTTLS.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_budget_survives_the_starttls_session_rebuild(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 3)
    server = _FakeServer()
    handler, envelope = _handler(), _envelope()

    # Two misses before STARTTLS.
    for i in range(2):
        reply = await handler.handle_RCPT(
            server, _session(), envelope, f"<miss{i}@example.com>", []
        )
        assert reply.startswith("550"), reply

    # STARTTLS hands us a brand-new session object; the budget must not reset.
    reply = await handler.handle_RCPT(
        server, _session(), envelope, "<miss3@example.com>", []
    )
    assert reply.startswith("421"), reply
    assert getattr(server, _RCPT_MISSES_ATTR) == 3


# ---------------------------------------------------------------------------
# PROXY-protocol trust boundary.
#
# aiosmtpd parses a PROXY header from whoever sends it. The claimed source
# becomes the per-IP rate-limit key AND the client_address the MDA bakes into
# Received, so an unfiltered header is a free pass past both.
# ---------------------------------------------------------------------------


def _proxy_data(src="203.0.113.9"):
    return types.SimpleNamespace(
        src_addr=ip_address(src), src_port=52000, version=2, protocol=1
    )


@pytest.mark.asyncio
async def test_proxy_header_from_untrusted_peer_is_refused(monkeypatch):
    monkeypatch.setattr(
        settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")]
    )
    session = types.SimpleNamespace(
        host_name=None, peer=("198.51.100.7", 5555), proxy_data=None
    )
    accepted = await _handler().handle_PROXY(
        _FakeServer(), session, _envelope(), _proxy_data()
    )
    assert accepted is False


@pytest.mark.asyncio
async def test_proxy_header_from_trusted_peer_is_accepted(monkeypatch):
    monkeypatch.setattr(
        settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")]
    )
    server = _FakeServer()
    session = types.SimpleNamespace(
        host_name=None, peer=("10.89.0.2", 5555), proxy_data=None
    )
    accepted = await _handler().handle_PROXY(
        server, session, _envelope(), _proxy_data()
    )
    assert accepted is True


@pytest.mark.asyncio
async def test_empty_allowlist_fails_closed(monkeypatch):
    # Unreachable in practice, since server.py refuses to start PROXY protocol
    # with an empty allowlist, but the matcher must not treat "no entries" as
    # "everyone".
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    session = types.SimpleNamespace(
        host_name=None, peer=("198.51.100.7", 5555), proxy_data=None
    )
    accepted = await _handler().handle_PROXY(
        _FakeServer(), session, _envelope(), _proxy_data()
    )
    assert accepted is False


# ---------------------------------------------------------------------------
# With PROXY protocol on, the wire peer is the balancer, never the client.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_proxy_source_reports_no_client_address(monkeypatch):
    # A PROXY v2 LOCAL command (health check) carries no source. Falling back
    # to session.peer would stamp the balancer's own IP into Received as if it
    # were the sender.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    mda = _FakeMDA()
    server = _FakeServer()
    session = types.SimpleNamespace(
        host_name="client.test", peer=("10.89.0.2", 43154), proxy_data=None
    )
    envelope = _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["rcpt@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"

    reply = await _handler(mda).handle_DATA(server, session, envelope)

    assert reply.startswith("250"), reply
    assert mda.deliver_kwargs["client_address"] is None
    assert mda.deliver_kwargs["client_port"] is None


@pytest.mark.asyncio
async def test_wire_peer_is_the_client_when_proxy_protocol_is_off():
    mda = _FakeMDA()
    session = _session()  # peer=("203.0.113.5", 12345)
    envelope = _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["rcpt@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"

    await _handler(mda).handle_DATA(_FakeServer(), session, envelope)

    assert mda.deliver_kwargs["client_address"] == "203.0.113.5"
    assert mda.deliver_kwargs["client_port"] == "12345"


# ---------------------------------------------------------------------------
# EHLO response filtering must leave a well-formed terminator.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ehlo_strips_auth_and_keeps_a_final_line():
    responses = [
        "250-mta.example.com",
        "250-SIZE 10240000",
        "250-8BITMIME",
        "250-AUTH ",
        "250 HELP",
    ]
    clean = await _handler().handle_EHLO(
        None, _session(), _envelope(), "client.example.com", responses
    )
    assert not any(line[4:].upper().startswith("AUTH") for line in clean)
    assert clean[-1].startswith("250 ")
    assert all(line.startswith("250-") for line in clean[:-1])


@pytest.mark.asyncio
async def test_ehlo_remarks_terminator_when_the_last_line_is_stripped():
    # Latent today (aiosmtpd always appends "250 HELP" last) but a reply left
    # ending on a "250-" continuation hangs clients forever.
    clean = await _handler().handle_EHLO(
        None,
        _session(),
        _envelope(),
        "client.example.com",
        ["250-mta.example.com", "250-SIZE 10240000", "250 PIPELINING"],
    )
    assert clean == ["250-mta.example.com", "250 SIZE 10240000"]
