"""APNs (Apple Push Notification service) sender — token auth, HTTP/2.

A visible, high-priority, content-free alert that survives app termination. The
provider token is minted once and cached (Apple throttles re-minting); requests
go over the process-global HTTP/2 client (see :mod:`common`).
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import hashlib
import time
from logging import getLogger

from django.conf import settings
from django.core.cache import cache

import jwt

from core import models
from core.services.push.common import (
    PushResult,
    _apns_client,
    _channel_token,
    _deactivate_stale_channels,
    _is_transient_status,
    warn_gateway_unconfigured,
)

logger = getLogger(__name__)


def apns_configured() -> bool:
    """True when all APNs token-auth settings are present."""
    return bool(
        settings.PUSH_APNS_KEY
        and settings.PUSH_APNS_KEY_ID
        and settings.PUSH_APNS_TEAM_ID
        and settings.PUSH_APNS_BUNDLE_ID
    )


# APNs reasons that mean "this device token is permanently dead" → deactivate.
# Deliberately narrow: only ``Unregistered`` (which Apple pairs with a 410), the
# unambiguous "this token is gone" signal.
#
# ``BadDeviceToken`` is intentionally NOT here. Apple returns it both for a
# genuinely malformed token AND — far more commonly in practice — for a token
# sent to the wrong environment (a production token to the sandbox gateway or
# vice-versa, i.e. a mis-set ``PUSH_APNS_USE_SANDBOX``). Treating it as "dead"
# would let one wrong env flag delete a user's iOS device on the first send,
# below the mass-stale circuit-breaker's batch threshold. So we log it and keep
# the row. Genuinely-bad tokens are still cleaned by the *regular* paths:
#   - the device re-registers with a fresh, valid token on its next app launch
#     (same-user upsert replaces the row in place), or
#   - APNs later returns 410/``Unregistered`` once it considers the token gone
#     (deleted here), or
#   - the per-user device cap (``PUSH_MAX_DEVICES_PER_USER``) evicts it as LRU
#     when newer devices register, or
#   - the user removes it by hand from device management (/users/me/channels/).
# (DeviceTokenNotForTopic / ExpiredProviderToken are config/auth errors, not a
# dead device, so they are likewise excluded.)
_APNS_STALE_REASONS = frozenset({"Unregistered"})

# Client-side localization key for the visible APNs alert. The push carries
# only this KEY — never message content — and the app maps it to a localized
# string in its Localizable.strings.
APNS_ALERT_LOC_KEY = "NEW_MESSAGE"


# APNs validates one provider token for 1h and *rejects* regenerating it too
# often (``TooManyProviderTokenUpdates`` — Apple recommends no more than once per
# ~20min). Minting per message would trip that on a busy server, so we share one
# token across all fan-outs via the (redis-backed) cache and refresh comfortably
# inside the 1h hard expiry.
APNS_TOKEN_CACHE_TTL = 45 * 60


def _apns_auth_token() -> str:
    """Return a cached ES256 provider token, minting (and caching) on a miss.

    Shared across messages through the process cache so we don't re-mint per
    fan-out (which Apple throttles). The cache key folds in the key id plus a
    digest of the signing key, so rotating either credential yields a fresh
    token immediately rather than serving a stale one until TTL.
    """
    key_fingerprint = hashlib.sha256(
        (settings.PUSH_APNS_KEY or "").encode("utf-8")
    ).hexdigest()[:16]
    cache_key = (
        f"push:apns:provider_token:{settings.PUSH_APNS_KEY_ID}:{key_fingerprint}"
    )
    token = cache.get(cache_key)
    if token:
        return token
    token = jwt.encode(
        {"iss": settings.PUSH_APNS_TEAM_ID, "iat": int(time.time())},
        settings.PUSH_APNS_KEY,
        algorithm="ES256",
        headers={"kid": settings.PUSH_APNS_KEY_ID},
    )
    cache.set(cache_key, token, APNS_TOKEN_CACHE_TTL)
    return token


def send_apns(
    items: list[tuple[models.Channel, dict]], collapse_key: str
) -> PushResult:
    """Send to iOS devices via APNs (token auth, HTTP/2).

    ``items`` pairs each device channel with its own thin payload (the badge
    count is per-user). A visible, high-priority alert that survives app
    termination. The provider token is cached and the HTTP/2 connection is reused
    across tasks. Devices APNs reports as ``Unregistered`` (410) are removed (via
    the circuit-breaker); transient failures (429 / 5xx / network) are counted
    for retry; other rejections are logged only. Returns a :class:`PushResult`.
    """
    if not items:
        return PushResult()
    if not apns_configured():
        warn_gateway_unconfigured("apns")
        return PushResult()

    try:
        auth = _apns_auth_token()
    except (ValueError, jwt.exceptions.InvalidKeyError) as exc:
        # The signing key itself is unusable, which no amount of retrying fixes:
        # count the batch as permanently failed rather than burning the retry
        # budget (5 attempts, backing off to 10min) on every device, for every
        # message, until PUSH_APNS_KEY is corrected. Both types are needed:
        # cryptography raises a bare ValueError for a malformed PEM (the common
        # case — a mis-pasted key), while PyJWT only raises InvalidKeyError for a
        # well-formed key of the wrong type (e.g. RSA where ES256 wants EC).
        # Neither message echoes the key material.
        logger.error(
            "APNs signing key is unusable (%s: %s); dropping %d notification(s). "
            "Check PUSH_APNS_KEY.",
            type(exc).__name__,
            exc,
            len(items),
        )
        return PushResult()
    except Exception as exc:
        # Anything else (e.g. the cache backend being down) is transient — the
        # whole batch is worth retrying once a fresh token is mintable again.
        # Logged with a traceback: the cause is unknown by construction.
        logger.exception("APNs provider-token mint failed: %s", exc)
        return PushResult(0, len(items))

    host = (
        "api.sandbox.push.apple.com"
        if settings.PUSH_APNS_USE_SANDBOX
        else "api.push.apple.com"
    )
    headers = {
        "authorization": f"bearer {auth}",
        "apns-topic": settings.PUSH_APNS_BUNDLE_ID,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    if collapse_key:
        headers["apns-collapse-id"] = collapse_key

    delivered = 0
    transient = 0
    stale: list[models.Channel] = []
    try:
        # Process-global HTTP/2 client, reused across notification tasks.
        client = _apns_client()
        for channel, payload in items:
            token = _channel_token(channel)
            if not token:
                # Missing/undecryptable settings (e.g. after a Fernet key
                # rotation) — permanent, not transient: skip without counting a
                # retry or deleting (the row self-heals when the device
                # re-registers). Resolved before the try so it can't be
                # miscounted as a transient network failure. See
                # _channel_settings.
                logger.warning(
                    "APNs channel %s has unreadable token; skipping", channel.id
                )
                continue
            # Visible, high-priority alert that survives app termination.
            # Content-free: only a localization KEY (the app renders the
            # string) plus the unread badge — never the sender or subject.
            # mutable-content lets a Notification Service Extension enrich
            # it after refetching.
            aps = {
                "alert": {"loc-key": APNS_ALERT_LOC_KEY},
                "sound": "default",
                "mutable-content": 1,
                "badge": int(payload.get("unread_count", 0)),
            }
            body = {"aps": aps, **payload}
            try:
                resp = client.post(
                    f"https://{host}/3/device/{token}",
                    json=body,
                    headers=headers,
                )
            except Exception as exc:
                # Network/timeout — transient, retry.
                logger.warning("APNs send failed for channel %s: %s", channel.id, exc)
                transient += 1
                continue
            if resp.status_code == 200:
                delivered += 1
                continue
            reason = ""
            try:
                reason = resp.json().get("reason", "") or ""
            except Exception:
                logger.debug("APNs response not JSON for channel %s", channel.id)
            if resp.status_code == 410 or reason in _APNS_STALE_REASONS:
                logger.info(
                    "APNs reports channel %s stale (%s)",
                    channel.id,
                    reason or resp.status_code,
                )
                stale.append(channel)
            elif _is_transient_status(resp.status_code):
                logger.warning(
                    "APNs transient failure for channel %s: status=%s reason=%s",
                    channel.id,
                    resp.status_code,
                    reason,
                )
                transient += 1
            else:
                logger.warning(
                    "APNs rejected channel %s: status=%s reason=%s",
                    channel.id,
                    resp.status_code,
                    reason,
                )
    except Exception as exc:
        # The whole batch failed to even run (client setup) — transient. Logged
        # with a traceback: unlike the per-device network errors above (an
        # expected timeout, where the message says it all), reaching here means
        # something unforeseen broke and the stack is the only clue.
        logger.exception("APNs batch send failed: %s", exc)
        transient = len(items) - delivered

    _deactivate_stale_channels(stale, len(items), platform="apns")
    return PushResult(delivered, transient)
