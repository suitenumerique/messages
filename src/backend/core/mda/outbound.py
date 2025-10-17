"""Handles outbound email delivery logic: composing and sending messages."""
# pylint: disable=broad-exception-caught

import logging
from typing import Any, Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import check_local_recipient, deliver_inbound_message
from core.mda.outbound_direct import send_message_via_mx
from core.mda.rfc5322 import (
    compose_email,
    create_forward_message,
    create_reply_message,
    parse_email_message,
)
from core.mda.signing import sign_message_dkim
from core.mda.smtp import send_smtp_mail

logger = logging.getLogger(__name__)

RETRY_INTERVALS = [
    timezone.timedelta(minutes=15),
    timezone.timedelta(minutes=30),
    timezone.timedelta(minutes=45),
    timezone.timedelta(minutes=60),
    timezone.timedelta(hours=2),
    timezone.timedelta(hours=4),
    timezone.timedelta(hours=8),
    timezone.timedelta(hours=12),
    timezone.timedelta(hours=18),
    timezone.timedelta(hours=24),
    timezone.timedelta(hours=36),
    timezone.timedelta(hours=48),
]


def prepare_outbound_message(
    mailbox_sender: models.Mailbox,
    message: models.Message,
    text_body: str,
    html_body: str,
    user: Optional[models.User] = None,
) -> bool:
    """Compose and sign an existing draft Message object before sending via SMTP.

    This part is called synchronously from the API view.
    """

    # Get recipients from the MessageRecipient model
    recipients_by_type = {
        kind: [{"name": contact.name, "email": contact.email} for contact in contacts]
        for kind, contacts in message.get_all_recipient_contacts().items()
    }

    # TODO: Fetch MIME IDs of "references" from the thread
    # references = message.thread.messages.exclude(id=message.id).order_by("-created_at").all()

    # TODO: set the thread snippet?

    # Generate a MIME id
    message.mime_id = message.generate_mime_id()

    # Insert the validated signature
    validated_signature = mailbox_sender.get_validated_signature(
        message.signature.id if message.signature else None
    )
    if message.signature != validated_signature:
        message.signature = validated_signature
        message.save(update_fields=["signature"])
    if message.signature:
        try:
            signatures = message.signature.render_template(
                mailbox=mailbox_sender, user=user
            )
            if signatures:
                text_body = (
                    text_body + "\n" + signatures["text_body"]
                    if text_body
                    else signatures["text_body"]
                )
                html_body = (
                    html_body + signatures["html_body"]
                    if html_body
                    else signatures["html_body"]
                )
        except Exception as e:
            logger.error(
                "Failed to render signature %s for message %s: %s",
                message.signature.id,
                message.id,
                e,
            )

    # Handle reply and forward message embedding
    if message.parent:
        # Check if this is a forward (subject starts with Fwd:)
        is_forward = message.subject.lower().startswith("fwd:")
        nested_data = None

        if is_forward:
            # Handle forward message embedding
            parent_parsed = message.parent.get_parsed_data()

            nested_data = create_forward_message(
                original_message=parent_parsed,
                forward_text=text_body,
                forward_html=html_body,
                include_original=True,
            )
        else:
            # Handle reply message embedding
            parent_parsed = message.parent.get_parsed_data()

            nested_data = create_reply_message(
                original_message=parent_parsed,
                reply_text=text_body,
                reply_html=html_body,
                include_quote=True,
            )

        # Update the bodies with properly formatted reply content
        if nested_data.get("textBody"):
            text_body = nested_data["textBody"][0]["content"]
        if nested_data.get("htmlBody"):
            html_body = nested_data["htmlBody"][0]["content"]

    # Generate the MIME data dictionary
    mime_data = {
        "from": [
            {
                "name": message.sender.name,
                "email": message.sender.email,
            }
        ],
        "date": timezone.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
        "to": recipients_by_type.get(models.MessageRecipientTypeChoices.TO, []),
        "cc": recipients_by_type.get(models.MessageRecipientTypeChoices.CC, []),
        # BCC is not included in headers
        "subject": message.subject,
        "textBody": [{"content": text_body}] if text_body else [],
        "htmlBody": [{"content": html_body}] if html_body else [],
        "message_id": message.mime_id,
    }

    # Add attachments if present
    if message.attachments.exists():
        attachments = []
        for attachment in message.attachments.select_related("blob").all():
            # Get the blob data
            blob = attachment.blob

            # Add the attachment to the MIME data
            attachments.append(
                {
                    "content": blob.get_content(),  # Decompressed binary content
                    "type": blob.content_type,  # MIME type
                    "name": attachment.name,  # Original filename
                    "disposition": "attachment",  # Default to attachment disposition
                    "size": blob.size,  # Size in bytes
                }
            )

        # Add attachments to the MIME data
        if attachments:
            mime_data["attachments"] = attachments

    # Assemble the raw mime message
    try:
        raw_mime = compose_email(
            mime_data,
            in_reply_to=message.parent.mime_id if message.parent else None,
            # TODO: Add References header logic
        )
    except Exception as e:
        logger.error("Failed to compose MIME for message %s: %s", message.id, e)
        return False

    # Sign the message with DKIM
    dkim_signature_header: Optional[bytes] = sign_message_dkim(
        raw_mime_message=raw_mime, maildomain=mailbox_sender.domain
    )

    raw_mime_signed = raw_mime
    if dkim_signature_header:
        # Prepend the signature header
        raw_mime_signed = dkim_signature_header + b"\r\n" + raw_mime

    # Create a blob to store the raw MIME content
    blob = mailbox_sender.create_blob(
        content=raw_mime_signed,
        content_type="message/rfc822",
    )

    draft_blob = message.draft_blob

    message.blob = blob
    message.is_draft = False
    message.draft_blob = None
    message.created_at = timezone.now()
    message.updated_at = timezone.now()
    message.save(
        update_fields=[
            "updated_at",
            "blob",
            "mime_id",
            "is_draft",
            "draft_blob",
            "created_at",
        ]
    )
    message.thread.update_stats()

    # Clean up the draft blob and the attachment blobs
    if draft_blob:
        draft_blob.delete()
    for attachment in message.attachments.all():
        if attachment.blob:
            attachment.blob.delete()
        attachment.delete()

    return True


def send_message(message: models.Message, force_mta_out: bool = False):
    """Send an existing Message, internally or externally.

    This part is called asynchronously from the celery worker.
    """

    # Refuse to send messages that are draft or not senders
    if message.is_draft:
        raise ValueError("Cannot send a draft message")
    if not message.is_sender:
        raise ValueError("Cannot send a message we are not sender of")

    # Create a unique lock key for this message to prevent double sends
    lock_key = f"send_message_lock:{message.id}"
    lock_timeout = 1800  # 30 minutes timeout for the lock

    # Try to acquire the lock
    if not cache.add(lock_key, "locked", lock_timeout):
        logger.warning(
            "Message %s is already being sent by another worker, skipping duplicate send",
            message.id,
        )
        return

    try:
        message.sent_at = timezone.now()
        message.save(update_fields=["sent_at"])

        # Include all recipients in the envelope that have not been delivered yet, including BCC
        envelope_to = {
            recipient.contact.email: recipient
            for recipient in message.recipients.select_related("contact").all()
            if recipient.delivery_status
            in {
                None,
                MessageDeliveryStatusChoices.RETRY,
            }
            and (recipient.retry_at is None or recipient.retry_at <= timezone.now())
        }

        def _mark_delivered(
            recipient_email: str,
            delivered: bool,
            internal: bool,
            error: Optional[str] = None,
            retry: Optional[bool] = False,
            smtp_host: Optional[str] = None,
        ) -> None:
            status = "delivered" if delivered else "failed"
            relay = smtp_host if not internal else "internal"

            logger.info(
                "module=core.mda.outbound.send_message message_id=%s to=%s from=%s relay=%s status=%s error=(%s)",
                message.id,
                recipient_email,
                message.sender.email,
                relay,
                status,
                error or "nil",
            )
            if delivered:
                # TODO also update message.updated_at?
                envelope_to[recipient_email].delivered_at = timezone.now()
                envelope_to[recipient_email].delivery_message = None
                envelope_to[recipient_email].delivery_status = (
                    MessageDeliveryStatusChoices.INTERNAL
                    if internal
                    else MessageDeliveryStatusChoices.SENT
                )
                envelope_to[recipient_email].save(
                    update_fields=[
                        "delivered_at",
                        "delivery_message",
                        "delivery_status",
                    ]
                )
            elif retry and envelope_to[recipient_email].retry_count < len(
                RETRY_INTERVALS
            ):
                envelope_to[recipient_email].retry_at = (
                    timezone.now()
                    + RETRY_INTERVALS[envelope_to[recipient_email].retry_count]
                )
                envelope_to[recipient_email].retry_count += 1
                envelope_to[
                    recipient_email
                ].delivery_status = MessageDeliveryStatusChoices.RETRY
                envelope_to[recipient_email].delivery_message = error
                envelope_to[recipient_email].save(
                    update_fields=[
                        "retry_at",
                        "retry_count",
                        "delivery_status",
                        "delivery_message",
                    ]
                )
            else:
                envelope_to[
                    recipient_email
                ].delivery_status = MessageDeliveryStatusChoices.FAILED
                envelope_to[recipient_email].delivery_message = error
                envelope_to[recipient_email].save(
                    update_fields=["delivery_status", "delivery_message"]
                )

        external_recipients = set()
        parsed_email = None
        blob_content = message.blob.get_content()
        for recipient_email in envelope_to:
            if (
                check_local_recipient(recipient_email, create_if_missing=True)
                and not force_mta_out
            ):
                try:
                    if parsed_email is None:
                        parsed_email = parse_email_message(blob_content)
                    delivered = deliver_inbound_message(
                        recipient_email, parsed_email, blob_content
                    )
                    _mark_delivered(recipient_email, delivered, True)
                except Exception as e:
                    logger.error(
                        "Failed to deliver internal message to %s: %s",
                        recipient_email,
                        e,
                    )
                    _mark_delivered(recipient_email, False, True, str(e), False)

            else:
                external_recipients.add(recipient_email)

        if external_recipients:
            try:
                statuses = send_outbound_message(
                    external_recipients, message, blob_content
                )
                for recipient_email, status in statuses.items():
                    _mark_delivered(
                        recipient_email,
                        status["delivered"],
                        False,
                        status.get("error"),
                        status.get("retry", False),
                        status.get("smtp_host"),
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Failed to send outbound message: %s", e, exc_info=True)
                for recipient_email in external_recipients:
                    _mark_delivered(
                        recipient_email,
                        False,
                        False,
                        "Internal error while delivering",
                        True,
                    )
    finally:
        # Always release the lock when done
        cache.delete(lock_key)


def send_outbound_message(
    recipient_emails: set[str], message: models.Message, mime_data: bytes
) -> dict[str, Any]:
    """Send an existing Message object via MTA out (SMTP) or direct MX if not configured."""

    return send_outbound_email(
        recipient_emails,
        message.sender.email,
        mime_data,
        message.sender.mailbox.domain.custom_attributes or {},
    )


def send_outbound_email(
    recipient_emails: set[str],
    envelope_from: str,
    mime_data: bytes,
    custom_attributes: dict[str, Any],
) -> dict[str, Any]:
    """Send an existing email via MTA out (SMTP) or direct MX if not configured."""

    mta_out_mode = custom_attributes.get("_mta_out_mode") or settings.MTA_OUT_MODE

    # Use direct MX delivery
    if mta_out_mode == "direct":
        return send_message_via_mx(envelope_from, recipient_emails, mime_data)

    if mta_out_mode == "relay":
        mta_out_smtp_host = (
            custom_attributes.get("_mta_out_smtp_host") or settings.MTA_OUT_RELAY_HOST
        )
        mta_out_smtp_username = (
            custom_attributes.get("_mta_out_smtp_username")
            or settings.MTA_OUT_RELAY_USERNAME
        )
        mta_out_smtp_password = (
            custom_attributes.get("_mta_out_smtp_password")
            or settings.MTA_OUT_RELAY_PASSWORD
        )

        statuses = send_smtp_mail(
            smtp_host=(mta_out_smtp_host or "").split(":")[0],
            smtp_port=int(
                (mta_out_smtp_host or "").split(":")[1]
                if ":" in mta_out_smtp_host
                else 587
            ),
            envelope_from=envelope_from,
            recipient_emails=recipient_emails,
            message_content=mime_data,
            smtp_username=mta_out_smtp_username,
            smtp_password=mta_out_smtp_password,
        )
        return statuses

    raise ValueError(f"Invalid MTA out mode: {mta_out_mode}")
