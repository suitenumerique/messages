"""Webhook channel implementation for receiving messages from external services."""

import logging
from html import escape as html_escape
from secrets import compare_digest

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


class WebhookAuthentication(BaseAuthentication):
    """
    Custom authentication for webhook endpoints with configurable auth methods.
    Currently supports API Key authentication.
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        # Get channel ID from header
        channel_id = request.headers.get("X-Channel-ID")
        if not channel_id:
            raise AuthenticationFailed("Missing X-Channel-ID header")

        try:
            channel = models.Channel.objects.get(id=channel_id)
        except models.Channel.DoesNotExist as e:
            raise AuthenticationFailed("Invalid channel ID") from e

        # Get authentication method from channel settings
        auth_method = (channel.settings or {}).get("auth_method", "api_key")

        if auth_method == "api_key":
            return self._authenticate_api_key(request, channel)

        raise AuthenticationFailed(f"Unsupported authentication method: {auth_method}")

    def _authenticate_api_key(self, request, channel):
        """Authenticate using API key from channel settings."""
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise AuthenticationFailed("Missing X-API-Key header")

        expected_api_key = (channel.settings or {}).get("api_key")
        if not expected_api_key:
            raise AuthenticationFailed("API key not configured for this channel")

        # Use constant-time comparison to prevent timing attacks
        if not compare_digest(api_key, expected_api_key):
            raise AuthenticationFailed("Invalid API key")

        return (None, {"channel": channel, "auth_method": "api_key"})

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'ApiKey realm="Webhook"'


class InboundWebhookViewSet(viewsets.GenericViewSet):
    """Handles incoming messages from webhooks with configurable authentication."""

    # Channel metadata
    CHANNEL_TYPE = "webhook"
    CHANNEL_DESCRIPTION = "Generic webhook integration"

    permission_classes = [IsAuthenticated]
    authentication_classes = [WebhookAuthentication]

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="message",
        url_name="inbound-webhook-message",
    )
    def message(self, request):
        """Handle incoming webhook message."""
        # TODO: Add rate limiting/throttling

        data = request.data
        auth_data = request.auth
        channel = auth_data["channel"]

        # Extract message data with standard field names
        sender_email = data.get("from", {}).get("email")
        sender_name = data.get("from", {}).get("name")
        message_text = data.get("message", "")
        subject = data.get("subject", "Message from webhook")

        # Validate required fields
        if not sender_email:
            return Response(
                {"detail": "Missing email"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Validate the sender email format
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

        # Determine target email and name
        if mailbox.contact:
            target_email = mailbox.contact.email
            target_name = mailbox.contact.name
        else:
            target_email = str(mailbox)
            target_name = str(mailbox)

        # Build sender information
        sender_info = {"email": sender_email}
        if sender_name:
            sender_info["name"] = sender_name

        # Sanitize headers to prevent header injection
        def sanitize_header(header: str) -> str:
            return header.replace("\r", "").replace("\n", "")[0:1000]

        # Add webhook-specific headers
        prepend_headers = [("X-StMsg-Sender-Auth", "webhook")]

        prepend_headers.append(
            (
                "Received",
                f"from webhook ({sanitize_header(request.META.get('REMOTE_ADDR'))})",
            )
        )

        # Build a JMAP-like structured format
        parsed_email = {
            "subject": subject,
            "from": sender_info,
            "to": [{"name": target_name, "email": target_email}],
            "date": timezone.now(),
            "htmlBody": [{"content": html_escape(message_text).replace("\n", "<br/>")}],
            "textBody": [{"content": message_text}],
        }

        # Deliver the message
        message = deliver_inbound_message(
            target_email,
            parsed_email,
            compose_email(parsed_email, prepend_headers=prepend_headers),
            channel=channel,
        )

        if not message:
            return Response(
                {"detail": "Failed to deliver message"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "Successfully created message from webhook for channel %s, sender: %s, message: %s, thread: %s",
            channel.id,
            sender_email,
            message.id,
            message.thread.id,
        )

        return Response(
            {
                "success": True,
                "message": "Message delivered successfully",
                "message_id": str(message.id),
                "thread_id": str(message.thread.id),
            }
        )

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="threadevent",
        url_name="inbound-webhook-threadevent",
    )
    def threadevent(self, request):
        """Handle incoming webhook thread event."""
        # TODO: Add rate limiting/throttling

        data = request.data
        auth_data = request.auth
        channel = auth_data["channel"]

        # Extract thread event data
        thread_id = data.get("thread_id")
        event_type = data.get("type")
        event_data = data.get("data", {})

        # Validate required fields
        if not thread_id:
            return Response(
                {"detail": "Missing thread_id"}, status=status.HTTP_400_BAD_REQUEST
            )

        if not event_type:
            return Response(
                {"detail": "Missing type"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Validate event type length
        if len(event_type) > 36:
            return Response(
                {"detail": "Type exceeds maximum length of 36 characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the target mailbox
        mailbox = channel.mailbox
        if not mailbox:
            return Response(
                {"detail": "No mailbox configured for this channel"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Verify thread exists and mailbox has access to it
        try:
            thread = models.Thread.objects.get(id=thread_id)
        except models.Thread.DoesNotExist:
            return Response(
                {"detail": "Thread not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # Check if mailbox has access to this thread
        thread_access = models.ThreadAccess.objects.filter(
            thread=thread, mailbox=mailbox
        ).first()
        if not thread_access:
            return Response(
                {"detail": "Mailbox does not have access to this thread"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Create the thread event
        thread_event = models.ThreadEvent.objects.create(
            thread=thread,
            type=event_type,
            channel=channel,
            data=event_data,
        )

        logger.info(
            "Successfully created thread event from webhook for channel %s, thread %s, type %s",
            channel.id,
            thread.id,
            event_type,
        )

        return Response(
            {
                "success": True,
                "message": "Thread event created successfully",
                "event_id": str(thread_event.id),
            },
            status=status.HTTP_201_CREATED,
        )
