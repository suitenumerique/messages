"""EML file import task."""

# pylint: disable=broad-exception-caught
from typing import Any, Dict

from django.conf import settings
from django.core.files.storage import storages

from celery.utils.log import get_task_logger
from sentry_sdk import capture_exception

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def process_eml_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process an EML file asynchronously.

    Args:
        file_key: The storage key of the EML file
        recipient_id: The UUID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    try:
        recipient = Mailbox.objects.get(id=recipient_id)
    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process message",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 1,
            "type": "eml",
            "current_message": 0,
        }
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    try:
        # Update progress state
        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": "Processing message 1 of 1",
                    "total_messages": 1,
                    "success_count": 0,
                    "failure_count": 0,
                    "type": "eml",
                    "current_message": 1,
                },
                "error": None,
            },
        )

        # Get storage and read file
        message_imports_storage = storages["message-imports"]
        with message_imports_storage.open(file_key, "rb") as file:
            file_content = file.read()

        # Check message size limit
        if len(file_content) > settings.MAX_INCOMING_EMAIL_SIZE:
            error_msg = f"File too large: {len(file_content)} bytes"
            logger.warning("Skipping oversized EML file: %d bytes", len(file_content))
            result = {
                "message_status": "Failed to process message",
                "total_messages": 1,
                "success_count": 0,
                "failure_count": 1,
                "type": "eml",
                "current_message": 1,
            }
            return {
                "status": "FAILURE",
                "result": result,
                "error": error_msg,
            }

        # Parse the email message
        parsed_email = parse_email_message(file_content)
        # Deliver the message
        success = deliver_inbound_message(
            str(recipient), parsed_email, file_content, is_import=True
        )

        result = {
            "message_status": "Completed processing message",
            "total_messages": 1,
            "success_count": 1 if success else 0,
            "failure_count": 0 if success else 1,
            "type": "eml",
            "current_message": 1,
        }

        if success:
            return {
                "status": "SUCCESS",
                "result": result,
                "error": None,
            }

        return {
            "status": "FAILURE",
            "result": result,
            "error": "Failed to deliver message",
        }

    except Exception as e:
        capture_exception(e)
        logger.exception(
            "Error processing EML file for recipient %s: %s",
            recipient_id,
            e,
        )
        result = {
            "message_status": "Failed to process message",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 1,
            "type": "eml",
            "current_message": 1,
        }
        return {
            "status": "FAILURE",
            "result": result,
            "error": "An error occurred while processing the EML file.",
        }
