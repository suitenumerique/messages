"""Message delivery and processing tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines

import logging
import math

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.outbound import send_message
from core.mda.selfcheck import run_selfcheck
from core.task_utils import cron_task, register_task, set_task_progress

logger = logging.getLogger(__name__)


@register_task(queue="outbound")
def send_message_task(message_id, force_mta_out=False, must_archive=False):
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

        set_task_progress(50, {"message": "Sending message"})

        send_message(message, force_mta_out)

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

        set_task_progress(100, {"message": "Message sent"})

        return {
            "message_id": str(message_id),
            "success": True,
        }
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in send_message_task for message %s: %s", message_id, e)
        raise


@cron_task(interval=settings.MESSAGES_SELFCHECK_INTERVAL)
@register_task(queue="outbound")
def selfcheck_task():
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
        return run_selfcheck()
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in selfcheck_task: %s", e)
        raise


@cron_task(crontab="*/5 * * * *")
# A large retry backlog is walked in one pass, one SMTP delivery at a time, so
# this needs more room than the 10-minute default.
@register_task(queue="outbound", time_limit=3600)
def retry_messages_task(message_ids=None, force_mta_out=False, batch_size=100):
    """Retry sending messages with retryable recipients (respects retry timing).

    Args:
        message_ids: Optional message IDs list to retry
        force_mta_out: Whether to force sending via MTA
        batch_size: Number of messages to process in each batch

    Returns:
        dict: A dictionary with task status and results
    """
    # Find all messages with at least one recipient ready for retry.
    # ``is_spam=False`` is a defence-in-depth filter: should any code path
    # mint an is_sender=True spam record, it must never re-enter the
    # outbound pipeline.
    #
    # ``Exists`` keeps the outer query linear at multi-million recipient
    # scale — PG short-circuits on the first matching recipient per
    # message instead of materialising a join.
    now = timezone.now()
    ready_recipients = models.MessageRecipient.objects.filter(
        message_id=OuterRef("pk"),
    ).filter(
        Q(delivery_status=MessageDeliveryStatusChoices.RETRY)
        | Q(delivery_status__isnull=True),
        Q(retry_at__isnull=True) | Q(retry_at__lte=now),
    )

    message_filter_q = Q(
        is_draft=False,
        is_sender=True,
        is_spam=False,
    ) & Exists(ready_recipients)

    if message_ids is not None:
        message_filter_q &= Q(id__in=message_ids)

    # ``sender__mailbox__domain`` is hit per message on the external-send
    # path (SPF check, DKIM verify, MTA-out envelope) in send_message.
    messages_to_process = models.Message.objects.filter(
        message_filter_q
    ).select_related("sender", "sender__mailbox__domain")
    total_messages = messages_to_process.count()

    if total_messages == 0:
        result = {
            "success": True,
            "total_messages": 0,
            "processed_messages": 0,
            "success_count": 0,
            "error_count": 0,
            "message": "No messages ready for retry",
        }
        if message_ids is not None:
            result["message_ids"] = message_ids
        return result

    # Process messages in batches
    processed_count = 0
    success_count = 0
    error_count = 0

    for index, message in enumerate(
        messages_to_process.iterator(chunk_size=batch_size)
    ):
        # Update progress for bulk operations
        if index % batch_size == 0:
            set_task_progress(
                100 * index // total_messages,
                {
                    "message": (
                        f"Batch {index // batch_size + 1} of "
                        f"{math.ceil(total_messages / batch_size)}"
                    ),
                    "processed_messages": processed_count,
                    "total_messages": total_messages,
                    "success_count": success_count,
                    "error_count": error_count,
                },
            )

        # The outer ``Exists`` filter is the gate: any message reaching
        # this loop has at least one ready recipient. ``send_message``
        # re-checks recipient state itself, so a recipient turning
        # terminal between the outer scan and this call is handled
        # there.
        try:
            send_message(message, force_mta_out=force_mta_out)
            success_count += 1
            logger.info("Successfully retried message %s", message.id)
            processed_count += 1

        except Exception as e:
            error_count += 1
            logger.exception("Failed to retry message %s: %s", message.id, e)

    # Return appropriate result format
    result = {
        "success": True,
        "total_messages": total_messages,
        "processed_messages": processed_count,
        "success_count": success_count,
        "error_count": error_count,
    }

    if message_ids is not None:
        result["message_ids"] = message_ids

    return result
