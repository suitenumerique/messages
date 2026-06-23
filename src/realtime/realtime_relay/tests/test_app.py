"""Endpoint + end-to-end SSE tests against the ASGI app."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from starlette.requests import Request

import realtime_relay.app as app_module
from realtime_relay.config import Config
from realtime_relay.hub import Hub

# Mirror of conftest constants (importlib import-mode can't `from conftest import`).
JWT_SECRET = "test-secret"
JWT_ALG = "HS256"
CHANNEL_PREFIX = "rtchannel:"


@pytest_asyncio.fixture
async def wired(monkeypatch, redis_factory):
    """Patch the app's module globals to a fake-Redis hub and start it."""
    test_config = Config(
        redis_url="redis://fake",
        jwt_secret=JWT_SECRET,
        jwt_algorithm=JWT_ALG,
        channel_prefix=CHANNEL_PREFIX,
        heartbeat_seconds=60,
        max_connections=0,
        cors_origins=(),
    )
    test_hub = Hub(
        "redis://fake", channel_prefix=CHANNEL_PREFIX, redis_factory=redis_factory
    )
    monkeypatch.setattr(app_module, "config", test_config)
    monkeypatch.setattr(app_module, "hub", test_hub)
    await test_hub.start()
    await asyncio.sleep(0.05)
    try:
        yield test_hub
    finally:
        await test_hub.stop()


@pytest_asyncio.fixture
async def client(wired):
    """A real ASGI HTTP client over the wired app (non-streaming endpoints)."""
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_lbheartbeat(client):
    resp = await client.get("/__lbheartbeat__")
    assert resp.status_code == 200
    assert resp.text == "ok"


async def test_healthz_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_events_without_token_is_401(client):
    resp = await client.get("/realtime-relay/")
    assert resp.status_code == 401


async def test_events_with_bad_token_is_401(client):
    resp = await client.get("/realtime-relay/?token=not-a-jwt")
    assert resp.status_code == 401


async def test_events_at_capacity_is_503(client, monkeypatch, make_token):
    # Cap at 1 and pretend the process is already full.
    monkeypatch.setattr(
        app_module,
        "config",
        app_module.config.__class__(
            redis_url="redis://fake",
            jwt_secret=JWT_SECRET,
            jwt_algorithm=JWT_ALG,
            channel_prefix=CHANNEL_PREFIX,
            heartbeat_seconds=60,
            max_connections=1,
            cors_origins=(),
        ),
    )
    monkeypatch.setattr(type(app_module.hub), "active", property(lambda self: 5))
    resp = await client.get(f"/realtime-relay/?token={make_token()}")
    assert resp.status_code == 503


def _make_request(token: str):
    """Minimal ASGI GET Request carrying ?token=…."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/realtime-relay/",
        "query_string": f"token={token}".encode(),
        "headers": [],
    }

    async def receive():
        return {"type": "http.request"}

    return Request(scope, receive)


async def _drive_sse(response, *, until: str, timeout: float, after_ready=None) -> str:
    """Run an EventSourceResponse as an ASGI app and collect its body.

    ``receive`` never returns http.disconnect, so the stream stays open like a
    real browser connection (httpx's ASGITransport injects a disconnect, which
    sse-starlette honours by closing early — hence driving it directly here).

    The subscription is created lazily inside the response generator, so a
    publisher must fire only *after* the ``ready`` frame appears — pass it as
    ``after_ready`` (awaited once).
    """
    chunks: list[str] = []
    release = asyncio.Event()
    fired = False

    async def receive():
        await release.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                chunks.append(body.decode())

    task = asyncio.create_task(response({"type": "http"}, receive, send))
    try:

        async def wait_marker():
            nonlocal fired
            while True:
                blob = "".join(chunks)
                if after_ready and not fired and "event: ready" in blob:
                    fired = True
                    await after_ready()
                if until in blob:
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_marker(), timeout)
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    return "".join(chunks)


async def test_sse_stream_delivers_published_event(wired, bus, make_token):
    response = await app_module.events(_make_request(make_token(sub="user-1")))
    assert response.status_code == 200
    assert "text/event-stream" in response.media_type

    async def publish():
        await bus.publish(
            f"{CHANNEL_PREFIX}user:user-1",
            json.dumps({"event": "inbox.changed", "data": {"mailbox": "m1"}}),
        )

    blob = await _drive_sse(
        response, until="inbox.changed", timeout=5, after_ready=publish
    )
    assert "event: ready" in blob
    assert "event: inbox.changed" in blob
    assert "m1" in blob


async def test_capacity_slot_acquired_then_released(wired, bus, make_token):
    """A slot is reserved when the response is built and freed when the ASGI
    call unwinds — even though the body generator may never start — so the cap
    can't leak toward a permanent 503."""
    hub = wired
    assert hub.active == 0
    response = await app_module.events(_make_request(make_token(sub="user-1")))
    # acquire() ran synchronously while building the response.
    assert hub.active == 1
    # Drive the stream to the ready frame, then let _drive_sse cancel it; the
    # response's __call__ finally must release the slot.
    await _drive_sse(response, until="event: ready", timeout=5)
    assert hub.active == 0


async def test_sse_room_isolation(wired, bus, make_token):
    """A connection joined only to user:user-1 must not see another user's events."""
    response = await app_module.events(_make_request(make_token(sub="user-1")))

    async def publish_other():
        await bus.publish(
            f"{CHANNEL_PREFIX}user:other",
            json.dumps({"event": "inbox.changed", "data": {}}),
        )

    # Published (to another room) only after we're subscribed, so a timeout
    # genuinely proves room isolation rather than a publish-before-subscribe race.
    with pytest.raises(asyncio.TimeoutError):
        await _drive_sse(
            response, until="inbox.changed", timeout=1.0, after_ready=publish_other
        )
