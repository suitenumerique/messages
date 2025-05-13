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
from core.mda.outbound import prepare_outbound_message
from core.tasks import send_message_task

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
        200: inline_serializer(
            name="SendMessageResponse",
            fields={
                "message": serializers.MessageSerializer(),
                "task_id": drf_serializers.CharField(help_text="Task ID for tracking"),
            },
        ),
        400: OpenApiExample(
            "Validation Error",
            value={"detail": "Message does not exist or is not a draft."},
        ),
        403: OpenApiExample(
            "Permission Error",
            value={"detail": "You do not have permission to send this message."},
        ),
        503: OpenApiExample(
            "Service Unavailable",
            value={"detail": "Failed to prepare message for sending."},
        ),
    },
    description="""
    Send a previously created draft message.

    This endpoint finalizes and sends a message previously saved as a draft.
    The message content (subject, body, recipients) should be set when creating/updating the draft.
    Returns a task ID that can be used to track the sending status.
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

    permission_classes = [permissions.IsAllowedToAccess]
    # Note: IsAllowedToAccess checks object permission based on ThreadAccess now.
    # We still need senderId for the sending context.

    action = "send"  # TODO: check permission for this action

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
                models.Message.objects.select_related("sender")
                .prefetch_related(
                    "thread__accesses", "recipients__contact", "attachments__blob"
                )
                .get(
                    id=message_id, is_draft=True, thread__accesses__mailbox_id=sender_id
                )
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

        # Launch async task for sending the message
        task = send_message_task.delay(str(message.id))

        # --- Finalize ---
        # Message state should be updated by prepare_outbound_message/send_message
        # Refresh from DB to get final state (e.g., is_draft=False)
        message.refresh_from_db()

        # Update thread stats after un-drafting
        message.thread.update_stats()

        return Response({"task_id": task.id}, status=status.HTTP_200_OK)
