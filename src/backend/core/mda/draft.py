"""Draft message creation and management functionality."""

import logging
import uuid
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import rest_framework as drf

from core import enums, models
from core.api.utils import get_attachment_from_blob_id
from core.services.blob_gc import release_upload, schedule_for_gc

logger = logging.getLogger(__name__)


def validate_body_size(body_bytes: bytes) -> None:
    """Validate the size of the body."""
    if len(body_bytes) > settings.MAX_OUTGOING_BODY_SIZE:
        # Use binary (MiB) to match frontend formatting
        body_mb = len(body_bytes) / (1024 * 1024)
        max_body_mb = settings.MAX_OUTGOING_BODY_SIZE / (1024 * 1024)

        raise drf.exceptions.ValidationError(
            {
                "draftBody": (
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
                "attachments": (
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
    message: models.Message,
) -> Optional[models.Attachment]:
    """
    Get or create an attachment from message raw data (msg_* format blobId).

    The Attachment is owned by ``message`` (per-message FK).

    Args:
        mailbox: The mailbox to associate the attachment with
        attachment_data: Dictionary containing blobId, name, and optional cid
        user: The user making the request
        message: The draft Message that will own the Attachment

    Returns:
        The created Attachment or None if processing failed
    """
    blob_id = attachment_data.get("blobId")
    name = attachment_data.get("name", "unnamed")
    cid = attachment_data.get("cid")

    try:
        # Extract attachment from original message MIME
        parsed_attachment = get_attachment_from_blob_id(blob_id, user)

        # Use cid from parsed attachment if not provided
        if not cid:
            cid = parsed_attachment.get("cid")

        # Use name from parsed attachment if not provided
        if name == "unnamed":
            name = parsed_attachment.get("name", "unnamed")

        # Atomic: the Blob INSERT and the Attachment INSERT must be
        # visible together so the GC sweep never sees the blob row
        # without its referencing FK row. ``BlobManager.create_blob``
        # opens its own atomic for the blob INSERT; nesting inside
        # this outer atomic turns it into a savepoint that only
        # commits with the outer block.
        with transaction.atomic():
            blob = models.Blob.objects.create_blob(
                content=parsed_attachment["content"],
                content_type=parsed_attachment["type"],
            )

            attachment, created = models.Attachment.objects.get_or_create(
                blob=blob,
                mailbox=mailbox,
                message=message,
                defaults={"name": name, "cid": cid},
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
    message: models.Message,
) -> Optional[models.Attachment]:
    """
    Get or create an attachment from a blobId.

    The Attachment is owned by ``message`` (per-message FK). If the
    same blob is attached to two different drafts, each draft gets
    its own ``Attachment`` row — sending or deleting one doesn't
    affect the other.

    Args:
        mailbox: The mailbox to associate the attachment with
        attachment_data: Dictionary containing blobId, name, and optional cid
        message: The draft Message that will own the Attachment

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

        # Provenance check: the user attaching this blob must have
        # either an active upload reservation tied to this mailbox
        # (the JMAP upload-then-attach window) or an existing
        # Attachment in this mailbox already referencing the blob
        # (re-attaching a known file — the row may be on a different
        # draft, but proves the mailbox already has authz to this
        # blob).
        #
        # Deliberately NOT accepted as provenance: ``Message.blob`` /
        # ``Message.draft_blob`` matches via shared thread access. A
        # user with read access to a shared thread shouldn't be able
        # to attach the raw RFC822 of any message in that thread
        # (or another user's draft body) to their own outbound draft
        # by quoting its blob_id directly — that's an exfil channel,
        # not a legitimate user flow. Users with shared-thread
        # access who want to forward a message attachment must go
        # through the ``msg_*`` blob-id path
        # (``_get_or_create_attachment_from_message_blob``), which
        # re-parses the MIME and creates a fresh Blob owned by the
        # forwarding mailbox.
        has_reservation = models.MailboxBlob.objects.filter(
            blob=blob, mailbox=mailbox, expires_at__gt=timezone.now()
        ).exists()
        has_existing_link = models.Attachment.objects.filter(
            blob=blob, mailbox=mailbox
        ).exists()
        if not (has_reservation or has_existing_link):
            logger.warning(
                "Blob %s has no provenance for mailbox %s (no reservation, no existing link)",
                blob_id,
                mailbox.id,
            )
            return None

        attachment, created = models.Attachment.objects.get_or_create(
            blob=blob,
            mailbox=mailbox,
            message=message,
            defaults={"name": name, "cid": cid},
        )

        if created:
            logger.debug(
                "Created new attachment %s for blob %s",
                attachment.id,
                blob_id,
            )
            # Once the Attachment exists, the reference graph covers
            # authz; drop the upload reservation row.
            release_upload(blob, mailbox)

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

    Per-message FK semantics: each ``Attachment`` row belongs to
    exactly one ``Message`` via ``Attachment.message``. The
    ``_get_or_create_attachment_from_blob`` helpers create rows
    already linked to ``message``; this function only needs to
    delete the ones that fell out of the new list.

    Args:
        message: The message to update attachments for
        mailbox: The mailbox making the update
        attachments_data: List of attachment data dictionaries
        user: The user making the update (needed for forwarded attachments)
    """
    if not message.pk:
        return

    current_attachment_ids = set(message.attachments.values_list("id", flat=True))

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
                mailbox, attachment_data, user, message
            )
        else:
            attachment = _get_or_create_attachment_from_blob(
                mailbox, attachment_data, message
            )

        if attachment:
            new_attachment_ids.append(attachment.id)

    new_attachments = set(new_attachment_ids)
    to_remove = current_attachment_ids - new_attachments
    just_added = new_attachments - current_attachment_ids

    # Validate the post-change total size. ``to_remove`` rows are
    # dropped; everything else (existing-and-kept + just-created)
    # contributes to the limit.
    if just_added:
        kept = message.attachments.exclude(id__in=to_remove).exclude(id__in=just_added)
        kept_size = sum(att.blob.size for att in kept.select_related("blob"))
        added_size = sum(
            att.blob.size
            for att in models.Attachment.objects.filter(
                id__in=just_added
            ).select_related("blob")
        )
        validate_attachment_size(kept_size, added_size)

    # Remove attachments no longer in the list. Filtering by
    # ``message=message`` is belt-and-braces — the FK guarantees
    # rows in ``current_attachment_ids`` belong to this message —
    # but it makes the intent obvious and limits damage if a caller
    # ever passes a stale id set. The Attachment post_delete
    # signal schedules each blob_id for GC.
    if to_remove:
        deleted_attachments, _ = models.Attachment.objects.filter(
            id__in=to_remove, message=message
        ).delete()
        if deleted_attachments:
            logger.debug("Deleted %d orphan attachment(s)", deleted_attachments)


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

    # The Blob INSERT and the Message INSERT must commit together
    # so the GC sweep never sees the Blob row without its
    # referencing FK on ``Message.draft_blob`` — atomic wraps both.
    draft_body_bytes = None
    if draft_body:
        draft_body_bytes = draft_body.encode("utf-8")
        validate_body_size(draft_body_bytes)

    with transaction.atomic():
        draft_blob = None
        if draft_body_bytes is not None:
            draft_blob = models.Blob.objects.create_blob(
                content=draft_body_bytes,
                content_type="application/json",
            )

        # Create message instance
        message = models.Message(
            thread=thread,
            sender=sender_contact,
            parent=reply_to_message,
            subject=subject,
            is_draft=True,
            is_sender=True,
            draft_blob=draft_blob,
            signature=signature,
        )
        message.save()

    # Mark the thread as read for the draft creator (use message.created_at
    # to stay consistent with inbound_create sender flow)
    models.ThreadAccess.objects.filter(thread=thread, mailbox=mailbox).update(
        read_at=message.created_at
    )

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

    # Pre-validate the new body (no DB writes yet) so we can keep
    # the atomic block below tight around the blob+save pair.
    new_draft_body_bytes = None
    if "draftBody" in update_data and update_data["draftBody"]:
        new_draft_body_bytes = update_data["draftBody"].encode("utf-8")
        validate_body_size(new_draft_body_bytes)

    # Atomic block: any new draft-body Blob INSERT must commit
    # together with the Message.save that establishes the FK on
    # ``Message.draft_blob``. Without this, the new blob row would
    # be visible to other transactions before the FK row, and the
    # GC sweep could reap it as an orphan.
    with transaction.atomic():
        # Update draft body if provided
        if "draftBody" in update_data:
            old_draft_blob_id = message.draft_blob_id
            message.draft_blob = None
            if old_draft_blob_id:
                # Old draft body may now be orphan; let the GC sweep
                # collect it if no other row references the same content.
                schedule_for_gc(old_draft_blob_id)
            if new_draft_body_bytes is not None:
                message.draft_blob = models.Blob.objects.create_blob(
                    content=new_draft_body_bytes,
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
