"""API ViewSet for sending messages."""

import logging

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import exceptions as drf_exceptions
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.mda.delivery import prepare_outbound_message, send_outbound_message

from .. import permissions, serializers

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["messages"],
    request=inline_serializer(
        name="SendMessageRequest",
        fields={
            "messageId": drf_serializers.UUIDField(
                required=True,
                help_text="ID of the draft message to send",
            ),
            "senderId": drf_serializers.UUIDField(
                required=True,
                help_text="Mailbox ID from which to send (must match draft)",
            ),
            "textBody": drf_serializers.CharField(
                required=False,
                help_text="Text body of the message",
            ),
            "htmlBody": drf_serializers.CharField(
                required=False,
                help_text="HTML body of the message",
            ),
        },
    ),
    responses={
        200: serializers.MessageSerializer,
        400: OpenApiExample(
            "Validation Error",
            value={"detail": "Message does not exist or is not a draft."},
        ),
        403: OpenApiExample(
            "Permission Error",
            value={"detail": "You do not have permission to send this message."},
        ),
        503: OpenApiExample(
            "Service Unavailable", value={"detail": "Failed to send message via MTA."}
        ),
    },
    description="""
    Send a previously created draft message.

    This endpoint finalizes and sends a message previously saved as a draft.
    The message content (subject, body, recipients) should be set when creating/updating the draft.
    """,
    examples=[
        OpenApiExample(
            "Send Draft",
            value={
                "messageId": "123e4567-e89b-12d3-a456-426614174000",
                "senderId": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "textBody": "Hello, world!",
                "htmlBody": "<p>Hello, world!</p>",
            },
        ),
    ],
)
class SendMessageView(APIView):
    """Send a previously created draft message."""

    permission_classes = [permissions.IsAllowedToAccessMailbox]

    def post(self, request):
        """Send a draft message identified by messageId."""
        message_id = request.data.get("messageId")
        sender_id = request.data.get("senderId")

        if not message_id:
            raise drf_exceptions.ValidationError("messageId is required.")
        if not sender_id:
            raise drf_exceptions.ValidationError("senderId is required.")

        try:
            message = (
                models.Message.objects.select_related("thread__mailbox", "sender")
                .prefetch_related("recipients__contact")
                .get(id=message_id, is_draft=True, thread__mailbox_id=sender_id)
            )
        except models.Message.DoesNotExist as e:
            raise drf_exceptions.NotFound(
                "Draft message not found or does not belong to the specified sender mailbox."
            ) from e

        prepared = prepare_outbound_message(
            message, request.data.get("textBody"), request.data.get("htmlBody")
        )
        if not prepared:
            raise drf_exceptions.APIException(
                "Failed to prepare message for sending.",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # TODO: this part should be done in a background task
        try:
            send_successful = send_outbound_message(message)
        except Exception as e:
            logger.error(
                "Unexpected error calling send_outbound_message for %s: %s",
                message_id,
                e,
                exc_info=True,
            )
            raise drf_exceptions.APIException(
                "An unexpected error occurred while preparing to send the message.",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from e

        if not send_successful:
            # Raise exception with explicit status_code attribute
            exc = drf_exceptions.APIException(
                "Failed to send message via MTA after multiple attempts.",
            )
            exc.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            raise exc

        serializer = serializers.MessageSerializer(
            message, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
