"""
RFC5322 email composer using Flanker library.

This module provides functions for composing email messages in RFC5322 format
from JMAP-style data structures. It uses the Flanker library to generate properly
formatted emails, handling complex cases like quotations, multipart messages,
encodings, and attachments.
"""

import base64
import binascii
import datetime
import html
import logging
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any, Dict, List, Optional

from django.utils import timezone

# Import necessary functions/classes from flanker
from flanker.mime import create
from flanker.mime.message import errors as mime_errors
from flanker.mime.message.part import MimePart

# Setup logger
logger = logging.getLogger(__name__)


class EmailComposeError(Exception):
    """Exception raised for errors during email composition."""


def format_address(name: str, email: str) -> str:
    """
    Format a name and email address according to RFC5322.

    Args:
        name: The display name (can be empty)
        email: The email address

    Returns:
        Properly formatted email address string

    Examples:
        >>> format_address('', 'user@example.com')
        'user@example.com'
        >>> format_address('John Doe', 'john@example.com')
        'John Doe <john@example.com>'
    """
    if not email:
        return ""

    if not name:
        return email.strip()

    # Check if the name needs quoting (contains special chars)
    needs_quoting = any(c in name for c in ',.;:@<>()[]"\\')

    if needs_quoting and not (name.startswith('"') and name.endswith('"')):
        # Quote the name and escape any quotes inside it
        name = '"' + name.replace('"', '\\"') + '"'

    return f"{name} <{email.strip()}>"


def format_address_list(addresses: List[Dict[str, str]]) -> str:
    """
    Format a list of address objects into a comma-separated string.

    Args:
        addresses: List of dicts with 'name' and 'email' keys

    Returns:
        Comma-separated string of formatted addresses
    """
    formatted = []
    for addr in addresses:
        name = addr.get("name", "")
        email = addr.get("email", "")
        if email:
            formatted.append(format_address(name, email))

    return ", ".join(formatted)


def set_basic_headers(message_part, jmap_data, in_reply_to=None):
    """
    Set the basic email headers on a message part. (Renamed param for clarity)

    Args:
        message_part: The Flanker MimePart object to set headers on
        jmap_data: Dictionary containing email data in JMAP format
        in_reply_to: Optional message ID being replied to
    """
    subject = jmap_data.get("subject", "")
    if subject:
        message_part.headers["Subject"] = subject

    # From
    from_data = jmap_data.get("from", {})
    # Handle if from_data is a list (normalize to the expected dictionary)
    if isinstance(from_data, list) and from_data:
        from_data = from_data[0]

    from_name = from_data.get("name", "") if isinstance(from_data, dict) else ""
    from_email = from_data.get("email", "") if isinstance(from_data, dict) else ""
    if from_email:
        message_part.headers["From"] = format_address(from_name, from_email)

    # To, CC, BCC recipients
    if jmap_data.get("to"):
        message_part.headers["To"] = format_address_list(
            jmap_data["to"] if isinstance(jmap_data["to"], list) else [jmap_data["to"]]
        )

    if jmap_data.get("cc"):
        message_part.headers["Cc"] = format_address_list(
            jmap_data["cc"] if isinstance(jmap_data["cc"], list) else [jmap_data["cc"]]
        )

    if jmap_data.get("bcc"):
        message_part.headers["Bcc"] = format_address_list(
            jmap_data["bcc"]
            if isinstance(jmap_data["bcc"], list)
            else [jmap_data["bcc"]]
        )

    # Date (use current time if not provided)
    date = jmap_data.get("date", datetime.datetime.now(datetime.timezone.utc))
    if isinstance(date, str):
        # Try to parse the date string
        try:
            date = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
            # Ensure date is timezone-aware
            if date.tzinfo is None or date.tzinfo.utcoffset(date) is None:
                date = timezone.make_aware(
                    date, datetime.timezone.utc
                )  # Use Django's timezone utils
        except (ValueError, TypeError):
            # Default to current time if parsing fails or type is wrong
            date = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(date, datetime.datetime):
        # Ensure provided datetime is timezone-aware
        if date.tzinfo is None or date.tzinfo.utcoffset(date) is None:
            date = timezone.make_aware(date, datetime.timezone.utc)

    message_part.headers["Date"] = format_datetime(date)

    # Set Message-ID if provided
    message_id = jmap_data.get("messageId", jmap_data.get("message_id"))
    if message_id:
        if not message_id.startswith("<") and not message_id.endswith(">"):
            message_id = f"<{message_id}>"
        message_part.headers["Message-ID"] = message_id

    # Set In-Reply-To and References headers for replies
    if in_reply_to:
        if not in_reply_to.startswith("<") and not in_reply_to.endswith(">"):
            in_reply_to = f"<{in_reply_to}>"
        message_part.headers["In-Reply-To"] = in_reply_to

        # Handle References properly: append In-Reply-To to existing ones
        existing_references = jmap_data.get("headers", {}).get(
            "References", ""
        )  # Get from JMAP if provided
        if not existing_references:
            existing_references = jmap_data.get(
                "references", ""
            )  # Check alternative key

        if existing_references:
            # Append the new reference, ensuring space separation
            message_part.headers["References"] = (
                f"{existing_references.strip()} {in_reply_to}"
            )
        else:
            message_part.headers["References"] = in_reply_to

    # Add any custom headers provided in JMAP data
    custom_headers = jmap_data.get("headers", {})
    for header_name, header_value in custom_headers.items():
        # Avoid overwriting standard headers we've already set, unless explicitly intended
        # Also skip References/In-Reply-To if we handled them via in_reply_to argument
        lower_header_name = header_name.lower()
        if lower_header_name not in [
            "from",
            "to",
            "cc",
            "bcc",
            "subject",
            "date",
            "message-id",
        ] and not (in_reply_to and lower_header_name in ["in-reply-to", "references"]):
            message_part.headers[header_name] = header_value


def create_attachment_part(attachment: Dict[str, Any]) -> Optional[MimePart]:
    """
    Create a MIME part for an attachment from JMAP data.

    Args:
        attachment: Dictionary containing attachment data with keys:
            - content: Base64 encoded content
            - type: MIME type (e.g., 'image/jpeg')
            - name: Filename
            - disposition: 'attachment' or 'inline'
            - cid: Content-ID for inline images (optional)

    Returns:
        Part object or None if creation fails
    """
    if not attachment or not isinstance(attachment, dict):
        logger.warning("Invalid attachment data provided")
        return None

    # Get attachment data
    content = attachment.get("content")
    content_type = attachment.get("type", "application/octet-stream")
    filename = attachment.get("name", "")
    disposition = attachment.get("disposition", "attachment")
    content_id = attachment.get("cid")

    if not content:
        logger.warning("No content provided for attachment")
        return None

    try:
        # Decode base64 content
        if isinstance(content, str):
            try:
                decoded_content = base64.b64decode(content)
            except binascii.Error as e:
                logger.error("Failed to decode base64 content: %s", str(e))
                return None
        else:
            # Assume it's already decoded binary data
            decoded_content = content

        # Create the attachment part
        attachment_part = create.attachment(
            content_type, decoded_content, filename=filename, disposition=disposition
        )

        # Set Content-ID header for inline images
        if disposition == "inline" and content_id:
            # Ensure Content-ID is properly formatted with angle brackets
            if not content_id.startswith("<") and not content_id.endswith(">"):
                content_id = f"<{content_id}>"
            elif not content_id.startswith("<"):
                content_id = f"<{content_id}"
            elif not content_id.endswith(">"):
                content_id = f"{content_id}>"

            attachment_part.headers["Content-ID"] = content_id

        return attachment_part
    except (TypeError, ValueError, mime_errors.MimeError) as e:
        logger.error("Failed to create attachment part: %s", str(e))
        return None


def create_multipart_message(  # pylint: disable=too-many-branches
    jmap_data: Dict[str, Any], in_reply_to: Optional[str] = None
) -> MimePart:
    """
    Create the top-level MIME part (message structure) from JMAP data.

    Args:
        jmap_data: Dictionary with JMAP email data
        in_reply_to: Optional message ID being replied to

    Returns:
        The top-level MimePart object representing the email.
    """
    # Determine content types and attachments
    has_text = bool(jmap_data.get("textBody"))
    has_html = bool(jmap_data.get("htmlBody"))
    attachments = jmap_data.get("attachments", [])

    inline_attachments = [
        att
        for att in attachments
        if att.get("disposition") == "inline" and att.get("cid")
    ]
    regular_attachments = [att for att in attachments if att not in inline_attachments]

    # 1. Determine the top-level structure/content type

    top_level_part = None

    if has_text and not has_html and not attachments:
        # Simple text email
        text_content_data = jmap_data["textBody"][0]
        content = (
            text_content_data.get("content", "")
            if isinstance(text_content_data, dict)
            else text_content_data
        )
        top_level_part = create.text("plain", content, "utf-8")

    elif has_html and not has_text and not attachments:
        # Simple HTML email
        html_content_data = jmap_data["htmlBody"][0]
        content = (
            html_content_data.get("content", "")
            if isinstance(html_content_data, dict)
            else html_content_data
        )
        content = content.replace("&rsquo;", "'")  # Handle French apostrophe
        top_level_part = create.text("html", content, "utf-8")

    # Multipart email required
    elif regular_attachments:
        # If regular attachments exist, top level must be multipart/mixed
        top_level_part = create.multipart("mixed")

        # Build the main content part (alternative or related) to add to mixed
        main_content_part = None
        if has_html and inline_attachments:
            # Related needed for HTML + inline images
            related_part = create.multipart("related")
            if has_text:
                # Alternative needed inside related
                alternative_part = create.multipart("alternative")
                for part_data in jmap_data.get("textBody", []):
                    content = (
                        part_data.get("content", "")
                        if isinstance(part_data, dict)
                        else part_data
                    )
                    alternative_part.append(create.text("plain", content, "utf-8"))
                for part_data in jmap_data.get("htmlBody", []):
                    content = (
                        part_data.get("content", "")
                        if isinstance(part_data, dict)
                        else part_data
                    )
                    alternative_part.append(
                        create.text("html", content.replace("&rsquo;", "'"), "utf-8")
                    )
                related_part.append(alternative_part)
            else:  # Only HTML + inline
                for part_data in jmap_data.get("htmlBody", []):
                    content = (
                        part_data.get("content", "")
                        if isinstance(part_data, dict)
                        else part_data
                    )
                    related_part.append(
                        create.text("html", content.replace("&rsquo;", "'"), "utf-8")
                    )

            # Add inline attachments to related part
            for attachment in inline_attachments:
                att_part = create_attachment_part(attachment)
                if att_part:
                    related_part.append(att_part)

            main_content_part = related_part

        elif has_text or has_html:
            # Alternative needed for text/html (no inline)
            alternative_part = create.multipart("alternative")
            if has_text:
                for part_data in jmap_data.get("textBody", []):
                    content = (
                        part_data.get("content", "")
                        if isinstance(part_data, dict)
                        else part_data
                    )
                    alternative_part.append(create.text("plain", content, "utf-8"))
            if has_html:
                for part_data in jmap_data.get("htmlBody", []):
                    content = (
                        part_data.get("content", "")
                        if isinstance(part_data, dict)
                        else part_data
                    )
                    alternative_part.append(
                        create.text("html", content.replace("&rsquo;", "'"), "utf-8")
                    )
            main_content_part = alternative_part

        # Add the main content (alternative/related) to the mixed part if it exists
        if main_content_part:
            top_level_part.append(main_content_part)
        elif (
            not regular_attachments
        ):  # Should not happen if top_level_part is mixed, but safety check
            top_level_part.append(create.text("plain", ""))  # Add empty part if needed

        # Add regular attachments to mixed part
        for attachment in regular_attachments:
            att_part = create_attachment_part(attachment)
            if att_part:
                top_level_part.append(att_part)

    elif has_html and inline_attachments:
        # Top level is multipart/related (no regular attachments)
        top_level_part = create.multipart("related")
        if has_text:
            # Alternative needed inside related
            alternative_part = create.multipart("alternative")
            for part_data in jmap_data.get("textBody", []):
                content = (
                    part_data.get("content", "")
                    if isinstance(part_data, dict)
                    else part_data
                )
                alternative_part.append(create.text("plain", content, "utf-8"))
            for part_data in jmap_data.get("htmlBody", []):
                content = (
                    part_data.get("content", "")
                    if isinstance(part_data, dict)
                    else part_data
                )
                alternative_part.append(
                    create.text("html", content.replace("&rsquo;", "'"), "utf-8")
                )
            top_level_part.append(alternative_part)
        else:  # Only HTML + inline
            for part_data in jmap_data.get("htmlBody", []):
                content = (
                    part_data.get("content", "")
                    if isinstance(part_data, dict)
                    else part_data
                )
                top_level_part.append(
                    create.text("html", content.replace("&rsquo;", "'"), "utf-8")
                )
        # Add inline attachments
        for attachment in inline_attachments:
            att_part = create_attachment_part(attachment)
            if att_part:
                top_level_part.append(att_part)

    elif has_text and has_html:
        # Top level is multipart/alternative (no attachments)
        top_level_part = create.multipart("alternative")
        for part_data in jmap_data.get("textBody", []):
            content = (
                part_data.get("content", "")
                if isinstance(part_data, dict)
                else part_data
            )
            top_level_part.append(create.text("plain", content, "utf-8"))
        for part_data in jmap_data.get("htmlBody", []):
            content = (
                part_data.get("content", "")
                if isinstance(part_data, dict)
                else part_data
            )
            top_level_part.append(
                create.text("html", content.replace("&rsquo;", "'"), "utf-8")
            )

    else:
        # Should not be reachable if logic covers all cases (text only, html only handled above)
        # Handle case of only attachments? create_attachment_part handles content.
        # If only attachments, top level should be mixed. This is covered by the first 'if regular_attachments' block.
        # If only headers provided?
        # logger.warning("Unexpected message structure in create_multipart_message.")
        # Create a minimal valid part
        top_level_part = create.text("plain", "")

    # Fallback if somehow top_level_part wasn't created
    if top_level_part is None:
        logger.error("Failed to determine top-level part in create_multipart_message.")
        top_level_part = create.text("plain", "")  # Create minimal valid part

    # 2. Set headers on the determined top-level part
    set_basic_headers(top_level_part, jmap_data, in_reply_to)

    return top_level_part


def compose_email(
    jmap_data: Dict[str, Any], in_reply_to: Optional[str] = None
) -> bytes:
    """
    Convert a JMAP email object to RFC5322 format.

    Args:
        jmap_data: Dictionary with JMAP email data
        in_reply_to: Optional message ID being replied to

    Returns:
        RFC5322 formatted email as bytes

    Raises:
        EmailComposeError: If composition fails
    """
    try:
        # Validate minimum required fields
        if not jmap_data:
            raise EmailComposeError("Empty JMAP data provided")

        # Validate and normalize 'from' field
        from_data = jmap_data.get("from", {})

        # Handle if from_data is a list
        if isinstance(from_data, list):
            if not from_data:
                raise EmailComposeError("Empty 'from' list in JMAP data")
            from_data = from_data[0]
            jmap_data["from"] = from_data

        # Check we have an email address
        if not isinstance(from_data, dict) or not from_data.get("email"):
            raise EmailComposeError("Missing or invalid 'from' field in JMAP data")

        # Ensure textBody and htmlBody are lists if present
        if "textBody" in jmap_data and not isinstance(jmap_data["textBody"], list):
            jmap_data["textBody"] = [jmap_data["textBody"]]

        if "htmlBody" in jmap_data and not isinstance(jmap_data["htmlBody"], list):
            jmap_data["htmlBody"] = [jmap_data["htmlBody"]]

        # Create the top-level MIME part (message structure)
        msg_part = create_multipart_message(jmap_data, in_reply_to)

        # Convert the top-level part to string
        message_str = msg_part.to_string()

        # Convert string to bytes using UTF-8
        message_bytes = message_str.encode("utf-8")

        return message_bytes
    except mime_errors.MimeError as e:
        # Catch flanker specific errors during composition/string conversion
        logger.error(
            "Flanker MIME composition/encoding error: %s", str(e), exc_info=True
        )
        raise EmailComposeError(f"MIME composition error: {str(e)}") from e
    except Exception as e:
        # Log other unexpected exceptions
        logger.exception("Unexpected error during email composition: %s", str(e))
        raise EmailComposeError(f"Failed to compose email: {str(e)}") from e

def _embed_original_message(
    original_message: Dict[str, Any],
    new_text: str = "",
    new_html: Optional[str] = None,
    include_original: bool = True,
    is_forward: bool = False,
) -> tuple[str, str]:
    """
    Embed original message content into new text and HTML.

    Args:
        original_message: The JMAP structure of the original message
        new_text: The new text content
        new_html: Optional HTML version of the new content
        include_original: Whether to include the original message content
        is_forward: Whether this is a forward (affects header format)

    Returns:
        Tuple of (text_body, html_body)
    """
    if new_text is None:
        new_text = ""

    if not include_original:
        # Return simple versions without embedding
        html_body = new_html or f"<p>{html.escape(new_text)}</p>"
        if html_body:
            html_body = html_body.replace("&rsquo;", "'")
        return new_text, html_body

    # Get information from the original message
    orig_subject = original_message.get("subject", "")
    orig_from = original_message.get("from", {})
    orig_to = original_message.get("to", [])
    orig_cc = original_message.get("cc", [])
    orig_date = original_message.get("date", "")

    # Format date as string for quoting
    date_str = ""
    if isinstance(orig_date, datetime.datetime):
        if orig_date.tzinfo is None or orig_date.tzinfo.utcoffset(orig_date) is None:
            orig_date = (
                timezone.make_aware(orig_date, datetime.timezone.utc)
                if hasattr(timezone, "make_aware")
                else orig_date.replace(tzinfo=datetime.timezone.utc)
            )

        # Format according to RFC 5322 preferred date format (e.g., "15 May 2023 14:30:00 +0000")
        date_str = format_datetime(orig_date)
    elif isinstance(orig_date, str) and orig_date:
        # Try parsing the string date to reformat it consistently
        parsed_dt = parsedate_to_datetime(orig_date)
        if parsed_dt:
            date_str = format_datetime(parsed_dt)
        else:
            date_str = orig_date  # Use original string if parsing fails
    else:
        date_str = "an unknown date"  # Fallback if date is missing or invalid type

    # Prepare header for text
    header_text = ""
    if is_forward:
        from_display = format_address(
            orig_from.get("name", ""), orig_from.get("email", "")
        )
        to_display = format_address_list(orig_to)
        cc_display = format_address_list(orig_cc) if orig_cc else ""

        header_text = "\n\n---------- Forwarded message ----------\n"
        if from_display:
            header_text += f"From: {from_display}\n"
        if to_display:
            header_text += f"To: {to_display}\n"
        if cc_display:
            header_text += f"Cc: {cc_display}\n"
        header_text += f"Subject: {orig_subject}\n"
        header_text += f"Date: {date_str}\n\n"
    else:
        # Reply format
        from_display = format_address(
            orig_from.get("name", ""), orig_from.get("email", "")
        )
        if from_display:
            header_text = f"\n\nOn {date_str}, {from_display} wrote:\n"
        else:
            header_text = f"\n\nOn {date_str}, someone wrote:\n"

    # Create the text body with original message
    text_body = f"{new_text}{header_text}"

    # Add original text content
    if original_message.get("textBody"):
        text_body_list = original_message["textBody"]
        if not isinstance(text_body_list, list):
            text_body_list = [text_body_list]

        first_text = text_body_list[0] if text_body_list else None
        orig_text = ""

        if isinstance(first_text, str):
            orig_text = first_text
        elif isinstance(first_text, dict):
            orig_text = first_text.get("content", "")

        if orig_text:
            if is_forward:
                text_body += orig_text
            else:
                # For replies, quote each line
                quoted_text = "\n".join([f"> {line}" for line in orig_text.split("\n")])
                text_body += quoted_text

    # Create HTML content
    html_content = new_html or f"<p>{html.escape(new_text)}</p>"
    if html_content:
        html_content = html_content.replace("&rsquo;", "'")

    html_body = html_content
    if new_html or original_message.get("htmlBody"):
        # Forward HTML format
        from_display_html = html.escape(
            format_address(orig_from.get("name", ""), orig_from.get("email", ""))
        )
        to_display_html = html.escape(format_address_list(orig_to))
        cc_display_html = html.escape(format_address_list(orig_cc)) if orig_cc else ""

        if is_forward:
            header_html = "<p>---------- Forwarded message ----------<br/>"
        else:
            header_html = "<p>---------- In reply to ----------<br/>"

        if from_display_html:
            header_html += f'<strong>From:</strong> {from_display_html}<br/>'
        if to_display_html:
            header_html += f'<strong>To:</strong> {to_display_html}<br/>'
        if cc_display_html:
            header_html += f'<strong>Cc:</strong> {cc_display_html}<br/>'
        header_html += f'<strong>Subject:</strong> {html.escape(orig_subject)}<br/>'
        header_html += f'<strong>Date:</strong> {html.escape(date_str)}<br/>'
        header_html += "</p>"

        # Get original HTML content
        orig_html = ""
        if original_message.get("htmlBody"):
            html_body_list = original_message["htmlBody"]
            if not isinstance(html_body_list, list):
                html_body_list = [html_body_list]

            first_html = html_body_list[0] if html_body_list else None

            if isinstance(first_html, str):
                orig_html = first_html
            elif isinstance(first_html, dict):
                orig_html = first_html.get("content", "")


        nested_html = f"""
        <hr data-type="quote-separator" />
        <blockquote>
            {header_html}
            {orig_html}
        </blockquote>
        """
        html_body = f"{html_content}{nested_html}"

    return text_body, html_body


def create_reply_message(
    original_message: Dict[str, Any],
    reply_text: str = "",
    reply_html: Optional[str] = None,
    include_quote: bool = True,
) -> Dict[str, Any]:
    """
    Create a JMAP reply message to an existing email.

    Args:
        original_message: The JMAP structure of the original message
        reply_text: The reply text content
        reply_html: Optional HTML version of the reply
        include_quote: Whether to include a quote of the original message

    Returns:
        A JMAP-style message structure for the reply
    """
    # Get information from the original message
    orig_subject = original_message.get("subject", "")
    orig_from = original_message.get("from", {})
    orig_message_id = original_message.get(
        "messageId", original_message.get("message_id", "")
    )
    orig_references = original_message.get("references", "")

    if reply_text is None:
        reply_text = ""

    # Create reply subject (add Re: if needed)
    if orig_subject.lower().startswith("re:"):
        reply_subject = orig_subject
    else:
        reply_subject = f"Re: {orig_subject}"

    # Use the shared embedding logic
    text_body, html_body = _embed_original_message(
        original_message, reply_text, reply_html, include_quote, is_forward=False
    )

    # Construct the reply JMAP structure
    reply_headers = {}

    # Set In-Reply-To and References headers for threading
    if orig_message_id:
        # Ensure Message-ID is enclosed in angle brackets
        if not orig_message_id.startswith("<") and not orig_message_id.endswith(">"):
            orig_message_id_formatted = f"<{orig_message_id}>"
        else:
            orig_message_id_formatted = orig_message_id

        reply_headers["In-Reply-To"] = orig_message_id_formatted
        # Append original Message-ID to existing references, or start new References
        if orig_references:
            # Ensure original references are also formatted correctly (list of IDs)
            # This part might need more robust parsing of the References header
            reply_headers["References"] = (
                f"{orig_references} {orig_message_id_formatted}"
            )
        else:
            reply_headers["References"] = orig_message_id_formatted

    reply = {
        "subject": reply_subject,
        "textBody": [
            {
                "partId": "text-part",  # Consider generating unique part IDs if needed
                "type": "text/plain",
                "content": text_body,
            }
        ],
        "from": {},  # To be filled by the caller
        "to": [orig_from]
        if orig_from and orig_from.get("email")
        else [],  # Check orig_from exists
        # Keep original CC recipients by default when replying
        "cc": original_message.get("cc", []),
        # Don't typically include original BCC in a reply
        # 'bcc': original_message.get('bcc', []),
        "headers": reply_headers,  # Add the threading headers
    }

    # Add HTML part if it was generated
    if html_body != (reply_html or f"<p>{html.escape(reply_text)}</p>"):
        reply["htmlBody"] = [
            {
                "partId": "html-part",  # Consider unique ID
                "type": "text/html",
                "content": html_body,
            }
        ]

    return reply


def create_forward_message(
    original_message: Dict[str, Any],
    forward_text: str,
    forward_html: Optional[str] = None,
    include_original: bool = True,
) -> Dict[str, Any]:
    """
    Create a JMAP forward message from an existing email.

    Args:
        original_message: The JMAP structure of the original message
        forward_text: The forward text content
        forward_html: Optional HTML version of the forward
        include_original: Whether to include the original message content

    Returns:
        A JMAP-style message structure for the forward
    """
    # Get information from the original message
    orig_subject = original_message.get("subject", "")

    # Create forward subject (add Fwd: if needed)
    if orig_subject.lower().startswith("fwd:"):
        forward_subject = orig_subject
    else:
        forward_subject = f"Fwd: {orig_subject}"

    if forward_text is None:
        forward_text = ""

    # Use the shared embedding logic
    text_body, html_body = _embed_original_message(
        original_message, forward_text, forward_html, include_original, is_forward=True
    )

    # For forwards, we typically don't set In-Reply-To or References headers
    # as forwards are not replies to the original message
    forward_headers = {}

    forward = {
        "subject": forward_subject,
        "textBody": [
            {
                "partId": "text-part",
                "type": "text/plain",
                "content": text_body,
            }
        ],
        "from": {},  # To be filled by the caller
        "to": [],  # To be filled by the caller
        "cc": [],
        "headers": forward_headers,
    }

    # Add HTML part if it was generated
    if html_body != (forward_html or f"<p>{html.escape(forward_text)}</p>"):
        forward["htmlBody"] = [
            {
                "partId": "html-part",
                "type": "text/html",
                "content": html_body,
            }
        ]

    return forward
