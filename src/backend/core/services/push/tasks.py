"""Background tasks: the recipient-resolving orchestrator and the per-device send.

``enqueue_push_notifications`` is called (on commit) by the delivery pipeline;
``send_push_for_message`` resolves recipients and dispatches one
``send_push_notification`` task per device.
"""

# pylint: disable=broad-exception-caught, unused-argument

from __future__ import annotations

from collections import defaultdict
from logging import getLogger

from django.conf import settings
from django.db import transaction

import core.services.push as _push
from core import models
from core.enums import ChannelTypes, PushPlatformChoices
from core.services.push.common import (
    PushTransientError,
    _mailbox_by_user_for_thread,
    _push_channels_for_users,
    _recipient_users,
    _unread_counts_for_users,
    build_thin_payload,
    collapse_key_for_message,
)
from core.task_utils import register_task

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


def enqueue_push_notifications(message: models.Message) -> None:
    """Schedule push delivery for ``message`` after the current transaction commits.

    Safe to call unconditionally from the delivery pipeline: it no-ops when
    push is disabled, and otherwise defers the actual send to a background task
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


@register_task(queue="default")
def send_push_for_message(message_id: str):
    """Resolve recipients for ``message_id`` and dispatch one task per device.

    Loads the message, resolves the recipient users and their devices, builds
    each user's thin payload (the badge count is per-user), then dispatches one
    :func:`send_push_notification` task **per device**. This task is the
    *orchestrator*: it does the per-recipient DB work once (channels, badge
    counts, deep-link mailboxes — a handful of batched queries) and dispatches;
    it never contacts a gateway itself, so a flaky provider can't stall
    recipient resolution. Never raises: a push problem can't disrupt anything
    upstream.

    One task per notification makes each push an independently-retryable atomic
    unit: a single flaky device retries on its own without re-sending to anyone
    else, and the worker pool delivers them in parallel (the gateways
    have no multi-device batch API, so parallelism — not batching — is the
    lever). The shared, cached gateway tokens (APNs JWT / FCM OAuth) mean the
    many tasks don't each re-authenticate.
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

    # One query each for the recipients' badge counts and deep-link mailboxes
    # (only those with devices).
    users_with_devices = [u.id for u in users if channels_by_user.get(u.id)]
    unread_by_user = _unread_counts_for_users(users_with_devices)
    mailbox_by_user = _mailbox_by_user_for_thread(message, users_with_devices)

    collapse_key = collapse_key_for_message(message)
    notified_users = 0
    dispatched = 0
    for user in users:
        user_channels = channels_by_user.get(user.id)
        if not user_channels:
            continue
        notified_users += 1
        # One thin payload per user (the badge count is per-user); each of the
        # user's devices gets its own task carrying that payload.
        payload = build_thin_payload(
            message,
            unread_by_user.get(user.id, 0),
            mailbox_id=mailbox_by_user.get(user.id),
        )
        for channel in user_channels:
            # Guarded per device: this task holds the only resolved recipient
            # list, so letting one broker hiccup escape would strand every
            # device still to come with no way to recover them.
            try:
                send_push_notification.delay(  # pylint: disable=no-member
                    str(channel.id), payload, collapse_key
                )
            except Exception as exc:
                logger.warning(
                    "Failed to dispatch push for channel %s: %s", channel.id, exc
                )
                continue
            dispatched += 1

    logger.info(
        "send_push_for_message %s: notified %d user(s), dispatched %d device task(s)",
        message_id,
        notified_users,
        dispatched,
    )
    return {
        "success": True,
        "notified_users": notified_users,
        "dispatched": dispatched,
    }


@register_task(
    queue="default",
    max_retries=5,
    retry_on=(PushTransientError,),
    max_backoff=600,
)
def send_push_notification(channel_id: str, payload: dict, collapse_key: str):
    """Deliver one push to one device, retrying on a transient failure.

    The atomic unit of delivery: it re-fetches the one channel (skips if the
    device was un-associated since dispatch), resolves its platform, and hands a
    single-item batch to that platform's sender. On a *transient* failure
    (429 / 5xx / network) it raises :class:`PushTransientError` so just this
    notification is retried with exponential backoff; retrying is idempotent
    on-device because the collapse key / Topic coalesces it onto the same
    notification. Dead-token devices are deleted inside the sender; permanent
    rejections (bad payload, auth) end the task. Delivery is at-least-once, so
    a worker crash re-runs this one push (again collapse-deduped), not the
    whole fan-out.
    """
    if not settings.PUSH_ENABLED:
        return {"success": True, "skipped": "push_disabled"}

    try:
        channel = models.Channel.objects.get(id=channel_id, type=ChannelTypes.PUSH)
    except models.Channel.DoesNotExist:
        # Device un-associated (or reclaimed) between dispatch and delivery.
        return {"success": True, "skipped": "channel_gone"}

    platform = (channel.settings or {}).get("platform")
    sender_name = _PLATFORM_SENDER_NAMES.get(platform)
    # Resolve against the package namespace so tests can monkeypatch the senders.
    sender = getattr(_push, sender_name, None) if sender_name else None
    if sender is None:
        logger.warning("No push sender for platform %r", platform)
        return {"success": False, "error": "no_sender"}

    try:
        result = sender([(channel, payload)], collapse_key)
    except Exception as exc:
        # Senders swallow their own errors; a bug here must not crash the task
        # into an infinite retry, so we catch and stop (logged for visibility).
        logger.exception("Push sender for platform %s raised: %s", platform, exc)
        return {"success": False, "error": "sender_raised"}

    if result.transient:
        raise PushTransientError(
            f"{platform} channel {channel_id} hit a transient failure"
        )

    return {"success": True, "delivered": result.delivered}
