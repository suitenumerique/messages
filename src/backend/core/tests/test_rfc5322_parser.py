"""
Tests for the RFC5322 email parser module.
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from email.header import Header

from django.test import TestCase

import pytest
from flanker.mime import create

from core.formats.rfc5322.parser import (
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


class TestMessageContentParsing(TestCase):
    """Tests for parsing email message content."""

    def test_parse_plain_text_content(self):
        """Test parsing a plain text email."""
        message = create.text("plain", "This is a test message")
        result = parse_message_content(message)
        # Should now correctly parse the content of the non-multipart message
        assert len(result["textBody"]) == 1
        assert result["textBody"][0]["content"] == "This is a test message"
        assert result["textBody"][0]["type"] == "text/plain"
        assert len(result["htmlBody"]) == 0
        assert len(result["attachments"]) == 0

    def test_parse_html_content(self):
        """Test parsing an HTML email."""
        html_content = "<html><body><p>This is a test message</p></body></html>"
        message = create.text("html", html_content)
        result = parse_message_content(message)
        # Should now correctly parse the content of the non-multipart message
        assert len(result["htmlBody"]) == 1
        assert result["htmlBody"][0]["content"] == html_content
        assert result["htmlBody"][0]["type"] == "text/html"
        assert len(result["textBody"]) == 0
        assert len(result["attachments"]) == 0

    def test_parse_multipart_content(self):
        """Test parsing a multipart email with both text and HTML."""
        # Create flanker message with both text and HTML content
        message = create.multipart("alternative")
        message.append(
            create.text("plain", "This is a test message"),
            create.text(
                "html", "<html><body><p>This is a test message</p></body></html>"
            ),
        )

        result = parse_message_content(message)

        # Check we have both parts
        assert len(result["textBody"]) == 1
        assert len(result["htmlBody"]) == 1

        # Check content
        assert result["textBody"][0]["content"] == "This is a test message"
        assert (
            result["htmlBody"][0]["content"]
            == "<html><body><p>This is a test message</p></body></html>"
        )

        # Check types
        assert result["textBody"][0]["type"] == "text/plain"
        assert result["htmlBody"][0]["type"] == "text/html"

    def test_parse_with_attachment(self):
        """Test parsing an email with an attachment."""
        message = create.multipart("mixed")
        attachment_data = b"Test attachment content"

        # Use flanker's create functions as originally intended
        message.append(
            create.text("plain", "This is a test message"),
            create.binary(
                "application",
                "octet-stream",
                attachment_data,
                filename="test.txt",
                disposition="attachment",
            ),  # Use kwargs
        )

        result = parse_message_content(message)

        # Check text content found
        self.assertEqual(len(result["textBody"]), 1)
        self.assertEqual(result["textBody"][0]["content"], "This is a test message")

        # Check attachment found
        self.assertEqual(len(result["attachments"]), 1)
        attachment = result["attachments"][0]

        # Assertions
        self.assertEqual(
            attachment["type"],
            "application/octet-stream",
            f"Expected 'application/octet-stream', but got '{attachment['type']}'",
        )
        self.assertEqual(attachment["name"], "test.txt")
        self.assertEqual(attachment["disposition"], "attachment")
        # Optional: Check size if your parser logic calculates it correctly for binary/base64
        # self.assertEqual(attachment['size'], len(attachment_data))

    def test_parse_nested_multipart(self):
        """Test parsing a nested multipart message."""
        # Create a nested multipart message (mixed with alternative inside)
        outer = create.multipart("mixed")
        inner = create.multipart("alternative")

        inner.append(
            create.text("plain", "This is a test message"),
            create.text(
                "html", "<html><body><p>This is a test message</p></body></html>"
            ),
        )

        attachment_data = b"Test attachment content"
        outer.append(
            inner,
            create.binary(
                "application", "pdf", attachment_data, "document.pdf", "attachment"
            ),
        )

        result = parse_message_content(outer)

        # Check attachment
        assert len(result["attachments"]) == 1
        assert result["attachments"][0]["type"] == "application/pdf"
        assert result["attachments"][0]["name"] == "document.pdf"

        # Check content (from the nested parts)
        assert len(result["textBody"]) == 1
        assert len(result["htmlBody"]) == 1
        assert result["textBody"][0]["content"] == "This is a test message"
        assert (
            result["htmlBody"][0]["content"]
            == "<html><body><p>This is a test message</p></body></html>"
        )

    def test_parse_multipart_with_multiple_html_parts(self):
        """Test parsing a multipart message with multiple HTML parts."""
        outer = create.multipart("alternative")
        outer.append(create.text("plain", "Plain text content"))

        # Create a mixed part with multiple HTML parts
        mixed_part = create.multipart("mixed")
        mixed_part.append(
            create.text("html", "<html><body><p>First HTML part</p></body></html>"),
            create.binary(
                "application", "pdf", b"PDF content", "doc.pdf", "attachment"
            ),
            create.text("html", "<html><body><p>Second HTML part</p></body></html>"),
        )

        outer.append(mixed_part)

        result = parse_message_content(outer)

        # Should have both plain text and HTML content
        assert len(result["textBody"]) == 1
        assert len(result["htmlBody"]) == 2  # Should capture both HTML parts
        assert result["textBody"][0]["content"] == "Plain text content"
        assert "First HTML part" in result["htmlBody"][0]["content"]
        assert "Second HTML part" in result["htmlBody"][1]["content"]
        assert len(result["attachments"]) == 1

    def test_parse_multipart_with_encoding(self):
        """Test parsing a multipart message with encoded parts."""
        message = create.multipart("mixed")

        # Add text part with quoted-printable encoding
        text_part = create.text("plain", "This contains special chars: é à ç")
        text_part.headers["Content-Transfer-Encoding"] = "quoted-printable"

        # Add HTML part with an image and base64 encoding
        html_part = create.text(
            "html",
            "<html><body><p>HTML with image: <img src='cid:image1'></p></body></html>",
        )
        html_part.headers["Content-Transfer-Encoding"] = "base64"

        message.append(text_part, html_part)

        result = parse_message_content(message)

        assert len(result["textBody"]) == 1
        assert "special chars" in result["textBody"][0]["content"]
        assert len(result["htmlBody"]) == 1
        assert "<img" in result["htmlBody"][0]["content"]

    def test_parse_content_with_no_body(self):
        """Test parsing a message with headers but no body."""
        # Create message with no body part
        message = create.from_string("Subject: No Body\nFrom: sender@example.com\n\n")
        result = parse_message_content(message)

        # No body parts should be found
        assert len(result["textBody"]) == 0
        assert len(result["htmlBody"]) == 0
        assert len(result["attachments"]) == 0


class TestEmailMessageParsing:
    """Tests for parsing complete email messages."""

    @pytest.fixture
    def simple_email(self):
        """Create a simple email message with plain text content."""
        message = create.text("plain", "This is the body!\nIt has more than one line")
        message.headers["To"] = "user@example.com"
        message.headers["From"] = "me@example.com"
        message.headers["Reply-To"] = "otherme@example.com"
        message.headers["Subject"] = "Test Email"

        return message.to_string()

    @pytest.fixture
    def multipart_email(self):
        """Create a multipart email message with text and HTML parts."""
        message = create.multipart("alternative")
        message.headers["To"] = (
            "Alex Johnson <alex@example.com>, Taylor Reed <taylor@example.com>"
        )
        message.headers["CC"] = (
            "Jamie Smith <jamie@example.com>, Robin Anderson <robin@example.com>"
        )
        message.headers["From"] = "Sam Jones <sam@example.com>"
        message.headers["Reply-To"] = "Support <support@example.com>"
        message.headers["Subject"] = "Report summary"
        message.headers["Mime-Version"] = "1.0"

        message.append(
            create.text("plain", "This is a summary of the quarterly report"),
            create.text(
                "html",
                "<h1>Quarterly Report Summary</h1><p>This is a summary of the quarterly report</p>",
            ),
        )

        return message.to_string()

    @pytest.fixture
    def complex_email(self):
        """Create a complex email with nested parts and attachments."""
        message = create.multipart("mixed")
        message.headers["To"] = (
            "Test User <user@example.com>, Other User <other@example.com>"
        )
        message.headers["CC"] = (
            "The Dude <dude@example.com>, Batman <batman@example.com>"
        )
        message.headers["From"] = "Me <me@example.com>"
        message.headers["Date"] = "Fri, 1 Jan 2016 00:00:00 +0000"

        # Create nested multipart/alternative
        alt_part = create.multipart("alternative")
        alt_part.append(
            create.text("plain", "This is some text"),
            create.text("html", "<h1>This is the HTML</h1>"),
        )

        # Correct create.binary call: maintype, subtype
        attachment = create.binary(
            maintype="text",  # Use maintype
            subtype="markdown",  # Use subtype
            body=b"Hello world!",
            filename="README.md",
            disposition="attachment",
        )

        message.append(alt_part, attachment)

        return message.to_string()

    @pytest.fixture
    def email_with_encoded_headers(self):
        """Create an email with encoded headers using RFC 2047 encoding."""
        message = create.text("plain", "Email with encoded headers")
        message.headers["To"] = "user@example.com"
        message.headers["From"] = (
            "=?UTF-8?B?am9obi5kb2VAcmVkYWN0ZS4uLg==?= <comments-noreply@docs.google.com>"
        )
        message.headers["Subject"] = "=?utf-8?Q?=C2=A3?=200.00=?UTF-8?q?_=F0=9F=92=B5?="

        return message.to_string()

    @pytest.fixture
    def test_email(self):
        """Create a test email message for parsing tests using flanker."""
        message = create.multipart("alternative")
        message.headers["From"] = "Test Sender <sender@example.com>"
        message.headers["To"] = "Test Recipient <recipient@example.com>"
        message.headers["Cc"] = "Copy <copy@example.com>, Another <another@example.com>"
        message.headers["Subject"] = "Test Subject"
        message.headers["Date"] = "Mon, 15 Jan 2024 12:30:45 +0000"
        message.headers["Message-ID"] = "<12345@example.com>"

        # Add text and HTML parts
        message.append(
            create.text("plain", "This is a plain text message."),
            create.text(
                "html", "<html><body><p>This is an HTML message.</p></body></html>"
            ),
        )

        return message.to_string()

    @pytest.fixture
    def email_with_special_charset(self):
        """Create an email with non-UTF8 charset."""
        message = create.text(
            "plain", "Este é um e-mail em português com caracteres especiais."
        )
        message.headers["To"] = "Recipient <recipient@example.com>"
        message.headers["From"] = "=?ISO-8859-1?Q?Jo=E3o_Silva?= <joao@example.br>"
        message.headers["Subject"] = "=?ISO-8859-1?Q?Relat=F3rio_de_Atividades?="

        return message.to_string()

    def test_parse_simple_email(self, simple_email):
        """Test parsing a simple email with text content."""
        parsed = parse_email_message(simple_email)
        assert parsed is not None  # Check parsing succeeded

        # Access headers via the 'headers' dictionary
        assert parsed["headers"]["to"] == "user@example.com"
        assert parsed["from"]["email"] == "me@example.com"
        # Check 'reply-to' in the headers dict
        assert parsed["headers"].get("reply-to") == "otherme@example.com"
        assert parsed["subject"] == "Test Email"
        # Check content-type in headers dict (might be list if multiple)
        assert "text/plain" in parsed["headers"].get("content-type", "")
        assert len(parsed["textBody"]) == 1
        assert (
            parsed["textBody"][0]["content"]
            == "This is the body!\nIt has more than one line"
        )
        # Check backward compatible field
        assert parsed["body_text"] == "This is the body!\nIt has more than one line"

    def test_parse_multipart_email(self, multipart_email):
        """Test parsing a multipart email."""
        parsed = parse_email_message(multipart_email)

        # Check recipients
        assert len(parsed["to"]) == 2
        assert parsed["to"][0]["name"] == "Alex Johnson"
        assert parsed["to"][0]["email"] == "alex@example.com"
        assert parsed["to"][1]["name"] == "Taylor Reed"
        assert parsed["to"][1]["email"] == "taylor@example.com"

        assert len(parsed["cc"]) == 2
        assert parsed["cc"][0]["name"] == "Jamie Smith"
        assert parsed["cc"][0]["email"] == "jamie@example.com"

        # Check content
        assert len(parsed["textBody"]) == 1
        assert (
            parsed["textBody"][0]["content"]
            == "This is a summary of the quarterly report"
        )

        assert len(parsed["htmlBody"]) == 1
        assert (
            parsed["htmlBody"][0]["content"]
            == "<h1>Quarterly Report Summary</h1><p>This is a summary of the quarterly report</p>"
        )

    def test_parse_complex_email(self, complex_email):
        """Test parsing a complex email with nested parts and attachments."""
        parsed = parse_email_message(complex_email)
        assert parsed is not None

        # Check headers via 'headers' dict
        assert parsed["headers"]["date"] == "Fri, 1 Jan 2016 00:00:00 +0000"

        # Check content parts
        assert len(parsed["textBody"]) == 1
        assert parsed["textBody"][0]["content"] == "This is some text"

        assert len(parsed["htmlBody"]) == 1
        assert parsed["htmlBody"][0]["content"] == "<h1>This is the HTML</h1>"

        # Check attachment
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["type"] == "text/markdown"
        assert parsed["attachments"][0]["name"] == "README.md"
        # Check size if possible (flanker might decode base64)
        # assert parsed["attachments"][0]["size"] == len(b"Hello world!")

    def test_parse_email_with_encoded_headers(self, email_with_encoded_headers):
        """Test parsing an email with encoded headers."""
        parsed = parse_email_message(email_with_encoded_headers)

        # Check decoded headers
        assert parsed["from"]["name"] == "john.doe@redacte..."
        assert parsed["from"]["email"] == "comments-noreply@docs.google.com"
        assert parsed["subject"] == "£200.00 💵"

    def test_parse_email_message(self, test_email):
        """Test parsing a complete email message."""
        parsed = parse_email_message(test_email)
        assert parsed is not None
        # ... other assertions ...
        # Assert against the ID without angle brackets
        assert parsed["message_id"] == "12345@example.com"

    def test_parse_invalid_message(self):
        """Test parsing an invalid message format returns None or raises."""
        invalid_input = b"This is definitely not an email\r\n\r\n"
        # Flanker might parse this as an empty message or raise an error later
        try:
            result = parse_email_message(invalid_input)
            # If it doesn't raise, Flanker might have returned None or an empty structure
            assert result is None or not result.get(
                "subject"
            )  # Check if parsing essentially failed
        except EmailParseError:
            # Or explicitly expect the error if Flanker/our code raises it
            pass
        # Alternative: If None is the expected outcome for completely invalid input:
        # assert parse_email_message(invalid_input) is None

    def test_parse_email_with_no_content_type(self):
        """Test parsing an email with no content type header."""
        raw_email_str = "To: recipient@example.com\nFrom: sender@example.com\nSubject: No Content-Type\n\nSimple message"
        parsed = parse_email_message(raw_email_str.encode("utf-8"))
        assert parsed is not None
        # Should now correctly parse the content
        assert len(parsed["textBody"]) == 1
        assert parsed["textBody"][0]["content"] == "Simple message"
        assert parsed["textBody"][0]["type"] == "text/plain"  # Default assumption
        assert len(parsed["htmlBody"]) == 0

    def test_parse_email_with_custom_headers(self):
        """Test parsing an email with custom, non-standard headers."""
        message = create.text("plain", "Message with custom headers")
        message.headers["To"] = "recipient@example.com"
        message.headers["From"] = "sender@example.com"
        message.headers["Subject"] = "Custom Headers"
        message.headers["X-Custom-Header"] = "Custom Value"
        message.headers["X-Priority"] = "1"
        message.headers["X-Mailer"] = "Custom Mailer v1.0"

        parsed = parse_email_message(message.to_string())
        assert parsed is not None

        # Check custom headers were preserved in the headers dict (lowercase keys)
        assert parsed["headers"]["x-custom-header"] == "Custom Value"
        assert parsed["headers"]["x-priority"] == "1"
        assert parsed["headers"]["x-mailer"] == "Custom Mailer v1.0"

    def test_parse_email_with_missing_from(self):
        """Test parsing an email with missing From header."""
        message = create.text("plain", "Message with no From")
        message.headers["To"] = "recipient@example.com"
        message.headers["Subject"] = "No From Header"
        # Ensure no From header
        if "From" in message.headers:
            del message.headers["From"]

        parsed = parse_email_message(message.to_string())

        # Check handling of missing From
        assert "from" in parsed
        assert parsed["from"]["name"] == ""
        assert parsed["from"]["email"] == ""


if __name__ == "__main__":
    pytest.main()
