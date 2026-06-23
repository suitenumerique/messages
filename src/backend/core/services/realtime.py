"""Realtime fan-out: publish events to the relay's Redis bus + mint SSE tokens.

The Django side never holds a connection. It (1) mints a short-lived JWT the
browser uses to open its SSE stream against the relay, and (2) PUBLISHes thin
"something changed" events to Redis after the surrounding DB transaction
commits. The ``realtime-relay`` service (src/realtime/realtime_relay) does the rest.

Everything no-ops unless ``REALTIME_ENABLED`` is True, so this is safe to ship
dark. Keep published ``data`` thin — a hint to refetch, never message content
(the client refetches over the authenticated API).

Contract (channel naming, token claims) is documented in
``src/realtime/README.md`` and MUST stay in sync.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from django.conf import settings
from django.db import transaction

import jwt

from core.utils import get_redis_client

logger = logging.getLogger(__name__)

# Redis pub/sub key namespace. Hardcoded (not a setting): the relay must use the
# exact same prefix, so a config knob would only be a way to break delivery.
# Kept in sync with src/realtime/realtime_relay/hub.py.
REALTIME_CHANNEL_PREFIX = "rtchannel:"

# SSE connection-token lifetime (seconds). Hardcoded (not a setting): the token
# is used once to open the stream and re-minted on every reconnect, so it only
# needs to survive mint→connect. 60s is ample; longer would just widen the
# replay window for a token that necessarily rides in the URL.
TOKEN_TTL_SECONDS = 60


class RealtimeMisconfigured(Exception):
    """Realtime is enabled but a required setting (e.g. the JWT secret) is missing."""


def mint_connection_token(user_id: str, *, rooms: list[str] | None = None) -> str:
    """Mint a short-lived JWT authorizing an SSE connection for ``user_id``.

    ``rooms`` are the extra channels (beyond the implied ``user:<id>``) the
    connection may join — the caller is responsible for only listing rooms the
    user is authorized for, because the relay trusts this list verbatim.
    """
    secret = settings.REALTIME_JWT_SECRET
    if not secret:
        # Fail closed: signing with an empty key produces a token anyone can
        # forge. Refuse rather than hand out a worthless-but-dangerous token.
        raise RealtimeMisconfigured(
            "REALTIME_JWT_SECRET is empty; refusing to mint a connection token"
        )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    if rooms:
        payload["rooms"] = rooms
    return jwt.encode(payload, secret, algorithm=settings.REALTIME_JWT_ALGORITHM)


def publish(room: str, event: str, data: dict[str, Any] | None = None) -> None:
    """Publish an event to ``room``, via ``transaction.on_commit``.

    ``on_commit`` guarantees that a caller *inside* an atomic block doesn't
    notify subscribers before the rows they'll refetch are committed. When
    there is no open transaction (Django autocommit — e.g. the inbound pipeline
    tail, which runs after the message has already been committed), the callback
    simply fires immediately, which is correct there too. Either way the
    contract holds: never publish before the data is visible.

    Failures are swallowed — realtime is best-effort, the DB is source of truth
    and the client's polling fallback reconciles.
    """
    if not settings.REALTIME_ENABLED:
        return

    channel = f"{REALTIME_CHANNEL_PREFIX}{room}"
    body = json.dumps({"event": event, "data": data or {}}, separators=(",", ":"))

    def _do_publish() -> None:
        try:
            # Publish over the shared django_redis cache connection (same Redis
            # the relay psubscribes to) — no separate client/URL to keep in sync.
            get_redis_client().publish(channel, body)
        except Exception:
            logger.exception("realtime publish failed for channel=%s", channel)

    transaction.on_commit(_do_publish)


def notify_users(user_ids, event: str, data: dict[str, Any] | None = None) -> None:
    """Publish the same event to each user's personal channel."""
    for user_id in user_ids:
        publish(f"user:{user_id}", event, data)
