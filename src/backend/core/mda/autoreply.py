"""Autoreply logic: loop detection, rate limiting, and sending."""

import logging
import re
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from jmap_email import (
    JmapEmail,
    find_header,
    find_headers,
    first_address_email,
    has_header,
)

from core import models
from core.enums import (
    MessageRecipientTypeChoices,
    MessageTemplateTypeChoices,
)
from core.mda.outbound import compose_and_sign_mime
from core.mda.replies import reply_subject
from core.services.throttle import ThrottleLimitExceeded, ThrottleManager

logger = logging.getLogger(__name__)

# Headers that indicate an automatic message (loop prevention)
_PRECEDENCE_VALUES = {"bulk", "list", "junk"}
_LOOP_HEADERS = (
    "X-Auto-Response-Suppress",
    "X-Autoreply",
    "X-Autorespond",
    "X-Loop",
    "List-Id",
    "List-Unsubscribe",
    "List-Post",
    "List-Help",
    "List-Subscribe",
    "List-Owner",
    "List-Archive",
    "Feedback-ID",
)

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


def _is_recipient_explicit(mailbox_email: str, parsed_email: JmapEmail) -> bool:
    """Check that the mailbox address appears in To or Cc.

    Per RFC 5230 §4.5, a vacation responder MUST NOT respond to a
    message unless the recipient's address is explicitly listed. We
    only check To and Cc because BCC headers are stripped before
    delivery — if the mailbox was BCC'd its address won't appear in
    the received headers, which is exactly the behaviour we want
    (no autoreply for BCC'd copies).
    """
    target = mailbox_email.lower()
    for field in ("to", "cc"):
        for entry in parsed_email.get(field) or []:
            if isinstance(entry, dict) and (entry.get("email") or "").lower() == target:
                return True
    return False


def _is_auto_reply_message(
    parsed_email: JmapEmail, envelope: Optional[dict] = None
) -> bool:
    """Detect whether the inbound message is itself an automatic reply.

    Checks the SMTP envelope MAIL FROM (bounce indicator) plus the
    Auto-Submitted, Precedence, List-Id, X-Auto-Response-Suppress,
    X-Autoreply, and X-Autorespond headers.
    """
    # A null envelope sender (MAIL FROM ``<>`` or empty) marks a bounce /
    # notification we must never reply to (RFC 3834 §2). We read the
    # authoritative SMTP envelope rather than a ``Return-Path`` header: the
    # delivering MTA never writes one on our inbound path, and any header in
    # the body is sender-forgeable. Only an explicitly-present null value
    # counts — an absent ``mail_from`` key means "no envelope info supplied"
    # (e.g. a caller that doesn't carry one), not "null sender".
    mail_from = (envelope or {}).get("mail_from")
    if mail_from is not None and mail_from.strip() in ("", "<>"):
        return True

    # Auto-Submitted (max=1 per RFC 3834 §5). Parameters after ``;``
    # (e.g. ``auto-replied; owner-email=...``) are stripped before
    # comparison; anything other than ``no`` counts.
    auto_submitted = find_header(parsed_email, "Auto-Submitted").strip().lower()
    if auto_submitted and auto_submitted.split(";", 1)[0].strip() not in ("", "no"):
        return True

    # Precedence: bulk / list / junk. Repeatable per RFC 5322
    # (optional-field).
    for precedence in find_headers(parsed_email, "Precedence"):
        if precedence.strip().lower() in _PRECEDENCE_VALUES:
            return True

    # Presence of any loop indicator header is enough (list-id,
    # list-unsubscribe, x-loop, …).
    return any(has_header(parsed_email, name) for name in _LOOP_HEADERS)


def should_send_autoreply(
    mailbox: models.Mailbox,
    parsed_email: JmapEmail,
    is_spam: bool = False,
    envelope: Optional[dict] = None,
) -> Optional[models.MessageTemplate]:
    """Determine whether we should send an autoreply and return the template.

    Returns the active autoreply MessageTemplate if all conditions pass,
    otherwise None.
    """
    # 1. Never autoreply to spam
    if is_spam:
        return None

    # 2. Skip auto-generated messages and bounces (loop prevention)
    if _is_auto_reply_message(parsed_email, envelope):
        return None

    # 3. Self-reply prevention: skip if sender == mailbox email
    sender_email = first_address_email(parsed_email.get("from")).lower()
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
    if not _is_recipient_explicit(mailbox_email, parsed_email):
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


def _create_reply_record_from_template(
    template: models.MessageTemplate,
    mailbox: models.Mailbox,
    inbound_message: models.Message,
    *,
    is_draft: bool,
    channel: Optional[models.Channel] = None,
):
    """Build the reply ``Message`` row + ``MessageRecipient`` and resolve
    the validated signature.

    Shared by the autoreply path (which then composes + DKIM-signs the
    MIME via ``compose_and_sign_mime`` and triggers an async send) and
    the webhook-driven ``reply_draft`` path (which stores the template's
    editor-format raw body as ``draft_blob`` so the user can refine the
    draft inline in the UI before sending).

    Returns ``(message, validated_signature)``. The caller is
    responsible for attaching the body blob (sent message → ``blob``,
    draft → ``draft_blob``) inside the same transaction.
    """
    # 1. Get or create a Contact for the mailbox's own email
    mailbox_email = str(mailbox)
    mailbox_contact, _ = models.Contact.objects.get_or_create(
        email=mailbox_email,
        mailbox=mailbox,
        defaults={"name": mailbox.contact.name if mailbox.contact else mailbox_email},
    )

    # 2. Build subject with Re: prefix
    subject = reply_subject(inbound_message.subject or "")[:255]

    # 3. Resolve signature: forced domain/mailbox signature takes priority
    #    over the one attached to the template
    validated_signature = mailbox.get_validated_signature(
        template.signature.id if template.signature else None
    )

    # 4. Create Message record
    message = models.Message.objects.create(
        thread=inbound_message.thread,
        sender=mailbox_contact,
        subject=subject,
        parent=inbound_message,
        sent_at=None if is_draft else timezone.now(),
        is_draft=is_draft,
        is_sender=True,
        is_trashed=False,
        is_spam=False,
        signature=validated_signature if is_draft else None,
        channel=channel,
    )

    # 5. Create MessageRecipient (must exist before compose_and_sign_mime)
    models.MessageRecipient.objects.create(
        message=message,
        contact=inbound_message.sender,
        type=MessageRecipientTypeChoices.TO,
    )

    return message, validated_signature


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

    # 1-5 + compose: atomic so a failure in compose_and_sign_mime
    # cannot leave orphan Message / Recipient rows.
    with transaction.atomic():
        message, validated_signature = _create_reply_record_from_template(
            template,
            mailbox,
            inbound_message,
            is_draft=False,
        )

        # Compose MIME, DKIM sign, and store as blob
        auto_reply_headers = [
            ("Auto-Submitted", "auto-replied"),
            ("X-Auto-Response-Suppress", "All"),
            ("Precedence", "bulk"),
        ]
        signed_mime = compose_and_sign_mime(
            message,
            mailbox,
            template.text_body,
            template.html_body,
            prepend_headers=auto_reply_headers,
            signature=validated_signature,
        )
        message.blob = models.Blob.objects.create_blob(
            content=signed_mime, content_type="message/rfc822"
        )
        message.save(update_fields=["mime_id", "blob", "has_attachments"])

    # Trigger async send (outside transaction to avoid sending before commit)
    send_message_task.delay(str(message.id))

    # Update thread stats — do NOT update read_at here: the autoreply
    # sender is away, so the thread must stay unread for them to notice
    # new messages when they return.
    inbound_message.thread.update_stats()

    logger.info(
        "Autoreply message %s created and queued for sending (mailbox=%s, to=%s)",
        message.id,
        mailbox.id,
        sender_email,
    )


def create_draft_reply_from_template(
    template: models.MessageTemplate,
    mailbox: models.Mailbox,
    inbound_message: models.Message,
    *,
    channel: Optional[models.Channel] = None,
) -> Optional[models.Message]:
    """Materialise a draft reply from ``template`` against
    ``inbound_message``. Returns the draft Message, or ``None`` if the
    inbound has no sender (no one to reply to — same skip as the
    autoreply path).

    Stores the template's editor-format ``raw_body`` JSON as
    ``draft_blob`` so the recipient mailbox can edit the draft inline
    in the rich-text editor before sending — no MIME pre-composition,
    no premature DKIM. The send-draft pipeline composes + signs at
    send time exactly as it does for hand-composed drafts.
    """
    if not inbound_message.sender or not inbound_message.sender.email:
        logger.warning(
            "Cannot create reply_draft: inbound message %s has no sender email",
            inbound_message.id,
        )
        return None

    raw_body = template.raw_body or "{}"
    raw_body_bytes = raw_body.encode("utf-8")

    with transaction.atomic():
        message, _ = _create_reply_record_from_template(
            template,
            mailbox,
            inbound_message,
            is_draft=True,
            channel=channel,
        )
        message.draft_blob = models.Blob.objects.create_blob(
            content=raw_body_bytes,
            content_type="application/json",
        )
        message.save(update_fields=["draft_blob"])

    inbound_message.thread.update_stats()

    logger.info(
        "Draft reply %s created from template %s (mailbox=%s, channel=%s)",
        message.id,
        template.id,
        mailbox.id,
        channel.id if channel else None,
    )
    return message


def try_send_autoreply(
    mailbox: models.Mailbox,
    parsed_email: JmapEmail,
    message: models.Message,
    is_spam: bool = False,
    envelope: Optional[dict] = None,
):
    """Evaluate autoreply conditions and send if appropriate.

    Safe to call from any delivery path (MTA inbound, internal delivery).
    ``envelope`` carries the SMTP envelope (see ``InboundMessage.envelope``)
    so the null-sender bounce check reads the authoritative MAIL FROM.
    Exceptions are logged but never propagated.
    """
    try:
        template = should_send_autoreply(
            mailbox, parsed_email, is_spam=is_spam, envelope=envelope
        )
        if template:
            send_autoreply_for_message(template, mailbox, message)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Autoreply failed for mailbox %s, message %s",
            mailbox.id,
            message.id,
        )
