"""Mbox file import task."""

# pylint: disable=broad-exception-caught
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.files.storage import storages

from celery.utils.log import get_task_logger
from sentry_sdk import capture_exception

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.mda.rfc5322.parser import parse_date
from core.models import Mailbox

from messages.celery_app import app as celery_app

from .s3_seekable import BUFFER_CENTERED, S3SeekableReader

logger = get_task_logger(__name__)


@dataclass
class MboxMessageIndex:
    """Index entry for a single message inside an mbox file."""

    start_byte: int
    end_byte: int
    date: Optional[datetime] = None


def extract_date_from_headers(raw_message: bytes) -> Optional[datetime]:
    """Extract the Date header from raw message bytes (headers only, fast).

    Reads only until the first blank line (end of headers) to avoid
    parsing the entire message body. Handles RFC 5322 folded headers
    (continuation lines starting with whitespace).
    """
    # Find the end of headers (first blank line)
    header_end = raw_message.find(b"\r\n\r\n")
    if header_end == -1:
        header_end = raw_message.find(b"\n\n")
    if header_end == -1:
        header_end = len(raw_message)

    headers = raw_message[:header_end]

    # Unfold headers: continuation lines start with whitespace (RFC 5322 §2.2.3)
    unfolded = headers.replace(b"\r\n ", b" ").replace(b"\r\n\t", b" ")
    unfolded = unfolded.replace(b"\n ", b" ").replace(b"\n\t", b" ")

    # Parse the Date header
    for line in unfolded.split(b"\n"):
        line_str = line.decode("utf-8", errors="replace").strip()
        if line_str.lower().startswith("date:"):
            date_value = line_str[5:].strip()
            return parse_date(date_value)

    return None


def index_mbox_messages(
    file,
    chunk_size: int = 65536,
    initial_buffer: bytes = b"",
    initial_offset: int = 0,
) -> List[MboxMessageIndex]:
    """Index all messages in an mbox file by scanning for 'From ' separators.

    Returns a list of MboxMessageIndex with byte offsets and parsed dates.
    The file object must support read() and optionally seek().
    """
    indices: List[MboxMessageIndex] = []
    # We need to scan through the file finding "From " lines at line starts
    buffer = initial_buffer
    file_offset = initial_offset  # tracks where buffer starts in the file
    message_start: Optional[int] = None
    scan_pos = 0  # position within buffer to scan from

    while True:
        # Read more data if needed
        if scan_pos >= len(buffer) - 5:
            new_data = file.read(chunk_size)
            if not new_data:
                break
            # Keep unprocessed tail
            buffer = buffer[scan_pos:] + new_data
            file_offset += scan_pos
            scan_pos = 0

        # Find next newline to process line by line
        nl = buffer.find(b"\n", scan_pos)
        if nl == -1:
            # No complete line yet, read more
            new_data = file.read(chunk_size)
            if not new_data:
                break
            buffer = buffer[scan_pos:] + new_data
            file_offset += scan_pos
            scan_pos = 0
            continue

        line_start_abs = file_offset + scan_pos
        line = buffer[scan_pos : nl + 1]

        if line.startswith(b"From "):
            if message_start is not None:
                # End previous message (exclusive of this From line)
                msg_end = line_start_abs - 1
                # Read headers to extract date
                _extract_and_store_index(
                    file, indices, message_start, msg_end, buffer, file_offset
                )
            # Start new message (content begins after the "From " line)
            message_start = line_start_abs + len(line)

        scan_pos = nl + 1

    # Handle last message
    if message_start is not None:
        # Get file end position
        current_pos = file.tell()
        file.seek(0, io.SEEK_END)
        file_end = file.tell()
        total_end = file_end - 1
        # Restore position for _extract_and_store_index
        file.seek(current_pos)
        if total_end >= message_start:
            _extract_and_store_index(
                file,
                indices,
                message_start,
                total_end,
                buffer[scan_pos:] if scan_pos < len(buffer) else b"",
                file_offset + scan_pos,
            )

    return indices


def _extract_and_store_index(
    file, indices, msg_start, msg_end, buffer, buf_file_offset
):
    """Extract date from a message and add an index entry."""
    # Try to read first 2048 bytes of the message for header parsing
    header_size = min(2048, msg_end - msg_start + 1)

    # Check if the header bytes are in our buffer
    buf_start = buf_file_offset
    buf_end = buf_start + len(buffer) - 1

    if buf_start <= msg_start and msg_start + header_size - 1 <= buf_end:
        offset_in_buf = msg_start - buf_start
        header_bytes = buffer[offset_in_buf : offset_in_buf + header_size]
    else:
        # Need to seek and read
        current_pos = file.tell() if hasattr(file, "tell") else None
        try:
            file.seek(msg_start)
            header_bytes = file.read(header_size)
        finally:
            if current_pos is not None:
                file.seek(current_pos)

    date = extract_date_from_headers(header_bytes)
    indices.append(MboxMessageIndex(start_byte=msg_start, end_byte=msg_end, date=date))


@celery_app.task(bind=True)
def process_mbox_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process a MBOX file asynchronously using a 2-pass approach.

    Pass 1: Index messages with byte offsets and dates.
    Pass 2: Process messages in chronological order (oldest first).

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
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    try:
        # Get storage and create S3 seekable reader
        message_imports_storage = storages["message-imports"]
        s3_client = message_imports_storage.connection.meta.client

        with S3SeekableReader(
            s3_client,
            message_imports_storage.bucket_name,
            file_key,
            buffer_strategy=BUFFER_CENTERED,
        ) as reader:
            self.update_state(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Indexing messages",
                        "total_messages": None,
                        "success_count": 0,
                        "failure_count": 0,
                        "type": "mbox",
                        "current_message": 0,
                    },
                    "error": None,
                },
            )

            # Pass 1: Index messages
            message_indices = index_mbox_messages(reader)
            total_messages = len(message_indices)

            if total_messages == 0:
                return {
                    "status": "SUCCESS",
                    "result": {
                        "message_status": "Completed processing messages",
                        "total_messages": 0,
                        "success_count": 0,
                        "failure_count": 0,
                        "type": "mbox",
                        "current_message": 0,
                    },
                    "error": None,
                }

            # Sort by date (oldest first, messages without dates go last)
            # Normalize naive datetimes to UTC for safe comparison
            # (parsedate_to_datetime returns naive for "-0000" timezone, aware otherwise)
            _utc = timezone.utc
            _max_date = datetime.max.replace(tzinfo=_utc)
            message_indices.sort(
                key=lambda m: (
                    m.date is None,
                    m.date.replace(tzinfo=_utc)
                    if m.date and m.date.tzinfo is None
                    else (m.date or _max_date),
                )
            )

            # Pass 2: Process messages in chronological order
            for i, msg_index in enumerate(message_indices, 1):
                current_message = i
                try:
                    result = {
                        "message_status": f"Processing message {i} of {total_messages}",
                        "total_messages": total_messages,
                        "success_count": success_count,
                        "failure_count": failure_count,
                        "type": "mbox",
                        "current_message": i,
                    }
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "result": result,
                            "error": None,
                        },
                    )

                    reader.seek(msg_index.start_byte)
                    message_content = reader.read(
                        msg_index.end_byte - msg_index.start_byte + 1
                    )

                    if len(message_content) > settings.MAX_INCOMING_EMAIL_SIZE:
                        logger.warning(
                            "Skipping oversized message: %d bytes",
                            len(message_content),
                        )
                        failure_count += 1
                        continue

                    parsed_email = parse_email_message(message_content)
                    if deliver_inbound_message(
                        str(recipient), parsed_email, message_content, is_import=True
                    ):
                        success_count += 1
                    else:
                        failure_count += 1
                except Exception as e:
                    capture_exception(e)
                    logger.exception(
                        "Error processing message from mbox file for recipient %s: %s",
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

        return {
            "status": "SUCCESS",
            "result": result,
            "error": None,
        }

    except Exception as e:
        capture_exception(e)
        logger.exception(
            "Error processing MBOX file for recipient %s: %s",
            recipient_id,
            e,
        )
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "mbox",
            "current_message": current_message,
        }
        return {
            "status": "FAILURE",
            "result": result,
            "error": "An error occurred while processing the MBOX file.",
        }
