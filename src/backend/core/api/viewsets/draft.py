"""API ViewSet for creating and updating draft messages."""

import json
import logging
import uuid

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

from core import enums, models

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
            "attachments": drf_serializers.ListField(
                child=drf_serializers.DictField(),
                required=False,
                default=list,
                help_text="List of attachment objects with blobId, partId, and name",
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
        - subject: str
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        - attachmentIds: list[str] (optional, IDs of previously uploaded blobs)
        Return newly created draft message

    PUT /api/v1.0/draft/{message_id}/ with expected data:
        - subject: str (optional)
        - draftBody: str (optional)
        - to: list[str] (optional)
        - cc: list[str] (optional)
        - bcc: list[str] (optional)
        - attachmentIds: list[str] (optional, IDs of previously uploaded blobs)
        Return updated draft message
    """

    permission_classes = [permissions.IsAllowedToCreateMessage]
    mailbox = None

    def _update_draft_details(
        self, message: models.Message, request_data: dict
    ) -> models.Message:
        """Helper method to update draft details (subject, recipients, body, attachments).
        Ensures user has access to the thread."""

        updated_fields = []
        thread_updated_fields = ["updated_at"]  # Always update thread timestamp

        # --- Check Access (redundant if permission class covers it, but safe) ---
        # Ensure user has access to the thread this message belongs to
        if (
            message.thread
            and not models.ThreadAccess.objects.filter(
                thread=message.thread,
                mailbox=self.mailbox,
                role=models.ThreadAccessRoleChoices.EDITOR,
            ).exists()
        ):
            raise drf.exceptions.PermissionDenied(
                "Access denied to this message's thread."
            )

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

        # Update attachments if provided
        if "attachments" in request_data:
            # Only process attachments if message has been saved
            if message.pk:
                # Get the current attachment IDs
                current_attachment_ids = set(
                    message.attachments.values_list("id", flat=True)
                )

                # Process the new attachments from request
                new_attachment_ids = []

                for attachment_data in request_data.get("attachments", []):
                    if not attachment_data:  # Skip empty values
                        continue

                    # Get the blob ID
                    blob_id = attachment_data.get("blobId")
                    # TODO
                    # part_id = attachment_data.get("partId", f"att-{uuid.uuid4()}")
                    name = attachment_data.get("name", "unnamed")

                    if not blob_id:
                        logger.warning(
                            "Missing blobId in attachment data: %s",
                            attachment_data,
                        )
                        continue

                    try:
                        # Convert blob_id to UUID if it's a string
                        if isinstance(blob_id, str):
                            blob_id = uuid.UUID(blob_id)

                        # Try to get the blob
                        blob = models.Blob.objects.get(id=blob_id)

                        # Create an attachment for this blob if it doesn't exist
                        attachment, created = models.Attachment.objects.get_or_create(
                            blob=blob, mailbox=self.mailbox, defaults={"name": name}
                        )

                        if created:
                            logger.info(
                                "Created new attachment %s for blob %s",
                                attachment.id,
                                blob_id,
                            )

                        new_attachment_ids.append(attachment.id)

                    except (ValueError, models.Blob.DoesNotExist) as e:
                        logger.warning(
                            "Invalid or missing blob %s: %s", blob_id, str(e)
                        )

                # Combine all valid attachment IDs
                new_attachments = set(new_attachment_ids)

                # Add new attachments and remove old ones
                to_add = new_attachments - current_attachment_ids
                to_remove = current_attachment_ids - new_attachments

                # Remove attachments no longer in the list
                if to_remove:
                    message.attachments.remove(*to_remove)

                # Add new attachments
                if to_add:
                    valid_attachments = models.Attachment.objects.filter(id__in=to_add)
                    message.attachments.add(*valid_attachments)

                    # Log if some attachments weren't found
                    if len(valid_attachments) != len(to_add):
                        logger.warning(
                            "Some attachments were not found: %s",
                            set(to_add) - {a.id for a in valid_attachments},
                        )

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

        sender_mailbox = self.mailbox

        # Then get the parent message if it's a reply
        parent_id = request.data.get("parentId")
        reply_to_message = None
        if parent_id:
            try:
                # Reply to an existing message in a thread
                reply_to_message = models.Message.objects.select_related("thread").get(
                    id=parent_id
                )
                # Ensure user has access to parent thread (already checked by permission, but safe)
                if not models.ThreadAccess.objects.filter(
                    thread=reply_to_message.thread,
                    mailbox=sender_mailbox,
                    role=models.ThreadAccessRoleChoices.EDITOR,
                ).exists():
                    raise drf.exceptions.PermissionDenied(
                        "Access denied to the thread you are replying to."
                    )
                thread = reply_to_message.thread

            except models.Message.DoesNotExist as exc:
                raise drf.exceptions.NotFound("Parent message not found.") from exc
        else:
            # Create a new thread for the new draft
            thread = models.Thread.objects.create(
                subject=subject,
            )
            # Grant access to the creator via the sending mailbox context
            # permission to create a draft message if already check with permission class
            models.ThreadAccess.objects.create(
                thread=thread,
                mailbox=sender_mailbox,
                role=enums.ThreadAccessRoleChoices.EDITOR,
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
            read_at=timezone.now(),
            is_draft=True,
            is_sender=True,
            draft_body=request.data.get("draftBody", ""),
        )
        message.save()  # Save message before adding recipients

        # Populate details using helper
        message = self._update_draft_details(message, request.data)

        thread.update_stats()

        # Refresh required as _update_draft_details might have saved again
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
        # Get sender mailbox (needed for contact creation in helper)
        # TODO: Should senderId be required in PUT? Or derive from message?
        # Assuming it's required for now, matching POST.
        sender_id = request.data.get("senderId")
        if not sender_id:
            raise drf.exceptions.ValidationError(
                "senderId is required in request body for update."
            )
        try:
            # Make sure senderId corresponds to an actual mailbox
            self.mailbox = models.Mailbox.objects.get(id=sender_id)
        except models.Mailbox.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                f"Mailbox with senderId {sender_id} not found."
            ) from exc

        # Permission class checks senderId validity, send permission, and thread access.
        sender_mailbox = self.mailbox  # Set by permission class

        try:
            # Fetch the draft message, ensuring it belongs to the user indirectly via ThreadAccess
            # and matches the sender mailbox context if that's a requirement for *updating*.
            message = models.Message.objects.select_related("thread").get(
                id=message_id,
                is_draft=True,
                # Ensure the user has access to this thread
                thread__accesses__mailbox=sender_mailbox,
                thread__accesses__role=models.ThreadAccessRoleChoices.EDITOR,
            )
        except models.Message.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                "Draft message not found, is not a draft, or access denied."
            ) from exc

        # Populate details using helper, passing user for potential checks
        updated_message = self._update_draft_details(message, request.data)

        # Update thread stats
        updated_message.thread.update_stats()

        # Refresh needed as helper might save thread
        updated_message.refresh_from_db()
        serializer = serializers.MessageSerializer(
            updated_message, context={"request": request}
        )
        return Response(serializer.data)
