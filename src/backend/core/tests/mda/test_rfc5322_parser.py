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
        assert not addresses

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

    def test_parse_address_with_comma_and_unicode_in_name(self):
        """Quoted name combining ',' and non-ASCII must yield one recipient.

        ',' is the address-list separator; if quote-stripping or encoded-word
        decoding misorders, the parser can split a single recipient into two.
        """
        name, email_addr = parse_email_address('"García, José" <jose@example.es>')
        assert name == "García, José"
        assert email_addr == "jose@example.es"

        # Same idea but with the non-ASCII coming through an RFC 2047
        # encoded-word inside the quoted display-name.
        name, email_addr = parse_email_address(
            '"=?utf-8?q?Garc=C3=ADa=2C_Jos=C3=A9?=" <jose@example.es>'
        )
        assert name == "García, José"
        assert email_addr == "jose@example.es"

    def test_parse_multiple_recipients_with_comma_and_unicode_in_names(self):
        """Address-list with ',' inside non-ASCII display names must not be split.

        Three recipients where two have both a literal ',' and non-ASCII chars
        in their names. A naive splitter that decodes encoded-words before
        splitting on ',' (or that ignores quotes) would yield more than three
        addresses.
        """
        addresses = parse_email_addresses(
            '"Doe, Jané" <jane@example.com>, '
            '"=?utf-8?q?M=C3=BCller=2C_Frank?=" <frank@example.com>, '
            "third@example.com"
        )
        assert len(addresses) == 3
        assert addresses[0] == ("Doe, Jané", "jane@example.com")
        assert addresses[1] == ("Müller, Frank", "frank@example.com")
        assert addresses[2] == ("", "third@example.com")

    # RFC 5322 Group Syntax Tests
    # These test cases handle email headers like "undisclosed-recipients:;"
    # which are valid RFC 5322 group syntax used to hide recipients.

    def test_parse_address_undisclosed_recipients(self):
        """Test parsing undisclosed-recipients:; returns empty."""
        name, email_addr = parse_email_address("undisclosed-recipients:;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_empty_group(self):
        """Test parsing empty group :; returns empty."""
        name, email_addr = parse_email_address(":;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_group_with_space(self):
        """Test parsing group with space in name."""
        name, email_addr = parse_email_address("undisclosed recipients:;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_malformed_group_colon_gt(self):
        """Test parsing malformed group syntax with :> instead of :;"""
        name, email_addr = parse_email_address("undisclosed-recipients:>")
        assert name == ""
        assert email_addr == ""

    def test_parse_addresses_undisclosed_recipients(self):
        """Test parsing undisclosed-recipients:; returns empty list."""
        addresses = parse_email_addresses("undisclosed-recipients:;")
        assert not addresses

    def test_parse_addresses_group_with_members(self):
        """Test parsing group syntax extracts member addresses."""
        addresses = parse_email_addresses(
            "Group: user1@example.com, user2@example.com;"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("", "user1@example.com")
        assert addresses[1] == ("", "user2@example.com")

    def test_parse_addresses_mixed_normal_and_group(self):
        """Test parsing mix of normal addresses and group syntax."""
        addresses = parse_email_addresses("test@example.com, undisclosed-recipients:;")
        assert len(addresses) == 1
        assert addresses[0] == ("", "test@example.com")

    def test_parse_addresses_normal_group_normal(self):
        """Test parsing normal, group, normal pattern."""
        addresses = parse_email_addresses(
            "First <a@b.com>, undisclosed-recipients:;, Last <z@y.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("First", "a@b.com")
        assert addresses[1] == ("Last", "z@y.com")

    def test_parse_addresses_complex_group_with_addresses(self):
        """Test parsing complex case with addresses before and after group."""
        addresses = parse_email_addresses("a@b.com, Group: c@d.com, e@f.com;, g@h.com")
        assert len(addresses) == 4
        assert ("", "a@b.com") in addresses
        assert ("", "c@d.com") in addresses
        assert ("", "e@f.com") in addresses
        assert ("", "g@h.com") in addresses

    def test_parse_addresses_malformed_group_colon_gt(self):
        """Test parsing malformed group syntax :> returns empty."""
        addresses = parse_email_addresses("undisclosed-recipients:>")
        assert not addresses

    def test_parse_addresses_malformed_group_mixed(self):
        """Test parsing mix of normal addresses and malformed :> group."""
        addresses = parse_email_addresses("test@example.com, undisclosed-recipients:>")
        assert len(addresses) == 1
        assert addresses[0] == ("", "test@example.com")

    def test_parse_addresses_empty_group(self):
        """Test parsing various empty group patterns."""
        assert not parse_email_addresses(":;")
        assert not parse_email_addresses(":>")
        assert not parse_email_addresses("test:;")
        assert not parse_email_addresses("Empty Group:;")

    def test_parse_address_unquoted_name_no_quotes_added(self):
        """Test that unquoted display names don't get quotes added."""
        name, email = parse_email_address("City of Example <contact@example.org>")
        assert name == "City of Example"
        assert email == "contact@example.org"
        # Ensure no quotes in name
        assert "'" not in name
        assert '"' not in name

    def test_parse_addresses_encoded_names_with_parentheses(self):
        """Test parsing encoded names containing parentheses."""
        to_header = (
            "=?UTF-8?Q?John_DOE_=28Organization_A=29?= <john@example.com>, "
            "=?UTF-8?Q?John_DOE_=28Organization_B=29?= <john@example.org>"
        )
        addresses = parse_email_addresses(to_header)
        assert len(addresses) == 2
        assert addresses[0] == ("John DOE (Organization A)", "john@example.com")
        assert addresses[1] == ("John DOE (Organization B)", "john@example.org")

    def test_parse_address_strips_single_quotes(self):
        """Test that single quotes around display names are stripped.

        Some email clients incorrectly use single quotes instead of double quotes
        for display names. We strip them for consistency.
        """
        # Single quotes should be stripped
        name, email = parse_email_address("'City of Example' <contact@example.org>")
        assert name == "City of Example"
        assert email == "contact@example.org"

        # Apostrophe inside name should be preserved
        name, email = parse_email_address("'John's Company' <john@example.org>")
        assert name == "John's Company"
        assert email == "john@example.org"

    def test_parse_addresses_strips_single_quotes(self):
        """Test that single quotes are stripped from multiple addresses."""
        addresses = parse_email_addresses(
            "'Company A' <a@example.com>, 'Company B' <b@example.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("Company A", "a@example.com")
        assert addresses[1] == ("Company B", "b@example.com")


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
        # Non-scalar headers (X-*, optional-field per RFC 5322 §3.6.8)
        # are stored as list[str] in document order.
        assert parsed["headers"]["x-custom-header"] == ["Custom Value"]
        assert parsed["headers"]["x-priority"] == ["1"]
        assert parsed["headers"]["x-mailer"] == ["Custom Mailer v1.0"]

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

    def test_parse_email_with_nul_bytes(self):
        """Test that NUL bytes are stripped from subject and body content.

        PostgreSQL text fields cannot store NUL (0x00) bytes.
        """
        raw = b"Subject: Test\x00Subject\x00With\x00NUL\nFrom: a@b.c\nTo: d@e.f\n\nBody\x00with\x00NUL\x00bytes."
        parsed = parse_email_message(raw)
        assert parsed is not None
        # Verify NUL bytes were stripped from subject
        assert "\x00" not in parsed["subject"]
        assert parsed["subject"] == "TestSubjectWithNUL"
        # Verify NUL bytes were stripped from body content
        assert len(parsed["textBody"]) == 1
        assert "\x00" not in parsed["textBody"][0]["content"]
        assert parsed["textBody"][0]["content"] == "BodywithNULbytes."

    def test_parse_message_content_strips_nul_bytes_in_fallback_path(self):
        """Test NUL bytes are stripped in the fallback path for malformed messages.

        When a message has no content-type but has a body, the fallback path
        in parse_message_content should still strip NUL bytes.
        """

        class MockMessage:
            """Mock message without content_type to trigger fallback path."""

            content_type = None
            body = "Body\x00with\x00NUL\x00bytes"

        result = parse_message_content(MockMessage())
        assert len(result["textBody"]) == 1
        assert "\x00" not in result["textBody"][0]["content"]
        assert result["textBody"][0]["content"] == "BodywithNULbytes"

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

    def test_x_keywords_comma_separated(self):
        """Test parsing X-Keywords header with comma-separated values."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Keywords: work, important, project

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert "work" in parsed["gmail_labels"]
        assert "important" in parsed["gmail_labels"]
        assert "project" in parsed["gmail_labels"]

    def test_x_keywords_space_separated(self):
        """Test parsing X-Keywords header with space-separated values (Dovecot format)."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Keywords: work important project

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert "work" in parsed["gmail_labels"]
        assert "important" in parsed["gmail_labels"]
        assert "project" in parsed["gmail_labels"]

    def test_x_keywords_with_quoted_strings(self):
        """Test parsing X-Keywords header with quoted strings containing spaces."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Keywords: work, "project alpha", important

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert "work" in parsed["gmail_labels"]
        assert "project alpha" in parsed["gmail_labels"]
        assert "important" in parsed["gmail_labels"]

    def test_x_keywords_combined_with_gmail_labels(self):
        """Test that X-Keywords and X-Gmail-Labels are combined."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: gmail-label
X-Keywords: keyword-label

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert "gmail-label" in parsed["gmail_labels"]
        assert "keyword-label" in parsed["gmail_labels"]

    def test_x_keywords_combined_deduplication(self):
        """Test that duplicate labels across X-Gmail-Labels and X-Keywords are deduplicated."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Gmail-Labels: shared-label, gmail-only
X-Keywords: shared-label, keywords-only

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert parsed["gmail_labels"].count("shared-label") == 1
        assert "gmail-only" in parsed["gmail_labels"]
        assert "keywords-only" in parsed["gmail_labels"]

    def test_x_keywords_empty(self):
        """Test parsing empty X-Keywords header."""
        email_content = """From: test@example.com
To: recipient@example.com
Subject: Test Email
X-Keywords:

This is a test email.
"""
        parsed = parse_email_message(email_content.encode("utf-8"))
        assert len(parsed["gmail_labels"]) == 0


class TestMalformedTransferEncoding:
    """Tests for emails with malformed transfer encoding."""

    def test_quoted_printable_with_non_ascii_chars(self):
        """Test email claiming quoted-printable but containing raw non-ASCII bytes.

        This tests the case where Content-Transfer-Encoding says quoted-printable
        but the body contains raw non-ASCII characters, which causes Python's
        quopri.decodestring() to raise ValueError.
        """
        # Email with quoted-printable encoding but raw UTF-8 bytes in body
        email_content = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

This contains raw non-ASCII: \xc3\xa9\xc3\xa0\xc3\xbc (should be accented chars)
"""
        # Should not raise an exception
        parsed = parse_email_message(email_content)
        assert parsed is not None
        assert parsed["from"]["email"] == "sender@example.com"

    def test_quoted_printable_multipart_with_non_ascii(self):
        """Test multipart email with malformed quoted-printable part."""
        email_content = b"""From: sender@example.com
To: recipient@example.com
Subject: Multipart with malformed QP
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

Raw bytes here: \xe9\xe0\xfc
--boundary123
Content-Type: text/html; charset="utf-8"

<html><body>Normal HTML</body></html>
--boundary123--
"""
        # Should not crash, should still parse what it can
        parsed = parse_email_message(email_content)
        assert parsed is not None
        assert "htmlBody" in parsed


class TestUnrecognisedMessageSubtypeRobustness:
    """Coverage for inbound bounces and any ``message/*`` content type
    the underlying MIME engine has no dedicated branch for.

    RFC 5337 / RFC 6533 i18n DSN variants
    (``message/global-delivery-status``,
    ``message/global-disposition-notification``,
    ``message/global-headers``), plus other standardised ``message/*``
    subtypes (``message/partial`` RFC 2046, ``message/imdn+xml`` RFC 5438,
    ``message/sip`` RFC 3261, ``message/cpim`` RFC 3862, …) and any
    vendor / unknown subtype must all parse successfully when nested
    inside a ``multipart/report`` bounce.

    Tests exercise the public ``parse_email_message`` API only — no
    MIME-engine internals — so they remain valid if the parser backend
    is swapped (e.g. flanker → ``email.parser``). The behavioural
    contract:

    1. Parsing must not raise ``EmailParseError`` on any well-formed
       multipart/report whose status part uses an unrecognised
       ``message/*`` subtype.
    2. The human-readable notification text part must survive parsing
       so the bounce remains useful to the recipient.
    3. Legacy bounce part types must continue to parse correctly.
    4. Top-level unrecognised ``message/*`` content types are accepted
       too, not only the multipart-nested case.
    """

    BOUNCE_BOUNDARY = "boundary-dsn"
    NOTIFICATION_TEXT = "Your message could not be delivered to one or more recipients."

    @classmethod
    def _build_dsn(cls, status_content_type: str, status_body: bytes) -> bytes:
        """Build a realistic multipart/report bounce wrapping ``status_body``."""
        boundary = cls.BOUNCE_BOUNDARY.encode()
        return (
            b"From: MAILER-DAEMON@mta.example.com (Mail Delivery System)\r\n"
            b"To: sender@example.org\r\n"
            b"Subject: Undelivered Mail Returned to Sender\r\n"
            b"Date: Thu,  4 Jun 2026 02:30:20 +0200 (CEST)\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/report; report-type=delivery-status;\r\n"
            b'\tboundary="' + boundary + b'"\r\n'
            b"\r\n"
            b"This is a MIME-encapsulated message.\r\n"
            b"\r\n"
            b"--" + boundary + b"\r\n"
            b"Content-Description: Notification\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n" + cls.NOTIFICATION_TEXT.encode() + b"\r\n"
            b"\r\n"
            b"--" + boundary + b"\r\n"
            b"Content-Description: Delivery report\r\n"
            b"Content-Type: " + status_content_type.encode() + b"\r\n"
            b"\r\n" + status_body + b"\r\n"
            b"\r\n"
            b"--" + boundary + b"--\r\n"
        )

    @pytest.mark.parametrize(
        "content_type",
        [
            # RFC 5337 / 6533 i18n equivalents of the legacy bounce / MDN /
            # rfc822-headers parts. These need predicate-level recognition
            # to be classified correctly (not just treated as opaque).
            "message/global-delivery-status",
            "message/global-disposition-notification",
            "message/global-headers",
            # Other standardised ``message/*`` subtypes — any unknown
            # subtype must be tolerated, not only the i18n ones.
            "message/partial",  # RFC 2046 §5.2.2 fragmented messages
            "message/imdn+xml",  # RFC 5438 IMDN
            "message/sip",  # RFC 3261 SIP signalling
            "message/sipfrag",  # RFC 3420 partial SIP messages
            "message/cpim",  # RFC 3862 Common Presence and IM
            # Vendor / unknown subtypes must also be tolerated.
            "message/x-vendor-future",
            "message/x-totally-unknown",
        ],
    )
    def test_dsn_with_unrecognised_message_subtype(self, content_type):
        """A multipart/report bounce whose status part uses any unrecognised
        ``message/*`` subtype must parse without raising, and the
        human-readable notification text part must be preserved."""
        raw = self._build_dsn(
            content_type,
            b"Reporting-MTA: dns; mta.example.com\r\n"
            b"\r\n"
            b"Final-Recipient: rfc822; recipient@example.com\r\n"
            b"Action: failed\r\n"
            b"Status: 5.0.0",
        )
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        assert parsed["from"]["email"] == "MAILER-DAEMON@mta.example.com"
        assert any(
            self.NOTIFICATION_TEXT in part["content"] for part in parsed["textBody"]
        ), (
            f"notification text missing from textBody for {content_type}; "
            f"got parts: {[p.get('content', '')[:60] for p in parsed['textBody']]}"
        )

    @pytest.mark.parametrize(
        "content_type",
        [
            "message/delivery-status",
            "message/disposition-notification",
            "text/rfc822-headers",
        ],
    )
    def test_dsn_with_legacy_recognised_subtype(self, content_type):
        """Legacy bounce part types must keep parsing correctly."""
        raw = self._build_dsn(
            content_type,
            b"Reporting-MTA: dns; mta.example.com\r\n"
            b"\r\n"
            b"Final-Recipient: rfc822; recipient@example.com\r\n"
            b"Action: failed\r\n"
            b"Status: 5.0.0",
        )
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        assert any(
            self.NOTIFICATION_TEXT in part["content"] for part in parsed["textBody"]
        )

    def test_postfix_i18n_dsn_full_shape(self):
        """End-to-end shape: a Postfix bounce wrapping RFC 6533
        ``message/global-delivery-status`` inside ``multipart/report``,
        with the full set of headers Postfix typically emits."""
        raw = self._build_dsn(
            "message/global-delivery-status",
            b"Reporting-MTA: dns; mta.example.com\r\n"
            b"X-Postfix-Queue-ID: ABC123\r\n"
            b"X-Postfix-Sender: rfc822; sender@example.org\r\n"
            b"\r\n"
            b"Final-Recipient: rfc822; user@example.com\r\n"
            b"Original-Recipient: rfc822;user@example.com\r\n"
            b"Action: failed\r\n"
            b"Status: 5.0.0",
        )
        parsed = parse_email_message(raw)
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        # Sanity-check the headers that downstream bounce handlers read.
        assert parsed["headers"].get("from", "").startswith("MAILER-DAEMON")
        assert "multipart/report" in parsed["headers"]["content-type"]

    def test_top_level_unrecognised_message_subtype(self):
        """An email whose ROOT Content-Type is an unrecognised
        ``message/*`` subtype must parse — the fallback applies at the
        root, not only nested inside a multipart."""
        raw = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: Weird top-level subtype\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: message/x-something-vendor\r\n"
            b"\r\n"
            b"opaque payload bytes\r\n"
        )
        # The contract is "do not raise". Whether the body bytes are
        # surfaced verbatim in textBody is implementation-defined and
        # not asserted here, so the test survives a backend swap.
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed["subject"] == "Weird top-level subtype"
        assert parsed["from"]["email"] == "sender@example.com"

    def test_unrecognised_subtype_nested_in_message_container(self):
        """A ``message/rfc822``-wrapped inner message with its own
        unrecognised ``message/*`` content type must parse — the outer
        container should not be affected by an unknown inner subtype."""
        raw = (
            b"From: x@example.com\r\n"
            b"To: y@example.com\r\n"
            b"Subject: Forwarded weird\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: multipart/mixed; boundary="OUTER"\r\n'
            b"\r\n"
            b"--OUTER\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"See the attached forwarded message.\r\n"
            b"\r\n"
            b"--OUTER\r\n"
            b"Content-Type: message/rfc822\r\n"
            b"\r\n"
            b"From: inner@example.com\r\n"
            b"Subject: Inner\r\n"
            b"Content-Type: message/x-unknown-vendor\r\n"
            b"\r\n"
            b"opaque body\r\n"
            b"--OUTER--\r\n"
        )
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed["subject"] == "Forwarded weird"
        assert any("See the attached" in part["content"] for part in parsed["textBody"])

    def test_multiple_unrecognised_subtypes_in_same_report(self):
        """A bounce can contain multiple status sub-parts with mixed
        legacy and i18n content types. None of them should crash and
        the notification text must survive."""
        raw = (
            b"From: MAILER-DAEMON@mta.example.com\r\n"
            b"To: sender@example.org\r\n"
            b"Subject: Mixed bounce\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/report; report-type=delivery-status;\r\n"
            b'\tboundary="MULTI"\r\n'
            b"\r\n"
            b"--MULTI\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n" + self.NOTIFICATION_TEXT.encode() + b"\r\n"
            b"--MULTI\r\n"
            b"Content-Type: message/delivery-status\r\n"
            b"\r\n"
            b"Reporting-MTA: dns; legacy.example.com\r\n"
            b"--MULTI\r\n"
            b"Content-Type: message/global-delivery-status\r\n"
            b"\r\n"
            b"Reporting-MTA: dns; i18n.example.com\r\n"
            b"--MULTI\r\n"
            b"Content-Type: message/x-something-unknown\r\n"
            b"\r\n"
            b"opaque\r\n"
            b"--MULTI--\r\n"
        )
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed["subject"] == "Mixed bounce"
        assert any(
            self.NOTIFICATION_TEXT in part["content"] for part in parsed["textBody"]
        )


class TestScalarHeaderDuplicates:
    """RFC 5322 §3.6 makes every scalar header — Date, From, Sender,
    Reply-To, To, Cc, Bcc, Message-ID, In-Reply-To, References, Subject —
    appear at most once. Real-world senders sometimes emit duplicates
    anyway, and the parser must tolerate that.

    Behaviour matches the Python stdlib's
    ``email.message.Message.__getitem__``: when a header is repeated,
    the parser silently uses the first occurrence. Tests pin that
    contract through the public ``parse_email_message`` API so they
    survive a swap from flanker to ``email.parser``.
    """

    BASE_HEADERS = {
        "From": "sender@example.com",
        "To": "recipient@example.com",
        "Subject": "hello",
        "Date": "Thu, 4 Jun 2026 00:47:09 +0000",
        "Message-ID": "<canonical@example.com>",
    }

    @classmethod
    def _build(cls, **overrides: object) -> bytes:
        """Build a minimal email. ``overrides`` may map a header name to
        a single str (default behaviour) or a list[str] (the header is
        emitted multiple times, in that order)."""
        headers = {**cls.BASE_HEADERS, **overrides}
        lines: list[str] = []
        for name, value in headers.items():
            if isinstance(value, list):
                for v in value:
                    lines.append(f"{name}: {v}")
            else:
                lines.append(f"{name}: {value}")
        lines += ["", "body"]
        return ("\r\n".join(lines)).encode("utf-8")

    @pytest.mark.parametrize(
        "header_name,values,result_key,expected_first",
        [
            # Identification: Message-ID and In-Reply-To strip <> on the
            # first value; the second one must not leak into the result.
            (
                "Message-ID",
                ["<first@example.com>", "<second@example.com>"],
                "message_id",
                "first@example.com",
            ),
            (
                "In-Reply-To",
                ["<parent-first@example.com>", "<parent-second@example.com>"],
                "in_reply_to",
                "parent-first@example.com",
            ),
            (
                "References",
                ["<r1@example.com>", "<r2@example.com>"],
                "references",
                "<r1@example.com>",
            ),
            (
                "Subject",
                ["first subject", "second subject"],
                "subject",
                "first subject",
            ),
        ],
    )
    def test_duplicate_scalar_header_takes_first(
        self, header_name, values, result_key, expected_first
    ):
        """A duplicated scalar header must surface only its first
        occurrence in the structured result."""
        raw = self._build(**{header_name: values})
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert parsed[result_key] == expected_first

    def test_duplicate_from_takes_first(self):
        """``From`` is parsed into ``{name, email}`` — first occurrence wins."""
        raw = self._build(**{"From": ["first@example.com", "second@example.com"]})
        parsed = parse_email_message(raw)
        assert parsed["from"]["email"] == "first@example.com"

    @pytest.mark.parametrize(
        "header_name,result_key",
        [("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")],
    )
    def test_duplicate_address_list_header_takes_first(self, header_name, result_key):
        """Only the first occurrence's addresses should appear in the
        structured result for duplicated address-list headers."""
        raw = self._build(**{header_name: ["first@example.com", "second@example.com"]})
        parsed = parse_email_message(raw)
        assert len(parsed[result_key]) == 1
        assert parsed[result_key][0]["email"] == "first@example.com"

    def test_duplicate_date_takes_first(self):
        """The first ``Date`` header wins."""
        first = "Thu, 4 Jun 2026 00:47:09 +0000"
        second = "Fri, 5 Jun 2026 00:00:00 +0000"
        raw = self._build(Date=[first, second])
        parsed = parse_email_message(raw)
        # Assert only the day-of-month — uniquely identifies the choice
        # without depending on tz formatting which can differ across
        # parser backends.
        assert parsed["date"] is not None
        assert parsed["date"].day == 4

    def test_dual_message_id_returns_first(self):
        """A message with two ``Message-ID`` headers must yield a single
        deterministic Message-ID downstream so the ``mime_id``-based
        duplicate-message check in inbound delivery has something
        stable to compare against."""
        raw = self._build(
            **{
                "Message-ID": [
                    "<0S7NGNc8g9oEF8bStCvPthDYCCU0T9dnM20qLmmECY@example.com>",
                    "<cdd440bf7fd91e2e23ac53136b0b860d@example.com>",
                ]
            }
        )
        parsed = parse_email_message(raw)
        assert isinstance(parsed["message_id"], str)
        assert (
            parsed["message_id"]
            == "0S7NGNc8g9oEF8bStCvPthDYCCU0T9dnM20qLmmECY@example.com"
        )

    def test_no_duplication_still_works(self):
        """Regression guard: emails without any duplicated header must
        keep producing the same scalar fields after the helper change."""
        raw = self._build()
        parsed = parse_email_message(raw)
        assert parsed["subject"] == "hello"
        assert parsed["from"]["email"] == "sender@example.com"
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert parsed["message_id"] == "canonical@example.com"

    def test_every_scalar_header_duplicated_simultaneously(self):
        """Stress test: every scalar header per RFC 5322 §3.6 emitted
        twice in the same email must not crash the parser."""
        raw = self._build(
            **{
                "From": ["first-from@example.com", "second-from@example.com"],
                "To": ["first-to@example.com", "second-to@example.com"],
                "Cc": ["first-cc@example.com", "second-cc@example.com"],
                "Bcc": ["first-bcc@example.com", "second-bcc@example.com"],
                "Subject": ["first subj", "second subj"],
                "Date": [
                    "Thu, 4 Jun 2026 00:47:09 +0000",
                    "Fri, 5 Jun 2026 00:00:00 +0000",
                ],
                "Message-ID": ["<id-a@example.com>", "<id-b@example.com>"],
                "In-Reply-To": ["<irt-a@example.com>", "<irt-b@example.com>"],
                "References": ["<ref-a@example.com>", "<ref-b@example.com>"],
                "Reply-To": ["first-rt@example.com", "second-rt@example.com"],
                "Sender": ["first-sender@example.com", "second-sender@example.com"],
            }
        )
        parsed = parse_email_message(raw)
        # Every scalar field must be a plain str (or scalar-shaped
        # dict for ``from``) — never a list.
        assert isinstance(parsed["subject"], str)
        assert isinstance(parsed["from"]["email"], str)
        assert isinstance(parsed["message_id"], str)
        assert isinstance(parsed["in_reply_to"], str)
        assert isinstance(parsed["references"], str)
        # And the first values won.
        assert parsed["subject"] == "first subj"
        assert parsed["from"]["email"] == "first-from@example.com"
        assert parsed["message_id"] == "id-a@example.com"
        assert parsed["in_reply_to"] == "irt-a@example.com"
        assert parsed["references"] == "<ref-a@example.com>"


class TestParsedHeadersTypeContract:
    """The ``parsed["headers"]`` dict follows a fixed per-header contract:

    - Headers registered with max=1 in the IANA Provisional Message
      Header Field Registry (RFC 5322 §3.6, RFC 3834 §5, RFC 2045 /
      2046 / 2183, RFC 4021, RFC 3798, RFC 5703, RFC 8058, RFC 8098)
      are stored as ``str``. On duplication, the first occurrence
      wins — matching stdlib ``email.message.Message[name]`` semantics.
    - Every other header (``received``, ``return-path``, ``precedence``,
      ``dkim-signature``, ``authentication-results``, ``arc-*``,
      ``list-*``, ``comments``, ``keywords``, every ``X-*`` /
      optional-field) is stored as ``list[str]`` in document order.

    Consumers can therefore rely on the type at the call site based on
    the header name alone — no runtime ``isinstance`` checks. Tests
    here exercise the public API only so they survive a swap to the
    Python stdlib parser.
    """

    @pytest.mark.parametrize(
        "header_name",
        [
            # RFC 5322 §3.6
            "subject",
            "from",
            "sender",
            "reply-to",
            "to",
            "cc",
            "bcc",
            "date",
            "message-id",
            "in-reply-to",
            "references",
            # RFC 3834 §5
            "auto-submitted",
            # RFC 2045 / 2046 / 2183
            "mime-version",
            "content-type",
            "content-transfer-encoding",
            # RFC 4021 — message-class indicators
            "importance",
            "priority",
            "sensitivity",
            # RFC 8098 — MDN request
            "disposition-notification-to",
            # RFC 3798 — MDN reporting
            "original-message-id",
            # RFC 5703 — Sieve archive
            "original-from",
            "original-subject",
            # RFC 8058 — one-click unsubscribe
            "list-unsubscribe-post",
        ],
    )
    def test_scalar_header_is_str(self, header_name):
        """Every header registered with max=1 in the IANA registry must
        be ``str`` in ``parsed["headers"]``, even when the input had
        only one occurrence. Pre-emptive coverage: a future consumer
        reading any of these can rely on the type without
        ``isinstance``."""
        raw = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Date: Thu, 4 Jun 2026 00:47:09 +0000\r\n"
            b"Subject: hi\r\n"
            b"Sender: real-sender@example.com\r\n"
            b"Reply-To: replies@example.com\r\n"
            b"Cc: cc@example.com\r\n"
            b"Bcc: bcc@example.com\r\n"
            b"Message-ID: <id@example.com>\r\n"
            b"In-Reply-To: <parent@example.com>\r\n"
            b"References: <ref@example.com>\r\n"
            b"Auto-Submitted: no\r\n"
            b"Importance: high\r\n"
            b"Priority: urgent\r\n"
            b"Sensitivity: Personal\r\n"
            b"Disposition-Notification-To: mdn@example.com\r\n"
            b"Original-Message-ID: <orig@example.com>\r\n"
            b"Original-From: orig@example.com\r\n"
            b"Original-Subject: original subject\r\n"
            b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Transfer-Encoding: 7bit\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email_message(raw)
        value = parsed["headers"].get(header_name)
        assert isinstance(value, str), (
            f"{header_name} expected str, got {type(value).__name__}"
        )

    @pytest.mark.parametrize(
        "header_name,header_value",
        [
            # Trace fields — explicitly unlimited per RFC 5322 §3.6.7.
            ("received", "from a.example.com by b.example.com"),
            ("return-path", "<sender@example.com>"),
            # RFC 5322 §3.6.5 — unlimited.
            ("comments", "a comment"),
            ("keywords", "tag1, tag2"),
            # Optional-field — RFC 5322 §3.6.8 unlimited.
            ("precedence", "bulk"),
            ("x-mailer", "PHPMailer 7.1.1"),
            ("x-priority", "1"),
            # RFC 6376 — multiple signatures expected.
            ("dkim-signature", "v=1; a=rsa-sha256; d=example.com; ..."),
            # RFC 8601 — one per authserv-id, repeatable.
            ("authentication-results", "mta.example.com; spf=pass"),
            # RFC 2369 list-* — repeatable in practice.
            ("list-id", "<list.example.com>"),
            ("list-unsubscribe", "<mailto:u@example.com>"),
        ],
    )
    def test_repeatable_header_is_list(self, header_name, header_value):
        """Every header outside ``_SCALAR_HEADERS`` must be ``list[str]``
        even when it appears only once."""
        raw = (
            b"From: sender@example.com\r\n"
            b"Subject: hi\r\n"
            + header_name.encode("ascii")
            + b": "
            + header_value.encode("ascii")
            + b"\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email_message(raw)
        value = parsed["headers"].get(header_name)
        assert isinstance(value, list), (
            f"{header_name} expected list, got {type(value).__name__}"
        )
        assert value == [header_value]

    def test_multiple_received_preserved_in_order(self):
        """``Received`` is repeatable — every hop must appear in
        document order so spam / forensics consumers can walk the chain."""
        raw = (
            b"Received: from hop3 by hop4\r\n"
            b"Received: from hop2 by hop3\r\n"
            b"Received: from hop1 by hop2\r\n"
            b"From: sender@example.com\r\n"
            b"Subject: traced\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email_message(raw)
        received = parsed["headers"]["received"]
        assert isinstance(received, list)
        assert len(received) == 3
        assert "hop3 by hop4" in received[0]
        assert "hop2 by hop3" in received[1]
        assert "hop1 by hop2" in received[2]

    def test_multiple_dkim_signatures_preserved(self):
        """Multiple ``DKIM-Signature`` headers (RFC 6376) — each relay
        can add its own. All must survive parsing as list entries."""
        sig_a = "v=1; a=rsa-sha256; d=wanadoo.fr; s=t20230301; bh=AAA"
        sig_b = "v=1; a=rsa-sha256; d=ac-limoges.fr; s=default; bh=BBB"
        raw = (
            b"From: sender@example.com\r\n"
            b"Subject: dkim test\r\n"
            b"DKIM-Signature: " + sig_a.encode() + b"\r\n"
            b"DKIM-Signature: " + sig_b.encode() + b"\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email_message(raw)
        signatures = parsed["headers"]["dkim-signature"]
        assert isinstance(signatures, list)
        assert len(signatures) == 2
        assert "wanadoo.fr" in signatures[0]
        assert "ac-limoges.fr" in signatures[1]

    @pytest.mark.parametrize(
        "header_name",
        [
            "received",
            "dkim-signature",
            "authentication-results",
            "comments",
            "keywords",
            "resent-from",
            "resent-to",
            "list-id",
            "x-custom",
        ],
    )
    def test_repeatable_header_preserves_all_occurrences(self, header_name):
        """Every occurrence of a repeatable header must be retained in
        document order — no silent deduplication or truncation."""
        raw = (
            b"From: sender@example.com\r\nSubject: hi\r\n"
            + (header_name.encode() + b": value-A\r\n")
            + (header_name.encode() + b": value-B\r\n")
            + (header_name.encode() + b": value-C\r\n")
            + b"\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        values = parsed["headers"][header_name]
        assert values == ["value-A", "value-B", "value-C"], (
            f"{header_name}: expected all three preserved in order, got {values!r}"
        )

    def test_headers_blocks_always_uses_list_values(self):
        """Inside ``headers_blocks`` every value is ``list[str]``,
        even for scalar headers like Subject — block consumers index
        uniformly so the trusted-relays cut stays simple.

        ``parsed["headers"]`` and ``parsed["headers_blocks"]`` have
        intentionally different shapes; pin both so a future refactor
        doesn't quietly unify them.
        """
        raw = (
            b"Received: from hop1 by hop2\r\n"
            b"From: sender@example.com\r\n"
            b"Subject: scalar in block\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        # parsed["headers"]["subject"] is the str scalar.
        assert parsed["headers"]["subject"] == "scalar in block"
        # parsed["headers_blocks"][N]["subject"] is the same value
        # wrapped in a single-element list.
        for block in parsed["headers_blocks"]:
            if "subject" in block:
                assert isinstance(block["subject"], list)
                assert block["subject"] == ["scalar in block"]
                break
        else:  # pragma: no cover — fail loudly if no block had Subject
            pytest.fail("Subject not found in any header block")

    @pytest.mark.parametrize("case_variant", ["From", "FROM", "from", "FrOm"])
    def test_scalar_header_lookup_is_case_insensitive(self, case_variant):
        """Header names in input are case-insensitive per RFC 5322
        §3.6.8; ``parsed["headers"]`` keys are always lowercase and
        callers must reach values via the lowercase form regardless of
        the wire casing."""
        raw = (
            case_variant.encode("ascii") + b": sender@example.com\r\n"
            b"Subject: hi\r\n\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        assert parsed["headers"]["from"] == "sender@example.com"

    def test_auto_submitted_duplicate_first_wins_per_rfc_3834(self):
        """RFC 3834 §5 makes Auto-Submitted max=1; on duplication we
        take the first value — matching stdlib semantics. A sender
        that emits ``Auto-Submitted: no`` first and
        ``Auto-Submitted: auto-replied`` second is non-compliant; we
        intentionally trust the first occurrence."""
        raw = (
            b"From: sender@example.com\r\n"
            b"Subject: hi\r\n"
            b"Auto-Submitted: no\r\n"
            b"Auto-Submitted: auto-replied\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        assert parsed["headers"]["auto-submitted"] == "no"

    def test_precedence_poison_first_still_detected_as_auto_reply(self):
        """Defence-in-depth for loop detection. ``Precedence`` is
        repeatable per RFC 5322 §3.6.8 (optional-field); the autoreply
        check iterates every occurrence, so a non-bulk value cannot
        mask a later ``bulk`` one and provoke a reply loop."""
        raw = (
            b"From: list@example.com\r\nSubject: newsletter\r\n"
            b"Precedence: not-bulk\r\n"
            b"Precedence: bulk\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
            _is_auto_reply_message,
        )

        assert _is_auto_reply_message(parsed["headers"]) is True

    def test_return_path_poison_first_still_detected_as_bounce(self):
        """Same defence-in-depth as Precedence: a duplicate
        ``Return-Path`` with a non-bounce value first must not mask a
        bounce indicator (``<>`` / empty) later."""
        raw = (
            b"From: MAILER-DAEMON@example.com\r\nSubject: bounce\r\n"
            b"Return-Path: <victim@anywhere.com>\r\n"
            b"Return-Path: <>\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email_message(raw)
        from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
            _is_auto_reply_message,
        )

        assert _is_auto_reply_message(parsed["headers"]) is True

    def test_headers_dict_type_matches_call_site_expectation(self):
        """Real-world inbound with multiple legitimately-repeated
        ``DKIM-Signature`` and ``Received`` headers (RFC 6376 /
        RFC 5322) must pass through the autoreply detection logic —
        which does ``.strip().lower()`` on scalar lookups — without
        raising."""
        raw = (
            b"Received: from hop2 by hop3\r\n"
            b"Received: from hop1 by hop2\r\n"
            b"DKIM-Signature: v=1; a=rsa-sha256; d=a.example.com\r\n"
            b"DKIM-Signature: v=1; a=rsa-sha256; d=b.example.com\r\n"
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: legitimate forwarded mail\r\n"
            b"Precedence: bulk\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email_message(raw)
        # Defer the autoreply import to keep this test isolated from
        # the autoreply module's import-time side effects.
        from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
            _is_auto_reply_message,
        )

        # Must not raise — and the bulk Precedence must be detected.
        assert _is_auto_reply_message(parsed["headers"]) is True


if __name__ == "__main__":
    pytest.main()
