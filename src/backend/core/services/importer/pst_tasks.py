"""PST file import task."""

# pylint: disable=broad-exception-caught
from typing import Any, Dict

from django.conf import settings
from django.core.files.storage import storages

import pypff
from celery.utils.log import get_task_logger
from sentry_sdk import capture_exception

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox

from messages.celery_app import app as celery_app

from .pst import (
    FLAG_STATUS_FOLLOWUP,
    FOLDER_TYPE_DELETED,
    FOLDER_TYPE_DRAFTS,
    FOLDER_TYPE_INBOX,
    FOLDER_TYPE_NORMAL,
    FOLDER_TYPE_OUTBOX,
    FOLDER_TYPE_SENT,
    MSGFLAG_READ,
    MSGFLAG_UNSENT,
    build_special_folder_map,
    count_pst_messages,
    get_store_owner_email,
    sanitize_folder_name,
    walk_pst_messages,
)
from .s3_seekable import BUFFER_NONE, S3SeekableReader

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def process_pst_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process a PST file asynchronously.

    Args:
        file_key: The storage key of the PST file
        recipient_id: The UUID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    success_count = 0
    failure_count = 0
    total_messages = 0
    current_message = 0

    try:
        recipient = Mailbox.objects.get(id=recipient_id)
    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process messages",
            "total_messages": 0,
            "success_count": 0,
            "failure_count": 0,
            "type": "pst",
            "current_message": 0,
        }
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    try:
        message_imports_storage = storages["message-imports"]

        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": "Initializing import",
                    "total_messages": None,
                    "success_count": 0,
                    "failure_count": 0,
                    "type": "pst",
                    "current_message": 0,
                },
                "error": None,
            },
        )

        # Create S3 seekable reader with block-aligned LRU cache
        # for pypff's random-access B-tree traversal pattern.
        # 64 KB blocks x 2048 cache slots = 128 MB max cache.
        s3_client = message_imports_storage.connection.meta.client
        with S3SeekableReader(
            s3_client,
            message_imports_storage.bucket_name,
            file_key,
            buffer_strategy=BUFFER_NONE,
            buffer_size=64 * 1024,
            buffer_count=2048,
        ) as reader:
            # Open PST file
            pst = pypff.file()
            pst.open_file_object(reader)

            try:
                # Build special folder map and get store owner email
                special_folder_map = build_special_folder_map(pst)
                store_email = get_store_owner_email(pst)

                # Count messages
                total_messages = count_pst_messages(pst, special_folder_map)

                # Iterate messages chronologically
                for (
                    folder_type,
                    folder_path,
                    message_flags,
                    flag_status,
                    eml_bytes,
                ) in walk_pst_messages(
                    pst, special_folder_map, store_email=store_email
                ):
                    current_message += 1
                    try:
                        # Check message size limit
                        if len(eml_bytes) > settings.MAX_INCOMING_EMAIL_SIZE:
                            logger.warning(
                                "Skipping oversized message: %d bytes",
                                len(eml_bytes),
                            )
                            failure_count += 1
                            continue

                        result = {
                            "message_status": (
                                f"Processing message {current_message}"
                                f" of {total_messages}"
                            ),
                            "total_messages": total_messages,
                            "success_count": success_count,
                            "failure_count": failure_count,
                            "type": "pst",
                            "current_message": current_message,
                        }
                        self.update_state(
                            state="PROGRESS",
                            meta={
                                "result": result,
                                "error": None,
                            },
                        )

                        parsed_email = parse_email_message(eml_bytes)

                        # Compute IMAP-compatible flags from PST message flags
                        imap_flags = []
                        if message_flags & MSGFLAG_READ:
                            imap_flags.append("\\Seen")
                        if (
                            message_flags & MSGFLAG_UNSENT
                            or folder_type == FOLDER_TYPE_DRAFTS
                        ):
                            imap_flags.append("\\Draft")
                        if (
                            flag_status is not None
                            and flag_status >= FLAG_STATUS_FOLLOWUP
                        ):
                            imap_flags.append("\\Flagged")

                        # Compute IMAP-compatible labels from folder type
                        imap_labels = []
                        if folder_type == FOLDER_TYPE_SENT:
                            imap_labels.append("Sent")
                        elif folder_type == FOLDER_TYPE_DELETED:
                            imap_labels.append("Trash")
                        elif folder_type == FOLDER_TYPE_OUTBOX:
                            imap_labels.append("OUTBOX")
                        elif folder_type in (
                            FOLDER_TYPE_INBOX,
                            FOLDER_TYPE_DRAFTS,
                        ):
                            pass  # No label for inbox/drafts
                        elif folder_path:
                            imap_labels.append(sanitize_folder_name(folder_path))

                        # Subfolders of special folders also get their
                        # subfolder name as an additional label.
                        if folder_path and folder_type != FOLDER_TYPE_NORMAL:
                            imap_labels.append(sanitize_folder_name(folder_path))

                        is_sender = folder_type in (
                            FOLDER_TYPE_SENT,
                            FOLDER_TYPE_OUTBOX,
                        )

                        if deliver_inbound_message(
                            str(recipient),
                            parsed_email,
                            eml_bytes,
                            is_import=True,
                            is_import_sender=is_sender,
                            imap_labels=imap_labels,
                            imap_flags=imap_flags,
                        ):
                            success_count += 1
                        else:
                            failure_count += 1
                    except Exception as e:
                        capture_exception(e)
                        logger.exception(
                            "Error processing message from PST file for recipient %s: %s",
                            recipient_id,
                            e,
                        )
                        failure_count += 1
            finally:
                pst.close()

        result = {
            "message_status": "Completed processing messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "pst",
            "current_message": current_message,
        }

        return {
            "status": "SUCCESS",
            "result": result,
            "error": None,
        }

    except Exception as e:
        capture_exception(e)
        logger.exception(
            "Error processing PST file for recipient %s: %s",
            recipient_id,
            e,
        )
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "pst",
            "current_message": current_message,
        }
        return {
            "status": "FAILURE",
            "result": result,
            "error": "An error occurred while processing the PST file.",
        }
