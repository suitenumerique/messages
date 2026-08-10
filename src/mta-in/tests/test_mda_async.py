"""Unit tests for :class:`pymta.mda_async.MDAClient`.

Stubs ``httpx.AsyncClient.post`` to drive every code path (timeout, transport
error, 5xx, 4xx, 200) and to verify the circuit breaker opens / resets as
expected. No real HTTP traffic, no Docker stack.
"""

from __future__ import annotations

import datetime

import httpx
import jwt
import pytest

from pymta import settings
from pymta.mda_async import MDAClient


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

    async def post(self, url, content=None, headers=None):
        self.calls += 1
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
async def test_transport_error_returns_temp_fail():
    client, _ = _new_client()
    client._client = _StubAsyncClient([httpx.ConnectError("no route")])
    result = await client.check_recipient("user@example.com")
    assert result.temp_fail is True


@pytest.mark.asyncio
async def test_5xx_returns_temp_fail():
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(503, b'{"detail":"upstream"}')])
    result = await client.check_recipient("user@example.com")
    assert result.temp_fail is True
    assert result.status_code == 503


async def _deliver(client):
    return await client.deliver(
        message=b"From: a@example.com\r\n\r\nbody\r\n",
        sender="a@example.com",
        original_recipients=["user@example.com"],
        client_address="192.0.2.1",
        client_port="2525",
        client_hostname=None,
        client_helo="relay.example.com",
    )


@pytest.mark.parametrize("status", [400, 413, 415])
@pytest.mark.asyncio
async def test_message_rejecting_statuses_are_permanent_on_deliver(status):
    # The only statuses that mean "this message is unacceptable, retrying
    # cannot help": unparseable, oversize, wrong content type.
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(status, b'{"detail":"no"}')])
    result = await _deliver(client)
    assert result.ok is False
    assert result.temp_fail is False
    assert result.status_code == status


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
    MDAClient(base_url="https://mda.example.com/api/", secret="too-short")
    assert any("MDA_API_SECRET" in rec.message for rec in caplog.records)


def test_local_http_url_is_silent(caplog):
    caplog.set_level("WARNING")
    MDAClient(base_url="http://127.0.0.1:8000/api/", secret="x" * 32)
    assert not any("plaintext" in rec.message for rec in caplog.records)


def test_empty_secret_logs_warning(caplog, monkeypatch):
    # Without this the misconfiguration only surfaces on the first MDA call,
    # as a signing RuntimeError that defers every message.
    # An empty `secret` argument falls back to the setting, which the docker
    # test runner populates from the env, so blank it to exercise the
    # unconfigured case rather than the dev secret.
    monkeypatch.setattr(settings, "MDA_API_SECRET", "")
    caplog.set_level("WARNING")
    MDAClient(base_url="https://mda.example.com/api/", secret="")
    assert any("MDA_API_SECRET is empty" in rec.message for rec in caplog.records)


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


def test_jwt_ttl_is_configurable():
    # A fixed 60s expiry leaves no room for clock skew against the MDA, and a
    # token the MDA reads as expired is a 401, which now defers rather than
    # bounces, but still stalls the mail.
    client = MDAClient(base_url="https://mda.example.invalid/api/", secret="x" * 32, jwt_ttl=600)
    before = datetime.datetime.now(tz=datetime.UTC)
    decoded = jwt.decode(client._build_jwt(b"body", {}), client.secret, algorithms=["HS256"])
    ttl = datetime.datetime.fromtimestamp(decoded["exp"], tz=datetime.UTC) - before
    assert 590 <= ttl.total_seconds() <= 600
