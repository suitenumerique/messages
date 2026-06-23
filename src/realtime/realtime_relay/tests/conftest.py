"""Shared fixtures: an in-memory Redis (fakeredis) and a token minter.

The hub takes an injectable ``redis_factory`` so every test runs against a
single shared FakeServer — the test publisher and the hub's subscriber see
the same pub/sub state without a real Redis.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

JWT_SECRET = "test-secret"
JWT_ALG = "HS256"
CHANNEL_PREFIX = "rtchannel:"


@pytest.fixture
def fake_server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def redis_factory(fake_server):
    def factory(_url: str):
        return FakeRedis(server=fake_server, decode_responses=True)

    return factory


@pytest.fixture
def bus(fake_server) -> FakeRedis:
    """A publisher client on the same server the hub subscribes to."""
    return FakeRedis(server=fake_server, decode_responses=True)


@pytest.fixture
def make_token():
    def _make(sub: str = "user-1", rooms=None, ttl: int = 60, secret: str = JWT_SECRET):
        payload = {"sub": sub, "exp": int(time.time()) + ttl}
        if rooms is not None:
            payload["rooms"] = rooms
        return jwt.encode(payload, secret, algorithm=JWT_ALG)

    return _make
