"""
RFC5322 email parser using Flanker library.

This module provides functions for parsing email addresses and messages
according to RFC5322 standards. It uses the Flanker library for robust
parsing and is intended to be the central place for all email parsing
operations in the application.
"""

import logging
import re
from datetime import datetime
from datetime import timezone as dt_timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from flanker.addresslib import address
from flanker.mime import create
from flanker.mime.message import errors as mime_errors

logger = logging.getLogger(__name__)


class EmailParseError(Exception):
    """Exception raised for errors during email parsing."""

    pass


def decode_email_header_text(header_text: str) -> str:
    """
    Decode email header text that might be encoded (RFC 2047).
    """
    if not header_text:
        return ""

    try:
        # Ensure input is a string
        header_text_str = str(header_text)
        # Use decode_header which returns a list of (decoded_string, charset) pairs
        # charset is None if the part was not encoded
        decoded_parts = decode_header(header_text_str)

        result_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                # If charset is None or invalid, try common fallbacks
                if not charset or charset == "unknown-8bit":
                    try:
                        result_parts.append(part.decode("utf-8", errors="replace"))
                    except UnicodeDecodeError:
                        result_parts.append(part.decode("latin-1", errors="replace"))
                else:
                    try:
                        result_parts.append(part.decode(charset, errors="replace"))
                    except (LookupError, UnicodeDecodeError):  # Catch decoding errors
                        # Fallback if specified charset fails
                        result_parts.append(part.decode("utf-8", errors="replace"))
            else:
                # Part is already a string (not encoded)
                result_parts.append(part)

        # Join the decoded parts. Avoid adding extra spaces between adjacent encoded words.
        # decode_header sometimes leaves whitespace that needs cleaning.
        full_result = "".join(result_parts)
        # Collapse multiple spaces into one
        return " ".join(full_result.split())

    except Exception:
        # If anything goes wrong, return the original text as a string
        return str(header_text)


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
    return parsed.display_name or "", parsed.address


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
    return [(addr.display_name or "", addr.address) for addr in parsed]


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
    except Exception:
        return None


def parse_message_content(message) -> Dict[str, Any]:
    """
    Extract text, HTML, and attachments from a message, following JMAP format.
    """
    result = {"textBody": [], "htmlBody": [], "attachments": []}

    parts_to_process = []
    # Initial check for valid message structure
    if not hasattr(message, "content_type") or not message.content_type:
        if hasattr(message, "body") and isinstance(message.body, str):
            result["textBody"].append(
                {"partId": "", "type": "text/plain", "content": message.body}
            )
        return result

    if not message.content_type.is_multipart():
        parts_to_process.append(message)
    else:
        try:
            for part in message.walk():
                if (
                    part is message
                    or not hasattr(part, "content_type")
                    or not part.content_type
                    or (
                        hasattr(part.content_type, "is_multipart")
                        and part.content_type.is_multipart()
                    )
                ):
                    continue
                parts_to_process.append(part)
        except Exception as e:
            logger.error(f"Error walking message parts: {e}", exc_info=True)
            return result  # Return potentially partial result on error

    for part in parts_to_process:
        # Safety checks for each part
        if (
            not hasattr(part, "content_type")
            or not part.content_type
            or not hasattr(part, "body")
            or not hasattr(part, "headers")
        ):
            logger.warning("Skipping invalid or incomplete part during parsing.")
            continue

        content_type_obj = part.content_type
        body = part.body
        if body is None:
            continue

        # Extract common attributes
        part_id = getattr(part, "message_id", "") or ""
        headers_dict = getattr(part, "headers", {})
        disposition_header = headers_dict.get("Content-Disposition", "")
        disposition_value = (
            str(disposition_header).lower() if disposition_header else ""
        )
        is_attachment_disposition = "attachment" in disposition_value
        is_inline_disposition = "inline" in disposition_value
        has_disposition = bool(disposition_value)

        # Extract filename
        filename_param = (
            content_type_obj.params.get("name")
            if hasattr(content_type_obj, "params")
            else None
        )
        disposition_filename = None
        if "filename=" in str(disposition_header):
            try:
                match = re.search(
                    r'filename\*?=(?:(["\'])(.*?)\1|([^;]+))',
                    str(disposition_header),
                    re.IGNORECASE,
                )
                if match:
                    fname_raw = match.group(2) or match.group(3)
                    if fname_raw:
                        disposition_filename = decode_email_header_text(
                            fname_raw.strip()
                        )
            except Exception:
                pass  # Ignore filename parsing errors
        filename = disposition_filename or filename_param or None

        content_id_header = headers_dict.get("Content-ID")
        content_id = str(content_id_header).strip("<>") if content_id_header else None

        # --- Part Classification ---
        is_classified_as_attachment = False
        # Calculate default type based on flanker's interpretation
        default_type_str = f"{content_type_obj.main}/{content_type_obj.sub}"
        final_part_type = default_type_str  # Initialize with default

        # 1. Prioritize parts with disposition or filename as potential attachments
        if has_disposition or filename:
            is_explicit_text = default_type_str in ["text/plain", "text/html"]

            # Only treat as main body if explicitly text AND has NO disposition
            if not (is_explicit_text and not has_disposition):
                # --- Classify as Attachment ---

                # Override type if flanker reports text/plain for a part with 'attachment' disposition
                if is_attachment_disposition and default_type_str == "text/plain":
                    final_part_type = "application/octet-stream"  # Override
                    # logger.warning(...) # Removed warning log

                elif (
                    has_disposition
                ):  # If disposition exists (but not the override case) try raw header
                    raw_content_type_header = headers_dict.get("Content-Type", "")
                    raw_type_str = str(raw_content_type_header).split(";")[0].strip()
                    if raw_type_str:
                        final_part_type = raw_type_str
                # Else: final_part_type remains default_type_str

                attach_disposition = "inline" if is_inline_disposition else "attachment"
                final_filename = filename if filename else "unnamed"

                result["attachments"].append(
                    {
                        "partId": part_id,
                        "type": final_part_type,
                        "name": final_filename,
                        "size": len(body) if isinstance(body, (str, bytes)) else 0,
                        "disposition": attach_disposition,
                        "cid": content_id,
                    }
                )
                is_classified_as_attachment = True

        # 2. Check for standard text/html body parts (only if not already classified)
        elif isinstance(body, str) and body:
            is_text_plain = default_type_str == "text/plain"
            is_text_html = default_type_str == "text/html"

            if is_text_plain:
                result["textBody"].append(
                    {"partId": part_id, "type": "text/plain", "content": body}
                )
            elif is_text_html:
                result["htmlBody"].append(
                    {"partId": part_id, "type": "text/html", "content": body}
                )
            else:
                # Fallback: Treat other string content as attachment
                attach_disposition = "attachment"
                final_filename = filename if filename else "unnamed"
                result["attachments"].append(
                    {
                        "partId": part_id,
                        "type": default_type_str,
                        "name": final_filename,
                        "size": len(body),
                        "disposition": attach_disposition,
                        "cid": content_id,
                    }
                )

        # 3. Fallback for bytes content (only if not already classified)
        elif isinstance(body, bytes):
            attach_disposition = "attachment"
            final_filename = filename if filename else "unnamed"
            # Use default type determined by flanker for bytes without disposition
            final_part_type = default_type_str
            result["attachments"].append(
                {
                    "partId": part_id,
                    "type": final_part_type,
                    "name": final_filename,
                    "size": len(body),
                    "disposition": attach_disposition,
                    "cid": content_id,
                }
            )

    return result


def parse_email_message(raw_email) -> Optional[Dict[str, Any]]:
    """
    Parse a raw email message into a structured dictionary following JMAP format.

    Args:
        raw_email: Raw email data as bytes or string

    Returns:
        Dictionary containing parsed email data, or None if parsing fails fundamentally.

    Raises:
        EmailParseError: If parsing fails with a specific error we want to propagate.
    """
    if not raw_email:
        # Return None for empty input instead of raising error immediately
        # Or raise EmailParseError("Empty email data") if preferred
        return None

    try:
        # Ensure input is bytes for flanker
        if isinstance(raw_email, str):
            # Detect potential encoding issues early? Usually headers declare encoding.
            # Assume UTF-8 as a reasonable default for string input, but Flanker should handle MIME encoding.
            try:
                raw_email_bytes = raw_email.encode("utf-8")
            except UnicodeDecodeError:
                # Fallback if string itself is problematic (e.g., mixed encodings)
                raw_email_bytes = raw_email.encode("latin-1", errors="replace")
        elif isinstance(raw_email, bytes):
            raw_email_bytes = raw_email
        else:
            raise EmailParseError(
                f"Invalid input type for raw_email: {type(raw_email)}"
            )

        # Parse with flanker
        message = create.from_string(raw_email_bytes)

        # Handle cases where flanker might return None for severely malformed input
        if message is None:
            # logger.warning("Flanker returned None parsing email data.")
            return None  # Indicate parsing failure

        # Extract all headers, normalizing keys to lowercase
        headers = {}
        # Iterate through raw headers (key, value pairs)
        for k, v in message.headers.items():
            # Decode the raw value using our function
            decoded_value = decode_email_header_text(v)
            key_lower = k.lower()
            # Store potentially multi-value headers as lists
            if key_lower in headers:
                current_value = headers[key_lower]
                if isinstance(current_value, list):
                    current_value.append(decoded_value)
                else:
                    headers[key_lower] = [current_value, decoded_value]
            else:
                headers[key_lower] = decoded_value

        # Extract specific headers using decoded values
        subject = headers.get("subject", "")  # Already decoded

        # Parse from address using the decoded header
        from_header_decoded = headers.get("from", "")
        from_name, from_addr = parse_email_address(from_header_decoded)

        # Parse recipients using decoded headers
        to_header_decoded = headers.get("to", "")
        cc_header_decoded = headers.get("cc", "")
        bcc_header_decoded = headers.get(
            "bcc", ""
        )  # Note: BCC usually not present in received mail

        to_recipients = parse_email_addresses(to_header_decoded)
        cc_recipients = parse_email_addresses(cc_header_decoded)
        bcc_recipients = parse_email_addresses(bcc_header_decoded)

        # Parse date using decoded header
        date_header_decoded = headers.get("date", "")
        date = parse_date(date_header_decoded)

        # Extract content following JMAP format
        # Pass the flanker message object
        body_parts = parse_message_content(message)

        # Get message ID from decoded headers
        message_id = headers.get("message-id", "")
        # Strip angle brackets commonly found in Message-ID
        if message_id.startswith("<") and message_id.endswith(">"):
            message_id = message_id[1:-1]

        # Get references (already decoded)
        references = headers.get("references", "")
        in_reply_to = headers.get("in-reply-to", "")
        # Strip angle brackets from In-Reply-To as well
        if in_reply_to.startswith("<") and in_reply_to.endswith(">"):
            in_reply_to = in_reply_to[1:-1]

        # For backward compatibility, extract the first text and HTML parts as strings
        body_text = (
            body_parts["textBody"][0]["content"] if body_parts["textBody"] else ""
        )
        body_html = (
            body_parts["htmlBody"][0]["content"] if body_parts["htmlBody"] else ""
        )

        # Store raw MIME as string (best effort decoding)
        try:
            raw_mime_str = raw_email_bytes.decode("utf-8", errors="replace")
        except Exception:
            try:
                raw_mime_str = raw_email_bytes.decode("latin-1", errors="replace")
            except Exception:
                raw_mime_str = repr(
                    raw_email_bytes
                )  # Fallback if decoding fails completely

        # Use datetime.timezone.utc for the default date
        default_date = datetime.now(dt_timezone.utc)

        return {
            "subject": subject or "",  # Use empty string instead of 'No Subject'
            "from": {"name": from_name, "email": from_addr},
            "to": [{"name": name, "email": email} for name, email in to_recipients],
            "cc": [{"name": name, "email": email} for name, email in cc_recipients],
            "bcc": [{"name": name, "email": email} for name, email in bcc_recipients],
            "date": date or default_date,  # Use the defined default
            # JMAP format body parts
            "textBody": body_parts["textBody"],
            "htmlBody": body_parts["htmlBody"],
            "attachments": body_parts["attachments"],
            # Deprecated fields (keep for now if needed, but prefer JMAP)
            "body_text": body_text,
            "body_html": body_html,
            # Raw MIME and Headers
            "raw_mime": raw_mime_str,
            "headers": headers,  # Store all headers
            "message_id": message_id,  # Stripped of angle brackets
            "references": references,  # Raw references string
            "in_reply_to": in_reply_to,  # Stripped In-Reply-To ID
        }

    except mime_errors.MimeError as e:
        # Catch specific flanker errors
        # logger.error(f"Flanker MIME parsing error: {str(e)}")
        raise EmailParseError(f"MIME parsing failed: {str(e)}")
    except Exception as e:
        # Catch other unexpected errors during parsing
        # logger.exception(f"Unexpected error during email parsing: {str(e)}")
        raise EmailParseError(f"Failed to parse email: {str(e)}")
