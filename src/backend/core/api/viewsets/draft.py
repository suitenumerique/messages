"""API ViewSet for creating and updating draft messages."""

import json
import logging

from django.db import transaction

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import enums, models
from core.mda.draft import create_draft, update_draft

from .. import permissions, serializers

# Define logger
logger = logging.getLogger(__name__)


@extend_schema(
    tags=["messages"],
    request=inline_serializer(
        name="DraftMessageRequest",
        fields={
            "messageId": drf_serializers.UUIDField(
                required=False,
                allow_null=True,
                help_text="Message ID if updating an existing draft",
            ),
            "parentId": drf_serializers.UUIDField(
                required=False,
                allow_null=True,
                help_text="Message ID if replying to an existing message",
            ),
            "senderId": drf_serializers.UUIDField(
                required=True,
                help_text="Mailbox ID to use as sender",
            ),
            "subject": drf_serializers.CharField(
                required=False,
                allow_blank=True,
                allow_null=True,
                help_text="Subject of the message (optional; empty or null allowed)",
            ),
            "draftBody": drf_serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Content of the draft message as arbitrary text (usually JSON)",
            ),
            "to": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=False,
                help_text="List of recipient email addresses",
            ),
            "cc": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=False,
                default=list,
                help_text="List of CC recipient email addresses",
            ),
            "bcc": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=False,
                default=list,
                help_text="List of BCC recipient email addresses",
            ),
            "attachments": drf_serializers.ListField(
                child=drf_serializers.DictField(),
                required=False,
                default=list,
                help_text="List of attachment objects with blobId, partId, and name",
            ),
            "signatureId": drf_serializers.UUIDField(
                required=False,
                allow_null=True,
                help_text="ID of the signature template to use",
            ),
        },
    ),
    responses={
        201: serializers.MessageSerializer,
        200: serializers.MessageSerializer,
        400: OpenApiExample(
            "Validation Error",
            value={"detail": "Missing or invalid required fields."},
        ),
        403: OpenApiExample(
            "Permission Error",
            value={"detail": "You do not have permission to perform this action."},
        ),
        404: OpenApiExample(
            "Not Found",
            value={"detail": "Message does not exist or is not a draft."},
        ),
    },
    description="""
    Create or update a draft message.
    
    This endpoint allows you to:
    - Create a new draft message in a new thread
    - Create a draft reply to an existing message in an existing thread
    - Update an existing draft message
    
    For creating a new draft:
    - Do not include messageId
    - Include parentId if replying to an existing message
    
    For updating an existing draft:
    - Include messageId of the draft to update
    - Only the fields that are provided will be updated
    
    At least one of draftBody must be provided.
    
    To add attachments, upload them first using the /api/v1.0/blob/upload/{mailbox_id}/ endpoint
    and include the returned blobIds in the attachmentIds field.
    """,
    examples=[
        OpenApiExample(
            "New Draft Message",
            value={
                "subject": "Hello",
                "draftBody": json.dumps({"arbitrary": "json content"}),
                "to": ["recipient@example.com"],
                "cc": ["cc@example.com"],
                "bcc": ["bcc@example.com"],
                "signatureId": "123e4567-e89b-12d3-a456-426614174000",
            },
        ),
        OpenApiExample(
            "Draft Reply",
            value={
                "parentId": "123e4567-e89b-12d3-a456-426614174000",
                "subject": "Re: Hello",
                "draftBody": json.dumps({"arbitrary": "json content"}),
                "to": ["recipient@example.com"],
            },
        ),
        OpenApiExample(
            "Update Draft with Attachments",
            value={
                "messageId": "123e4567-e89b-12d3-a456-426614174000",
                "subject": "Updated subject",
                "draftBody": json.dumps({"arbitrary": "new json content"}),
                "to": ["new-recipient@example.com"],
                "signatureId": "123e4567-e89b-12d3-a456-426614174000",
                "attachments": [
                    {
                        "partId": "att-1",
                        "blobId": "123e4567-e89b-12d3-a456-426614174001",
                        "name": "document.pdf",
                    }
                ],
            },
        ),
    ],
)
class DraftMessageView(APIView):
    """Create or update a draft message.

    This endpoint is used to create a new draft message, draft reply, or update an existing draft.

    POST /api/v1.0/draft/ with expected data:
        - parentId: str (optional, message id if reply, None if first message)
        - senderId: str (mailbox id of the sender)
        - subject: str (optional)
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        - attachmentIds: list[str] (optional, IDs of previously uploaded blobs)
        - signatureId: str (optional, ID of the signature template to use)
        Return newly created draft message

    PUT /api/v1.0/draft/{message_id}/ with expected data:
        - subject: str (optional)
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        - attachmentIds: list[str] (optional, IDs of previously uploaded blobs)
        - signatureId: str (optional, ID of the signature template to use)
        Return updated draft message
    """

    permission_classes = [permissions.IsAllowedToCreateMessage]
    mailbox = None

    @transaction.atomic
    def post(self, request):
        """Create a new draft message."""
        # Validate required fields
        sender_id = request.data.get("senderId")
        if not sender_id:
            raise drf.exceptions.ValidationError("senderId is required")

        subject = request.data.get("subject")

        # Get mailbox (permission class validates access)
        try:
            sender_mailbox = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                f"Mailbox with senderId {sender_id} not found."
            ) from exc

        # Create draft
        message = create_draft(
            mailbox=sender_mailbox,
            subject=subject,
            draft_body=request.data.get("draftBody", ""),
            parent_id=request.data.get("parentId"),
            to_emails=request.data.get("to", []),
            cc_emails=request.data.get("cc", []),
            bcc_emails=request.data.get("bcc", []),
            attachments=request.data.get("attachments", []),
            signature_id=request.data.get("signatureId"),
        )

        # Refresh to get latest data
        message.refresh_from_db()
        return Response(
            serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED
        )

    @transaction.atomic
    def put(self, request, message_id: str):
        """Update an existing draft message."""
        if not message_id:
            raise drf.exceptions.ValidationError(
                "Message ID is required for updating a draft."
            )

        # Validate required fields
        sender_id = request.data.get("senderId")
        if not sender_id:
            raise drf.exceptions.ValidationError(
                "senderId is required in request body for update."
            )

        # Get mailbox
        try:
            sender_mailbox = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                f"Mailbox with senderId {sender_id} not found."
            ) from exc

        # Get the draft message
        try:
            message = models.Message.objects.select_related("thread", "draft_blob").get(
                id=message_id,
                is_draft=True,
                # Ensure the user has access to this thread
                thread__accesses__mailbox=sender_mailbox,
                thread__accesses__role=enums.ThreadAccessRoleChoices.EDITOR,
            )
        except models.Message.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                "Draft message not found, is not a draft, or access denied."
            ) from exc

        # Update draft using the new function
        updated_message = update_draft(sender_mailbox, message, request.data)

        # Update thread stats
        updated_message.thread.update_stats()

        # Refresh to get latest data
        updated_message.refresh_from_db()
        serializer = serializers.MessageSerializer(
            updated_message, context={"request": request}
        )
        return Response(serializer.data)
