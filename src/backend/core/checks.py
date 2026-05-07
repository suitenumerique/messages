"""Django system checks for the core app.

Run automatically by ``manage.py check`` and at server startup; surface
configuration mistakes before they manifest as runtime errors.
"""

# pylint: disable=unused-argument

import os

from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning

from core.enums import CompressionTypeChoices, parse_compression_spec
from core.services.tiered_storage import (
    MIN_KEY_LEN,
    decode_key,
    normalize_key_entry,
)

# Valid zstd compression-level range. Negative levels (-7..-1) are
# zstd's "fast" tier; 1..22 is the standard ladder, with 22 the
# maximum-effort setting. Outside this range, pyzstd raises a cryptic
# error at first blob write — so we surface it at boot as a warning
# (not an Error: pyzstd is the source of truth and might widen its
# range in a future version; we don't want to falsely block startup).
_ZSTD_MIN_LEVEL = -7
_ZSTD_MAX_LEVEL = 22


@register()
def check_blob_compression_config(app_configs, **kwargs):
    """``MESSAGES_BLOBS_COMPRESS`` must parse as ``"<algo>"`` or ``"<algo>:<level>"``."""
    value = settings.MESSAGES_BLOBS_COMPRESS
    issues = []
    try:
        compression, level = parse_compression_spec(value)
    except (ValueError, AttributeError) as e:
        issues.append(
            Error(
                f"MESSAGES_BLOBS_COMPRESS={value!r} is invalid: {e}",
                hint='Use "none", "zstd", or "zstd:<level>".',
                id="core.E004",
            )
        )
    else:
        if (
            compression == CompressionTypeChoices.ZSTD
            and level is not None
            and not _ZSTD_MIN_LEVEL <= level <= _ZSTD_MAX_LEVEL
        ):
            issues.append(
                CheckWarning(
                    f"MESSAGES_BLOBS_COMPRESS={value!r}: zstd level {level} is "
                    f"outside the valid range "
                    f"[{_ZSTD_MIN_LEVEL}, {_ZSTD_MAX_LEVEL}]. "
                    "pyzstd will reject it at the first blob write.",
                    hint=f"Use a level in [{_ZSTD_MIN_LEVEL}, {_ZSTD_MAX_LEVEL}] "
                    "(typical: 3 for fast, 7 for balanced, 22 for max).",
                    id="core.W003",
                )
            )

    if "MESSAGES_BLOB_ZSTD_LEVEL" in os.environ:
        issues.append(
            CheckWarning(
                "MESSAGES_BLOB_ZSTD_LEVEL is deprecated and ignored.",
                hint='Set MESSAGES_BLOBS_COMPRESS="zstd:<level>" instead.',
                id="core.W001",
            )
        )
    return issues


@register()
def check_blob_encryption_config(app_configs, **kwargs):
    """Validate the tiered-storage blob encryption settings.

    - ``MESSAGES_BLOBS_ENCRYPT_KEYS`` must be a dict (or empty/None).
    - Every entry must be the explicit
      ``{"algo": ..., "secret": ..., "active": <bool>}`` shape with a
      known algo and a decodeable secret.
    - At most one entry may have ``active=true``. The active entry, if
      any, is the key new blobs encrypt with; inactive entries remain
      readable for legacy ciphertext.
    """
    errors = []
    keys = settings.MESSAGES_BLOBS_ENCRYPT_KEYS or {}

    if not isinstance(keys, dict):
        errors.append(
            Error(
                "MESSAGES_BLOBS_ENCRYPT_KEYS must be a JSON object "
                f"(got {type(keys).__name__}).",
                id="core.E001",
            )
        )
        return errors

    active_ids = []
    for key_id, key_value in keys.items():
        try:
            entry = normalize_key_entry(key_value)
            decode_key(entry["secret"])
        except Exception as e:  # pylint: disable=broad-except
            errors.append(
                Error(
                    f"MESSAGES_BLOBS_ENCRYPT_KEYS[{key_id!r}] is invalid: {e}",
                    id="core.E002",
                )
            )
            continue
        if entry["active"]:
            active_ids.append(key_id)
        secret = entry["secret"]
        if isinstance(secret, str) and len(secret) < MIN_KEY_LEN:
            errors.append(
                CheckWarning(
                    f"MESSAGES_BLOBS_ENCRYPT_KEYS[{key_id!r}] secret is shorter "
                    f"than {MIN_KEY_LEN} chars — likely low-entropy.",
                    hint="Generate with `openssl rand -base64 32` or similar.",
                    id="core.W002",
                )
            )

    if len(active_ids) > 1:
        errors.append(
            Error(
                f"MESSAGES_BLOBS_ENCRYPT_KEYS has {len(active_ids)} entries "
                f"flagged active=true (key_ids: {sorted(active_ids)}). "
                "Exactly one (or zero) entry may be active.",
                hint="Set active=true on a single entry; remove the flag "
                "(or set active=false) on the others.",
                id="core.E003",
            )
        )

    return errors


@register()
def check_blob_lifecycle_redis(app_configs, **kwargs):
    """Refuse to boot when the blob GC candidate set needs Redis but
    the default cache isn't django_redis.

    The fast-mode GC drains a Redis set that ``post_delete`` signals
    SADD blob_ids into. Other Django cache backends (LocMem,
    FileBased, Dummy) can't deliver SADD/SPOP atomicity across
    workers; without them the candidate set is a no-op and orphan
    blobs accumulate between manual ``--full`` sweeps.

    With offload enabled the consequences are operational data loss
    (orphans in S3 keep growing), so we hard-error. Without offload
    the failure mode is silent accumulation in PG, which we surface
    as a warning so dev / test environments without Redis still boot.
    """
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "django_redis" in backend:
        return []

    if settings.MESSAGES_BLOBS_OFFLOAD_ENABLED:
        return [
            Error(
                "MESSAGES_BLOBS_OFFLOAD_ENABLED=True requires django_redis "
                f"as the default cache backend (got {backend!r}). Without it, "
                "the GC candidate set drops every id and orphans accumulate "
                "in object storage between manual `--full` sweeps.",
                hint="Configure CACHES['default'] to use "
                "'django_redis.cache.RedisCache'.",
                id="core.E005",
            )
        ]

    return [
        CheckWarning(
            f"CACHES['default'] is not django_redis ({backend!r}); the blob "
            "GC candidate set is a no-op and orphan blobs will accumulate. "
            "Acceptable in dev/test, never in production.",
            hint="Configure CACHES['default'] to use "
            "'django_redis.cache.RedisCache', or rely on periodic "
            "`gc_orphan_blobs_task(mode='full')` runs to catch orphans.",
            id="core.W004",
        )
    ]
