import json
import logging

from django.db import transaction
from django.utils import timezone

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

from core import models

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
                required=True, help_text="Subject of the message"
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
            "Update Draft",
            value={
                "messageId": "123e4567-e89b-12d3-a456-426614174000",
                "subject": "Updated subject",
                "draftBody": json.dumps({"arbitrary": "new json content"}),
                "to": ["new-recipient@example.com"],
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
        - subject: str
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        Return newly created draft message

    PUT /api/v1.0/draft/{message_id}/ with expected data:
        - subject: str (optional)
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        Return updated draft message
    """

    permission_classes = [permissions.IsAllowedToCreateMessage]
    mailbox = None

    def _update_draft_details(
        self, message: models.Message, request_data: dict
    ) -> models.Message:
        """Helper method to update draft details (subject, recipients, body)."""
        updated_fields = []
        thread_updated_fields = []

        # Update subject if provided
        if "subject" in request_data:
            message.subject = request_data["subject"]
            updated_fields.append("subject")
            # Also update thread subject if this is the first message
            # Check if the message has already been saved (i.e., has an ID)
            if message.pk and message.thread.messages.count() == 1:
                message.thread.subject = request_data["subject"]
                thread_updated_fields.extend(["subject", "updated_at"])

        # Update recipients if provided
        recipient_types = ["to", "cc", "bcc"]
        for recipient_type in recipient_types:
            if recipient_type in request_data:
                # Delete existing recipients of this type
                # Ensure message has a pk before accessing m2m
                if message.pk:
                    message.recipients.filter(type=recipient_type).delete()

                # Create new recipients
                emails = request_data.get(recipient_type) or []
                for email in emails:
                    contact, _ = models.Contact.objects.get_or_create(
                        email__iexact=email,
                        mailbox=self.mailbox,
                        defaults={  # Provide defaults for creation
                            "email": email,
                            "name": email.split("@")[0],  # Basic default name
                        },
                    )
                    # Only create MessageRecipient if message has been saved
                    if message.pk:
                        models.MessageRecipient.objects.create(
                            message=message,
                            contact=contact,
                            type=recipient_type,
                        )
                    # If message not saved yet (POST case), recipients will be added after save

        # Update draft body if provided
        if "draftBody" in request_data:
            message.draft_body = request_data.get("draftBody", "")
            updated_fields.append("draft_body")

        # Save message and thread if changes were made
        if updated_fields and message.pk:  # Only save if message exists
            message.save(update_fields=updated_fields + ["updated_at"])
        if thread_updated_fields and message.thread.pk:  # Check thread exists
            message.thread.save(
                update_fields=list(set(thread_updated_fields))
            )  # Use set to avoid duplicate updated_at

        return message

    @transaction.atomic
    def post(self, request):
        """Create a new draft message."""
        sender_id = request.data.get("senderId")
        self.mailbox = models.Mailbox.objects.get(id=sender_id)
        subject = request.data.get("subject")

        # Then get the parent message if it's a reply
        parent_id = request.data.get("parentId")
        reply_to_message = None
        if parent_id:
            try:
                # Reply to an existing message in a thread
                reply_to_message = models.Message.objects.get(id=parent_id)
                thread = reply_to_message.thread
                # Permission check: ensure parent thread belongs to the mailbox
                if thread.mailbox != self.mailbox:
                    raise drf.exceptions.PermissionDenied(
                        "Cannot reply to a message in a different mailbox."
                    )
            except models.Message.DoesNotExist as exc:
                raise drf.exceptions.ValidationError(
                    "Parent message does not exist."
                ) from exc
        else:
            # Create a new thread
            thread = models.Thread.objects.create(
                # self.mailbox is set in the permission class
                mailbox=self.mailbox,
                subject=subject,
            )

        # --- Get Sender Contact --- #
        # Find the contact associated with the sending mailbox
        # Construct the email address from mailbox parts
        mailbox_email = f"{self.mailbox.local_part}@{self.mailbox.domain.name}"
        sender_contact, _ = models.Contact.objects.get_or_create(
            email__iexact=mailbox_email,
            mailbox=self.mailbox,  # Ensure contact is linked to this mailbox
            defaults={  # Provide defaults for creation
                "email": mailbox_email,
                "name": self.mailbox.local_part,  # Basic default name
            },
        )

        # Create message instance with all data
        message = models.Message(
            thread=thread,
            sender=sender_contact,
            parent=reply_to_message,
            subject=subject,
            created_at=timezone.now(),
            read_at=timezone.now(),  # Drafts are typically marked read for the sender
            mta_sent=False,
            is_draft=True,  # Mark as draft
            draft_body=request.data.get("draftBody", ""),  # Get content from draftBody
        )
        message.save()  # Save message before adding recipients

        # Populate details using helper
        message = self._update_draft_details(message, request.data)

        thread.update_counters()

        # Refresh required as _update_draft_details might have saved again
        message.refresh_from_db()
        return Response(
            serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED
        )

    @transaction.atomic
    def put(self, request, message_id=None):
        """Update an existing draft message."""
        if not message_id:
            raise drf.exceptions.BadRequest(
                "Message ID is required for updating a draft."
            )

        # Get sender mailbox (needed for contact creation in helper)
        # TODO: Should senderId be required in PUT? Or derive from message?
        # Assuming it's required for now, matching POST.
        sender_id = request.data.get("senderId")
        if not sender_id:
            raise drf.exceptions.BadRequest(
                "senderId is required in request body for update."
            )
        try:
            # Make sure senderId corresponds to an actual mailbox
            self.mailbox = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                f"Mailbox with senderId {sender_id} not found."
            ) from exc

        # --- Get Existing Draft --- #
        try:
            message = models.Message.objects.select_related(
                "thread__mailbox"
            ).get(
                id=message_id,
                is_draft=True,
                thread__mailbox=self.mailbox,  # Ensure message belongs to the claimed sender mailbox
            )
        except models.Message.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                "Draft message not found or does not belong to the specified sender mailbox."
            ) from exc

        # --- Check Permissions --- #
        # Permission class IsAllowedToCreateMessage likely checks based on self.mailbox set above.
        # If more granular checks needed (e.g., based on message.thread.mailbox explicitly),
        # they could be added here or in the permission class.

        # Populate details using helper
        updated_message = self._update_draft_details(message, request.data)

        # Refresh needed as helper might save
        updated_message.refresh_from_db()
        return Response(serializers.MessageSerializer(updated_message).data)
