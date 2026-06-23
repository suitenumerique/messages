"""Hub fan-out: one Redis subscription routed in-process to rooms."""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from realtime_relay.hub import Hub, Subscription

CHANNEL_PREFIX = (
    "rtchannel:"  # mirror of conftest (importlib can't `from conftest import`)
)


@pytest_asyncio.fixture
async def running_hub(redis_factory):
    hub = Hub(
        "redis://fake", channel_prefix=CHANNEL_PREFIX, redis_factory=redis_factory
    )
    await hub.start()
    await asyncio.sleep(0.05)  # let the reader task spin up
    try:
        yield hub
    finally:
        await hub.stop()


async def _next(sub, timeout=2.0):
    return await asyncio.wait_for(sub.queue.get(), timeout)


async def _publish(bus, room, payload):
    await bus.publish(f"{CHANNEL_PREFIX}{room}", json.dumps(payload))
    await asyncio.sleep(0)  # yield to the reader loop


async def test_event_delivered_to_subscribed_room(running_hub, bus):
    sub = running_hub.subscribe(("user:abc",))
    await _publish(bus, "user:abc", {"event": "inbox.changed", "data": {"n": 1}})
    event = await _next(sub)
    assert event["event"] == "inbox.changed"
    assert json.loads(event["data"]) == {"n": 1}


async def test_event_not_delivered_to_other_room(running_hub, bus):
    sub = running_hub.subscribe(("user:abc",))
    await _publish(bus, "user:other", {"event": "x", "data": {}})
    with pytest.raises(asyncio.TimeoutError):
        await _next(sub, timeout=0.3)


async def test_id_passed_through(running_hub, bus):
    sub = running_hub.subscribe(("user:abc",))
    await _publish(bus, "user:abc", {"event": "e", "data": {}, "id": 42})
    event = await _next(sub)
    assert event["id"] == "42"


async def test_non_json_payload_forwarded_as_message(running_hub, bus):
    sub = running_hub.subscribe(("user:abc",))
    await bus.publish(f"{CHANNEL_PREFIX}user:abc", "plain-text")
    event = await _next(sub)
    assert event == {"event": "message", "data": "plain-text"}


async def test_multiple_subscribers_same_room_all_receive(running_hub, bus):
    a = running_hub.subscribe(("thread:t1",))
    b = running_hub.subscribe(("thread:t1",))
    await _publish(bus, "thread:t1", {"event": "viewing", "data": {"u": "x"}})
    ea, eb = await _next(a), await _next(b)
    assert ea["event"] == eb["event"] == "viewing"


async def test_connection_joining_multiple_rooms(running_hub, bus):
    sub = running_hub.subscribe(("user:abc", "thread:t1"))
    await _publish(bus, "thread:t1", {"event": "viewing", "data": {}})
    assert (await _next(sub))["event"] == "viewing"
    await _publish(bus, "user:abc", {"event": "inbox.changed", "data": {}})
    assert (await _next(sub))["event"] == "inbox.changed"


async def test_close_detaches_from_all_rooms(running_hub, bus):
    sub = running_hub.subscribe(("user:abc", "thread:t1"))
    assert running_hub.connection_count == 1
    sub.close()
    assert running_hub.connection_count == 0
    await _publish(bus, "user:abc", {"event": "e", "data": {}})
    with pytest.raises(asyncio.TimeoutError):
        await _next(sub, timeout=0.3)


def test_active_counter_tracks_acquire_release():
    # The capacity slot counter is independent of subscriptions: it is reserved
    # synchronously at accept time so a burst can't overshoot max_connections.
    hub = Hub("redis://fake", channel_prefix=CHANNEL_PREFIX)
    assert hub.active == 0
    hub.acquire()
    hub.acquire()
    assert hub.active == 2
    hub.release()
    assert hub.active == 1
    # Never goes negative even on an extra release.
    hub.release()
    hub.release()
    assert hub.active == 0


def test_deliver_drops_when_queue_full():
    # Direct unit test of backpressure — no event loop needed.
    sub = Subscription(hub=None, rooms=("user:abc",))
    for i in range(sub.queue.maxsize):
        sub.deliver({"event": "e", "data": str(i)})
    assert sub.dropped == 0
    sub.deliver({"event": "overflow", "data": "x"})
    assert sub.dropped == 1
