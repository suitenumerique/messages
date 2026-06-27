"""Shared push-delivery infrastructure.

Result types, the thin payload, device storage (``Channel(type="push")``
registration + management), recipient resolution, the process-global HTTP
clients, and stale-device deactivation. The per-transport senders live in
:mod:`apns` / :mod:`fcm` / :mod:`webpush`; the Celery tasks in :mod:`tasks`.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import hashlib
from functools import lru_cache
from logging import getLogger
from typing import NamedTuple

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

import httpx
from celery.signals import worker_process_shutdown

from core import models
from core.enums import ChannelScopeLevel, ChannelTypes, PushPlatformChoices

logger = getLogger(__name__)

# Notification type marker carried in every payload so the client can route
# without inspecting content it does not (and must not) receive here.
PUSH_TYPE_NEW_MESSAGE = "new_message"


@lru_cache(maxsize=None)
def warn_gateway_unconfigured(platform: str) -> None:
    """Warn that pushes for ``platform`` are dropped — once per process.

    Registration refuses platforms whose gateway is unconfigured, so devices
    can only reach an unconfigured sender when credentials were removed *after*
    they enrolled. That is an operator misconfiguration worth surfacing, but a
    whole fleet would otherwise log one line per notification — hence the
    ``lru_cache`` dedup (per worker process, which is throttle enough).
    """
    logger.warning(
        "Dropping push notifications for platform %r: gateway not configured",
        platform,
    )


class PushResult(NamedTuple):
    """Outcome of a send (one device in the per-notification path).

    ``delivered`` is the number of devices the gateway accepted (2xx).
    ``transient`` is the number that hit a *retryable* failure (429 / 5xx /
    network error) — as opposed to a permanent rejection (bad payload, auth) or
    a dead-token signal (handled by stale-deactivation). A non-zero ``transient``
    tells :func:`tasks.send_push_notification` to retry with backoff. (The senders
    still accept a list, so the same shape is returned for a multi-item batch.)
    """

    delivered: int = 0
    transient: int = 0


class PushTransientError(Exception):
    """Raised by :func:`tasks.send_push_notification` to trigger a Celery retry.

    Signals the device hit a transient gateway failure. Retrying re-sends the
    push, which is safe: the collapse key / Topic coalesces it onto the same
    on-device notification, so a retry never stacks a duplicate.
    """


def build_thin_payload(
    message: models.Message, unread_count: int, mailbox_id=None
) -> dict:
    """Build the privacy-preserving payload for ``message``.

    Deliberately content-free: only routing ids, the notification type and
    the unread badge count. The receiving app uses these to refetch the
    message over its own authenticated session — the push channel never
    carries subject/body/sender.

    ``mailbox_id`` is the recipient's mailbox the thread is read in, so the
    client can deep-link straight to ``/mailbox/{mailbox_id}/.../thread/{thread_id}``
    on tap. It is still just a routing id (no content), and is per-recipient —
    the same message yields a different ``mailbox_id`` for each notified user.
    """
    return {
        "type": PUSH_TYPE_NEW_MESSAGE,
        "thread_id": str(message.thread_id),
        "message_id": str(message.id),
        "mailbox_id": str(mailbox_id) if mailbox_id else None,
        "unread_count": int(unread_count),
    }


def collapse_key_for_message(message: models.Message) -> str:
    """Per-thread coalescing key.

    Used as the APNs ``apns-collapse-id`` and the FCM ``collapse_key`` so a
    burst of messages in one thread collapses to a single visible
    notification on the device rather than stacking. Keyed on the thread so
    successive messages in the same conversation supersede each other.
    """
    return f"thread-{message.thread_id}"


# ---------------------------------------------------------------------------
# Push channels (one user-scoped Channel of type ``push`` per device)
#
# The opaque device token lives encrypted in ``encrypted_settings.token`` (and,
# for Web Push, ``encrypted_settings.keys``). ``settings`` carries the queryable
# ``platform``; the dedup/reclaim key is ``Channel.lookup_hash`` (sha256 of the
# ``push:``-prefixed token, see ``_token_hash``) — an indexed, globally-unique
# column, so we never put the token itself in a queryable column.
# ---------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    # Namespace the input with a ``push:`` prefix (like ``session_hash`` uses
    # ``sess:``) so the globally-unique ``lookup_hash`` can never collide with
    # another channel type that hashes the same raw value — see the field's
    # comment in models.py. This is the sole writer of push ``lookup_hash``.
    return hashlib.sha256(f"push:{token}".encode("utf-8")).hexdigest()


def session_hash(session_key: str) -> str:
    """Hash of the Django session key a device registered under.

    Stored in the channel's plaintext ``settings`` (it is preimage-resistant, so
    it discloses nothing usable — unlike the raw key, which would allow session
    hijacking). Its single purpose is the *voluntary logout* teardown: the
    ``user_logged_out`` receiver deletes the push channels whose stored hash
    matches the session being destroyed, so only the device that logged out
    stops receiving. A session that merely *expires* never passes through the
    logout view, so its channels survive — by design, notifications outlive
    session expiry and only stop on explicit logout.
    """
    return hashlib.sha256(f"sess:{session_key}".encode("utf-8")).hexdigest()


def _channel_settings(channel: models.Channel) -> dict | None:
    """Return the channel's decrypted settings dict, or ``None`` if unreadable.

    ``encrypted_settings`` normally decrypts to a dict, but after a Fernet key
    rotation that leaves a row undecryptable, ``EncryptedJSONField.to_python``
    swallows the ``InvalidToken`` and hands back the *raw JSON string* instead.
    Reading it as a dict then raises ``AttributeError`` — a PERMANENT failure
    (it repeats identically on every retry). Collapsing that to ``None`` here
    gives all three senders one uniform contract: an unreadable device is
    skipped (logged), never counted transient (no futile retries) and never
    deleted (a key rotation is recoverable — the row self-heals when the device
    re-registers with fresh settings on its next launch).
    """
    return (
        channel.encrypted_settings
        if isinstance(channel.encrypted_settings, dict)
        else None
    )


def _channel_token(channel: models.Channel) -> str | None:
    return (_channel_settings(channel) or {}).get("token")


def _channel_keys(channel: models.Channel) -> dict | None:
    return (_channel_settings(channel) or {}).get("keys")


# Fallback device names when the client registers without one. Best-effort only
# — the real, OS-specific label ("Eliane's iPhone") is supplied by the client.
_DEFAULT_DEVICE_NAMES = {
    PushPlatformChoices.APNS: "Apple device",
    PushPlatformChoices.FCM: "Android device",
    PushPlatformChoices.WEB: "Web browser",
}


def _default_device_name(platform: str) -> str:
    return _DEFAULT_DEVICE_NAMES.get(platform, "Push device")


def _drop_other_users_token(user: models.User, token_hash: str) -> None:
    """Delete any push channel for this token owned by a *different* user.

    First step of the cross-user reclaim: the caller then upserts a fresh row.
    Isolated so the concurrent-reclaim retry can rerun exactly this step (a
    foreign row that commits between our delete and our create is only removable
    by redoing the delete).
    """
    models.Channel.objects.filter(
        type=ChannelTypes.PUSH, lookup_hash=token_hash
    ).exclude(user=user).delete()


def register_push_device(
    *,
    user: models.User,
    platform: str,
    token: str,
    app_version: str | None = None,
    keys: dict | None = None,
    name: str | None = None,
    session_key: str | None = None,
):
    """Register (or refresh) a user's device as a ``push`` Channel.

    Device-bound semantics: a physical push token belongs to whoever currently
    registers it. ``Channel.lookup_hash`` (sha256 of the ``push:``-prefixed
    token) is globally unique. Re-registering by the *same* user updates in place
    (stable id); a *different* user's row for the same token is deleted and a
    fresh channel is created for the caller (new id, no carried-over fields). The
    token is stored encrypted. Returns ``(channel, created)`` — ``created`` is
    True for a cross-user reclaim, since the caller gets a new row.

    ``session_key`` stamps the channel with the registering session (hashed —
    see ``session_hash``) so a *voluntary logout* of that session unregisters
    this device and only this device. Clients re-register on every app load, so
    the stamp tracks the current session across key rotations.
    """
    token_hash = _token_hash(token)

    settings_data: dict = {"platform": platform}
    if app_version:
        settings_data["app_version"] = app_version
    if session_key:
        settings_data["session_hash"] = session_hash(session_key)
    encrypted: dict = {"token": token}
    if keys:
        encrypted["keys"] = keys

    # Cross-user reclaim is a DELETE, not a reassign: if A logs out and B logs in
    # on the same device, the OS may reissue the same token, so we drop A's row
    # entirely and let B get a brand-new channel below. Reassigning the row in
    # place would carry A's id, created_at and (if B sends no name) A's device
    # label over to B — a small but real info leak. Same-user re-registration
    # (relaunch / token rotation) keeps its row and id via the update path.
    #
    # Accepted risk: the reclaim is authorized only by presenting a raw token
    # that hashes to the victim's lookup_hash — no proof of device control. An
    # authenticated user holding another user's *raw* token can thus evict that
    # user's channel. We accept it because the hash is preimage-resistant (a
    # column/DB leak does not enable this; the raw token lives only in
    # encrypted_settings + on the device), and the impact is a self-healing
    # notification DoS (the victim's device re-registers on next launch) with no
    # content disclosure. See docs/push-notifications.md §9.
    common = {
        "type": ChannelTypes.PUSH,
        "scope_level": ChannelScopeLevel.USER,
        "user": user,
        "settings": settings_data,
        "encrypted_settings": encrypted,
        "last_used_at": timezone.now(),
    }
    # ``defaults`` (update path) omits ``name`` so a same-user refresh keeps the
    # device's existing label; ``create_defaults`` sets it on first registration
    # (``name`` is a required field, so it must be present when the row is first
    # saved).
    create_defaults = {**common, "name": name or _default_device_name(platform)}

    def _reclaim_and_upsert():
        # Drop any foreign row for this token, then upsert the caller's own. The
        # lookup is scoped by ``user`` (not ``lookup_hash`` alone) on purpose: a
        # foreign row that slips past the delete can then never resolve to a
        # silent in-place update (which would carry the other user's id,
        # created_at and device label over to the caller). Instead the get misses,
        # the create conflicts on the unique ``lookup_hash`` index, and we retry.
        _drop_other_users_token(user, token_hash)
        return models.Channel.objects.update_or_create(
            lookup_hash=token_hash,
            user=user,
            defaults=common,
            create_defaults=create_defaults,
        )

    with transaction.atomic():
        try:
            channel, created = _reclaim_and_upsert()
        except (DjangoValidationError, IntegrityError):
            # A different user's first-time registration of the same token
            # committed between our delete and our create. full_clean raises
            # ValidationError before the INSERT (or, in the narrower TOCTOU window
            # after full_clean's uniqueness SELECT, the DB raises IntegrityError);
            # either way the transaction stays usable. Redo the reclaim — the
            # delete now removes that freshly-committed row — so the outcome is a
            # delete + fresh create, never an in-place update.
            channel, created = _reclaim_and_upsert()
        # On a refresh, adopt a newly-supplied label (the client may have a
        # better OS-derived name than last time); otherwise leave it untouched.
        if not created and name and channel.name != name:
            channel.name = name
            channel.save(update_fields=["name", "updated_at"])
    if created:
        _prune_excess_devices(user, keep_id=channel.id)
    return channel, created


def _prune_excess_devices(user: models.User, *, keep_id) -> None:
    """Cap one user's device fleet at PUSH_MAX_DEVICES_PER_USER.

    Called after a *new* device is registered: if the user is now over the cap,
    delete the least-recently-used surplus (oldest ``last_used_at`` first). Backs
    the loose registration throttle with a hard ceiling on persistent rows. The
    just-registered device is always kept.
    """
    cap = settings.PUSH_MAX_DEVICES_PER_USER
    if not cap or cap <= 0:
        return
    # Push rows always have last_used_at set (registration stamps it), so
    # "-last_used_at" reliably orders most-recently-active first; we keep the
    # first ``cap`` and prune the rest.
    device_ids = list(
        models.Channel.objects.filter(type=ChannelTypes.PUSH, user=user)
        .order_by("-last_used_at")
        .values_list("id", flat=True)
    )
    surplus = device_ids[cap:]
    surplus = [cid for cid in surplus if cid != keep_id]
    if surplus:
        models.Channel.objects.filter(id__in=surplus).delete()


def _push_channels_for_users(user_ids) -> list[models.Channel]:
    """All push channels for the given users, in one query."""
    return list(
        models.Channel.objects.filter(
            type=ChannelTypes.PUSH,
            scope_level=ChannelScopeLevel.USER,
            user_id__in=list(user_ids),
        )
    )


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------


def _recipient_users(message: models.Message) -> list[models.User]:
    """Return the distinct users who should be notified about ``message``.

    These are the users with any access to a mailbox that has access to the
    message's thread — i.e. the inboxes in which this message is now
    visible. The message's own sender is excluded: a user never needs a push
    for a message they just sent.
    """
    user_qs = models.User.objects.filter(
        mailbox_accesses__mailbox__thread_accesses__thread_id=message.thread_id,
    ).distinct()
    if message.sender_user_id:
        user_qs = user_qs.exclude(id=message.sender_user_id)
    return list(user_qs)


def _mailbox_by_user_for_thread(message: models.Message, user_ids) -> dict:
    """Map each recipient user to a mailbox the thread is visible in (one query).

    Used to put a deep-link target in the per-user payload. A user may reach the
    thread through more than one mailbox; we pick one deterministically (lowest
    id) — any is a valid landing inbox for the tap. Returns ``{user_id: mailbox_id}``.
    """
    rows = (
        models.Mailbox.objects.filter(
            thread_accesses__thread_id=message.thread_id,
            accesses__user_id__in=list(user_ids),
        )
        .values_list("accesses__user_id", "id")
        .order_by("id")
    )
    mapping: dict = {}
    for user_id, mailbox_id in rows:
        mapping.setdefault(user_id, mailbox_id)
    return mapping


def _unread_counts_for_users(user_ids) -> dict:
    """Badge counts (distinct unread threads) for many users in ONE query.

    A thread is unread when it has never been read, or has a message newer than
    the last read. Returns ``{user_id: count}``; best-effort (empty on error).
    """
    user_ids = list(user_ids)
    if not user_ids:
        return {}
    try:
        # Reuse the canonical unread predicate so the push badge can't drift
        # from the in-app unread count (see ThreadAccess.unread_filter).
        rows = (
            models.ThreadAccess.objects.filter(
                mailbox__accesses__user_id__in=user_ids,
            )
            .filter(models.ThreadAccess.unread_filter())
            .values("mailbox__accesses__user_id")
            .annotate(n=Count("thread_id", distinct=True))
        )
        return {r["mailbox__accesses__user_id"]: r["n"] for r in rows}
    except Exception as exc:
        logger.warning("Failed to compute unread counts: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Shared HTTP plumbing
# ---------------------------------------------------------------------------

# Per-request timeout (seconds) for outbound push HTTP calls.
PUSH_HTTP_TIMEOUT = 10.0


def _is_transient_status(status_code: int) -> bool:
    """True for HTTP statuses worth retrying: rate-limit (429) and server (5xx).

    Permanent client errors (400/401/403/404/413) are NOT transient — retrying
    them just repeats a request the gateway will keep rejecting.
    """
    return status_code == 429 or 500 <= status_code <= 599


# Process-global HTTP clients, reused across notification tasks.
#
# Delivery is one Celery task per push, but opening a fresh TLS connection per
# push is wasteful — and for APNs specifically, Apple penalizes rapid
# connect/disconnect (it reads as abuse) and HTTP/2 setup is comparatively
# expensive. So we keep one client (and its kept-alive connection pool) per
# worker *process* for the gateways that talk to a single host: APNs (HTTP/2,
# multiplexed) and FCM (HTTP/1.1, keep-alive). Web Push can't share a client —
# each subscription is a different push-service host, delivered through a
# per-request SSRF-IP-pinned session — so it stays per-call.
_APNS_CLIENT: httpx.Client | None = None
_FCM_CLIENT: httpx.Client | None = None


def _apns_client() -> httpx.Client:
    """Return the process-global APNs HTTP/2 client, creating it on first use.

    Lazy init is safe under the default prefork pool (one task per process at a
    time). A race under a threaded pool would at worst leak one extra client.
    """
    global _APNS_CLIENT  # noqa: PLW0603  # pylint: disable=global-statement
    if _APNS_CLIENT is None:
        _APNS_CLIENT = httpx.Client(http2=True, timeout=PUSH_HTTP_TIMEOUT)
    return _APNS_CLIENT


def _fcm_client() -> httpx.Client:
    """Return the process-global FCM HTTP/1.1 client, creating it on first use."""
    global _FCM_CLIENT  # noqa: PLW0603  # pylint: disable=global-statement
    if _FCM_CLIENT is None:
        _FCM_CLIENT = httpx.Client(timeout=PUSH_HTTP_TIMEOUT)
    return _FCM_CLIENT


@worker_process_shutdown.connect
def _close_push_clients(**_kwargs):
    """Close the shared clients when a worker process shuts down."""
    global _APNS_CLIENT, _FCM_CLIENT  # noqa: PLW0603  # pylint: disable=global-statement
    for client in (_APNS_CLIENT, _FCM_CLIENT):
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.debug("Error closing push HTTP client: %s", exc)
    _APNS_CLIENT = None
    _FCM_CLIENT = None


# ---------------------------------------------------------------------------
# Stale-device deactivation (shared by all senders)
# ---------------------------------------------------------------------------

# Defence in depth against the deletion path itself: a gateway reporting that a
# large share of one batch is "stale" is far more likely a systemic error on our
# side (bad payload, wrong auth) than a real fleet of dead tokens. Above this
# ratio (on a batch large enough to be meaningful) we refuse to delete and log
# loudly, so no single bug can wipe a platform's registrations. The primary
# guard is still the narrow per-provider stale codes; this is the backstop.
# Below MIN_BATCH the ratio check is skipped on purpose: a tiny fan-out can't
# distinguish "systemic" from "genuinely dead", and the narrow codes
# (UNREGISTERED / 410 / 404-gone) are device-dead signals, not transient — so on
# a 1-3 device user we trust them and delete.
STALE_DELETE_RATIO_LIMIT = 0.5
STALE_DELETE_MIN_BATCH = 4

# Second backstop, for the one-task-per-notification path where there is no
# batch to ratio-check (each task deactivates at most its own single device): a
# rolling per-platform cap on how many devices we'll delete in a short window.
# A systemic fault (wrong env, auth rot) would otherwise wipe a fleet one task
# at a time, invisibly to the per-batch ratio guard. The limit is well above any
# plausible genuine churn for a single deployment in a minute, so it only trips
# on a runaway. Hardcoded (no operator knob) — the default is the only sane value.
STALE_DELETE_WINDOW_SECONDS = 60
STALE_DELETE_WINDOW_LIMIT = 500


def _stale_delete_within_window(platform: str, count: int) -> bool:
    """True if deleting ``count`` more ``platform`` devices stays under the cap.

    Uses an atomic per-platform counter in the shared cache with a rolling TTL.
    On any cache error we fail *open* (allow the delete) — the narrow stale codes
    and the per-batch ratio guard are the primary protections; this is a backstop.
    """
    cache_key = f"push:stale_deletes:{platform}"
    try:
        cache.add(cache_key, 0, STALE_DELETE_WINDOW_SECONDS)
        total = cache.incr(cache_key, count)
    except Exception as exc:
        logger.warning("Stale-delete window counter unavailable: %s", exc)
        return True
    return total <= STALE_DELETE_WINDOW_LIMIT


def _deactivate_stale_channels(
    stale: list[models.Channel], attempted: int, *, platform: str
) -> int:
    """Delete channels a provider reported as permanently gone, with two guards.

    Returns how many channels were deactivated (0 if a circuit-breaker tripped).
    """
    if not stale:
        return 0
    if (
        attempted >= STALE_DELETE_MIN_BATCH
        and len(stale) / attempted >= STALE_DELETE_RATIO_LIMIT
    ):
        logger.error(
            "Refusing to delete %d/%d %s push channels reported stale in one run "
            "— treating as a systemic error, not dead tokens.",
            len(stale),
            attempted,
            platform,
        )
        return 0
    if not _stale_delete_within_window(platform, len(stale)):
        logger.error(
            "Refusing to delete %d %s push channel(s): more than %d stale "
            "deletions in %ds — treating as a systemic error, not dead tokens.",
            len(stale),
            platform,
            STALE_DELETE_WINDOW_LIMIT,
            STALE_DELETE_WINDOW_SECONDS,
        )
        return 0
    models.Channel.objects.filter(id__in=[c.id for c in stale]).delete()
    return len(stale)
