"""Self-check functionality for end-to-end mail delivery testing."""

import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from core import models
from core.mda.draft import create_draft
from core.mda.outbound import prepare_outbound_message, send_message

logger = logging.getLogger(__name__)


class SelfCheckError(Exception):
    """Exception raised when self-check fails."""


def create_and_send_draft(
    from_mailbox: models.Mailbox,
    to_emails: list,
    subject: str,
    text_body: str,
    html_body: str,
    cc_emails: list = None,
    bcc_emails: list = None,
    parent_id: Optional[str] = None,
) -> models.Message:
    """
    Create a draft and send it immediately.

    This is a convenience function that combines draft creation and sending
    for use in self-check and testing scenarios.

    Args:
        from_mailbox: The mailbox that will send the message
        to_emails: List of recipient email addresses
        subject: Subject of the message
        text_body: Plain text body of the message
        html_body: HTML body of the message
        cc_emails: List of CC recipient emails (optional)
        bcc_emails: List of BCC recipient emails (optional)
        parent_id: Optional message ID to reply to

    Returns:
        The sent message

    Raises:
        SelfCheckError: If message preparation or sending fails
    """

    # Create draft body as JSON
    draft_body = {
        "textBody": text_body,
        "htmlBody": html_body or text_body,
    }

    draft_body_json = json.dumps(draft_body, separators=(",", ":"))

    # Create the draft
    message = create_draft(
        mailbox=from_mailbox,
        subject=subject,
        draft_body=draft_body_json,
        parent_id=parent_id,
        to_emails=to_emails,
        cc_emails=cc_emails or [],
        bcc_emails=bcc_emails or [],
    )

    # Prepare the outbound message
    if not prepare_outbound_message(from_mailbox, message, text_body, html_body):
        raise SelfCheckError("Failed to prepare outbound message")

    # Send the message synchronously
    send_message(message, force_mta_out=True)

    # Check message delivery status
    recipient_status = message.recipients.first().delivery_status  # pylint: disable=no-member

    if recipient_status != models.MessageDeliveryStatusChoices.SENT:
        raise SelfCheckError("Message not delivered")

    return message


def _create_test_mailboxes(
    from_email: str, to_email: str
) -> Tuple[models.Mailbox, models.Mailbox]:
    """Create test mailboxes for FROM and TO addresses if they don't exist."""

    # Parse email addresses
    from_local, from_domain = from_email.split("@", 1)
    to_local, to_domain = to_email.split("@", 1)

    # Create or get mail domains
    from_maildomain, _ = models.MailDomain.objects.get_or_create(name=from_domain)
    to_maildomain, _ = models.MailDomain.objects.get_or_create(name=to_domain)

    # Create or get mailboxes
    from_mailbox, _ = models.Mailbox.objects.get_or_create(
        local_part=from_local,
        domain=from_maildomain,
    )

    to_mailbox, _ = models.Mailbox.objects.get_or_create(
        local_part=to_local,
        domain=to_maildomain,
    )

    # Create contacts for the mailboxes if they don't exist
    from_contact, _ = models.Contact.objects.get_or_create(
        email=from_email, mailbox=from_mailbox, defaults={"name": from_local}
    )

    to_contact, _ = models.Contact.objects.get_or_create(
        email=to_email, mailbox=to_mailbox, defaults={"name": to_local}
    )

    # Link contacts to mailboxes if not already linked
    if not from_mailbox.contact:
        from_mailbox.contact = from_contact
        from_mailbox.save(update_fields=["contact"])

    if not to_mailbox.contact:
        to_mailbox.contact = to_contact
        to_mailbox.save(update_fields=["contact"])

    return from_mailbox, to_mailbox


def _wait_for_message_reception(
    to_mailbox: models.Mailbox,
    from_email: str,
    subject: str,
    secret: str,
    timeout_seconds: int = None,
    check_interval_seconds: float = 0.1,
) -> Optional[models.Message]:
    """Wait for a message containing the secret to be received in the target mailbox."""

    if timeout_seconds is None:
        timeout_seconds = settings.MESSAGES_SELFCHECK_TIMEOUT

    start_time = time.time()
    deadline = start_time + timeout_seconds

    while time.time() < deadline:
        # Check for messages in the mailbox that contain the secret
        messages = models.Message.objects.filter(
            thread__accesses__mailbox=to_mailbox,
            sender__email=from_email,
            subject=subject,
            created_at__gte=timezone.now() - timedelta(minutes=5),
        ).select_related("thread", "sender")

        for message in messages:
            # Check if the message contains our secret
            parsed_data = message.get_parsed_data()
            text_body = parsed_data.get("textBody", [{}])[0].get("content", "")
            html_body = parsed_data.get("htmlBody", [{}])[0].get("content", "")

            if secret in text_body or secret in html_body:
                logger.info("Found received message with secret: %s", message.id)
                return message

        time.sleep(check_interval_seconds)

    return None


def _verify_message_integrity(message: models.Message, original_secret: str) -> bool:
    """Verify the integrity of a received message."""

    parsed_data = message.get_parsed_data()

    # Check that we have basic required fields
    if not parsed_data:
        logger.error("Message has no parsed data")
        return False

    # Check that the secret is present in the message body
    text_body = parsed_data.get("textBody", [{}])[0].get("content", "")
    html_body = parsed_data.get("htmlBody", [{}])[0].get("content", "")

    if original_secret not in text_body or original_secret not in html_body:
        logger.error("Secret not found in message body")
        return False

    # TODO: check added headers?

    return True


def _cleanup_test_data(message: models.Message):
    """Clean up test message and thread, but keep mailboxes."""

    # Delete the whole thread (this will also clean up the message and blobs)
    message.thread.delete()
    logger.info("Cleaned up test thread")


def run_selfcheck() -> Dict[str, Any]:
    """
    Run a complete end-to-end selfcheck of the mail delivery system.

    This function:
    1. Creates test mailboxes if they don't exist
    2. Creates a test message with a secret
    3. Sends the message via the outbound system
    4. Waits for the message to be received
    5. Verifies the integrity of the received message
    6. Cleans up test data
    7. Times all operations and returns metrics

    Returns:
        dict: Dictionary containing success status and timing metrics
    """

    result = {
        "success": False,
        "error": None,
        "send_time": None,
        "reception_time": None,
    }

    # Get configuration
    from_email = settings.MESSAGES_SELFCHECK_FROM
    to_email = settings.MESSAGES_SELFCHECK_TO
    secret = f"{settings.MESSAGES_SELFCHECK_SECRET}/{secrets.token_hex(8)}"

    received_message = None
    message = None

    # Don't do anything if FROM or TO is empty
    if not from_email or not to_email:
        logger.info(
            "MESSAGES_SELFCHECK_FROM or MESSAGES_SELFCHECK_TO is empty, skipping selfcheck"
        )
        return result

    logger.info("Starting selfcheck: %s -> %s", from_email, to_email)

    try:
        # Step 1: Create test mailboxes
        from_mailbox, to_mailbox = _create_test_mailboxes(from_email, to_email)
        logger.info("Created/found mailboxes: %s -> %s", from_mailbox, to_mailbox)

        # Step 2: Create test message content
        text_body = f"""
This is a self-check test message.

Secret: {secret}

This message was automatically generated by the Messages self-check system to verify
that the mail delivery pipeline is working correctly.

Timestamp: {timezone.now().isoformat()}
        """.strip()

        html_body = f"""
<html>
<body>
<p>This is a self-check test message.</p>
<p><strong>Secret:</strong> {secret}</p>
<p>This message was automatically generated by the Messages self-check system to verify
that the mail delivery pipeline is working correctly.</p>
<p><em>Timestamp:</em> {timezone.now().isoformat()}</p>
</body>
</html>
        """.strip()

        subject = f"Self-check test message - {secret[:8]}"

        # Step 3: Create and send message using new draft function
        start_time = time.time()

        message = create_and_send_draft(
            from_mailbox=from_mailbox,
            to_emails=[to_email],
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

        result["send_time"] = time.time() - start_time
        logger.info("Sent message: %s", message.id)

        # Step 4: Wait for message reception
        reception_start = time.time()
        received_message = _wait_for_message_reception(
            to_mailbox, from_email, subject, secret
        )
        result["reception_time"] = time.time() - reception_start

        if not received_message:
            raise SelfCheckError(
                f"Message not received within {settings.MESSAGES_SELFCHECK_TIMEOUT} seconds"
            )

        logger.info("Message received: %s", received_message.id)

        # Step 5: Verify message integrity
        if not _verify_message_integrity(received_message, secret):
            raise SelfCheckError("Message integrity verification failed")
        logger.info("Message integrity verified")

        # Set success
        result["success"] = True

        logger.info("Selfcheck completed successfully")

    except Exception as e:  # pylint: disable=broad-exception-caught
        result["success"] = False
        result["error"] = str(e)
        logger.error("Selfcheck failed: %s", e, exc_info=True)

    finally:
        if message:
            _cleanup_test_data(message)
        if received_message:
            _cleanup_test_data(received_message)
        logger.info("Cleanup completed")

    return result
