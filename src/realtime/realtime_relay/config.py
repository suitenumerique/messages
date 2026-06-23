"""Environment-driven configuration.

Everything the relay needs comes from env vars so it deploys identically as
a Scalingo process. Kept as a frozen dataclass loaded once at import so the
app and the tests share one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Redis bus shared with the Django backend. The backend PUBLISHes events;
    # the relay PSUBSCRIBEs. Same instance as the Celery broker is fine.
    redis_url: str

    # Symmetric secret the Django backend signs connection tokens with. The
    # relay only ever *verifies* — it never mints. MUST match the backend's
    # ``REALTIME_JWT_SECRET``.
    jwt_secret: str
    jwt_algorithm: str

    # Redis pub/sub key space. The backend publishes to ``{prefix}{room}``
    # (e.g. ``rtchannel:user:<uuid>``); the relay psubscribes to ``{prefix}*``
    # and routes in-process to the connections that joined ``room``. Hardcoded
    # (not env): MUST match the backend's REALTIME_CHANNEL_PREFIX constant.
    channel_prefix: str

    # Seconds between SSE heartbeat comments. Scalingo's router cuts a stream
    # that sends no bytes for its idle window (~30s initially), so we keep the
    # heartbeat well under that; 15s (the default) is a comfortable margin.
    heartbeat_seconds: int

    # Hard cap on concurrent SSE connections this process will hold, as a
    # crude backpressure valve (0 = unlimited).
    max_connections: int

    # Allowed CORS origins for the SSE endpoint. Empty (default) = no CORS
    # headers, i.e. same-origin only (the prod setup, where Caddy serves the
    # relay under the app's own origin). In dev the relay is a separate origin
    # (its own container/port), so set this to the frontend origin.
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Config":
        raw_origins = os.environ.get("REALTIME_CORS_ORIGINS", "")
        return cls(
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379"),
            jwt_secret=os.environ.get("REALTIME_JWT_SECRET", ""),
            jwt_algorithm=os.environ.get("REALTIME_JWT_ALGORITHM", "HS256"),
            channel_prefix="rtchannel:",
            heartbeat_seconds=int(os.environ.get("REALTIME_HEARTBEAT", "15")),
            max_connections=int(os.environ.get("REALTIME_MAX_CONNECTIONS", "0")),
            cors_origins=tuple(o.strip() for o in raw_origins.split(",") if o.strip()),
        )
