"""PST file import task."""

# pylint: disable=broad-exception-caught
from typing import Any, Dict

from django.conf import settings
from django.core.files.storage import storages

import pypff
from celery.utils.log import get_task_logger
from jmap_email import parse_email

from core import enums
from core.mda.inbound import deliver_inbound_message
from core.models import Mailbox
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

from messages.celery_app import app as celery_app

from .channel import (
    get_import_channel,
    mark_finished,
    mark_started,
    update_import_state,
)
from .pst import (
    PSTFileUnreadableError,
    assert_pst_readable,
    build_special_folder_map,
    compute_pst_labels_flags,
    count_pst_messages,
    get_store_owner_email,
    walk_pst_messages,
)
from .s3_seekable import BUFFER_NONE, S3SeekableReader

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def process_pst_file_task(
    self, file_key: str, recipient_id: str, channel_id: str | None = None
) -> Dict[str, Any]:
    """
    Process a PST file asynchronously.

    Args:
        file_key: The storage key of the PST file
        recipient_id: The UUID of the recipient mailbox
        channel_id: Optional import-channel id grouping the created messages

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
        mark_finished(
            channel_id,
            status=enums.ImportStatus.FAILED.value,
            success_count=0,
            failure_count=0,
            error=error_msg,
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    channel = get_import_channel(channel_id)
    mark_started(channel_id)

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
                # Fail fast on archives whose MAPI tree is broken — otherwise
                # the traversal crashes later with an opaque AttributeError.
                assert_pst_readable(pst)

                # Build special folder map and get store owner email
                special_folder_map = build_special_folder_map(pst)
                store_email = get_store_owner_email(pst)

                # Count messages
                total_messages = count_pst_messages(pst, special_folder_map)
                update_import_state(channel_id, total_messages=total_messages)

                # Iterate messages chronologically. The deferrers batch all
                # OpenSearch indexing and thread-stats updates into a single
                # bulk task at context exit, instead of enqueuing hundreds of
                # thousands of per-row tasks that saturate Celery during
                # large imports.
                with (
                    ThreadReindexDeferrer.defer(),
                    ThreadStatsUpdateDeferrer.defer(),
                ):
                    for (
                        folder_type,
                        folder_path,
                        message_flags,
                        flag_status,
                        eml_bytes,
                    ) in walk_pst_messages(
                        pst,
                        special_folder_map,
                        store_email=store_email,
                        recipient_email=str(recipient),
                    ):
                        current_message += 1
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
                        try:
                            # Reconstruction failed upstream — already logged
                            # by walk_pst_messages; count it as a failure here
                            # so the task reports it instead of swallowing it.
                            if eml_bytes is None:
                                failure_count += 1
                                continue
                            # Check message size limit
                            if len(eml_bytes) > settings.MAX_INCOMING_EMAIL_SIZE:
                                logger.warning(
                                    "Skipping oversized message: %d bytes",
                                    len(eml_bytes),
                                )
                                failure_count += 1
                                continue

                            parsed_email = parse_email(eml_bytes)
                            if parsed_email is None:
                                logger.warning(
                                    "PST: skipping unparseable message (%d bytes)",
                                    len(eml_bytes),
                                )
                                failure_count += 1
                                continue

                            # Map PST folder/message metadata to IMAP-style
                            # labels, flags and sender (shared with the batch
                            # importer so both paths land identical messages).
                            (
                                imap_labels,
                                imap_flags,
                                is_sender,
                            ) = compute_pst_labels_flags(
                                folder_type,
                                folder_path,
                                message_flags,
                                flag_status,
                            )

                            if deliver_inbound_message(
                                str(recipient),
                                parsed_email,
                                eml_bytes,
                                is_import=True,
                                is_import_sender=is_sender,
                                imap_labels=imap_labels,
                                imap_flags=imap_flags,
                                channel=channel,
                            ):
                                success_count += 1
                            else:
                                failure_count += 1
                        except Exception as e:
                            # logger.exception routes to Sentry via the
                            # LoggingIntegration; no separate capture needed.
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

        mark_finished(
            channel_id,
            status=enums.ImportStatus.COMPLETED.value,
            success_count=success_count,
            failure_count=failure_count,
            total_messages=total_messages,
        )

        return {
            "status": "SUCCESS",
            "result": result,
            "error": None,
        }

    except PSTFileUnreadableError as e:
        logger.warning("PST file unreadable for recipient %s: %s", recipient_id, e)
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "pst",
            "current_message": current_message,
        }
        mark_finished(
            channel_id,
            status=enums.ImportStatus.FAILED.value,
            success_count=success_count,
            failure_count=failure_count,
            total_messages=total_messages,
            error=f"PST_UNREADABLE: {e}",
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": f"PST_UNREADABLE: {e}",
        }

    except Exception as e:
        # logger.exception routes to Sentry via LoggingIntegration.
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
        mark_finished(
            channel_id,
            status=enums.ImportStatus.FAILED.value,
            success_count=success_count,
            failure_count=failure_count,
            total_messages=total_messages,
            error="An error occurred while processing the PST file.",
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": "An error occurred while processing the PST file.",
        }
