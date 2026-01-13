"""Widget channel implementation for receiving messages from web widgets."""

import logging
from html import escape as html_escape
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response

from core import models
from core.api.permissions import IsAuthenticated
from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import compose_email

logger = logging.getLogger(__name__)


class WidgetAuthentication(BaseAuthentication):
    """
    Custom authentication for widget endpoints using channel_id header
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        # Try API key authentication first
        channel_id = request.headers.get("X-Channel-ID")
        if not channel_id:
            raise AuthenticationFailed("Missing channel_id")

        # API key authentication for check endpoint
        try:
            channel = models.Channel.objects.get(id=channel_id)
        except models.Channel.DoesNotExist as e:
            raise AuthenticationFailed("Invalid channel_id") from e

        return (None, {"channel": channel})


class InboundWidgetViewSet(viewsets.GenericViewSet):
    """Handles incoming messages from web widgets."""

    # Channel metadata
    CHANNEL_TYPE = "widget"
    CHANNEL_DESCRIPTION = "Web widgets and forms"

    permission_classes = [IsAuthenticated]
    authentication_classes = [WidgetAuthentication]

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["get"],
        url_path="config",
        url_name="inbound-widget-config",
    )
    def config(self, request):
        """Return the configuration for the widget."""

        auth_data = request.auth
        channel = auth_data["channel"]

        return Response(
            {"success": True, "config": (channel.settings or {}).get("config") or {}}
        )

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-widget-deliver",
    )
    def deliver(self, request):
        """Handle incoming widget message."""

        # TODO: throttle

        data = request.data
        auth_data = request.auth
        channel = auth_data["channel"]

        sender_email = data.get("email")
        message_text = data.get("textBody", "")

        if not sender_email:
            return Response(
                {"detail": "Missing email"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Validate the sender email format with django's email validator
        try:
            validate_email(sender_email)
        except ValidationError:
            return Response(
                {"detail": "Invalid email format"}, status=status.HTTP_400_BAD_REQUEST
            )

        if not message_text:
            return Response(
                {"detail": "Missing message"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Get the target mailbox
        mailbox = channel.mailbox
        if not mailbox:
            return Response(
                {"detail": "No mailbox configured for this channel"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if mailbox.contact:
            target_email = mailbox.contact.email
            target_name = mailbox.contact.name
        else:
            target_email = str(mailbox)
            target_name = str(mailbox)

        def sanitize_header(header: str) -> str:
            return header.replace("\r", "").replace("\n", "")[0:1000]

        prepend_headers = [("X-StMsg-Sender-Auth", "none")]
        source_name = "widget"
        if request.META.get("HTTP_REFERER"):
            referer = sanitize_header(request.META.get("HTTP_REFERER"))
            prepend_headers.append(("X-StMsg-Widget-Referer", referer))
            try:
                parsed_referer = urlparse(referer)
                if parsed_referer.netloc:
                    source_name = parsed_referer.netloc
            except ValueError as e:
                logger.warning("Cannot retrieve netloc from referer %s: %s", referer, e)

        prepend_headers.append(
            (
                "Received",
                f"from widget ({sanitize_header(request.META.get('REMOTE_ADDR'))})",
            ),
        )

        # Build a JMAP-like structured format that we could have got from parse_email_message()

        parsed_email = {
            "subject": f"Message from {source_name}",
            "from": {"email": sender_email},
            "to": [{"name": target_name, "email": target_email}],
            "date": timezone.now(),
            "htmlBody": [{"content": html_escape(message_text).replace("\n", "<br/>")}],
            "textBody": [{"content": message_text}],
        }

        delivered = deliver_inbound_message(
            target_email,
            parsed_email,
            compose_email(parsed_email, prepend_headers=prepend_headers),
            channel=channel,
        )

        if not delivered:
            return Response(
                {"detail": "Failed to deliver message"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "Successfully created message from widget for channel %s, sender: %s",
            channel.id,
            sender_email,
        )

        return Response(
            {
                "success": True,
            }
        )
