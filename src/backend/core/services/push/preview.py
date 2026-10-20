"""Notification previews (sender + subject) that only the device can read.

The thin payload alone can only render a generic "new message" banner. A preview
lets the device show *who* wrote and *about what* without putting readable
content on the push transport:

- **Web Push** is already end-to-end encrypted (RFC 8291): the browser holds the
  private key, the push service only relays ciphertext. The preview is therefore
  added in clear to the payload and encrypted by :func:`webpush.send_webpush`.
- **APNs / FCM** relay their payload readable by Apple/Google. The native app
  registers its own P-256 public key + auth secret (the same shape as a Web Push
  subscription), and the preview is sealed for it with the very same aes128gcm
  scheme. Only the ciphertext (``enc``) and the id of the key it was sealed for
  (``kid``) travel; the Notification Service Extension (iOS) / messaging service
  (Android) decrypts it locally and falls back to the generic banner on failure.

The preview is built at *send* time by the per-device task, never by the
orchestrator: task arguments go through the broker and are persisted by the
result backend (``CELERY_RESULT_EXTENDED``), so they must stay content-free.

Whether a device may show content at all is decided by :func:`preview_allowed`:
a browser only while the session that enrolled it is alive (shared computers),
a phone only while its owner opened the app recently (lost devices).
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from logging import getLogger

from django.conf import settings
from django.core.cache import caches
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import http_ece
from cryptography.hazmat.primitives.asymmetric import ec

from core import models
from core.enums import PushPlatformChoices

logger = getLogger(__name__)

PREVIEW_SENDER_MAX_CHARS = 100
PREVIEW_SUBJECT_MAX_CHARS = 200

# Serialized payloads are padded with trailing whitespace (valid JSON) up to the
# first bucket that fits, so the ciphertext length the gateway sees does not
# leak the length of the sender or the subject. The largest bucket holds a
# worst-case preview (every truncated character 4 bytes in UTF-8: ~1.2 KB) plus
# the routing ids, and stays within the 4 KB APNs / FCM / Web Push limits once
# sealed (+103 bytes) and base64url-encoded (+33%).
PAD_BUCKETS = (512, 1024, 2048)

P256_UNCOMPRESSED_POINT_LENGTH = 65
AUTH_SECRET_LENGTH = 16


def b64url_decode(value: str) -> bytes:
    """Decode a base64url value tolerating missing padding."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(raw: bytes) -> str:
    """Encode ``raw`` as unpadded base64url."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def validate_device_keys(keys: dict) -> None:
    """Raise ``ValueError`` unless ``keys`` is a usable native key pair.

    Native clients are ours, so unlike browser subscriptions we can afford a
    strict check: a malformed key would otherwise only surface as previews that
    silently never decrypt on the device.
    """
    p256dh = b64url_decode(keys["p256dh"])
    auth = b64url_decode(keys["auth"])
    if len(p256dh) != P256_UNCOMPRESSED_POINT_LENGTH:
        raise ValueError("p256dh must be an uncompressed P-256 point")
    if len(auth) != AUTH_SECRET_LENGTH:
        raise ValueError("auth must be 16 bytes")
    # Raises ValueError when the bytes are not a point on the curve.
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)


def key_id(p256dh: str) -> str:
    """Short, stable id of a device public key.

    Sent in clear next to ``enc`` so a device that rotated its key pair knows
    which of its private keys sealed the preview. aes128gcm's own ``keyid``
    header field cannot carry it: it holds the sender's ephemeral public key.
    """
    return b64url_encode(hashlib.sha256(b64url_decode(p256dh)).digest()[:8])


def pad_json(data: dict) -> bytes:
    """Serialize ``data`` and pad it to the next :data:`PAD_BUCKETS` size."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for bucket in PAD_BUCKETS:
        if len(raw) <= bucket:
            return raw + b" " * (bucket - len(raw))
    return raw


def build_preview(message: models.Message) -> dict:
    """The content a device may show for ``message``: sender and subject."""
    sender = message.sender
    return {
        "sender": (sender.name or sender.email or "")[:PREVIEW_SENDER_MAX_CHARS],
        "subject": (message.subject or "")[:PREVIEW_SUBJECT_MAX_CHARS],
    }


def encrypt_for_device(plaintext: bytes, keys: dict) -> bytes:
    """Encrypt ``plaintext`` for the device holding ``keys`` (RFC 8291 aes128gcm).

    The Web Push message encryption, used as is for native previews. A fresh
    ephemeral server key is generated per call, so there is no server key to
    manage or rotate.
    """
    return http_ece.encrypt(
        plaintext,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=b64url_decode(keys["p256dh"]),
        auth_secret=b64url_decode(keys["auth"]),
        version="aes128gcm",
    )


def session_expires_at(session) -> datetime | None:
    """When ``session`` actually expires, or ``None`` without a session.

    The cache session backend keeps no expiry of its own: the key simply
    vanishes ``SESSION_COOKIE_AGE`` after the session was last *saved*.
    ``get_expiry_date()`` would answer "now + age", overshooting by up to a full
    age when the session was saved long ago, so the backing cache's TTL is
    preferred when it exposes one (django-redis does). The TTL is read before
    this request saves the session, so the answer errs on the early side.
    """
    if session is None or not getattr(session, "session_key", None):
        return None
    cache_key = getattr(session, "cache_key", None)
    ttl_reader = getattr(caches[settings.SESSION_CACHE_ALIAS], "ttl", None)
    if cache_key and ttl_reader:
        try:
            ttl = ttl_reader(cache_key)
        except Exception:  # pylint: disable=broad-exception-caught
            # The registration must not fail over a best-effort precision.
            ttl = None
        if isinstance(ttl, int) and ttl > 0:
            return timezone.now() + timedelta(seconds=ttl)
    return session.get_expiry_date()


def preview_allowed(channel: models.Channel) -> bool:
    """Whether ``channel``'s device may receive the message content.

    - Web: only until the session that registered the browser expires. A
      shared computer must not keep displaying a former user's subjects; the
      browser re-registers (and so refreshes the stamp) on every app load.
    - Native: only while the app was opened with a valid session (which
      re-registers the device) within ``PUSH_PREVIEW_MAX_IDLE_DAYS``.

    :func:`with_preview` additionally requires the device's key pair.
    """
    if not settings.PUSH_PREVIEWS_ENABLED:
        return False
    channel_settings = channel.settings or {}
    now = timezone.now()
    if channel_settings.get("platform") == PushPlatformChoices.WEB:
        expires_at = parse_datetime(channel_settings.get("session_expires_at") or "")
        return expires_at is not None and expires_at > now
    max_idle_days = settings.PUSH_PREVIEW_MAX_IDLE_DAYS
    return (
        max_idle_days > 0
        and channel.last_used_at is not None
        and channel.last_used_at >= now - timedelta(days=max_idle_days)
    )


def with_preview(channel: models.Channel, keys: dict | None, payload: dict) -> dict:
    """Return ``payload`` enriched with the preview ``channel`` may receive.

    Never raises: any failure (message deleted since dispatch, unusable key…)
    returns the thin ``payload`` unchanged, i.e. the generic banner.
    """
    try:
        if not keys or not payload.get("message_id") or not preview_allowed(channel):
            return payload
        message = models.Message.objects.select_related("sender").get(
            id=payload["message_id"]
        )
        preview = build_preview(message)
        if (channel.settings or {}).get("platform") == PushPlatformChoices.WEB:
            return {**payload, "preview": preview}
        return {
            **payload,
            "enc": b64url_encode(encrypt_for_device(pad_json(preview), keys)),
            "kid": key_id(keys["p256dh"]),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The exception class only: its message could quote message content.
        logger.warning(
            "Push preview skipped for channel %s: %s",
            channel.id,
            type(exc).__name__,
        )
        return payload
