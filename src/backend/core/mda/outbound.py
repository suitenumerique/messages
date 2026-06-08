"""Handles outbound email delivery logic: composing and sending messages."""
# pylint: disable=broad-exception-caught

import json
import logging
from typing import Any, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

import rest_framework as drf
from jmap_email import (
    compose_email,
    first_address_email,
    parse_email,
)

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import check_local_recipient, deliver_inbound_message
from core.mda.inline_images import (
    extract_inline_images_html,
    extract_inline_images_text,
)
from core.mda.outbound_direct import send_message_via_mx
from core.mda.replies import make_forward, make_reply
from core.mda.signing import sign_message_dkim, verify_message_dkim
from core.mda.smtp import send_smtp_mail
from core.mda.utils import current_sent_at
from core.services.blob_gc import schedule_for_gc
from core.services.dns.check import check_spf_status
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


def validate_mime_size(mime_size: int, message_id: str) -> None:
    """Raise a ValidationError if *mime_size* exceeds the outgoing MIME limit."""
    max_total_size = settings.MAX_OUTGOING_BODY_SIZE + (
        settings.MAX_OUTGOING_ATTACHMENT_SIZE * 1.4
    )
    if mime_size > max_total_size:
        mime_mb = mime_size / (1024 * 1024)
        max_mb = max_total_size / (1024 * 1024)

        logger.error(
            "MIME for message %s exceeds size limit: %d bytes (%.1f MB) > %d bytes (%.0f MB)",
            message_id,
            mime_size,
            mime_mb,
            max_total_size,
            max_mb,
        )

        raise drf.exceptions.ValidationError(
            {
                "message": (
                    "The email (%(mime_size)s MB) exceeds the maximum allowed "
                    "size of %(max_size)s MB."
                )
                % {
                    "mime_size": f"{mime_mb:.1f}",
                    "max_size": f"{max_mb:.0f}",
                }
            }
        )


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


def compose_and_sign_mime(
    message: models.Message,
    mailbox: models.Mailbox,
    text_body: str,
    html_body: str,
    attachments: list | None = None,
    prepend_headers: list | None = None,
    signature: Optional[models.MessageTemplate] = None,
    user: Optional[models.User] = None,
) -> bytes:
    """Compose and DKIM-sign an outbound email; return the raw MIME bytes.

    Pure-Python work — no DB writes, no Blob INSERT — so callers can run
    this outside any surrounding ``transaction.atomic`` block. The
    Message instance is mutated in memory (``mime_id``,
    ``has_attachments``) but not saved here; the caller persists.

    The signature is inserted between the new content and the quoted
    original so recipients see it in the expected position.
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
                nested_data = make_forward(
                    original_message=parent_parsed,
                    body_text=text_body,
                    body_html=html_body,
                    include_original=True,
                )
            else:
                nested_data = make_reply(
                    original_message=parent_parsed,
                    body_text=text_body,
                    body_html=html_body,
                    include_original=True,
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
        "sentAt": current_sent_at(),
        "to": recipients_by_type.get(models.MessageRecipientTypeChoices.TO, []),
        "cc": recipients_by_type.get(models.MessageRecipientTypeChoices.CC, []),
        "subject": message.subject,
        "textBody": [{"content": text_body}] if text_body else [],
        "htmlBody": [{"content": html_body}] if html_body else [],
        "messageId": [message.mime_id] if message.mime_id else None,
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

    return raw_mime


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
        text_body, text_images = extract_inline_images_text(
            text_body, known_images=known_images
        )
        raw_images.extend(text_images)

    if html_body:
        html_body, html_images = extract_inline_images_html(
            html_body, known_images=known_images
        )
        raw_images.extend(html_images)

    # ``extract_inline_images_*`` already returns the JMAP / composer
    # attachment shape (``type`` key, etc.). Set ``disposition="inline"``
    # on each entry so the composer wraps in ``multipart/related`` and
    # emits the ``cid`` Content-ID header.
    inline_attachments = [{**img, "disposition": "inline"} for img in raw_images]

    return text_body, html_body, inline_attachments


def prepare_outbound_message(
    mailbox_sender: models.Mailbox,
    message: models.Message,
    text_body: str,
    html_body: str,
    user: Optional[models.User] = None,
    raw_mime: Optional[bytes] = None,
) -> bool:
    """Prepare a Message for outbound delivery: compose (or accept raw) MIME,
    sign with DKIM, create a blob, and mark the message as non-draft.

    When ``raw_mime`` is provided (e.g. from a raw MIME submission),
    the MIME composition step is skipped and the raw bytes are used directly.
    Validation, throttling, DKIM signing, and blob creation still apply.

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

    if raw_mime is not None:
        # Raw MIME path: caller already has the body. Sign first
        # (CPU work, outside any DB transaction), then take a tight
        # atomic for just the Blob INSERT + FK-establishing save —
        # this keeps the per-sha advisory lock taken inside
        # ``create_blob`` held for ms, not for the duration of DKIM.
        signed_mime = _sign_mime(mailbox_sender, raw_mime)
        validate_mime_size(len(signed_mime), message.id)
        message.sender_user = user
        with transaction.atomic():
            message.blob = models.Blob.objects.create_blob(
                content=signed_mime, content_type="message/rfc822"
            )
            _finalize_sent_message(mailbox_sender, message)
        return True

    # --- Web/API path: compose MIME from text/html body --- #

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

    # Compose + DKIM-sign outside any DB transaction so the per-sha
    # advisory lock taken inside ``create_blob`` isn't held while we
    # do CPU-bound MIME assembly + RSA signing. Validate size before
    # the blob INSERT so an oversize message never creates an orphan
    # row that the GC sweep would have to collect.
    try:
        signed_mime = compose_and_sign_mime(
            message,
            mailbox_sender,
            text_body,
            html_body,
            attachments=attachments or None,
            signature=message.signature,
            user=user,
        )
        validate_mime_size(len(signed_mime), message.id)

        draft_blob_id = message.draft_blob_id
        message.sender_user = user
        with transaction.atomic():
            message.blob = models.Blob.objects.create_blob(
                content=signed_mime, content_type="message/rfc822"
            )
            # ``has_attachments`` is set by ``compose_and_sign_mime``
            # (includes inline signature images), so we do not
            # overwrite it here.
            _finalize_sent_message(
                mailbox_sender,
                message,
                extra_update_fields=("mime_id", "has_attachments"),
            )

            # Drop draft body + attachment-row references now that
            # the message has been finalized; the GC sweep will
            # collect any orphan blobs.
            if draft_blob_id:
                message.draft_blob = None
                message.save(update_fields=["draft_blob"])
                schedule_for_gc(draft_blob_id)
            # Each Attachment is owned 1:1 by this Message via FK;
            # bulk-delete them. The post_delete signal fires for
            # each row and schedules its blob_id for the GC sweep.
            message.attachments.all().delete()
    except drf.exceptions.ValidationError:
        raise
    except Exception:
        logger.exception("Failed to compose MIME for message %s", message.id)
        return False

    return True


def _sign_mime(mailbox_sender: models.Mailbox, raw_mime: bytes) -> bytes:
    """DKIM-sign raw MIME bytes and return the signed bytes.

    Pure-Python; no DB writes. Run outside ``transaction.atomic`` so
    the RSA signing isn't done with the per-sha advisory lock held.
    """
    dkim_signature_header: Optional[bytes] = sign_message_dkim(
        raw_mime_message=raw_mime, maildomain=mailbox_sender.domain
    )
    if dkim_signature_header:
        return dkim_signature_header + b"\r\n" + raw_mime
    return raw_mime


def _finalize_sent_message(
    mailbox_sender: models.Mailbox,
    message: models.Message,
    extra_update_fields: tuple = (),
) -> None:
    """Finalize an outbound message once its blob is attached: clear draft
    state, stamp timestamps, save, mark the thread as read for the sender,
    and refresh thread stats."""
    message.is_draft = False
    message.draft_blob = None
    message.created_at = timezone.now()
    message.updated_at = timezone.now()

    update_fields = [
        "updated_at",
        "blob",
        "is_draft",
        "sender_user",
        "draft_blob",
        "created_at",
        *extra_update_fields,
    ]
    message.save(update_fields=update_fields)

    models.ThreadAccess.objects.filter(
        thread=message.thread,
        mailbox=mailbox_sender,
    ).update(read_at=message.created_at)

    message.thread.update_stats()


def send_message(message: models.Message, force_mta_out: bool = False):
    """Send an existing Message, internally or externally.

    This part is called asynchronously from the celery worker.
    """

    # Refuse to send messages that are draft, not senders, or flagged spam.
    # The spam guard is defence-in-depth: any code path that mints an
    # is_sender=True message on a spam-flagged record (today none, but the
    # invariant must hold for future paths) cannot exfiltrate it via the
    # outbound pipeline.
    if message.is_draft:
        raise ValueError("Cannot send a draft message")
    if not message.is_sender:
        raise ValueError("Cannot send a message we are not sender of")
    if message.is_spam:
        raise ValueError("Cannot send a message flagged as spam")

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
            parsed_email = parse_email(blob_content)
            if parsed_email is None:
                logger.error("Failed to parse email for message %s", message.id)
                # Mark all recipients as failed
                for recipient in message.recipients.all():
                    recipient.delivery_status = MessageDeliveryStatusChoices.FAILED
                    recipient.delivery_message = "Internal error: failed to parse email"
                    recipient.save(
                        update_fields=["delivery_status", "delivery_message"]
                    )
                return

            if first_address_email(parsed_email.get("from")) != message.sender.email:
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
                proxy_host: Optional[str] = None,
            ) -> None:
                status = "delivered" if delivered else "failed"
                relay = smtp_host if not internal else "internal"

                logger.info(
                    (
                        "module=core.mda.outbound.send_message "
                        "message_id=%s to=%s from=%s "
                        "relay=%s socks=%s status=%s error=%s"
                    ),
                    message.id,
                    recipient_email,
                    message.sender.email,
                    relay,
                    proxy_host or "nil",
                    status,
                    json.dumps(error or "nil"),
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
                # Check SPF include chain if enabled (only for external recipients)
                if settings.MESSAGES_SPF_CHECK_OUTGOING:
                    sender_domain = message.sender.mailbox.domain
                    if not check_spf_status(sender_domain):
                        error_msg = f"SPF check failed for domain {sender_domain.name}"
                        logger.warning(
                            "SPF check failed for message %s (domain: %s), marking recipients for retry",
                            message.id,
                            sender_domain.name,
                        )
                        for recipient_email in external_recipients:
                            _mark_delivered(
                                recipient_email, False, False, error_msg, True
                            )
                        return

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
                            status.get("proxy_host"),
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
            smtp_tls_security_level=settings.MTA_OUT_SMTP_TLS_SECURITY_LEVEL,
        )
        return statuses

    raise ValueError(f"Invalid MTA out mode: {mta_out_mode}")
