"""
Tests for the RFC5322 email composer module.
"""

# pylint: disable=too-many-lines
import base64
import email
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.parser import BytesParser

import pytest

from core.formats.rfc5322.composer import (
    EmailComposeError,
    compose_email,
    create_attachment_part,
    create_reply_message,
    format_address,
    format_address_list,
)


# Helper function to decode a header string fully
def decode_header_string(header_value):
    """Decode an RFC 2047 encoded header string."""
    if not header_value:
        return ""
    # make_header handles joining decoded parts
    decoded = make_header(decode_header(header_value))
    return str(decoded)


class TestAddressFormatting:
    """Tests for email address formatting functions."""

    def test_format_simple_address(self):
        """Test formatting a simple email address without a display name."""
        formatted = format_address("", "user@example.com")
        assert formatted == "user@example.com"

    def test_format_with_display_name(self):
        """Test formatting an email address with a display name."""
        formatted = format_address("Maria Garcia", "maria@example.com")
        assert formatted == "Maria Garcia <maria@example.com>"

    def test_format_with_comma_in_name(self):
        """Test formatting an email address with a comma in the display name."""
        formatted = format_address("Garcia, Maria", "maria@example.com")
        assert formatted == '"Garcia, Maria" <maria@example.com>'

    def test_format_with_special_chars(self):
        """Test formatting a name with special characters that require quoting."""
        formatted = format_address("Maria (Admin)", "maria@example.com")
        assert formatted == '"Maria (Admin)" <maria@example.com>'

    def test_format_with_quoted_name(self):
        """Test formatting a name that's already quoted properly."""
        formatted = format_address('"Maria Garcia"', "maria@example.com")
        assert formatted == '"Maria Garcia" <maria@example.com>'

    def test_format_with_escaped_quotes(self):
        """Test formatting a name with quotes that need escaping."""
        formatted = format_address('Maria "Admin" Garcia', "maria@example.com")
        assert formatted == '"Maria \\"Admin\\" Garcia" <maria@example.com>'

    def test_format_empty_address(self):
        """Test formatting with empty email address."""
        formatted = format_address("Maria Garcia", "")
        assert formatted == ""

    def test_format_address_list(self):
        """Test formatting a list of addresses."""
        addresses = [
            {"name": "Maria Garcia", "email": "maria@example.com"},
            {"name": "", "email": "info@example.com"},
            {"name": "Support Team", "email": "support@example.com"},
        ]
        formatted = format_address_list(addresses)
        assert "Maria Garcia <maria@example.com>" in formatted
        assert "info@example.com" in formatted
        assert "Support Team <support@example.com>" in formatted
        assert formatted.count(", ") == 2  # Two commas separating three addresses

    def test_format_address_list_with_empty_entries(self):
        """Test formatting a list with some empty email addresses."""
        addresses = [
            {"name": "Maria Garcia", "email": "maria@example.com"},
            {"name": "Invalid", "email": ""},
            {"name": "Support Team", "email": "support@example.com"},
        ]
        formatted = format_address_list(addresses)
        assert "Maria Garcia <maria@example.com>" in formatted
        assert "Invalid" not in formatted
        assert "Support Team <support@example.com>" in formatted
        assert formatted.count(", ") == 1  # Only one comma for two valid addresses


class TestEmailComposition:
    """Tests for composing emails from JMAP data."""

    def test_compose_simple_text_email(self):
        """Test composing a simple text-only email."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Hello",
            "textBody": ["This is a simple text email"],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        # Parse the bytes result
        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        # Subject decoding might happen automatically, compare decoded
        subject_header = parsed["Subject"]
        decoded_subject = decode_header(subject_header)[0][0]
        assert decoded_subject == "Hello"
        assert parsed.get_content_maintype() == "text"
        assert parsed.get_content_subtype() == "plain"
        # Decode payload for assertion
        payload = parsed.get_payload(decode=True).decode(
            parsed.get_content_charset() or "utf-8"
        )
        assert "This is a simple text email" in payload

    def test_compose_html_email(self):
        """Test composing an HTML email."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Hello",
            "htmlBody": ["<h1>Hello World</h1><p>This is an HTML email</p>"],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        assert parsed["Subject"] == "Hello"
        assert parsed.get_content_type() == "text/html"
        payload = parsed.get_payload(decode=True).decode(
            parsed.get_content_charset() or "utf-8"
        )
        assert "<h1>Hello World</h1>" in payload
        assert "<p>This is an HTML email</p>" in payload

    def test_compose_multipart_alternative_email(self):
        """Test composing a multipart/alternative email with both text and HTML."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Hello",
            "textBody": ["This is the plain text version"],
            "htmlBody": ["<h1>Hello</h1><p>This is the HTML version</p>"],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        assert parsed["Subject"] == "Hello"
        assert parsed.get_content_type() == "multipart/alternative"

        parts = parsed.get_payload()
        assert len(parts) == 2

        text_part = parts[0]
        html_part = parts[1]

        assert text_part.get_content_type() == "text/plain"
        text_payload = text_part.get_payload(decode=True).decode(
            text_part.get_content_charset() or "utf-8"
        )
        assert "This is the plain text version" in text_payload

        assert html_part.get_content_type() == "text/html"
        html_payload = html_part.get_payload(decode=True).decode(
            html_part.get_content_charset() or "utf-8"
        )
        assert "<h1>Hello</h1>" in html_payload

    def test_compose_with_attachment(self):
        """Test composing an email with an attachment."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Email with Attachment",
            "textBody": ["Email with attachment"],
            "attachments": [
                {
                    "name": "test.txt",
                    "type": "text/plain",
                    "content": "SGVsbG8gV29ybGQ=",  # Base64 for "Hello World"
                }
            ],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        assert parsed["Subject"] == "Email with Attachment"
        assert parsed.get_content_type() == "multipart/mixed"

        parts = parsed.get_payload()
        # First part should be text, second part should be attachment
        assert len(parts) >= 2

        # Find the attachment part
        attachment_part = None
        for part in parts:
            if part.get_filename() == "test.txt":
                attachment_part = part
                break

        assert attachment_part is not None
        assert attachment_part.get_content_type() == "text/plain"
        # Content-Disposition should be attachment
        assert "attachment" in attachment_part.get("Content-Disposition", "")

    def test_compose_with_multiple_recipients(self):
        """Test composing an email with multiple recipients."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [
                {"name": "Jane Smith", "email": "jane@example.com"},
                {"name": "Bob Johnson", "email": "bob@example.com"},
            ],
            "cc": [{"name": "Alice", "email": "alice@example.com"}],
            "bcc": [{"name": "Secret", "email": "secret@example.com"}],
            "subject": "Email to Multiple Recipients",
            "textBody": ["Hello everyone!"],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert (
            parsed["To"]
            == "Jane Smith <jane@example.com>, Bob Johnson <bob@example.com>"
        )
        assert parsed["Cc"] == "Alice <alice@example.com>"
        assert parsed["Bcc"] == "Secret <secret@example.com>"
        assert parsed["Subject"] == "Email to Multiple Recipients"

    def test_compose_with_custom_headers(self):
        """Test composing an email with custom headers."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Email with Custom Headers",
            "textBody": ["Email with custom headers"],
            "headers": {
                "X-Custom-Header": "Custom Value",
                "X-Priority": "1",
                "X-Mailer": "Test Mailer",
            },
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        assert parsed["Subject"] == "Email with Custom Headers"
        assert parsed["X-Custom-Header"] == "Custom Value"
        assert parsed["X-Priority"] == "1"
        assert parsed["X-Mailer"] == "Test Mailer"

    def test_compose_with_unicode_headers(self):
        """Test composing an email with unicode headers."""
        jmap_data = {
            "from": [{"name": "José Martín", "email": "jose@example.com"}],
            "to": [{"name": "Søren Kierkegård", "email": "soren@example.com"}],
            "subject": "Hélló Wörld with ñ and é characters",
            "textBody": ["Unicode email content"],
            "headers": {"X-Custom-Header": "Ünicode Välue"},
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)

        # Decode headers before asserting content
        decoded_from = decode_header_string(parsed["From"])
        decoded_to = decode_header_string(parsed["To"])
        decoded_subject = decode_header_string(parsed["Subject"])
        decoded_custom = decode_header_string(parsed["X-Custom-Header"])

        assert "José Martín" in decoded_from
        assert "jose@example.com" in decoded_from
        assert "Søren Kierkegård" in decoded_to
        assert "soren@example.com" in decoded_to
        # Direct comparison should work after decoding
        assert decoded_subject == "Hélló Wörld with ñ and é characters"
        assert decoded_custom == "Ünicode Välue"

    def test_compose_with_reply_headers(self):
        """Test composing a reply email with appropriate headers."""
        jmap_data = {
            "subject": "Re: Original Subject",
            "from": {"name": "Replier", "email": "replier@example.com"},
            "to": [{"name": "Original Sender", "email": "original@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is a reply.",
                }
            ],
        }

        original_message_id = "<original123@example.com>"
        raw_email = compose_email(jmap_data, in_reply_to=original_message_id)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        assert msg["Subject"] == "Re: Original Subject"
        assert msg["In-Reply-To"] == "<original123@example.com>"
        assert msg["References"] == "<original123@example.com>"

    def test_compose_with_date(self):
        """Test composing an email with a specified date."""
        date = datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc)

        jmap_data = {
            "subject": "Email with Date",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "date": date,
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This email has a specified date.",
                }
            ],
        }

        raw_email = compose_email(jmap_data)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        # Verify the date format (RFC 5322 date format)
        date_pattern = r"Mon, 15 May 2023 14:30:00 [+-]\d{4}"
        assert re.match(date_pattern, msg["Date"]), (
            f"Date format incorrect: {msg['Date']}"
        )

    def test_compose_with_multiple_text_parts(self):
        """Test composing an email with multiple text body parts (expects only first)."""
        jmap_data = {
            "subject": "Multiple Text Parts",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is the first text part.",
                },
                {
                    "partId": "text-2",
                    "type": "text/plain",
                    "content": "This is the second text part.",
                },
            ],
        }

        raw_email = compose_email(jmap_data)
        msg = email.message_from_bytes(raw_email)

        assert msg["Subject"] == "Multiple Text Parts"

        # Expect a single text/plain part when only textBody is provided
        assert msg.get_content_maintype() == "text"
        assert msg.get_content_subtype() == "plain"

        payload = msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8"
        )
        # Check that it contains the content of the *first* part
        assert "This is the first text part." in payload
        # Check that it *doesn't* contain the second (unless concatenation is desired)
        assert "This is the second text part." not in payload

    def test_compose_with_binary_attachment_and_filename(self):
        """Test composing an email with a binary attachment with filename containing special characters."""
        # Create a sample PDF-like binary content
        attachment_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

        jmap_data = {
            "subject": "Email with PDF Attachment",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Please find the attached PDF file.",
                }
            ],
            "attachments": [
                {
                    "partId": "att-1",
                    "type": "application/pdf",
                    "name": "Report (2023) - Financé.pdf",
                    "content": attachment_content,
                }
            ],
        }

        raw_email = compose_email(jmap_data)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        assert msg["Subject"] == "Email with PDF Attachment"
        assert msg.is_multipart()

        # Check for the attachment with special characters in filename
        attachment_found = False
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                attachment_found = True
                filename = part.get_filename()
                assert "Report" in filename
                assert "2023" in filename
                assert "PDF" in filename.upper() or "pdf" in filename
                break

        assert attachment_found, "PDF attachment not found in the email"

    def test_compose_with_empty_subject(self):
        """Test composing an email with an empty subject."""
        jmap_data = {
            "subject": "",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This email has no subject.",
                }
            ],
        }

        raw_email = compose_email(jmap_data)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        # Subject should be empty or missing
        assert not msg["Subject"] or msg["Subject"] == ""
        assert "This email has no subject." in msg.get_payload()

    def test_compose_minimal_email(self):
        """Test composing a minimal email with only required fields."""
        jmap_data = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": "recipient@example.com"}],
            "textBody": [{"content": "Minimal email."}],
        }

        raw_email = compose_email(jmap_data)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        # Check minimal required headers
        assert msg["From"] == "sender@example.com"
        assert msg["To"] == "recipient@example.com"
        assert msg["Date"]  # Should have auto-generated date
        assert "Minimal email." in msg.get_payload()

    def test_compose_with_inline_images(self):
        """Test composing an email with inline images in HTML using JMAP format."""
        jmap_data = {
            "from": {"name": "John Doe", "email": "john@example.com"},
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Email with Inline Images",
            "htmlBody": [
                '<h1>Email with Image</h1><p>Here is an inline image: <img src="cid:image1@example.com"></p>'
            ],
            "attachments": [
                {  # Inline attachment
                    "name": "image.jpg",
                    "type": "image/jpeg",
                    "content": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
                    "cid": "image1@example.com",
                    "disposition": "inline",
                }
                # Add a test case with a regular attachment as well if needed
            ],
        }

        image_cid = "image1@example.com"  # Store expected CID
        jmap_data["attachments"][0]["cid"] = image_cid

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)
        assert parsed["From"] == "John Doe <john@example.com>"
        assert parsed["To"] == "Jane Smith <jane@example.com>"
        assert parsed["Subject"] == "Email with Inline Images"

        # Determine expected root type
        has_regular_attachments = any(
            att.get("disposition") != "inline" or not att.get("cid")
            for att in jmap_data["attachments"]
        )

        expected_root_type = (
            "multipart/mixed" if has_regular_attachments else "multipart/related"
        )

        assert parsed.get_content_type() == expected_root_type, (
            f"Root should be {expected_root_type}"
        )

        # Find the multipart/related structure (might be the root or inside mixed)
        related_part = None
        if parsed.get_content_type() == "multipart/related":
            related_part = parsed
        elif parsed.get_content_type() == "multipart/mixed":
            for part in parsed.walk():
                if part.get_content_type() == "multipart/related":
                    related_part = part
                    break

        assert related_part is not None, "multipart/related part not found"

        html_part = None
        image_part = None

        for part in related_part.walk():
            # Skip container parts
            if part.is_multipart():
                continue

            if (
                part.get_content_maintype() == "text"
                and part.get_content_subtype() == "html"
            ):
                html_part = part
            # Check for image content type OR matching Content-ID
            elif part.get_content_maintype() == "image":
                # Check if CID matches if present
                part_cid_header = part.get("Content-ID", "")
                if f"<{image_cid}>" in part_cid_header:
                    image_part = part
                elif image_part is None:  # Fallback: take the first image part found
                    image_part = part
            elif f"<{image_cid}>" in part.get(
                "Content-ID", ""
            ):  # Found by CID even if not image/* type
                image_part = part

        assert html_part is not None, "HTML part not found in related part"
        assert image_part is not None, (
            f"Image part with CID <{image_cid}> not found in related part"
        )

        # Check HTML references the image by Content-ID
        html_content = html_part.get_payload(decode=True).decode("utf-8")
        assert f'src="cid:{image_cid}"' in html_content

        # Check image has proper Content-ID header
        assert image_part.get("Content-ID") == f"<{image_cid}>"
        assert "inline" in image_part.get("Content-Disposition", "")
        assert "image.jpg" in image_part.get_filename()

    def test_compose_with_french_accents(self):
        """Test composing an email with French accented characters in both subject and content."""
        jmap_data = {
            "from": [{"name": "François Dupont", "email": "francois@example.com"}],
            "to": [{"name": "Amélie Poulain", "email": "amelie@example.com"}],
            "subject": "Réunion d'équipe à 15h",
            "textBody": [
                """Bonjour Amélie,
                J'espère que vous allez bien. 
                Pouvons-nous discuter du projet demain?
                
                Cordialement,
                François"""
            ],
            "htmlBody": [
                """<p>Bonjour Amélie,</p>
                <p>J'espère que vous allez bien. Pouvons-nous discuter du projet demain?</p>
                <p>Cordialement,<br>François</p>"""
            ],
        }

        result_bytes = compose_email(jmap_data)
        assert isinstance(result_bytes, bytes)

        parsed = BytesParser().parsebytes(result_bytes)

        # Decode headers
        decoded_from = decode_header_string(parsed["From"])
        decoded_to = decode_header_string(parsed["To"])
        decoded_subject = decode_header_string(parsed["Subject"])

        assert "François Dupont" in decoded_from
        assert "francois@example.com" in decoded_from
        assert "Amélie Poulain" in decoded_to
        assert "amelie@example.com" in decoded_to
        assert decoded_subject == "Réunion d'équipe à 15h"

        # Check content type and parts (as before)
        assert parsed.get_content_type() == "multipart/alternative"
        text_part = None
        html_part = None
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                text_part = part
            elif part.get_content_type() == "text/html":
                html_part = part

        assert text_part is not None
        assert html_part is not None

        text_content = text_part.get_payload(decode=True).decode("utf-8")
        html_content = html_part.get_payload(decode=True).decode("utf-8")

        assert "François" in text_content
        assert "Amélie" in text_content
        assert "J'espère" in text_content  # Check for apostrophe

        assert "François" in html_content
        assert "Amélie" in html_content
        # Check HTML has apostrophe, not entity
        assert "J'espère" in html_content
        assert "&rsquo;" not in html_content


class TestReplyGeneration:
    """Tests for creating reply messages."""

    def test_create_simple_reply(self):
        """Test creating a simple reply to an email."""
        original_message = {
            "subject": "Original Subject",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is the original message.",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        reply_text = "This is my reply."

        reply = create_reply_message(original_message, reply_text)

        assert reply["subject"] == "Re: Original Subject"
        assert reply["to"] == [
            {"name": "Original Sender", "email": "original@example.com"}
        ]
        assert len(reply["textBody"]) == 1
        assert reply["textBody"][0]["type"] == "text/plain"
        assert reply["textBody"][0]["content"].startswith("This is my reply.")
        assert "On Mon, 15 May 2023 14:30:00" in reply["textBody"][0]["content"]
        assert "Original Sender" in reply["textBody"][0]["content"]
        assert "> This is the original message." in reply["textBody"][0]["content"]

    def test_create_reply_with_html(self):
        """Test creating a reply with HTML content."""
        original_message = {
            "subject": "Original HTML Subject",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "htmlBody": [
                {
                    "partId": "html-1",
                    "type": "text/html",
                    "content": "<html><body><p>This is the original HTML message.</p></body></html>",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        reply_text = "This is my reply."
        reply_html = "<html><body><p>This is my HTML reply.</p></body></html>"

        reply = create_reply_message(original_message, reply_text, reply_html)

        assert reply["subject"] == "Re: Original HTML Subject"
        assert len(reply["textBody"]) == 1

        # Check text body content - includes reply text AND the quote header
        text_content = reply["textBody"][0]["content"]
        assert text_content.startswith("This is my reply.")
        # Check for the quote header components
        assert "On" in text_content
        assert "Original Sender" in text_content
        assert "wrote:" in text_content
        # Specifically check that NO lines start with "> " (quote marker)
        # because the original had no text part.
        assert not any(
            line.strip().startswith(">") for line in text_content.splitlines()
        )

        # Check HTML body
        assert len(reply["htmlBody"]) == 1
        assert reply["htmlBody"][0]["type"] == "text/html"
        html_content = reply["htmlBody"][0]["content"]
        assert "This is my HTML reply." in html_content
        assert "This is the original HTML message." in html_content  # Check quoted HTML
        assert "blockquote" in html_content  # Check blockquote tag

    def test_create_reply_without_quote(self):
        """Test creating a reply without quoting the original message."""
        original_message = {
            "subject": "Original Subject",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is the original message.",
                }
            ],
        }

        reply_text = "This is my reply without a quote."

        reply = create_reply_message(original_message, reply_text, include_quote=False)

        assert reply["subject"] == "Re: Original Subject"
        assert reply["textBody"][0]["content"] == "This is my reply without a quote."
        assert ">" not in reply["textBody"][0]["content"]  # No quote marker

    def test_create_reply_to_email_with_re_subject(self):
        """Test creating a reply to an email that already has 'Re:' in the subject."""
        original_message = {
            "subject": "Re: Already a Reply",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is already a reply.",
                }
            ],
        }

        reply_text = "This is my reply to a reply."

        reply = create_reply_message(original_message, reply_text)

        assert reply["subject"] == "Re: Already a Reply"  # Should not add another "Re:"
        assert reply["textBody"][0]["content"].startswith(
            "This is my reply to a reply."
        )

    def test_reply_with_multipart_original(self):
        """Test replying to a multipart email with both text and HTML."""
        original_message = {
            "subject": "Multipart Original",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This is the original plain text.",
                }
            ],
            "htmlBody": [
                {
                    "partId": "html-1",
                    "type": "text/html",
                    "content": "<html><body><p>This is the original <b>HTML</b> content.</p></body></html>",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        reply_text = "Here's my reply to your multipart email."
        reply_html = "<html><body><p>Here's my <i>HTML</i> reply to your multipart email.</p></body></html>"

        reply = create_reply_message(original_message, reply_text, reply_html)

        # Check basic structure
        assert reply["subject"] == "Re: Multipart Original"
        assert reply["to"] == [
            {"name": "Original Sender", "email": "original@example.com"}
        ]

        # Check text part with quote
        assert len(reply["textBody"]) == 1
        assert reply["textBody"][0]["content"].startswith("Here's my reply")
        assert "> This is the original plain text." in reply["textBody"][0]["content"]

        # Check HTML part with quote
        assert len(reply["htmlBody"]) == 1
        assert "<i>HTML</i> reply" in reply["htmlBody"][0]["content"]
        assert (
            "This is the original <b>HTML</b> content"
            in reply["htmlBody"][0]["content"]
        )
        assert "border-left: 1px solid #ccc" in reply["htmlBody"][0]["content"]

    def test_reply_with_long_original(self):
        """Test replying to a long email, ensuring proper quoting."""
        # Create a long message with multiple paragraphs
        long_text = "\n\n".join(
            [f"This is paragraph {i} of the original message." for i in range(1, 6)]
        )

        original_message = {
            "subject": "Long Original Email",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "textBody": [
                {"partId": "text-1", "type": "text/plain", "content": long_text}
            ],
            "date": datetime(2023, 6, 20, 10, 15, 0, tzinfo=timezone.utc),
        }

        reply_text = "Here's my short reply to your long email."

        reply = create_reply_message(original_message, reply_text)

        # Check text part with quote
        assert len(reply["textBody"]) == 1
        assert reply["textBody"][0]["content"].startswith("Here's my short reply")

        # Verify all paragraphs were quoted properly
        quoted_content = reply["textBody"][0]["content"]
        for i in range(1, 6):
            assert f"> This is paragraph {i}" in quoted_content

        # Make sure we have the right number of quote markers (>)
        assert quoted_content.count(">") >= 5  # At least one for each paragraph

    def test_reply_with_threading(self):
        """Test reply creation with proper email threading information."""
        original_message = {
            "subject": "Original for Threading",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "messageId": "<original-message-id-12345@example.com>",
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Original message for testing threading.",
                }
            ],
            "references": "<initial-ref@example.com> <another-ref@example.com>",
        }

        reply_text = "This reply should maintain threading information."

        reply = create_reply_message(original_message, reply_text)

        # Check that message ID is added to headers correctly
        assert "In-Reply-To" in reply["headers"]
        # Check value is formatted with angle brackets
        assert (
            reply["headers"]["In-Reply-To"] == "<original-message-id-12345@example.com>"
        )

        # Check References header includes original refs and the new In-Reply-To ID
        assert "References" in reply["headers"]
        assert "<initial-ref@example.com>" in reply["headers"]["References"]
        assert "<another-ref@example.com>" in reply["headers"]["References"]
        assert (
            "<original-message-id-12345@example.com>" in reply["headers"]["References"]
        )

    def test_reply_with_special_characters(self):
        """Test replying to an email with special characters in the subject and content."""
        original_message = {
            "subject": "Spécial Châracters & Symbols!",
            "from": {"name": "José García", "email": "jose@example.es"},
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Este mensaje contiene caracteres especiales: áéíóú ñ çãõ",
                }
            ],
        }

        reply_text = "Replying to your message with special characters."

        reply = create_reply_message(original_message, reply_text)

        # Check that special characters are preserved
        assert reply["subject"] == "Re: Spécial Châracters & Symbols!"
        assert reply["to"][0]["name"] == "José García"
        assert (
            "> Este mensaje contiene caracteres especiales: áéíóú ñ çãõ"
            in reply["textBody"][0]["content"]
        )

    def test_create_reply_with_empty_original(self):
        """Test creating a reply with an empty or minimal original message."""
        minimal_original = {
            "subject": "Minimal"
            # No 'from', 'date', etc.
        }

        reply_text = "Replying to minimal message."
        reply = create_reply_message(minimal_original, reply_text)

        assert reply["subject"] == "Re: Minimal"
        assert len(reply["textBody"]) == 1
        assert reply["textBody"][0]["content"].startswith(
            "Replying to minimal message."
        )

        # The 'to' field should be empty as original had no 'from' address
        assert "to" in reply
        assert not reply["to"]  # Check list is empty

        # Check quote header contains fallback text
        assert "On an unknown date, someone wrote:" in reply["textBody"][0]["content"]


class TestErrorHandling:
    """Tests for error handling in the RFC5322 composer."""

    def test_compose_with_invalid_data(self):
        """Test composing with invalid JMAP data raises appropriate exception."""
        invalid_data = {
            "subject": "Invalid Email",
            # Missing required 'from' field
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            # Invalid body data structure
            "textBody": "This is not an array as required",
        }

        with pytest.raises(EmailComposeError):
            compose_email(invalid_data)

    def test_compose_with_invalid_date(self):
        """Test composing with invalid date format."""
        jmap_data = {
            "subject": "Invalid Date",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "date": "Not a valid date string",
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "This has an invalid date.",
                }
            ],
        }

        # Should not raise an exception but use current date instead
        raw_email = compose_email(jmap_data)

        # Parse the generated email
        msg = email.message_from_bytes(raw_email)

        # Should have a valid date header despite invalid input
        assert msg["Date"] is not None
        assert msg["Date"] != "Not a valid date string"

    def test_create_reply_with_empty_original(self):
        """Test creating a reply with an empty or minimal original message."""
        minimal_original = {
            "subject": "Minimal"
            # No 'from', 'date', etc.
        }

        reply_text = "Replying to minimal message."
        reply = create_reply_message(minimal_original, reply_text)

        assert reply["subject"] == "Re: Minimal"
        assert len(reply["textBody"]) == 1
        assert reply["textBody"][0]["content"].startswith(
            "Replying to minimal message."
        )

        # The 'to' field should be empty as original had no 'from' address
        assert "to" in reply
        assert not reply["to"]  # Check list is empty

        # Check quote header contains fallback text
        assert "On an unknown date, someone wrote:" in reply["textBody"][0]["content"]

    def test_format_address_with_malformed_input(self):
        """Test formatting addresses with unusual or malformed input."""
        # Test with None values
        assert format_address(None, "user@example.com") == "user@example.com"
        assert format_address("User", None) == ""

        # Test with empty strings
        assert format_address("", "") == ""

        # Test with unusual email format (missing domain)
        assert "user-without-domain" in format_address("Test", "user-without-domain")

        # Test with extremely long name
        long_name = "A" * 100
        formatted = format_address(long_name, "long@example.com")
        assert long_name in formatted
        assert "long@example.com" in formatted

    def test_content_id_formatting_for_inline_images(self):
        """Test that Content-ID is properly formatted with angle brackets for inline images."""
        # Test cases with different Content-ID formats
        test_cases = [
            {"cid": "image123", "expected": "<image123>"},
            {"cid": "<image123>", "expected": "<image123>"},
            {"cid": "image123>", "expected": "<image123>"},
            {"cid": "<image123", "expected": "<image123>"},
        ]

        for case in test_cases:
            attachment = {
                "content": base64.b64encode(b"test image data").decode("utf-8"),
                "type": "image/jpeg",
                "name": "test.jpg",
                "disposition": "inline",
                "cid": case["cid"],
            }

            attachment_part = create_attachment_part(attachment)

            # Verify the attachment part was created
            assert attachment_part is not None

            # Verify the Content-ID header is correctly formatted
            assert attachment_part.headers["Content-ID"] == case["expected"], (
                f"Content-ID not properly formatted for input '{case['cid']}'"
            )


if __name__ == "__main__":
    pytest.main()
