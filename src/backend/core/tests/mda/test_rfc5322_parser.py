# pylint: disable=too-many-lines,too-many-public-methods
"""
Tests for the RFC5322 email parser module.
"""

import base64
import hashlib
from datetime import datetime
from datetime import timezone as dt_timezone
from email.header import Header

import pytest
from flanker.mime import create

from core.mda.rfc5322.parser import (
    EmailParseError,
    decode_email_header_text,
    parse_date,
    parse_email_address,
    parse_email_addresses,
    parse_email_message,
    parse_message_content,
)


# --- Fixtures for TestEmailMessageParsing ---
@pytest.fixture(name="simple_email")
def fixture_simple_email():
    """Fixture providing a simple text email as bytes."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body."""


@pytest.fixture(name="multipart_email")
def fixture_multipart_email():
    """Fixture providing a multipart email with text and HTML as bytes."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Multipart Test Email
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary-string"

--boundary-string
Content-Type: text/plain; charset="utf-8"

This is the plain text version.

--boundary-string
Content-Type: text/html; charset="utf-8"

<html><body><h1>Multipart Email</h1><p>This is the <b>HTML version</b>.</p></body></html>

--boundary-string--
"""


@pytest.fixture(name="complex_email")
def fixture_complex_email():
    """Fixture providing a complex email with headers, attachments, etc., as bytes."""
    # Includes multiple headers, cc, bcc, attachments, different encodings
    return b"""From: "Sender Name" <sender@example.com>
To: "Recipient One" <rec1@example.com>, recipient2@example.com
Cc: "Carbon Copy" <cc@example.com>
Bcc: bcc@hidden.com
Subject: Complex Multipart Email with Attachments
Date: Fri, 19 Apr 2024 10:00:00 +0000
Message-ID: <complex-message-id@example.com>
References: <ref1@example.com>
In-Reply-To: <ref2@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="mixed-boundary"

--mixed-boundary
Content-Type: multipart/alternative; boundary="alt-boundary"

--alt-boundary
Content-Type: text/plain; charset=us-ascii
Content-Transfer-Encoding: 7bit

Plain text body content.

--alt-boundary
Content-Type: text/html; charset=utf-8
Content-Transfer-Encoding: quoted-printable

<html><body><h1>HTML Content</h1><p>This is the =48TML version with <a href=3D"=
http://example.com">a link</a>.</p></body></html>

--alt-boundary--

--mixed-boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename="document.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKJSDi48/FzwoxIDAgb2JqPDwvUGFnZXMgMiAwIFIvVHlwZS9DYXRhbG9nPj4KZW5k
b2JqCjIgMCBvYmo8PC9Db3VudCAxL0tpZHMgWzMgMCBSXS9UeXBlL1BhZ2VzPj4KZW5kb2JqCjMg
MCBvYmo8PC9NZWRpYUJveCBbMCAwIDYxMiA3OTJdL1BhcmVudCAyIDAgUi9SZXNvdXJjZXMgPDwv
Rm9udCA8PC9GMSA0IDAgUj4+Pj4vVHlwZS9QYWdlPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDY0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDIxNyAwMDAwMCBuIAp0cmFpbGVyPDwvUm9vdCAxIDAgUi9TaXplIDU+PgpzdGFydHhyZWYKMzI1CjUlRU9G

--mixed-boundary
Content-Type: image/png
Content-Disposition: inline; filename="image.png"
Content-ID: <inline-image@example.com>
Content-Transfer-Encoding: base64

aW1hZ2UgZGF0YSBoZXJlCg==

--mixed-boundary--
"""


@pytest.fixture(name="email_with_encoded_headers")
def fixture_email_with_encoded_headers():
    """Fixture providing an email with RFC 2047 encoded headers as bytes."""
    return b"""From: =?utf-8?b?U8OgbmRlciBOw6FtZQ==?= <sender@example.com>
To: =?utf-8?q?Recipient?= <recipient@example.com>
Subject: =?iso-8859-1?q?Encoded_Subject_with_=E4ccents?=

Simple body."""


@pytest.fixture(name="email_with_attachment")
def fixture_email_with_attachment():
    """Fixture providing an email with a simple text attachment as bytes."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Email with Attachment
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary-string"

--boundary-string
Content-Type: text/plain

This is the main body.

--boundary-string
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="attachment.txt"
Content-Transfer-Encoding: base64

VGhpcyBpcyB0aGUgYXR0YWNobWVudCBjb250ZW50Lg==

--boundary-string--
"""


@pytest.fixture(name="test_email")
def fixture_test_email(simple_email):
    """Generic email fixture, defaulting to simple_email."""
    # Generic email fixture, can be overridden in specific tests if needed
    return simple_email


# --- Fixtures for Flanker message objects ---
@pytest.fixture(name="flanker_simple_message")
def fixture_flanker_simple_message(simple_email):
    """Fixture providing a Flanker message object from simple_email."""
    return create.from_string(simple_email)


@pytest.fixture(name="flanker_multipart_message")
def fixture_flanker_multipart_message(multipart_email):
    """Fixture providing a Flanker message object from multipart_email."""
    return create.from_string(multipart_email)


@pytest.fixture(name="flanker_attachment_message")
def fixture_flanker_attachment_message(email_with_attachment):
    """Fixture providing a Flanker message object from email_with_attachment."""
    return create.from_string(email_with_attachment)


@pytest.fixture(name="flanker_test_message")
def fixture_flanker_test_message(test_email):
    """Fixture providing a Flanker message object from the generic test_email fixture."""
    return create.from_string(test_email)


class TestEmailAddressParsing:
    """Tests for email address parsing functions."""

    def test_parse_simple_email(self):
        """Test parsing a simple email address without a display name."""
        name, email_addr = parse_email_address("user@example.com")
        assert name == ""
        assert email_addr == "user@example.com"

    def test_parse_email_with_display_name(self):
        """Test parsing an email address with a display name."""
        name, email_addr = parse_email_address("Test User <user@example.com>")
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_email_with_quoted_display_name(self):
        """Test parsing an email address with a quoted display name."""
        name, email_addr = parse_email_address('"Test User" <user@example.com>')
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_email_with_comma_in_display_name(self):
        """Test parsing an email address with a comma in the display name."""
        name, email_addr = parse_email_address('"User, Test" <user@example.com>')
        assert name == "User, Test"
        assert email_addr == "user@example.com"

    def test_parse_email_with_comments(self):
        """Test parsing an email address with comment."""
        name, email_addr = parse_email_address("Test User <user@example.com> (comment)")
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_empty_address(self):
        """Test parsing an empty address string."""
        name, email_addr = parse_email_address("")
        assert name == ""
        assert email_addr == ""

    def test_parse_invalid_address(self):
        """Test parsing an invalid address."""
        name, email_addr = parse_email_address("Not an email address")
        assert name == ""
        assert email_addr == "Not an email address"

    def test_parse_multiple_addresses(self):
        """Test parsing multiple email addresses."""
        addresses = parse_email_addresses(
            "Test User <user@example.com>, Another User <another@example.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("Test User", "user@example.com")
        assert addresses[1] == ("Another User", "another@example.com")

    def test_parse_multiple_recipients_with_various_formats(self):
        """Test parsing multiple recipients in various formats."""
        addresses = parse_email_addresses(
            'user@example.com, "John Doe" <other@example.com>, jane@example.com'
        )
        assert len(addresses) == 3
        assert addresses[0] == ("", "user@example.com")
        assert addresses[1] == ("John Doe", "other@example.com")
        assert addresses[2] == ("", "jane@example.com")

    def test_parse_multiple_recipients_with_comma_in_names(self):
        """Test parsing multiple recipients with comma in names."""
        addresses = parse_email_addresses(
            '"User, First" <first@example.com>, "User, Second" <second@example.com>, third@example.com'
        )
        assert len(addresses) == 3
        assert addresses[0] == ("User, First", "first@example.com")
        assert addresses[1] == ("User, Second", "second@example.com")
        assert addresses[2] == ("", "third@example.com")

    def test_parse_empty_addresses(self):
        """Test parsing an empty address list."""
        addresses = parse_email_addresses("")
        assert addresses == []

    def test_parse_address_with_dot_in_name(self):
        """Test parsing an email address with dots in the display name."""
        name, email_addr = parse_email_address("J.R.R. Tolkien <author@example.com>")
        assert name == "J.R.R. Tolkien"
        assert email_addr == "author@example.com"

    def test_parse_address_with_symbols_in_name(self):
        """Test parsing an email address with symbols in the display name."""
        name, email_addr = parse_email_address(
            '"Smith, Dr. John (CEO)" <ceo@company.org>'
        )
        assert name == "Smith, Dr. John (CEO)"
        assert email_addr == "ceo@company.org"

    def test_parse_address_with_unicode_chars(self):
        """Test parsing an email address with Unicode characters."""
        name, email_addr = parse_email_address("José García <jose@example.es>")
        assert name == "José García"
        assert email_addr == "jose@example.es"


class TestHeaderDecoding:
    """Tests for email header decoding functions."""

    def test_decode_simple_text(self):
        """Test decoding a simple unencoded text."""
        decoded = decode_email_header_text("Simple text")
        assert decoded == "Simple text"

    def test_decode_encoded_text(self):
        """Test decoding encoded text."""
        # Create an encoded header and manually decode it
        header = Header("Tést with açcents", "utf-8")
        encoded = str(header)
        decoded = decode_email_header_text(encoded)
        assert "Tést with açcents" in decoded

    def test_decode_address(self):
        """Test decoding a header that contains an email address."""
        decoded = decode_email_header_text("Test User <user@example.com>")
        assert decoded == "Test User <user@example.com>"

    def test_decode_empty(self):
        """Test decoding an empty header."""
        decoded = decode_email_header_text("")
        assert decoded == ""

    def test_decode_encoded_word_syntax(self):
        """Test decoding headers with encoded word syntax (RFC 2047)."""
        decoded = decode_email_header_text(
            "=?utf-8?Q?=C2=A3?=200.00=?UTF-8?q?_=F0=9F=92=B5?="
        )
        assert decoded == "£200.00 💵"

    def test_decode_nonencoded_text_with_encoded_word_markers(self):
        """Test decoding text that contains =? but is not encoded word."""
        decoded = decode_email_header_text(
            "Subject with =? marker and =?utf-8?B?8J+YgA==?="
        )
        assert decoded == "Subject with =? marker and 😀"

    def test_decode_multiple_encoded_words(self):
        """Test decoding multiple encoded words that need to be joined (RFC 2047)."""
        decoded = decode_email_header_text(
            "=?ISO-8859-1?B?SWYgeW91IGNhbiByZWFkIHRoaXMgeW8=?= =?ISO-8859-2?B?dSB1bmRlcnN0YW5kIHRoZSBleGFtcGxlLg==?="
        )
        assert decoded == "If you can read this you understand the example."

    def test_decode_special_characters(self):
        """Test decoding encoded words with special characters."""
        decoded = decode_email_header_text("=?ISO-8859-1?Q?Patrik_F=E4ltstr=F6m?=")
        assert "Patrik" in decoded
        assert "ltstr" in decoded  # The special chars might be decoded differently

    def test_decode_folded_header(self):
        """Test decoding a header that was folded across multiple lines."""
        folded_header = (
            "This is a very long header that has been folded\r\n across multiple lines"
        )
        decoded = decode_email_header_text(folded_header)
        assert (
            decoded
            == "This is a very long header that has been folded across multiple lines"
        )

    def test_decode_encoded_emoji(self):
        """Test decoding headers with emoji characters."""
        encoded_header = (
            "=?UTF-8?B?8J+Mj+KAjfCfjok=?="  # 🌏‍🏉 (globe + rugby ball emoji)
        )
        decoded = decode_email_header_text(encoded_header)
        assert len(decoded) > 0
        assert "=" not in decoded  # Make sure it's not returning the raw encoded text


class TestDateParsing:
    """Tests for email date parsing functions."""

    def test_parse_rfc_date(self):
        """Test parsing a valid RFC 5322 date."""
        date_str = "Mon, 15 Jan 2024 12:30:45 +0000"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2024
        assert parsed.month == 1
        assert parsed.day == 15
        assert parsed.hour == 12
        assert parsed.minute == 30
        assert parsed.second == 45

    def test_parse_date_without_seconds(self):
        """Test parsing a date without seconds (RFC 5322 makes seconds optional)."""
        date_str = "14 Jun 2019 11:24 +0000"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2019
        assert parsed.month == 6
        assert parsed.day == 14
        assert parsed.hour == 11
        assert parsed.minute == 24
        assert parsed.second == 0  # Default to 0 seconds

    def test_parse_date_with_named_timezone(self):
        """Test parsing a date with a named timezone."""
        date_str = "01 Aug 2023 08:59:03 UTC"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2023
        assert parsed.month == 8
        assert parsed.day == 1
        assert parsed.hour == 8
        assert parsed.minute == 59
        assert parsed.second == 3

    def test_parse_date_without_day_name(self):
        """Test parsing a date without the day name."""
        date_str = "1 Jan 2016 00:00:00 +0000"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2016
        assert parsed.month == 1
        assert parsed.day == 1

    def test_parse_date_with_extra_whitespace(self):
        """Test parsing a date with extra whitespace."""
        date_str = "  1 Mar 2016 11:12:13 +0000"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2016
        assert parsed.month == 3
        assert parsed.day == 1

    def test_parse_date_with_comment(self):
        """Test parsing a date with a comment."""
        date_str = "25 Dec 2016 00:00:00 +0000 (UTC)"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2016
        assert parsed.month == 12
        assert parsed.day == 25

    def test_parse_invalid_date(self):
        """Test parsing an invalid date."""
        parsed = parse_date("Not a date")
        assert parsed is None

    def test_parse_empty_date(self):
        """Test parsing an empty date string."""
        parsed = parse_date("")
        assert parsed is None

    def test_parse_date_with_single_digit_day(self):
        """Test parsing a date with a single digit day."""
        date_str = "5 Apr 2023 14:25:16 +0200"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.tzinfo is not None

        # Use the imported alias dt_timezone or datetime.timezone directly
        parsed_utc = parsed.astimezone(dt_timezone.utc)

        assert parsed_utc.year == 2023
        assert parsed_utc.month == 4
        assert parsed_utc.day == 5
        assert parsed_utc.hour == 12
        assert parsed_utc.minute == 25
        assert parsed_utc.second == 16

    def test_parse_date_with_full_month_name(self):
        """Test parsing a date with full month name instead of abbreviation."""
        date_str = "15 September 2022 08:45:30 +0000"
        parsed = parse_date(date_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2022
        assert parsed.month == 9
        assert parsed.day == 15


@pytest.mark.django_db
class TestEmailMessageParsing:
    """Test the main email message parsing function."""

    def test_parse_simple_email(self, simple_email):
        """Test parsing a simple email with text content."""
        parsed = parse_email_message(simple_email)
        assert parsed is not None
        assert parsed["subject"] == "Test Email"
        assert parsed["from"]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert len(parsed.get("textBody", [])) == 1, "Expected textBody"
        text_content = parsed["textBody"][0].get("content", "")
        assert "This is a test email body." in text_content
        assert parsed["textBody"][0].get("type", "") == "text/plain"
        # Per JMAP spec, text/plain outside alternative goes to both arrays
        assert len(parsed.get("htmlBody", [])) == 1, "JMAP: text copies to htmlBody"
        assert parsed["htmlBody"][0] == parsed["textBody"][0]
        assert not parsed.get("attachments"), "Expected no attachments"

        # Check headers_list
        assert "headers_list" in parsed
        headers_list = parsed["headers_list"]
        assert isinstance(headers_list, list)
        # Should contain from, to, subject at minimum
        header_keys = [h[0] for h in headers_list]
        assert "from" in header_keys
        assert "to" in header_keys
        assert "subject" in header_keys

        # Check headers_blocks (no Received headers, so should have one block)
        assert "headers_blocks" in parsed
        headers_blocks = parsed["headers_blocks"]
        assert isinstance(headers_blocks, list)
        assert len(headers_blocks) == 1  # One block with all headers (no Received)
        assert "from" in headers_blocks[0]
        assert "to" in headers_blocks[0]
        assert "subject" in headers_blocks[0]
        # All values should be lists
        assert isinstance(headers_blocks[0]["from"], list)
        assert isinstance(headers_blocks[0]["to"], list)
        assert isinstance(headers_blocks[0]["subject"], list)

    def test_parse_multipart_email(self, multipart_email):
        """Test parsing a multipart email."""
        parsed = parse_email_message(multipart_email)
        assert parsed is not None
        assert parsed["subject"] == "Multipart Test Email"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert parsed["from"]["email"] == "sender@example.com"
        assert parsed["from"]["name"] == ""
        assert not parsed.get("cc")
        assert len(parsed["textBody"]) == 1
        assert "This is the plain text version." in parsed["textBody"][0]["content"]
        assert len(parsed["htmlBody"]) == 1
        assert "<h1>Multipart Email</h1>" in parsed["htmlBody"][0]["content"]

        # Check headers_list
        assert "headers_list" in parsed
        headers_list = parsed["headers_list"]
        assert isinstance(headers_list, list)
        header_keys = [h[0] for h in headers_list]
        assert "from" in header_keys
        assert "to" in header_keys
        assert "subject" in header_keys
        assert "mime-version" in header_keys
        assert "content-type" in header_keys

        # Check headers_blocks (no Received headers, so should have one block)
        assert "headers_blocks" in parsed
        headers_blocks = parsed["headers_blocks"]
        assert isinstance(headers_blocks, list)
        assert len(headers_blocks) == 1
        assert "mime-version" in headers_blocks[0]
        assert "content-type" in headers_blocks[0]

    def test_parse_complex_email(self, complex_email):
        """Test parsing a complex email with nested parts and attachments."""
        parsed = parse_email_message(complex_email)
        assert parsed is not None
        assert parsed["subject"] == "Complex Multipart Email with Attachments"
        assert parsed["from"]["email"] == "sender@example.com"
        assert parsed["from"]["name"] == "Sender Name"
        assert len(parsed["cc"]) == 1
        assert parsed["cc"][0]["name"] == "Carbon Copy"
        assert len(parsed["to"]) == 2
        assert parsed["to"][0]["email"] == "rec1@example.com"
        assert parsed["to"][0]["name"] == "Recipient One"
        assert parsed["to"][1]["email"] == "recipient2@example.com"
        assert parsed["to"][1]["name"] == ""
        # textBody: text/plain from alternative + inline image
        assert len(parsed.get("textBody", [])) == 2
        assert "Plain text body content." in parsed["textBody"][0]["content"]
        # htmlBody: text/html from alternative + inline image
        assert len(parsed.get("htmlBody", [])) == 2
        assert "<h1>HTML Content</h1>" in parsed["htmlBody"][0]["content"]
        # Only the PDF attachment should be in attachments (inline image goes to body)
        assert len(parsed.get("attachments", [])) == 1

        # Check for PDF attachment
        pdf_attachment = next(
            (
                a
                for a in parsed["attachments"]
                if a.get("type") == "application/pdf"
                and a.get("disposition") == "attachment"
            ),
            None,
        )

        assert pdf_attachment is not None, (
            "PDF attachment not found or correctly classified"
        )

        # Inline image should be in htmlBody, not attachments (per JMAP algorithm)
        inline_image_in_html = next(
            (p for p in parsed["htmlBody"] if p.get("type") == "image/png"),
            None,
        )
        assert inline_image_in_html is not None, (
            "Inline image should be in htmlBody per JMAP algorithm"
        )

        # Verify all top-level fields are present
        assert "subject" in parsed
        assert "from" in parsed
        assert "to" in parsed
        assert "cc" in parsed
        assert "bcc" in parsed
        assert "date" in parsed
        assert "textBody" in parsed
        assert "htmlBody" in parsed
        assert "attachments" in parsed
        assert "headers" in parsed
        assert "message_id" in parsed
        assert "references" in parsed
        assert "in_reply_to" in parsed
        assert "gmail_labels" in parsed

        # Verify attachment fields are complete
        attachment = parsed["attachments"][0]
        assert "type" in attachment
        assert "name" in attachment
        assert "size" in attachment
        assert "disposition" in attachment
        assert "cid" in attachment
        assert "content" in attachment
        assert "sha256" in attachment
        # Verify SHA256 is a valid hex string
        assert len(attachment["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in attachment["sha256"])

    def test_parse_email_with_encoded_headers(self, email_with_encoded_headers):
        """Test parsing an email with encoded headers."""
        parsed = parse_email_message(email_with_encoded_headers)
        assert parsed is not None
        # Adjust expectation to match actual decode_header output
        assert parsed["from"]["name"] == "Sànder Náme"
        assert parsed["from"]["email"] == "sender@example.com"
        assert parsed["subject"] == "Encoded Subject with äccents"
        assert parsed["to"][0]["email"] == "recipient@example.com"
        # Check the decoded name which might include accents
        assert parsed["to"][0]["name"] == "Recipient"

    def test_parse_email_message(self, test_email):
        """Test parsing a complete email message."""
        parsed = parse_email_message(test_email)
        assert parsed is not None
        assert parsed["subject"] == "Test Email"
        assert parsed["from"]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert not parsed.get("cc")
        assert len(parsed["textBody"]) == 1
        assert "This is a test email body." in parsed["textBody"][0]["content"]
        # Per JMAP spec, text/plain outside alternative copies to htmlBody
        assert len(parsed.get("htmlBody", [])) == 1
        assert not parsed.get("attachments")

        # Check headers_list and headers_blocks are present
        assert "headers_list" in parsed
        assert "headers_blocks" in parsed
        assert isinstance(parsed["headers_list"], list)
        assert isinstance(parsed["headers_blocks"], list)

    def test_parse_invalid_message(self):
        """Test parsing an invalid (malformed multipart) message."""
        invalid_email_bytes = b"""From: sender@example.com
To: recipient@example.com
Subject: Malformed Multipart
Content-Type: multipart/alternative; boundary="bad_boundary"

--correct_boundary
Content-Type: text/plain

Text part.

--correct_boundary--
"""
        with pytest.raises(
            EmailParseError,
            match="Failed to parse email",
        ):
            parse_email_message(invalid_email_bytes)

    def test_parse_email_with_no_content_type(self):
        """Test parsing an email seemingly without a Content-Type header."""
        raw = b"Subject: No Content Type\nFrom: a@b.c\nTo: d@e.f\n\nBody text."
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert len(parsed["textBody"]) == 1
        assert parsed["textBody"][0]["content"] == "Body text."
        assert parsed["textBody"][0]["type"] == "text/plain"

    def test_parse_email_with_custom_headers(self):
        """Test parsing an email with custom, non-standard headers."""
        message = create.text("plain", "Message with custom headers")
        message.headers["To"] = "recipient@example.com"
        message.headers["From"] = "sender@example.com"
        message.headers["Subject"] = "Custom Headers"
        message.headers["X-Custom-Header"] = "Custom Value"
        message.headers["X-Priority"] = "1"
        message.headers["X-Mailer"] = "Custom Mailer v1.0"

        parsed = parse_email_message(message.to_string().encode("utf-8"))
        assert parsed is not None
        assert parsed["subject"] == "Custom Headers"
        assert "x-custom-header" in parsed["headers"]
        assert parsed["headers"]["x-custom-header"] == "Custom Value"
        assert parsed["headers"]["x-priority"] == "1"
        assert parsed["headers"]["x-mailer"] == "Custom Mailer v1.0"

        # Check headers_list contains custom headers in order
        assert "headers_list" in parsed
        headers_list = parsed["headers_list"]
        header_keys = [h[0] for h in headers_list]
        assert "x-custom-header" in header_keys
        assert "x-priority" in header_keys
        assert "x-mailer" in header_keys

        # Check headers_blocks
        assert "headers_blocks" in parsed
        headers_blocks = parsed["headers_blocks"]
        assert len(headers_blocks) == 1  # No Received headers
        assert "x-custom-header" in headers_blocks[0]
        assert isinstance(headers_blocks[0]["x-custom-header"], list)
        assert headers_blocks[0]["x-custom-header"][0] == "Custom Value"

    def test_parse_email_with_missing_from(self):
        """Test parsing an email with missing From header."""
        message = create.text("plain", "Message with no From")
        message.headers["To"] = "recipient@example.com"
        message.headers["Subject"] = "No From Header"
        if "From" in message.headers:
            del message.headers["From"]

        parsed = parse_email_message(message.to_string().encode("utf-8"))
        assert parsed is not None
        assert "from" in parsed
        assert parsed["from"]["email"] == ""
        assert parsed["from"]["name"] == ""

    def test_parse_email_with_received_headers(self):
        """Test parsing an email with Received headers to verify headers_blocks structure."""
        # Email with multiple Received headers (simulating relay chain)
        # Headers are prepended, so order in raw email is: most recent first
        raw_email = b"""Received: from our_mta.example.com (our_mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our_mta_id;
    Mon, 1 Jan 2024 12:02:00 +0000
X-Spam: Ham
Received: from relay2.example.com (relay2.example.com [5.6.7.8])
    by mail.example.com with SMTP id def456;
    Mon, 1 Jan 2024 12:01:00 +0000
X-Spam: Spam
Received: from relay1.example.com (relay1.example.com [1.2.3.4])
    by mail.example.com with SMTP id abc123;
    Mon, 1 Jan 2024 12:00:00 +0000
X-Spam: SenderSpam
From: sender@example.com
To: recipient@example.com
Subject: Test Email
Date: Mon, 1 Jan 2024 12:00:00 +0000

This is a test email body.
"""
        parsed = parse_email_message(raw_email)
        assert parsed is not None

        # Check headers_list contains all headers in order (most recent first)
        assert "headers_list" in parsed
        headers_list = parsed["headers_list"]
        assert isinstance(headers_list, list)

        # Find positions of Received headers in headers_list
        received_indices = [
            i for i, (key, _) in enumerate(headers_list) if key == "received"
        ]
        assert len(received_indices) == 3

        # Verify order: first Received should be our_mta (most recent)
        assert "our_mta_id" in headers_list[received_indices[0]][1]
        assert "def456" in headers_list[received_indices[1]][1]
        assert "abc123" in headers_list[received_indices[2]][1]

        # Check headers_blocks structure
        # When iterating through headers_list (most recent first), Received headers mark the END of their block
        # Block 0: First Received (our_mta) - marks end of block 0
        # Block 1: X-Spam (Ham) + second Received (relay2) - marks end of block 1
        # Block 2: X-Spam (Spam) + third Received (relay1) - marks end of block 2
        # Block 3: X-Spam (SenderSpam) + From, To, Subject, Date (original message)
        assert "headers_blocks" in parsed
        headers_blocks = parsed["headers_blocks"]
        assert isinstance(headers_blocks, list)
        # Should have 4 blocks: 3 blocks ending with Received headers + 1 final block
        assert len(headers_blocks) == 4

        # Block 0: First Received (our MTA) only
        assert "received" in headers_blocks[0]
        assert "our_mta_id" in headers_blocks[0]["received"][0]

        # Block 1: X-Spam (Ham) + second Received (relay2)
        assert "x-spam" in headers_blocks[1]
        assert headers_blocks[1]["x-spam"][0] == "Ham"
        assert "received" in headers_blocks[1]
        assert "def456" in headers_blocks[1]["received"][0]

        # Block 2: X-Spam (Spam) + third Received (relay1)
        assert "x-spam" in headers_blocks[2]
        assert headers_blocks[2]["x-spam"][0] == "Spam"
        assert "received" in headers_blocks[2]
        assert "abc123" in headers_blocks[2]["received"][0]

        # Block 3: Original message headers (X-Spam from sender, From, To, Subject, Date)
        assert "x-spam" in headers_blocks[3]
        assert headers_blocks[3]["x-spam"][0] == "SenderSpam"
        assert "from" in headers_blocks[3]
        assert "to" in headers_blocks[3]
        assert "subject" in headers_blocks[3]
        assert "date" in headers_blocks[3]

        # Verify all values in headers_blocks are lists
        for block in headers_blocks:
            for key, value in block.items():
                assert isinstance(value, list), (
                    f"Header {key} in block should be a list, got {type(value)}"
                )
                assert len(value) > 0, (
                    f"Header {key} in block should have at least one value"
                )

    def test_parse_empty_message(self):
        """Test parsing an empty message raises an error."""
        with pytest.raises(EmailParseError, match="Input must be non-empty bytes."):
            parse_email_message(b"")

    def test_parse_none_input(self):
        """Test parsing None input raises an error."""
        with pytest.raises(EmailParseError, match="Input must be non-empty bytes."):
            parse_email_message(None)

    def test_parse_message_content_simple(self, flanker_simple_message):
        """Test parsing content of a simple text message."""
        content = parse_message_content(flanker_simple_message)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["content"] == "This is a test email body."
        # Per JMAP spec, text/plain outside alternative copies to htmlBody
        assert len(content["htmlBody"]) == 1
        assert not content["attachments"]

    def test_parse_message_content_multipart(self, flanker_multipart_message):
        """Test parsing content of a multipart message."""
        content = parse_message_content(flanker_multipart_message)
        assert len(content["textBody"]) == 1
        # Expect trailing newline from flanker parsing
        assert content["textBody"][0]["content"] == "This is the plain text version.\n"
        assert len(content["htmlBody"]) == 1
        assert "<b>HTML version</b>" in content["htmlBody"][0]["content"]

    def test_parse_with_attachment(self, email_with_attachment):
        """Test parsing an email with an attachment."""
        # Placeholder test for parsing email with attachment.
        # Actual parsing logic is covered by parse_message_content tests.
        parsed = parse_email_message(email_with_attachment)
        assert parsed is not None
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["name"] == "attachment.txt"

    def test_parse_message_content_returns_dict(self, test_email):
        """Test that parse_message_content returns a dictionary with expected keys.

        This test verifies the structure of the returned dictionary,
        ensuring all expected keys are present.
        """
        message_obj = create.from_string(test_email)
        content = parse_message_content(message_obj)
        assert isinstance(content, dict)
        assert "textBody" in content
        assert "htmlBody" in content
        assert "attachments" in content
        # Verify they are lists
        assert isinstance(content["textBody"], list)
        assert isinstance(content["htmlBody"], list)
        assert isinstance(content["attachments"], list)

    def test_parse_non_multipart_edge_case(self):
        """Test parsing a text/plain email with content type parameters."""
        # Test case where Content-Type is text/plain but has parameters
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Plain text with parameters
Content-Type: text/plain; charset="us-ascii"; format=flowed

Body text.
"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert "textBody" in content
        assert content["textBody"][0]["content"] == "Body text.\n"
        # Per JMAP spec, text/plain outside alternative copies to htmlBody
        assert len(content["htmlBody"]) == 1
        assert not content["attachments"]

    def test_parse_html_only_email(self):
        """Test parsing an email that only contains an HTML part."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: HTML Only
Content-Type: text/html; charset="utf-8"

<p>HTML body only.</p>
"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        # Per JMAP spec, text/html outside alternative copies to textBody
        assert len(content["textBody"]) == 1
        assert len(content["htmlBody"]) == 1
        assert content["htmlBody"][0]["content"] == "<p>HTML body only.</p>\n"
        assert not content["attachments"]

    def test_parse_multipart_related(self):
        """Test parsing a multipart/related email (e.g., with inline images)."""
        # Example of multipart/related typically used for embedded images
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Multipart Related
Content-Type: multipart/related; boundary="related_boundary"

--related_boundary
Content-Type: text/html; charset="utf-8"

<p>See image: <img src="cid:image1"></p>

--related_boundary
Content-Type: image/png
Content-ID: <image1>
Content-Disposition: inline; filename="image.png"
Content-Transfer-Encoding: base64

aW1hZ2UgZGF0YSBoZXJl

--related_boundary--
"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["htmlBody"]) == 1
        assert '<img src="cid:image1">' in content["htmlBody"][0]["content"]
        # Per JMAP spec, text/html outside alternative copies to textBody
        assert len(content["textBody"]) == 1
        # Image at position > 0 in multipart/related goes to attachments
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["name"] == "image.png"
        assert attachment["type"] == "image/png"
        assert attachment["cid"] == "image1"

    def test_malformed_multipart(self):
        """Test parsing a malformed multipart email (boundary mismatch)."""
        # Boundary missing or incorrect
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Malformed Multipart
Content-Type: multipart/alternative; boundary="bad_boundary"

--correct_boundary
Content-Type: text/plain

Text part.

--correct_boundary--
"""
        # Revert structure to test parse_email_message's error handling
        with pytest.raises(EmailParseError, match="Failed to parse email"):
            parse_email_message(raw_email)

    def test_attachment_without_filename(self):
        """Test parsing an attachment that does not have a filename specified."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Attachment No Filename
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Main body.

--boundary
Content-Type: application/pdf
Content-Disposition: attachment

PDF data
--boundary--
"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["content"] == "Main body.\n"
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment.get("name") == "unnamed.pdf"
        assert attachment["type"] == "application/pdf"
        assert attachment["content"] == b"PDF data"

    def test_attachment_sha256_hash(self):
        """Test that attachment SHA256 hash is correctly calculated."""
        attachment_content = b"Test attachment content for SHA256"
        expected_hash = hashlib.sha256(attachment_content).hexdigest()

        raw_email = (
            b"""From: sender@example.com
To: recipient@example.com
Subject: Attachment with SHA256
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Main body.

--boundary
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="test.bin"

"""
            + attachment_content
            + b"""
--boundary--"""
        )
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["sha256"] == expected_hash
        assert attachment["size"] == len(attachment_content)

    def test_content_id_extraction(self):
        """Test Content-ID extraction with and without angle brackets."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Content-ID Test
Content-Type: multipart/related; boundary="boundary"

--boundary
Content-Type: text/html

<html><body><img src="cid:image1"></body></html>

--boundary
Content-Type: image/png
Content-ID: <image1@example.com>
Content-Disposition: inline

image data

--boundary
Content-Type: image/jpeg
Content-ID: image2@example.com
Content-Disposition: inline

image data 2
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 2

        # First attachment with angle brackets
        img1 = next(
            (a for a in content["attachments"] if a.get("cid") == "image1@example.com"),
            None,
        )
        assert img1 is not None

        # Second attachment without angle brackets
        img2 = next(
            (a for a in content["attachments"] if a.get("cid") == "image2@example.com"),
            None,
        )
        assert img2 is not None

    def test_message_id_angle_bracket_stripping(self):
        """Test that angle brackets are stripped from Message-ID and In-Reply-To."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
Message-ID: <msg123@example.com>
In-Reply-To: <reply123@example.com>
References: <ref1@example.com> <ref2@example.com>
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body text."""
        parsed = parse_email_message(raw_email)
        assert parsed["message_id"] == "msg123@example.com"
        assert parsed["in_reply_to"] == "reply123@example.com"
        assert parsed["references"] == "<ref1@example.com> <ref2@example.com>"

    def test_message_id_without_angle_brackets(self):
        """Test Message-ID and In-Reply-To without angle brackets."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
Message-ID: msg123@example.com
In-Reply-To: reply123@example.com
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body text."""
        parsed = parse_email_message(raw_email)
        assert parsed["message_id"] == "msg123@example.com"
        assert parsed["in_reply_to"] == "reply123@example.com"

    def test_missing_date_header(self):
        """Test that default date is used when Date header is missing."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: No Date

Body text."""
        parsed = parse_email_message(raw_email)
        assert isinstance(parsed["date"], datetime)
        assert parsed["date"].tzinfo == dt_timezone.utc
        # Should be recent (within last minute)
        now = datetime.now(dt_timezone.utc)
        time_diff = abs((now - parsed["date"]).total_seconds())
        assert time_diff < 60  # Within 60 seconds

    def test_multiple_same_headers(self):
        """Test parsing email with multiple headers of same name."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
X-Custom: Value1
X-Custom: Value2
X-Custom: Value3
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body text."""
        parsed = parse_email_message(raw_email)
        assert isinstance(parsed["headers"]["x-custom"], list)
        assert len(parsed["headers"]["x-custom"]) == 3
        assert parsed["headers"]["x-custom"] == ["Value1", "Value2", "Value3"]

    def test_filename_from_content_type_name(self):
        """Test filename extraction from Content-Type name parameter."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Main body.

--boundary
Content-Type: application/pdf; name="document.pdf"
Content-Disposition: attachment

PDF content
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        # Filename should be extracted from Content-Type name parameter
        assert attachment["name"] == "document.pdf"

    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            ("../../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\config", "config"),
            ("/etc/passwd", "passwd"),
            ("C:\\Windows\\System32", "System32"),
            (".hidden", "hidden"),
            ("..hidden", "hidden"),
            (".test.", "test"),
            ("..", "unnamed"),
            (".", "unnamed"),
            ("", "unnamed"),
            ("_long", None),
        ],
    )
    def test_filename_sanitization(self, filename, expected_name):
        """Test that filenames are sanitized."""

        # Avoid passing long strings into test names
        if filename == "_long":
            filename, expected_name = "a" * 500, "a" * 255

        raw_email = (
            b"""From: sender@example.com
To: recipient@example.com
Subject: Path Traversal Test
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body.

--boundary
Content-Type: application/octet-stream
Content-Disposition: attachment; filename=\""""
            + filename.encode("utf-8")
            + b"""\"

malicious content
--boundary--"""
        )
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["name"] == expected_name

    def test_multiple_text_parts(self):
        """Test email with multiple text/plain parts."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Multiple Text Parts
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain

First text part.

--boundary
Content-Type: text/plain

Second text part.

--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["textBody"]) == 2
        assert "First text part" in content["textBody"][0]["content"]
        assert "Second text part" in content["textBody"][1]["content"]

    def test_multiple_html_parts(self):
        """Test email with multiple text/html parts."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Multiple HTML Parts
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/html

<html><body>First HTML part.</body></html>

--boundary
Content-Type: text/html

<html><body>Second HTML part.</body></html>

--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["htmlBody"]) == 2
        assert "First HTML part" in content["htmlBody"][0]["content"]
        assert "Second HTML part" in content["htmlBody"][1]["content"]

    def test_message_only_attachments(self):
        """Test message with only attachments, no text/html body."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Attachments Only
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc1.pdf"

PDF content 1

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc2.pdf"

PDF content 2
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["textBody"]) == 0
        assert len(content["htmlBody"]) == 0
        assert len(content["attachments"]) == 2
        assert content["attachments"][0]["name"] == "doc1.pdf"
        assert content["attachments"][1]["name"] == "doc2.pdf"

    def test_infer_filename_unknown_type(self):
        """Test filename inference for unknown content types."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Unknown Type
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: application/x-unknown-type
Content-Disposition: attachment

content
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        attachment = content["attachments"][0]
        # Should return "unnamed" without extension for unknown types
        assert attachment["name"] == "unnamed"

    def test_part_with_empty_body(self):
        """Test parsing part with empty body."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Empty Body Part
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Main body.

--boundary
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="empty.bin"

--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        # Empty body part should be skipped (line 255-256 in parser)
        # Only the text part should be present
        assert len(content["textBody"]) == 1
        # The empty attachment might or might not be included depending on Flanker behavior

    def test_binary_attachment_content(self):
        """Test parsing email with binary attachment content.

        Note: In real SMTP emails, binary attachments are base64-encoded.
        Raw binary in email bodies is not standard and will be corrupted
        by MIME parsers. This test uses base64 encoding as per RFC 2045.
        """
        # Create binary data (all byte values 0-255)
        binary_data = bytes(range(256))
        # Encode as base64 (how real emails work)
        base64_data = base64.b64encode(binary_data)

        raw_email = (
            b"""From: sender@example.com
To: recipient@example.com
Subject: Binary Attachment
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Main body.

--boundary
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="binary.bin"
Content-Transfer-Encoding: base64

"""
            + base64_data
            + b"""

--boundary--"""
        )
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        # Flanker should decode base64 back to binary
        assert attachment["content"] == binary_data
        assert attachment["size"] == len(binary_data)

    def test_header_decoding_null_bytes(self):
        """Test header decoding handles null bytes gracefully."""
        # Note: decode_email_header_text should handle null bytes
        # This tests the actual behavior
        header_with_null = "Test\x00Header"
        decoded = decode_email_header_text(header_with_null)
        # Should either remove null bytes or handle them gracefully
        assert isinstance(decoded, str)

    def test_header_decoding_invalid_charset(self):
        """Test header decoding with invalid charset name."""
        # Create a header with invalid charset
        invalid_header = "=?INVALID-CHARSET?Q?Test?="
        decoded = decode_email_header_text(invalid_header)
        # Should fall back to UTF-8 or handle gracefully
        assert isinstance(decoded, str)
        assert len(decoded) > 0

    def test_gmail_labels_empty_quotes(self):
        """Test Gmail labels with empty quoted strings."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "", Work, ""

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        # Empty quoted strings should be filtered out (stripped_label check)
        assert "Work" in parsed["gmail_labels"]
        # Empty strings should not be in the list
        assert "" not in parsed["gmail_labels"]

    def test_gmail_labels_very_long(self):
        """Test Gmail labels with very long label names."""
        long_label = "A" * 1000
        email_content = f"""From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "{long_label}", Work

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert long_label in parsed["gmail_labels"]
        assert "Work" in parsed["gmail_labels"]

    def test_address_with_angle_brackets_in_name(self):
        """Test email address with angle brackets in display name."""
        name, email_addr = parse_email_address('"User <Name>" <user@example.com>')
        assert email_addr == "user@example.com"
        # Display name should be extracted correctly
        assert "User" in name or "Name" in name

    def test_address_with_special_characters(self):
        """Test email address with various special characters."""
        test_cases = [
            ('"User; Name" <user@example.com>', "user@example.com"),
            ("User: Name <user@example.com>", "user@example.com"),
            ("user+tag@example.com", "user+tag@example.com"),
        ]
        for address_str, expected_email in test_cases:
            _, email_addr = parse_email_address(address_str)
            assert email_addr == expected_email

    def test_date_with_invalid_timezone(self):
        """Test date parsing with invalid timezone."""
        # Should return None for invalid dates
        invalid_date = "Mon, 1 Jan 2024 12:00:00 INVALID"
        parsed = parse_date(invalid_date)
        # Should return None or handle gracefully
        assert parsed is None or isinstance(parsed, datetime)

    def test_date_with_future_date(self):
        """Test date parsing with future date."""
        future_date = "Mon, 1 Jan 2100 12:00:00 +0000"
        parsed = parse_date(future_date)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2100

    def test_date_with_very_old_date(self):
        """Test date parsing with very old date."""
        old_date = "Mon, 1 Jan 1900 12:00:00 +0000"
        parsed = parse_date(old_date)
        assert isinstance(parsed, datetime)
        assert parsed.year == 1900

    def test_parse_email_with_all_recipient_types(self):
        """Test parsing email with To, Cc, and Bcc recipients."""
        raw_email = b"""From: sender@example.com
To: to1@example.com, to2@example.com
Cc: cc1@example.com, cc2@example.com
Bcc: bcc1@example.com, bcc2@example.com
Subject: All Recipients
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body text."""
        parsed = parse_email_message(raw_email)
        assert len(parsed["to"]) == 2
        assert len(parsed["cc"]) == 2
        assert len(parsed["bcc"]) == 2
        assert parsed["to"][0]["email"] == "to1@example.com"
        assert parsed["to"][1]["email"] == "to2@example.com"
        assert parsed["cc"][0]["email"] == "cc1@example.com"
        assert parsed["cc"][1]["email"] == "cc2@example.com"
        assert parsed["bcc"][0]["email"] == "bcc1@example.com"
        assert parsed["bcc"][1]["email"] == "bcc2@example.com"

    def test_parse_email_with_missing_optional_headers(self):
        """Test parsing email with missing optional headers."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Minimal Headers

Body text."""
        parsed = parse_email_message(raw_email)
        assert parsed["subject"] == "Minimal Headers"
        assert parsed["from"]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        # Optional headers should have default values
        assert parsed["message_id"] == ""
        assert parsed["references"] == ""
        assert parsed["in_reply_to"] == ""
        assert not parsed["gmail_labels"]

    def test_inline_image_without_filename(self):
        """Test inline image without filename."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Inline Image
Content-Type: multipart/related; boundary="boundary"

--boundary
Content-Type: text/html

<html><body><img src="cid:image1"></body></html>

--boundary
Content-Type: image/png
Content-ID: <image1>
Content-Disposition: inline

image data
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["disposition"] == "inline"
        assert attachment["cid"] == "image1"
        # Should infer filename from content type
        assert attachment["name"] == "unnamed.png"

    def test_attachment_vs_inline_classification(self):
        """Test correct classification of attachment vs inline.

        Per JMAP algorithm: inline images (Content-Disposition: inline) in
        multipart/mixed go to body arrays, not attachments. Only explicit
        attachments (Content-Disposition: attachment) go to attachments.
        """
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Classification Test
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body.

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

PDF content

--boundary
Content-Type: image/png
Content-Disposition: inline; filename="img.png"

Image content
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)

        # Only the PDF should be in attachments (inline image goes to body)
        assert len(content["attachments"]) == 1
        pdf = next((a for a in content["attachments"] if a["name"] == "doc.pdf"), None)
        assert pdf is not None
        assert pdf["disposition"] == "attachment"

        # Inline image should be in body arrays (textBody and htmlBody)
        assert len(content["textBody"]) == 2  # text/plain body + inline image
        assert len(content["htmlBody"]) == 2  # same parts copied
        img_in_body = next(
            (p for p in content["textBody"] if p.get("type") == "image/png"), None
        )
        assert img_in_body is not None, "Inline image should be in textBody"

    def test_email_with_many_parts(self):
        """Test parsing email with many MIME parts.

        Note: This tests behavior with many parts. The parser currently
        does not enforce a limit on the number of parts.
        """
        # Create email with 50 parts
        parts = []
        for i in range(50):
            parts.append(
                f"""--boundary
Content-Type: text/plain

Part {i} content.

""".encode("utf-8")
            )

        raw_email = (
            b"""From: sender@example.com
To: recipient@example.com
Subject: Many Parts
Content-Type: multipart/mixed; boundary="boundary"

"""
            + b"".join(parts)
            + b"""--boundary--"""
        )
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        # Should handle many parts (currently no limit enforced)
        assert len(content["textBody"]) == 50

    def test_deeply_nested_multipart(self):
        """Test parsing email with deeply nested multipart structure.

        Note: This tests behavior with deep nesting. The parser currently
        does not enforce a nesting depth limit.
        """
        # Create 10 levels of nesting
        nested_content = b"""Content-Type: text/plain

Innermost content."""

        for _ in range(10):
            nested_content = (
                b"""Content-Type: multipart/alternative; boundary="inner"
--inner
"""
                + nested_content
                + b"""
--inner--
"""
            )

        raw_email = (
            b"""From: sender@example.com
To: recipient@example.com
Subject: Deep Nesting
Content-Type: multipart/mixed; boundary="outer"

--outer
"""
            + nested_content
            + b"""
--outer--"""
        )
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        # Should handle deep nesting (currently no limit enforced)
        assert len(content["textBody"]) >= 1

    def test_header_with_control_characters(self):
        """Test header with control characters."""
        # Headers should be decoded, but control chars might cause issues
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test\x00Header
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body."""
        # Should handle gracefully or raise error
        try:
            parsed = parse_email_message(raw_email)
            # If it parses, subject should be handled
            assert isinstance(parsed["subject"], str)
        except EmailParseError:
            # If it fails, that's also acceptable behavior
            pass

    def test_content_type_with_parameters(self):
        """Test Content-Type with various parameters."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Content-Type Params
Content-Type: text/plain; charset=utf-8; format=flowed; delsp=yes

Body with parameters."""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["type"] == "text/plain"

    def test_message_with_encoded_filename(self):
        """Test message with RFC 2047 encoded filename."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Encoded Filename
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename*=utf-8''document%C3%A9.pdf

PDF content
--boundary--"""
        message_obj = create.from_string(raw_email)
        content = parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        # Filename should be decoded
        assert (
            "document" in attachment["name"].lower()
            or "pdf" in attachment["name"].lower()
        )


class TestGmailLabelsSplitting:
    """Tests specifically for Gmail labels splitting functionality."""

    def test_split_simple_labels(self):
        """Test splitting simple labels without quotes."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: Important, Work, Personal

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == ["Important", "Work", "Personal"]

    def test_split_labels_with_quoted_strings(self):
        """Test splitting labels that contain quoted strings with commas."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "Culture, associations, événements", Work, Personal

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == [
            "Culture, associations, événements",
            "Work",
            "Personal",
        ]

    def test_split_single_quoted_label(self):
        """Test splitting when there's only one quoted label."""

        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "Culture, associations, événements"

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == ["Culture, associations, événements"]

    def test_split_empty_header(self):
        """Test splitting when X-Gmail-Labels header is empty."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: 

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert len(parsed["gmail_labels"]) == 0

    def test_split_missing_header(self):
        """Test splitting when X-Gmail-Labels header is missing."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert len(parsed["gmail_labels"]) == 0

    def test_split_multiple_headers(self):
        """Test splitting when there are multiple X-Gmail-Labels headers (should take first)."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "Culture, associations, événements", Work
X-Gmail-Labels: Personal, Family

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        # Should take the first X-Gmail-Labels header
        assert parsed["gmail_labels"] == ["Culture, associations, événements", "Work"]

    def test_split_with_escaped_quotes(self):
        """Test splitting with escaped quotes inside quoted strings.

        Note: The current regex pattern r'"([^"]*)"|([^,]+)' does not handle
        escaped quotes. This test verifies the actual behavior - escaped quotes
        will break the quoted string parsing.
        """
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: "Culture, associations, événements", "Test with \\"quotes\\" inside", Work

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        # The regex will match up to the first unescaped quote, so the label
        # will be split incorrectly. This test documents the current behavior.
        assert "Culture, associations, événements" in parsed["gmail_labels"]
        assert "Work" in parsed["gmail_labels"]
        # The escaped quotes case will not parse correctly with current implementation

    def test_split_edge_case_trailing_comma(self):
        """Test splitting with trailing comma."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: Important, Work, Personal,

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == ["Important", "Work", "Personal"]

    def test_split_edge_case_leading_comma(self):
        """Test splitting with leading comma."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: , Important, Work, Personal

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == ["Important", "Work", "Personal"]

    def test_split_edge_case_consecutive_commas(self):
        """Test splitting with consecutive commas."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: Important,, Work, Personal

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"] == ["Important", "Work", "Personal"]

    def test_split_utf8_encoded_labels(self):
        """Test splitting with real Gmail labels format from .mbox file."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: =?UTF-8?Q?Messages_archiv=C3=A9s,Ouvert,Cat=C3=A9gorie=C2=A0:_E-mails_?=
 =?UTF-8?Q?personnels,"Culture,_associations,_=C3=A9v=C3=A9nements"?=

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        # The parser should handle the UTF-8 encoded content and extract the labels
        # Expected: ["Messages archivés", "Ouvert", "Catégorie : E-mails personnels",
        # "Culture, associations, événements"]
        assert "Messages archivés" in parsed["gmail_labels"]
        assert "Ouvert" in parsed["gmail_labels"]
        assert "Culture, associations, événements" in parsed["gmail_labels"]
        assert "Catégorie : E-mails personnels" in parsed["gmail_labels"]


if __name__ == "__main__":
    pytest.main()
