"""What the server does with MAIL, RCPT, DATA and the PROXY header.

Every test talks to an in-process listener over a real socket and looks only
at what can be seen from outside: replies, what the MDA stand-in was asked,
log records, counters, and whether the connection was closed. A per-session
budget is seen through its effect: set it low and the session is cut.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
from ipaddress import ip_network

import pytest

from pymta import settings
from pymta.handler import _REPLY_RESERVE_SECONDS, _normalize_line_endings
from pymta.limits import IPGate
from pymta.logfmt import LogfmtFormatter
from pymta.mda_async import MDAResult

from tests.harness import (
    Client,
    ScriptedMDA,
    capture_all,
    connected,
    counter,
    listening,
    records,
    unlimited_gate,
)

_BODY = b"Subject: hi\r\n\r\nbody\r\n"

_TOO_MANY_ERRORS = b"421 4.7.0 Too many errors, goodbye"

# PROXY protocol headers. The wire peer in these tests is always 127.0.0.1.
_PROXY_V1 = b"PROXY TCP4 203.0.113.9 192.0.2.1 52000 25\r\n"
_PROXY_V2 = b"\r\n\r\n\x00\r\nQUIT\n"
# A LOCAL command: a health check, with no source at all.
_PROXY_V2_LOCAL = _PROXY_V2 + b"\x20\x00\x00\x00"
_LOOPBACK = ip_network("127.0.0.0/8")


def _proxy_v2_unix(path: bytes) -> bytes:
    """A v2 header for an AF_UNIX stream: the source is 108 raw bytes."""
    return _PROXY_V2 + b"\x21\x31\x00\xd8" + path.ljust(108, b"\x00") + bytes(108)


def _proxy_on(monkeypatch, trusted) -> None:
    """Turn PROXY protocol on. Call before the listener is built."""
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_PROXY_PROTOCOL_TIMEOUT", 1)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", trusted)


def _tls_contexts():
    """Server and client contexts for the dev image's self-signed pair."""
    cert = os.environ.get("PYMTA_TLS_CERT_FILE", "")
    key = os.environ.get("PYMTA_TLS_KEY_FILE", "")
    if not (os.path.exists(cert) and os.path.exists(key)):
        pytest.skip("the dev image's self-signed certificate is not available")
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    return server, client


async def _mail(client, sender=b"<sender@example.com>", helo=b"EHLO mx.sender.example") -> bytes:
    """Greet and open a transaction. Returns the reply to MAIL."""
    await client.cmd(helo)
    return await client.cmd(b"MAIL FROM:" + sender)


async def _message(
    client, sender=b"<sender@example.com>", rcpts=(b"<a@example.com>",), body=_BODY
) -> bytes:
    """One whole transaction, after the greeting. Returns the last reply."""
    await client.cmd(b"MAIL FROM:" + sender)
    for rcpt in rcpts:
        await client.cmd(b"RCPT TO:" + rcpt)
    return await client.data(body)


async def _deliver_with(answer) -> bytes:
    """Send one message to an MDA that answers ``answer``. Returns the reply."""
    async with listening(ScriptedMDA(delivers=[answer])) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        return await _message(client)


async def _cut_for_errors(client) -> bool:
    """True when the next command gets the 421 for errors and a hang-up."""
    reply = await client.cmd(b"NOOP")
    return reply.startswith(_TOO_MANY_ERRORS) and await client.closed()


async def _no_recipient_accepted(client) -> bool:
    """True when DATA is refused: the transaction has no recipient."""
    return not (await client.cmd(b"DATA")).startswith(b"354")


async def _no_transaction_open(client) -> bool:
    """True when RCPT is refused: no MAIL was accepted."""
    return not (await client.cmd(b"RCPT TO:<a@example.com>")).startswith(b"250")


def _info(caplog):
    return caplog.at_level(logging.INFO, logger="pymta")


# ---------------------------------------------------------------------------
# A refused MAIL SIZE= is charged to the error budget, on both branches.
#
# A repeated SIZE is refused whatever its values: which one counts would be
# ours to guess.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters", [b"SIZE=not-a-number", b"SIZE=not-a-number SIZE=1", b"SIZE=1 SIZE=2"]
)
async def test_mail_size_bad_value_is_charged_to_the_error_budget(monkeypatch, parameters):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    async with listening() as port, connected(port) as client:
        reply = await _mail(client, b"<a@example.com> " + parameters)
        assert reply.startswith(b"501"), reply
        assert await _cut_for_errors(client)


@pytest.mark.asyncio
async def test_mail_size_oversize_is_charged_to_the_error_budget(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    too_big = settings.PYMTA_MAX_INCOMING_EMAIL_SIZE + 1
    async with listening() as port, connected(port) as client:
        reply = await _mail(client, b"<a@example.com> SIZE=%d" % too_big)
        assert reply == b"552 5.3.4 Message size exceeds fixed maximum\r\n", reply
        assert await _cut_for_errors(client)


# ---------------------------------------------------------------------------
# DATA refusals are charged to the error budget.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_nul_byte_is_charged_to_the_error_budget(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client, body=b"Subject: x\r\n\r\nhello\x00world\r\n")
        assert reply.startswith(b"554 5.6.0 NUL byte"), reply
        assert await _cut_for_errors(client)
    assert mda.delivered == []


@pytest.mark.asyncio
async def test_data_oversize_is_charged_to_the_error_budget(monkeypatch):
    """The cap applies to the bytes sent on to the MDA.

    Each bare LF gains a CR, so this body is under the cap on the wire and
    over it once normalised.
    """
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 100)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client, body=b"x\n" * 45 + b"\r\n")
        assert reply.startswith(b"552 5.3.4"), reply
        assert await _cut_for_errors(client)
    assert mda.delivered == []


@pytest.mark.asyncio
async def test_max_envelopes_closes_the_session(monkeypatch):
    """421 rather than 451: the budget is spent and cannot be won back.

    Every further MAIL on this connection would fail the same way, so holding
    the channel open only invites attempts that must all be refused. 421
    means "closing transmission channel", and the disconnect follows. At
    MAIL, before the message over the budget is uploaded.
    """
    monkeypatch.setattr(settings, "PYMTA_MAX_ENVELOPES_PER_SESSION", 1)
    mda = ScriptedMDA()
    before = counter("pymta_disconnects_421_total", reason="max_envelopes_per_session")
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        assert (await _message(client)).startswith(b"250")
        reply = await client.cmd(b"MAIL FROM:<sender@example.com>")
        assert reply.startswith(b"421 4.7.0 Too many messages"), reply
        assert await client.closed()
    assert len(mda.delivered) == 1
    after = counter("pymta_disconnects_421_total", reason="max_envelopes_per_session")
    assert after == before + 1


# ---------------------------------------------------------------------------
# Unknown recipients have their own budget and cutoff (S3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_counter_triggers_421_at_limit(monkeypatch, caplog):
    # Tight limit so we don't have to do many round-trips.
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 3)
    mda = ScriptedMDA(checks=["no", "no", "no"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client)
            # First two misses get the normal 550.
            for i in range(2):
                reply = await client.cmd(b"RCPT TO:<miss%d@example.com>" % i)
                assert reply.startswith(b"550 5.1.1 No such recipient"), reply
            # Third miss hits the per-session cap and forces 421.
            reply = await client.cmd(b"RCPT TO:<miss3@example.com>")
            assert reply.startswith(b"421 4.7.0 Too many unknown recipients"), reply
            assert await client.closed()
    assert [r.misses for r in await records(caplog, "reject", count=3)] == [1, 2, 3]


# ---------------------------------------------------------------------------
# A 200 that does not name the address is not a verdict.
#
# The MDA client collapses an absent, unparseable, or non-object body to {},
# so the server cannot tell those apart from "the MDA said this mailbox is
# missing" unless it insists on the key. Reading a bodyless 200 (an ingress
# error page, a response shape that drifted) as a miss would answer 550 and
# have the sending MTA bounce mail to a working address.
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


async def _defers_without_spending_a_miss(monkeypatch, result) -> None:
    """RCPT answered ``result`` gets a 451 and is not counted as a miss.

    With a budget of two misses, a miss that follows gets the plain 550. It
    would get the 421 if the deferral had been counted.
    """
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 2)
    mda = ScriptedMDA(checks=[result, "no", "yes"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<rcpt@example.com>")
        assert reply.startswith(b"451 4.3.0 Recipient verification"), reply
        # Not a miss: an unusable answer must not spend the unknown-recipient
        # budget, or an MDA hiccup would hang up on a sender addressing real
        # mailboxes.
        reply = await client.cmd(b"RCPT TO:<miss@example.com>")
        assert reply.startswith(b"550"), reply
        # And the deferred address is not a recipient of the message.
        await client.cmd(b"RCPT TO:<good@example.com>")
        assert (await client.data(_BODY)).startswith(b"250")
    assert mda.delivered[0]["original_recipients"] == ["good@example.com"]


@pytest.mark.parametrize("payload", UNUSABLE_CHECK_BODIES)
@pytest.mark.asyncio
async def test_rcpt_defers_when_a_200_carries_no_verdict(monkeypatch, payload):
    await _defers_without_spending_a_miss(
        monkeypatch, MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)
    )


@pytest.mark.asyncio
async def test_a_200_without_a_verdict_is_logged_as_a_warning(caplog):
    """It reports the MDA breaking its contract, and is the refusal's one line."""
    mda = ScriptedMDA(checks=["no_verdict"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            await client.cmd(b"RCPT TO:<rcpt@example.com>")
    (warning,) = await records(caplog, "mda_check_no_verdict")
    assert warning.levelno == logging.WARNING
    assert (warning.sender, warning.recipient) == ("alice@sender.example", "rcpt@example.com")
    assert not [r for r in caplog.records if r.getMessage() == "reject"]


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
async def test_rcpt_defers_on_every_unsuccessful_check(monkeypatch, result):
    # No check failure is ever a verdict on the mailbox: 400/413/415 included,
    # since there they describe the request we built, not the address.
    await _defers_without_spending_a_miss(monkeypatch, result)


@pytest.mark.asyncio
async def test_a_check_refused_for_good_is_a_permanent_refusal():
    """The landing spot for a permanent check failure, should the MDA name one."""
    mda = ScriptedMDA(
        checks=[MDAResult(ok=False, temp_fail=False, payload={}, status_code=400), "yes"]
    )
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<rcpt@example.com>")
        assert reply.startswith(b"550 5.1.1 Recipient verification failed"), reply
        await client.cmd(b"RCPT TO:<good@example.com>")
        await client.data(_BODY)
    assert mda.delivered[0]["original_recipients"] == ["good@example.com"]


@pytest.mark.asyncio
async def test_rcpt_explicit_false_is_still_a_permanent_miss(monkeypatch):
    # The guard above must not soften a real answer: an explicit False is the
    # MDA saying the mailbox does not exist, and that stays a 550. It spends
    # the budget: the second one is the last of a budget of two.
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 2)
    mda = ScriptedMDA(checks=["no", "no"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<rcpt@example.com>")
        assert reply.startswith(b"550"), reply
        reply = await client.cmd(b"RCPT TO:<rcpt2@example.com>")
        assert reply.startswith(b"421"), reply


@pytest.mark.asyncio
async def test_rcpt_existence_does_not_spend_the_miss_budget(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 2)
    mda = ScriptedMDA(checks=["yes", "no"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<hit@example.com>")
        assert reply.startswith(b"250"), reply
        # The first miss of two, so not yet the 421.
        reply = await client.cmd(b"RCPT TO:<miss@example.com>")
        assert reply.startswith(b"550"), reply


# ---------------------------------------------------------------------------
# The DATA budget is one deadline shared by receive and deliver.
#
# The reply reserve is held back from it, so a budget of the reserve plus one
# second leaves the message one second to arrive and be delivered.
# ---------------------------------------------------------------------------


async def _slow_data(client, pause: float, during=None) -> bytes:
    """DATA with ``pause`` seconds before the final dot. Returns the reply."""
    assert (await client.cmd(b"DATA")).startswith(b"354")
    await client.send(_BODY)
    if during is not None:
        during()
    await asyncio.sleep(pause)
    return await client.cmd(b".")


@pytest.mark.asyncio
async def test_data_budget_subtracts_time_already_spent(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", _REPLY_RESERVE_SECONDS + 1)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        # Sent at once, the message fits in the budget.
        assert (await _message(client)).startswith(b"250")
        # A slow receive that ate the whole budget must not still be granted
        # a deliver call: that would overrun the transport deadline the
        # reserve is there to stay clear of, costing the peer its 451.
        await client.cmd(b"MAIL FROM:<sender@example.com>")
        await client.cmd(b"RCPT TO:<a@example.com>")
        reply = await _slow_data(client, 1.1)
        assert reply.startswith(b"451 4.3.0 Delivery timed out"), reply
    assert len(mda.delivered) == 1


@pytest.mark.asyncio
async def test_data_budget_uses_the_value_the_transport_was_armed_at(monkeypatch):
    """PYMTA_DATA_TIMEOUT is read at each use, so it can move mid-phase.

    The transport is armed once, at DATA. Re-reading the setting for the
    deliver call would size it against a deadline nobody set: raise it
    mid-phase and the deliver call is allowed longer than the transport will
    wait, so the transport tears the session down first and the peer gets a
    bare 421 instead of the 451 the reply reserve exists to buy.
    """
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", _REPLY_RESERVE_SECONDS + 1)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        await client.cmd(b"RCPT TO:<a@example.com>")
        reply = await _slow_data(
            client, 1.1, during=lambda: monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", 600)
        )
        assert reply.startswith(b"451 4.3.0 Delivery timed out"), reply
    assert mda.delivered == []


@pytest.mark.asyncio
async def test_data_replies_451_when_the_budget_is_already_spent(monkeypatch, caplog):
    """The MDA is not called at all, and the refusal has its own reason.

    Without its own reason this is indistinguishable from an MDA defer, and
    the two call for opposite responses: raise the budget versus go fix the
    MDA.
    """
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", _REPLY_RESERVE_SECONDS)
    mda = ScriptedMDA()
    before = counter("pymta_security_rejections_total", reason="data_timeout")
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            reply = await _message(client)
    assert reply.startswith(b"451 4.3.0 Delivery timed out"), reply
    assert mda.delivered == []
    (reject,) = await records(caplog, "reject")
    assert (reject.verb, reject.reason, reject.code) == ("data", "data_timeout", 451)
    assert counter("pymta_security_rejections_total", reason="data_timeout") == before + 1


@pytest.mark.asyncio
async def test_a_deliver_call_that_outlives_the_budget_gets_451(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", _REPLY_RESERVE_SECONDS + 0.5)

    async def never_answers(**_kwargs):
        await asyncio.sleep(30)

    mda = ScriptedMDA(delivers=[never_answers])
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client)
        assert reply.startswith(b"451 4.3.0 Delivery timed out"), reply
        # The session goes on.
        assert (await client.cmd(b"NOOP")).startswith(b"250")
    assert len(mda.delivered) == 1


# ---------------------------------------------------------------------------
# The error budget cuts the session, whatever the verb.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_error_limit_blocks_further_rcpts(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    mda = ScriptedMDA(checks=["no", "no"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        await client.cmd(b"RCPT TO:<miss0@example.com>")
        await client.cmd(b"RCPT TO:<miss1@example.com>")
        reply = await client.cmd(b"RCPT TO:<anyone@example.com>")
        assert reply.startswith(_TOO_MANY_ERRORS), reply
    # The MDA is not asked about a recipient named past the budget.
    assert mda.checked == ["miss0@example.com", "miss1@example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [b"<not an address>", b"<alice@sender%example>"])
async def test_mail_malformed_sender_is_charged_to_the_error_budget(monkeypatch, sender):
    # Without this the hard-error budget is sidestepped by burning errors on
    # MAIL FROM alone, which never counted towards it.
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    async with listening() as port, connected(port) as client:
        reply = await _mail(client, sender)
        assert reply.startswith(b"501"), reply
        assert await _cut_for_errors(client)


@pytest.mark.asyncio
async def test_hard_error_limit_blocks_further_mails(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    mda = ScriptedMDA()
    rejections = counter("pymta_security_rejections_total", reason="max_errors_per_session")
    disconnects = counter("pymta_disconnects_421_total", reason="max_errors_per_session")
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<alice@sender%example>")
        await client.cmd(b"MAIL FROM:<alice@sender%example>")
        reply = await client.cmd(b"MAIL FROM:<a@example.com>")
        assert reply.startswith(_TOO_MANY_ERRORS), reply
    assert (
        counter("pymta_security_rejections_total", reason="max_errors_per_session")
        == rejections + 1
    )
    assert (
        counter("pymta_disconnects_421_total", reason="max_errors_per_session") == disconnects + 1
    )


@pytest.mark.asyncio
async def test_hard_error_limit_on_mail_closes_the_connection(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 1)
    async with listening() as port, connected(port) as client:
        await _mail(client, b"<alice@sender%example>")
        reply = await client.cmd(b"MAIL FROM:<a@example.com>")
        assert reply.startswith(b"421"), reply
        assert await client.closed()


# ---------------------------------------------------------------------------
# The 64-octet local-part cap gates RCPT only: forwarded mail carries an
# SRS-wrapped return path whose local-part routinely exceeds it.
# ---------------------------------------------------------------------------

_SRS_SENDER = "SRS0=Ab3d=TT=bounces.mailer.example=" + "b" * 60 + "@forwarder.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender",
    [
        pytest.param(_SRS_SENDER, id="local-part-over-64-octets"),
        pytest.param("a" * 300 + "@example.com", id="sender-over-256-octets"),
    ],
)
async def test_mail_accepts_a_long_sender(sender):
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        reply = await _mail(client, f"<{sender}>".encode())
        assert reply.startswith(b"250"), reply
        await client.cmd(b"RCPT TO:<a@example.com>")
        await client.data(_BODY)
    assert mda.delivered[0]["sender"] == sender


@pytest.mark.asyncio
async def test_rcpt_still_rejects_local_part_over_64_octets():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<" + b"a" * 65 + b"@example.com>")
        assert reply.startswith(b"553"), reply
        assert await _no_recipient_accepted(client)
    assert mda.checked == []


# ---------------------------------------------------------------------------
# The null sender reaches the MDA as the empty string.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_sender_round_trip(caplog):
    mda = ScriptedMDA()
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            reply = await _mail(client, b"<>")
            assert reply.startswith(b"250"), reply
            # A bounce still has recipients: RCPT is not refused for it.
            assert (await client.cmd(b"RCPT TO:<a@example.com>")).startswith(b"250")
            assert (await client.data(_BODY)).startswith(b"250")
    assert mda.delivered[0]["sender"] == ""
    (delivered,) = await records(caplog, "message_delivered")
    assert delivered.sender == "<>"


# ---------------------------------------------------------------------------
# PROXY-protocol source survives STARTTLS.
#
# The TLS handshake starts the session over, and the wire peer is the load
# balancer. The real client IP must still reach the MDA on the post-TLS DATA
# command. Regression guard for the "client_ip == LB internal IP" bug seen in
# production behind HAProxy.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_source_survives_starttls_and_reaches_mda(monkeypatch):
    # Mirror a real deployment: PROXY protocol on, balancer allowlisted.
    _proxy_on(monkeypatch, [_LOOPBACK])
    server_ctx, client_ctx = _tls_contexts()
    mda = ScriptedMDA()
    async with (
        listening(mda, gate=unlimited_gate(), tls_context=server_ctx) as port,
        connected(port, preface=_PROXY_V1) as client,
    ):
        await client.cmd(b"EHLO mx.sender.example")
        assert (await client.starttls(client_ctx)).startswith(b"220")
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client)

    assert reply.startswith(b"250"), reply
    (delivery,) = mda.delivered
    assert delivery["client_address"] == "203.0.113.9"
    assert delivery["client_port"] == "52000"
    assert delivery["transport"]["protocol"] == "ESMTPS"


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
    mda = ScriptedMDA(
        delivers=[
            MDAResult(
                ok=False,
                temp_fail=True,
                payload={"status": "partial_success", "delivered": 1, "failed": 1},
                status_code=207,
            )
        ]
    )
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client, rcpts=(b"<a@example.com>", b"<b@example.com>"))
    assert reply.startswith(b"451 4.3.0 Delivery temporarily unavailable"), reply
    assert mda.delivered[0]["original_recipients"] == ["a@example.com", "b@example.com"]


@pytest.mark.asyncio
async def test_http_200_without_status_ok_defers():
    # A 200 whose body we don't recognise is not proof of delivery; deferring
    # keeps the message alive while someone works out what the MDA meant.
    reply = await _deliver_with(
        MDAResult(ok=True, temp_fail=False, payload={"status": "queued"}, status_code=200)
    )
    assert reply.startswith(b"451"), reply


@pytest.mark.parametrize("status", [400, 413])
@pytest.mark.asyncio
async def test_message_rejecting_status_still_bounces(status):
    # 400 and 413 keep their permanent reject: retrying sends the same bytes.
    reply = await _deliver_with(
        MDAResult(ok=False, temp_fail=False, payload={"detail": "unparseable"}, status_code=status)
    )
    assert reply.startswith(b"554 5.6.0 Message rejected by delivery agent"), reply


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
    reply = await _deliver_with(
        MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)
    )
    assert reply.startswith(b"451"), reply


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
    reply = await _deliver_with(
        MDAResult(ok=False, temp_fail=True, payload={}, status_code=status)
    )
    assert reply.startswith(b"451"), reply


@pytest.mark.asyncio
async def test_a_delivered_message_is_accepted_and_the_session_goes_on():
    mda = ScriptedMDA()
    before = counter("pymta_messages_total", result="delivered")
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client)
        assert reply.startswith(b"250 2.0.0 Message accepted for delivery"), reply
        assert (await _message(client)).startswith(b"250")
    assert [d["message"] for d in mda.delivered] == [_BODY, _BODY]
    assert counter("pymta_messages_total", result="delivered") == before + 2


# ---------------------------------------------------------------------------
# A refusal has to leave a trace.
#
# The metrics count rejections without naming one, so without these lines the
# most common failure of all, an unknown recipient, is unanswerable from the
# log: no sender, no recipient, no IP.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_recipient_logs_one_info_line(caplog):
    mda = ScriptedMDA(checks=["no"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client)
            reply = await client.cmd(b"RCPT TO:<miss@example.com>")

    assert reply.startswith(b"550")
    rejects = await records(caplog, "reject")
    assert len(rejects) == 1, caplog.records
    assert rejects[0].levelno == logging.INFO
    assert rejects[0].verb == "rcpt"
    assert rejects[0].reason == "unknown_recipient"
    assert rejects[0].recipient == "miss@example.com"
    assert rejects[0].client_ip == "127.0.0.1"


@pytest.mark.asyncio
async def test_a_recipient_refusal_names_the_sender(caplog):
    """Searching the log for the sending domain has to find its refusals."""
    mda = ScriptedMDA(checks=["no"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            await client.cmd(b"RCPT TO:<miss@example.com>")

    (reject,) = await records(caplog, "reject")
    assert reject.sender == "alice@sender.example"


@pytest.mark.asyncio
async def test_a_bounce_refusal_names_the_null_sender(caplog):
    mda = ScriptedMDA(checks=["no"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<>")
            await client.cmd(b"RCPT TO:<miss@example.com>")

    (reject,) = await records(caplog, "reject")
    assert reject.sender == "<>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "reply_start"),
    [
        ("max_errors_per_session", _TOO_MANY_ERRORS),
        ("defer_all", b"451 4.3.2 Service temporarily unavailable"),
        ("blocked_recipient", b"550 5.1.1 Recipient not accepted"),
        ("mda_temp_fail", b"451 4.3.0 Recipient verification temporarily unavailable"),
    ],
)
async def test_every_recipient_refusal_names_the_sender(monkeypatch, caplog, reason, reply_start):
    mda = ScriptedMDA()
    spent_first = 0
    if reason == "max_errors_per_session":
        monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
        mda, spent_first = ScriptedMDA(checks=["temp", "temp"]), 2
    elif reason == "defer_all":
        monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    elif reason == "blocked_recipient":
        monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"miss@example.com"}))
    else:
        mda = ScriptedMDA(checks=["temp"])

    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            for i in range(spent_first):
                await client.cmd(b"RCPT TO:<other%d@example.com>" % i)
            reply = await client.cmd(b"RCPT TO:<miss@example.com>")

    assert reply.startswith(reply_start), reply
    rejects = await records(caplog, "reject", count=spent_first + 1)
    assert len(rejects) == spent_first + 1
    assert rejects[-1].reason == reason
    assert rejects[-1].code == int(reply_start[:3])
    assert rejects[-1].sender == "alice@sender.example"
    if reason in ("defer_all", "blocked_recipient"):
        # Refused without a question to the MDA.
        assert mda.checked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "parameters"),
    [
        ("bad_size_parameter", b" SIZE=not-a-number"),
        ("oversize_announced", b" SIZE=%d" % 10**12),
        ("bad_syntax", b" SIZE=1 SIZE=2"),
        ("max_errors_per_session", b""),
    ],
)
async def test_every_sender_refusal_names_the_sender(monkeypatch, caplog, reason, parameters):
    """An announced size over the cap is the usual way a large mail is lost."""
    spent_first = 0
    if reason == "max_errors_per_session":
        monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
        spent_first = 2

    with _info(caplog):
        async with listening() as port, connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            for _ in range(spent_first):
                await client.cmd(b"MAIL FROM:<other@sender%example>")
            reply = await client.cmd(b"MAIL FROM:<alice@sender.example>" + parameters)

    assert reply[:1] in (b"4", b"5")
    # One line for the refusal, not one from each layer that saw it.
    rejects = await records(caplog, "reject", count=spent_first + 1)
    assert len(rejects) == spent_first + 1
    if reason is not None:
        assert rejects[-1].reason == reason
    assert rejects[-1].verb == "mail"
    assert rejects[-1].code == int(reply[:3])
    assert "alice@sender.example" in rejects[-1].sender


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [b"SIZE=%d"])
async def test_an_oversize_bounce_is_logged_as_a_null_sender(caplog, parameters):
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            reply = await _mail(client, b"<> " + parameters % 10**12)

    assert reply.startswith(b"552"), reply
    (reject,) = await records(caplog, "reject")
    assert reject.sender == "<>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "reply_start"),
    [
        ("max_errors_per_session", _TOO_MANY_ERRORS),
        ("defer_all", b"451"),
        ("max_recipients_per_envelope", b"452 4.5.3 Too many recipients"),
    ],
)
async def test_recipient_refusals_before_validation_name_the_recipient(
    monkeypatch, caplog, reason, reply_start
):
    mda = ScriptedMDA()
    before = []
    if reason == "max_errors_per_session":
        monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
        mda = ScriptedMDA(checks=["temp", "temp"])
        before = [b"<other0@example.com>", b"<other1@example.com>"]
    elif reason == "defer_all":
        monkeypatch.setattr(settings, "PYMTA_DEFER_ALL", True)
    else:
        monkeypatch.setattr(settings, "PYMTA_MAX_RECIPIENTS_PER_ENVELOPE", 1)
        before = [b"<first@example.com>"]

    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            for rcpt in before:
                await client.cmd(b"RCPT TO:" + rcpt)
            reply = await client.cmd(b"RCPT TO:<bob@example.com>")

    assert reply.startswith(reply_start), reply
    reject = (await records(caplog, "reject"))[-1]
    assert reject.reason == reason
    if reason == "max_errors_per_session":
        # Refused before the command is read as a RCPT: the line carries the
        # argument as sent.
        assert "bob@example.com" in reject.arg
    else:
        assert reject.recipient == "bob@example.com"
    if reason == "max_recipients_per_envelope":
        assert reject.recipient_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["nul_byte", "oversize_message"])
async def test_every_data_refusal_names_sender_and_recipients(monkeypatch, caplog, reason):
    body = b"Subject: x\r\n\r\nbody\r\n"
    if reason == "nul_byte":
        body += b"\x00\r\n"
    elif reason == "oversize_message":
        # Under the cap on the wire, over it once each bare LF has its CR.
        monkeypatch.setattr(settings, "PYMTA_MAX_INCOMING_EMAIL_SIZE", 100)
        body = b"x\n" * 45 + b"\r\n"

    with _info(caplog):
        async with listening() as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            await client.cmd(b"RCPT TO:<bob@example.com>")
            reply = await client.data(body)

    assert reply[:1] in (b"4", b"5"), reply
    (reject,) = await records(caplog, "reject")
    assert (reject.verb, reject.reason) == ("data", reason)
    assert reject.sender == "alice@sender.example"
    assert reject.recipients == "bob@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "event", "status"),
    [("temp", "message_deferred", 503), ("perm", "message_rejected", 400)],
)
async def test_a_message_outcome_claims_its_refusal(caplog, answer, event, status):
    """The outcome line is the refusal's line: no ``reject`` next to it."""
    with _info(caplog):
        async with listening(ScriptedMDA(delivers=[answer])) as port, connected(port) as client:
            await _mail(client, b"<alice@sender.example>")
            await client.cmd(b"RCPT TO:<bob@example.com>")
            reply = await client.data(_BODY)

    assert reply[:1] in (b"4", b"5"), reply
    (outcome,) = await records(caplog, event)
    assert not [r for r in caplog.records if r.getMessage() == "reject"]
    assert (outcome.sender, outcome.recipients) == ("alice@sender.example", "bob@example.com")
    assert outcome.status == status


@pytest.mark.asyncio
async def test_a_refusal_keeps_the_domain_of_a_long_sender(caplog):
    """MAIL FROM has no length cap, and the domain is what gets searched."""
    sender = "SRS0=Ab3d=TT=bounces.mailer.example=" + "b" * 400 + "@forwarder.example"
    mda = ScriptedMDA(checks=["no"])
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            assert (await _mail(client, f"<{sender}>".encode())).startswith(b"250")
            await client.cmd(b"RCPT TO:<miss@example.com>")

    (reject,) = await records(caplog, "reject")
    assert reject.sender.startswith("SRS0=Ab3d=")
    assert reject.sender.endswith("@forwarder.example")
    assert len(reject.sender) < len(sender)


@pytest.mark.asyncio
async def test_malformed_recipient_logs_the_address_it_refused(caplog):
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            await _mail(client)
            reply = await client.cmd(b"RCPT TO:<a b@example.com>")

    assert reply.startswith(b"5")
    rejects = await records(caplog, "reject")
    assert len(rejects) == 1
    # What was sent, so a malformed address can be recognised in the log.
    assert "a b" in rejects[0].recipient
    assert rejects[0].recipient.endswith("@example.com")


@pytest.mark.asyncio
async def test_a_refusal_is_logged_once_not_once_per_gate(monkeypatch, caplog):
    """The miss cutoff replaces the unknown-recipient line, it does not add to it."""
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 1)
    mda = ScriptedMDA(checks=["no"])
    rejections = counter("pymta_security_rejections_total", reason="max_rcpt_misses_per_session")
    disconnects = counter("pymta_disconnects_421_total", reason="max_rcpt_misses_per_session")
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client)
            reply = await client.cmd(b"RCPT TO:<miss@example.com>")

    assert reply.startswith(b"421")
    rejects = await records(caplog, "reject")
    assert len(rejects) == 1, [r.reason for r in rejects]
    assert rejects[0].reason == "max_rcpt_misses_per_session"
    assert (
        counter("pymta_security_rejections_total", reason="max_rcpt_misses_per_session")
        == rejections + 1
    )
    assert (
        counter("pymta_disconnects_421_total", reason="max_rcpt_misses_per_session")
        == disconnects + 1
    )


# ---------------------------------------------------------------------------
# A 421 must actually hang up: "goodbye" is only a promise until the
# connection is closed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_cutoff_closes_the_connection(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 1)
    mda = ScriptedMDA(checks=["no"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        reply = await client.cmd(b"RCPT TO:<miss@example.com>")
        assert reply.startswith(b"421")
        assert await client.closed()


@pytest.mark.asyncio
async def test_hard_error_cutoff_closes_the_connection(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 2)
    mda = ScriptedMDA(checks=["temp", "temp"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client)
        await client.cmd(b"RCPT TO:<other0@example.com>")
        await client.cmd(b"RCPT TO:<other1@example.com>")
        reply = await client.cmd(b"RCPT TO:<anyone@example.com>")
        assert reply.startswith(b"421")
        assert await client.closed()


# ---------------------------------------------------------------------------
# Abuse counters are keyed to the TCP connection.
#
# STARTTLS starts the session over. Counters that started over with it would
# hand a peer a free budget reset for the price of one STARTTLS.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rcpt_miss_budget_survives_starttls(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 3)
    server_ctx, client_ctx = _tls_contexts()
    mda = ScriptedMDA(checks=["no", "no", "no"])
    with _info(caplog):
        async with (
            listening(mda, tls_context=server_ctx) as port,
            connected(port) as client,
        ):
            await _mail(client)
            # Two misses before STARTTLS.
            for i in range(2):
                reply = await client.cmd(b"RCPT TO:<miss%d@example.com>" % i)
                assert reply.startswith(b"550"), reply

            assert (await client.starttls(client_ctx)).startswith(b"220")
            await _mail(client)
            # The budget must not have been reset.
            reply = await client.cmd(b"RCPT TO:<miss3@example.com>")
            assert reply.startswith(b"421"), reply
    assert (await records(caplog, "reject", count=3))[-1].misses == 3


@pytest.mark.asyncio
async def test_error_budget_survives_starttls(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_MAX_ERRORS_PER_SESSION", 3)
    server_ctx, client_ctx = _tls_contexts()
    async with listening(tls_context=server_ctx) as port, connected(port) as client:
        # Two refusals before STARTTLS, the third after it.
        await _mail(client, b"<alice@sender%example>")
        await client.cmd(b"MAIL FROM:<alice@sender%example>")
        assert (await client.starttls(client_ctx)).startswith(b"220")
        await _mail(client, b"<alice@sender%example>")
        assert await _cut_for_errors(client)


# ---------------------------------------------------------------------------
# PROXY-protocol trust boundary.
#
# A PROXY header is parsed from whoever sends it. The claimed source becomes
# the per-IP rate-limit key AND the client_address the MDA bakes into
# Received, so an unfiltered header is a free pass past both.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_header_from_untrusted_peer_is_refused(monkeypatch, caplog):
    _proxy_on(monkeypatch, [ip_network("10.89.0.0/24")])
    mda = ScriptedMDA()
    rejections = counter("pymta_security_rejections_total", reason="untrusted_proxy")
    connections = counter("pymta_connections_total", result="rejected_untrusted_proxy")
    with capture_all(caplog):
        async with listening(mda, gate=unlimited_gate()) as port:
            client = await Client.connect(port, preface=_PROXY_V1, greeting=False)
            try:
                # No greeting, no reply: the connection is dropped.
                assert await client.reply() == b""
                assert await client.closed()
            finally:
                await client.close()
        (refused,) = await records(caplog, "proxy_header_untrusted")

    # DEBUG: any host that can reach port 25 provokes this once a connection.
    assert refused.levelno == logging.DEBUG
    assert refused.wire_peer == "127.0.0.1"
    assert str(refused.claimed_src) == "203.0.113.9"
    assert counter("pymta_security_rejections_total", reason="untrusted_proxy") == rejections + 1
    assert counter("pymta_connections_total", result="rejected_untrusted_proxy") == connections + 1


@pytest.mark.asyncio
async def test_proxy_header_from_trusted_peer_is_accepted(monkeypatch, caplog):
    _proxy_on(monkeypatch, [_LOOPBACK])
    mda = ScriptedMDA()
    with capture_all(caplog):
        async with (
            listening(mda, gate=unlimited_gate()) as port,
            connected(port, preface=_PROXY_V1) as client,
        ):
            assert client.greeting.startswith(b"220")
            await client.cmd(b"EHLO mx.sender.example")
            assert (await _message(client)).startswith(b"250")
        (closed,) = await records(caplog, "session_closed")

    assert mda.delivered[0]["client_address"] == "203.0.113.9"
    assert mda.delivered[0]["client_port"] == "52000"
    # The forensic record tying the session to the origin behind the balancer.
    (header,) = await records(caplog, "proxy_header")
    assert (str(header.client_ip), header.client_port) == ("203.0.113.9", 52000)
    assert header.wire_peer == "127.0.0.1"
    (delivered,) = await records(caplog, "message_delivered")
    assert delivered.client_ip == "203.0.113.9"
    assert (closed.client_ip, closed.wire_peer) == ("203.0.113.9", "127.0.0.1")


@pytest.mark.asyncio
async def test_the_proxy_source_is_what_the_connection_caps_count_by(monkeypatch):
    """Not the balancer, or every session would share one source's slots.

    With two slots a source may hold one. A second connection from the same
    claimed source is refused, one from another source is let in.
    """
    _proxy_on(monkeypatch, [_LOOPBACK])
    other = b"PROXY TCP4 203.0.113.10 192.0.2.1 52000 25\r\n"
    async with (
        listening(gate=IPGate(max_total=2)) as port,
        connected(port, preface=_PROXY_V1) as first,
    ):
        assert first.greeting.startswith(b"220")
        async with connected(port, preface=_PROXY_V1) as same_source:
            assert same_source.greeting.startswith(b"421 4.7.0 Too many connections")
            assert await same_source.closed()
        async with connected(port, preface=other) as other_source:
            assert other_source.greeting.startswith(b"220")


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [b"/var/run/haproxy-", b"203.0.113.9"])
async def test_an_unparseable_proxy_source_does_not_become_a_gate_key(monkeypatch, claimed):
    """A claim that is not an address shares the "unknown" bucket.

    A PROXY v2 AF_UNIX header carries an arbitrary 108-byte string as its
    source, even one that spells an address. Used as the per-source key it
    would be a fresh bucket per connection: the share stops applying and the
    table of sources grows without bound.
    """
    _proxy_on(monkeypatch, [_LOOPBACK])
    mda = ScriptedMDA()
    async with (
        listening(mda, gate=IPGate(max_total=2)) as port,
        connected(port, preface=_proxy_v2_unix(claimed)) as client,
    ):
        assert client.greeting.startswith(b"220"), (
            "an odd header is not grounds to drop the connection"
        )
        # A different claim, the same bucket: refused as the same source.
        async with connected(port, preface=_proxy_v2_unix(claimed + b"-2")) as second:
            assert second.greeting.startswith(b"421 4.7.0 Too many connections")
        await client.cmd(b"EHLO mx.sender.example")
        assert (await _message(client)).startswith(b"250")

    # And it must not be recorded as the client address either: it would reach
    # the MDA and the Received header as a plausible-looking origin.
    assert mda.delivered[0]["client_address"] is None
    # Nor the port that rode in on the same rejected header.
    assert mda.delivered[0]["client_port"] is None


@pytest.mark.parametrize("allowlist", [[], [ip_network("0.0.0.0/0")]])
@pytest.mark.asyncio
async def test_empty_allowlist_trusts_every_peer(monkeypatch, allowlist):
    # "No upstream named" means there is nothing left to filter on, so the
    # header is taken from anyone -- the same posture 0.0.0.0/0 spells out.
    # server.py warns loudly at startup; the isolation is the trust boundary.
    _proxy_on(monkeypatch, allowlist)
    mda = ScriptedMDA()
    async with (
        listening(mda, gate=unlimited_gate()) as port,
        connected(port, preface=_PROXY_V1) as client,
    ):
        assert client.greeting.startswith(b"220")
        await client.cmd(b"EHLO mx.sender.example")
        await _message(client)
    # And the claimed source is what Received is keyed on.
    assert mda.delivered[0]["client_address"] == "203.0.113.9"
    assert mda.delivered[0]["client_port"] == "52000"


# ---------------------------------------------------------------------------
# With PROXY protocol on, the wire peer is the balancer, never the client.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [_PROXY_V2_LOCAL, b"PROXY UNKNOWN\r\n"])
async def test_no_proxy_source_reports_no_client_address(monkeypatch, caplog, header):
    # A PROXY v2 LOCAL command (health check) carries no source. Falling back
    # to the wire peer would stamp the balancer's own IP into Received as if
    # it were the sender.
    _proxy_on(monkeypatch, [_LOOPBACK])
    mda = ScriptedMDA()
    with _info(caplog):
        async with (
            listening(mda, gate=unlimited_gate()) as port,
            connected(port, preface=header) as client,
        ):
            await client.cmd(b"EHLO client.test")
            reply = await _message(client)

    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["client_address"] is None
    assert mda.delivered[0]["client_port"] is None
    (delivered,) = await records(caplog, "message_delivered")
    assert delivered.client_ip is None


@pytest.mark.asyncio
async def test_wire_peer_is_the_client_when_proxy_protocol_is_off():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        local_port = client.writer.get_extra_info("sockname")[1]
        await client.cmd(b"EHLO mx.sender.example")
        await _message(client)

    assert mda.delivered[0]["client_address"] == "127.0.0.1"
    assert mda.delivered[0]["client_port"] == str(local_port)


# ---------------------------------------------------------------------------
# The EHLO reply offers no AUTH and is well formed.
# ---------------------------------------------------------------------------


def _assert_well_formed_without_auth(reply: bytes) -> None:
    lines = reply.splitlines()
    keywords = [line[4:].split(b" ")[0].upper() for line in lines]
    assert not {b"AUTH", b"CHUNKING", b"BDAT", b"PIPELINING"} & set(keywords), reply
    # A reply left ending on a "250-" continuation hangs clients forever.
    assert lines[-1].startswith(b"250 ")
    assert all(line.startswith(b"250-") for line in lines[:-1])


@pytest.mark.asyncio
async def test_ehlo_offers_no_auth_and_keeps_a_final_line():
    """Inside TLS too, where AUTH would otherwise be offered."""
    server_ctx, client_ctx = _tls_contexts()
    async with listening(tls_context=server_ctx) as port, connected(port) as client:
        _assert_well_formed_without_auth(await client.cmd(b"EHLO client.example.com"))
        assert (await client.starttls(client_ctx)).startswith(b"220")
        _assert_well_formed_without_auth(await client.cmd(b"EHLO client.example.com"))


# ---------------------------------------------------------------------------
# Bare-LF bodies: line endings normalised, and the dot-stuffing left on the
# lines that did not end in CRLF removed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("received", "stored"),
    [
        # Clean CRLF: already unstuffed on receipt, nothing to do.
        (b"one\r\n.two\r\n", b"one\r\n.two\r\n"),
        # After a bare LF the sender's stuffing dot is still there.
        (b"one\n..two\n...\r\n", b"one\r\n.two\r\n..\r\n"),
        (b"one\n.two\r\n", b"one\r\ntwo\r\n"),
        # A lone dot after a bare LF is not the end of DATA: an empty line.
        (b"one\n.\r\nthree\r\n", b"one\r\n\r\nthree\r\n"),
        (b"one\n.\nthree\r\n", b"one\r\n\r\nthree\r\n"),
        # A dot that does not start a line is content.
        (b"one\na.b\n", b"one\r\na.b\r\n"),
        # A CRLF line starting with a dot was unstuffed on receipt.
        (b"one\nx\r\n.two\r\n", b"one\r\nx\r\n.two\r\n"),
        # Bare CR is a line ending we normalise, but nobody stuffs after one.
        (b"one\r.two\r\n", b"one\r\n.two\r\n"),
    ],
)
def test_normalize_line_endings(received, stored):
    content, changed = _normalize_line_endings(received)
    assert content == stored
    assert changed is (received != stored)


@pytest.mark.parametrize(
    ("received", "stored"),
    [
        # One rule everywhere: a bare ending in a header is a line break too,
        # whatever surrounds it. No guess about what the sender meant.
        (
            b"Subject: a\nFrom: ceo@bank.example\r\nTo: x@y\r\n\r\nbody\n",
            b"Subject: a\r\nFrom: ceo@bank.example\r\nTo: x@y\r\n\r\nbody\r\n",
        ),
        (
            b"Subject: a\nFrom: b@c\nTo: x@y\n\nbody\n",
            b"Subject: a\r\nFrom: b@c\r\nTo: x@y\r\n\r\nbody\r\n",
        ),
        (
            b"Received: by r\r\nSubject: a\nFrom: b@c\nTo: x@y\n\nbody\n",
            b"Received: by r\r\nSubject: a\r\nFrom: b@c\r\nTo: x@y\r\n\r\nbody\r\n",
        ),
    ],
)
def test_headers_and_body_get_the_same_line_ending_rule(received, stored):
    assert _normalize_line_endings(received) == (stored, True)


@pytest.mark.asyncio
async def test_a_bare_lf_body_reaches_the_mda_normalised():
    mda = ScriptedMDA()
    before = counter("pymta_bare_newline_messages_total")
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        reply = await _message(client, body=b"Subject: a\nFrom: b@c\n\none\n..two\r\n")
    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["message"] == b"Subject: a\r\nFrom: b@c\r\n\r\none\r\n.two\r\n"
    assert counter("pymta_bare_newline_messages_total") == before + 1


# ---------------------------------------------------------------------------
# RCPT TO:<postmaster>, the bare form RFC 5321 §4.5.1 requires accepting.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [b"<postmaster>", b"postmaster", b"<PostMaster>"])
async def test_bare_postmaster_goes_to_the_configured_mailbox(monkeypatch, address):
    monkeypatch.setattr(settings, "PYMTA_POSTMASTER_ADDRESS", "admin@example.com")
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        reply = await client.cmd(b"RCPT TO:" + address)
        assert reply.startswith(b"250"), reply
        await client.data(_BODY)

    assert mda.checked == ["admin@example.com"]
    assert mda.delivered[0]["original_recipients"] == ["admin@example.com"]


@pytest.mark.asyncio
async def test_bare_postmaster_is_refused_when_no_mailbox_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_POSTMASTER_ADDRESS", "")
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        reply = await client.cmd(b"RCPT TO:<postmaster>")
        assert reply.startswith(b"501"), reply
        assert await _no_recipient_accepted(client)
    assert mda.checked == []


@pytest.mark.asyncio
async def test_the_configured_postmaster_mailbox_is_still_checked_with_the_mda(monkeypatch):
    """A mailbox that does not exist is refused like any other."""
    monkeypatch.setattr(settings, "PYMTA_POSTMASTER_ADDRESS", "admin@example.com")
    mda = ScriptedMDA(checks=["no"])
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        reply = await client.cmd(b"RCPT TO:<postmaster>")
    assert reply.startswith(b"550"), reply


@pytest.mark.asyncio
async def test_postmaster_at_a_domain_is_an_ordinary_address(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_POSTMASTER_ADDRESS", "admin@example.com")
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        await client.cmd(b"RCPT TO:<postmaster@example.net>")
        await client.data(_BODY)

    assert mda.checked == ["postmaster@example.net"]
    assert mda.delivered[0]["original_recipients"] == ["postmaster@example.net"]


# ---------------------------------------------------------------------------
# MAIL FROM address forms.
# ---------------------------------------------------------------------------

_VERP_SENDER = "bounce-first.last%40town.example@esp.example"


@pytest.mark.asyncio
async def test_mail_accepts_a_percent_in_the_local_part():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        reply = await _mail(client, f"<{_VERP_SENDER}>".encode())
        assert reply.startswith(b"250"), reply
        await client.cmd(b"RCPT TO:<a@example.com>")
        await client.data(_BODY)
    assert mda.delivered[0]["sender"] == _VERP_SENDER


@pytest.mark.asyncio
async def test_rcpt_still_refuses_a_percent(caplog):
    mda = ScriptedMDA()
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await _mail(client, b"<sender@example.org>")
            reply = await client.cmd(b"RCPT TO:<user%other.example@example.com>")
            assert reply.startswith(b"501"), reply
            assert await _no_recipient_accepted(client)
    assert mda.checked == []
    (reject,) = [r for r in await records(caplog, "reject") if r.verb == "rcpt"]
    assert reject.recipient == "user%other.example@example.com"


def _domain_of(address: str) -> str:
    """What follows the last ``@`` or quote of a bracketed address."""
    return re.split(r'[@"]', address.rstrip(">"))[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "code", "reason"),
    [
        ('<"al\tice"@sender.example>', "501", "control_char"),
        ('<"al\rice"@sender.example>', "501", "control_char"),
        # A no-break space.
        ('<"al ice"@sender.example>', "501", "control_char"),
        # A quote that never closes: refused when the command is parsed.
        ('<"alice@sender.example>', "553", "malformed_address"),
        ('<"alice"sender.example>', "501", "bad_address"),
        ('<""@sender.example>', "501", "bad_address"),
        ('<"a"@b@sender.example>', "501", "bad_address"),
        ("<alice@[192.0.2.999]>", "501", "address_literal"),
        ("<alice@[IPv6:zz::1]>", "501", "address_literal"),
        ("<alice@[sender.example]>", "501", "address_literal"),
        ("<alice@sender%example>", "501", "control_char"),
    ],
)
async def test_sender_forms_refused_are_logged_with_their_reason(caplog, address, code, reason):
    before = counter("pymta_security_rejections_total", reason=reason)
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            reply = await _mail(client, address.encode())
            assert reply.startswith(code.encode()), reply
            assert await _no_transaction_open(client)

    # One line for the refused MAIL. The RCPT above adds its own.
    (reject,) = [r for r in await records(caplog, "reject") if r.verb == "mail"]
    assert (reject.reason, reject.code) == (reason, int(code))
    # How the address is spelled in the log depends on how far it was parsed,
    # so only its domain is looked for.
    assert _domain_of(address) in reject.sender
    if reason != "malformed_address":
        assert counter("pymta_security_rejections_total", reason=reason) == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "stored"),
    [
        # Quoted where the content needs it, and only there: "a..b"@x keeps
        # its quotes, "alice"@x is alice@x.
        ('<"al ice"@sender.example>', '"al ice"@sender.example'),
        ('<"al@ice"@Sender.Example>', '"al@ice"@sender.example'),
        ('<"al\\"ice"@sender.example>', '"al\\"ice"@sender.example'),
        ('<"al\\\\ice"@sender.example>', '"al\\\\ice"@sender.example'),
        ('<"alice"@sender.example>', "alice@sender.example"),
        ("<.alice@sender.example>", '".alice"@sender.example'),
        ("<alice.@sender.example>", '"alice."@sender.example'),
        ("<al..ice@sender.example>", '"al..ice"@sender.example'),
        ('<"a%b c"@sender.example>', '"a%b c"@sender.example'),
        # Address literals, normalised.
        ("<alice@[192.0.2.1]>", "alice@[192.0.2.1]"),
        ("<alice@[IPv6:2001:DB8:0:0::1]>", "alice@[IPv6:2001:db8::1]"),
        ("<alice@[ipv6:::1]>", "alice@[IPv6:::1]"),
    ],
)
async def test_mail_accepts_every_form_rfc_5321_allows(monkeypatch, address, stored):
    # Address literals go with single-label domains: both need this off.
    monkeypatch.setattr(settings, "PYMTA_REQUIRE_FQDN_SENDER", False)
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        reply = await _mail(client, address.encode())
        assert reply.startswith(b"250"), reply
        await client.cmd(b"RCPT TO:<a@example.com>")
        await client.data(_BODY)
        # Stable: what is forwarded would be accepted again, unchanged.
        await _message(client, sender=f"<{stored}>".encode())
    assert [d["sender"] for d in mda.delivered] == [stored, stored]


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["<alice@[192.0.2.1]>", "<alice@[IPv6:2001:db8::1]>"])
async def test_an_address_literal_is_not_a_fully_qualified_sender(caplog, address):
    """Refused by default, with single-label domains: nothing can answer it
    through an MX. Quoted local-parts are accepted either way."""
    assert settings.PYMTA_REQUIRE_FQDN_SENDER is True
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            reply = await _mail(client, address.encode())
            quoted = await client.cmd(b'MAIL FROM:<"al ice"@sender.example>')

    assert reply.startswith(b"501 5.1.3 Address literals not accepted"), reply
    assert quoted.startswith(b"250"), quoted
    (reject,) = await records(caplog, "reject")
    assert (reject.reason, reject.sender) == ("address_literal", address[1:-1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        b'<"al ice"@example.com>',
        b"<al..ice@example.com>",
        b"<.alice@example.com>",
        b"<a@[192.0.2.1]>",
    ],
)
async def test_rcpt_accepts_none_of_them(address):
    """A recipient names a mailbox of ours, and none has such a name."""
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        reply = await client.cmd(b"RCPT TO:" + address)
        assert reply[:1] == b"5", reply
        assert await _no_recipient_accepted(client)
    assert mda.checked == []


@pytest.mark.asyncio
async def test_a_single_label_sender_domain_is_accepted_when_allowed(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_REQUIRE_FQDN_SENDER", False)
    async with listening() as port, connected(port) as client:
        reply = await _mail(client, b"<root@localhost>")
    assert reply.startswith(b"250"), reply


@pytest.mark.asyncio
async def test_a_single_label_sender_domain_is_refused(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_REQUIRE_FQDN_SENDER", True)
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            reply = await _mail(client, b"<root@localhost>")
            bounce = await client.cmd(b"MAIL FROM:<>")

    assert reply.startswith(b"504 5.5.2"), reply
    assert bounce.startswith(b"250"), bounce
    (reject,) = await records(caplog, "reject")
    assert (reject.reason, reject.sender) == ("non_fqdn_sender", "root@localhost")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender", "blocked"),
    [
        (b"<alice@spam.example>", True),
        (b"<alice@SPAM.Example>", True),
        (b"<alice@notspam.example>", False),
        (b"<alice@sub.spam.example>", False),
        # A bounce has no domain to block.
        (b"<>", False),
    ],
)
async def test_a_blocked_sender_domain_is_refused(monkeypatch, caplog, sender, blocked):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_SENDER_DOMAINS", frozenset({"spam.example"}))
    before = counter("pymta_security_rejections_total", reason="blocked_sender_domain")
    with _info(caplog):
        async with listening() as port, connected(port) as client:
            reply = await _mail(client, sender)

    if not blocked:
        assert reply.startswith(b"250"), reply
        return
    assert reply.startswith(b"554 5.7.1 Sender domain not accepted"), reply
    (reject,) = await records(caplog, "reject")
    assert (reject.verb, reject.reason) == ("mail", "blocked_sender_domain")
    assert reject.sender == "alice@spam.example"
    after = counter("pymta_security_rejections_total", reason="blocked_sender_domain")
    assert after == before + 1


# ---------------------------------------------------------------------------
# The message budget is applied at MAIL, before anything is uploaded.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mail_is_refused_once_the_message_budget_is_spent(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_ENVELOPES_PER_SESSION", 2)
    mda = ScriptedMDA()
    rejections = counter("pymta_security_rejections_total", reason="max_envelopes_per_session")
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            # Two messages go through, and the session is still open.
            assert (await _message(client, sender=b"<a@example.com>")).startswith(b"250")
            assert (await _message(client, sender=b"<a@example.com>")).startswith(b"250")

            reply = await client.cmd(b"MAIL FROM:<a@example.com>")
            assert reply.startswith(b"421 4.7.0 Too many messages"), reply
            assert await client.closed()

    assert len(mda.delivered) == 2
    (reject,) = await records(caplog, "reject")
    assert (reject.verb, reject.reason, reject.envelopes) == (
        "mail",
        "max_envelopes_per_session",
        2,
    )
    assert reject.sender == "a@example.com"
    assert (
        counter("pymta_security_rejections_total", reason="max_envelopes_per_session")
        == rejections + 1
    )


@pytest.mark.asyncio
async def test_a_refused_message_still_spends_the_message_budget(monkeypatch):
    """The budget counts messages uploaded, not messages delivered."""
    monkeypatch.setattr(settings, "PYMTA_MAX_ENVELOPES_PER_SESSION", 1)
    mda = ScriptedMDA(delivers=["temp"])
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        assert (await _message(client)).startswith(b"451")
        reply = await client.cmd(b"MAIL FROM:<a@example.com>")
        assert reply.startswith(b"421 4.7.0 Too many messages"), reply


# ---------------------------------------------------------------------------
# A HELO the MDA could not store is not forwarded.
# ---------------------------------------------------------------------------

_NON_UTF8_HELO = b"mx\xe9.sender.example"


@pytest.mark.asyncio
async def test_a_helo_that_is_not_utf8_is_sanitised_and_logged(caplog):
    mda = ScriptedMDA()
    before = counter("pymta_security_rejections_total", reason="bad_helo")
    with capture_all(caplog):
        async with listening(mda) as port, connected(port) as client:
            reply = await client.cmd(b"HELO " + _NON_UTF8_HELO)
            assert reply.startswith(b"250"), reply
            # The greeting counts: MAIL is not answered "send HELO first".
            assert (await _message(client)).startswith(b"250")

    (record,) = await records(caplog, "helo_sanitised")
    assert record.levelno == logging.DEBUG
    # What the peer sent, and what was forwarded in its place.
    assert record.helo != record.forwarded
    assert record.helo.startswith("mx") and record.helo.endswith(".sender.example")
    assert record.forwarded == "mx?.sender.example"
    assert record.session_id == mda.delivered[0]["session"]
    assert record.client_ip == "127.0.0.1"
    # The line can be written: the offending byte is escaped, not emitted.
    LogfmtFormatter().format(record).encode("utf-8")
    assert counter("pymta_security_rejections_total", reason="bad_helo") == before + 1
    # Replaced at the greeting: the delivery line names the same host.
    (delivered,) = await records(caplog, "message_delivered")
    assert delivered.helo == "mx?.sender.example"


@pytest.mark.asyncio
async def test_a_helo_that_is_not_utf8_never_reaches_the_mda(caplog):
    mda = ScriptedMDA()
    with _info(caplog):
        async with listening(mda) as port, connected(port) as client:
            await client.cmd(b"EHLO " + _NON_UTF8_HELO)
            reply = await _message(client)

    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["client_helo"] == "mx?.sender.example"
    # Storable: no lone surrogate left in it.
    mda.delivered[0]["client_helo"].encode("utf-8")
    (delivered,) = await records(caplog, "message_delivered")
    assert delivered.helo == "mx?.sender.example"


@pytest.mark.asyncio
async def test_control_characters_in_a_helo_are_replaced():
    mda = ScriptedMDA()
    async with listening(mda) as port, connected(port) as client:
        await client.cmd(b"EHLO mx\t.sender\x00.example")
        await _message(client)
    assert mda.delivered[0]["client_helo"] == "mx?.sender?.example"


@pytest.mark.asyncio
async def test_a_utf8_helo_is_kept(caplog):
    mda = ScriptedMDA()
    with capture_all(caplog):
        async with listening(mda) as port, connected(port) as client:
            await client.cmd("HELO mx.bücher.example".encode())
            await _message(client)
    assert mda.delivered[0]["client_helo"] == "mx.bücher.example"
    assert not [r for r in caplog.records if r.getMessage() == "helo_sanitised"]


# ---------------------------------------------------------------------------
# A blocked recipient is blocked under every tag.
# ---------------------------------------------------------------------------


async def _rcpt(address: bytes) -> bytes:
    async with listening() as port, connected(port) as client:
        await _mail(client, b"<sender@example.org>")
        return await client.cmd(b"RCPT TO:" + address)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "blocked"),
    [
        (b"<victim@example.com>", True),
        (b"<victim+news@example.com>", True),
        (b"<Victim+a+b@Example.COM>", True),
        (b"<victim+@example.com>", True),
        (b"<victimized@example.com>", False),
        (b"<other+victim@example.com>", False),
        (b"<victim+x@example.org>", False),
    ],
)
async def test_blocking_a_recipient_covers_its_tagged_forms(monkeypatch, address, blocked):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    reply = await _rcpt(address)
    assert reply.startswith(b"550 5.1.1 Recipient not accepted" if blocked else b"250"), reply


@pytest.mark.asyncio
async def test_a_block_on_a_tagged_address_is_exact(monkeypatch):
    monkeypatch.setattr(
        settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim+news@example.com"})
    )
    assert (await _rcpt(b"<victim+news@example.com>")).startswith(b"550")
    assert (await _rcpt(b"<victim@example.com>")).startswith(b"250")
    assert (await _rcpt(b"<victim+other@example.com>")).startswith(b"250")


@pytest.mark.asyncio
@pytest.mark.parametrize(("delimiter", "blocked"), [("", False), ("-", False), ("+", True)])
async def test_the_tag_delimiter_is_configurable(monkeypatch, delimiter, blocked):
    monkeypatch.setattr(settings, "PYMTA_BLOCKED_RECIPIENTS", frozenset({"victim@example.com"}))
    monkeypatch.setattr(settings, "PYMTA_RECIPIENT_DELIMITER", delimiter)
    reply = await _rcpt(b"<victim+news@example.com>")
    assert reply.startswith(b"550" if blocked else b"250"), reply


# ---------------------------------------------------------------------------
# How the message arrived, for the Received header the MDA writes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helo", "utf8", "tls", "protocol"),
    [
        # In clear: no trailing S, and no TLS fields at all.
        (b"EHLO", False, None, "ESMTP"),
        (b"HELO", False, None, "SMTP"),
        (b"EHLO", True, None, "UTF8SMTP"),
        # Encrypted, and how well.
        (b"EHLO", False, "TLSv1.3", "ESMTPS"),
        (b"EHLO", True, "TLSv1.3", "UTF8SMTPS"),
        (b"EHLO", False, "TLSv1.2", "ESMTPS"),
    ],
)
async def test_the_mda_is_told_how_the_message_arrived(monkeypatch, helo, utf8, tls, protocol):
    monkeypatch.setattr(settings, "PYMTA_SMTP_HOSTNAME", "mx1.ours.example")
    server_ctx, client_ctx = _tls_contexts() if tls else (None, None)
    if tls == "TLSv1.2":
        client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    cipher = None
    mda = ScriptedMDA()
    async with listening(mda, tls_context=server_ctx) as port, connected(port) as client:
        await client.cmd(helo + b" mx.sender.example")
        if tls:
            assert (await client.starttls(client_ctx)).startswith(b"220")
            # The cipher and version as this end of the handshake saw them.
            cipher = client.writer.get_extra_info("cipher")
            assert cipher[1] == tls
            await client.cmd(helo + b" mx.sender.example")
        sender = b"<sender@example.com>" + (b" SMTPUTF8" if utf8 else b"")
        reply = await _message(client, sender=sender)

    assert reply.startswith(b"250"), reply
    assert mda.delivered[0]["transport"] == {
        "protocol": protocol,
        "tls_version": cipher[1] if cipher else None,
        "tls_cipher": cipher[0] if cipher else None,
        "server_name": "mx1.ours.example",
    }
    assert mda.delivered[0]["client_helo"] == "mx.sender.example"
