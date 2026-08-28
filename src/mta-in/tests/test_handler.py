"""Unit tests for :mod:`pymta.handler`.

These tests cover the *session-state* invariants of the handler (counter
bumps, gate paths). They run the handler against fake session/envelope/MDA
stand-ins: no Docker stack, no real SMTP traffic.
"""

from __future__ import annotations

import logging
import time
import types
from ipaddress import ip_address, ip_network

import pytest

from pymta import settings
from pymta.handler import (
    _ENVELOPES_ATTR,
    _PROXY_SRC_ATTR,
    _RCPT_MISSES_ATTR,
    _REPLY_RESERVE_SECONDS,
    _SOFT_ERRORS_ATTR,
    NULL_SENDER_SENTINEL,
    InboundHandler,
    _peer_ip,
    _peer_port,
    _remaining_data_budget,
)
from pymta.mda_async import MDAResult


class _FakeMDA:
    """Stand-in for MDAClient. Returns whatever the test wires up.

    ``check_result`` pins one verbatim result for every address; leave it unset
    and the fake synthesises the real MDA shape instead, ``{address:
    check_exists}``. A miss has to be an explicit ``False``, because the
    handler treats a body that does not name the address as no answer at all.
    """

    def __init__(
        self,
        check_result: MDAResult | None = None,
        deliver_result: MDAResult | None = None,
        check_exists: bool = False,
    ):
        self.check_result = check_result
        self.check_exists = check_exists
        self.deliver_result = deliver_result or MDAResult(
            ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200
        )
        self.deliver_kwargs: dict | None = None

    async def check_recipient(self, address: str, session: str | None = None) -> MDAResult:
        if self.check_result is not None:
            return self.check_result
        return MDAResult(
            ok=True, temp_fail=False, payload={address: self.check_exists}, status_code=200
        )

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


async def _deliver_with(mda) -> str:
    """Run one complete DATA phase against ``mda`` and return the SMTP reply."""
    session, envelope = _session(), _envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = ["a@example.com"]
    envelope.content = b"Subject: hi\r\n\r\nbody\r\n"
    return await _handler(mda).handle_DATA(None, session, envelope)


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
    too_big = settings.PYMTA_MAX_INCOMING_EMAIL_SIZE + 1
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
    envelope.content = b"x" * (settings.PYMTA_MAX_INCOMING_EMAIL_SIZE + 10)
    reply = await _handler().handle_DATA(None, session, envelope)
    assert reply.startswith("552")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


@pytest.mark.asyncio
async def test_data_max_envelopes_closes_the_session():
    """421 rather than 451: the budget is spent and cannot be won back.

    Every further MAIL/DATA on this connection would fail the same way, so
    holding the channel open only invites attempts that must all be refused.
    421 means "closing transmission channel", and the disconnect follows.
    """
    session, envelope = _session(), _envelope()
    setattr(session, _ENVELOPES_ATTR, settings.PYMTA_MAX_ENVELOPES_PER_SESSION)
    reply = await _handler().handle_DATA(None, session, envelope)
    assert reply.startswith("421")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


# ---------------------------------------------------------------------------
# RCPT miss counter / dedicated cutoff (S3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_counter_triggers_421_at_limit(monkeypatch):
    # Tight limit so we don't have to do many round-trips.
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 3)
    mda = _FakeMDA(check_exists=False)
    handler, session, envelope = _handler(mda), _session(), _envelope()

    # First two misses get the normal 550.
    for i in range(2):
        reply = await handler.handle_RCPT(None, session, envelope, f"<miss{i}@example.com>", [])
        assert reply.startswith("550"), reply
    # Third miss hits the per-session cap and forces 421.
    reply = await handler.handle_RCPT(None, session, envelope, "<miss3@example.com>", [])
    assert reply.startswith("421")
    assert getattr(session, _RCPT_MISSES_ATTR) == 3


# ---------------------------------------------------------------------------
# A 200 that does not name the address is not a verdict.
#
# ``_post`` collapses an absent, unparseable, or non-object body to {}, so the
# handler cannot tell those apart from "the MDA said this mailbox is missing"
# unless it insists on the key. Reading a bodyless 200 (an ingress error page,
# a response shape that drifted) as a miss would answer 550 and have the
# sending MTA bounce mail to a working address.
# ---------------------------------------------------------------------------


UNUSABLE_CHECK_BODIES = [
    pytest.param({}, id="empty-body-or-unparseable"),
    pytest.param({"detail": "ok"}, id="proxy-200-page"),
    pytest.param({"other@example.com": True}, id="answers-a-different-address"),
    pytest.param({"Rcpt@example.com": True}, id="case-drifted-key"),
    pytest.param({"rcpt@example.com": None}, id="null-verdict"),
    pytest.param({"rcpt@example.com": "false"}, id="stringified-verdict"),
    pytest.param({"rcpt@example.com": 0}, id="numeric-verdict"),
    pytest.param({"rcpt@example.com": {"exists": False}}, id="nested-verdict-shape"),
    pytest.param({"rcpt@example.com": []}, id="empty-list-verdict"),
]


@pytest.mark.parametrize("payload", UNUSABLE_CHECK_BODIES)
@pytest.mark.asyncio
async def test_rcpt_defers_when_a_200_carries_no_verdict(payload):
    mda = _FakeMDA(
        check_result=MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)
    )
    handler, session, envelope = _handler(mda), _session(), _envelope()

    reply = await handler.handle_RCPT(None, session, envelope, "<rcpt@example.com>", [])

    assert reply.startswith("451"), reply
    assert envelope.rcpt_tos == []
    # Not a miss: an unusable body must not spend the unknown-recipient budget,
    # or an MDA hiccup would hang up on a sender addressing real mailboxes.
    assert getattr(session, _RCPT_MISSES_ATTR, 0) == 0


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(MDAResult(ok=False, temp_fail=True, payload={}, status_code=0), id="timeout"),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={}, status_code=0), id="breaker-open"
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={"detail": "no"}, status_code=400),
            id="http-400",
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={"detail": "no"}, status_code=413),
            id="http-413",
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={"detail": "no"}, status_code=415),
            id="http-415",
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={}, status_code=401), id="http-401"
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={}, status_code=404), id="http-404"
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={}, status_code=429), id="http-429"
        ),
        pytest.param(
            MDAResult(ok=False, temp_fail=True, payload={}, status_code=503), id="http-503"
        ),
    ],
)
@pytest.mark.asyncio
async def test_rcpt_defers_on_every_unsuccessful_check(result):
    # No check failure is ever a verdict on the mailbox: 400/413/415 included,
    # since there they describe the request we built, not the address.
    mda = _FakeMDA(check_result=result)
    handler, session, envelope = _handler(mda), _session(), _envelope()

    reply = await handler.handle_RCPT(None, session, envelope, "<rcpt@example.com>", [])

    assert reply.startswith("451"), reply
    assert envelope.rcpt_tos == []
    assert getattr(session, _RCPT_MISSES_ATTR, 0) == 0


@pytest.mark.asyncio
async def test_rcpt_explicit_false_is_still_a_permanent_miss():
    # The guard above must not soften a real answer: an explicit False is the
    # MDA saying the mailbox does not exist, and that stays a 550.
    mda = _FakeMDA(check_exists=False)
    handler, session, envelope = _handler(mda), _session(), _envelope()

    reply = await handler.handle_RCPT(None, session, envelope, "<rcpt@example.com>", [])

    assert reply.startswith("550"), reply
    assert getattr(session, _RCPT_MISSES_ATTR) == 1


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
    reply = await handler.handle_RCPT(None, session, envelope, "<hit@example.com>", [])
    assert reply.startswith("250")
    assert getattr(session, _RCPT_MISSES_ATTR, 0) == 0


# ---------------------------------------------------------------------------
# The DATA budget is one deadline shared by receive and deliver.
# ---------------------------------------------------------------------------


def test_data_budget_without_a_start_timestamp_is_the_full_budget():
    assert _remaining_data_budget(types.SimpleNamespace()) == float(settings.PYMTA_DATA_TIMEOUT)


def test_data_budget_subtracts_time_already_spent(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 120)
    server = types.SimpleNamespace(data_phase_started=time.monotonic() - 20)
    remaining = _remaining_data_budget(server)
    assert 120 - 20 - _REPLY_RESERVE_SECONDS - 1 < remaining <= 120 - 20 - _REPLY_RESERVE_SECONDS


def test_data_budget_uses_the_value_the_transport_was_armed_at(monkeypatch):
    """PYMTA_DATA_TIMEOUT is reloadable, so it can move mid-phase.

    The protocol arms the transport once, at DATA, and records what it used.
    Re-reading the setting here would size the deliver call against a deadline
    nobody set: raise it mid-phase and wait_for gets longer than the transport
    will allow, so the transport tears the session down first and the peer gets
    a bare 421 instead of the 451 the reply reserve exists to buy.
    """
    server = types.SimpleNamespace(
        data_phase_started=time.monotonic() - 10, data_phase_budget=60.0
    )
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 600)  # the SIGHUP
    remaining = _remaining_data_budget(server)
    assert remaining <= 60 - 10 - _REPLY_RESERVE_SECONDS


def test_data_budget_falls_back_when_no_budget_was_recorded(monkeypatch):
    """Unit-test fakes and any future caller that only sets the timestamp."""
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 120)
    server = types.SimpleNamespace(data_phase_started=time.monotonic() - 20)
    assert _remaining_data_budget(server) <= 120 - 20 - _REPLY_RESERVE_SECONDS


def test_data_budget_raises_once_spent(monkeypatch):
    # A slow receive that ate the whole budget must not still be granted a
    # deliver call: that would overrun the transport deadline the reserve is
    # there to stay clear of, costing the peer its 451.
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 60)
    server = types.SimpleNamespace(data_phase_started=time.monotonic() - 60)
    with pytest.raises(TimeoutError):
        _remaining_data_budget(server)


@pytest.mark.asyncio
async def test_data_replies_451_when_the_budget_is_already_spent(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 60)
    server = _FakeServer()
    server.data_phase_started = time.monotonic() - 60
    session, envelope = _session(), _envelope()
    envelope.content = b"From: a@example.com\r\n\r\nhi\r\n"
    reply = await _handler().handle_DATA(server, session, envelope)
    assert reply.startswith("451")


# ---------------------------------------------------------------------------
# Hard-error budget cutoff still fires from the existing gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_error_limit_blocks_further_rcpts(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    handler, session, envelope = _handler(), _session(), _envelope()
    setattr(session, _SOFT_ERRORS_ATTR, 2)
    reply = await handler.handle_RCPT(None, session, envelope, "<anyone@example.com>", [])
    assert reply.startswith("421")


@pytest.mark.asyncio
async def test_mail_malformed_sender_bumps_soft_errors():
    # Without this the hard-error budget is sidestepped by burning errors on
    # MAIL FROM alone, which never counted towards it.
    session, envelope = _session(), _envelope()
    reply = await _handler().handle_MAIL(None, session, envelope, "<not an address>", [])
    assert reply.startswith("501")
    assert getattr(session, _SOFT_ERRORS_ATTR) == 1


@pytest.mark.asyncio
async def test_hard_error_limit_blocks_further_mails(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    handler, session, envelope = _handler(), _session(), _envelope()
    setattr(session, _SOFT_ERRORS_ATTR, 2)
    reply = await handler.handle_MAIL(None, session, envelope, "<a@example.com>", [])
    assert reply.startswith("421")
    assert envelope.mail_from is None


@pytest.mark.asyncio
async def test_hard_error_limit_on_mail_requests_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()
    setattr(server, _SOFT_ERRORS_ATTR, 1)
    reply = await handler.handle_MAIL(server, session, envelope, "<a@example.com>", [])
    assert reply.startswith("421")
    assert server.disconnect_requested is True


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
        self.gate_key = ip
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
    proxy_data = types.SimpleNamespace(src_addr=real_client, src_port=52000, version=2, protocol=1)
    session_pre_tls = types.SimpleNamespace(host_name=None, peer=lb_peer, proxy_data=proxy_data)
    gate = await handler.handle_PROXY(server, session_pre_tls, _envelope(), proxy_data)
    assert gate is True

    # 2. STARTTLS rebuilds the session: proxy_data gone, peer is the LB again.
    #    Same server instance carries over.
    session_post_tls = types.SimpleNamespace(host_name=None, peer=lb_peer, proxy_data=None)

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


@pytest.mark.parametrize("status", [400, 413, 415])
@pytest.mark.asyncio
async def test_message_rejecting_status_still_bounces(status):
    # 400/413/415 keep their permanent reject: retrying sends the same bytes.
    mda = _FakeMDA(
        deliver_result=MDAResult(
            ok=False, temp_fail=False, payload={"detail": "unparseable"}, status_code=status
        )
    )
    reply = await _deliver_with(mda)
    assert reply.startswith("554"), reply


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-body-or-unparseable"),
        pytest.param({"status": None}, id="null-status"),
        pytest.param({"status": "OK"}, id="wrong-case-status"),
        pytest.param({"detail": "accepted"}, id="no-status-key"),
        pytest.param({"status": "partial_success"}, id="partial-on-a-200"),
    ],
)
@pytest.mark.asyncio
async def test_deliver_200_without_an_unambiguous_ok_defers(payload):
    # 200 is not proof of delivery; only the exact ``{"status": "ok"}`` is.
    # Every other body keeps the message alive for a retry.
    mda = _FakeMDA(
        deliver_result=MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)
    )
    reply = await _deliver_with(mda)
    assert reply.startswith("451"), reply


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(0, id="timeout-transport-or-breaker"),
        pytest.param(207, id="multi-status"),
        pytest.param(401, id="jwt-rejected"),
        pytest.param(403, id="forbidden"),
        pytest.param(404, id="route-missing"),
        pytest.param(429, id="throttled"),
        pytest.param(418, id="unrecognised-4xx"),
        pytest.param(500, id="mda-error"),
        pytest.param(503, id="mda-unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_deliver_defers_on_every_status_outside_the_permanent_set(status):
    mda = _FakeMDA(
        deliver_result=MDAResult(ok=False, temp_fail=True, payload={}, status_code=status)
    )
    reply = await _deliver_with(mda)
    assert reply.startswith("451"), reply


# ---------------------------------------------------------------------------
# A 421 must actually hang up.
#
# aiosmtpd pushes whatever a hook returns and loops back for the next command,
# so "goodbye" is only a promise until the handler asks for the disconnect.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A refusal has to leave a trace.
#
# The metrics count rejections without naming one, so without these lines the
# most common failure of all — an unknown recipient — is unanswerable from the
# log: no sender, no recipient, no IP.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_recipient_logs_one_info_line(caplog):
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()

    with caplog.at_level(logging.INFO, logger="pymta.handler"):
        reply = await handler.handle_RCPT(server, session, envelope, "<miss@example.com>", [])

    assert reply.startswith("550")
    rejects = [r for r in caplog.records if r.getMessage() == "reject"]
    assert len(rejects) == 1, caplog.records
    assert rejects[0].levelno == logging.INFO
    assert rejects[0].verb == "rcpt"
    assert rejects[0].reason == "unknown_recipient"
    assert rejects[0].recipient == "miss@example.com"
    assert rejects[0].client_ip == "203.0.113.5"


@pytest.mark.asyncio
async def test_malformed_recipient_logs_the_address_it_refused(caplog):
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()

    with caplog.at_level(logging.INFO, logger="pymta.handler"):
        reply = await handler.handle_RCPT(server, session, envelope, "<a b@example.com>", [])

    assert reply.startswith("5")
    rejects = [r for r in caplog.records if r.getMessage() == "reject"]
    assert len(rejects) == 1
    # The raw value, so a malformed address can be recognised in the log.
    assert "a b@example.com" in rejects[0].recipient


@pytest.mark.asyncio
async def test_a_refusal_is_logged_once_not_once_per_gate(monkeypatch, caplog):
    """The miss cutoff replaces the unknown-recipient line, it does not add to it."""
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 1)
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()

    with caplog.at_level(logging.INFO, logger="pymta.handler"):
        reply = await handler.handle_RCPT(server, session, envelope, "<miss@example.com>", [])

    assert reply.startswith("421")
    rejects = [r for r in caplog.records if r.getMessage() == "reject"]
    assert len(rejects) == 1, [r.reason for r in rejects]
    assert rejects[0].reason == "max_rcpt_misses_per_session"


@pytest.mark.asyncio
async def test_rcpt_miss_cutoff_requests_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 1)
    server = _FakeServer()
    handler, session, envelope = _handler(), _session(), _envelope()

    reply = await handler.handle_RCPT(server, session, envelope, "<miss@example.com>", [])
    assert reply.startswith("421")
    assert server.disconnect_requested is True


@pytest.mark.asyncio
async def test_hard_error_cutoff_requests_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    server = _FakeServer()
    setattr(server, _SOFT_ERRORS_ATTR, 2)
    handler, session, envelope = _handler(), _session(), _envelope()

    reply = await handler.handle_RCPT(server, session, envelope, "<anyone@example.com>", [])
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
    reply = await handler.handle_RCPT(server, _session(), envelope, "<miss3@example.com>", [])
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
    return types.SimpleNamespace(src_addr=ip_address(src), src_port=52000, version=2, protocol=1)


@pytest.mark.asyncio
async def test_proxy_header_from_untrusted_peer_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")])
    session = types.SimpleNamespace(host_name=None, peer=("198.51.100.7", 5555), proxy_data=None)
    accepted = await _handler().handle_PROXY(_FakeServer(), session, _envelope(), _proxy_data())
    assert accepted is False


@pytest.mark.asyncio
async def test_proxy_header_from_trusted_peer_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")])
    server = _FakeServer()
    session = types.SimpleNamespace(host_name=None, peer=("10.89.0.2", 5555), proxy_data=None)
    accepted = await _handler().handle_PROXY(server, session, _envelope(), _proxy_data())
    assert accepted is True
    assert server.gate_key == "203.0.113.9"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claimed",
    [
        b"/var/run/haproxy-\x00\x00\x00",  # PROXY v2 AF_UNIX: raw off the wire
        "not-an-address",
        "203.0.113.9 extra",
        "",
    ],
)
async def test_an_unparseable_proxy_source_does_not_become_a_gate_key(monkeypatch, claimed):
    """A claim that is not an address shares the "unknown" bucket.

    aiosmtpd validates src_addr for the INET families but assigns AF_UNIX's
    straight from the wire, so a PROXY v2 UNIX header carries an arbitrary
    108-byte string. Used as the per-source key it would be a fresh bucket per
    connection: the share stops applying and the dict grows without bound.
    """
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")])
    server = _FakeServer()
    session = types.SimpleNamespace(host_name=None, peer=("10.89.0.2", 5555), proxy_data=None)
    proxy_data = types.SimpleNamespace(src_addr=claimed, src_port=52000, version=2, protocol=1)

    accepted = await _handler().handle_PROXY(server, session, _envelope(), proxy_data)

    assert accepted is True, "an odd header is not grounds to drop the connection"
    assert server.gate_key == "unknown"
    # aiosmtpd hangs the parsed header off the session; without this the
    # assertions below take the empty-session path and never reach _peer_ip's.
    session.proxy_data = proxy_data
    # And it must not be recorded as the client address either: it would reach
    # the MDA and the Received header as a plausible-looking origin.
    assert _peer_ip(session, server) is None
    # Nor the port that rode in on the same rejected header.
    assert _peer_port(session, server) is None


@pytest.mark.parametrize("allowlist", [[], [ip_network("0.0.0.0/0")]])
@pytest.mark.asyncio
async def test_empty_allowlist_trusts_every_peer(monkeypatch, allowlist):
    # "No upstream named" means there is nothing left to filter on, so the
    # header is taken from anyone -- the same posture 0.0.0.0/0 spells out.
    # server.py warns loudly at startup; the isolation is the trust boundary.
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", allowlist)
    server = _FakeServer()
    session = types.SimpleNamespace(host_name=None, peer=("198.51.100.7", 5555), proxy_data=None)
    accepted = await _handler().handle_PROXY(server, session, _envelope(), _proxy_data())
    assert accepted is True
    # And the claimed source is what the caps and Received are keyed on.
    assert getattr(server, _PROXY_SRC_ATTR) == ("203.0.113.9", 52000)


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
