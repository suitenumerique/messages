"""Unit tests for :class:`pymta.mda_async.MDAClient`.

Stubs ``httpx.AsyncClient.post`` to drive every code path (timeout, transport
error, 5xx, 4xx, 200) and to verify the circuit breaker opens / resets as
expected. No real HTTP traffic, no Docker stack.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import sys

import httpx
import jwt
import pytest

from pymta import settings
from pymta.mda_async import (
    _BODY_CHUNK,
    _MIN_SECRET_LENGTH,
    ENVELOPED_CONTENT_TYPE,
    MDAClient,
    _stream,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _StubAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` driven by a script of responses."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.headers = []
        self.bodies = []

    async def post(self, url, content=None, headers=None):
        self.calls += 1
        self.headers.append(headers or {})
        if hasattr(content, "__aiter__"):
            content = b"".join([part async for part in content])
        self.bodies.append(content)
        action = self.script.pop(0) if self.script else None
        if isinstance(action, Exception):
            raise action
        return action

    async def aclose(self):
        pass


def _resp(status_code: int, body: bytes = b'{"ok": true}'):
    return httpx.Response(status_code=status_code, content=body)


def _new_client(*, secret: str = "x" * 32, threshold: int = 3, cooldown: int = 30):
    """Construct an MDAClient wired to fakes, with no settings module mutation."""
    clock = _FakeClock()
    client = MDAClient(
        base_url="https://mda.example.invalid/api/",
        secret=secret,
        timeout=5,
        breaker_threshold=threshold,
        breaker_cooldown=cooldown,
        clock=clock,
    )
    return client, clock


# ---------------------------------------------------------------------------
# Single-shot result classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_temp_fail():
    client, _ = _new_client()
    client._client = _StubAsyncClient([httpx.TimeoutException("slow")])
    result = await client.check_recipient("user@example.com")
    assert result.ok is False
    assert result.temp_fail is True
    assert result.status_code == 0


@pytest.mark.asyncio
async def test_transport_error_returns_temp_fail(caplog):
    client, _ = _new_client()
    client._client = _StubAsyncClient([httpx.ConnectError("no route")])
    with caplog.at_level(logging.DEBUG, logger="pymta"):
        result = await client.check_recipient("user@example.com")
    assert result.temp_fail is True
    # The MDA's fault or the network's: a warning, with the traceback.
    (record,) = caplog.records
    assert (record.getMessage(), record.levelname) == ("mda_transport_error", "WARNING")
    assert record.exc_info is not None


@pytest.mark.asyncio
async def test_5xx_returns_temp_fail(caplog):
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(503, b'{"detail":"upstream"}')])
    with caplog.at_level(logging.DEBUG, logger="pymta"):
        result = await client.check_recipient("user@example.com")
    assert result.temp_fail is True
    assert result.status_code == 503
    assert [(r.getMessage(), r.levelname) for r in caplog.records] == [
        ("mda_unhealthy", "WARNING")
    ]


async def _deliver(client):
    return await client.deliver(
        message=b"From: a@example.com\r\n\r\nbody\r\n",
        sender="a@example.com",
        original_recipients=["user@example.com"],
        client_address="192.0.2.1",
        client_port="2525",
        client_helo="relay.example.com",
    )


@pytest.mark.parametrize("status", [400, 413])
@pytest.mark.asyncio
async def test_message_rejecting_statuses_are_permanent_on_deliver(status, caplog):
    # The only statuses that mean "this message is unacceptable, retrying
    # cannot help": unparseable, oversize.
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, b'{"status":"error","detail":"no"}')])
    with caplog.at_level(logging.DEBUG, logger="pymta"):
        result = await _deliver(client)
    assert result.ok is False
    assert result.temp_fail is False
    assert result.status_code == status
    # A sender chooses its messages: the refusal is not a line it can raise.
    assert [(r.getMessage(), r.levelname) for r in caplog.records] == [("mda_refused", "DEBUG")]


@pytest.mark.parametrize("status", [400, 413])
@pytest.mark.parametrize(
    "body", [b"<html>413 Request Entity Too Large</html>", b'{"detail":"no"}']
)
@pytest.mark.asyncio
async def test_the_same_statuses_from_something_else_than_the_mda_defer(status, body, caplog):
    """An ingress in front of the MDA answers 413 too, and knows nothing of the message."""
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, body)])
    with caplog.at_level(logging.DEBUG, logger="pymta"):
        result = await _deliver(client)
    assert (result.ok, result.temp_fail, result.status_code) == (False, True, status)
    # Answered, so not an outage: the event that names one is kept for a 5xx.
    assert [(r.getMessage(), r.levelname) for r in caplog.records] == [
        ("mda_unexpected_status", "WARNING")
    ]


@pytest.mark.asyncio
async def test_messages_the_mda_fails_on_do_not_open_the_breaker():
    """Its own 500 about a message is not an outage: one sender could otherwise
    have everyone else's mail deferred."""
    client, _ = _new_client(threshold=3)
    refused = _resp(500, b'{"status":"error","detail":"Failed to deliver"}')
    client._client = _StubAsyncClient([refused] * 5)
    for _ in range(5):
        result = await _deliver(client)
        assert (result.temp_fail, result.status_code) == (True, 500)
    assert client.breaker_is_open() is False
    assert client._client.calls == 5


@pytest.mark.asyncio
async def test_an_outage_the_mda_reports_opens_the_breaker():
    """Its 503 for a failing database carries no verdict."""
    client, _ = _new_client(threshold=3)
    down = _resp(503, b'{"status":"unavailable","detail":"Delivery is temporarily unavailable"}')
    client._client = _StubAsyncClient([down] * 3)
    for _ in range(3):
        result = await _deliver(client)
        assert (result.temp_fail, result.status_code) == (True, 503)
    assert client.breaker_is_open() is True


@pytest.mark.asyncio
async def test_an_mda_that_does_not_know_the_enveloped_form_defers():
    """415 is an MDA older than this client, mid-rollout. Bouncing would lose mail."""
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(415, b'{"detail":"no"}')])
    result = await _deliver(client)
    assert result.ok is False
    assert result.temp_fail is True


@pytest.mark.asyncio
async def test_deliver_sends_the_envelope_in_the_body():
    """Not in the JWT: 100 recipients do not fit in an HTTP header."""
    client, _ = _new_client()
    stub = _StubAsyncClient([_resp(200, b'{"status": "ok"}')])
    client._client = stub
    message = b"From: a@example.com\r\n\r\nline one\nline two\r\n"
    recipients = [f"{'r' * 60}{i:03d}@{'d' * 200}.example" for i in range(100)]

    await client.deliver(
        message=message,
        sender="sénder@example.com",
        original_recipients=recipients,
        client_address="192.0.2.1",
        client_port="2525",
        client_helo="relay.example.com",
        session="cafe1234",
        transport={
            "protocol": "ESMTPS",
            "tls_version": "TLSv1.3",
            "tls_cipher": "TLS_AES_256_GCM_SHA384",
            "server_name": "mx1.ours.example",
        },
    )

    headers, body = stub.headers[0], stub.bodies[0]
    assert headers["Content-Type"] == ENVELOPED_CONTENT_TYPE
    # The whole header stays far under gunicorn's 8190-byte limit.
    assert len(headers["Authorization"]) < 500
    assert headers["Content-Length"] == str(len(body))

    head, _, sent_message = body.partition(b"\n")
    assert sent_message == message
    assert json.loads(head) == {
        "sender": "sénder@example.com",
        "original_recipients": recipients,
        "client_address": "192.0.2.1",
        "client_port": "2525",
        "client_helo": "relay.example.com",
        "size": str(len(message)),
        "protocol": "ESMTPS",
        "tls_version": "TLSv1.3",
        "tls_cipher": "TLS_AES_256_GCM_SHA384",
        "server_name": "mx1.ours.example",
    }
    claims = _claims_of(stub, client)
    assert claims["body_hash"] == hashlib.sha256(body).hexdigest()
    assert set(claims) == {"exp", "body_hash", "mta_session"}


@pytest.mark.parametrize("status", [400, 413, 415])
@pytest.mark.asyncio
async def test_message_rejecting_statuses_defer_on_recipient_check(status):
    # A recipient check carries no message, so these statuses describe the
    # check request we built, not the mailbox. Bouncing on our own bug would
    # tell the sender a working address is permanently bad.
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, b'{"detail":"no"}')])
    result = await client.check_recipient("user@example.com")
    assert result.ok is False
    assert result.temp_fail is True
    assert result.status_code == status


@pytest.mark.parametrize(
    "status",
    [
        207,  # Multi-Status: some recipients delivered, some not
        401,  # secret rotation skew / clock skew on `exp`
        403,
        404,  # MDA route missing: a deployment mistake, not a verdict
        429,  # throttled
        418,  # anything unrecognised defaults to the safe side
    ],
)
@pytest.mark.asyncio
async def test_non_rejecting_statuses_defer(status):
    # Losing mail is worse than a retry, so everything outside the explicit
    # permanent set defers.
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, b'{"status":"partial_success"}')])
    result = await client.check_recipient("user@example.com")
    assert result.ok is False
    assert result.temp_fail is True
    assert result.status_code == status


@pytest.mark.asyncio
async def test_deferring_statuses_do_not_trip_the_breaker():
    # A 207 or a 401 is a complete answer from a healthy MDA. Only 5xx and
    # transport failures are liveness signals.
    client, _ = _new_client(threshold=2)
    client._client = _StubAsyncClient([_resp(207), _resp(401), _resp(429)])
    for _ in range(3):
        result = await client.check_recipient("a@b")
        assert result.temp_fail is True
    assert client._consecutive_failures == 0
    assert client._open_until is None


@pytest.mark.asyncio
async def test_200_returns_ok_with_payload():
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(200, b'{"user@example.com": true}')])
    result = await client.check_recipient("user@example.com")
    assert result.ok is True
    assert result.payload == {"user@example.com": True}


# ---------------------------------------------------------------------------
# ``payload`` is always a dict.
#
# Callers read it with ``.get()`` on the reply path of a live SMTP session; a
# body that is not a JSON object would otherwise raise AttributeError there and
# turn a readable defer into a 421 + disconnect. Collapsing to {} keeps the
# reply logic total, and the handlers treat {} as "no answer", never a verdict.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"<html><body>200 OK</body></html>", id="html-error-page"),
        pytest.param(b"not json at all", id="garbage"),
        pytest.param(b'{"user@example.com": true', id="truncated-json"),
        pytest.param(b"[true, false]", id="json-list"),
        pytest.param(b"null", id="json-null"),
        pytest.param(b'"user@example.com"', id="json-string"),
        pytest.param(b"42", id="json-number"),
        pytest.param(b"true", id="json-bool"),
    ],
)
@pytest.mark.parametrize("status", [200, 400, 500])
@pytest.mark.asyncio
async def test_non_object_bodies_become_an_empty_payload(body, status):
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, body)])
    result = await client.check_recipient("user@example.com")
    assert result.payload == {}
    assert result.status_code == status


@pytest.mark.asyncio
async def test_non_object_body_on_deliver_also_becomes_an_empty_payload():
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(200, b"[]")])
    result = await _deliver(client)
    assert result.ok is True
    assert result.payload == {}


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_consecutive_failures():
    client, clock = _new_client(threshold=3, cooldown=30)
    client._client = _StubAsyncClient(
        [httpx.TimeoutException("a"), httpx.TimeoutException("b"), httpx.TimeoutException("c")]
    )
    for _ in range(3):
        result = await client.check_recipient("a@b")
        assert result.temp_fail is True
    # Breaker should now be open; the next call must NOT hit the network.
    stub = client._client
    result = await client.check_recipient("a@b")
    assert result.temp_fail is True
    assert stub.calls == 3, "breaker did not short-circuit"


@pytest.mark.asyncio
async def test_breaker_closes_after_cooldown():
    client, clock = _new_client(threshold=2, cooldown=30)
    client._client = _StubAsyncClient(
        [httpx.TimeoutException("a"), httpx.TimeoutException("b"), _resp(200)]
    )
    await client.check_recipient("a@b")
    await client.check_recipient("a@b")
    # Breaker open, so this fast-fails.
    await client.check_recipient("a@b")
    assert client._open_until is not None
    # Advance past cooldown; the next call probes the network.
    clock.advance(31.0)
    result = await client.check_recipient("a@b")
    assert result.ok is True
    assert client._open_until is None
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_success_resets_failure_counter():
    client, _ = _new_client(threshold=10)
    client._client = _StubAsyncClient(
        [httpx.TimeoutException("a"), httpx.TimeoutException("b"), _resp(200)]
    )
    await client.check_recipient("a@b")
    await client.check_recipient("a@b")
    assert client._consecutive_failures == 2
    await client.check_recipient("a@b")
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_non_5xx_does_not_count_as_breaker_failure():
    # 404 means the MDA understood our request and rejected it. That is not a
    # liveness signal worth tripping the breaker.
    client, _ = _new_client(threshold=2)
    client._client = _StubAsyncClient([_resp(404), _resp(404), _resp(404)])
    for _ in range(3):
        await client.check_recipient("a@b")
    assert client._consecutive_failures == 0
    assert client._open_until is None


@pytest.mark.asyncio
async def test_breaker_disabled_when_threshold_zero():
    client, _ = _new_client(threshold=0)
    client._client = _StubAsyncClient([httpx.TimeoutException("a")] * 5)
    for _ in range(5):
        await client.check_recipient("a@b")
    assert client._open_until is None


# ---------------------------------------------------------------------------
# Credential / URL hygiene warnings (S1 + S2).
# ---------------------------------------------------------------------------


def test_non_local_http_url_logs_warning(caplog):
    caplog.set_level("WARNING")
    MDAClient(base_url="http://mda.example.com/api/", secret="x" * 32)
    assert any("plaintext" in rec.message for rec in caplog.records)


def test_short_secret_logs_warning(caplog):
    caplog.set_level("WARNING")
    secret = "too-short"
    MDAClient(base_url="https://mda.example.com/api/", secret=secret)
    record = next(r for r in caplog.records if r.message == "mda_secret_weak")
    assert record.levelname == "WARNING"
    assert record.minimum == _MIN_SECRET_LENGTH
    # The threshold is reported, the secret's own length is not: it would narrow
    # the search for anyone holding the log and a captured JWT.
    assert not hasattr(record, "length")
    assert secret not in caplog.text


def test_local_http_url_is_silent(caplog):
    caplog.set_level("WARNING")
    MDAClient(base_url="http://127.0.0.1:8000/api/", secret="x" * 32)
    assert not any(rec.message == "mda_url_plaintext" for rec in caplog.records)


def test_empty_secret_refuses_to_start(caplog, monkeypatch):
    # Started anyway, every MDA call would raise while signing and every
    # command would be answered 421 with a traceback.
    # An empty `secret` argument falls back to the setting, which the docker
    # test runner populates from the env, so blank it to exercise the
    # unconfigured case rather than the dev secret.
    monkeypatch.setattr(settings, "MDA_API_SECRET", "")
    caplog.set_level("WARNING")
    with pytest.raises(ValueError, match="MDA_API_SECRET"):
        MDAClient(base_url="https://mda.example.com/api/", secret="")
    record = next(r for r in caplog.records if r.message == "mda_secret_missing")
    assert record.levelname == "ERROR"


# ---------------------------------------------------------------------------
# JWT claim ordering (B1).
# ---------------------------------------------------------------------------


def test_metadata_cannot_shadow_exp_or_body_hash():
    client, _ = _new_client()
    body = b"hello"
    # Attacker-supplied metadata tries to overwrite security fields.
    token = client._build_jwt(body, {"exp": 0, "body_hash": "deadbeef", "sender": "u@x"})
    decoded = jwt.decode(token, client.secret, algorithms=["HS256"])
    # The real exp must be in the future, not 0.
    assert decoded["exp"] != 0
    # The real body_hash must be the sha256 of `body`, not "deadbeef".
    assert decoded["body_hash"] != "deadbeef"
    # Sender (non-conflicting metadata) survives.
    assert decoded["sender"] == "u@x"


def _claims_of(stub, client) -> dict:
    """Decode the JWT the stub was called with."""
    token = stub.headers[0]["Authorization"].split(" ", 1)[1]
    return jwt.decode(token, client.secret, algorithms=["HS256"])


@pytest.mark.asyncio
async def test_the_session_id_travels_as_a_signed_claim():
    """The MDA logs it, so it has to be signed rather than a header.

    A header could be retagged by anything on the path, which would attach one
    delivery's SMTP session to another's MDA lines.
    """
    client, _ = _new_client()
    stub = _StubAsyncClient([_resp(200), _resp(200)])
    client._client = stub

    await client.check_recipient("a@example.com", session="deadbeef")
    assert _claims_of(stub, client)["mta_session"] == "deadbeef"

    stub.headers.clear()
    await client.deliver(
        message=b"body",
        sender="s@example.com",
        original_recipients=["a@example.com"],
        client_address=None,
        client_port=None,
        client_helo=None,
        session="cafe1234",
    )
    claims = _claims_of(stub, client)
    assert claims["mta_session"] == "cafe1234"
    # A claim, not a field of the body envelope.
    assert "mta_session" not in json.loads(stub.bodies[-1].partition(b"\n")[0])


@pytest.mark.asyncio
async def test_no_session_means_no_claim():
    """Rather than a null one, which reads as an id the MDA could log."""
    client, _ = _new_client()
    stub = _StubAsyncClient([_resp(200)])
    client._client = stub

    await client.check_recipient("a@example.com")
    assert "mta_session" not in _claims_of(stub, client)


def test_jwt_ttl_is_configurable():
    # A fixed 60s expiry leaves no room for clock skew against the MDA, and a
    # token the MDA reads as expired is a 401, which now defers rather than
    # bounces, but still stalls the mail.
    client = MDAClient(base_url="https://mda.example.invalid/api/", secret="x" * 32, jwt_ttl=600)
    before = datetime.datetime.now(tz=datetime.UTC)
    decoded = jwt.decode(client._build_jwt(b"body", {}), client.secret, algorithms=["HS256"])
    ttl = datetime.datetime.fromtimestamp(decoded["exp"], tz=datetime.UTC) - before
    assert 590 <= ttl.total_seconds() <= 600


# ---------------------------------------------------------------------------
# Client construction. Everything above replaces ``client._client`` with a stub,
# which means the real one is never built and its options are never checked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_ignores_proxy_environment(monkeypatch):
    """A proxy in the environment must not capture MDA traffic.

    httpx defaults ``trust_env=True``, which routes requests through whatever
    ALL_PROXY / HTTPS_PROXY names. These carry a bearer JWT and the full message
    body, and the MDA is one known internal endpoint, so there is nothing here a
    proxy should be selecting. This stack ships a socks-proxy service and an
    MTA_OUT_DIRECT_PROXIES setting, so a stray variable is a real possibility.

    Asserted on the built client's mount table rather than on the keyword we
    passed: the keyword is what we wrote, an empty mount table is what it has to
    mean. With trust_env left at its default this same code yields one mount.
    """
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")

    client = MDAClient(secret="x" * 32)
    try:
        http = await client.start()
        assert http._mounts == {}, f"proxy environment captured MDA traffic: {http._mounts}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_does_not_follow_redirects():
    """A redirect would replay the Authorization header at the named host.

    body_hash would still validate there, so the token stays usable for exactly
    the request it was minted for, against a server we never chose.
    """
    client = MDAClient(secret="x" * 32)
    try:
        http = await client.start()
        assert http.follow_redirects is False
    finally:
        await client.close()


@pytest.mark.parametrize(("sessions", "connections"), [(120, 120), (549, 549), (0, None)])
@pytest.mark.asyncio
async def test_the_pool_has_a_connection_for_every_session(monkeypatch, sessions, connections):
    """A fixed pool smaller than the session cap would have the sessions past
    it wait for a connection, holding their message, and be deferred."""
    monkeypatch.setattr(settings, "PYMTA_MAX_SESSIONS_TOTAL", sessions)
    client = MDAClient(secret="x" * 32)
    try:
        http = await client.start()
        assert http._transport._pool._max_connections == (connections or sys.maxsize)
        # And keeps as many open, or a busy server reconnects for each call.
        assert http._transport._pool._max_keepalive_connections == (connections or sys.maxsize)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_breaker_reads_closed_once_the_cooldown_lapses_without_traffic():
    """The gauge must not stay stuck at 1 on an idle process.

    Nothing calls the MDA after the last failure, so nothing runs the code that
    notices the cooldown elapsed. A gauge assigned at the moment the breaker
    tripped would still read "open" long after calls stopped being
    short-circuited, which is why it is computed instead.
    """
    client, clock = _new_client(threshold=1, cooldown=30)
    client._client = _StubAsyncClient([_resp(503, b"{}")])
    await client.check_recipient("a@example.com")
    assert client.breaker_is_open() is True

    # No further requests: only time passes.
    clock.now += 31
    assert client.breaker_is_open() is False


@pytest.mark.asyncio
async def test_an_error_page_that_is_not_utf8_is_an_ordinary_failure():
    """A proxy's latin-1 502 page used to raise out of the call: the session
    got 421 and a traceback, and the breaker never saw the failure."""
    page = "<h1>Passerelle incorrecte: délai dépassé</h1>".encode("latin-1")
    client, _ = _new_client(threshold=1)
    client._client = _StubAsyncClient([_resp(502, page)])

    result = await client.check_recipient("a@example.com")

    assert (result.ok, result.temp_fail, result.status_code) == (False, True, 502)
    assert result.payload == {}
    assert client.breaker_is_open() is True


@pytest.mark.asyncio
async def test_a_200_that_is_not_utf8_carries_no_payload():
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(200, b"caf\xe9")])
    result = await client.check_recipient("a@example.com")
    assert (result.ok, result.payload) == (True, {})


# ---------------------------------------------------------------------------
# After the cooldown, one call probes; the others wait for its answer.
# ---------------------------------------------------------------------------


class _GatedClient:
    """An HTTP client whose answers the test releases one at a time."""

    def __init__(self):
        self.calls = 0
        self.release = asyncio.Event()
        self.outcome = None

    async def post(self, url, content=None, headers=None):
        self.calls += 1
        await self.release.wait()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self):
        pass


async def _tripped(threshold=2):
    client, clock = _new_client(threshold=threshold, cooldown=30)
    client._client = _StubAsyncClient([httpx.TimeoutException("down")] * threshold)
    for _ in range(threshold):
        await client.check_recipient("a@example.com")
    assert client.breaker_is_open() is True
    return client, clock


@pytest.mark.asyncio
async def test_after_the_cooldown_a_single_call_probes_a_dead_mda():
    client, clock = await _tripped()
    gated = client._client = _GatedClient()
    clock.advance(31)

    # Ten sessions arrive together once the cooldown is over.
    calls = [asyncio.ensure_future(client.check_recipient("a@example.com")) for _ in range(10)]
    await asyncio.sleep(0.01)
    assert gated.calls == 1, "every waiting session went to an MDA not known to be back"
    assert client.breaker_is_open() is True

    gated.outcome = httpx.TimeoutException("still down")
    gated.release.set()
    results = await asyncio.gather(*calls)

    assert all(result.temp_fail for result in results)
    assert gated.calls == 1
    # Open again for a full cooldown, on that one failure.
    assert client.breaker_is_open() is True
    clock.advance(29)
    assert client.breaker_is_open() is True


@pytest.mark.asyncio
async def test_a_probe_that_succeeds_closes_the_breaker():
    client, clock = await _tripped()
    gated = client._client = _GatedClient()
    clock.advance(31)

    probe = asyncio.ensure_future(client.check_recipient("a@example.com"))
    await asyncio.sleep(0.01)
    shed = await client.check_recipient("a@example.com")
    assert shed.temp_fail and gated.calls == 1

    gated.outcome = _resp(200, b'{"a@example.com": true}')
    gated.release.set()
    assert (await probe).ok is True
    assert client.breaker_is_open() is False
    # Everyone goes through again.
    assert (await client.check_recipient("a@example.com")).ok is True
    assert gated.calls == 2


@pytest.mark.asyncio
async def test_a_probe_cancelled_midway_does_not_hold_the_breaker_shut():
    """The DATA deadline cancels a delivery in flight. If that call was the
    probe, the next one has to be able to take its place."""
    client, clock = await _tripped()
    gated = client._client = _GatedClient()
    clock.advance(31)

    probe = asyncio.ensure_future(client.check_recipient("a@example.com"))
    await asyncio.sleep(0.01)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    gated.outcome = _resp(200, b'{"a@example.com": true}')
    gated.release.set()
    assert (await client.check_recipient("a@example.com")).ok is True
    assert client.breaker_is_open() is False


@pytest.mark.asyncio
async def test_our_own_pool_running_out_is_not_an_mda_failure():
    client, _ = _new_client(threshold=2)
    client._client = _StubAsyncClient([httpx.PoolTimeout("no connection free")] * 5)

    results = [await client.check_recipient("a@example.com") for _ in range(5)]

    assert all(result.temp_fail and not result.ok for result in results)
    assert client.breaker_is_open() is False
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_a_call_in_flight_since_before_the_outage_is_not_taken_for_the_probe():
    """Its failure says nothing new: the probe keeps its turn and its cooldown."""
    client, clock = _new_client(threshold=2, cooldown=30)
    old = client._client = _GatedClient()
    straggler = asyncio.ensure_future(client.check_recipient("a@example.com"))
    await asyncio.sleep(0.01)
    client._client = _StubAsyncClient([httpx.TimeoutException("down")] * 2)
    for _ in range(2):
        await client.check_recipient("a@example.com")
    clock.advance(31)
    gated = client._client = _GatedClient()
    probe = asyncio.ensure_future(client.check_recipient("a@example.com"))
    await asyncio.sleep(0.01)
    assert gated.calls == 1

    old.outcome = httpx.TimeoutException("down")
    old.release.set()
    assert (await straggler).temp_fail
    # The probe is still the one call let through.
    assert (await client.check_recipient("a@example.com")).temp_fail
    assert gated.calls == 1

    # It ends without a verdict: the next call probes at once, where a
    # cooldown restarted by the straggler would shed it.
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    gated.outcome = _resp(200, b'{"a@example.com": true}')
    gated.release.set()
    assert (await client.check_recipient("a@example.com")).ok is True
    assert gated.calls == 2


class _SlowClient:
    """An HTTP client that answers 200 after ``delay`` seconds."""

    def __init__(self, delay: float):
        self.delay = delay

    async def post(self, url, content=None, headers=None):
        await asyncio.sleep(self.delay)
        return _resp(200, b'{"status": "ok"}')


@pytest.mark.asyncio
async def test_a_check_is_bounded_as_a_whole_and_a_delivery_is_not(caplog):
    """httpx times each read. An MDA that drips its answer meets none of them."""
    client = MDAClient(base_url="https://mda.example.invalid/api/", secret="x" * 32, timeout=0.05)
    client._client = _SlowClient(delay=0.3)
    with caplog.at_level(logging.WARNING, logger="pymta"):
        result = await client.check_recipient("a@example.com")
    assert (result.ok, result.temp_fail, result.status_code) == (False, True, 0)
    assert [r.getMessage() for r in caplog.records] == ["mda_timeout"]
    # Counted by the breaker like any other timeout.
    assert client._consecutive_failures == 1

    # The DATA deadline is what bounds a delivery.
    delivered = await client.deliver(
        message=b"x",
        sender="a@example.com",
        original_recipients=["b@example.com"],
        client_address=None,
        client_port=None,
        client_helo=None,
    )
    assert delivered.ok is True


@pytest.mark.asyncio
async def test_a_call_sent_during_an_outage_that_has_ended_does_not_count_against_the_mda():
    """Failing after the recovery, it would open the breaker again."""
    client, clock = _new_client(threshold=1, cooldown=30)
    old = client._client = _GatedClient()
    straggler = asyncio.ensure_future(client.check_recipient("a@example.com"))
    await asyncio.sleep(0.01)
    client._client = _StubAsyncClient([httpx.TimeoutException("down")])
    await client.check_recipient("a@example.com")
    assert client.breaker_is_open() is True
    clock.advance(31)
    client._client = _StubAsyncClient([_resp(200, b'{"a@example.com": true}')])
    assert (await client.check_recipient("a@example.com")).ok is True
    assert client.breaker_is_open() is False

    old.outcome = httpx.TimeoutException("down")
    old.release.set()
    assert (await straggler).temp_fail
    assert client.breaker_is_open() is False
    assert client._consecutive_failures == 0

    # A failure of now still counts.
    client._client = _StubAsyncClient([httpx.TimeoutException("down")])
    await client.check_recipient("a@example.com")
    assert client.breaker_is_open() is True


@pytest.mark.asyncio
async def test_a_message_is_handed_to_httpx_in_pieces():
    message = bytes(range(256)) * 1000
    parts = [part async for part in _stream((b"head\n", message))]
    assert b"".join(parts) == b"head\n" + message
    assert [len(part) for part in parts] == [5, _BODY_CHUNK, _BODY_CHUNK, _BODY_CHUNK, 59392]
