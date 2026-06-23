"""In-process fan-out backed by a single Redis subscription.

One ``Hub`` per relay process opens ONE Redis ``psubscribe`` on the whole
event keyspace and routes each message in-memory to the connections that
joined the matching room. This is the key scalability choice: N connected
clients share one Redis subscription, not one each.

Channel naming: the Django backend ``PUBLISH``es to ``<prefix><room>`` (e.g.
``rtchannel:user:<uuid>`` or ``rtchannel:thread:<uuid>``). The relay psubscribes to
``<prefix>*`` and strips the prefix to recover ``room``.

A published message is a JSON object ``{"event": str, "data": any, "id"?: str}``.
We pass ``event``/``data``/``id`` straight through to SSE; a non-JSON payload
is forwarded as a raw ``message`` event so the bus stays debuggable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger("realtime_relay.hub")

# Per-connection buffer. If a client can't keep up we drop events rather than
# stall the shared reader or grow memory — the DB stays source of truth and
# the client's polling fallback reconciles. Small on purpose.
_QUEUE_MAXSIZE = 100


class Subscription:
    """A single connection's view onto one or more rooms.

    Holds a bounded queue the Hub pushes matching events into and the SSE
    handler drains. ``close()`` detaches it from every room.
    """

    def __init__(self, hub: "Hub", rooms: tuple[str, ...]):
        self._hub = hub
        self.rooms = rooms
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(_QUEUE_MAXSIZE)
        self.dropped = 0

    def deliver(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning(
                "subscriber queue full; dropping event (rooms=%s)", self.rooms
            )

    def close(self) -> None:
        self._hub.detach(self)


class Hub:
    """Process-wide event router.

    Lifecycle: ``await start()`` once at app startup, ``await stop()`` at
    shutdown. ``subscribe(rooms)`` / ``Subscription.close()`` per connection.
    """

    def __init__(self, redis_url: str, *, channel_prefix: str, redis_factory=None):
        self._redis_url = redis_url
        self._prefix = channel_prefix
        # Injectable so tests can supply a fake; defaults to a real client.
        self._redis_factory = redis_factory or (
            lambda url: redis.from_url(url, decode_responses=True)
        )
        self._rooms: dict[str, set[Subscription]] = {}
        self._redis: Any | None = None
        self._pubsub: Any | None = None
        self._reader: asyncio.Task | None = None
        self._closing = False
        # Accepted-connection counter for the capacity cap. Incremented when a
        # connection is accepted (before it subscribes) and decremented when its
        # stream ends, so the cap can't be overshot by a burst that hasn't
        # reached subscribe() yet. Distinct from connection_count (subscriptions).
        self._active = 0

    @property
    def connection_count(self) -> int:
        return len({s for subs in self._rooms.values() for s in subs})

    @property
    def active(self) -> int:
        return self._active

    def acquire(self) -> None:
        self._active += 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)

    async def start(self) -> None:
        self._closing = False
        self._redis = self._redis_factory(self._redis_url)
        # The reader owns the subscription and re-establishes it on failure, so
        # a Redis blip/failover doesn't silently stop event delivery forever.
        self._reader = asyncio.create_task(self._run(), name="hub-reader")
        logger.info("hub started (psubscribe %s*)", self._prefix)

    async def stop(self) -> None:
        self._closing = True
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            await self._pubsub.aclose()
        if self._redis is not None:
            await self._redis.aclose()
        logger.info("hub stopped")

    def reader_alive(self) -> bool:
        """Whether the subscription reader task is running (for readiness)."""
        return self._reader is not None and not self._reader.done()

    async def ping(self) -> None:
        """Round-trip the Redis bus (for readiness). Raises if unreachable."""
        if self._redis is None:
            raise RuntimeError("hub not started")
        await self._redis.ping()

    def subscribe(self, rooms: tuple[str, ...]) -> Subscription:
        sub = Subscription(self, rooms)
        for room in rooms:
            self._rooms.setdefault(room, set()).add(sub)
        return sub

    def detach(self, sub: Subscription) -> None:
        for room in sub.rooms:
            holders = self._rooms.get(room)
            if not holders:
                continue
            holders.discard(sub)
            if not holders:
                del self._rooms[room]

    def _room_of(self, channel: str) -> str:
        return (
            channel[len(self._prefix) :]
            if channel.startswith(self._prefix)
            else channel
        )

    async def _run(self) -> None:
        """Supervise the subscription: (re)subscribe, read, and reconnect with
        backoff on any failure. Runs until ``stop()`` sets ``_closing``."""
        # Seconds a connection must stay up before we treat it as healthy and
        # reset the backoff. Resetting right after psubscribe would busy-loop at
        # 1s when Redis flaps (subscribe succeeds, the read fails immediately);
        # a few seconds is enough to rule that out while still recovering quickly
        # once a flapping bus settles.
        STABLE_SECONDS = 5
        backoff = 1
        while not self._closing:
            connected_at = None
            try:
                self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
                await self._pubsub.psubscribe(f"{self._prefix}*")
                logger.info("hub subscribed (%s*)", self._prefix)
                connected_at = time.monotonic()
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hub reader failed; reconnecting in %ss", backoff)
            finally:
                if self._pubsub is not None:
                    try:
                        await self._pubsub.aclose()
                    except Exception:
                        logger.debug(
                            "pubsub aclose failed during reconnect", exc_info=True
                        )
                    self._pubsub = None
            if self._closing:
                break
            # Only credit a clean reconnect once the connection actually held;
            # otherwise keep escalating so a flapping bus isn't hammered.
            if (
                connected_at is not None
                and time.monotonic() - connected_at >= STABLE_SECONDS
            ):
                backoff = 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _read_loop(self) -> None:
        if self._pubsub is None:
            return
        async for message in self._pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            room = self._room_of(message["channel"])
            holders = self._rooms.get(room)
            if not holders:
                continue
            event = self._parse(message["data"])
            for sub in tuple(holders):
                sub.deliver(event)

    @staticmethod
    def _parse(raw: str) -> dict[str, Any]:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return {"event": "message", "data": raw}
        if not isinstance(obj, dict):
            return {"event": "message", "data": raw}
        out: dict[str, Any] = {
            "event": obj.get("event", "message"),
            "data": json.dumps(obj.get("data", {}), separators=(",", ":")),
        }
        if obj.get("id") is not None:
            out["id"] = str(obj["id"])
        return out
