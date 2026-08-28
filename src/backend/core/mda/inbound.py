"""Handles inbound email delivery logic: receiving messages and delivering to mailboxes."""

# pylint: disable=broad-exception-caught

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.utils import Error as DjangoDbError

from jmap_email import first_msgid
from jmap_email.types import JmapEmail

from core import enums, models
from core.mda.addresses import ascii_lower, normalize_domain, split_address
from core.mda.inbound_tasks import process_inbound_message_task
from core.services.importer.labels import (
    handle_duplicate_message,
)

from .inbound_create import _create_message_from_inbound

logger = logging.getLogger(__name__)


def check_local_recipient(
    email_address: str, create_if_missing: bool = False
) -> bool | models.Mailbox:
    """Check if a recipient email is locally deliverable.

    Resolution is case-insensitive: the local part is ASCII-folded and the
    domain canonicalized (see ``core.mda.addresses``), so ``John.Doe@`` and
    ``john.doe@`` reach the same mailbox — and a mailbox created here is
    created under that same folded name.
    """

    is_deliverable = False

    parts = split_address(email_address)
    if parts is None:
        return False  # Invalid format
    local_part = ascii_lower(parts[0])
    domain_name = normalize_domain(parts[1])

    # We do not host mailboxes with a non-ASCII local part, so such an address
    # is never local — even under MESSAGES_ACCEPT_ALL_EMAILS, whose
    # create-if-missing path would otherwise raise ValidationError out of
    # ``send_message`` and leave the message retrying forever.
    if not local_part.isascii():
        return False

    # For unit testing, we accept all emails
    if settings.MESSAGES_ACCEPT_ALL_EMAILS:
        is_deliverable = True
    else:
        # Check if the email address exists in the database
        is_deliverable = models.Mailbox.objects.filter(
            local_part=local_part,
            domain__name=domain_name,
        ).exists()

    if not is_deliverable:
        return False

    if create_if_missing:
        # Create a new mailbox if it doesn't exist
        maildomain, _ = models.MailDomain.objects.get_or_create(name=domain_name)
        mailbox, _ = models.Mailbox.objects.get_or_create(
            local_part=local_part,
            domain=maildomain,
        )
        return mailbox

    return True


def check_local_recipients(email_addresses: list[str]) -> set[str]:
    """
    Check which email addresses are locally deliverable (batch version).

    Returns a subset of ``email_addresses``, verbatim: MTA-in keys its RCPT
    verdict by the exact string it sent us, so the folded form used for the
    lookup must never leak into the result.

    An email is deliverable if:
    - MESSAGES_ACCEPT_ALL_EMAILS is True (test mode), or
    - A mailbox exists for that email address
    """
    if not email_addresses:
        return set()

    deliverable = set()

    # Parse emails and collect unique domains
    email_parts = {}  # email -> (local_part, domain), both folded
    domains = set()

    for email in email_addresses:
        parts = split_address(email)
        if parts is None:
            continue  # Invalid email format, not deliverable
        local_part = ascii_lower(parts[0])
        # No mailbox can have a non-ASCII local part, so such an address is
        # never local. Checked before MESSAGES_ACCEPT_ALL_EMAILS below, which
        # widens which *domains* we take and not which local parts can exist:
        # answering yes here accepts the RCPT and then fails at DATA, which
        # MTA-in maps to a 451, so the sender retries the whole envelope for
        # its full backoff window. Mirrors ``check_local_recipient``.
        if not local_part.isascii():
            continue
        domain = normalize_domain(parts[1])
        email_parts[email] = (local_part, domain)
        domains.add(domain)

    # For unit testing, every address that could name a mailbox is deliverable
    if settings.MESSAGES_ACCEPT_ALL_EMAILS:
        return set(email_parts)

    # Query all mailboxes on the relevant domains in a single query
    if domains:
        existing_mailboxes = set(
            models.Mailbox.objects.filter(domain__name__in=domains).values_list(
                "local_part", "domain__name"
            )
        )

        for email, (local_part, domain) in email_parts.items():
            if email not in deliverable and (local_part, domain) in existing_mailboxes:
                deliverable.add(email)

    return deliverable


def count_external_recipients(message) -> int:
    """
    Count recipients whose domain is NOT managed by this instance.

    Uses check_local_recipients() to efficiently batch-check all recipients.
    """
    recipient_emails = list(message.recipients.values_list("contact__email", flat=True))

    if not recipient_emails:
        return 0

    local_emails = check_local_recipients(recipient_emails)
    return len(recipient_emails) - len(local_emails)


def deliver_inbound_message(
    recipient_email: str,
    parsed_email: JmapEmail,
    raw_data: bytes,
    is_import: bool = False,
    is_import_sender: bool = False,
    imap_labels: list[str] | None = None,
    imap_flags: list[str] | None = None,
    channel: models.Channel | None = None,
    envelope: dict | None = None,
    blob: "models.Blob | None" = None,
) -> bool:  # Return True on success, False on failure
    """Deliver a parsed inbound email message.

    Imports (``is_import=True``) bypass the queue and create the message
    directly — historical bulk data, no spam check, no user webhooks.
    Warning: messages imported here could be is_sender=True.

    Everything else is queued for the inbound pipeline via
    ``process_inbound_message_task`` (spam steps + user webhooks). The bytes
    are committed to an encrypted, content-addressed ``Blob`` at ingest: the
    caller may pass an already-committed ``blob`` (internal mail reuses the
    sender's ``Message.blob``), otherwise one is created from ``raw_data``
    (external MTA / widget). Because ``create_blob`` dedups by content hash,
    a message delivered to N recipients shares ONE blob and nothing sits in
    plaintext.

    ``envelope`` is the structured SMTP/provenance record for this delivery
    (see ``InboundMessage.envelope``); its ``origin`` key is the explicit
    trust discriminator that drives ``is_internal`` — internal mail skips the
    spam steps while still firing user webhooks.
    """
    # --- 1. Find or Create Mailbox --- #
    try:
        mailbox = check_local_recipient(recipient_email, create_if_missing=True)
    except Exception as e:
        logger.exception("Error checking local recipient: %s", e)
        return False

    if not mailbox:
        logger.warning("Invalid recipient address: %s", recipient_email)
        return False

    # --- 2. Check for Duplicate Message --- #
    mime_id = first_msgid(parsed_email.get("messageId"))
    if mime_id:
        # Check if a message with this MIME ID already exists in this mailbox
        existing_message = models.Message.objects.filter(
            mime_id=mime_id, thread__accesses__mailbox=mailbox
        ).first()

        if existing_message:
            if is_import and imap_labels:
                handle_duplicate_message(
                    existing_message, parsed_email, imap_labels, imap_flags, mailbox
                )
            logger.info(
                "Skipping duplicate message %s (MIME ID: %s) in mailbox %s",
                existing_message.id,
                mime_id,
                mailbox.id,
            )
            return True  # Return success since we handled the duplicate gracefully

    # --- 3. Imports bypass the queue; everything else runs the pipeline --- #
    if is_import:
        # Historical bulk import: create the message directly, no spam
        # check and no user webhooks (autoreply is suppressed too).
        result = _create_message_from_inbound(
            recipient_email=recipient_email,
            parsed_email=parsed_email,
            raw_data=raw_data,
            mailbox=mailbox,
            is_import=is_import,
            is_import_sender=is_import_sender,
            imap_labels=imap_labels,
            imap_flags=imap_flags,
            channel=channel,
            is_spam=False,  # Bypassed messages are never marked as spam
        )
        return bool(result)

    envelope = envelope or {}
    is_internal = envelope.get("origin") == enums.InboundOrigin.INTERNAL

    # Internal mail is expected to reference the sender's already-committed
    # blob — that's the whole point (no second plaintext copy). Enforce the
    # contract so a future caller can't silently fall back to re-ingesting.
    if is_internal and blob is None:
        raise ValueError("internal delivery requires a blob")

    # External and internal messages: queue for the inbound pipeline. Commit
    # the bytes to an encrypted, content-addressed blob at ingest — internal
    # mail already carries the sender's committed blob; external/widget mail
    # is ingested here. create_blob dedups by SHA-256, so N recipients of the
    # same message end up sharing one blob.
    try:
        if blob is None:
            blob = models.Blob.objects.create_blob(
                content=raw_data,
                content_type="message/rfc822",
            )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            blob=blob,
            envelope=envelope,
            channel=channel,
        )
        logger.info(
            "Queued inbound message %s (mailbox: %s, origin: %s)",
            inbound_message.id,
            mailbox.id,
            envelope.get("origin"),
        )
        # Queue the task immediately for processing (no lag)
        process_inbound_message_task.delay(str(inbound_message.id))
        return True
    except (DjangoDbError, ValidationError) as e:
        logger.error("Failed to queue inbound message for %s: %s", recipient_email, e)
        return False
    except Exception as e:
        logger.exception(
            "Unexpected error queueing inbound message for %s: %s",
            recipient_email,
            e,
        )
        return False
