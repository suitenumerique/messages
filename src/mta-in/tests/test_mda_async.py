"""Unit tests for :class:`pymta.mda_async.MDAClient`.

Stubs ``httpx.AsyncClient.post`` to drive every code path (timeout, transport
error, 5xx, 4xx, 200) and to verify the circuit breaker opens / resets as
expected. No real HTTP traffic, no Docker stack.
"""

from __future__ import annotations

import httpx
import pytest

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
    """Construct an MDAClient wired to fakes — no settings module mutation."""
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


@pytest.mark.asyncio
async def test_4xx_returns_perm_fail():
    client, _ = _new_client()
    client._client = _StubAsyncClient([_resp(404, b'{"detail":"no"}')])
    result = await client.check_recipient("user@example.com")
    assert result.ok is False
    assert result.temp_fail is False
    assert result.status_code == 404


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
    # Breaker should now be open — the next call must NOT hit the network.
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
    # Breaker open — fast-fail.
    await client.check_recipient("a@b")
    assert client._open_until is not None
    # Advance past cooldown — next call probes the network.
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
async def test_4xx_does_not_count_as_breaker_failure():
    # 4xx means the MDA understood our request and rejected it — that's not a
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


# ---------------------------------------------------------------------------
# JWT claim ordering (B1).
# ---------------------------------------------------------------------------


def test_metadata_cannot_shadow_exp_or_body_hash():
    import jwt

    client, _ = _new_client()
    body = b"hello"
    # Attacker-supplied metadata tries to overwrite security fields.
    token = client._build_jwt(
        body, {"exp": 0, "body_hash": "deadbeef", "sender": "u@x"}
    )
    decoded = jwt.decode(token, client.secret, algorithms=["HS256"])
    # The real exp must be in the future, not 0.
    assert decoded["exp"] != 0
    # The real body_hash must be the sha256 of `body`, not "deadbeef".
    assert decoded["body_hash"] != "deadbeef"
    # Sender (non-conflicting metadata) survives.
    assert decoded["sender"] == "u@x"
