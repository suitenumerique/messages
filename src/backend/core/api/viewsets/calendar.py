"""API ViewSet for calendar operations (RSVP, conflict detection, calendar listing)."""

import logging
from datetime import datetime

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core import enums, models
from core.api.permissions import HasAccessToMailbox, HasWriteAccessToMailbox
from core.api.viewsets.task import register_task_owner
from core.services.calendar.service import CalDAVError, CalDAVService
from core.services.calendar.tasks import calendar_add_event_task, calendar_rsvp_task

logger = logging.getLogger(__name__)

# Shared OpenAPI error-response schema for the calendar endpoints. Inline
# here so each endpoint's @extend_schema can reference it without dragging
# a one-off serializer class through drf_spectacular.
_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
}


class CalDAVChannelMixin:
    """Mixin to get the CalDAV channel or deployment-default config for a mailbox."""

    @cached_property
    def mailbox(self):
        """The Mailbox referenced in the URL."""
        return get_object_or_404(models.Mailbox, id=self.kwargs["mailbox_id"])

    @cached_property
    def caldav_channel(self):
        """The CalDAV channel for the mailbox, if any."""
        return models.Channel.objects.filter(
            mailbox=self.mailbox, type=enums.ChannelTypes.CALDAV
        ).first()

    def get_caldav_service(self):
        """Get a CalDAVService for this mailbox.

        Priority: per-mailbox Channel (user-configured, pointing at any
        CalDAV provider) > deployment-default config (``CALDAV_DEFAULT_*``
        env vars). For the default path, the mailbox email is sent as the
        Basic Auth username so the CalDAV server can route to the user's
        calendars via principal discovery — see
        ``CalDAVService.from_instance_config`` for the trust model.
        Returns None if neither is configured.
        """
        return CalDAVService.from_channel_or_instance(
            self.caldav_channel, str(self.mailbox)
        )

    def require_caldav_service(self):
        """Get the CalDAVService or raise 404."""
        service = self.get_caldav_service()
        if not service:
            raise NotFound("No CalDAV calendar is configured for this mailbox.")
        return service


@extend_schema(tags=["calendar"])
class CalendarRsvpView(CalDAVChannelMixin, APIView):
    """Submit an RSVP response to a calendar event."""

    # Writing an RSVP on behalf of a mailbox is a CalDAV write that produces
    # an outbound iTIP REPLY — VIEWER-only access must not be able to do it.
    permission_classes = [HasWriteAccessToMailbox]

    @extend_schema(
        request=inline_serializer(
            name="CalendarRsvpRequest",
            fields={
                "ics_data": drf_serializers.CharField(
                    help_text="Raw ICS content of the event"
                ),
                "response": drf_serializers.ChoiceField(
                    choices=["ACCEPTED", "DECLINED", "TENTATIVE"],
                    help_text="RSVP response",
                ),
                "calendar_id": drf_serializers.CharField(
                    required=False,
                    allow_null=True,
                    help_text="Optional specific calendar URL",
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="CalendarRsvpResponse",
                fields={
                    "task_id": drf_serializers.CharField(),
                },
            ),
            400: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="Missing or invalid ics_data / response.",
            ),
            503: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="Task broker unavailable; the RSVP could not be enqueued.",
            ),
        },
    )
    def post(self, request, mailbox_id):  # pylint: disable=unused-argument
        """Submit an RSVP response via a background CalDAV task."""
        ics_data = request.data.get("ics_data")
        response_type = request.data.get("response")
        calendar_id = request.data.get("calendar_id")

        if not ics_data or not response_type:
            return Response(
                {"detail": "ics_data and response are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if response_type not in ("ACCEPTED", "DECLINED", "TENTATIVE"):
            return Response(
                {"detail": "response must be ACCEPTED, DECLINED, or TENTATIVE."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.require_caldav_service()
        channel = self.caldav_channel
        mailbox_email = str(self.mailbox)

        try:
            task = calendar_rsvp_task.delay(
                channel_id=str(channel.id) if channel else None,
                mailbox_email=mailbox_email,
                ics_data=ics_data,
                response=response_type,
                attendee_email=mailbox_email,
                calendar_id=calendar_id,
            )
            register_task_owner(task.id, request.user.id)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to enqueue calendar_rsvp_task: %s", e)
            return Response(
                {"detail": "Could not schedule the RSVP task."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"task_id": task.id}, status=status.HTTP_200_OK)


@extend_schema(tags=["calendar"])
class CalendarAddEventView(CalDAVChannelMixin, APIView):
    """Add an event to a CalDAV calendar."""

    # Writing an event into the mailbox's calendar must not be allowed for
    # VIEWER-only access.
    permission_classes = [HasWriteAccessToMailbox]

    @extend_schema(
        request=inline_serializer(
            name="CalendarAddEventRequest",
            fields={
                "ics_data": drf_serializers.CharField(
                    help_text="Raw ICS content of the event"
                ),
                "calendar_id": drf_serializers.CharField(
                    required=False,
                    allow_null=True,
                    help_text="Optional specific calendar URL",
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="CalendarAddEventResponse",
                fields={
                    "task_id": drf_serializers.CharField(),
                },
            ),
            400: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="Missing ics_data.",
            ),
            503: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="Task broker unavailable; the add-event could not be enqueued.",
            ),
        },
    )
    def post(self, request, mailbox_id):  # pylint: disable=unused-argument
        """Add an event to the mailbox's CalDAV calendar via a background task."""
        ics_data = request.data.get("ics_data")
        calendar_id = request.data.get("calendar_id")

        if not ics_data:
            return Response(
                {"detail": "ics_data is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.require_caldav_service()
        channel = self.caldav_channel

        try:
            task = calendar_add_event_task.delay(
                channel_id=str(channel.id) if channel else None,
                mailbox_email=str(self.mailbox),
                ics_data=ics_data,
                calendar_id=calendar_id,
            )
            register_task_owner(task.id, request.user.id)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to enqueue calendar_add_event_task: %s", e)
            return Response(
                {"detail": "Could not schedule the add-event task."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"task_id": task.id}, status=status.HTTP_200_OK)


@extend_schema(tags=["calendar"])
class CalendarConflictsView(CalDAVChannelMixin, APIView):
    """Check for conflicting events in a given time range.

    Note: CalDAV calls are intentionally blocking (synchronous) here because
    the user is waiting for the result before interacting with the UI.

    Throttled per user under the ``caldav_conflicts`` scope. Each call
    PROPFINDs the home set and REPORTs every calendar in it, so a tight
    polling loop both stresses the CalDAV server and ties up request
    workers; a 30/min cap is generous for legitimate UI use (one call
    per opened invite) and bounds the cost of a runaway script.
    """

    permission_classes = [HasAccessToMailbox]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "caldav_conflicts"

    @extend_schema(
        request=inline_serializer(
            name="CalendarConflictsRequest",
            fields={
                "start": drf_serializers.DateTimeField(
                    help_text="Start of the time range (ISO 8601)"
                ),
                "end": drf_serializers.DateTimeField(
                    help_text="End of the time range (ISO 8601)"
                ),
                "exclude_uid": drf_serializers.CharField(
                    required=False,
                    allow_null=True,
                    allow_blank=True,
                    help_text=(
                        "Optional UID of an event to exclude from conflicts "
                        "(avoids flagging prior imports of the same invite)."
                    ),
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="CalendarConflictsResponse",
                fields={
                    "conflicts": drf_serializers.ListField(
                        child=drf_serializers.DictField()
                    ),
                    "existing_partstat": drf_serializers.CharField(
                        allow_null=True,
                        help_text=(
                            "PARTSTAT of the requesting mailbox on the prior "
                            "copy of ``exclude_uid``, if such a copy exists. "
                            "Lets the UI pre-select the user's prior RSVP."
                        ),
                    ),
                },
            ),
            400: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="Missing or invalid start/end.",
            ),
            502: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="CalDAV server error while checking conflicts.",
            ),
        },
    )
    def post(self, request, mailbox_id):  # pylint: disable=unused-argument
        """Return a list of events overlapping the requested time range."""
        start = request.data.get("start")
        end = request.data.get("end")
        exclude_uid = request.data.get("exclude_uid") or None

        if not start or not end:
            return Response(
                {"detail": "start and end are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            if isinstance(end, str):
                end = datetime.fromisoformat(end)
        except (ValueError, TypeError):
            return Response(
                {"detail": "start and end must be valid ISO 8601 datetimes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The CalDAV time-range filter (`_format_utc`) expects aware
        # datetimes — silently treating naive input as UTC would lie
        # about the wall-clock value sent to the server.
        if start.tzinfo is None or end.tzinfo is None:
            return Response(
                {"detail": "start and end must include timezone info."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if end <= start:
            return Response(
                {"detail": "end must be after start."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = self.require_caldav_service()

        try:
            result = service.check_conflicts(
                start=start,
                end=end,
                exclude_uid=exclude_uid,
                attendee_email=str(self.mailbox),
            )
        except CalDAVError as e:
            # Upstream CalDAV failure (network, 4xx/5xx from server,
            # SSRF guard tripped, etc.) — 502 is the right shape.
            logger.warning("CalDAV upstream failed during conflicts: %s", e)
            return Response(
                {"detail": "CalDAV server returned an error while checking conflicts."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Anything else is an unexpected programming error — let Django's
        # default handler return 500 so it doesn't get mis-labelled as
        # an upstream issue.

        return Response(result, status=status.HTTP_200_OK)


@extend_schema(tags=["calendar"])
class CalendarListView(CalDAVChannelMixin, APIView):
    """List available calendars on the CalDAV server.

    Note: CalDAV calls are intentionally blocking (synchronous) here because
    the user is waiting for the result before interacting with the UI.
    """

    permission_classes = [HasAccessToMailbox]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="CalendarListResponse",
                fields={
                    "calendars": drf_serializers.ListField(
                        child=drf_serializers.DictField()
                    ),
                    "web_url": drf_serializers.CharField(
                        allow_null=True,
                        help_text=("Public URL of the calendar web UI, if configured."),
                    ),
                    "configured": drf_serializers.BooleanField(
                        help_text=(
                            "True when a CalDAV service is configured for this "
                            "mailbox (per-mailbox channel or deployment default). "
                            "False means the integration is disabled."
                        ),
                    ),
                },
            ),
            502: OpenApiResponse(
                response=_ERROR_SCHEMA,
                description="CalDAV server error while listing calendars.",
            ),
        },
    )
    def get(self, request, mailbox_id):  # pylint: disable=unused-argument
        """Return the list of calendars available for the mailbox."""
        web_url = settings.CALDAV_DEFAULT_WEB_URL
        service = self.get_caldav_service()
        if not service:
            return Response(
                {"calendars": [], "web_url": web_url, "configured": False},
                status=status.HTTP_200_OK,
            )

        try:
            # Only list calendars the user can write to — the UI uses this
            # to pick a destination for RSVP/add-event, and read-only
            # calendars would fail at PUT time.
            calendars = service.list_calendars(writable_only=True)
        except CalDAVError as e:
            # 403 from the CalDAV proxy means "we authenticated the
            # service credential but the mailbox email is not a known
            # user upstream" — effectively this mailbox has no calendar
            # account. Surface it like an unconfigured integration so
            # the UI hides the footer rather than showing a misleading
            # "service unavailable" message.
            if e.status_code == 403:
                logger.info(
                    "CalDAV reports mailbox %s has no calendar account (HTTP 403).",
                    mailbox_id,
                )
                return Response(
                    {"calendars": [], "web_url": web_url, "configured": False},
                    status=status.HTTP_200_OK,
                )
            logger.warning("CalDAV upstream failed during list_calendars: %s", e)
            return Response(
                {"detail": "CalDAV server returned an error while listing calendars."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Anything else is unexpected — let Django return 500.

        return Response(
            {"calendars": calendars, "web_url": web_url, "configured": True},
            status=status.HTTP_200_OK,
        )
