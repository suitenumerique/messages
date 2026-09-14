"""Mbox import: message indexing (byte-offset + date scan) and the runner.

``run_mbox`` does one resumable pass over the mbox object, delivering messages
oldest-first with a positional ``cursor`` watermark. ``index_mbox_messages`` is
the low-level scan it (and the exporter tests) build the ordered plan from.
"""

# pylint: disable=broad-exception-caught
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone

from celery.utils.log import get_task_logger
from jmap_email import parse_date

from core.services.s3_seekable import BUFFER_CENTERED, S3SeekableReader

from .utils import beat, deliver, imports_storage, run_plan

logger = get_task_logger(__name__)

# How far into each message the index looks for its Date header
DATE_SCAN_BYTES = 64 * 1024

# A line break followed by an empty line, LF or CRLF
BLANK_LINE_PATTERN = re.compile(rb"\r?\n\r?\n")

# An escaped "From " line: one '>' to strip, then the bare or still-escaped form
ESCAPED_FROM_LINE_PATTERN = re.compile(rb"^>(>*From )", re.MULTILINE)

# A "From " line that reads like a real postmark: a sender token (possibly
# empty), then a date: a weekday later followed by a month name and a digit,
# which covers ctime ("Mon May 26 20:18:05 2025", as Takeout, Thunderbird,
# mutt, Python's mailbox and ourselves write it) and RFC 822 dates ("Mon, 26
# May 2025"), or an ISO 8601 timestamp. Prose such as "From me Mon to Fri"
# has no month and digit after its weekday. The atomic group keeps the first
# month: a digit after a later one is also after it, and retrying each month
# would make a long line quadratic.
_WEEKDAY = rb"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_MONTH = rb"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
POSTMARK_PATTERN = re.compile(
    rb"^From (?:\S+)?[ \t]+(?:"
    + _WEEKDAY
    + rb"[ ,](?>.*?\b"
    + _MONTH
    + rb"\b).*?\d|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})"
)

# A "From " line with a sender and nothing else, as some writers put no date
# on the separator: an address, "-" or MAILER-DAEMON, never a word of prose.
# The address is matched up to its first '@' only, or each '@' of a long
# token would be tried in turn.
BARE_FROM_LINE_PATTERN = re.compile(
    rb"^From (?:[^\s@]*@\S*|-|MAILER-DAEMON)[ \t]*\r?\n?$"
)

# A longer line is never a separator (one is a sender and a date), and only
# its first bytes are read: enough to tell whether it is a header, without
# buffering a line of any length.
SEPARATOR_MAX_LENGTH = 1000

# A header field name (RFC 5322 §3.6.8: printable US-ASCII except the colon
# itself) followed by its colon. The body may start right after the colon
# ("Subject:x"), but not with "//", or "https://example.org" would match.
HEADER_LINE_PATTERN = re.compile(rb"^[\x21-\x39\x3b-\x7e]+:(?!//)")


def unescape_from_lines(content: bytes) -> bytes:
    """Reverse the mbox "From " escaping: strip exactly one leading '>'.

    Writers escape body lines starting with "From " so they cannot be taken
    for a message separator. Stripping one '>' restores the original bytes of
    anything we exported (mboxrd, see the exporter), and restores the common
    case of an mboxo file such as Google Takeout's.

    The mboxo ambiguity stays: in a file from an mboxo writer, a line the
    author really did start with ">From " is indistinguishable from an escaped
    "From " and loses its '>'. Not unescaping at all is worse, since then
    every escaped line keeps a '>' it never had, which breaks bodies and DKIM
    signatures alike.
    """
    return ESCAPED_FROM_LINE_PATTERN.sub(rb"\1", content)


def strip_message_separator(content: bytes) -> bytes:
    """Drop the blank line mbox writers put between messages.

    A message's byte range runs up to the line before the next "From ", so it
    ends with that blank line; left in, every message gains an empty line at
    the end of its body on import. Only stripped when the content ends with a
    blank line. In a file written without separator lines, that blank line
    was the body's own and is lost: the two cases cannot be told apart.
    """
    if content.endswith(b"\r\n\r\n"):
        return content[:-2]
    if content.endswith(b"\n\n"):
        return content[:-1]
    return content


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
    # Find the end of headers: the first blank line, LF or CRLF. Looking for
    # CRLF first would land in the body of an LF message with a CRLF pair.
    blank_line = BLANK_LINE_PATTERN.search(raw_message)
    header_end = blank_line.start() if blank_line else len(raw_message)

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

    A body line beginning with "From " that the writer failed to escape would
    otherwise split one message in two, silently, and the fragment is
    delivered as its own message: the oldest bug in the format (Mozilla bug
    355237). So a "From " line is only a separator when (see _is_separator)
    it is followed by a header line and:

    - it stands where one normally stands (start of file, or right after an
      empty line), and reads like a postmark or is a bare "From <sender>";
    - or it stands anywhere else and reads like a postmark: some writers put
      no empty line between messages, and rejecting those would merge the
      next message into this one.

    The first separator is the exception: standing where one normally stands,
    a postmark needs no header after it, since there is no message yet to
    split and rejecting it would drop its message.

    A line over SEPARATOR_MAX_LENGTH bytes (line ending included) is never a
    separator, and only its head is kept, so memory stays bounded and time
    linear whatever the line lengths.
    """
    indices: list[MboxMessageIndex] = []
    # We need to scan through the file finding "From " lines at line starts
    buffer = initial_buffer
    file_offset = initial_offset  # tracks where buffer starts in the file
    message_start: int | None = None
    scan_pos = 0  # position within buffer to scan from
    # A separator normally follows an empty line; the start of the file counts
    prev_line_empty = True
    # A candidate separator waiting on the next line to confirm it:
    # (line_start, content_start, the From line itself, after an empty line)
    pending: tuple[int, int, bytes, bool] | None = None
    rejected = 0
    # Past the first SEPARATOR_MAX_LENGTH bytes of a longer line
    skipping = False

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

        if skipping:
            nl = buffer.find(b"\n", scan_pos)
            if nl == -1:
                # Nothing left to look at in the buffer: let the refill drop it
                scan_pos = len(buffer)
            else:
                scan_pos = nl + 1
                skipping = False
            continue

        # Find next newline to process line by line
        nl = buffer.find(b"\n", scan_pos, scan_pos + SEPARATOR_MAX_LENGTH)
        if nl != -1:
            line = buffer[scan_pos : nl + 1]
        elif len(buffer) - scan_pos >= SEPARATOR_MAX_LENGTH:
            # Longer than any separator: its head is all that matters
            line = buffer[scan_pos : scan_pos + SEPARATOR_MAX_LENGTH]
            skipping = True
        else:
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

        if pending is not None:
            # This line is the one right after a candidate separator
            if _is_separator(
                pending[2],
                line,
                after_empty_line=pending[3],
                first=message_start is None,
            ):
                if message_start is not None:
                    # End previous message (exclusive of the From line)
                    _extract_and_store_index(
                        file,
                        indices,
                        message_start,
                        pending[0] - 1,
                        buffer,
                        file_offset,
                    )
                # Start new message (content begins after the "From " line)
                message_start = pending[1]
            else:
                rejected += 1
            pending = None

        if line.startswith(b"From "):
            if not skipping and (prev_line_empty or POSTMARK_PATTERN.match(line)):
                pending = (
                    line_start_abs,
                    line_start_abs + len(line),
                    line,
                    prev_line_empty,
                )
            else:
                rejected += 1

        prev_line_empty = line in (b"\n", b"\r\n")
        scan_pos += len(line)

    # A candidate on the last complete line: the loop stopped before whatever
    # follows it (a last line without newline, or the few bytes it leaves), so
    # confirm it against that, as the loop would have. With nothing after it,
    # no message starts there and the line stays in the one it ends.
    if pending is not None:
        tail = buffer[scan_pos:]
        tail_nl = tail.find(b"\n")
        next_line = tail[: tail_nl + 1] if tail_nl != -1 else tail
        if _is_separator(
            pending[2],
            next_line,
            after_empty_line=pending[3],
            first=message_start is None,
        ):
            if message_start is not None:
                _extract_and_store_index(
                    file, indices, message_start, pending[0] - 1, buffer, file_offset
                )
            message_start = pending[1]
        else:
            rejected += 1

    if rejected:
        # One line, not one per occurrence: a body with many "From " lines
        # would otherwise flood the log.
        logger.warning(
            "mbox index: ignored %d 'From ' line(s) that do not look like a "
            "message separator; the writer most likely failed to escape them",
            rejected,
        )

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


def _is_separator(
    from_line: bytes, next_line: bytes, *, after_empty_line: bool, first: bool
):
    """Whether a "From " line is a message separator, given the line after it.

    A message starts with a header, so the next line must be one. After an
    empty line, the "From " line must be a postmark or a bare
    "From <sender>". Anywhere else it must be a postmark.

    The ``first`` separator, before any message, has no body to split, and
    rejecting it would drop its message: after an empty line (or at the start
    of the file), a postmark is enough there, with any non-empty line after.
    """
    if not next_line.strip():
        return False
    if HEADER_LINE_PATTERN.match(next_line):
        if after_empty_line and BARE_FROM_LINE_PATTERN.match(from_line):
            return True
    elif not (first and after_empty_line):
        return False
    return bool(POSTMARK_PATTERN.match(from_line))


def _extract_and_store_index(
    file, indices, msg_start, msg_end, buffer, buf_file_offset
):
    """Extract date from a message and add an index entry."""
    # Read the start of the message for header parsing: long Received / DKIM
    # chains and the label headers of our own exports come before the Date
    header_size = min(DATE_SCAN_BYTES, msg_end - msg_start + 1)

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
            return deliver(
                unescape_from_lines(strip_message_separator(raw)),
                recipient,
                channel,
                reasons=reasons,
            )

        return run_plan(channel, state, plan, deliver_item)
