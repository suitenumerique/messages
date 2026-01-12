"""
RFC5322 email parser using Flanker library.

This module provides functions for parsing email addresses and messages
according to RFC5322 standards. It uses the Flanker library for robust
parsing and is intended to be the central place for all email parsing
operations in the application.
"""

import base64
import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime
from datetime import timezone as dt_timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from ntpath import basename as nt_basename
from posixpath import basename as posix_basename
from typing import Any, Dict, List, Optional, Tuple

from flanker.addresslib import address
from flanker.mime import create

logger = logging.getLogger(__name__)


class EmailParseError(Exception):
    """Exception raised for errors during email parsing."""


def decode_email_header_text(header_text: str) -> str:
    """
    Decode email header text that might be encoded (RFC 2047).
    """
    if not header_text:
        return ""

    # Ensure input is a string
    header_text_str = str(header_text)
    # Use decode_header which returns a list of (decoded_string, charset) pairs
    # charset is None if the part was not encoded
    decoded_parts = decode_header(header_text_str)

    result_parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            # Decode bytes using charset or fallbacks
            if not charset or charset == "unknown-8bit":
                try:
                    result_parts.append(part.decode("utf-8", errors="replace"))
                except UnicodeDecodeError:
                    result_parts.append(part.decode("latin-1", errors="replace"))
            else:
                try:
                    result_parts.append(part.decode(charset, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    result_parts.append(part.decode("utf-8", errors="replace"))
        else:
            # Part is already a string
            result_parts.append(part)

    # Join the decoded parts first.
    full_result = "".join(result_parts)
    # Now, replace folding whitespace (CRLF followed by space/tab) with a single space.
    cleaned_result = re.sub(r"\r\n[ \t]+", " ", full_result)
    # Finally, collapse any multiple spaces into one.
    return " ".join(cleaned_result.split())


def parse_email_address(address_str: str) -> Tuple[str, str]:
    """
    Parse an email address that might include a display name.

    Args:
        address_str: String containing an email address, possibly with display name

    Returns:
        Tuple of (display_name, email_address)

    Examples:
        >>> parse_email_address('user@example.com')
        ('', 'user@example.com')
        >>> parse_email_address('User <user@example.com>')
        ('User', 'user@example.com')
    """
    if not address_str:
        return "", ""

    # Use flanker to parse the address
    parsed = address.parse(address_str)

    if parsed is None:
        return "", address_str.strip()

    # If parsed successfully, extract name and address
    return parsed.display_name or "", parsed.address  # pylint: disable=no-member


def parse_email_addresses(addresses_str: str) -> List[Tuple[str, str]]:
    """
    Parse multiple email addresses from a comma-separated string.

    Args:
        addresses_str: Comma-separated string of email addresses

    Returns:
        List of tuples, each containing (display_name, email_address)
    """
    if not addresses_str:
        return []

    # Use flanker to parse the address list
    parsed = address.parse_list(addresses_str)

    if parsed is None:
        return []

    # Extract name and address for each parsed address
    return [(addr.display_name or "", addr.address) for addr in parsed]  # pylint: disable=no-member


def parse_date(date_str: str) -> Optional[datetime]:
    """
    Parse date string from email header.

    Args:
        date_str: Date string in RFC5322 format

    Returns:
        Datetime object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        # Use email.utils which handles RFC5322 date formats
        return parsedate_to_datetime(date_str)
    except (TypeError, ValueError) as e:  # Catch specific errors
        logger.warning("Could not parse date string '%s': %s", date_str, e)
        return None


def _infer_filename_from_content_type(content_type: str) -> str:
    """
    Infer a filename with extension from a MIME content type.
    Uses the most commonly used file extensions for each MIME type.

    Args:
        content_type: MIME type string (e.g., "image/png", "application/pdf")

    Returns:
        Filename with appropriate extension (e.g., "unnamed.png", "unnamed.pdf")
    """
    extension_map = {
        "text/plain": ".txt",
        "text/html": ".html",
        "text/csv": ".csv",
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "application/json": ".json",
        "application/xml": ".xml",
        "application/zip": ".zip",
    }
    ext = extension_map.get(content_type, "")
    return f"unnamed{ext}"


def _sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize an attachment filename, preserving the extension when truncating."""

    filename = nt_basename(posix_basename(filename))

    filename = filename.strip('"/.\\')

    # Remove null bytes and control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)

    # Remove dangerous characters
    filename = re.sub(r'[<>:"|?*\\/]', "_", filename)

    # Truncate while preserving extension
    if len(filename) > max_length:
        # Find the last dot for extension (but not at the start like .gitignore)
        last_dot = filename.rfind(".")
        if last_dot > 0:
            name = filename[:last_dot]
            ext = filename[last_dot:]
            # Only preserve extension if it's reasonable length (up to 10 chars including dot)
            if len(ext) <= 10:
                max_name_length = max_length - len(ext)
                if max_name_length > 0:
                    return name[:max_name_length] + ext
        return filename[:max_length]

    return filename


def _build_attachment_dict(
    body: Any,
    part_type: str,
    filename: str,
    disposition: str,
    content_id: Optional[str],
) -> Dict[str, Any]:
    """
    Helper function to build an attachment dictionary.
    Converts body to bytes, computes SHA-256 hash, and constructs the attachment dict.

    Args:
        body: The part body (str or bytes)
        part_type: MIME type of the part
        filename: Name of the attachment file
        disposition: Content-Disposition value ("attachment", "inline", etc.)
        content_id: Content-ID if present

    Returns:
        Dictionary representing the attachment
    """
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body

    content_hash = hashlib.sha256(body_bytes).hexdigest()

    return {
        "type": part_type,
        "name": _sanitize_filename(filename) or "unnamed",
        "size": len(body_bytes),
        "disposition": disposition,
        "cid": content_id,
        "content": body_bytes,
        "sha256": content_hash,
    }


def _is_inline_media_type(content_type: str) -> bool:
    """
    Check if the content type is an inline media type (image/*, audio/*, video/*).

    Args:
        content_type: MIME type string (e.g., "image/png", "audio/mp3")

    Returns:
        True if the type is an inline media type
    """
    return (
        content_type.startswith("image/")
        or content_type.startswith("audio/")
        or content_type.startswith("video/")
    )


def _get_part_info(part) -> Dict[str, Any]:
    """
    Extract relevant information from a MIME part for classification.

    Args:
        part: A Flanker MIME part

    Returns:
        Dictionary with type, disposition, name, body, content_id, part_id
    """
    if not hasattr(part, "content_type") or not part.content_type:
        return {"type": "text/plain", "disposition": None, "name": None, "body": None}

    content_type_obj = part.content_type
    part_type = f"{content_type_obj.main}/{content_type_obj.sub}"

    # Get disposition
    disposition = None
    disposition_info = getattr(part, "content_disposition", None)
    if disposition_info and isinstance(disposition_info, tuple) and disposition_info[0]:
        disposition = disposition_info[0].lower()

    # Get filename from disposition or content-type params
    filename = None
    if (
        disposition_info
        and isinstance(disposition_info, tuple)
        and len(disposition_info) > 1
    ):
        params = disposition_info[1]
        if isinstance(params, dict):
            filename_raw = params.get("filename")
            if filename_raw:
                filename = decode_email_header_text(str(filename_raw).strip())

    if not filename and hasattr(content_type_obj, "params"):
        filename_param = content_type_obj.params.get("name")
        if filename_param:
            filename = decode_email_header_text(filename_param.strip())

    # Get Content-ID
    headers_dict = getattr(part, "headers", {})
    content_id_header = headers_dict.get("Content-ID")
    content_id = str(content_id_header).strip("<>") if content_id_header else None

    # Get body
    body = getattr(part, "body", None)

    # Get part ID
    part_id = getattr(part, "message_id", "") or ""

    return {
        "type": part_type,
        "disposition": disposition,
        "name": filename,
        "body": body,
        "content_id": content_id,
        "part_id": part_id,
    }


def _build_body_part_dict(part_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a body part dictionary for textBody/htmlBody arrays.

    Args:
        part_info: Dictionary from _get_part_info

    Returns:
        Dictionary with partId, type, content
    """
    body = part_info["body"]
    part_type = part_info["type"]

    # Binary types (images, audio, video) need base64 encoding for JSON transport
    if _is_inline_media_type(part_type):
        if body is None:
            content = ""
        elif isinstance(body, bytes):
            content = base64.b64encode(body).decode("ascii")
        else:
            # Already a string (unlikely for binary), encode it
            content = base64.b64encode(body.encode("latin-1")).decode("ascii")
    # Text types - decode as UTF-8
    elif body is not None and not isinstance(body, str):
        content = body.decode("utf-8", errors="replace")
    else:
        content = body or ""

    return {
        "partId": part_info["part_id"],
        "type": part_type,
        "content": content,
    }


def _build_attachment_from_part_info(
    part_info: Dict[str, Any], disposition_override: str = "attachment"
) -> Dict[str, Any]:
    """
    Build an attachment dictionary from part info.

    Args:
        part_info: Dictionary from _get_part_info
        disposition_override: Disposition to use if not set

    Returns:
        Dictionary representing the attachment
    """
    disposition = part_info["disposition"] or disposition_override
    filename = part_info["name"] or _infer_filename_from_content_type(part_info["type"])

    return _build_attachment_dict(
        part_info["body"] or b"",
        part_info["type"],
        filename,
        disposition,
        part_info["content_id"],
    )


def _parse_body_structure(
    parts: List,
    multipart_type: str,
    in_alternative: bool,
    html_body: Optional[List],
    text_body: Optional[List],
    attachments: List,
) -> None:
    """
    Recursively parse MIME structure following JMAP spec algorithm (Section 4.1).

    This implements the parseStructure algorithm from the JMAP specification,
    with a modification: inline media types are NOT added to attachments when
    one of textBody/htmlBody is null (unlike the spec example).

    Args:
        parts: List of MIME parts to process
        multipart_type: Type of parent multipart (mixed/alternative/related)
        in_alternative: Whether we're inside a multipart/alternative
        html_body: List to append HTML body parts (or None if nullified)
        text_body: List to append text body parts (or None if nullified)
        attachments: List to append attachment parts
    """
    # Track lengths for multipart/alternative fallback
    text_length = len(text_body) if text_body is not None else -1
    html_length = len(html_body) if html_body is not None else -1

    for i, part in enumerate(parts):
        if not hasattr(part, "content_type") or not part.content_type:
            continue

        content_type_obj = part.content_type
        part_type = f"{content_type_obj.main}/{content_type_obj.sub}"
        is_multipart = content_type_obj.is_multipart()

        # Get part info for classification
        part_info = _get_part_info(part)

        # Determine if this is an inline body part (not attachment)
        # Per JMAP spec: disposition != "attachment" AND
        # (type is text/plain OR text/html OR inline media) AND
        # (first part OR (not in related AND (is inline media OR no filename)))
        is_inline = (
            part_info["disposition"] != "attachment"
            and (
                part_type in {"text/plain", "text/html"}
                or _is_inline_media_type(part_type)
            )
            and (
                i == 0
                or (
                    multipart_type != "related"
                    and (_is_inline_media_type(part_type) or not part_info["name"])
                )
            )
        )

        if is_multipart:
            # Recurse into multipart
            sub_multipart_type = content_type_obj.sub  # e.g., "alternative", "related"
            sub_parts = getattr(part, "parts", []) or []
            _parse_body_structure(
                sub_parts,
                sub_multipart_type,
                in_alternative or sub_multipart_type == "alternative",
                html_body,
                text_body,
                attachments,
            )

        elif is_inline:
            # Handle inline parts based on context
            if multipart_type == "alternative":
                # In direct alternative: route based on type only
                if part_type == "text/plain":
                    if text_body is not None:
                        text_body.append(_build_body_part_dict(part_info))
                elif part_type == "text/html":
                    if html_body is not None:
                        html_body.append(_build_body_part_dict(part_info))
                else:
                    # Other types in alternative go to attachments
                    attachments.append(_build_attachment_from_part_info(part_info))
                continue

            # Outside alternative but within an alternative ancestor
            if in_alternative:
                # text/plain nullifies htmlBody locally
                if part_type == "text/plain":
                    html_body = None
                # text/html nullifies textBody locally
                if part_type == "text/html":
                    text_body = None

            # Push to both arrays if not nullified
            if text_body is not None:
                text_body.append(_build_body_part_dict(part_info))
            if html_body is not None:
                html_body.append(_build_body_part_dict(part_info))

            # NOTE: We intentionally skip the JMAP spec's condition:
            # if ((!textBody || !htmlBody) && isInlineMediaType) attachments.push(part)
            # This is our modification to not duplicate inline media in attachments

        else:
            # Non-inline parts go to attachments
            attachments.append(_build_attachment_from_part_info(part_info))

    # Handle multipart/alternative fallback:
    # If only one type was found, copy to the other array
    if (
        multipart_type == "alternative"
        and text_body is not None
        and html_body is not None
    ):
        # Found HTML part only - copy to textBody
        if text_length == len(text_body) and html_length != len(html_body):
            for j in range(html_length, len(html_body)):
                text_body.append(html_body[j])
        # Found text part only - copy to htmlBody
        if html_length == len(html_body) and text_length != len(text_body):
            for j in range(text_length, len(text_body)):
                html_body.append(text_body[j])


def parse_message_content(message) -> Dict[str, Any]:
    """
    Extract text, HTML, and attachments from a message, following JMAP format.

    This uses the JMAP spec's parseStructure algorithm (Section 4.1) to properly
    handle multipart structures including alternative, related, and mixed.

    Key behavior:
    - text/plain parts go to textBody
    - text/html parts go to htmlBody
    - Inline media (images, audio, video) go to textBody/htmlBody, NOT attachments
    - Explicit attachments (Content-Disposition: attachment) go to attachments
    - Parts in multipart/related after the first go to attachments

    Args:
        message: A Flanker MIME message object

    Returns:
        Dictionary with textBody, htmlBody, and attachments arrays
    """
    result = {"textBody": [], "htmlBody": [], "attachments": []}

    # Handle invalid message structure
    if not hasattr(message, "content_type") or not message.content_type:
        if hasattr(message, "body") and isinstance(message.body, str):
            result["textBody"].append(
                {"partId": "", "type": "text/plain", "content": message.body}
            )
        return result

    try:
        # Use the JMAP-style recursive parser
        # Wrap the message in a list and treat it as if inside multipart/mixed
        _parse_body_structure(
            [message],
            "mixed",
            False,
            result["htmlBody"],
            result["textBody"],
            result["attachments"],
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error parsing message body structure: %s", e, exc_info=True)

    return result


def parse_email_message(raw_email_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse a raw email message (bytes) into a structured dictionary following JMAP format.

    Args:
        raw_email_bytes: Raw email data as bytes

    Returns:
        Dictionary containing parsed email data, or None if parsing fails fundamentally.

    Raises:
        EmailParseError: If parsing fails with a specific error we want to propagate.
    """
    if not raw_email_bytes or not isinstance(raw_email_bytes, bytes):
        # Ensure input is non-empty bytes
        logger.warning(
            "Invalid input provided to parse_email_message: type=%s",
            type(raw_email_bytes),
        )
        raise EmailParseError("Input must be non-empty bytes.")

    try:
        # Parse with flanker directly from bytes
        message = create.from_string(raw_email_bytes)

        if message is None or not hasattr(message, "headers"):
            logger.warning(
                "Flanker failed to parse email data into a valid message object. Input length: %d",
                len(raw_email_bytes),
            )
            raise EmailParseError(
                "Flanker could not parse the input into a valid email message."
            )

        # Extract all headers, normalizing keys to lowercase
        headers = {}
        # Also extract headers in order for position-based filtering (e.g., spam checks)
        # Flanker's message.headers.items() preserves the order from the raw email
        headers_list = []

        for k, v in message.headers.items():
            decoded_value = decode_email_header_text(v)
            key_lower = k.lower()
            headers_list.append((key_lower, decoded_value))

            # Build headers dict (for compatibility)
            if key_lower in headers:
                current_value = headers[key_lower]
                if isinstance(current_value, list):
                    current_value.append(decoded_value)
                else:
                    headers[key_lower] = [current_value, decoded_value]
            else:
                headers[key_lower] = decoded_value

        # Split headers into blocks based on Received headers
        # Each Received header marks the END of its block - everything above it (before it in the list) is trusted
        # All values in blocks are stored as lists for consistency
        headers_blocks = []
        current_block = defaultdict(list)

        for header_name, header_value in headers_list:
            if header_name == "received":
                # Received header marks the end of the current block
                # Add it to the current block, then finalize the block
                current_block["received"].append(header_value)
                headers_blocks.append(dict(current_block))
                current_block = defaultdict(list)
            else:
                # Add header to current block (always as list)
                current_block[header_name].append(header_value)

        # Add the last block if it has any headers (headers after the last Received)
        if current_block:
            headers_blocks.append(dict(current_block))

        # Extract Gmail labels
        gmail_labels = []
        if "x-gmail-labels" in headers:
            labels_str = headers["x-gmail-labels"]
            if isinstance(labels_str, list):
                labels_str = labels_str[0]  # Take first value if multiple

            # Parse labels, handling quoted strings with commas
            # Split by comma, but respect quoted strings
            pattern = r'"([^"]*)"|([^,]+)'
            matches = re.findall(pattern, labels_str)
            for match in matches:
                # match[0] is the quoted part, match[1] is the unquoted part
                label = match[0] if match[0] else match[1]
                stripped_label = label.strip()
                if stripped_label:
                    gmail_labels.append(stripped_label)

        subject = headers.get("subject", "")
        from_header_decoded = headers.get("from", "")
        from_name, from_addr = parse_email_address(from_header_decoded)
        to_recipients = parse_email_addresses(headers.get("to", ""))
        cc_recipients = parse_email_addresses(headers.get("cc", ""))
        bcc_recipients = parse_email_addresses(headers.get("bcc", ""))
        date = parse_date(headers.get("date", ""))
        message_id = headers.get("message-id", "")
        if message_id.startswith("<") and message_id.endswith(">"):
            message_id = message_id[1:-1]
        references = headers.get("references", "")
        in_reply_to = headers.get("in-reply-to", "")
        if in_reply_to.startswith("<") and in_reply_to.endswith(">"):
            in_reply_to = in_reply_to[1:-1]

        # Extract content using parse_message_content
        body_parts = parse_message_content(message)

        # Use datetime.timezone.utc for the default date
        default_date = datetime.now(dt_timezone.utc)

        return {
            "subject": subject or "",
            "from": {"name": from_name, "email": from_addr},
            "to": [{"name": name, "email": email} for name, email in to_recipients],
            "cc": [{"name": name, "email": email} for name, email in cc_recipients],
            "bcc": [{"name": name, "email": email} for name, email in bcc_recipients],
            "date": date or default_date,
            # JMAP format body parts
            "textBody": body_parts["textBody"],
            "htmlBody": body_parts["htmlBody"],
            "attachments": body_parts["attachments"],
            # Raw MIME is passed in, no need to include decoded string version
            "headers": headers,  # Dict for compatibility
            "headers_list": headers_list,  # List of (name, value) tuples in order
            "headers_blocks": headers_blocks,  # List of dicts, each block ends with a Received header
            "message_id": message_id,
            "references": references,
            "in_reply_to": in_reply_to,
            "gmail_labels": gmail_labels,  # Add Gmail labels to parsed data
        }

    except Exception as e:
        # Ensure any EmailParseError raised above is not caught again
        if isinstance(e, EmailParseError):
            raise e
        logger.exception("Unexpected error during email parsing: %s", str(e))
        raise EmailParseError("Failed to parse email") from e
