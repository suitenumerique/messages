"""Message delivery and processing tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines

import re
from typing import Any, Dict, Optional, Tuple

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

import requests
from celery.utils.log import get_task_logger

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import _create_message_from_inbound
from core.mda.outbound import send_message
from core.mda.rfc5322 import parse_email_message
from core.mda.selfcheck import run_selfcheck

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def send_message_task(self, message_id, force_mta_out=False, must_archive=False):
    """Send a message asynchronously.

    Args:
        message_id: The ID of the message to send
        force_mta_out: Whether to force sending via MTA

    Returns:
        dict: A dictionary with success status and info
    """
    try:
        message = (
            models.Message.objects.select_related("thread", "sender")
            .prefetch_related("recipients__contact")
            .get(id=message_id)
        )

        send_message(message, force_mta_out)

        # Update task state with progress information
        self.update_state(
            state="SUCCESS",
            meta={
                "status": "completed",  # TODO fetch recipients statuses
                "message_id": str(message_id),
                "success": True,
            },
        )

        # If requested, archive the whole thread after sending
        if must_archive:
            try:
                thread = message.thread
                models.Message.objects.filter(thread=thread).update(
                    is_archived=True, archived_at=timezone.now()
                )
                thread.update_stats()
            except Exception as e:
                # Not critical, just log the error
                logger.exception(
                    "Error in send_message_task when archiving thread %s after sending message %s: %s",
                    thread.id,
                    message_id,
                    e,
                )

        return {
            "message_id": str(message_id),
            "success": True,
        }
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in send_message_task for message %s: %s", message_id, e)
        self.update_state(
            state="FAILURE",
            meta={"status": "failed", "message_id": str(message_id), "error": str(e)},
        )
        raise


@celery_app.task(bind=True)
def selfcheck_task(self):
    """Run a selfcheck of the mail delivery system.

    This task performs an end-to-end test of the mail delivery pipeline:
    1. Creates test mailboxes if they don't exist
    2. Creates a test message with a secret
    3. Sends the message via the outbound system
    4. Waits for the message to be received
    5. Verifies the integrity of the received message
    6. Cleans up test data
    7. Returns timing metrics

    Returns:
        dict: A dictionary with success status, timings, and metrics
    """
    try:
        result = run_selfcheck()

        # Update task state with progress information
        self.update_state(
            state="SUCCESS",
            meta={
                "status": "completed",
                "success": result["success"],
                "send_time": result["send_time"],
                "reception_time": result["reception_time"],
            },
        )

        return result
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in selfcheck_task: %s", e)
        self.update_state(
            state="FAILURE",
            meta={"status": "failed", "error": str(e)},
        )
        raise


@celery_app.task(bind=True)
def retry_messages_task(self, message_id=None, force_mta_out=False, batch_size=100):
    """Retry sending messages with retryable recipients (respects retry timing).

    Args:
        message_id: Optional specific message ID to retry
        force_mta_out: Whether to force sending via MTA
        batch_size: Number of messages to process in each batch

    Returns:
        dict: A dictionary with task status and results
    """
    # Get messages to process
    if message_id:
        # Single message mode
        try:
            message = models.Message.objects.get(id=message_id)
        except models.Message.DoesNotExist:
            error_msg = f"Message with ID '{message_id}' does not exist"
            return {"success": False, "error": error_msg}

        if message.is_draft:
            error_msg = f"Message '{message_id}' is still a draft and cannot be sent"
            return {"success": False, "error": error_msg}

        messages_to_process = [message]
        total_messages = 1
    else:
        # Bulk mode - find all messages with retryable recipients that are ready for retry
        message_filter_q = (
            Q(
                is_draft=False,
                is_sender=True,
            )
            & (
                Q(recipients__delivery_status=MessageDeliveryStatusChoices.RETRY)
                | Q(recipients__delivery_status__isnull=True)
            )
            & (
                Q(recipients__retry_at__isnull=True)
                | Q(recipients__retry_at__lte=timezone.now())
            )
        )

        messages_to_process = list(
            models.Message.objects.filter(message_filter_q).distinct()
        )
        total_messages = len(messages_to_process)

    if total_messages == 0:
        return {
            "success": True,
            "total_messages": 0,
            "processed_messages": 0,
            "success_count": 0,
            "error_count": 0,
            "message": "No messages ready for retry",
        }

    # Process messages in batches
    processed_count = 0
    success_count = 0
    error_count = 0

    for batch_start in range(0, total_messages, batch_size):
        batch_messages = messages_to_process[batch_start : batch_start + batch_size]

        # Update progress for bulk operations
        if not message_id:
            self.update_state(
                state="PROGRESS",
                meta={
                    "current_batch": batch_start // batch_size + 1,
                    "total_batches": (total_messages + batch_size - 1) // batch_size,
                    "processed_messages": processed_count,
                    "total_messages": total_messages,
                    "success_count": success_count,
                    "error_count": error_count,
                },
            )

        for message in batch_messages:
            try:
                # Get recipients with retry status that are ready for retry
                retry_filter_q = (
                    Q(delivery_status=MessageDeliveryStatusChoices.RETRY)
                    | Q(delivery_status__isnull=True)
                ) & (Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now()))
                retry_recipients = message.recipients.filter(retry_filter_q)

                if retry_recipients.exists():
                    # Process this message
                    send_message(message, force_mta_out=force_mta_out)
                    success_count += 1
                    logger.info(
                        "Successfully retried message %s (%d recipients)",
                        message.id,
                        retry_recipients.count(),
                    )

                processed_count += 1

            except Exception as e:
                error_count += 1
                logger.exception("Failed to retry message %s: %s", message.id, e)

    # Return appropriate result format
    if message_id:
        return {
            "success": True,
            "message_id": str(message_id),
            "recipients_processed": success_count,
            "processed_messages": processed_count,
            "success_count": success_count,
            "error_count": error_count,
        }

    return {
        "success": True,
        "total_messages": total_messages,
        "processed_messages": processed_count,
        "success_count": success_count,
        "error_count": error_count,
    }


def _check_spam_with_hardcoded_rules(
    parsed_email: Dict[str, Any], spam_config: Dict[str, Any]
) -> Optional[bool]:
    """Check if a message is spam using hardcoded rules.

    Args:
        parsed_email: Parsed email message
        spam_config: Spam configuration

    Returns:
        is_spam: True if the message is spam, False otherwise. None if no rules matched.
    """
    rules = spam_config.get("rules", [])
    headers = parsed_email.get("headers", {})

    for rule in rules:
        if rule.get("header_match") or rule.get("header_match_regex"):
            # Split on first colon only, in case value contains colons
            header_match = rule.get("header_match") or rule.get("header_match_regex")
            if ":" not in header_match:
                logger.warning(
                    "Invalid header_match format (missing colon): %s", header_match
                )
                continue

            key, value = header_match.split(":", 1)
            key = key.lower().strip()
            value = value.lower().strip()

            # Get header value(s) - can be a string or list
            header_value = headers.get(key)
            if header_value is None:
                continue

            # Use headers_blocks to identify which headers to trust based on trusted_relays config.
            # Each block ends with a Received header, marking everything above it as trusted.
            # Block 0: headers before first Received (ours from MTA), ending with first Received
            # Block 1: headers between first and second Received, ending with second Received (relay 1)
            # Block 2+: headers after second Received, ending with third Received (relay 2+)
            headers_blocks = parsed_email.get("headers_blocks", [])

            # Get number of trusted relays (default: 1, meaning we trust block 0 and block 1)
            trusted_relays = spam_config.get("trusted_relays", 1)
            # Number of blocks to check: block 0 (before our Received) + trusted_relays blocks
            blocks_to_check = trusted_relays + 1

            # Check only the trusted blocks (slicing beyond list length just returns all blocks)
            # Blocks are ordered from most recent to oldest, so we want the first match (most recent)
            found_value = None
            for block in headers_blocks[:blocks_to_check]:
                if key in block:
                    block_value = block[key]
                    # Values are always lists in headers_blocks, use the first one (most recent in that block)
                    if block_value:
                        found_value = block_value[0]
                    # Break after first match since blocks are ordered most recent to oldest
                    break

            if found_value is None:
                continue
            header_value = found_value

            # Normalize header value for comparison
            if isinstance(header_value, str):
                header_value = header_value.lower().strip()
            else:
                header_value = str(header_value).lower().strip()

            if rule.get("header_match"):
                is_match = header_value == value
            elif rule.get("header_match_regex"):
                is_match = re.fullmatch(value, header_value) is not None
            else:
                raise ValueError("Invalid header match type")

            # Check if header matches
            if is_match:
                action = rule.get("action") or "spam"
                if action in ["spam", "reject"]:
                    return True
                if action in ["ham", "no action"]:
                    return False

    return None


def _check_spam_with_rspamd(
    raw_data: bytes, spam_config: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Check if a message is spam using rspamd.

    Args:
        raw_data: Raw email message bytes
        spam_config: Spam configuration

    Returns:
        Tuple of (is_spam, error_message). error_message is None on success.
    """

    spam_url = spam_config.get("rspamd_url")
    if not spam_url:
        # If rspamd is not configured, treat all messages as not spam
        logger.debug("SPAM_CONFIG.rspamd_url not configured, skipping spam check")
        return False, None

    try:
        headers = {"Content-Type": "message/rfc822"}
        spam_auth = spam_config.get("rspamd_auth")
        if spam_auth:
            headers["Authorization"] = spam_auth

        response = requests.post(
            f"{spam_url}/checkv2",
            data=raw_data,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()

        result = response.json()
        # rspamd returns action: "reject", "add header", "greylist", or "no action"
        # We consider it spam if action is "reject"
        action = result.get("action", "")
        score = result.get("score", 0.0)
        required_score = result.get("required_score", 15.0)

        is_spam = action == "reject"

        logger.info(
            "Rspamd check result: action=%s, score=%.2f, required=%.2f, is_spam=%s",
            action,
            score,
            required_score,
            is_spam,
        )

        return is_spam, None

    except requests.exceptions.RequestException as e:
        logger.exception("Error checking spam with rspamd: %s", e)
        # On error, treat as not spam to avoid blocking legitimate messages
        return False, str(e)
    except Exception as e:
        logger.exception("Unexpected error checking spam with rspamd: %s", e)
        return False, str(e)


@celery_app.task(bind=True)
def process_inbound_message_task(self, inbound_message_id: str):
    """Process an inbound message from the queue: check spam and create message.

    Args:
        inbound_message_id: The ID of the InboundMessage to process

    Returns:
        dict: A dictionary with success status and info
    """
    # Create a unique lock key for this inbound message to prevent double processing
    lock_key = f"process_inbound_message_lock:{inbound_message_id}"
    lock_timeout = 300  # 5 minutes timeout for the lock

    # Try to acquire the lock
    if not cache.add(lock_key, "locked", lock_timeout):
        logger.warning(
            "InboundMessage %s is already being processed by another worker, skipping duplicate processing",
            inbound_message_id,
        )
        return {"success": False, "error": "Message already being processed"}

    try:
        try:
            inbound_message = models.InboundMessage.objects.get(id=inbound_message_id)
        except models.InboundMessage.DoesNotExist:
            error_msg = f"InboundMessage with ID '{inbound_message_id}' does not exist"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        # Redis lock prevents concurrent processing, no need to mark as PROCESSING
        mailbox = inbound_message.mailbox
        recipient_email = str(mailbox)  # Use mailbox email as recipient_email

        # Parse the email from raw_data
        raw_data_bytes = bytes(inbound_message.raw_data)
        try:
            parsed_email = parse_email_message(raw_data_bytes)
        except Exception as e:
            error_msg = f"Failed to parse email message: {e}"
            logger.error(error_msg)
            inbound_message.error_message = error_msg
            inbound_message.save(update_fields=["error_message"])
            # Keep the message for retry
            return {"success": False, "error": error_msg}

        if not parsed_email:
            error_msg = "Failed to parse email message (returned None)"
            logger.error(error_msg)
            inbound_message.error_message = error_msg
            inbound_message.save(update_fields=["error_message"])
            # Keep the message for retry
            return {"success": False, "error": error_msg}

        # Get spam config from maildomain (includes global settings + domain-specific overrides)
        spam_config = mailbox.domain.get_spam_config()

        # If we have hardcoded rules, check them sequentially
        is_spam = _check_spam_with_hardcoded_rules(parsed_email, spam_config)

        # If no rules matched, check with rspamd
        if is_spam is None:
            is_spam, spam_check_error = _check_spam_with_rspamd(
                raw_data_bytes, spam_config
            )
            if spam_check_error:
                logger.warning(
                    "Spam check error for inbound message %s: %s (treating as not spam)",
                    inbound_message_id,
                    spam_check_error,
                )

        # Create the message using the extracted function
        success = _create_message_from_inbound(
            recipient_email=recipient_email,
            parsed_email=parsed_email,
            raw_data=raw_data_bytes,
            mailbox=mailbox,
            channel=inbound_message.channel,
            is_spam=is_spam,
        )

        if success:
            # Delete the message after successful processing
            inbound_message.delete()

            logger.info(
                "Successfully processed inbound message %s (is_spam=%s)",
                inbound_message_id,
                is_spam,
            )

            return {
                "success": True,
                "inbound_message_id": str(inbound_message_id),
                "is_spam": is_spam,
            }

        error_msg = "Failed to create message from inbound message"
        inbound_message.error_message = error_msg
        inbound_message.save(update_fields=["error_message"])
        # Keep the message for retry
        return {"success": False, "error": error_msg}

    except Exception as e:
        logger.exception(
            "Error processing inbound message %s: %s", inbound_message_id, e
        )
        if inbound_message:
            inbound_message.error_message = str(e)
            inbound_message.save(update_fields=["error_message"])
        return {"success": False, "error": str(e)}
    finally:
        # Always release the lock
        cache.delete(lock_key)


@celery_app.task(bind=True)
def process_inbound_messages_queue_task(self, batch_size: int = 10):
    """Retry processing of inbound messages that are older than 5 minutes.

    This task only handles retries for messages that may have failed or gotten stuck.
    Regular messages are processed immediately when created via process_inbound_message_task.delay().

    Args:
        batch_size: Number of messages to process in this batch

    Returns:
        dict: A dictionary with processing results
    """
    # Only retry messages older than 5 minutes
    retry_threshold = timezone.now() - timezone.timedelta(minutes=5)
    old_messages = models.InboundMessage.objects.filter(
        created_at__lt=retry_threshold
    ).order_by("created_at")[:batch_size]

    total = len(old_messages)
    if total == 0:
        return {
            "success": True,
            "processed": 0,
            "total": 0,
        }

    processed = 0
    errors = 0

    for inbound_message in old_messages:
        try:
            # Trigger async task for each old message (retry)
            process_inbound_message_task.delay(str(inbound_message.id))
            processed += 1
        except Exception as e:
            logger.exception(
                "Error queuing inbound message %s for retry: %s",
                inbound_message.id,
                e,
            )
            errors += 1

    return {
        "success": True,
        "processed": processed,
        "errors": errors,
        "total": total,
    }
