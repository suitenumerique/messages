"""Mbox import: message indexing (byte-offset + date scan) and the runner.

``run_mbox`` does one resumable pass over the mbox object, delivering messages
oldest-first with a positional ``cursor`` watermark. ``index_mbox_messages`` is
the low-level scan it (and the exporter tests) build the ordered plan from.
"""

# pylint: disable=broad-exception-caught
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone

from celery.utils.log import get_task_logger
from jmap_email.parser import parse_date

from core.services.s3_seekable import BUFFER_CENTERED, S3SeekableReader

from .utils import beat, deliver, imports_storage, run_plan

logger = get_task_logger(__name__)


@dataclass
class MboxMessageIndex:
    """Index entry for a single message inside an mbox file."""

    start_byte: int
    end_byte: int
    date: datetime | None = None


def extract_date_from_headers(raw_message: bytes) -> datetime | None:
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
    on_progress: Callable[[], None] | None = None,
) -> list[MboxMessageIndex]:
    """Index all messages in an mbox file by scanning for 'From ' separators.

    Returns a list of MboxMessageIndex with byte offsets and parsed dates.
    The file object must support read() and optionally seek(). ``on_progress``
    is invoked once per chunk read — the import runner passes it to beat the
    heartbeat during this (potentially long) full-file scan.
    """
    indices: list[MboxMessageIndex] = []
    # We need to scan through the file finding "From " lines at line starts
    buffer = initial_buffer
    file_offset = initial_offset  # tracks where buffer starts in the file
    message_start: int | None = None
    scan_pos = 0  # position within buffer to scan from

    while True:
        # Read more data if needed
        if scan_pos >= len(buffer) - 5:
            new_data = file.read(chunk_size)
            if not new_data:
                break
            if on_progress:
                on_progress()
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
            if on_progress:
                on_progress()
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


def _mbox_plan(
    file_key: str, on_progress: Callable[[], None] | None = None
) -> list[dict[str, int]]:
    """Byte-range locators for every mbox message, oldest-first (deterministic
    so a resume rebuilds the identical order)."""
    storage, s3_client = imports_storage()
    with S3SeekableReader(
        s3_client, storage.bucket_name, file_key, buffer_strategy=BUFFER_CENTERED
    ) as reader:
        indices = index_mbox_messages(reader, on_progress=on_progress)
    _max = datetime.max.replace(tzinfo=dt_timezone.utc)
    indices.sort(
        key=lambda m: (
            m.date is None,
            m.date.replace(tzinfo=dt_timezone.utc)
            if m.date and m.date.tzinfo is None
            else (m.date or _max),
        )
    )
    return [{"start": i.start_byte, "end": i.end_byte} for i in indices]


def run_mbox(channel, state) -> tuple[int, int, int]:
    """Resumable mbox pass: deliver each message oldest-first from ``cursor``."""
    recipient = channel.mailbox
    file_key = (channel.settings or {})["import"]["file_key"]
    # Beat during the (potentially minutes-long) full-file index so a live run
    # keeps renewing its lock and never looks stalled to the scheduler.
    plan = _mbox_plan(file_key, on_progress=lambda: beat(channel))

    storage, s3_client = imports_storage()
    with S3SeekableReader(
        s3_client, storage.bucket_name, file_key, buffer_strategy=BUFFER_CENTERED
    ) as reader:

        def deliver_item(loc, reasons):
            reader.seek(loc["start"])
            raw = reader.read(loc["end"] - loc["start"] + 1)
            return deliver(raw, recipient, channel, reasons=reasons)

        return run_plan(channel, state, plan, deliver_item)
