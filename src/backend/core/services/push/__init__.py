"""Mobile/web push-notification delivery.

This is the tail end of the inbound-delivery pipeline: once a message has
landed, :func:`enqueue_push_notifications` is called (by the pipeline, on
commit) to fan a *thin* notification out to every active device the
recipient mailbox's user(s) registered.

Layout:

- :mod:`common` — result types, the thin payload, device storage/registration,
  recipient resolution, the process-global HTTP clients, stale-device deletion.
- :mod:`apns` / :mod:`fcm` / :mod:`webpush` — the per-transport senders.
- :mod:`tasks` — the Celery orchestrator + the per-device delivery task.

Design constraints baked in here:

- **Feature-flagged off.** Every public entry point short-circuits when
  ``settings.PUSH_ENABLED`` is False, so the package is inert until an operator
  opts in. Enabling push for only one platform is safe: registration refuses
  the unconfigured platforms (:func:`gateway_configured`), and each sender
  additionally no-ops — with a deduplicated warning — should credentials
  disappear after devices enrolled.
- **No message content on the wire.** We never put the subject, body, sender
  name or any other message *content* into the payload — only routing
  identifiers (``thread_id`` / ``message_id`` / ``mailbox_id``), a ``type`` and
  an unread *count* for the badge, then the device refetches over its
  authenticated session. See :func:`build_thin_payload`. Note this is not full
  privacy: only Web Push is end-to-end encrypted (RFC 8291) — for APNs and FCM
  those routing UUIDs and the unread count are visible to Apple/Google in
  transit. The content itself never is.
- **Non-fatal.** Push is best-effort. Senders never raise into the caller —
  failures are logged and swallowed so a flaky gateway can never break delivery.
- **One task per notification.** Each device's push is an independently-retryable
  Celery task; the gateways have no multi-device batch API, so the Celery worker
  pool provides the parallelism.
- **Self-healing devices.** A gateway "this token is dead" response (APNs 410
  ``Unregistered``, FCM ``UNREGISTERED`` / ``NOT_FOUND``, Web Push 404/410)
  deletes that channel — narrowly, behind two circuit-breakers, to avoid wiping
  live devices on a config error.
"""

from core.enums import PushPlatformChoices
from core.services.ssrf import SSRFSafeSession, SSRFValidationError

from . import apns, common, fcm, tasks, webpush
from .apns import APNS_ALERT_LOC_KEY, send_apns
from .common import (
    PUSH_TYPE_NEW_MESSAGE,
    PushResult,
    PushTransientError,
    build_thin_payload,
    collapse_key_for_message,
    register_push_device,
)
from .fcm import (
    FCM_ANDROID_CHANNEL_ID,
    FCM_BODY_LOC_KEY,
    FCM_TITLE_LOC_KEY,
    send_fcm,
)
from .tasks import (
    enqueue_push_notifications,
    send_push_for_message,
    send_push_notification,
)
from .webpush import (
    WEBPUSH_TTL_SECONDS,
    derive_vapid_public_key,
    generate_vapid_keypair,
    send_webpush,
)


def gateway_configured(platform: str) -> bool:
    """True when the gateway serving ``platform`` has its credentials set.

    Device registration refuses platforms this returns False for: accepting
    them would enroll a fleet whose notifications are silently dropped at send
    time (each sender no-ops without its credentials). Unknown platform values
    are treated as unconfigured.
    """
    checks = {
        PushPlatformChoices.APNS: apns.apns_configured,
        PushPlatformChoices.FCM: fcm.fcm_configured,
        PushPlatformChoices.WEB: webpush.webpush_configured,
    }
    check = checks.get(platform)
    return bool(check and check())


__all__ = [
    "APNS_ALERT_LOC_KEY",
    "FCM_ANDROID_CHANNEL_ID",
    "FCM_BODY_LOC_KEY",
    "FCM_TITLE_LOC_KEY",
    "PUSH_TYPE_NEW_MESSAGE",
    "PushResult",
    "PushTransientError",
    "SSRFSafeSession",
    "SSRFValidationError",
    "WEBPUSH_TTL_SECONDS",
    "apns",
    "build_thin_payload",
    "collapse_key_for_message",
    "common",
    "derive_vapid_public_key",
    "enqueue_push_notifications",
    "fcm",
    "gateway_configured",
    "generate_vapid_keypair",
    "register_push_device",
    "send_apns",
    "send_fcm",
    "send_push_for_message",
    "send_push_notification",
    "send_webpush",
    "tasks",
    "webpush",
]
