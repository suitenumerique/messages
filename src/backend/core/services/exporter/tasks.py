"""Celery tasks for exporting mailbox messages."""

import gzip
import html
import io
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, Dict

from django.conf import settings
from django.core.files.storage import storages

from celery.utils.log import get_task_logger
from sentry_sdk import capture_exception

from core.api.utils import generate_presigned_url
from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322.parser import parse_email_message
from core.models import Label, Mailbox, Message

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)

# 7 days in seconds
PRESIGNED_URL_EXPIRATION = 7 * 24 * 60 * 60

# Multipart upload chunk size (100MB)
CHUNK_SIZE = 100 * 1024 * 1024

# Minimum S3 multipart part size (5MB, required by S3)
MIN_PART_SIZE = 5 * 1024 * 1024


class S3MultipartGzipUploader:  # pylint: disable=too-many-instance-attributes
    """
    A file-like object that streams gzip-compressed data to S3 using multipart upload.

    This allows writing large amounts of data without storing the full file on disk.
    Data is compressed with gzip, buffered, and uploaded in chunks.

    Each uploaded part is a complete gzip stream. When concatenated, they form a valid
    multi-stream gzip file that standard gzip tools can decompress.
    """

    def __init__(
        self,
        s3_client,
        bucket: str,
        key: str,
        chunk_size: int = CHUNK_SIZE,
        min_part_size: int = MIN_PART_SIZE,
    ):
        self.s3_client = s3_client
        self.bucket = bucket
        self.key = key
        self.chunk_size = chunk_size
        self.min_part_size = min_part_size

        # Start multipart upload
        response = self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            ContentType="application/gzip",
        )
        self.upload_id = response["UploadId"]
        self.parts = []
        self.part_number = 1

        # Create gzip compressor writing to an in-memory buffer
        self._buffer = io.BytesIO()
        self._gzip = gzip.GzipFile(fileobj=self._buffer, mode="wb")
        self._closed = False

    def write(self, data: bytes) -> int:
        """Write data to the gzip stream, uploading chunks as needed."""
        if self._closed:
            raise ValueError("Cannot write to closed uploader")

        self._gzip.write(data)

        # Only upload if we have enough data to ensure the last part won't be tiny.
        # We keep min_part_size as reserve, so after uploading there's still enough
        # data to form a reasonable final part (avoids e.g. 100MB + 4MB parts).
        if self._buffer.tell() >= self.chunk_size + self.min_part_size:
            self._upload_chunk()

        return len(data)

    def _upload_chunk(self):
        """Upload the current buffer as a multipart part."""
        # Close gzip to finalize this stream (writes CRC and size trailer).
        # This creates a complete gzip stream that can be concatenated with others.
        self._gzip.close()

        # Get buffer contents (always non-empty: gzip close writes header + footer)
        self._buffer.seek(0)
        chunk_data = self._buffer.read()

        # Upload part
        response = self.s3_client.upload_part(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            PartNumber=self.part_number,
            Body=chunk_data,
        )

        self.parts.append(
            {
                "PartNumber": self.part_number,
                "ETag": response["ETag"],
            }
        )
        self.part_number += 1

        # Reset buffer and create new gzip stream for next chunk
        self._buffer = io.BytesIO()
        self._gzip = gzip.GzipFile(fileobj=self._buffer, mode="wb")

    def close(self):
        """Finalize the gzip stream and complete the multipart upload."""
        if self._closed:
            return

        # Close gzip to flush final data
        self._gzip.close()

        # Get any remaining data in buffer
        self._buffer.seek(0)
        remaining_data = self._buffer.read()

        if remaining_data:
            # Upload final part
            response = self.s3_client.upload_part(
                Bucket=self.bucket,
                Key=self.key,
                UploadId=self.upload_id,
                PartNumber=self.part_number,
                Body=remaining_data,
            )
            self.parts.append(
                {
                    "PartNumber": self.part_number,
                    "ETag": response["ETag"],
                }
            )

        # Complete or abort the multipart upload
        try:
            if self.parts:
                self.s3_client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                    MultipartUpload={"Parts": self.parts},
                )
            else:
                # No data was written, abort the upload and create empty file
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                )
                # Upload empty gzip file
                empty_gzip = io.BytesIO()
                with gzip.GzipFile(fileobj=empty_gzip, mode="wb"):
                    pass
                empty_gzip.seek(0)
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=self.key,
                    Body=empty_gzip.read(),
                    ContentType="application/gzip",
                )
        except Exception:
            # Abort the multipart upload to avoid leaked parts on S3
            try:
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to abort multipart upload", exc_info=True)
            raise
        finally:
            self._closed = True

    def abort(self):
        """Abort the multipart upload in case of error."""
        if not self._closed:
            self._closed = True
            self.s3_client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=self.key,
                UploadId=self.upload_id,
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.abort()
        else:
            self.close()
        return False


# Pattern to match "From " at the start of a line (needs escaping in MBOX)
FROM_LINE_PATTERN = re.compile(rb"^From ", re.MULTILINE)


def _escape_from_lines(content: bytes) -> bytes:
    """Escape 'From ' at the start of lines by prepending '>'."""
    return FROM_LINE_PATTERN.sub(b">From ", content)


def _build_status_headers(
    is_unread: bool, is_starred: bool, is_draft: bool, is_sender: bool
) -> bytes:
    """
    Build Status and X-Status headers for mbox format.

    Status header flags:
        R - Read (seen)
        O - Old (not recent, always set for exports)

    X-Status header flags:
        A - Answered (we use is_sender as proxy for replied messages)
        F - Flagged (starred)
        T - Draft
        D - Deleted (not used)

    Args:
        is_unread: Whether the message is unread
        is_starred: Whether the message is starred/flagged
        is_draft: Whether the message is a draft
        is_sender: Whether this is a sent message (used as answered proxy)

    Returns:
        Bytes containing Status and X-Status headers
    """
    # Status: R = read, O = old (always set for exports)
    status_flags = "O"  # Old is always set for exported messages
    if not is_unread:
        status_flags = "R" + status_flags  # RO = read and old

    # X-Status: A = answered, F = flagged, T = draft, D = deleted
    x_status_flags = ""
    if is_sender:
        x_status_flags += "A"  # Answered/sent
    if is_starred:
        x_status_flags += "F"  # Flagged
    if is_draft:
        x_status_flags += "T"  # Draft

    headers = f"Status: {status_flags}\n".encode()
    if x_status_flags:
        headers += f"X-Status: {x_status_flags}\n".encode()

    return headers


def _build_keywords_header(labels: list) -> bytes:
    """
    Build X-Keywords header from a list of label names.

    Format: X-Keywords: label1, label2, "label with spaces"

    Labels containing commas or spaces are quoted.
    This format is compatible with Dovecot, OfflineIMAP, and mu4e.

    Args:
        labels: List of label name strings

    Returns:
        Bytes containing X-Keywords header, or empty bytes if no labels
    """
    if not labels:
        return b""

    formatted_labels = []
    for label in labels:
        # Quote labels that contain commas, spaces, or quotes
        if "," in label or " " in label or '"' in label:
            # Escape any quotes in the label
            escaped = label.replace('"', '\\"')
            formatted_labels.append(f'"{escaped}"')
        else:
            formatted_labels.append(label)

    return f"X-Keywords: {', '.join(formatted_labels)}\n".encode()


def _inject_headers(raw_content: bytes, extra_headers: bytes) -> bytes:
    """
    Inject extra headers at the top of raw email content.

    Headers are prepended before existing headers so they appear before
    the Received: chain, keeping chronological descending order.

    Args:
        raw_content: Raw RFC 5322 email content
        extra_headers: Headers to inject (must end with newline)

    Returns:
        Email content with injected headers
    """
    if not extra_headers:
        return raw_content

    # Detect original line ending style from the first line break
    line_end = b"\r\n" if b"\r\n" in raw_content[:256] else b"\n"

    # Normalize extra_headers line endings to match the original message
    normalized_headers = extra_headers.rstrip(b"\r\n").replace(b"\r\n", b"\n")
    if line_end == b"\r\n":
        normalized_headers = normalized_headers.replace(b"\n", b"\r\n")

    return normalized_headers + line_end + raw_content


def _create_mbox_entry(
    raw_content: bytes,
    timestamp: datetime,
    is_unread: bool = True,
    is_starred: bool = False,
    is_draft: bool = False,
    is_sender: bool = False,
    labels: list = None,
) -> bytes:
    """
    Create an MBOX entry from raw email content with metadata headers.

    Injects the following headers for compatibility with mail clients:
    - Status: R (read) O (old) - mbox standard
    - X-Status: A (answered) F (flagged) T (draft) - mbox standard
    - X-Keywords: comma-separated labels - Dovecot/OfflineIMAP/mu4e compatible

    Args:
        raw_content: Raw RFC 5322 email content
        timestamp: Timestamp for the "From " line
        is_unread: Whether the message is unread
        is_starred: Whether the message is starred/flagged
        is_draft: Whether the message is a draft
        is_sender: Whether this is a sent message
        labels: List of label names to include as X-Keywords

    Returns:
        MBOX-formatted message bytes with metadata headers
    """
    # Build extra headers for metadata
    extra_headers = _build_status_headers(is_unread, is_starred, is_draft, is_sender)
    extra_headers += _build_keywords_header(labels or [])

    # Inject metadata headers into the email content
    content_with_headers = _inject_headers(raw_content, extra_headers)

    # Format timestamp for MBOX "From " line (traditional Unix mbox format)
    # Format: "From sender@example.com Fri Dec 20 12:00:00 2024"
    # Use English day/month names regardless of server locale
    _days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    from_date = (
        f"{_days[timestamp.weekday()]} {_months[timestamp.month - 1]} "
        f"{timestamp.day:2d} {timestamp.hour:02d}:{timestamp.minute:02d}"
        f":{timestamp.second:02d} {timestamp.year}"
    )

    # Escape any "From " lines in the content
    escaped_content = _escape_from_lines(content_with_headers)

    # Build MBOX entry: "From " line + content + blank line
    # Use "-" as sender since we don't always have a reliable envelope sender
    mbox_entry = f"From - {from_date}\n".encode() + escaped_content

    # Ensure it ends with double newline (MBOX message separator)
    if not mbox_entry.endswith(b"\n\n"):
        if mbox_entry.endswith(b"\n"):
            mbox_entry += b"\n"
        else:
            mbox_entry += b"\n\n"

    return mbox_entry


@celery_app.task(bind=True)  # pylint: disable=too-many-locals
def export_mailbox_task(self, mailbox_id: str, user_id: str) -> Dict[str, Any]:  # pylint: disable=unused-argument
    """
    Export all messages from a mailbox to an MBOX file and upload to S3.

    Uses streaming multipart upload to avoid storing large files locally.

    Args:
        mailbox_id: The UUID of the mailbox to export
        user_id: The UUID of the user who triggered the export

    Returns:
        Dict with task status and result
    """
    total_messages = 0
    exported_count = 0
    skipped_count = 0
    current_message = 0
    s3_key = None

    try:
        mailbox_obj = Mailbox.objects.select_related("domain").get(id=mailbox_id)
    except Mailbox.DoesNotExist:
        error_msg = f"Mailbox {mailbox_id} not found"
        result = {
            "message_status": "Failed to export messages",
            "total_messages": 0,
            "exported_count": 0,
            "skipped_count": 0,
            "error": error_msg,
        }
        self.update_state(
            state="FAILURE",
            meta={"result": result, "error": error_msg},
        )
        return {"status": "FAILURE", "result": result, "error": error_msg}

    mailbox_email = str(mailbox_obj)

    try:
        # Update state to show we're starting
        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": "Counting messages",
                    "total_messages": 0,
                    "exported_count": 0,
                    "skipped_count": 0,
                },
                "error": None,
            },
        )

        # Query all messages in this mailbox with their threads and labels
        messages_qs = (
            Message.objects.filter(thread__accesses__mailbox_id=mailbox_id)
            .select_related("blob", "thread")
            .prefetch_related("thread__labels")
            .order_by("created_at")
            .distinct()
        )

        # Get labels for this mailbox (to filter thread labels)
        mailbox_label_ids = set(
            Label.objects.filter(mailbox_id=mailbox_id).values_list("id", flat=True)
        )

        total_messages = messages_qs.count()

        if total_messages == 0:
            logger.info("Mailbox %s has no messages to export", mailbox_id)

        # Setup S3 multipart upload
        storage = storages["message-imports"]
        s3_client = storage.connection.meta.client
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3_key = f"exports/{mailbox_id}/{timestamp}.mbox.gz"

        # Stream messages directly to S3 with gzip compression
        with S3MultipartGzipUploader(
            s3_client, storage.bucket_name, s3_key
        ) as uploader:
            for msg in messages_qs.iterator(chunk_size=1000):
                current_message += 1

                # Update progress every 100 messages to reduce overhead
                if current_message % 100 == 0 or current_message == total_messages:
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "result": {
                                "message_status": (
                                    f"Exporting message {current_message} "
                                    f"of {total_messages}"
                                ),
                                "total_messages": total_messages,
                                "exported_count": exported_count,
                                "skipped_count": skipped_count,
                                "current_message": current_message,
                            },
                            "error": None,
                        },
                    )

                # Skip messages without blobs
                if not msg.blob:
                    logger.warning("Message %s has no blob, skipping", msg.id)
                    skipped_count += 1
                    continue

                try:
                    # Get raw email content from blob
                    raw_content = msg.blob.get_content()

                    # Get labels for this message's thread (only from this mailbox)
                    thread_labels = [
                        label.name
                        for label in msg.thread.labels.all()
                        if label.id in mailbox_label_ids
                    ]

                    # Create MBOX entry with metadata and write to stream
                    mbox_entry = _create_mbox_entry(
                        raw_content,
                        msg.created_at or datetime.now(timezone.utc),
                        is_unread=msg.is_unread,
                        is_starred=msg.is_starred,
                        is_draft=msg.is_draft,
                        is_sender=msg.is_sender,
                        labels=thread_labels,
                    )
                    uploader.write(mbox_entry)
                    exported_count += 1

                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to export message %s: %s", msg.id, e)
                    skipped_count += 1

        # Generate presigned URL (7 days)
        # Use the project helper that respects AWS_S3_DOMAIN_REPLACE
        presigned_url = generate_presigned_url(
            storage,
            ClientMethod="get_object",
            Params={"Bucket": storage.bucket_name, "Key": s3_key},
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
        )

        # Create notification message
        self.update_state(
            state="PROGRESS",
            meta={
                "result": {
                    "message_status": "Creating notification",
                    "total_messages": total_messages,
                    "exported_count": exported_count,
                    "skipped_count": skipped_count,
                },
                "error": None,
            },
        )

        try:
            _create_notification_message(
                mailbox_email=mailbox_email,
                presigned_url=presigned_url,
                exported_count=exported_count,
                skipped_count=skipped_count,
                total_messages=total_messages,
            )
        except Exception as notif_exc:  # pylint: disable=broad-exception-caught
            capture_exception(notif_exc)
            logger.warning(
                "Failed to create notification for mailbox %s: %s",
                mailbox_id,
                notif_exc,
            )

        # Success — export to S3 completed regardless of notification delivery
        result = {
            "message_status": "Export completed",
            "total_messages": total_messages,
            "exported_count": exported_count,
            "skipped_count": skipped_count,
            "s3_key": s3_key,
        }

        self.update_state(
            state="SUCCESS",
            meta={"result": result, "error": None},
        )

        return {"status": "SUCCESS", "result": result, "error": None}

    except Exception as e:  # pylint: disable=broad-exception-caught
        capture_exception(e)
        error_msg = str(e)
        logger.exception(
            "Error exporting mailbox %s: %s",
            mailbox_id,
            e,
        )

        result = {
            "message_status": "Failed to export messages",
            "total_messages": total_messages,
            "exported_count": exported_count,
            "skipped_count": skipped_count,
            "error": error_msg,
        }

        self.update_state(
            state="FAILURE",
            meta={"result": result, "error": error_msg},
        )

        return {"status": "FAILURE", "result": result, "error": error_msg}


def _create_notification_message(
    mailbox_email: str,
    presigned_url: str,
    exported_count: int,
    skipped_count: int,
    total_messages: int,
) -> bool:
    """
    Create a notification message in the mailbox with the download link.

    Args:
        mailbox_email: The email address of the mailbox
        presigned_url: The presigned S3 URL for download
        exported_count: Number of messages exported
        skipped_count: Number of messages skipped
        total_messages: Total number of messages in mailbox

    Returns:
        True if message was delivered successfully, False otherwise
    """
    # Build the notification email
    msg = EmailMessage()
    msg["From"] = f"noreply@{settings.MESSAGES_TECHNICAL_DOMAIN}"
    msg["To"] = mailbox_email
    msg["Subject"] = "Your mailbox export is ready"
    msg["Date"] = format_datetime(datetime.now(timezone.utc))

    # Create message body
    body_text = f"""Your mailbox export is ready for download.

Export Summary:
- Total messages in mailbox: {total_messages}
- Messages exported: {exported_count}
- Messages skipped: {skipped_count}

Download your export here (link valid for 7 days):
{presigned_url}

This file is in MBOX format and can be imported into most email clients.
"""

    escaped_url = html.escape(presigned_url)
    body_html = f"""<html>
<body>
<h2>Your mailbox export is ready for download</h2>

<h3>Export Summary</h3>
<ul>
<li>Total messages in mailbox: {total_messages}</li>
<li>Messages exported: {exported_count}</li>
<li>Messages skipped: {skipped_count}</li>
</ul>

<p><strong><a href="{escaped_url}">Download your export</a></strong> (link valid for 7 days)</p>

<p><em>This file is in MBOX format and can be imported into most email clients.</em></p>
</body>
</html>"""

    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    # Convert to bytes for delivery
    raw_data = msg.as_bytes()

    # Parse the email
    parsed_email = parse_email_message(raw_data)

    # Deliver to the mailbox
    return deliver_inbound_message(
        recipient_email=mailbox_email,
        parsed_email=parsed_email,
        raw_data=raw_data,
        is_import=True,  # Skip spam checking
        skip_inbound_queue=True,
    )
