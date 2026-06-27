"""FCM (Firebase Cloud Messaging) HTTP v1 sender.

One POST per device (the v1 API is single-recipient) over the process-global
HTTP/1.1 client; the OAuth token is cached. Carries a content-free, OS-localized
notification block so Android renders a banner even when the app is force-quit.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import hashlib
import json
from logging import getLogger

from django.conf import settings
from django.core.cache import cache

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from core import models
from core.services.push.common import (
    PushResult,
    _channel_token,
    _deactivate_stale_channels,
    _fcm_client,
    _is_transient_status,
    warn_gateway_unconfigured,
)

logger = getLogger(__name__)

# Android loc-keys the app resolves from its strings.xml. Content-free contract,
# like the APNs alert — only keys, never content.
FCM_TITLE_LOC_KEY = "new_message_title"
FCM_BODY_LOC_KEY = "new_message_body"

# Android notification channel the Capacitor app creates at enable/refresh time
# (features/native/push.ts) and that the manifest declares as FCM default.
# Without an explicit channel Android 8+ renders on the SDK's "Miscellaneous"
# fallback at DEFAULT importance — no heads-up banner, whatever the message
# priority says. Must stay in sync with ANDROID_NOTIFICATION_CHANNEL_ID on the
# frontend (contract-tested on both sides).
FCM_ANDROID_CHANNEL_ID = "new_messages"

# FCM OAuth access tokens are ~1h-lived. Cache one across fan-outs (keyed on a
# digest of the service-account JSON, so rotating credentials refreshes it)
# rather than running the service-account → OAuth exchange on every message.
FCM_TOKEN_CACHE_TTL = 45 * 60


def fcm_configured() -> bool:
    """True when FCM credentials + project id are present."""
    return bool(settings.PUSH_FCM_CREDENTIALS and settings.PUSH_FCM_PROJECT_ID)


def _fcm_access_token() -> str | None:
    """Return a cached OAuth token for FCM, minting one on a cache miss.

    Returns ``None`` (and logs) on any credential/refresh error so the caller
    treats FCM as unavailable rather than failing the send. The token is shared
    across messages through the cache so we don't re-run the OAuth exchange per
    fan-out.
    """
    creds_fingerprint = hashlib.sha256(
        (settings.PUSH_FCM_CREDENTIALS or "").encode("utf-8")
    ).hexdigest()[:16]
    cache_key = f"push:fcm:access_token:{creds_fingerprint}"
    token = cache.get(cache_key)
    if token:
        return token
    try:
        info = json.loads(settings.PUSH_FCM_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        credentials.refresh(Request())
    except Exception as exc:
        logger.warning("Failed to obtain FCM access token: %s", exc)
        return None
    cache.set(cache_key, credentials.token, FCM_TOKEN_CACHE_TTL)
    return credentials.token


def send_fcm(items: list[tuple[models.Channel, dict]], collapse_key: str) -> PushResult:
    """Send to Android devices via FCM HTTP v1.

    ``items`` pairs each device channel with its own thin payload. One POST per
    device over the shared client; the OAuth token is cached. The data payload
    (all string values, as v1 requires) carries no message content; a
    content-free, OS-localized ``notification`` block lets Android render a banner
    when the app is killed, and the app refetches over its session to enrich it.
    An ``UNREGISTERED`` error removes the device's push channel (via the
    circuit-breaker); transient failures (429 / 5xx / network) are counted for
    retry. Returns a :class:`PushResult`.
    """
    if not items:
        return PushResult()
    if not fcm_configured():
        warn_gateway_unconfigured("fcm")
        return PushResult()

    access_token = _fcm_access_token()
    if not access_token:
        # Credentials unavailable/transient (network to Google's token
        # endpoint) — worth retrying the batch.
        return PushResult(0, len(items))

    url = (
        "https://fcm.googleapis.com/v1/projects/"
        f"{settings.PUSH_FCM_PROJECT_ID}/messages:send"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    delivered = 0
    transient = 0
    stale: list[models.Channel] = []
    try:
        # Process-global client, reused across notification tasks (keep-alive to
        # the single FCM host avoids a TLS handshake per push).
        client = _fcm_client()
        for channel, payload in items:
            token = _channel_token(channel)
            if not token:
                # Missing/undecryptable settings (e.g. after a Fernet key
                # rotation) — permanent, not transient: skip without retrying or
                # deleting (the row self-heals when the device re-registers).
                # See _channel_settings.
                logger.warning(
                    "FCM channel %s has unreadable token; skipping", channel.id
                )
                continue
            # FCM v1 data values must all be strings. None values are dropped
            # (FCM rejects null data values).
            data = {k: str(v) for k, v in payload.items() if v is not None}
            # Attach a content-free, OS-localized notification so Android
            # renders a banner even when the app is force-quit (data-only
            # messages are not auto-displayed then). The keys map to the app's
            # strings.xml — the push carries no sender/subject, only loc-keys +
            # the unread badge. Symmetric with the APNs alert path.
            android: dict = {
                "collapse_key": collapse_key,
                "priority": "high",
                "notification": {
                    "channel_id": FCM_ANDROID_CHANNEL_ID,
                    "title_loc_key": FCM_TITLE_LOC_KEY,
                    "body_loc_key": FCM_BODY_LOC_KEY,
                    "notification_count": int(payload.get("unread_count", 0)),
                },
            }
            body = {
                "message": {
                    "token": token,
                    "data": data,
                    "android": android,
                }
            }
            try:
                response = client.post(url, headers=headers, json=body)
            except Exception as exc:
                logger.warning("FCM send failed for channel %s: %s", channel.id, exc)
                transient += 1
                continue
            if response.status_code == 200:
                delivered += 1
                continue
            if _fcm_response_is_stale(response):
                logger.info("FCM reports channel %s stale", channel.id)
                stale.append(channel)
            elif _is_transient_status(response.status_code):
                logger.warning(
                    "FCM transient failure for channel %s: status=%s",
                    channel.id,
                    response.status_code,
                )
                transient += 1
            else:
                logger.warning(
                    "FCM rejected channel %s: status=%s body=%s",
                    channel.id,
                    response.status_code,
                    response.text[:500],
                )
    except Exception as exc:
        # The whole batch failed to even run (client setup) — transient.
        logger.warning("FCM batch send failed: %s", exc)
        transient = len(items) - delivered

    _deactivate_stale_channels(stale, len(items), platform="fcm")
    return PushResult(delivered, transient)


def _fcm_response_is_stale(response) -> bool:
    """Decide whether an FCM error response means the token is dead.

    Only ``UNREGISTERED`` (404, or in a 400's error ``details``) and ``NOT_FOUND``
    deactivate the token. ``INVALID_ARGUMENT`` is deliberately NOT treated as
    stale — FCM also returns it for a malformed *request* (a bug on our side), so
    acting on it would delete the whole fleet on one bad deploy. Everything else
    is a transient/operator error we leave the token in place for.
    """
    if response.status_code not in (400, 404):
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    status_str = (error.get("status") or "").upper()
    if status_str in ("UNREGISTERED", "NOT_FOUND"):
        return True
    # Only UNREGISTERED is unambiguously a dead token. INVALID_ARGUMENT is
    # deliberately NOT treated as stale: FCM also returns it for a malformed
    # *request* (a payload/schema bug on our side), so acting on it would let
    # one bad deploy delete every Android registration in the fleet.
    for detail in error.get("details", []) or []:
        if isinstance(detail, dict) and detail.get("errorCode") == "UNREGISTERED":
            return True
    return False
