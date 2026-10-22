"""Celery tasks: the recipient-resolving orchestrator and the per-user send.

``enqueue_push_notifications`` is called (on commit) by the delivery pipeline;
``send_push_for_message`` resolves recipients and dispatches one
``send_push_notification`` task per recipient user, covering their devices.
"""

# pylint: disable=broad-exception-caught, unused-argument

from __future__ import annotations

import traceback
from collections import defaultdict
from logging import getLogger

from django.conf import settings
from django.db import transaction

from celery.utils.time import get_exponential_backoff_interval

import core.services.push as _push
from core import models
from core.enums import ChannelScopeLevel, ChannelTypes, PushPlatformChoices
from core.services.push.common import (
    PushTransientError,
    _channel_keys,
    _mailbox_by_user_for_thread,
    _push_channels_for_users,
    _recipient_users,
    build_thin_payload,
    collapse_key_for_message,
)
from core.services.push.preview import preview_loader, with_preview

from messages.celery_app import app as celery_app

logger = getLogger(__name__)

# Maps each platform to the *name* of its sender. Resolved by name at call time
# against the package namespace (``_push``) rather than captured here, so the
# dispatch follows monkeypatching of ``push.send_apns`` (used by the tests) and
# stays a one-line change to extend.
_PLATFORM_SENDER_NAMES = {
    PushPlatformChoices.APNS: "send_apns",
    PushPlatformChoices.FCM: "send_fcm",
    PushPlatformChoices.WEB: "send_webpush",
}

PUSH_MAX_RETRIES = 5
PUSH_RETRY_BACKOFF_MAX = 600


def enqueue_push_notifications(message: models.Message) -> None:
    """Schedule push delivery for ``message`` after the current transaction commits.

    Safe to call unconditionally from the delivery pipeline: it no-ops when
    push is disabled, and otherwise defers the actual send to a Celery task
    via ``transaction.on_commit`` so we never push for a message that ends
    up rolled back. Never raises.
    """
    if not settings.PUSH_ENABLED:
        return
    message_id = str(message.id)

    def _publish():
        # Contain broker failures *inside* the callback: it runs at commit
        # time, off this stack, so an exception escaping here would surface in
        # whoever triggered the commit — the inbound pipeline, which by then has
        # already deleted its queue row and would retry a row that is gone.
        try:
            send_push_for_message.delay(message_id)  # pylint: disable=no-member
        except Exception as exc:
            logger.warning("Failed to enqueue push for message %s: %s", message_id, exc)

    try:
        transaction.on_commit(_publish)
    except Exception as exc:
        # on_commit can only fail in pathological setups (e.g. no DB
        # connection); push is best-effort so we swallow it.
        logger.warning("Failed to schedule push for message %s: %s", message_id, exc)


@celery_app.task(bind=True)
def send_push_for_message(self, message_id: str):
    """Resolve recipients for ``message_id`` and dispatch one task per user.

    Loads the message, resolves the recipient users and their devices, builds
    each user's thin payload (the deep-link mailbox is per-user), then
    dispatches one :func:`send_push_notification` task **per user** who has
    devices. This task is the *orchestrator*: it does the
    per-recipient DB work once (channels, deep-link mailboxes — a handful of
    batched queries) and dispatches; it never contacts a gateway itself, so a
    flaky provider can't stall recipient resolution. Never raises: a push
    problem can't disrupt anything upstream.

    One task per user checks their thread access and loads the preview once
    for all their devices, while the Celery worker pool still delivers to
    different users in parallel. The shared, cached gateway tokens (APNs JWT /
    FCM OAuth) mean the many tasks don't each re-authenticate.
    """
    if not settings.PUSH_ENABLED:
        return {"success": True, "skipped": "push_disabled"}

    try:
        message = models.Message.objects.select_related("thread").get(id=message_id)
    except models.Message.DoesNotExist:
        logger.warning("send_push_for_message: message %s not found", message_id)
        return {"success": False, "error": "message_not_found"}

    users = _recipient_users(message)
    if not users:
        return {"success": True, "notified_users": 0, "dispatched": 0}

    # One query for every recipient's push channels, grouped by user.
    channels_by_user: dict = defaultdict(list)
    for channel in _push_channels_for_users(u.id for u in users):
        channels_by_user[channel.user_id].append(channel)

    # One query for the recipients' deep-link mailboxes (only those with
    # devices).
    users_with_devices = [u.id for u in users if channels_by_user.get(u.id)]
    mailbox_by_user = _mailbox_by_user_for_thread(message, users_with_devices)

    collapse_key = collapse_key_for_message(message)
    notified_users = 0
    dispatched = 0
    for user in users:
        user_channels = channels_by_user.get(user.id)
        if not user_channels:
            continue
        notified_users += 1
        # One thin payload per user: the deep-link mailbox is per-user.
        payload = build_thin_payload(message, mailbox_id=mailbox_by_user.get(user.id))
        # Guarded per user: this task holds the only resolved recipient list,
        # so letting one broker hiccup escape would strand every user still to
        # come with no way to recover them.
        try:
            send_push_notification.delay(  # pylint: disable=no-member
                str(user.id), payload, collapse_key
            )
        except Exception as exc:
            logger.warning("Failed to dispatch push for user %s: %s", user.id, exc)
            continue
        dispatched += 1

    logger.info(
        "send_push_for_message %s: notified %d user(s), dispatched %d user task(s)",
        message_id,
        notified_users,
        dispatched,
    )
    return {
        "success": True,
        "notified_users": notified_users,
        "dispatched": dispatched,
    }


@celery_app.task(bind=True, max_retries=PUSH_MAX_RETRIES, acks_late=True)
def send_push_notification(
    self,
    user_id: str,
    payload: dict,
    collapse_key: str,
    channel_ids: list[str] | None = None,
):
    """Deliver one push to ``user_id``'s devices, retrying the transient failures.

    The unit of delivery: it resolves the user's devices when it runs (so
    skipping one un-associated since dispatch), checks once that the user can
    still read the thread, then hands each device to its platform's sender.
    Devices that hit a *transient* failure (429 / 5xx / network) are retried
    with exponential backoff, and only them (``channel_ids``): the others
    already got the push, and a browser would re-alert for it (``renotify``).
    Retrying is idempotent on-device as the collapse key / Topic coalesces it
    onto the same notification. Dead-token devices are deleted inside the
    sender; permanent rejections (bad payload, auth) end that device's
    delivery. ``acks_late`` means a worker crash re-runs this push (again
    collapse-deduped), not the whole fan-out.

    ``payload`` is the thin payload: the preview each device may receive is
    added here, right before sending, so message content never sits in the
    broker or in the persisted task arguments.
    """
    if not settings.PUSH_ENABLED:
        return {"success": True, "skipped": "push_disabled"}

    channels = models.Channel.objects.select_related("user").filter(
        type=ChannelTypes.PUSH, scope_level=ChannelScopeLevel.USER, user_id=user_id
    )
    if channel_ids is not None:
        channels = channels.filter(id__in=channel_ids)
    channels = list(channels)
    if not channels:
        # Devices un-associated (or reclaimed) between dispatch and delivery.
        return {"success": True, "skipped": "channel_gone"}

    # Someone who lost the thread since dispatch (a retry can run minutes later)
    # gets no push at all, not even the generic banner: it would still tell
    # them the thread is active.
    if not models.ThreadAccess.objects.viewer_user_ids(
        payload.get("thread_id"), user_ids=[user_id]
    ).exists():
        return {"success": True, "skipped": "access_revoked"}

    load_preview = preview_loader(payload.get("message_id"))
    delivered = 0
    retry_ids = []
    for channel in channels:
        result = _send_to_device(channel, payload, load_preview, collapse_key)
        if result is None:
            continue
        delivered += result.delivered
        if result.transient:
            retry_ids.append(str(channel.id))

    if retry_ids:
        raise self.retry(
            args=(user_id, payload, collapse_key),
            kwargs={"channel_ids": retry_ids},
            exc=PushTransientError(
                f"{len(retry_ids)} channel(s) hit a transient failure"
            ),
            countdown=get_exponential_backoff_interval(
                factor=1,
                retries=self.request.retries,
                maximum=PUSH_RETRY_BACKOFF_MAX,
                full_jitter=True,
            ),
        )

    return {"success": True, "delivered": delivered}


def _send_to_device(channel, payload, load_preview, collapse_key):
    """Hand one device its push through its platform's sender.

    Returns the sender's :class:`PushResult`, or ``None`` when the device can't
    be sent to (unknown platform, sender bug). The enriched payload is never
    bound in a frame: error trackers (Sentry) capture frame locals, and a Web
    Push preview is in clear until the sender encrypts it.
    """
    platform = (channel.settings or {}).get("platform")
    sender_name = _PLATFORM_SENDER_NAMES.get(platform)
    # Resolve against the package namespace so tests can monkeypatch the senders.
    sender = getattr(_push, sender_name, None) if sender_name else None
    if sender is None:
        logger.warning("No push sender for platform %r", platform)
        return None

    try:
        return sender(
            [
                (
                    channel,
                    with_preview(
                        channel, _channel_keys(channel), payload, load_preview
                    ),
                )
            ],
            collapse_key,
        )
    except Exception as exc:
        # Senders swallow their own errors; a bug here must not crash the task
        # into an infinite retry, so we catch and skip the device. Logged
        # without exc_info nor message for the same reason: the stack alone, as
        # text.
        logger.error(
            "Push sender for platform %s raised %s\n%s",
            platform,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        return None
