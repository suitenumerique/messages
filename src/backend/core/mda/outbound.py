"""Handles outbound email delivery logic: composing and sending messages."""
# pylint: disable=broad-exception-caught

import logging
import smtplib
from collections import defaultdict
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import check_local_recipient, deliver_inbound_message
from core.mda.rfc5322 import compose_email, parse_email_message
from core.mda.signing import sign_message_dkim

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
    message: models.Message, text_body: str, html_body: str
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

    # Generate the MIME data dictionary
    mime_data = {
        "from": [
            {
                "name": message.sender.name,
                "email": message.sender.email,
            }
        ],
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
                    "content": blob.raw_content,  # Binary content
                    "type": blob.type,  # MIME type
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
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to compose MIME for message %s: %s", message.id, e)
        return False

    # Sign the message with DKIM
    dkim_signature_header: Optional[bytes] = sign_message_dkim(
        raw_mime_message=raw_mime, sender_email=message.sender.email
    )

    raw_mime_signed = raw_mime
    if dkim_signature_header:
        # Prepend the signature header
        raw_mime_signed = dkim_signature_header + b"\r\n" + raw_mime

    message.raw_mime = raw_mime_signed
    message.is_draft = False
    message.draft_body = None
    message.created_at = timezone.now()
    message.updated_at = timezone.now()
    message.save(
        update_fields=[
            "updated_at",
            "raw_mime",
            "mime_id",
            "is_draft",
            "draft_body",
            "created_at",
        ]
    )
    message.thread.update_stats()

    return True


def send_message(message: models.Message, force_mta_out: bool = False):
    """Send an existing Message, internally or externally.

    This part is called asynchronously from the celery worker.
    """

    message.sent_at = timezone.now()
    message.save(update_fields=["sent_at"])

    mime_data = parse_email_message(message.raw_mime)

    # Include all recipients in the envelope that have not been delivered yet, including BCC
    envelope_to = {
        recipient.contact.email: recipient
        for recipient in message.recipients.select_related("contact").all()
        if recipient.delivery_status
        in {
            None,
            MessageDeliveryStatusChoices.RETRY,
        }
        and (recipient.retry_at is None or recipient.retry_at < timezone.now())
    }

    def _mark_delivered(
        recipient_email: str,
        delivered: bool,
        internal: bool,
        error: Optional[str] = None,
    ) -> None:
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
                update_fields=["delivered_at", "delivery_message", "delivery_status"]
            )
        elif envelope_to[recipient_email].retry_count < len(RETRY_INTERVALS):
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

    external_recipients_by_domain = defaultdict(list)
    for recipient_email in envelope_to:
        if (
            check_local_recipient(recipient_email, create_if_missing=True)
            and not force_mta_out
        ):
            try:
                delivered = deliver_inbound_message(
                    recipient_email, mime_data, message.raw_mime
                )
                _mark_delivered(recipient_email, delivered, True)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Failed to deliver internal message to %s: %s", recipient_email, e
                )
                _mark_delivered(recipient_email, False, True, str(e))

        else:
            # TODO: actual grouping should be by MX, not by email domain
            # grouping_key = recipient_email.split("@")[1]
            # For now we don't group at all.
            grouping_key = "all"
            external_recipients_by_domain[grouping_key].append(recipient_email)

    if len(external_recipients_by_domain) > 0:
        for external_recipients in external_recipients_by_domain.values():
            statuses = send_outbound_message(external_recipients, message)
            for recipient_email, status in statuses.items():
                _mark_delivered(
                    recipient_email, status["delivered"], False, status.get("error")
                )


def send_outbound_message(
    recipient_emails: list[str], message: models.Message
) -> dict[str, Any]:
    """Send an existing Message object via MTA out (SMTP)."""

    def _global_error(error: str) -> dict[str, Any]:
        return {
            email: {
                "error": error,
                "delivered": False,
            }
            for email in recipient_emails
        }

    # Send via SMTP
    if not settings.MTA_OUT_HOST:
        logger.warning(
            "MTA_OUT_HOST is not set, skipping SMTP sending for %s", message.id
        )
        return _global_error("MTA_OUT_HOST is not set")

    smtp_host, smtp_port_str = settings.MTA_OUT_HOST.split(":")
    smtp_port = int(smtp_port_str)
    envelope_from = message.sender.email

    statuses = {}

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as client:
            client.ehlo()
            if settings.MTA_OUT_SMTP_USE_TLS:
                client.starttls()
                client.ehlo()  # Re-EHLO after STARTTLS

            # Authenticate if credentials provided
            if settings.MTA_OUT_SMTP_USERNAME and settings.MTA_OUT_SMTP_PASSWORD:
                client.login(
                    settings.MTA_OUT_SMTP_USERNAME,
                    settings.MTA_OUT_SMTP_PASSWORD,
                )

            smtp_response = client.sendmail(
                envelope_from, recipient_emails, message.raw_mime
            )
            logger.info(
                "Sent message %s via SMTP. Response: %s",
                message.id,
                smtp_response,
            )
            # Return delivery success for each recipient, looking at smtp_response
            for recipient_email in recipient_emails:
                if recipient_email not in smtp_response:
                    statuses[recipient_email] = {"delivered": True}
                else:
                    statuses[recipient_email] = {
                        "error": smtp_response[recipient_email],
                        "delivered": False,
                    }

    except (smtplib.SMTPException, OSError) as e:
        logger.error(
            "SMTP error sending message %s: %s",
            message.id,
            e,
        )
        return _global_error(str(e))

    return statuses
