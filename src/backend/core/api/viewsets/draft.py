"""API ViewSet for creating and updating draft messages."""

import json
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
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

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _resolve_editable_sender_mailbox(user, sender_id):
        """Return the ``sender_id`` mailbox the ``user`` may draft/send from.

        Resolves and authorizes in one query: the mailbox is looked up scoped
        to a ``MAILBOX_ROLES_CAN_EDIT`` access for ``user``. Raises 403 when the
        mailbox is missing OR the user lacks an editor role on it — without
        distinguishing the two, so the endpoint never reveals which mailboxes
        exist. (This is the authorization that previously lived in the
        single-use, request-shape-coupled ``IsAllowedToCreateMessage``
        permission; create_draft still enforces parent-thread access for
        replies, and the PUT draft lookup scopes to an editable draft thread.)
        """
        try:
            mailbox = models.Mailbox.objects.filter(
                id=sender_id,
                accesses__user=user,
                accesses__role__in=enums.MAILBOX_ROLES_CAN_EDIT,
            ).first()
        except (DjangoValidationError, ValueError, TypeError):
            mailbox = None  # malformed senderId — treat as no access
        if mailbox is None:
            raise drf.exceptions.PermissionDenied(
                "You do not have permission to send as this mailbox."
            )
        return mailbox

    @transaction.atomic
    def post(self, request):
        """Create a new draft message."""
        # Validate required fields
        sender_id = request.data.get("senderId")
        if not sender_id:
            raise drf.exceptions.ValidationError("senderId is required")

        subject = request.data.get("subject")

        # Resolve + authorize the sender mailbox (user must hold an editor-or-
        # above role on it). create_draft separately enforces access to the
        # parent thread for replies.
        sender_mailbox = self._resolve_editable_sender_mailbox(request.user, sender_id)

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
            user=request.user,
        )

        # Re-query with read-state annotation for accurate is_unread
        message = models.Message.objects.with_read_state(sender_mailbox.id).get(
            id=message.id
        )
        return Response(
            serializers.MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
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

        # Resolve + authorize the sender mailbox (editor-or-above role). The
        # draft lookup below additionally scopes to a draft whose thread this
        # mailbox can edit (404 otherwise).
        sender_mailbox = self._resolve_editable_sender_mailbox(request.user, sender_id)

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
        updated_message = update_draft(
            sender_mailbox, message, request.data, user=request.user
        )

        # Update thread stats
        updated_message.thread.update_stats()

        # Re-query with read-state annotation for accurate is_unread
        updated_message = models.Message.objects.with_read_state(sender_mailbox.id).get(
            id=updated_message.id
        )
        serializer = serializers.MessageSerializer(
            updated_message, context={"request": request}
        )
        return Response(serializer.data)
