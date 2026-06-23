"""Connection-token verification."""

from __future__ import annotations

import time

import jwt
import pytest

from realtime_relay.auth import AuthError, authenticate

# Mirror of conftest constants (importlib import-mode can't `from conftest import`).
JWT_SECRET = "test-secret"
JWT_ALG = "HS256"


def _auth(token):
    return authenticate(token, secret=JWT_SECRET, algorithm=JWT_ALG)


def test_valid_token_includes_own_user_room(make_token):
    principal = _auth(make_token(sub="abc"))
    assert principal.user_id == "abc"
    assert principal.rooms == ("user:abc",)


def test_extra_rooms_are_appended(make_token):
    principal = _auth(make_token(sub="abc", rooms=["thread:t1", "thread:t2"]))
    assert principal.rooms == ("user:abc", "thread:t1", "thread:t2")


def test_user_room_always_present_even_if_not_listed(make_token):
    # Backend lists only thread rooms; the owner channel is still implied.
    principal = _auth(make_token(sub="abc", rooms=["thread:t1"]))
    assert "user:abc" in principal.rooms


def test_duplicate_and_bad_rooms_are_ignored(make_token):
    principal = _auth(make_token(sub="abc", rooms=["user:abc", "", 123, "thread:t1"]))
    assert principal.rooms == ("user:abc", "thread:t1")


def test_missing_token_rejected():
    with pytest.raises(AuthError):
        _auth(None)


def test_unconfigured_secret_fails_closed(make_token):
    with pytest.raises(AuthError):
        authenticate(make_token(), secret="", algorithm=JWT_ALG)


def test_bad_signature_rejected(make_token):
    with pytest.raises(AuthError):
        _auth(make_token(secret="wrong-secret"))


def test_expired_token_rejected():
    token = jwt.encode(
        {"sub": "abc", "exp": int(time.time()) - 10}, JWT_SECRET, algorithm=JWT_ALG
    )
    with pytest.raises(AuthError):
        _auth(token)


def test_token_without_sub_rejected():
    token = jwt.encode({"exp": int(time.time()) + 60}, JWT_SECRET, algorithm=JWT_ALG)
    with pytest.raises(AuthError):
        _auth(token)
