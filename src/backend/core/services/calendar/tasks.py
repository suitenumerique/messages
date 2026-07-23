"""Celery tasks for CalDAV calendar operations."""

from typing import Any, Dict

from celery.utils.log import get_task_logger
from sentry_sdk import capture_exception

from core.enums import ChannelTypes
from core.models import Channel
from core.services.calendar.service import CalDAVError, CalDAVService

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)

# Generic, user-facing error wording. The exception text is logged + sent to
# Sentry for diagnosis, but is never surfaced to the API client — exception
# strings can include the CalDAV server URL, internal hostnames, or other
# details we do not want to render in a toast.
_RSVP_FAILURE = "Failed to send the RSVP."
_ADD_FAILURE = "Failed to add the event to the calendar."
_APPLY_REPLY_FAILURE = "Failed to apply the inbound RSVP reply."


def _get_caldav_service(channel_id: str | None, user_email: str):
    """Build a CalDAVService from a channel ID or instance-level config.

    ``user_email`` is the requesting user's OIDC identity email — NOT
    the acting mailbox's email. It is the Basic Auth username for the
    instance-level path because the calendars CalDAV provider keys
    principals on the OIDC ``email`` claim. Per-channel auth is
    self-contained so the email is ignored there.
    """
    if channel_id:
        # Only resolve active channels: a paused CalDAV channel must not
        # sync, so it raises DoesNotExist here just like an unknown id.
        channel = Channel.objects.get(
            id=channel_id, type=ChannelTypes.CALDAV, is_active=True
        )
        return CalDAVService.from_channel(channel)

    return CalDAVService.from_instance_config(user_email)


@celery_app.task(bind=True)
def calendar_rsvp_task(
    self,  # pylint: disable=unused-argument
    channel_id: str | None,
    user_email: str,
    ics_data: str,
    response: str,
    attendee_email: str,
    calendar_id: str | None = None,
) -> Dict[str, Any]:
    """
    Respond to a calendar event via CalDAV (RSVP).

    Args:
        channel_id: UUID of the CalDAV channel, or None for instance config
        user_email: Requesting user's OIDC identity email (Basic Auth user
            for instance config — addresses the user's principal on the
            CalDAV server, which keys on the OIDC email claim).
        ics_data: Raw ICS content
        response: ACCEPTED, DECLINED, or TENTATIVE
        attendee_email: Email of the responding attendee in the .ics
            ATTENDEE list (the mailbox address the invitation was sent
            to — this is what iTIP matches on, NOT the user's OIDC email).
        calendar_id: Optional specific calendar URL to use
    """
    try:
        service = _get_caldav_service(channel_id, user_email)
    except Channel.DoesNotExist as e:
        # Race: the row existed when the viewset enqueued the task, gone
        # by the time the worker ran. Worth a Sentry breadcrumb so we
        # can see if this happens at any volume.
        capture_exception(e)
        logger.warning(
            "CalDAV channel %s vanished between enqueue and execute", channel_id
        )
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV channel not found.",
        }
    except ValueError as e:
        # Configuration error (URL/password missing). User-facing message
        # is intentionally generic; full detail is on the worker logs.
        logger.warning("CalDAV service unavailable: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV service is not configured.",
        }

    try:
        service.respond_to_event(
            ics_data=ics_data,
            response=response,
            attendee_email=attendee_email,
            calendar_id=calendar_id,
        )

        return {
            "status": "SUCCESS",
            "result": {"response": response},
            "error": None,
        }
    except CalDAVError as e:
        # CalDAVError is the protocol-shaped error the service raises for
        # known failure modes (no attendee match, SSRF-blocked URL, 4xx/5xx
        # from server). Its message is safe to surface — it is composed
        # by us, not by ``requests`` or the upstream — and is informative
        # to the user (e.g. "Mailbox is not an attendee of this event").
        logger.warning("RSVP failed: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": str(e),
        }
    except Exception:  # pylint: disable=broad-exception-caught
        # Anything else is unexpected. Stack to Sentry, generic copy to
        # the client — raw ``str(e)`` from ``requests``/``icalendar`` can
        # leak the CalDAV URL or other internal details.
        logger.exception("Error responding to calendar event")
        return {
            "status": "FAILURE",
            "result": None,
            "error": _RSVP_FAILURE,
        }


# ignore_result: this task is fire-and-forget from the inbound hook (never
# polled), and its args carry the raw REPLY ICS + addresses — keeping them out
# of the result backend avoids persisting that content there.
@celery_app.task(bind=True, ignore_result=True)
def calendar_apply_reply_task(
    self,  # pylint: disable=unused-argument
    channel_id: str | None,
    user_email: str,
    ics_data: str,
    attendee_email: str,
    organizer_email: str | None = None,
) -> Dict[str, Any]:
    """
    Apply an inbound iTIP ``METHOD:REPLY`` to the organizer's calendar.

    Mirror of ``calendar_rsvp_task`` for the reverse direction. Trust gating
    happens in the inbound task before enqueue.

    Args:
        channel_id: UUID of the CalDAV channel, or None for instance config.
        user_email: The organizer mailbox's CalDAV principal email (OIDC
            identity / Basic Auth user for instance config).
        ics_data: Raw REPLY ICS content.
        attendee_email: The DMARC-verified sender (From) — the only ATTENDEE
            ``apply_reply`` may modify.
        organizer_email: The recipient mailbox address; only a stored copy whose
            ORGANIZER matches it is updated.
    """
    try:
        service = _get_caldav_service(channel_id, user_email)
    except Channel.DoesNotExist as e:
        capture_exception(e)
        logger.warning(
            "CalDAV channel %s vanished between enqueue and execute", channel_id
        )
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV channel not found.",
        }
    except ValueError as e:
        logger.warning("CalDAV service unavailable: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV service is not configured.",
        }

    try:
        result = service.apply_reply(
            ics_data=ics_data,
            attendee_email=attendee_email,
            organizer_email=organizer_email,
        )
        return {
            "status": "SUCCESS",
            "result": result,
            "error": None,
        }
    except CalDAVError as e:
        logger.warning("Apply-reply failed: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": str(e),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        capture_exception(e)
        logger.exception("Error applying inbound iTIP REPLY")
        return {
            "status": "FAILURE",
            "result": None,
            "error": _APPLY_REPLY_FAILURE,
        }


@celery_app.task(bind=True)
def calendar_add_event_task(
    self,  # pylint: disable=unused-argument
    channel_id: str | None,
    user_email: str,
    ics_data: str,
    calendar_id: str | None = None,
) -> Dict[str, Any]:
    """
    Add a calendar event to a CalDAV calendar.

    Args:
        channel_id: UUID of the CalDAV channel, or None for instance config
        user_email: Requesting user's OIDC identity email (Basic Auth user
            for instance config). The event is stored on a calendar owned
            by this user's CalDAV principal, not on the mailbox's.
        ics_data: Raw ICS content
        calendar_id: Optional specific calendar URL to use
    """
    try:
        service = _get_caldav_service(channel_id, user_email)
    except Channel.DoesNotExist as e:
        # Race: the row existed when the viewset enqueued the task, gone
        # by the time the worker ran. Worth a Sentry breadcrumb so we
        # can see if this happens at any volume.
        capture_exception(e)
        logger.warning(
            "CalDAV channel %s vanished between enqueue and execute", channel_id
        )
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV channel not found.",
        }
    except ValueError as e:
        # Configuration error (URL/password missing). User-facing message
        # is intentionally generic; full detail is on the worker logs.
        logger.warning("CalDAV service unavailable: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": "CalDAV service is not configured.",
        }

    try:
        service.add_event(ics_data=ics_data, calendar_id=calendar_id)

        return {
            "status": "SUCCESS",
            "result": {"added": True},
            "error": None,
        }
    except CalDAVError as e:
        # See ``calendar_rsvp_task`` — CalDAVError messages are
        # user-composed and safe to surface.
        logger.warning("Add-event failed: %s", e)
        return {
            "status": "FAILURE",
            "result": None,
            "error": str(e),
        }
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Error adding calendar event")
        return {
            "status": "FAILURE",
            "result": None,
            "error": _ADD_FAILURE,
        }
