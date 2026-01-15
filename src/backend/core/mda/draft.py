"""Draft message creation and management functionality."""

import logging
import uuid
from typing import Optional

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import rest_framework as drf

from core import enums, models
from core.api.utils import get_attachment_from_blob_id

logger = logging.getLogger(__name__)


def validate_body_size(body_bytes: bytes) -> None:
    """Validate the size of the body."""
    if len(body_bytes) > settings.MAX_OUTGOING_BODY_SIZE:
        # Use binary (MiB) to match frontend formatting
        body_mb = len(body_bytes) / (1024 * 1024)
        max_body_mb = settings.MAX_OUTGOING_BODY_SIZE / (1024 * 1024)

        raise drf.exceptions.ValidationError(
            {
                "draftBody": _(
                    "Message body size (%(body_size)s MB) exceeds the %(max_size)s MB limit. "
                    "Please reduce message content."
                )
                % {
                    "body_size": f"{body_mb:.1f}",
                    "max_size": f"{max_body_mb:.0f}",
                }
            }
        )


def validate_attachment_size(current_total_size: int, new_total_size: int) -> None:
    """Validate the size of the attachments."""

    total_attachment_size = current_total_size + new_total_size

    if total_attachment_size > settings.MAX_OUTGOING_ATTACHMENT_SIZE:
        # Use binary (MiB) to match frontend formatting
        total_mb = total_attachment_size / (1024 * 1024)
        max_mb = settings.MAX_OUTGOING_ATTACHMENT_SIZE / (1024 * 1024)
        current_mb = current_total_size / (1024 * 1024)
        new_mb = new_total_size / (1024 * 1024)

        raise drf.exceptions.ValidationError(
            {
                "attachments": _(
                    "Cannot add attachment(s) (%(new_size)s MB). "
                    "Total attachments would be %(total_size)s MB, exceeding the %(max_size)s MB limit. "
                    "Current attachments: %(current_size)s MB."
                )
                % {
                    "new_size": f"{new_mb:.1f}",
                    "total_size": f"{total_mb:.1f}",
                    "max_size": f"{max_mb:.0f}",
                    "current_size": f"{current_mb:.1f}",
                }
            }
        )


def _get_or_create_attachment_from_message_blob(
    mailbox: models.Mailbox,
    attachment_data: dict,
    user: models.User,
) -> Optional[models.Attachment]:
    """
    Get or create an attachment from message raw data (msg_* format blobId).

    Args:
        mailbox: The mailbox to associate the attachment with
        attachment_data: Dictionary containing blobId, name, and optional cid
        user: The user making the request

    Returns:
        The created Attachment or None if processing failed
    """
    blob_id = attachment_data.get("blobId")
    name = attachment_data.get("name", "unnamed")
    cid = attachment_data.get("cid")

    try:
        # Extract attachment from original message MIME
        parsed_attachment = get_attachment_from_blob_id(blob_id, user)

        # Create a real Blob from the extracted content
        blob = mailbox.create_blob(
            content=parsed_attachment["content"],
            content_type=parsed_attachment["type"],
        )

        # Use cid from parsed attachment if not provided
        if not cid:
            cid = parsed_attachment.get("cid")

        # Use name from parsed attachment if not provided
        if name == "unnamed":
            name = parsed_attachment.get("name", "unnamed")

        attachment, created = models.Attachment.objects.get_or_create(
            blob=blob, mailbox=mailbox, defaults={"name": name, "cid": cid}
        )

        if created:
            logger.debug(
                "Created new attachment %s for forwarded blob %s",
                attachment.id,
                blob_id,
            )

        return attachment

    except (ValueError, models.Blob.DoesNotExist) as e:
        logger.warning("Failed to extract forwarded attachment %s: %s", blob_id, e)
        return None


def _get_or_create_attachment_from_blob(
    mailbox: models.Mailbox,
    attachment_data: dict,
) -> Optional[models.Attachment]:
    """
    Get or create an attachment from a blobId.

    Args:
        mailbox: The mailbox to associate the attachment with
        attachment_data: Dictionary containing blobId, name, and optional cid

    Returns:
        The created/existing Attachment or None if processing failed
    """
    blob_id = attachment_data.get("blobId")
    name = attachment_data.get("name", "unnamed")
    cid = attachment_data.get("cid")

    try:
        # Convert blob_id to UUID if it's a string
        if isinstance(blob_id, str):
            blob_id = uuid.UUID(blob_id)

        # Try to get the blob
        blob = models.Blob.objects.get(id=blob_id)
        if blob.mailbox != mailbox:
            logger.warning(
                "Blob %s is not associated with mailbox %s",
                blob_id,
                mailbox.id,
            )
            return None

        attachment, created = models.Attachment.objects.get_or_create(
            blob=blob, mailbox=mailbox, defaults={"name": name, "cid": cid}
        )

        if created:
            logger.debug(
                "Created new attachment %s for blob %s",
                attachment.id,
                blob_id,
            )

        return attachment

    except (ValueError, models.Blob.DoesNotExist) as e:
        logger.warning("Invalid or missing blob %s: %s", blob_id, str(e))
        return None


def _update_message_attachments(
    message: models.Message,
    mailbox: models.Mailbox,
    attachments_data: list,
    user: Optional[models.User] = None,
) -> None:
    """
    Update message attachments based on provided attachment data.

    Args:
        message: The message to update attachments for
        mailbox: The mailbox making the update
        attachments_data: List of attachment data dictionaries
        user: The user making the update (needed for forwarded attachments)
    """
    if not message.pk:
        return

    # Get the current attachment IDs
    current_attachment_ids = set(message.attachments.values_list("id", flat=True))

    # Process the new attachments
    new_attachment_ids = []

    for attachment_data in attachments_data:
        if not attachment_data:  # Skip empty values
            continue

        blob_id = attachment_data.get("blobId")
        if not blob_id:
            logger.warning("Missing blobId in attachment data: %s", attachment_data)
            continue

        # Handle msg_* format blobId (from forwarded message)
        if isinstance(blob_id, str) and blob_id.startswith("msg_"):
            if not user:
                logger.warning(
                    "Cannot process forwarded attachment %s without user", blob_id
                )
                continue
            attachment = _get_or_create_attachment_from_message_blob(
                mailbox, attachment_data, user
            )
        else:
            attachment = _get_or_create_attachment_from_blob(mailbox, attachment_data)

        if attachment:
            new_attachment_ids.append(attachment.id)

    # Combine all valid attachment IDs
    new_attachments = set(new_attachment_ids)

    # Add new attachments and remove old ones
    to_add = new_attachments - current_attachment_ids
    to_remove = current_attachment_ids - new_attachments

    # Validate total attachment size before adding
    if to_add:
        # Calculate current total (excluding attachments about to be removed)
        current_attachments = message.attachments.exclude(id__in=to_remove)
        current_total_size = sum(
            att.blob.size for att in current_attachments.select_related("blob")
        )

        # Calculate size of new attachments being added
        new_attachments_objs = models.Attachment.objects.filter(
            id__in=to_add
        ).select_related("blob")
        new_total_size = sum(att.blob.size for att in new_attachments_objs)

        # Check if adding these would exceed the attachment limit
        validate_attachment_size(current_total_size, new_total_size)

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


def create_draft(
    mailbox: models.Mailbox,
    subject: str = "",
    draft_body: str = "",
    parent_id: Optional[str] = None,
    to_emails: Optional[list] = None,
    cc_emails: Optional[list] = None,
    bcc_emails: Optional[list] = None,
    attachments: Optional[list] = None,
    signature_id: Optional[str] = None,
    user: Optional[models.User] = None,
) -> models.Message:
    """
    Create a new draft message.

    Args:
        mailbox: The mailbox that will be the sender
        subject: Subject of the draft message
        draft_body: Content of the draft (usually JSON)
        parent_id: Optional message ID to reply to
        to_emails: List of TO recipient emails
        cc_emails: List of CC recipient emails
        bcc_emails: List of BCC recipient emails
        attachments: List of attachment objects with blobId, partId, and name
        signature_id: Optional signature template ID
        user: The user creating the draft (needed for forwarded attachments)

    Returns:
        The created draft message

    Raises:
        drf.exceptions.NotFound: If parent message not found
        drf.exceptions.PermissionDenied: If access denied to parent thread
    """

    # Get or create sender contact
    mailbox_email = f"{mailbox.local_part}@{mailbox.domain.name}"
    sender_contact, _created = models.Contact.objects.get_or_create(
        email=mailbox_email,
        mailbox=mailbox,
        defaults={
            "email": mailbox_email,
            "name": mailbox.local_part,
        },
    )

    # Handle parent message if this is a reply
    reply_to_message = None
    if parent_id:
        try:
            reply_to_message = models.Message.objects.select_related("thread").get(
                id=parent_id
            )
            # Ensure mailbox has access to parent thread
            if not models.ThreadAccess.objects.filter(
                thread=reply_to_message.thread,
                mailbox=mailbox,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            ).exists():
                raise drf.exceptions.PermissionDenied(
                    "Access denied to the thread you are replying to."
                )
            thread = reply_to_message.thread
        except models.Message.DoesNotExist as exc:
            raise drf.exceptions.NotFound("Parent message not found.") from exc
    else:
        # Create a new thread for the new draft
        thread = models.Thread.objects.create(subject=subject)
        # Grant access to the creator
        models.ThreadAccess.objects.create(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
    # Validate and get signature if provided
    signature = mailbox.get_validated_signature(signature_id)

    # Validate and prepare draft body
    draft_blob = None
    if draft_body:
        draft_body_bytes = draft_body.encode("utf-8")

        validate_body_size(draft_body_bytes)

        draft_blob = mailbox.create_blob(
            content=draft_body_bytes,
            content_type="application/json",
        )

    # Create message instance
    message = models.Message(
        thread=thread,
        sender=sender_contact,
        parent=reply_to_message,
        subject=subject,
        read_at=timezone.now(),
        is_draft=True,
        is_sender=True,
        draft_blob=draft_blob,
        signature=signature,
    )
    message.save()

    # Update draft details with recipients and attachments
    update_data = {
        "to": to_emails or [],
        "cc": cc_emails or [],
        "bcc": bcc_emails or [],
        "attachments": attachments or [],
    }

    message = update_draft(mailbox, message, update_data, user=user)

    # Update thread stats
    thread.update_stats()

    return message


def update_draft(
    mailbox: models.Mailbox,
    message: models.Message,
    update_data: dict,
    user: Optional[models.User] = None,
) -> models.Message:
    """
    Update draft details (subject, recipients, body, attachments).

    Args:
        mailbox: The mailbox making the update
        message: The draft message to update
        update_data: Dictionary containing fields to update
        user: The user making the update (needed for forwarded attachments)

    Returns:
        The updated message

    Raises:
        drf.exceptions.PermissionDenied: If access denied to thread
    """

    updated_fields = []
    thread_updated_fields = []

    # Check access to the thread
    if (
        message.thread
        and not models.ThreadAccess.objects.filter(
            thread=message.thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        ).exists()
    ):
        raise drf.exceptions.PermissionDenied("Access denied to this message's thread.")

    # Update signature if provided
    signature_id = update_data.get("signatureId")
    signature = mailbox.get_validated_signature(signature_id)
    if signature and message.signature != signature:
        message.signature = signature
        message.save(update_fields=["signature", "updated_at"])
    elif not signature_id and "signatureId" in update_data and signature is None:
        # explicitly clearing the signature
        message.signature = None
        message.save(update_fields=["signature", "updated_at"])

    # Update subject if provided
    if "subject" in update_data and update_data["subject"] != message.subject:
        message.subject = update_data["subject"]
        updated_fields.append("subject")
        # Also update thread subject if this is the first message
        if message.pk and message.thread.messages.count() == 1:
            message.thread.subject = update_data["subject"]
            thread_updated_fields.append("subject")

    # Update recipients if provided
    recipient_type_mapping = {
        "to": enums.MessageRecipientTypeChoices.TO,
        "cc": enums.MessageRecipientTypeChoices.CC,
        "bcc": enums.MessageRecipientTypeChoices.BCC,
    }
    recipient_types = ["to", "cc", "bcc"]
    for recipient_type in recipient_types:
        if recipient_type in update_data:
            # Delete existing recipients of this type
            if message.pk:
                message.recipients.filter(
                    type=recipient_type_mapping[recipient_type]
                ).delete()

            # Create new recipients
            emails = update_data.get(recipient_type) or []
            for email in emails:
                contact, _created = models.Contact.objects.get_or_create(
                    email=email,
                    mailbox=mailbox,
                    defaults={
                        "email": email,
                        "name": email.split("@")[0],
                    },
                )
                # Only create MessageRecipient if message has been saved
                if message.pk:
                    models.MessageRecipient.objects.get_or_create(
                        message=message,
                        contact=contact,
                        type=recipient_type_mapping[recipient_type],
                    )

    # Update draft body if provided
    if "draftBody" in update_data:
        try:
            if message.draft_blob:
                message.draft_blob.delete()
            message.draft_blob = None
        except models.Blob.DoesNotExist:
            pass
        if update_data["draftBody"]:
            draft_body_bytes = update_data["draftBody"].encode("utf-8")

            validate_body_size(draft_body_bytes)

            message.draft_blob = mailbox.create_blob(
                content=draft_body_bytes,
                content_type="application/json",
            )
        updated_fields.append("draft_blob")

    # Update attachments if provided
    if "attachments" in update_data:
        _update_message_attachments(
            message=message,
            mailbox=mailbox,
            attachments_data=update_data.get("attachments", []),
            user=user,
        )

    has_attachments = message.attachments.exists()
    if has_attachments != message.has_attachments:
        message.has_attachments = has_attachments
        updated_fields.append("has_attachments")

    # Save message and thread if changes were made
    if len(updated_fields) > 0 and message.pk:  # Only save if message exists
        logger.debug("Saving message %s with fields %s", message.id, updated_fields)
        message.save(update_fields=updated_fields + ["updated_at"])
    if len(thread_updated_fields) > 0 and message.thread.pk:  # Check thread exists
        message.thread.save(update_fields=thread_updated_fields + ["updated_at"])

    return message
