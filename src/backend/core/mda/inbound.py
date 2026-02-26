"""Handles inbound email delivery logic: receiving messages and delivering to mailboxes."""

# pylint: disable=broad-exception-caught

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.utils import Error as DjangoDbError

from core import models
from core.mda.inbound_tasks import process_inbound_message_task
from core.services.importer.labels import (
    handle_duplicate_message,
)

from .inbound_create import _create_message_from_inbound

logger = logging.getLogger(__name__)


def check_local_recipient(
    email_address: str, create_if_missing: bool = False
) -> bool | models.Mailbox:
    """Check if a recipient email is locally deliverable."""

    is_deliverable = False

    try:
        local_part, domain_name = email_address.split("@", 1)
    except ValueError:
        return False  # Invalid format

    # For unit testing, we accept all emails
    if settings.MESSAGES_ACCEPT_ALL_EMAILS:
        is_deliverable = True
    # MESSAGES_TESTDOMAIN acts as a catch-all, if configured.
    elif settings.MESSAGES_TESTDOMAIN == domain_name:
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

    Returns a set of email addresses that are deliverable locally.
    An email is deliverable if:
    - MESSAGES_ACCEPT_ALL_EMAILS is True (test mode), or
    - The domain matches MESSAGES_TESTDOMAIN (catch-all), or
    - A mailbox exists for that email address
    """
    if not email_addresses:
        return set()

    # For unit testing, all emails are deliverable
    if settings.MESSAGES_ACCEPT_ALL_EMAILS:
        return set(email_addresses)

    deliverable = set()

    # Parse emails and collect unique domains
    email_parts = {}  # email -> (local_part, domain)
    domains = set()

    for email in email_addresses:
        try:
            local_part, domain = email.rsplit("@", 1)
            email_parts[email] = (local_part, domain)
            domains.add(domain)
        except ValueError:
            pass  # Invalid email format, not deliverable

    # Handle MESSAGES_TESTDOMAIN - acts as catch-all
    test_domain = settings.MESSAGES_TESTDOMAIN
    if test_domain:
        for email, (_, domain) in email_parts.items():
            if domain == test_domain:
                deliverable.add(email)
        domains.discard(test_domain)

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
    parsed_email: Dict[str, Any],
    raw_data: bytes,
    is_import: bool = False,
    is_import_sender: bool = False,
    imap_labels: Optional[List[str]] = None,
    imap_flags: Optional[List[str]] = None,
    channel: Optional[models.Channel] = None,
    skip_inbound_queue: bool = False,
) -> bool:  # Return True on success, False on failure
    """Deliver a parsed inbound email message.

    For imports (is_import=True) or when skip_inbound_queue=True, messages are created
    directly without spam checking. For regular messages, they are queued for spam
    processing via rspamd. Warning: messages imported here could be is_sender=True.

    raw_data is not parsed again, just stored as is.
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
    mime_id = parsed_email.get("messageId", parsed_email.get("message_id"))
    if mime_id:
        # Remove angle brackets if present
        if mime_id.startswith("<") and mime_id.endswith(">"):
            mime_id = mime_id[1:-1]

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

    # --- 3. Handle imports and internal messages directly, queue others for spam processing --- #
    if is_import or skip_inbound_queue:
        # Imports and internal messages bypass spam checking and create messages directly
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

        # Send autoreply for internal messages (not imports, which are historical)
        if not is_import and isinstance(result, models.Message):
            from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
                try_send_autoreply,
            )

            try_send_autoreply(mailbox, parsed_email, result)

        return result is not None

    # Regular messages: queue for spam processing
    try:
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=raw_data,
            channel=channel,
        )
        logger.info(
            "Queued inbound message %s (recipient: %s)",
            inbound_message.id,
            recipient_email,
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
