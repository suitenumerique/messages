"""Tests for the realtime publish/token helpers (core.services.realtime)."""

import time
from unittest import mock

from django.test import override_settings

import jwt
import pytest

from core.services import realtime

pytestmark = pytest.mark.django_db


@override_settings(
    REALTIME_ENABLED=True,
    REALTIME_JWT_SECRET="s3cret",
    REALTIME_JWT_ALGORITHM="HS256",
)
def test_mint_connection_token_roundtrip():
    token = realtime.mint_connection_token("user-42", rooms=["thread:t1"])
    claims = jwt.decode(token, "s3cret", algorithms=["HS256"])
    assert claims["sub"] == "user-42"
    assert claims["rooms"] == ["thread:t1"]
    assert claims["exp"] > int(time.time())


@override_settings(REALTIME_ENABLED=True, REALTIME_JWT_SECRET="")
def test_mint_refuses_empty_secret():
    """Signing with an empty key yields a forgeable token — fail closed."""
    with pytest.raises(realtime.RealtimeMisconfigured):
        realtime.mint_connection_token("user-42")


@override_settings(REALTIME_ENABLED=False)
def test_publish_noops_when_disabled():
    with mock.patch("core.services.realtime.get_redis_client") as get_client:
        realtime.publish("user:1", "inbox.changed", {"x": 1})
        get_client.assert_not_called()


@override_settings(REALTIME_ENABLED=True)
def test_publish_emits_on_commit(django_capture_on_commit_callbacks):
    fake = mock.MagicMock()
    with mock.patch("core.services.realtime.get_redis_client", return_value=fake):
        with django_capture_on_commit_callbacks(execute=True):
            realtime.publish("user:1", "inbox.changed", {"mailbox": "m1"})
    fake.publish.assert_called_once()
    channel, body = fake.publish.call_args[0]
    assert channel == "rtchannel:user:1"
    assert '"event":"inbox.changed"' in body
    assert '"mailbox":"m1"' in body


@override_settings(REALTIME_ENABLED=True)
def test_notify_users_publishes_per_user(django_capture_on_commit_callbacks):
    fake = mock.MagicMock()
    with mock.patch("core.services.realtime.get_redis_client", return_value=fake):
        with django_capture_on_commit_callbacks(execute=True):
            realtime.notify_users(["u1", "u2"], "inbox.changed", {})
    channels = {c.args[0] for c in fake.publish.call_args_list}
    assert channels == {"rtchannel:user:u1", "rtchannel:user:u2"}
