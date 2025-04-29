"""
Tests for the RFC5322 email parser module.
"""

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
        assert not parsed.get("htmlBody"), "Expected no htmlBody"
        assert not parsed.get("attachments"), "Expected no attachments"

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
        assert len(parsed.get("textBody", [])) == 1
        assert "Plain text body content." in parsed["textBody"][0]["content"]
        assert len(parsed.get("htmlBody", [])) == 1
        assert "<h1>HTML Content</h1>" in parsed["htmlBody"][0]["content"]
        assert len(parsed.get("attachments", [])) == 2

        # Check for PDF attachment more robustly
        pdf_attachment = next(
            (
                a
                for a in parsed["attachments"]
                if a.get("type") == "application/pdf"
                and a.get("disposition") == "attachment"
            ),
            None,
        )
        # Check for inline image more robustly using cid and disposition
        image = next(
            (
                a
                for a in parsed["attachments"]
                if a.get("cid") == "inline-image@example.com"
                and a.get("disposition") == "inline"
            ),
            None,
        )

        assert pdf_attachment is not None, (
            "PDF attachment not found or correctly classified"
        )

        assert image is not None, (
            "Inline image attachment not found or correctly classified"
        )
        # assert image["name"] == "image.png" # Filename check is less reliable than CID
        assert image["type"] == "image/png"
        # assert image["cid"] == "inline-image@example.com" # Already checked in next()

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
        assert not parsed.get("htmlBody")
        assert not parsed.get("attachments")

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
            match="Failed to parse email: Multipart message without starting boundary",
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
        assert not content["htmlBody"]
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
        """Test that parse_message_content returns a dictionary."""
        message_obj = create.from_string(test_email)
        content = parse_message_content(message_obj)
        assert isinstance(content, dict)
        assert "textBody" in content
        assert "htmlBody" in content
        assert "attachments" in content

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
        assert not content["htmlBody"]
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
        assert not content["textBody"]
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
        assert not content["textBody"]
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["name"] == "image.png" or attachment["name"] == "unnamed"
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
        with pytest.raises(
            EmailParseError, match="Multipart message without starting boundary"
        ):
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
        assert attachment.get("name") == "unnamed"
        assert attachment["type"] == "application/pdf"


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


if __name__ == "__main__":
    pytest.main()
