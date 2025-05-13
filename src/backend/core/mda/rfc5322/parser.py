"""
RFC5322 email parser using Flanker library.

This module provides functions for parsing email addresses and messages
according to RFC5322 standards. It uses the Flanker library for robust
parsing and is intended to be the central place for all email parsing
operations in the application.
"""

import hashlib
import logging
import re
from datetime import datetime
from datetime import timezone as dt_timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
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
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error walking message parts: %s", e, exc_info=True)
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

        # --- Extract filename ---
        filename = None

        # 1. Try Flanker's parsed content_disposition property
        disposition_info = getattr(part, "content_disposition", None)
        if (
            disposition_info
            and isinstance(disposition_info, tuple)
            and len(disposition_info) > 1
        ):
            params = disposition_info[1]
            if isinstance(params, dict):
                filename_raw = params.get("filename")
                if filename_raw:
                    # Flanker might already decode, but decode again for safety/consistency
                    filename = decode_email_header_text(str(filename_raw).strip())

        # 2. If not found via Flanker property, try parsing raw headers
        if not filename:
            # headers_dict is already defined above

            # 2a. Try Content-Disposition header parsing (regex method)
            # disposition_header is already defined above
            if disposition_header and "filename=" in str(disposition_header):
                match_disp = re.search(
                    r'filename\*?=(?:(["\'])(.*?)\1|([^;]+))',
                    str(disposition_header),
                    re.IGNORECASE,
                )
                if match_disp:
                    fname_raw = match_disp.group(2) or match_disp.group(3)
                    if fname_raw:
                        filename = decode_email_header_text(fname_raw.strip())

            # 2b. Try Content-Type 'name' parameter
            if not filename:
                # content_type_obj is already defined above
                filename_param = (
                    content_type_obj.params.get("name")
                    if hasattr(content_type_obj, "params")
                    else None
                )
                if filename_param:
                    filename = decode_email_header_text(filename_param.strip())

        # --- Get Content-ID ---
        content_id_header = headers_dict.get("Content-ID")
        content_id = str(content_id_header).strip("<>") if content_id_header else None

        # --- Part Classification ---

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

                elif (
                    has_disposition
                ):  # If disposition exists (but not the override case) try raw header
                    raw_content_type_header = headers_dict.get("Content-Type", "")
                    raw_type_str = (
                        str(raw_content_type_header).split(";", maxsplit=1)[0].strip()
                    )
                    if raw_type_str:
                        final_part_type = raw_type_str
                # Else: final_part_type remains default_type_str

                attach_disposition = "inline" if is_inline_disposition else "attachment"
                final_filename = filename if filename else "unnamed"

                # DEBUG LOGGING START
                logger.debug(
                    "Classifying as attachment: type='%s', name='%s', disposition='%s', cid='%s', part_id='%s'",
                    final_part_type,
                    final_filename,
                    attach_disposition,
                    content_id,
                    part_id,
                )
                # DEBUG LOGGING END

                # Convert body to bytes if it's a string
                if isinstance(body, str):
                    body_bytes = body.encode("utf-8")
                else:
                    body_bytes = body

                content_hash = hashlib.sha256(body_bytes).hexdigest()

                # Store attachment info for later processing
                result["attachments"].append(
                    {
                        "type": final_part_type,
                        "name": final_filename,
                        "size": len(body_bytes),
                        "disposition": attach_disposition,
                        "cid": content_id,
                        "content": body_bytes,
                        "sha256": content_hash,
                    }
                )

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

            content_hash = hashlib.sha256(body).hexdigest()

            # Store attachment info for processing later
            result["attachments"].append(
                {
                    "type": final_part_type,
                    "name": final_filename,
                    "size": len(body),
                    "disposition": attach_disposition,
                    "cid": content_id,
                    "content": body,
                    "sha256": content_hash,
                }
            )

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
        for k, v in message.headers.items():
            decoded_value = decode_email_header_text(v)
            key_lower = k.lower()
            if key_lower in headers:
                current_value = headers[key_lower]
                if isinstance(current_value, list):
                    current_value.append(decoded_value)
                else:
                    headers[key_lower] = [current_value, decoded_value]
            else:
                headers[key_lower] = decoded_value
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
            "headers": headers,
            "message_id": message_id,
            "references": references,
            "in_reply_to": in_reply_to,
        }

    except Exception as e:
        # Ensure any EmailParseError raised above is not caught again
        if isinstance(e, EmailParseError):
            raise e
        logger.exception("Unexpected error during email parsing: %s", str(e))
        raise EmailParseError(f"Failed to parse email: {str(e)}") from e  # Add `from e`
