"""API ViewSet for canceling queued send tasks."""

import logging

from celery.result import AsyncResult
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import exceptions as drf_exceptions
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.mda.tasks import celery_app

from .. import permissions, serializers

logger = logging.getLogger(__name__)


class CancelSendSerializer(drf_serializers.Serializer):
    """Serializer for canceling send tasks."""

    taskId = drf_serializers.CharField(required=True, help_text="Celery task ID to cancel")
    messageId = drf_serializers.UUIDField(required=True, help_text="Message ID to keep as draft")


@extend_schema(
    tags=["messages"],
    request=CancelSendSerializer,
    responses={
        200: OpenApiExample(
            "Success",
            value={"detail": "Send cancelled successfully."},
        ),
        400: OpenApiExample(
            "Validation Error",
            value={"detail": "Invalid request data."},
        ),
        404: OpenApiExample(
            "Not Found",
            value={"detail": "Message not found."},
        ),
    },
    description="""
    Cancel a queued send task and keep the message as a draft.

    This endpoint revokes the Celery task responsible for sending the message
    and ensures the message remains in draft state.
    """,
    examples=[
        OpenApiExample(
            "Cancel Send",
            value={
                "taskId": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                "messageId": "123e4567-e89b-12d3-a456-426614174000",
            },
        ),
    ],
)
class CancelSendView(APIView):
    """Cancel a queued send task."""

    permission_classes = [permissions.IsAllowedToAccess]

    def post(self, request):
        """Cancel a queued send task."""
        serializer = CancelSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        task_id = serializer.validated_data.get("taskId")
        message_id = serializer.validated_data.get("messageId")

        # Revoke the Celery task
        celery_app.control.revoke(task_id, terminate=True)
        logger.info(f"Revoked Celery task {task_id} for message {message_id}")

        # Ensure message stays as draft
        try:
            message = models.Message.objects.get(id=message_id)

            # Ensure message is marked as draft
            # Note: draft_blob is preserved during prepare_outbound_message(),
            # so it should still exist and doesn't need restoration
            needs_save = False
            update_fields = []

            if not message.is_draft:
                message.is_draft = True
                needs_save = True
                update_fields.append("is_draft")

            # Remove the blob (MIME) if it exists - drafts shouldn't have a blob
            # The blob was created by prepare_outbound_message() but we're cancelling
            if message.blob:
                try:
                    message.blob.delete()
                    logger.info(f"Deleted blob {message.blob_id} for cancelled message {message_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete blob for message {message_id}: {e}")

                message.blob = None
                needs_save = True
                update_fields.append("blob")

            if needs_save:
                message.save(update_fields=update_fields)
                # Update thread stats since message state changed
                message.thread.update_stats()
                logger.info(f"Message {message_id} reverted to draft state after send cancellation")
            else:
                logger.info(f"Message {message_id} was already in correct draft state")
        except models.Message.DoesNotExist as e:
            # Message might have been deleted, but task was revoked anyway
            logger.warning(f"Message {message_id} not found during cancel, but task {task_id} was revoked")
            raise drf_exceptions.NotFound("Message not found.") from e

        return Response(
            {"detail": "Send cancelled successfully."},
            status=status.HTTP_200_OK,
        )
