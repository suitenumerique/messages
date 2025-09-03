"""Draft message creation and management functionality."""

import logging
import uuid
from typing import Optional

from django.utils import timezone

import rest_framework as drf

from core import enums, models

logger = logging.getLogger(__name__)


def create_draft(
    mailbox: models.Mailbox,
    subject: str = "",
    draft_body: str = "",
    parent_id: Optional[str] = None,
    to_emails: Optional[list] = None,
    cc_emails: Optional[list] = None,
    bcc_emails: Optional[list] = None,
    attachments: Optional[list] = None,
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

    Returns:
        The created draft message

    Raises:
        drf.exceptions.NotFound: If parent message not found
        drf.exceptions.PermissionDenied: If access denied to parent thread
    """

    # Get or create sender contact
    mailbox_email = f"{mailbox.local_part}@{mailbox.domain.name}"
    sender_contact, _ = models.Contact.objects.get_or_create(
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
            # Ensure user has access to parent thread
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

    # Create message instance
    message = models.Message(
        thread=thread,
        sender=sender_contact,
        parent=reply_to_message,
        subject=subject,
        read_at=timezone.now(),
        is_draft=True,
        is_sender=True,
        draft_blob=mailbox.create_blob(
            content=draft_body.encode("utf-8"),
            content_type="application/json",
        )
        if draft_body
        else None,
    )
    message.save()

    # Update draft details with recipients and attachments
    update_data = {
        "to": to_emails or [],
        "cc": cc_emails or [],
        "bcc": bcc_emails or [],
        "attachments": attachments or [],
    }

    message = update_draft(mailbox, message, update_data)

    # Update thread stats
    thread.update_stats()

    return message


def update_draft(
    mailbox: models.Mailbox, message: models.Message, update_data: dict
) -> models.Message:
    """
    Update draft details (subject, recipients, body, attachments).

    Args:
        mailbox: The mailbox making the update
        message: The draft message to update
        update_data: Dictionary containing fields to update

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
                contact, _ = models.Contact.objects.get_or_create(
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
            message.draft_blob = mailbox.create_blob(
                content=update_data["draftBody"].encode("utf-8"),
                content_type="application/json",
            )
        updated_fields.append("draft_blob")

    # Update attachments if provided
    if "attachments" in update_data:
        # Only process attachments if message has been saved
        if message.pk:
            # Get the current attachment IDs
            current_attachment_ids = set(
                message.attachments.values_list("id", flat=True)
            )

            # Process the new attachments from update_data
            new_attachment_ids = []

            for attachment_data in update_data.get("attachments", []):
                if not attachment_data:  # Skip empty values
                    continue

                # Get the blob ID
                blob_id = attachment_data.get("blobId")
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
                    if blob.mailbox != mailbox:
                        logger.warning(
                            "Blob %s is not associated with mailbox %s",
                            blob_id,
                            mailbox.id,
                        )
                        continue

                    # Create an attachment for this blob if it doesn't exist
                    attachment, created = models.Attachment.objects.get_or_create(
                        blob=blob, mailbox=mailbox, defaults={"name": name}
                    )

                    if created:
                        logger.debug(
                            "Created new attachment %s for blob %s",
                            attachment.id,
                            blob_id,
                        )

                    new_attachment_ids.append(attachment.id)

                except (ValueError, models.Blob.DoesNotExist) as e:
                    logger.warning("Invalid or missing blob %s: %s", blob_id, str(e))

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
