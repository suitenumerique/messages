"""Handles outbound email delivery logic: composing and sending messages."""
# pylint: disable=broad-exception-caught

import logging
from typing import Any, Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

import rest_framework as drf

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import check_local_recipient, deliver_inbound_message
from core.mda.outbound_direct import send_message_via_mx
from core.mda.rfc5322 import (
    EmailParseError,
    compose_email,
    create_forward_message,
    create_reply_message,
    extract_base64_images_from_html,
    extract_base64_images_from_text,
    parse_email_message,
)
from core.mda.signing import sign_message_dkim, verify_message_dkim
from core.mda.smtp import send_smtp_mail
from core.services.throttle import check_and_increment_throttle
from core.utils import ThreadStatsUpdateDeferrer

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


def validate_attachments_size(total_size: int, message_id: str) -> None:
    """Raise a ValidationError if *total_size* exceeds the outgoing limit."""
    if total_size > settings.MAX_OUTGOING_ATTACHMENT_SIZE:
        max_mb = settings.MAX_OUTGOING_ATTACHMENT_SIZE / (1024 * 1024)

        logger.error(
            "Total attachment size for message %s exceeds configured limit of %d bytes (%.0f MB)",
            message_id,
            settings.MAX_OUTGOING_ATTACHMENT_SIZE,
            max_mb,
        )

        raise drf.exceptions.ValidationError(
            {
                "message": (
                    f"Total attachment size exceeds the {max_mb:.0f} MB limit. "
                    "Please remove or reduce attachments."
                )
            }
        )


def compose_and_store_mime(
    message: models.Message,
    mailbox: models.Mailbox,
    text_body: str,
    html_body: str,
    attachments: list | None = None,
    prepend_headers: list | None = None,
    signature: Optional[models.MessageTemplate] = None,
    user: Optional[models.User] = None,
) -> None:
    """Compose a complete outbound email: append signature, embed reply/forward
    quote, generate MIME, DKIM sign, and store as blob on the message.

    The signature is inserted between the new content and the quoted original
    so recipients see it in the expected position.

    The Message must already have a sender and MessageRecipients in the DB.
    Updates message.mime_id, message.blob and message.has_attachments
    (caller is responsible for saving).
    """
    # 1. Append signature (before quoting so it sits between reply and quote)
    text_body, html_body, inline_attachments = (
        append_signature_and_extract_inline_images(
            text_body,
            html_body,
            signature=signature,
            mailbox=mailbox,
            user=user,
            message=message,
        )
    )

    # 2. Embed reply/forward quote
    if message.parent:
        parent_parsed = message.parent.get_parsed_data()
        if parent_parsed:
            is_forward = (message.subject or "").lower().startswith("fwd:")
            if is_forward:
                nested_data = create_forward_message(
                    original_message=parent_parsed,
                    forward_text=text_body,
                    forward_html=html_body,
                    include_original=True,
                )
            else:
                nested_data = create_reply_message(
                    original_message=parent_parsed,
                    reply_text=text_body,
                    reply_html=html_body,
                    include_quote=True,
                )
            if nested_data.get("textBody"):
                text_body = nested_data["textBody"][0]["content"]
            if nested_data.get("htmlBody"):
                html_body = nested_data["htmlBody"][0]["content"]

    # 3. Merge inline attachments from signature with caller-provided attachments
    all_attachments = list(attachments or [])
    caller_size = sum(a.get("size", 0) for a in all_attachments)
    for img in inline_attachments:
        caller_size += img["size"]
        all_attachments.append(img)
        validate_attachments_size(caller_size, message.id)

    # 4. Compose MIME
    message.mime_id = message.generate_mime_id()

    recipients_by_type = {
        kind: [{"name": c.name, "email": c.email} for c in contacts]
        for kind, contacts in message.get_all_recipient_contacts().items()
    }

    mime_data = {
        "from": [{"name": message.sender.name, "email": message.sender.email}],
        "date": timezone.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
        "to": recipients_by_type.get(models.MessageRecipientTypeChoices.TO, []),
        "cc": recipients_by_type.get(models.MessageRecipientTypeChoices.CC, []),
        "subject": message.subject,
        "textBody": [{"content": text_body}] if text_body else [],
        "htmlBody": [{"content": html_body}] if html_body else [],
        "message_id": message.mime_id,
    }

    if all_attachments:
        mime_data["attachments"] = all_attachments
    message.has_attachments = bool(all_attachments)

    raw_mime = compose_email(
        mime_data,
        in_reply_to=message.parent.mime_id if message.parent else None,
        prepend_headers=prepend_headers,
    )

    dkim_header = sign_message_dkim(raw_mime, mailbox.domain)
    if dkim_header:
        raw_mime = dkim_header + b"\r\n" + raw_mime

    message.blob = mailbox.create_blob(content=raw_mime, content_type="message/rfc822")


def append_signature_and_extract_inline_images(
    text_body: str,
    html_body: str,
    signature: Optional[models.MessageTemplate] = None,
    mailbox: Optional[models.Mailbox] = None,
    user: Optional[models.User] = None,
    message: Optional[models.Message] = None,
) -> tuple[str, str, list]:
    """Append signature to bodies and extract base64 images as inline CID attachments.

    Returns (text_body, html_body, inline_attachments).
    """
    if signature:
        try:
            rendered = signature.render_template(
                mailbox=mailbox, user=user, message=message
            )
            if rendered:
                text_body = (
                    text_body + "\n" + rendered["text_body"]
                    if text_body
                    else rendered["text_body"]
                )
                html_body = (
                    html_body + rendered["html_body"]
                    if html_body
                    else rendered["html_body"]
                )
        except Exception as e:
            logger.error(
                "Failed to render signature %s: %s",
                signature.id,
                e,
            )

    known_images: dict[str, str] = {}
    raw_images = []

    if text_body:
        text_body, text_images = extract_base64_images_from_text(
            text_body, known_images=known_images
        )
        raw_images.extend(text_images)

    if html_body:
        html_body, html_images = extract_base64_images_from_html(
            html_body, known_images=known_images
        )
        raw_images.extend(html_images)

    # Normalize to the format expected by compose_email
    inline_attachments = [
        {
            "content": img["content"],
            "type": img["content_type"],
            "name": img["name"],
            "disposition": "inline",
            "cid": img["cid"],
            "size": img["size"],
        }
        for img in raw_images
    ]

    return text_body, html_body, inline_attachments


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

    # Enforce per-message recipient limit (to + cc + bcc)
    recipient_count = message.recipients.count()
    max_recipients = settings.MAX_RECIPIENTS_PER_MESSAGE
    if recipient_count > max_recipients:
        raise drf.exceptions.ValidationError(
            {
                "message": (
                    "Too many recipients: %(count)s (maximum is %(max)s). "
                    "Please reduce the number of recipients before sending."
                )
                % {"count": recipient_count, "max": max_recipients}
            }
        )

    # Throttle external recipients per mailbox/maildomain
    # ThrottleLimitExceeded propagates to the DRF exception handler (HTTP 429)
    check_and_increment_throttle(
        mailbox=mailbox_sender,
        maildomain=mailbox_sender.domain,
        message=message,
    )

    # TODO: Fetch MIME IDs of "references" from the thread
    # references = message.thread.messages.exclude(id=message.id).order_by("-created_at").all()

    # TODO: set the thread snippet?

    # Insert the validated signature
    validated_signature = mailbox_sender.get_validated_signature(
        message.signature.id if message.signature else None
    )
    if message.signature != validated_signature:
        message.signature = validated_signature
        message.save(update_fields=["signature"])

    # Add attachments if present and ensure they don't exceed the limit
    attachments = []
    total_attachment_size = 0

    if message.attachments.exists():
        for attachment in message.attachments.select_related("blob").all():
            # Get the blob data
            blob = attachment.blob
            total_attachment_size += blob.size

            # Add the attachment to the MIME data
            # Use inline disposition if attachment has a Content-ID (for inline images)
            attachments.append(
                {
                    "content": blob.get_content(),  # Decompressed binary content
                    "type": blob.content_type,  # MIME type
                    "name": attachment.name,  # Original filename
                    "disposition": "inline" if attachment.cid else "attachment",
                    "cid": attachment.cid,  # Content-ID for inline images
                    "size": blob.size,  # Size in bytes
                }
            )
            validate_attachments_size(total_attachment_size, message.id)

    # Compose MIME, DKIM sign, and store as blob
    try:
        compose_and_store_mime(
            message,
            mailbox_sender,
            text_body,
            html_body,
            attachments=attachments or None,
            signature=message.signature,
            user=user,
        )
    except drf.exceptions.ValidationError:
        raise
    except Exception as e:
        logger.error("Failed to compose MIME for message %s: %s", message.id, e)
        return False

    # Validate the composed MIME size
    mime_size = message.blob.size
    max_total_size = settings.MAX_OUTGOING_BODY_SIZE + (
        settings.MAX_OUTGOING_ATTACHMENT_SIZE * 1.4
    )
    if mime_size > max_total_size:
        mime_mb = mime_size / (1024 * 1024)
        max_mb = max_total_size / (1024 * 1024)

        logger.error(
            "Composed MIME for message %s exceeds size limit: %d bytes (%.1f MB) > %d bytes (%.0f MB)",
            message.id,
            mime_size,
            mime_mb,
            max_total_size,
            max_mb,
        )

        raise drf.exceptions.ValidationError(
            {
                "message": (
                    "The composed email (%(mime_size)s MB) exceeds the maximum allowed size of %(max_size)s MB. "
                    "Please reduce message content or attachments."
                )
                % {
                    "mime_size": f"{mime_mb:.1f}",
                    "max_size": f"{max_mb:.0f}",
                }
            }
        )

    draft_blob = message.draft_blob

    message.is_draft = False
    message.sender_user = user
    message.draft_blob = None
    message.created_at = timezone.now()
    message.updated_at = timezone.now()
    message.save(
        update_fields=[
            "updated_at",
            "blob",
            "mime_id",
            "is_draft",
            "sender_user",
            "draft_blob",
            "has_attachments",
            "created_at",
        ]
    )
    # Mark the thread as read for the sender — they've obviously seen
    # their own message, so read_at must be >= messaged_at.
    models.ThreadAccess.objects.filter(
        thread=message.thread,
        mailbox=mailbox_sender,
    ).update(read_at=message.created_at)

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
        # Use context manager to batch thread stats updates for all delivery status changes
        with ThreadStatsUpdateDeferrer.defer():
            blob_content = message.blob.get_content()
            try:
                parsed_email = parse_email_message(blob_content)
            except EmailParseError as e:
                logger.error(
                    "Failed to parse email for message %s: %s",
                    message.id,
                    e,
                )
                # Mark all recipients as failed
                for recipient in message.recipients.all():
                    recipient.delivery_status = MessageDeliveryStatusChoices.FAILED
                    recipient.delivery_message = "Internal error: failed to parse email"
                    recipient.save(
                        update_fields=["delivery_status", "delivery_message"]
                    )
                return

            if parsed_email.get("from", {}).get("email") != message.sender.email:
                raise ValueError("Mailbox email does not match the raw message sender")

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
            for recipient_email in envelope_to:
                if (
                    check_local_recipient(recipient_email, create_if_missing=True)
                    and not force_mta_out
                ):
                    try:
                        delivered = deliver_inbound_message(
                            recipient_email,
                            parsed_email,
                            blob_content,
                            skip_inbound_queue=True,
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
                # Verify DKIM signature if enabled (only for external recipients)
                if settings.MESSAGES_DKIM_VERIFY_OUTGOING:
                    sender_domain = message.sender.mailbox.domain

                    if not verify_message_dkim(blob_content):
                        error_msg = (
                            f"DKIM verification failed for domain {sender_domain.name}"
                        )
                        logger.warning(
                            "DKIM verification failed for message %s (domain: %s), marking recipients for retry",
                            message.id,
                            sender_domain.name,
                        )
                        for recipient_email in external_recipients:
                            _mark_delivered(
                                recipient_email, False, False, error_msg, True
                            )
                        return
                    logger.info(
                        "DKIM verification successful for message %s (domain: %s)",
                        message.id,
                        sender_domain.name,
                    )

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
                    logger.error(
                        "Failed to send outbound message: %s", e, exc_info=True
                    )
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
        message.sender.mailbox.domain.custom_settings or {},
    )


def send_outbound_email(
    recipient_emails: set[str],
    envelope_from: str,
    mime_data: bytes,
    custom_settings: dict[str, Any],
) -> dict[str, Any]:
    """Send an existing email via MTA out (SMTP) or direct MX if not configured."""

    mta_out_mode = custom_settings.get("MTA_OUT_MODE") or settings.MTA_OUT_MODE

    # Use direct MX delivery
    if mta_out_mode == "direct":
        return send_message_via_mx(envelope_from, recipient_emails, mime_data)

    if mta_out_mode == "relay":
        mta_out_smtp_host = (
            custom_settings.get("MTA_OUT_RELAY_HOST") or settings.MTA_OUT_RELAY_HOST
        )
        mta_out_smtp_username = (
            custom_settings.get("MTA_OUT_RELAY_USERNAME")
            or settings.MTA_OUT_RELAY_USERNAME
        )
        mta_out_smtp_password = (
            custom_settings.get("MTA_OUT_RELAY_PASSWORD")
            or settings.MTA_OUT_RELAY_PASSWORD
        )
        if not mta_out_smtp_host:
            raise ValueError("MTA_OUT_RELAY_HOST is not configured")

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
