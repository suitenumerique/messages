"""Autoreply logic: loop detection, rate limiting, and sending."""

import logging
import re
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import models
from core.enums import (
    MessageRecipientTypeChoices,
    MessageTemplateTypeChoices,
)
from core.mda.outbound import compose_and_store_mime
from core.mda.rfc5322.composer import make_reply_subject
from core.services.throttle import ThrottleLimitExceeded, ThrottleManager

logger = logging.getLogger(__name__)

# Headers that indicate an automatic message (loop prevention)
_PRECEDENCE_VALUES = {"bulk", "list", "junk"}
_LOOP_HEADERS = {
    "x-auto-response-suppress",
    "x-autoreply",
    "x-autorespond",
    "x-loop",
    "list-id",
    "list-unsubscribe",
    "list-post",
    "list-help",
    "list-subscribe",
    "list-owner",
    "list-archive",
    "feedback-id",
}

# Addresses that should never receive autoreplies (RFC 3834 / RFC 5230)
_NOREPLY_PATTERNS = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|postmaster|"
    r"mailer[-_.]?daemon|listserv|majordomo|bounce[s]?|"
    r"abuse|hostmaster|webmaster|root|noreply)"
    r"|^owner-|-request@|-owner@|-(bounces?|errors|confirm)@",
    re.IGNORECASE,
)


def _is_noreply_address(email: str) -> bool:
    """Return True if the email matches a well-known system/noreply address."""
    return bool(_NOREPLY_PATTERNS.search(email))


def _is_recipient_explicit(mailbox_email: str, parsed_email_headers: dict) -> bool:
    """Check that the mailbox address appears in To or Cc.

    Per RFC 5230 Section 4.5, a vacation responder MUST NOT respond to a
    message unless the recipient's address is explicitly listed.  We only
    check To and Cc because BCC headers are stripped before delivery — if
    the mailbox was BCC'd its address won't appear in the received headers,
    which is exactly the behaviour we want (no autoreply for BCC'd copies).
    """
    target = mailbox_email.lower()
    for field in ("to", "cc"):
        recipients = parsed_email_headers.get(field) or []
        for recipient in recipients:
            if isinstance(recipient, dict):
                if recipient.get("email", "").lower() == target:
                    return True
            elif isinstance(recipient, str):
                if recipient.lower() == target:
                    return True
    return False


def _is_auto_reply_message(headers: dict) -> bool:
    """Detect whether the inbound message is itself an automatic reply.

    Checks Auto-Submitted, Precedence, List-Id, X-Auto-Response-Suppress,
    X-Autoreply, X-Autorespond headers.
    """
    if not headers:
        return False

    # Normalize header keys to lowercase for comparison
    lower_headers = {k.lower(): v for k, v in headers.items()}

    # Return-Path: empty or <> means bounce (RFC 3834)
    if "return-path" in lower_headers:
        return_path = lower_headers["return-path"].strip()
        if return_path in ("", "<>"):
            return True

    # Auto-Submitted: anything other than "no" means auto-generated.
    # RFC 3834 allows parameters after ";" (e.g. "auto-replied; owner-email=...").
    auto_submitted = lower_headers.get("auto-submitted", "").strip().lower()
    if auto_submitted:
        # Strip parameters: "auto-replied; foo=bar" -> "auto-replied"
        auto_submitted_value = auto_submitted.split(";", 1)[0].strip()
        if auto_submitted_value and auto_submitted_value != "no":
            return True

    # Precedence: bulk, list, junk
    precedence = lower_headers.get("precedence", "").strip().lower()
    if precedence in _PRECEDENCE_VALUES:
        return True

    # Presence of any loop header
    for header_name in _LOOP_HEADERS:
        if lower_headers.get(header_name):
            return True

    return False


def should_send_autoreply(
    mailbox: models.Mailbox,
    parsed_email_headers: dict,
    is_spam: bool = False,
) -> Optional[models.MessageTemplate]:
    """Determine whether we should send an autoreply and return the template.

    Returns the active autoreply MessageTemplate if all conditions pass,
    otherwise None.
    """
    # 1. Never autoreply to spam
    if is_spam:
        return None

    headers = parsed_email_headers.get("headers", {})

    # 2. Skip auto-generated messages (loop prevention)
    if _is_auto_reply_message(headers):
        return None

    # 3. Self-reply prevention: skip if sender == mailbox email
    sender_info = parsed_email_headers.get("from", {})
    sender_email = sender_info.get("email", "").lower() if sender_info else ""

    if not sender_email:
        return None

    mailbox_email = str(mailbox).lower()
    if sender_email == mailbox_email:
        return None

    # 3b. Skip well-known system/noreply addresses
    if _is_noreply_address(sender_email):
        return None

    # 3c. RFC 5230 §4.5: only reply if mailbox address appears in To/Cc.
    #     Prevents autoreplies to BCC'd copies and mailing-list expansions.
    if not _is_recipient_explicit(mailbox_email, parsed_email_headers):
        return None

    # 4. Find active autoreply template for this mailbox
    template = (
        models.MessageTemplate.objects.filter(
            mailbox=mailbox,
            type=MessageTemplateTypeChoices.AUTOREPLY,
            is_active=True,
        )
        .select_related("blob", "signature__blob")
        .first()
    )
    if not template:
        return None

    # 5. Check schedule
    if not template.is_active_autoreply():
        return None

    # 6. Rate limiting: check and atomically increment the throttle counter
    try:
        with ThrottleManager() as throttle:
            throttle.check_limit(
                settings.THROTTLE_AUTOREPLY_PER_SENDER,
                "autoreply",
                f"{mailbox.id}:{sender_email}",
                counter_type="sends",
            )
    except ThrottleLimitExceeded:
        return None

    return template


def send_autoreply_for_message(
    template: models.MessageTemplate,
    mailbox: models.Mailbox,
    inbound_message: models.Message,
):
    """Compose and send an autoreply, creating a real Message record."""
    # pylint: disable-next=import-outside-toplevel
    from core.mda.outbound_tasks import send_message_task

    sender_email = ""
    if inbound_message.sender:
        sender_email = inbound_message.sender.email

    if not sender_email:
        logger.warning(
            "Cannot send autoreply: inbound message %s has no sender email",
            inbound_message.id,
        )
        return

    thread = inbound_message.thread

    # 1. Get or create a Contact for the mailbox's own email (the autoreply sender)
    mailbox_email = str(mailbox)
    mailbox_contact, _ = models.Contact.objects.get_or_create(
        email=mailbox_email,
        mailbox=mailbox,
        defaults={"name": mailbox.contact.name if mailbox.contact else mailbox_email},
    )

    # 2. Build subject with Re: prefix
    reply_subject = make_reply_subject(inbound_message.subject or "")[:255]

    # 3-7: Create records and compose MIME atomically so a failure in
    #       compose_and_store_mime does not leave orphan Message/Recipient rows.
    with transaction.atomic():
        # 3. Create Message record
        message = models.Message.objects.create(
            thread=thread,
            sender=mailbox_contact,
            subject=reply_subject,
            parent=inbound_message,
            sent_at=timezone.now(),
            is_draft=False,
            is_sender=True,
            is_trashed=False,
            is_spam=False,
        )

        # 4. Create MessageRecipient (must exist before compose_and_store_mime)
        models.MessageRecipient.objects.create(
            message=message,
            contact=inbound_message.sender,
            type=MessageRecipientTypeChoices.TO,
        )

        # 5. Resolve signature: forced domain/mailbox signature takes priority
        #    over the one attached to the autoreply template
        validated_signature = mailbox.get_validated_signature(
            template.signature.id if template.signature else None
        )

        # 6. Compose MIME, DKIM sign, and store as blob
        #    (signature + reply quote embedding is handled by compose_and_store_mime)
        auto_reply_headers = [
            ("Auto-Submitted", "auto-replied"),
            ("X-Auto-Response-Suppress", "All"),
            ("Precedence", "bulk"),
        ]
        compose_and_store_mime(
            message,
            mailbox,
            template.text_body,
            template.html_body,
            prepend_headers=auto_reply_headers,
            signature=validated_signature,
        )
        message.save(update_fields=["mime_id", "blob", "has_attachments"])

    # 7. Trigger async send (outside transaction to avoid sending before commit)
    send_message_task.delay(str(message.id))

    # 8. Update thread stats — do NOT update read_at here: the autoreply
    # sender is away, so the thread must stay unread for them to notice
    # new messages when they return.
    thread.update_stats()

    logger.info(
        "Autoreply message %s created and queued for sending (mailbox=%s, to=%s)",
        message.id,
        mailbox.id,
        sender_email,
    )


def try_send_autoreply(
    mailbox: models.Mailbox,
    parsed_email: dict,
    message: models.Message,
    is_spam: bool = False,
):
    """Evaluate autoreply conditions and send if appropriate.

    Safe to call from any delivery path (MTA inbound, internal delivery).
    Exceptions are logged but never propagated.
    """
    try:
        parsed_headers = {
            "from": parsed_email.get("from", {}),
            "to": parsed_email.get("to", []),
            "cc": parsed_email.get("cc", []),
            "subject": parsed_email.get("subject", ""),
            "messageId": parsed_email.get("messageId")
            or parsed_email.get("message_id"),
            "headers": parsed_email.get("headers", {}),
        }
        template = should_send_autoreply(mailbox, parsed_headers, is_spam=is_spam)
        if template:
            send_autoreply_for_message(template, mailbox, message)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Autoreply failed for mailbox %s, message %s",
            mailbox.id,
            message.id,
        )
