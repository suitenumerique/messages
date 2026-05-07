"""Tests for the Django system checks in ``core/checks.py``.

These checks validate ``MESSAGES_BLOBS_*`` settings at boot. They run
without DB or storage access, so the tests call the check functions
directly with ``override_settings``.
"""

# pylint: disable=protected-access,invalid-name
# Function names embed the error/warning code (E001, W003, …) for
# at-a-glance correlation with the check IDs they exercise; the
# uppercase letters trip pylint's snake_case rule, but renaming
# would lose that mapping.

import traceback

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

import pytest

from core.checks import (
    _ZSTD_MAX_LEVEL,
    _ZSTD_MIN_LEVEL,
    check_blob_compression_config,
    check_blob_encryption_config,
)
from core.utils import JSONValue


def _ids(issues):
    """Return the set of ``id`` strings on a list of system-check issues."""
    return {i.id for i in issues}


# --------------------------------------------------------------------
# check_blob_encryption_config
# --------------------------------------------------------------------


@override_settings(MESSAGES_BLOBS_ENCRYPT_KEYS={})
def test_check_encryption_empty_dict_passes():
    """No keys configured = no errors (encryption fully off)."""
    assert not check_blob_encryption_config(None)


@override_settings(MESSAGES_BLOBS_ENCRYPT_KEYS=["not", "a", "dict"])
def test_check_encryption_not_a_dict_yields_E001():
    """A non-dict value must be rejected as core.E001."""
    issues = check_blob_encryption_config(None)
    assert _ids(issues) == {"core.E001"}


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={"1": "shorthand-string-not-a-dict"},
)
def test_check_encryption_bad_entry_shape_yields_E002():
    """Shorthand strings are no longer accepted — must be the full
    {"algo", "secret", "active"} dict."""
    issues = check_blob_encryption_config(None)
    assert "core.E002" in _ids(issues)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "rot13", "secret": "x" * 32},
    },
)
def test_check_encryption_unknown_algo_yields_E002():
    """Unknown ``algo`` identifiers are rejected as core.E002."""
    issues = check_blob_encryption_config(None)
    assert "core.E002" in _ids(issues)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "aes-gcm", "secret": "a" * 32, "active": True},
        "2": {"algo": "aes-gcm", "secret": "b" * 32, "active": True},
    },
)
def test_check_encryption_two_active_yields_E003():
    """Exactly one (or zero) entry may be active; >1 is core.E003."""
    issues = check_blob_encryption_config(None)
    assert "core.E003" in _ids(issues)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "aes-gcm", "secret": "tooshort", "active": True},
    },
)
def test_check_encryption_short_secret_yields_W002():
    """Secrets shorter than the floor are flagged as core.W002."""
    issues = check_blob_encryption_config(None)
    assert "core.W002" in _ids(issues)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "aes-gcm", "secret": "x" * 32, "active": True},
    },
)
def test_check_encryption_green_path_no_issues():
    """One active entry, valid algo, secret >= 32 chars → silent."""
    assert not check_blob_encryption_config(None)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "aes-gcm", "secret": "x" * 32, "active": True},
        "2": {"algo": "aes-gcm", "secret": "y" * 32},  # passive (no active)
    },
)
def test_check_encryption_passive_entry_is_fine():
    """Multi-key dict with exactly one active is allowed (rotation
    setup)."""
    assert not check_blob_encryption_config(None)


@override_settings(
    MESSAGES_BLOBS_ENCRYPT_KEYS={
        "1": {"algo": "aes-gcm", "secret": "x" * 32, "active": "yes"},
    },
)
def test_check_encryption_non_bool_active_yields_E002():
    """``active`` must be a bool — strings are rejected (silently
    coercing to True would let an operator typo flip an entry on)."""
    issues = check_blob_encryption_config(None)
    assert "core.E002" in _ids(issues)


# --------------------------------------------------------------------
# check_blob_compression_config
# --------------------------------------------------------------------


@override_settings(MESSAGES_BLOBS_COMPRESS="zstd:7")
def test_check_compression_default_passes():
    """The default ``zstd:7`` is silent."""
    assert not check_blob_compression_config(None)


@override_settings(MESSAGES_BLOBS_COMPRESS="none")
def test_check_compression_none_passes():
    """Compression ``none`` is silent."""
    assert not check_blob_compression_config(None)


@override_settings(MESSAGES_BLOBS_COMPRESS="zstd")
def test_check_compression_zstd_no_level_passes():
    """``zstd`` without a level — pyzstd uses default; check is silent."""
    assert not check_blob_compression_config(None)


@override_settings(MESSAGES_BLOBS_COMPRESS="lz4:3")
def test_check_compression_unknown_algo_yields_E004():
    """Unknown algorithms are rejected as core.E004."""
    issues = check_blob_compression_config(None)
    assert "core.E004" in _ids(issues)


@override_settings(MESSAGES_BLOBS_COMPRESS=f"zstd:{_ZSTD_MAX_LEVEL + 1}")
def test_check_compression_level_above_max_yields_W003():
    """A level above the supported zstd ladder triggers core.W003."""
    issues = check_blob_compression_config(None)
    assert "core.W003" in _ids(issues)


@override_settings(MESSAGES_BLOBS_COMPRESS=f"zstd:{_ZSTD_MIN_LEVEL - 1}")
def test_check_compression_level_below_min_yields_W003():
    """A level below the supported zstd ladder triggers core.W003."""
    issues = check_blob_compression_config(None)
    assert "core.W003" in _ids(issues)


@override_settings(MESSAGES_BLOBS_COMPRESS=f"zstd:{_ZSTD_MIN_LEVEL}")
def test_check_compression_level_at_lower_boundary_passes():
    """The lower boundary is silent."""
    assert not check_blob_compression_config(None)


@override_settings(MESSAGES_BLOBS_COMPRESS=f"zstd:{_ZSTD_MAX_LEVEL}")
def test_check_compression_level_at_upper_boundary_passes():
    """The upper boundary is silent."""
    assert not check_blob_compression_config(None)


def test_check_compression_legacy_env_yields_W001(monkeypatch):
    """The deprecated ``MESSAGES_BLOBS_ZSTD_LEVEL`` env triggers a warning
    even if the new ``MESSAGES_BLOBS_COMPRESS`` is set correctly."""
    monkeypatch.setenv("MESSAGES_BLOBS_ZSTD_LEVEL", "5")
    issues = check_blob_compression_config(None)
    assert "core.W001" in _ids(issues)


# --------------------------------------------------------------------
# JSONValue malformed-input sanitization (B5 fix)
# --------------------------------------------------------------------


def test_jsonvalue_malformed_does_not_leak_secret():
    """``JSONValue.to_python`` raises ``ImproperlyConfigured`` with a
    sanitized message (no fragment of the input). Critical for env vars
    that carry secrets like ``MESSAGES_BLOBS_ENCRYPT_KEYS`` — a parse
    failure mustn't surface the secret in tracebacks, Sentry breadcrumbs,
    or pod logs.
    """
    secret_marker = "TOPSECRET_DO_NOT_LEAK_42"
    malformed = '{"1": "' + secret_marker + " (unterminated string..."
    # Bypass django-configurations' Value() construction which auto-resolves
    # to the underlying value type — we want the descriptor itself so we
    # can call ``to_python`` directly with a chosen input.
    v = JSONValue.__new__(JSONValue)
    v.environ_name = "MESSAGES_BLOBS_ENCRYPT_KEYS"

    with pytest.raises(ImproperlyConfigured) as exc_info:
        v.to_python(malformed)

    # Message must not contain any fragment of the input.
    msg = str(exc_info.value)
    assert secret_marker not in msg
    assert "MESSAGES_BLOBS_ENCRYPT_KEYS" in msg

    # Full traceback also must not contain the secret — the underlying
    # JSONDecodeError chain is suppressed via ``raise ... from None``.
    tb = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert secret_marker not in tb
    assert exc_info.value.__suppress_context__ is True
