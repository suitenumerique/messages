"""Env-var parsing contracts in :mod:`pymta.settings`.

The settings module runs once at import, so a bad value has to fail there,
loudly, rather than surfacing later as strange SMTP behaviour.
"""

from __future__ import annotations

import pytest

from pymta import settings
from pymta.settings import _env_bool, _env_int, _env_str, _env_token


def test_int_reads_env_over_default(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "42")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 42


def test_int_falls_back_on_unset_and_blank(monkeypatch):
    monkeypatch.delenv("PYMTA_TEST_INT", raising=False)
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 7
    monkeypatch.setenv("PYMTA_TEST_INT", "   ")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 7


def test_int_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "soon")
    with pytest.raises(ValueError, match="must be an integer"):
        _env_int("PYMTA_TEST_INT", 7, minimum=1)


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_int_rejects_values_below_the_minimum(monkeypatch, raw):
    # The settings that treat 0 as "disabled" declare minimum=0; everywhere
    # else 0 is nonsense that would otherwise fail silently, e.g. a
    # PYMTA_MAX_RECIPIENTS of 0 would 452 every recipient.
    monkeypatch.setenv("PYMTA_TEST_INT", raw)
    with pytest.raises(ValueError, match="must be >= 1"):
        _env_int("PYMTA_TEST_INT", 7, minimum=1)


def test_data_timeout_must_exceed_the_reply_reserve(monkeypatch):
    # The handler subtracts REPLY_RESERVE_SECONDS from this budget. A DATA
    # timeout at or below it leaves the deliver call nothing, which would defer
    # every message rather than fail loudly at startup.
    monkeypatch.setenv("PYMTA_TEST_INT", str(settings.REPLY_RESERVE_SECONDS))
    with pytest.raises(ValueError, match="must be >="):
        _env_int("PYMTA_TEST_INT", 300, minimum=settings.REPLY_RESERVE_SECONDS + 1)
    assert settings.PYMTA_DATA_TIMEOUT > settings.REPLY_RESERVE_SECONDS


def test_int_allows_zero_where_it_means_disabled(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "0")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=0) == 0


@pytest.mark.parametrize("raw", ["\r\n220 you are welcome here", "a\tb"])
def test_token_rejects_control_characters(monkeypatch, raw):
    # These land in the "220 {hostname} {ident}" banner; a CR/LF would append
    # attacker-chosen lines to our own greeting, and a TAB folds if the value
    # reaches a Received header. (NUL is also rejected but cannot be tested
    # through the environment: putenv refuses to store one.)
    monkeypatch.setenv("PYMTA_TEST_TOKEN", raw)
    with pytest.raises(ValueError, match="control characters"):
        _env_token("PYMTA_TEST_TOKEN", "mta-in")


def test_token_checks_the_default_too(monkeypatch):
    # PYMTA_HOSTNAME defaults to $MYHOSTNAME, which on k8s can come from the
    # downward API, so the fallback value needs the same check as the direct one.
    monkeypatch.delenv("PYMTA_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="control characters"):
        _env_token("PYMTA_TEST_TOKEN", "bad\r\nvalue")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_bool_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("PYMTA_TEST_BOOL", raw)
    assert _env_bool("PYMTA_TEST_BOOL", not expected) is expected


def test_bool_unrecognised_value_is_refused(monkeypatch):
    # A typo must not read as its opposite: PYMTA_ENABLE_PROXY_PROTOCOL=Ture
    # silently disabling PROXY protocol is a security-relevant misconfiguration.
    monkeypatch.setenv("PYMTA_TEST_BOOL", "maybe")
    with pytest.raises(ValueError, match="not a recognised boolean"):
        _env_bool("PYMTA_TEST_BOOL", True)
    with pytest.raises(ValueError, match="not a recognised boolean"):
        _env_bool("PYMTA_TEST_BOOL", False)


def test_bool_blank_and_missing_take_the_default(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_BOOL", "   ")
    assert _env_bool("PYMTA_TEST_BOOL", True) is True
    monkeypatch.delenv("PYMTA_TEST_BOOL", raising=False)
    assert _env_bool("PYMTA_TEST_BOOL", False) is False


def test_str_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_STR", "")
    assert _env_str("PYMTA_TEST_STR", "fallback") == "fallback"
