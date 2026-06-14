"""API ViewSet for sending messages."""

import logging
import uuid

from django.db import transaction

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

from core import enums, models
from core.api.viewsets.task import register_task_owner
from core.mda.outbound import prepare_outbound_message
from core.mda.outbound_tasks import send_message_task

from .. import permissions, serializers

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["messages"],
    request=serializers.SendMessageSerializer,
    responses={
        200: inline_serializer(
            name="SendMessageResponse",
            fields={
                "task_id": drf_serializers.UUIDField(help_text="Task ID for tracking"),
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
        500: OpenApiExample(
            "Prepare Failure",
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
            request_only=True,
        ),
        OpenApiExample(
            "Send Draft Result",
            value={"task_id": "123e4567-e89b-12d3-a456-426614174000"},
            response_only=True,
        ),
    ],
)
class SendMessageView(APIView):
    """Send a previously created draft message."""

    permission_classes = [permissions.IsAllowedToAccess]
    # Note: IsAllowedToAccess checks object permission based on ThreadAccess now.
    # We still need senderId for the sending context.

    action = "send"

    def post(self, request):
        """Send a draft message identified by messageId."""
        serializer = serializers.SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message_id = serializer.validated_data.get("messageId")
        sender_id = serializer.validated_data.get("senderId")
        must_archive = serializer.validated_data.get("archive", False) is True

        try:
            mailbox_sender = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist as e:
            raise drf_exceptions.NotFound("Sender mailbox not found.") from e

        try:
            message = (
                models.Message.objects.select_related("sender")
                .prefetch_related(
                    "thread__accesses", "recipients__contact", "attachments__blob"
                )
                .get(
                    id=message_id,
                    is_draft=True,
                    thread__accesses__mailbox=mailbox_sender,
                )
            )
        except models.Message.DoesNotExist as e:
            raise drf_exceptions.NotFound(
                "Draft message not found or does not belong to the specified sender mailbox."
            ) from e

        self.check_object_permissions(request, message)

        # The sender mailbox itself must be authorised to send on this thread.
        # ``IsAllowedToAccess`` only proves the user can SEND through *some*
        # mailbox holding EDITOR access to the thread — not necessarily
        # ``mailbox_sender``. Re-check against the specific ``senderId`` so a
        # VIEWER on the sender mailbox cannot send as it by piggy-backing on a
        # SENDER role they hold on a different mailbox sharing the thread.
        can_send_as_sender = models.ThreadAccess.objects.filter(
            thread=message.thread,
            mailbox=mailbox_sender,
            role=enums.ThreadAccessRoleChoices.EDITOR,
            mailbox__accesses__user=request.user,
            mailbox__accesses__role__in=enums.MAILBOX_ROLES_CAN_SEND,
        ).exists()
        if not can_send_as_sender:
            raise drf_exceptions.PermissionDenied(
                "You do not have permission to send as this mailbox."
            )

        # Pre-generate the Celery task id so we can return it to the caller
        # while still deferring the actual dispatch to ``transaction.on_commit``
        # below — the broker must never receive a delivery task for a message
        # whose finalized state is still uncommitted (or rolled back).
        task_id = str(uuid.uuid4())

        with transaction.atomic():
            prepared = prepare_outbound_message(
                mailbox_sender,
                message,
                request.data.get("textBody"),
                request.data.get("htmlBody"),
                request.user,
            )
            if not prepared:
                raise drf_exceptions.APIException(
                    "Failed to prepare message for sending.",
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            register_task_owner(task_id, request.user.id)

            # Dispatch only once the message's finalized state is durable.
            transaction.on_commit(
                lambda: send_message_task.apply_async(
                    args=[str(message.id)],
                    kwargs={"must_archive": must_archive},
                    task_id=task_id,
                )
            )

            # --- Finalize ---
            # Message state was updated by prepare_outbound_message (e.g.
            # is_draft=False); refresh and update thread stats in the same
            # transaction so the un-drafting and stats commit atomically.
            message.refresh_from_db()
            message.thread.update_stats()

        return Response({"task_id": task_id}, status=status.HTTP_200_OK)
