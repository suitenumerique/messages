"""Import-related tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import BytesParser
from typing import Any, Dict, List, Optional

from django.core.files.storage import storages

import magic
from celery.utils.log import get_task_logger

from core import enums
from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.mda.rfc5322.parser import parse_date
from core.models import Mailbox

from messages.celery_app import app as celery_app

from .imap import (
    IMAPConnectionManager,
    create_folder_mapping,
    get_message_numbers,
    get_selectable_folders,
    process_folder_messages,
    select_imap_folder,
)

logger = get_task_logger(__name__)


@dataclass
class MboxMessageIndex:
    """Index entry for a message in an mbox file.

    Stores byte offsets and date for sorting messages chronologically
    without loading full message content into memory.
    """

    start_byte: int
    end_byte: int
    date: Optional[datetime]


def extract_date_from_headers(raw_message: bytes) -> Optional[datetime]:
    """
    Extract Date header from raw email message.

    Only parses headers, not body - faster than full parse.
    Uses the standard library email parser with headersonly=True.

    Args:
        raw_message: Raw email bytes (headers + body)

    Returns:
        Parsed datetime (always timezone-aware in UTC) or None if parsing fails
    """
    try:
        # Find end of headers (double newline)
        header_end = raw_message.find(b"\r\n\r\n")
        if header_end == -1:
            header_end = raw_message.find(b"\n\n")

        if header_end != -1:
            headers_bytes = raw_message[: header_end + 4]  # Include separator
        else:
            # No body separator found, treat entire content as headers
            headers_bytes = raw_message

        # Parse headers only using standard library (faster than flanker)
        parser = BytesParser()
        msg = parser.parsebytes(headers_bytes, headersonly=True)

        date_str = msg.get("Date", "")
        if date_str:
            parsed = parse_date(date_str)
            if parsed:
                # Ensure timezone-aware for consistent comparison
                if parsed.tzinfo is None:
                    # Assume UTC for naive datetimes
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed
        return None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def get_s3_streaming_body(storage, file_key: str):
    """
    Get a streaming body directly from S3 without downloading the entire file.

    django-storages' storage.open() downloads the entire file to a SpooledTemporaryFile
    before returning, which causes OOM for large files. This function bypasses that
    by using boto3's get_object() directly, which returns a StreamingBody that can
    be read in chunks.

    Args:
        storage: The django-storages S3Boto3Storage instance
        file_key: The key of the file in S3

    Returns:
        A boto3 StreamingBody object that supports read(size)
    """
    # Access the underlying boto3 client from django-storages
    # S3Boto3Storage exposes the bucket and connection
    s3_client = storage.connection.meta.client
    bucket_name = storage.bucket_name

    # Handle storage location prefix if configured
    if storage.location:
        full_key = f"{storage.location}/{file_key}".lstrip("/")
    else:
        full_key = file_key

    response = s3_client.get_object(Bucket=bucket_name, Key=full_key)
    return response["Body"]


def get_s3_byte_range(storage, file_key: str, start: int, end: int) -> bytes:
    """
    Fetch a specific byte range from S3.

    Uses HTTP Range requests to fetch only the needed bytes,
    avoiding full file download. This is efficient for random access
    to large files stored in S3.

    Args:
        storage: The django-storages S3Boto3Storage instance
        file_key: The key of the file in S3
        start: Start byte offset (inclusive)
        end: End byte offset (inclusive)

    Returns:
        Bytes content of the specified range
    """
    s3_client = storage.connection.meta.client
    bucket_name = storage.bucket_name

    if storage.location:
        full_key = f"{storage.location}/{file_key}".lstrip("/")
    else:
        full_key = file_key

    response = s3_client.get_object(
        Bucket=bucket_name, Key=full_key, Range=f"bytes={start}-{end}"
    )
    body = response["Body"]
    try:
        return body.read()
    finally:
        body.close()


def index_mbox_messages(
    file,
    chunk_size: int = 1024 * 1024,
    initial_buffer: bytes = b"",
    initial_offset: int = 0,
) -> List[MboxMessageIndex]:
    """
    First pass: index all messages in an mbox file with byte offsets and dates.

    Streams through the file once, collecting only metadata needed for sorting.
    Does not load full message content into memory - only parses headers to
    extract the Date field.

    Args:
        file: File-like object to read from (supports read(size))
        chunk_size: Size of chunks to read at a time (default 1MB)
        initial_buffer: Bytes already read from the file (e.g., for MIME detection)
        initial_offset: Byte offset where initial_buffer starts in the file

    Returns:
        List of MboxMessageIndex entries in file order (not sorted by date)
    """
    indices: List[MboxMessageIndex] = []
    buffer = initial_buffer
    # Track absolute position in file (where current buffer content starts)
    buffer_start_pos = initial_offset
    message_start: Optional[int] = None
    current_headers: List[bytes] = []
    in_headers = False
    from_marker = b"From "

    def finalize_message(end_byte: int) -> None:
        """Finalize current message and add to index."""
        nonlocal message_start, current_headers, in_headers

        if message_start is not None:
            # Parse date from collected headers
            headers_bytes = b"".join(current_headers)
            date = extract_date_from_headers(headers_bytes)
            indices.append(
                MboxMessageIndex(
                    start_byte=message_start,
                    end_byte=end_byte,
                    date=date,
                )
            )

        message_start = None
        current_headers = []
        in_headers = False

    while True:
        chunk = file.read(chunk_size)
        if chunk:
            buffer += chunk

        # Process complete lines from buffer
        while b"\n" in buffer:
            newline_pos = buffer.index(b"\n")
            line = buffer[:newline_pos]
            line_with_newline = line + b"\n"

            # Calculate absolute position of this line's end (after newline)
            line_end_abs = buffer_start_pos + newline_pos + 1

            if line.startswith(from_marker):
                # Found a new message boundary
                if message_start is not None:
                    # End previous message at byte before "From " line
                    finalize_message(buffer_start_pos - 1)

                # New message content starts after the "From " line
                message_start = line_end_abs
                in_headers = True
                current_headers = []

            elif message_start is not None:
                if in_headers:
                    if line in (b"", b"\r"):
                        # Empty line marks end of headers
                        in_headers = False
                    else:
                        # Collect header lines for date extraction
                        current_headers.append(line_with_newline)

            # Advance buffer position
            buffer_start_pos = line_end_abs
            buffer = buffer[newline_pos + 1 :]

        # Exit when file is exhausted
        if not chunk:
            break

    # Handle any remaining data in buffer
    if buffer:
        if buffer.startswith(from_marker):
            # Edge case: file ends with a "From " line
            if message_start is not None:
                finalize_message(buffer_start_pos - 1)
        else:
            # Add remaining bytes to position
            buffer_start_pos += len(buffer)

    # Finalize the last message
    if message_start is not None:
        finalize_message(buffer_start_pos - 1)

    return indices


@celery_app.task(bind=True)
def process_mbox_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process a MBOX file asynchronously.

    Args:
        file_key: The storage key of the MBOX file
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
            "type": "mbox",
            "current_message": 0,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    try:
        # Get storage - we'll use direct S3 streaming to avoid OOM
        # django-storages' storage.open() downloads entire file to memory first
        message_imports_storage = storages["message-imports"]

        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": "Indexing messages",
                    "type": "mbox",
                },
                "error": None,
            },
        )

        # ===== PASS 1: Index all messages with byte offsets and dates =====
        streaming_body = get_s3_streaming_body(message_imports_storage, file_key)

        try:
            # Read first bytes for MIME type validation
            first_bytes = streaming_body.read(2048)
            content_type = magic.from_buffer(first_bytes, mime=True)
            if content_type not in enums.MBOX_SUPPORTED_MIME_TYPES:
                raise Exception(f"Expected MBOX file, got {content_type}")

            # Index all messages (collect byte offsets and dates)
            message_indices = index_mbox_messages(
                streaming_body,
                initial_buffer=first_bytes,
                initial_offset=0,
            )
        finally:
            streaming_body.close()

        total_messages = len(message_indices)

        if total_messages == 0:
            result = {
                "message_status": "No messages found in file",
                "total_messages": 0,
                "success_count": 0,
                "failure_count": 0,
                "type": "mbox",
                "current_message": 0,
            }
            self.update_state(state="SUCCESS", meta={"result": result, "error": None})
            return {"status": "SUCCESS", "result": result, "error": None}

        # Sort messages by date (oldest first) for correct threading
        # Messages without dates go to the end
        # Use a far-future UTC datetime as fallback for messages without dates
        max_date = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        message_indices.sort(key=lambda m: m.date if m.date else max_date)

        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": f"Processing {total_messages} messages",
                    "total_messages": total_messages,
                    "success_count": 0,
                    "failure_count": 0,
                    "type": "mbox",
                    "current_message": 0,
                },
                "error": None,
            },
        )

        # ===== PASS 2: Process messages in chronological order =====
        for idx, msg_index in enumerate(message_indices):
            current_message = idx + 1
            try:
                # Update task state with progress
                result = {
                    "message_status": f"Processing message {current_message}/{total_messages}",
                    "total_messages": total_messages,
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "type": "mbox",
                    "current_message": current_message,
                }
                self.update_state(
                    state="PROGRESS",
                    meta={"result": result, "error": None},
                )

                # Fetch message content via byte range request
                message_content = get_s3_byte_range(
                    message_imports_storage,
                    file_key,
                    msg_index.start_byte,
                    msg_index.end_byte,
                )

                # Parse the email message
                parsed_email = parse_email_message(message_content)

                # Deliver the message
                if deliver_inbound_message(
                    str(recipient), parsed_email, message_content, is_import=True
                ):
                    success_count += 1
                else:
                    failure_count += 1

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Error processing message %d from mbox file for recipient %s: %s",
                    current_message,
                    recipient_id,
                    e,
                )
                failure_count += 1

        result = {
            "message_status": "Completed processing messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "mbox",
            "current_message": current_message,
        }

        self.update_state(
            state="SUCCESS",
            meta={
                "result": result,
                "error": None,
            },
        )

        return {
            "status": "SUCCESS",
            "result": result,
            "error": None,
        }

    except Exception as e:
        logger.exception(
            "Error processing MBOX file for recipient %s: %s",
            recipient_id,
            e,
        )
        error_msg = str(e)
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "mbox",
            "current_message": current_message,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }


@celery_app.task(bind=True)
def import_imap_messages_task(
    self,
    imap_server: str,
    imap_port: int,
    username: str,
    password: str,
    use_ssl: bool,
    recipient_id: str,
) -> Dict[str, Any]:
    """Import messages from an IMAP server.

    Args:
        imap_server: IMAP server hostname
        imap_port: IMAP server port
        username: Email address for login
        password: Password for login
        use_ssl: Whether to use SSL
        recipient_id: ID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    success_count = 0
    failure_count = 0
    total_messages = 0
    current_message = 0

    try:
        # Get recipient mailbox
        recipient = Mailbox.objects.get(id=recipient_id)

        # Connect to IMAP server using context manager
        with IMAPConnectionManager(
            imap_server, imap_port, username, password, use_ssl
        ) as imap:
            # Get selectable folders
            selectable_folders = get_selectable_folders(imap, username, imap_server)

            # Process all folders
            folders_to_process = selectable_folders

            # Create folder mapping
            folder_mapping = create_folder_mapping(
                selectable_folders, username, imap_server
            )

            # Calculate total messages across all folders
            for folder_name in folders_to_process:
                if select_imap_folder(imap, folder_name):
                    message_list = get_message_numbers(
                        imap, folder_name, username, imap_server
                    )
                    total_messages += len(message_list)

            # Process each folder

            for folder_to_process in folders_to_process:
                display_name = folder_mapping.get(folder_to_process, folder_to_process)

                # Select folder
                if not select_imap_folder(imap, folder_to_process):
                    logger.warning(
                        "Skipping folder %s - could not select it", folder_to_process
                    )
                    continue

                # Get message numbers
                message_list = get_message_numbers(
                    imap, folder_to_process, username, imap_server
                )
                if not message_list:
                    logger.info("No messages found in folder %s", folder_to_process)
                    continue

                # Process messages in this folder
                success_count, failure_count, current_message = process_folder_messages(
                    imap_connection=imap,
                    folder=folder_to_process,
                    display_name=display_name,
                    message_list=message_list,
                    recipient=recipient,
                    username=username,
                    task_instance=self,
                    success_count=success_count,
                    failure_count=failure_count,
                    current_message=current_message,
                    total_messages=total_messages,
                )

        # Determine appropriate message status
        if len(folders_to_process) == 1:
            # If only one folder was processed, show which folder it was
            actual_folder = folders_to_process[0]
            message_status = (
                f"Completed processing messages from folder '{actual_folder}'"
            )
        else:
            message_status = "Completed processing messages from all folders"

        result = {
            "message_status": message_status,
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }

        self.update_state(
            state="SUCCESS",
            meta={"status": "SUCCESS", "result": result, "error": None},
        )

        return {"status": "SUCCESS", "result": result, "error": None}

    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process messages",
            "total_messages": 0,
            "success_count": 0,
            "failure_count": 0,
            "type": "imap",
            "current_message": 0,
        }
        self.update_state(state="FAILURE", meta={"result": result, "error": error_msg})
        return {"status": "FAILURE", "result": result, "error": error_msg}

    except Exception as e:
        logger.exception("Error in import_imap_messages_task: %s", e)

        error_msg = str(e)
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }
        self.update_state(state="FAILURE", meta={"result": result, "error": error_msg})
        return {"status": "FAILURE", "result": result, "error": error_msg}


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
            "failure_count": 0,
            "type": "eml",
            "current_message": 0,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "result": result,
            "error": error_msg,
        }

    try:
        # Update progress state
        progress_result = {
            "message_status": "Processing message 1 of 1",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 0,
            "type": "eml",
            "current_message": 1,
        }
        self.update_state(
            state="PROGRESS",
            meta={
                "result": progress_result,
                "error": None,
            },
        )

        # Get storage and read file
        message_imports_storage = storages["message-imports"]
        with message_imports_storage.open(file_key, "rb") as file:
            content_type = magic.from_buffer(file.read(2048), mime=True)
            if content_type not in enums.EML_SUPPORTED_MIME_TYPES:
                raise Exception(f"Expected EML file, got {content_type}")

            file.seek(0)
            file_content = file.read()

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
            self.update_state(
                state="SUCCESS",
                meta={
                    "result": result,
                    "error": None,
                },
            )
            return {
                "status": "SUCCESS",
                "result": result,
                "error": None,
            }

        error_msg = "Failed to deliver message"
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    except Exception as e:
        logger.exception(
            "Error processing EML file for recipient %s: %s",
            recipient_id,
            e,
        )
        error_msg = str(e)
        result = {
            "message_status": "Failed to process message",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 1,
            "type": "eml",
            "current_message": 1,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }
