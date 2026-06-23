"""ASGI entrypoint.

Routes:
  GET /realtime-relay/   — the SSE stream (auth via ``?token=<jwt>``)
  GET /__lbheartbeat__   — liveness (always 200), matches the repo convention
  GET /healthz           — readiness (verifies the Redis bus is connected)

Run: ``uvicorn realtime_relay.app:app --host 0.0.0.0 --port $PORT``.
HTTP/2 to the browser is provided by Caddy / the Scalingo router in front;
uvicorn only needs to speak HTTP/1.1 upstream.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .auth import AuthError, authenticate
from .config import Config
from .hub import Hub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("realtime_relay")

config = Config.from_env()
hub = Hub(config.redis_url, channel_prefix=config.channel_prefix)


async def lbheartbeat(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def healthz(_request: Request) -> JSONResponse:
    """Readiness: the relay is only useful if the bus is reachable AND the
    subscription reader is alive (a dead reader delivers nothing)."""
    try:
        await hub.ping()
    except Exception:
        return JSONResponse({"status": "error", "detail": "redis"}, status_code=503)
    if not hub.reader_alive():
        return JSONResponse({"status": "error", "detail": "reader"}, status_code=503)
    return JSONResponse({"status": "ok"})


class _SlotCountedEventSource(EventSourceResponse):
    """EventSourceResponse that releases its capacity slot when the ASGI call
    finishes — however it finishes.

    Starlette always awaits ``response(scope, receive, send)`` once a handler
    returns the response, and unwinds it (including on client disconnect /
    cancellation), so releasing in ``__call__``'s ``finally`` guarantees the
    slot is freed even if the body generator never starts. Releasing inside the
    generator instead would leak a slot in that (narrow) window.
    """

    def __init__(self, *args, hub: Hub, **kwargs):
        super().__init__(*args, **kwargs)
        self._hub = hub

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._hub.release()


async def events(request: Request) -> EventSourceResponse | JSONResponse:
    token = request.query_params.get("token")
    try:
        principal = authenticate(
            token, secret=config.jwt_secret, algorithm=config.jwt_algorithm
        )
    except AuthError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    # Reserve a slot synchronously here (cap check + acquire happen in one
    # event-loop turn with no await between them) so a burst can't overshoot the
    # cap. The matching release is tied to the response's __call__ below — NOT to
    # the body generator, which may never start if the client vanishes first.
    if config.max_connections and hub.active >= config.max_connections:
        return JSONResponse({"detail": "relay at capacity"}, status_code=503)

    logger.info("sse open user=%s rooms=%s", principal.user_id, principal.rooms)

    async def stream():
        # Subscribe *inside* the generator so the matching close() is guaranteed
        # to run in finally — even if the client disconnects before the first
        # iteration — instead of leaking a Subscription in the hub.
        subscription = hub.subscribe(principal.rooms)
        try:
            # First frame flushes headers through any proxy immediately and
            # tells the client which rooms it actually joined.
            yield {
                "event": "ready",
                "data": json.dumps({"rooms": list(principal.rooms)}),
            }
            while True:
                yield await subscription.queue.get()
        finally:
            subscription.close()
            logger.info("sse close user=%s", principal.user_id)

    response = _SlotCountedEventSource(
        stream(),
        hub=hub,
        ping=config.heartbeat_seconds,
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
    # Acquire only once the response object exists and nothing between here and
    # the return can fail, so every acquire is paired with the release that the
    # response's __call__ runs in its finally.
    hub.acquire()
    return response


@asynccontextmanager
async def lifespan(_app: Starlette):
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()


# CORS only when origins are configured (dev: the relay is a separate origin).
# Empty in prod, where Caddy serves the relay under the app's own origin.
middleware = (
    [
        Middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET"],
        )
    ]
    if config.cors_origins
    else []
)

app = Starlette(
    routes=[
        Route("/realtime-relay/", events),
        Route("/realtime-relay", events),
        Route("/__lbheartbeat__", lbheartbeat),
        Route("/__lbheartbeat__/", lbheartbeat),
        Route("/healthz", healthz),
    ],
    middleware=middleware,
    lifespan=lifespan,
)
