"""Message delivery and processing tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines

from django.db.models import Q
from django.utils import timezone

from celery.utils.log import get_task_logger

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.outbound import send_message
from core.mda.selfcheck import run_selfcheck

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def send_message_task(self, message_id, force_mta_out=False):
    """Send a message asynchronously.

    Args:
        message_id: The ID of the message to send
        mime_data: The MIME data dictionary
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
        message_filter_q = Q(
            is_draft=False,
            recipients__delivery_status=MessageDeliveryStatusChoices.RETRY,
        ) & (
            Q(recipients__retry_at__isnull=True)
            | Q(recipients__retry_at__lte=timezone.now())
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
                retry_filter_q = Q(
                    delivery_status=MessageDeliveryStatusChoices.RETRY
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
