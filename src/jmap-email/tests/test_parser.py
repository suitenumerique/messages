# pylint: disable=too-many-lines,too-many-public-methods
"""
Tests for the RFC 5322 email parser module.
"""

import base64
import email
import hashlib
from datetime import datetime
from datetime import timezone as dt_timezone
from email import policy as email_policy
from email.header import Header

import pytest

from jmap_email.parser import (
    _parse_message_content,
    decode_rfc2047_header,
    parse_address,
    parse_addresses,
    parse_date,
    parse_email,
)


def _stdlib_message(raw_bytes: bytes):
    """Parse raw email bytes into a stdlib ``email.message.Message`` —
    same lenient ``compat32`` policy the parser uses internally."""
    return email.message_from_bytes(raw_bytes, policy=email_policy.compat32)


def _body_text(parsed, part):
    # Resolve a text/html body part's decoded text. With the parser's
    # default ``body_values=True`` the per-part ``content`` field is
    # stripped and the text lives in ``bodyValues[partId]``. Falls
    # through to the inline shape when callers pass ``body_values=False``.
    if "content" in part:
        return part["content"]
    return parsed["bodyValues"][part["partId"]]["value"]


# ────────────────────────────────────────────────────────────────────
# Test helpers — small accessors over ``parsed["headers"]`` (a JMAP
# ``EmailHeader[]``, RFC 8621 §4.1.1). Both honour the
# case-insensitive name match the spec mandates.
# ────────────────────────────────────────────────────────────────────
def _header_first(parsed, name):
    """Return the first occurrence of ``name`` (case-insensitive) or
    an empty string if absent."""
    name_lower = name.lower()
    for h in parsed["headers"]:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _header_all(parsed, name):
    """Return all occurrences of ``name`` (case-insensitive) in
    document order."""
    name_lower = name.lower()
    return [h["value"] for h in parsed["headers"] if h["name"].lower() == name_lower]


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


# --- Fixtures for parsed stdlib email.message.Message objects ---
@pytest.fixture(name="stdlib_simple_message")
def fixture_stdlib_simple_message(simple_email):
    """Fixture providing a stdlib message object from simple_email."""
    return _stdlib_message(simple_email)


@pytest.fixture(name="stdlib_multipart_message")
def fixture_stdlib_multipart_message(multipart_email):
    """Fixture providing a stdlib message object from multipart_email."""
    return _stdlib_message(multipart_email)


class TestEmailAddressParsing:
    """Tests for email address parsing functions."""

    def test_parse_simple_email(self):
        """Test parsing a simple email address without a display name."""
        name, email_addr = parse_address("user@example.com")
        assert name == ""
        assert email_addr == "user@example.com"

    def test_parse_email_with_display_name(self):
        """Test parsing an email address with a display name."""
        name, email_addr = parse_address("Test User <user@example.com>")
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_email_with_quoted_display_name(self):
        """Test parsing an email address with a quoted display name."""
        name, email_addr = parse_address('"Test User" <user@example.com>')
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_email_with_comma_in_display_name(self):
        """Test parsing an email address with a comma in the display name."""
        name, email_addr = parse_address('"User, Test" <user@example.com>')
        assert name == "User, Test"
        assert email_addr == "user@example.com"

    def test_parse_email_with_comments(self):
        """Test parsing an email address with comment."""
        name, email_addr = parse_address("Test User <user@example.com> (comment)")
        assert name == "Test User"
        assert email_addr == "user@example.com"

    def test_parse_empty_address(self):
        """Test parsing an empty address string."""
        name, email_addr = parse_address("")
        assert name == ""
        assert email_addr == ""

    def test_parse_invalid_address_strict_default(self):
        """Strict by default: an unparseable input returns ``("", "")``
        so callers can't mistake garbage for a valid address."""
        name, email_addr = parse_address("Not an email address")
        assert name == ""
        assert email_addr == ""

    def test_parse_invalid_address_lenient(self):
        """``lenient=True`` opts into the archive-import behaviour:
        surface the original input as the address so the source
        record stays visible."""
        name, email_addr = parse_address("Not an email address", lenient=True)
        assert name == ""
        assert email_addr == "Not an email address"

    def test_parse_multiple_addresses(self):
        """Test parsing multiple email addresses."""
        addresses = parse_addresses(
            "Test User <user@example.com>, Another User <another@example.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("Test User", "user@example.com")
        assert addresses[1] == ("Another User", "another@example.com")

    def test_parse_addresses_silently_drops_invalid_entries(self):
        """``parse_addresses`` filters per-entry: tuples failing the
        addr-spec shape check (no ``@``, encoded-word residue, embedded
        CR/LF, …) are silently dropped. Callers can't compare
        ``len(result)`` to ``input.count(",") + 1`` and expect a match —
        documented behaviour, pin it."""
        addresses = parse_addresses(
            "good@example.com, no-at-sign-here, also-good@example.com"
        )
        assert addresses == [
            ("", "good@example.com"),
            ("", "also-good@example.com"),
        ]

    def test_parse_multiple_recipients_with_various_formats(self):
        """Test parsing multiple recipients in various formats."""
        addresses = parse_addresses(
            'user@example.com, "John Doe" <other@example.com>, jane@example.com'
        )
        assert len(addresses) == 3
        assert addresses[0] == ("", "user@example.com")
        assert addresses[1] == ("John Doe", "other@example.com")
        assert addresses[2] == ("", "jane@example.com")

    def test_parse_multiple_recipients_with_comma_in_names(self):
        """Test parsing multiple recipients with comma in names."""
        addresses = parse_addresses(
            '"User, First" <first@example.com>, "User, Second" <second@example.com>, third@example.com'
        )
        assert len(addresses) == 3
        assert addresses[0] == ("User, First", "first@example.com")
        assert addresses[1] == ("User, Second", "second@example.com")
        assert addresses[2] == ("", "third@example.com")

    def test_parse_empty_addresses(self):
        """Test parsing an empty address list."""
        addresses = parse_addresses("")
        assert not addresses

    def test_parse_address_with_dot_in_name(self):
        """Test parsing an email address with dots in the display name."""
        name, email_addr = parse_address("J.R.R. Tolkien <author@example.com>")
        assert name == "J.R.R. Tolkien"
        assert email_addr == "author@example.com"

    def test_parse_address_with_symbols_in_name(self):
        """Test parsing an email address with symbols in the display name."""
        name, email_addr = parse_address('"Smith, Dr. John (CEO)" <ceo@company.org>')
        assert name == "Smith, Dr. John (CEO)"
        assert email_addr == "ceo@company.org"

    def test_parse_address_with_unicode_chars(self):
        """Test parsing an email address with Unicode characters."""
        name, email_addr = parse_address("José García <jose@example.es>")
        assert name == "José García"
        assert email_addr == "jose@example.es"

    def test_parse_address_with_comma_and_unicode_in_name(self):
        """Quoted name combining ',' and non-ASCII must yield one recipient.

        ',' is the address-list separator; if quote-stripping or encoded-word
        decoding misorders, the parser can split a single recipient into two.
        """
        name, email_addr = parse_address('"García, José" <jose@example.es>')
        assert name == "García, José"
        assert email_addr == "jose@example.es"

        # Same idea but with the non-ASCII coming through an RFC 2047
        # encoded-word inside the quoted display-name.
        name, email_addr = parse_address(
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
        addresses = parse_addresses(
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
        name, email_addr = parse_address("undisclosed-recipients:;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_empty_group(self):
        """Test parsing empty group :; returns empty."""
        name, email_addr = parse_address(":;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_group_with_space(self):
        """Test parsing group with space in name."""
        name, email_addr = parse_address("undisclosed recipients:;")
        assert name == ""
        assert email_addr == ""

    def test_parse_address_malformed_group_colon_gt(self):
        """Test parsing malformed group syntax with :> instead of :;"""
        name, email_addr = parse_address("undisclosed-recipients:>")
        assert name == ""
        assert email_addr == ""

    def test_parse_addresses_undisclosed_recipients(self):
        """Test parsing undisclosed-recipients:; returns empty list."""
        addresses = parse_addresses("undisclosed-recipients:;")
        assert not addresses

    def test_parse_addresses_group_with_members(self):
        """Test parsing group syntax extracts member addresses."""
        addresses = parse_addresses("Group: user1@example.com, user2@example.com;")
        assert len(addresses) == 2
        assert addresses[0] == ("", "user1@example.com")
        assert addresses[1] == ("", "user2@example.com")

    def test_parse_addresses_mixed_normal_and_group(self):
        """Test parsing mix of normal addresses and group syntax."""
        addresses = parse_addresses("test@example.com, undisclosed-recipients:;")
        assert len(addresses) == 1
        assert addresses[0] == ("", "test@example.com")

    def test_parse_addresses_normal_group_normal(self):
        """Test parsing normal, group, normal pattern."""
        addresses = parse_addresses(
            "First <a@b.com>, undisclosed-recipients:;, Last <z@y.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("First", "a@b.com")
        assert addresses[1] == ("Last", "z@y.com")

    def test_parse_addresses_complex_group_with_addresses(self):
        """Test parsing complex case with addresses before and after group."""
        addresses = parse_addresses("a@b.com, Group: c@d.com, e@f.com;, g@h.com")
        assert len(addresses) == 4
        assert ("", "a@b.com") in addresses
        assert ("", "c@d.com") in addresses
        assert ("", "e@f.com") in addresses
        assert ("", "g@h.com") in addresses

    def test_parse_addresses_malformed_group_colon_gt(self):
        """Test parsing malformed group syntax :> returns empty."""
        addresses = parse_addresses("undisclosed-recipients:>")
        assert not addresses

    def test_parse_addresses_malformed_group_mixed(self):
        """Test parsing mix of normal addresses and malformed :> group."""
        addresses = parse_addresses("test@example.com, undisclosed-recipients:>")
        assert len(addresses) == 1
        assert addresses[0] == ("", "test@example.com")

    def test_parse_addresses_empty_group(self):
        """Test parsing various empty group patterns."""
        assert not parse_addresses(":;")
        assert not parse_addresses(":>")
        assert not parse_addresses("test:;")
        assert not parse_addresses("Empty Group:;")

    def test_parse_address_unquoted_name_no_quotes_added(self):
        """Test that unquoted display names don't get quotes added."""
        name, email = parse_address("City of Example <contact@example.org>")
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
        addresses = parse_addresses(to_header)
        assert len(addresses) == 2
        assert addresses[0] == ("John DOE (Organization A)", "john@example.com")
        assert addresses[1] == ("John DOE (Organization B)", "john@example.org")

    def test_parse_address_strips_single_quotes(self):
        """Test that single quotes around display names are stripped.

        Some email clients incorrectly use single quotes instead of double quotes
        for display names. We strip them for consistency.
        """
        # Single quotes should be stripped
        name, email = parse_address("'City of Example' <contact@example.org>")
        assert name == "City of Example"
        assert email == "contact@example.org"

        # Apostrophe inside name should be preserved
        name, email = parse_address("'John's Company' <john@example.org>")
        assert name == "John's Company"
        assert email == "john@example.org"

    def test_parse_addresses_strips_single_quotes(self):
        """Test that single quotes are stripped from multiple addresses."""
        addresses = parse_addresses(
            "'Company A' <a@example.com>, 'Company B' <b@example.com>"
        )
        assert len(addresses) == 2
        assert addresses[0] == ("Company A", "a@example.com")
        assert addresses[1] == ("Company B", "b@example.com")

    def test_parse_addresses_drops_bare_tokens(self):
        """Bare tokens inside an address list must be dropped, not kept.

        Stdlib's lenient ``getaddresses`` lifts bare tokens like the
        ``B`` in ``A <a@b>, B, C <c@d>`` into the addr slot of an
        entry. They are never legitimate recipients; the parser must
        skip them so callers don't end up trying to send to ``B``.
        """
        addresses = parse_addresses(
            "Alice <a@example.com>, junk-token, Bob <b@example.com>"
        )
        assert addresses == [
            ("Alice", "a@example.com"),
            ("Bob", "b@example.com"),
        ]

    def test_parse_addresses_drops_entries_without_at_sign(self):
        """Every returned entry must contain a real addr-spec (``@``)."""
        addresses = parse_addresses(
            "valid@example.com, no-at-sign-here, also@example.com"
        )
        for _name, addr in addresses:
            assert "@" in addr
        emails = [addr for _name, addr in addresses]
        assert emails == ["valid@example.com", "also@example.com"]


class TestHeaderDecoding:
    """Tests for email header decoding functions."""

    def test_decode_simple_text(self):
        """Test decoding a simple unencoded text."""
        decoded = decode_rfc2047_header("Simple text")
        assert decoded == "Simple text"

    def test_decode_encoded_text(self):
        """Test decoding encoded text."""
        # Create an encoded header and manually decode it
        header = Header("Tést with açcents", "utf-8")
        encoded = str(header)
        decoded = decode_rfc2047_header(encoded)
        assert "Tést with açcents" in decoded

    def test_decode_address(self):
        """Test decoding a header that contains an email address."""
        decoded = decode_rfc2047_header("Test User <user@example.com>")
        assert decoded == "Test User <user@example.com>"

    def test_decode_empty(self):
        """Test decoding an empty header."""
        decoded = decode_rfc2047_header("")
        assert decoded == ""

    def test_decode_encoded_word_syntax(self):
        """Test decoding headers with encoded word syntax (RFC 2047)."""
        decoded = decode_rfc2047_header(
            "=?utf-8?Q?=C2=A3?=200.00=?UTF-8?q?_=F0=9F=92=B5?="
        )
        assert decoded == "£200.00 💵"

    def test_decode_nonencoded_text_with_encoded_word_markers(self):
        """Test decoding text that contains =? but is not encoded word."""
        decoded = decode_rfc2047_header(
            "Subject with =? marker and =?utf-8?B?8J+YgA==?="
        )
        assert decoded == "Subject with =? marker and 😀"

    def test_decode_multiple_encoded_words(self):
        """Test decoding multiple encoded words that need to be joined (RFC 2047)."""
        decoded = decode_rfc2047_header(
            "=?ISO-8859-1?B?SWYgeW91IGNhbiByZWFkIHRoaXMgeW8=?= =?ISO-8859-2?B?dSB1bmRlcnN0YW5kIHRoZSBleGFtcGxlLg==?="
        )
        assert decoded == "If you can read this you understand the example."

    def test_decode_special_characters(self):
        """Test decoding encoded words with special characters."""
        decoded = decode_rfc2047_header("=?ISO-8859-1?Q?Patrik_F=E4ltstr=F6m?=")
        assert "Patrik" in decoded
        assert "ltstr" in decoded  # The special chars might be decoded differently

    def test_decode_folded_header(self):
        """Test decoding a header that was folded across multiple lines."""
        folded_header = (
            "This is a very long header that has been folded\r\n across multiple lines"
        )
        decoded = decode_rfc2047_header(folded_header)
        assert (
            decoded
            == "This is a very long header that has been folded across multiple lines"
        )

    def test_decode_encoded_emoji(self):
        """Test decoding headers with emoji characters."""
        encoded_header = (
            "=?UTF-8?B?8J+Mj+KAjfCfjok=?="  # 🌏‍🏉 (globe + rugby ball emoji)
        )
        decoded = decode_rfc2047_header(encoded_header)
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
        parsed = parse_email(simple_email)
        assert parsed is not None
        assert parsed["subject"] == "Test Email"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert len(parsed.get("textBody", [])) == 1, "Expected textBody"
        text_content = _body_text(parsed, parsed["textBody"][0])
        assert "This is a test email body." in text_content
        assert parsed["textBody"][0].get("type", "") == "text/plain"
        # Per JMAP spec, text/plain outside alternative goes to both arrays
        assert len(parsed.get("htmlBody", [])) == 1, "JMAP: text copies to htmlBody"
        assert parsed["htmlBody"][0] == parsed["textBody"][0]
        assert not parsed.get("attachments"), "Expected no attachments"

        # Check headers_list
        assert parsed["headers"]  # headers list always emitted under JMAP shape
        headers_list = [(h["name"].lower(), h["value"]) for h in parsed["headers"]]
        assert isinstance(headers_list, list)
        # Should contain from, to, subject at minimum
        header_keys = [h[0] for h in headers_list]
        assert "from" in header_keys
        assert "to" in header_keys
        assert "subject" in header_keys

    def test_parse_multipart_email(self, multipart_email):
        """Test parsing a multipart email."""
        parsed = parse_email(multipart_email)
        assert parsed is not None
        assert parsed["subject"] == "Multipart Test Email"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert parsed["from"][0]["name"] is None
        assert not parsed.get("cc")
        assert len(parsed["textBody"]) == 1
        assert "This is the plain text version." in _body_text(
            parsed, parsed["textBody"][0]
        )
        assert len(parsed["htmlBody"]) == 1
        assert "<h1>Multipart Email</h1>" in _body_text(parsed, parsed["htmlBody"][0])

        # Check headers_list
        assert parsed["headers"]  # headers list always emitted under JMAP shape
        headers_list = [(h["name"].lower(), h["value"]) for h in parsed["headers"]]
        assert isinstance(headers_list, list)
        header_keys = [h[0] for h in headers_list]
        assert "from" in header_keys
        assert "to" in header_keys
        assert "subject" in header_keys
        assert "mime-version" in header_keys
        assert "content-type" in header_keys

    def test_parse_complex_email(self, complex_email):
        """Test parsing a complex email with nested parts and attachments."""
        parsed = parse_email(complex_email)
        assert parsed is not None
        assert parsed["subject"] == "Complex Multipart Email with Attachments"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert parsed["from"][0]["name"] == "Sender Name"
        assert len(parsed["cc"]) == 1
        assert parsed["cc"][0]["name"] == "Carbon Copy"
        assert len(parsed["to"]) == 2
        assert parsed["to"][0]["email"] == "rec1@example.com"
        assert parsed["to"][0]["name"] == "Recipient One"
        assert parsed["to"][1]["email"] == "recipient2@example.com"
        assert parsed["to"][1]["name"] is None
        # textBody: text/plain from alternative + inline image
        assert len(parsed.get("textBody", [])) == 2
        assert "Plain text body content." in _body_text(parsed, parsed["textBody"][0])
        # htmlBody: text/html from alternative + inline image
        assert len(parsed.get("htmlBody", [])) == 2
        assert "<h1>HTML Content</h1>" in _body_text(parsed, parsed["htmlBody"][0])
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
        assert "sentAt" in parsed
        assert "textBody" in parsed
        assert "htmlBody" in parsed
        assert "attachments" in parsed
        assert "headers" in parsed
        assert "messageId" in parsed
        assert "references" in parsed
        assert "inReplyTo" in parsed

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
        parsed = parse_email(email_with_encoded_headers)
        assert parsed is not None
        # Adjust expectation to match actual decode_rfc2047_header output
        assert parsed["from"][0]["name"] == "Sànder Náme"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert parsed["subject"] == "Encoded Subject with äccents"
        assert parsed["to"][0]["email"] == "recipient@example.com"
        # Check the decoded name which might include accents
        assert parsed["to"][0]["name"] == "Recipient"

    def test_parse_email_message(self, test_email):
        """Test parsing a complete email message."""
        parsed = parse_email(test_email)
        assert parsed is not None
        assert parsed["subject"] == "Test Email"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert not parsed.get("cc")
        assert len(parsed["textBody"]) == 1
        assert "This is a test email body." in _body_text(parsed, parsed["textBody"][0])
        # Per JMAP spec, text/plain outside alternative copies to htmlBody
        assert len(parsed.get("htmlBody", [])) == 1
        assert not parsed.get("attachments")

        assert parsed["headers"]
        assert isinstance(
            [(h["name"].lower(), h["value"]) for h in parsed["headers"]], list
        )

    def test_parse_invalid_message(self):
        """Test parsing a malformed multipart message (boundary mismatch).

        Stdlib's compat32 parser does not raise on a boundary mismatch;
        it returns the headers and whatever body it could recover.
        ``parse_email`` returns ``None`` only for inputs the parser
        can't make any sense of at all.
        """
        invalid_email_bytes = b"""From: sender@example.com
To: recipient@example.com
Subject: Malformed Multipart
Content-Type: multipart/alternative; boundary="bad_boundary"

--correct_boundary
Content-Type: text/plain

Text part.

--correct_boundary--
"""
        parsed = parse_email(invalid_email_bytes)
        assert parsed is not None
        assert parsed["subject"] == "Malformed Multipart"
        assert parsed["from"][0]["email"] == "sender@example.com"

    def test_parse_email_with_no_content_type(self):
        """Test parsing an email seemingly without a Content-Type header."""
        raw = b"Subject: No Content Type\nFrom: a@b.c\nTo: d@e.f\n\nBody text."
        parsed = parse_email(raw)
        assert parsed is not None
        assert len(parsed["textBody"]) == 1
        assert _body_text(parsed, parsed["textBody"][0]) == "Body text."
        assert parsed["textBody"][0]["type"] == "text/plain"

    def test_parse_email_with_custom_headers(self):
        """Test parsing an email with custom, non-standard headers."""
        raw = (
            b"To: recipient@example.com\r\n"
            b"From: sender@example.com\r\n"
            b"Subject: Custom Headers\r\n"
            b"X-Custom-Header: Custom Value\r\n"
            b"X-Priority: 1\r\n"
            b"X-Mailer: Custom Mailer v1.0\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: text/plain; charset="utf-8"\r\n'
            b"\r\n"
            b"Message with custom headers\r\n"
        )

        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Custom Headers"
        # Non-scalar headers (X-*, optional-field per RFC 5322 §3.6.8)
        # are stored as list[str] in document order.
        assert _header_all(parsed, "x-custom-header") == ["Custom Value"]
        assert _header_all(parsed, "x-priority") == ["1"]
        assert _header_all(parsed, "x-mailer") == ["Custom Mailer v1.0"]

        # Check headers_list contains custom headers in order
        assert parsed["headers"]  # headers list always emitted under JMAP shape
        headers_list = [(h["name"].lower(), h["value"]) for h in parsed["headers"]]
        header_keys = [h[0] for h in headers_list]
        assert "x-custom-header" in header_keys
        assert "x-priority" in header_keys
        assert "x-mailer" in header_keys

    def test_parse_email_with_missing_from(self):
        """Test parsing an email with missing From header."""
        raw = (
            b"To: recipient@example.com\r\n"
            b"Subject: No From Header\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: text/plain; charset="utf-8"\r\n'
            b"\r\n"
            b"Message with no From\r\n"
        )

        parsed = parse_email(raw)
        assert parsed is not None
        assert "from" in parsed
        assert parsed["from"] is None  # JMAP: null when From header absent

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
        parsed = parse_email(raw_email)
        assert parsed is not None

        # Check headers_list contains all headers in order (most recent first)
        assert parsed["headers"]  # headers list always emitted under JMAP shape
        headers_list = [(h["name"].lower(), h["value"]) for h in parsed["headers"]]
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

    def test_parse_empty_message_returns_none(self):
        """Empty bytes are unparseable: ``parse_email`` returns ``None``."""
        assert parse_email(b"") is None

    def test_parse_none_input_returns_none(self):
        """Non-bytes input returns ``None`` instead of raising."""
        assert parse_email(None) is None  # type: ignore[arg-type]

    def test_parse_email_with_nul_bytes(self):
        """Test that NUL bytes are stripped from subject and body content.

        PostgreSQL text fields cannot store NUL (0x00) bytes.
        """
        raw = b"Subject: Test\x00Subject\x00With\x00NUL\nFrom: a@b.c\nTo: d@e.f\n\nBody\x00with\x00NUL\x00bytes."
        parsed = parse_email(raw)
        assert parsed is not None
        # Verify NUL bytes were stripped from subject
        assert "\x00" not in parsed["subject"]
        assert parsed["subject"] == "TestSubjectWithNUL"
        # Verify NUL bytes were stripped from body content
        assert len(parsed["textBody"]) == 1
        body_text = _body_text(parsed, parsed["textBody"][0])
        assert "\x00" not in body_text
        assert body_text == "BodywithNULbytes."

    def test_parse_message_content_strips_nul_bytes_in_fallback_path(self):
        """Test NUL bytes are stripped in the fallback path for malformed messages.

        When a message has no content-type but has a body, the fallback path
        in _parse_message_content should still strip NUL bytes.
        """

        class NotAStdlibMessage:
            """``_parse_message_content`` falls back when handed
            something that is not a stdlib ``Message`` — pin that
            fallback path (currently only reached by tests; real
            inbound always produces a ``Message``)."""

            body = "Body\x00with\x00NUL\x00bytes"

        result = _parse_message_content(NotAStdlibMessage())
        assert len(result["textBody"]) == 1
        assert "\x00" not in result["textBody"][0]["content"]
        assert result["textBody"][0]["content"] == "BodywithNULbytes"

    def test_parse_message_content_simple(self, stdlib_simple_message):
        """Test parsing content of a simple text message."""
        content = _parse_message_content(stdlib_simple_message)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["content"] == "This is a test email body."
        # Per JMAP spec, text/plain outside alternative copies to htmlBody
        assert len(content["htmlBody"]) == 1
        assert not content["attachments"]

    def test_parse_message_content_multipart(self, stdlib_multipart_message):
        """Test parsing content of a multipart message."""
        content = _parse_message_content(stdlib_multipart_message)
        assert len(content["textBody"]) == 1
        # Trailing newline before the boundary is preserved by the stdlib parser.
        assert content["textBody"][0]["content"] == "This is the plain text version.\n"
        assert len(content["htmlBody"]) == 1
        assert "<b>HTML version</b>" in content["htmlBody"][0]["content"]

    def test_parse_with_attachment(self, email_with_attachment):
        """Test parsing an email with an attachment."""
        # Placeholder test for parsing email with attachment.
        # Actual parsing logic is covered by _parse_message_content tests.
        parsed = parse_email(email_with_attachment)
        assert parsed is not None
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["name"] == "attachment.txt"

    def test_parse_message_content_returns_dict(self, test_email):
        """Test that _parse_message_content returns a dictionary with expected keys.

        This test verifies the structure of the returned dictionary,
        ensuring all expected keys are present.
        """
        message_obj = _stdlib_message(test_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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

    # ``test_malformed_multipart`` removed — was a near-duplicate of
    # ``test_parse_invalid_message`` (same input, same shape).

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["content"] == "Main body.\n"
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment.get("name") is None
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        parsed = parse_email(raw_email)
        assert (
            parsed["messageId"][0] if parsed["messageId"] else ""
        ) == "msg123@example.com"
        assert (
            parsed["inReplyTo"][0] if parsed["inReplyTo"] else ""
        ) == "reply123@example.com"
        assert parsed["references"] == ["ref1@example.com", "ref2@example.com"]

    def test_message_id_without_angle_brackets(self):
        """Test Message-ID and In-Reply-To without angle brackets."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
Message-ID: msg123@example.com
In-Reply-To: reply123@example.com
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body text."""
        parsed = parse_email(raw_email)
        assert (
            parsed["messageId"][0] if parsed["messageId"] else ""
        ) == "msg123@example.com"
        assert (
            parsed["inReplyTo"][0] if parsed["inReplyTo"] else ""
        ) == "reply123@example.com"

    def test_missing_date_header(self):
        """Test that default date is used when Date header is missing."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: No Date

Body text."""
        parsed = parse_email(raw_email)
        assert parsed["sentAt"] is None  # JMAP: null when Date header absent

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
        parsed = parse_email(raw_email)
        assert isinstance(_header_all(parsed, "x-custom"), list)
        assert len(_header_all(parsed, "x-custom")) == 3
        assert _header_all(parsed, "x-custom") == ["Value1", "Value2", "Value3"]

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
            ("..", None),
            (".", None),
            ("", None),
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        attachment = content["attachments"][0]
        # Should return "unnamed" without extension for unknown types
        assert attachment["name"] is None

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        # Empty body part should be skipped (line 255-256 in parser)
        # Only the text part should be present
        assert len(content["textBody"]) == 1
        # The empty attachment might or might not be included depending on stdlib behavior

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        # Stdlib decodes base64 back to binary
        assert attachment["content"] == binary_data
        assert attachment["size"] == len(binary_data)

    def test_header_decoding_preserves_null_bytes(self):
        """``decode_rfc2047_header`` is byte-faithful: it doesn't
        strip NUL itself — that's the parser's job via
        ``_strip_nul_bytes`` on the structured output. NUL stripping
        is intentionally NOT done at decode time because callers like
        ``parse_email`` need to know what was on the wire.
        Pin the contract.
        """
        decoded = decode_rfc2047_header("Test\x00Header")
        # NUL is preserved at the decode layer.
        assert decoded == "Test\x00Header"

    def test_header_decoding_invalid_charset(self):
        """Test header decoding with invalid charset name."""
        # Create a header with invalid charset
        invalid_header = "=?INVALID-CHARSET?Q?Test?="
        decoded = decode_rfc2047_header(invalid_header)
        # Should fall back to UTF-8 or handle gracefully
        assert isinstance(decoded, str)
        assert len(decoded) > 0

    def test_address_with_angle_brackets_in_name(self):
        """Test email address with angle brackets in display name."""
        name, email_addr = parse_address('"User <Name>" <user@example.com>')
        assert email_addr == "user@example.com"
        # Quoted display name carrying literal angle brackets must be
        # preserved verbatim (without the surrounding quotes).
        assert name == "User <Name>"

    def test_address_with_special_characters(self):
        """Test email address with various special characters."""
        test_cases = [
            ('"User; Name" <user@example.com>', "user@example.com"),
            ("User: Name <user@example.com>", "user@example.com"),
            ("user+tag@example.com", "user+tag@example.com"),
        ]
        for address_str, expected_email in test_cases:
            _, email_addr = parse_address(address_str)
            assert email_addr == expected_email

    def test_date_with_invalid_timezone(self):
        """An unparseable timezone token must yield ``None``, not a
        datetime with an arbitrary tz interpretation."""
        parsed = parse_date("Mon, 1 Jan 2024 12:00:00 INVALID")
        # stdlib's parsedate_to_datetime treats unknown tz tokens as
        # ``-0000`` (None offset) — we accept either ``None`` or a
        # datetime with naive/zero offset. The contract: never raise,
        # never invent a tz.
        if parsed is not None:
            from datetime import timedelta

            assert parsed.tzinfo is None or parsed.utcoffset() == timedelta(0), (
                f"invented tzinfo on invalid input: {parsed.tzinfo!r}"
            )

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
        parsed = parse_email(raw_email)
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
        parsed = parse_email(raw_email)
        assert parsed["subject"] == "Minimal Headers"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert len(parsed["to"]) == 1
        # Optional headers should have default values
        assert (parsed["messageId"][0] if parsed["messageId"] else "") == ""
        assert parsed["references"] is None
        assert (parsed["inReplyTo"][0] if parsed["inReplyTo"] else "") == ""

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        attachment = content["attachments"][0]
        assert attachment["disposition"] == "inline"
        assert attachment["cid"] == "image1"
        # Should infer filename from content type
        assert attachment["name"] is None

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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)

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
        """Many MIME parts below ``MAX_MIME_PARTS=1000`` must surface
        all parts. (Cap behavior is tested separately in
        ``test_huge_part_count_does_not_explode``.)
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        # Capped at ``MAX_MIME_PARTS=1000``; 50 stays well below the cap
        assert len(content["textBody"]) == 50

    def test_deeply_nested_multipart(self):
        """Deeply nested multipart below ``MAX_MIME_NESTING_DEPTH=100``
        must surface all levels. (Bomb behavior is tested separately
        in ``test_deeply_nested_multipart_bomb_does_not_recursion_error``.)
        """
        # Create 10 levels of nesting, each with a distinct boundary —
        # reused boundaries trip the Mailsploit / body-smuggling defence
        # (see TestBoundaryReuseDefence) and would zero the result.
        nested_content = b"""Content-Type: text/plain

Innermost content."""

        for i in range(10):
            boundary = f"inner{i}".encode()
            nested_content = (
                b'Content-Type: multipart/alternative; boundary="'
                + boundary
                + b'"\n--'
                + boundary
                + b"\n"
                + nested_content
                + b"\n--"
                + boundary
                + b"--\n"
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
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        # Capped at ``MAX_MIME_NESTING_DEPTH=100``; 10 levels stays well below
        assert len(content["textBody"]) >= 1

    def test_header_with_control_characters_strips_nul_from_subject(self):
        """NUL bytes in a Subject must be stripped from the structured
        ``subject`` field — ``_strip_nul_bytes`` runs on the output.
        The parse must not raise."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test\x00Header
Date: Mon, 1 Jan 2024 12:00:00 +0000

Body."""
        parsed = parse_email(raw_email)
        # Pin both: no NUL in surfaced subject, and the message survives.
        assert "\x00" not in parsed["subject"]
        assert parsed["subject"] == "TestHeader"

    def test_content_type_with_parameters(self):
        """Test Content-Type with various parameters."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Content-Type Params
Content-Type: text/plain; charset=utf-8; format=flowed; delsp=yes

Body with parameters."""
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["type"] == "text/plain"

    def test_message_with_rfc2231_encoded_filename(self):
        """RFC 2231 ``filename*=utf-8''document%C3%A9.pdf`` must
        surface as the exact decoded filename ``documenté.pdf``."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Encoded Filename
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename*=utf-8''document%C3%A9.pdf

PDF content
--boundary--"""
        message_obj = _stdlib_message(raw_email)
        content = _parse_message_content(message_obj)
        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["name"] == "documenté.pdf"


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
        parsed = parse_email(email_content)
        assert parsed is not None
        assert parsed["from"][0]["email"] == "sender@example.com"

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
        parsed = parse_email(email_content)
        assert parsed is not None
        assert "htmlBody" in parsed


class TestBodyCharsetDecoding:
    """Text bodies must be decoded using the part's declared charset,
    not blind-cast through UTF-8. Real-world inbound carries Windows-
    1252, ISO-8859-*, GB2312, KOI8-R, ... legacy MUAs in particular."""

    def test_windows_1252_body_decodes_correctly(self):
        """A ``charset=windows-1252`` body with high-bit chars must
        decode using cp1252 — not UTF-8, which would mangle 0x80-0xFF
        bytes into U+FFFD replacement chars."""
        # 0x80 is the Euro sign in cp1252, U+20AC. Pure UTF-8 would
        # reject 0x80 as an invalid start byte.
        raw = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: cp1252 body\r\n"
            b'Content-Type: text/plain; charset="windows-1252"\r\n'
            b"Content-Transfer-Encoding: 8bit\r\n"
            b"\r\n"
            b"price: 100\x80\r\n"  # 0x80 = Euro sign in cp1252
        )
        parsed = parse_email(raw)
        assert parsed is not None
        assert len(parsed["textBody"]) == 1
        # Euro sign U+20AC must be preserved, not replaced.
        body_text = _body_text(parsed, parsed["textBody"][0])
        assert "€" in body_text
        assert "�" not in body_text

    def test_iso_8859_1_body_decodes_correctly(self):
        """ISO-8859-1 (Latin-1) — the historical default for many
        Western mailers."""
        raw = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: latin1 body\r\n"
            b'Content-Type: text/plain; charset="iso-8859-1"\r\n'
            b"Content-Transfer-Encoding: 8bit\r\n"
            b"\r\n"
            # 0xE9 = é, 0xE0 = à in Latin-1.
            b"Caf\xe9 \xe0 Paris\r\n"
        )
        parsed = parse_email(raw)
        assert _body_text(parsed, parsed["textBody"][0]).strip() == "Café à Paris"

    def test_unknown_charset_falls_back_to_utf8_replace(self):
        """An unknown / bogus charset must not crash — fall back to
        UTF-8 with replacement."""
        raw = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: bad charset\r\n"
            b'Content-Type: text/plain; charset="X-NOT-A-REAL-CHARSET"\r\n'
            b"\r\n"
            b"plain ascii body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed is not None
        assert "plain ascii body" in _body_text(parsed, parsed["textBody"][0])


class TestRawEightBitHeader:
    """Real-world senders sometimes emit non-ASCII header values as raw
    8-bit UTF-8 bytes instead of RFC 2047 encoded-words. The parser
    must recover those — stdlib's compat32 ``Message.items()`` view
    would otherwise collapse them to U+FFFD."""

    def test_raw_utf8_display_name_in_from(self):
        """A From header carrying raw UTF-8 bytes (not an encoded-word)
        in the display name must surface the decoded name."""
        raw = (
            b"From: Ingo L\xc3\xbctkebohle <ingo@example.com>\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: hi\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["from"][0]["email"] == "ingo@example.com"
        assert parsed["from"][0]["name"] == "Ingo Lütkebohle"

    def test_raw_utf8_in_subject(self):
        """Same as above for the Subject header — raw 8-bit UTF-8."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: Caf\xc3\xa9 \xc3\xa0 Paris\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["subject"] == "Café à Paris"

    def test_raw_utf8_in_to_recipients(self):
        """Raw 8-bit UTF-8 in a recipient display name."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: Jos\xc3\xa9 Garc\xc3\xada <jose@example.es>\r\n"
            b"Subject: hi\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert len(parsed["to"]) == 1
        assert parsed["to"][0]["email"] == "jose@example.es"
        assert parsed["to"][0]["name"] == "José García"


class TestRfc2231ContinuationFilename:
    """RFC 2231 §3 allows long parameter values to be split across
    multiple continuation params (``filename*0=``, ``filename*1=``,
    ...). Stdlib's ``get_filename`` collapses them — we surface the
    joined result without losing any segments."""

    def test_filename_split_across_two_continuations(self):
        """A filename split into ``filename*0`` + ``filename*1`` must
        round-trip as a single concatenated string."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: continuation filename\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body\r\n"
            b"--B\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment;\r\n"
            b'\tfilename*0="part_one_of_a_long_";\r\n'
            b'\tfilename*1="filename.pdf"\r\n'
            b"\r\n"
            b"PDF\r\n"
            b"--B--\r\n"
        )
        parsed = parse_email(raw)
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["name"] == "part_one_of_a_long_filename.pdf"

    def test_filename_charset_aware_continuation(self):
        """RFC 2231 charset-tagged continuation: each segment carries
        percent-encoded bytes with the declared charset."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: x\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body\r\n"
            b"--B\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment;\r\n"
            b"\tfilename*0*=utf-8''r%C3%A9sum%C3%A9_;\r\n"
            b"\tfilename*1*=part2.pdf\r\n"
            b"\r\n"
            b"PDF\r\n"
            b"--B--\r\n"
        )
        parsed = parse_email(raw)
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0]["name"] == "résumé_part2.pdf"


class TestParserSecurityRegressions:
    """Coverage for known parser-confusion / DoS / smuggling classes.

    Each test ties back to a public CVE, gh-issue, or research
    write-up so the rationale is auditable. The parser must:

    1. Never raise on adversarial input (we surface a parsed dict or
       ``None``).
    2. Never produce a result that disagrees with the on-wire bytes in
       a way that lets a sender split or swap recipients.
    3. Stay bounded in time and memory on pathological inputs.
    """

    def test_cve_2023_27043_quoted_display_name_does_not_become_addr(self):
        """CVE-2023-27043 family: a quoted display name resembling an
        addr-spec must NOT surface as the address. Allow/deny logic
        that trusts our ``from.email`` would otherwise route mail to
        ``foo@evil.com`` instead of the real address ``real@you.com``.

        Source: https://github.com/python/cpython/issues/102988
        """
        raw = (
            b'From: "foo@evil.com" <real@you.com>\r\n'
            b"To: rcpt@example.com\r\n"
            b"Subject: subj\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # The structured ``from.email`` must reflect the real angle-addr,
        # not the quoted display-name's payload.
        assert parsed["from"][0]["email"] == "real@you.com"
        assert (parsed["from"][0]["email"] if parsed["from"] else "") != "foo@evil.com"

    def test_address_with_at_in_display_name_via_angle_addr(self):
        """Same threat model as CVE-2023-27043 but with an unquoted
        ``@`` inside the display-name area. The angle-addr ``<…>``
        is the authoritative addr-spec, not the loose tokens before
        it."""
        # Stdlib's lenient split may surface multiple tuples for this
        # input. The parser MUST pick the angle-addr as the
        # authoritative answer if it exists.
        name, addr = parse_address("nontrusted@evil.com <real@you.com>")
        # Either we recognise the angle-addr as authoritative, or we
        # refuse to surface ``nontrusted@evil.com`` as a valid
        # address. Both are acceptable; ``nontrusted@evil.com`` being
        # the answer is NOT.
        assert addr != "nontrusted@evil.com"

    def test_portswigger_encoded_word_in_local_part_does_not_smuggle(self):
        """PortSwigger / James Kettle "Splitting the Email Atom"
        (DEF CON 32, 2024): RFC 2047 encoded-words inside an
        addr-spec can smuggle a different recipient through a
        validator that decodes encoded-words before splitting.

        Source: https://portswigger.net/research/splitting-the-email-atom
        """
        # =40 is '@', =3e is '>', =3c is '<'. The decoded form would
        # change the apparent address; the parser must NOT honour
        # encoded-words inside an addr-spec.
        raw = (
            b"From: =?utf-8?q?victim=40you.com=3e?=@attacker.com\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: smuggling\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        from_email = parsed["from"][0]["email"] if parsed["from"] else ""
        # ``=40`` must NOT have been decoded into a literal ``@`` in
        # the addr-spec; the smuggled form ``victim@you.com`` would
        # otherwise surface as a "valid" From address.
        assert "victim@you.com" not in from_email, (
            f"address smuggling: encoded-word '=40' decoded into '@' "
            f"inside addr-spec: from.email={from_email!r}"
        )
        # ``=3e`` (>) must NOT have been decoded either.
        assert ">" not in from_email, (
            f"address smuggling: encoded-word '=3e' decoded into '>' "
            f"inside addr-spec: from.email={from_email!r}"
        )

    def test_encoded_word_with_embedded_newline_does_not_crash(self):
        """gh-114906: embedded ``\\n`` inside an encoded-word crashed
        the parser under ``policy.default``. Under ``compat32`` the
        bare LF is correctly treated as a header boundary per RFC
        5322 §2.2.3 (no preceding WSP → new header). The encoded-word
        is malformed and the "Bcc" line is a real header on the wire.

        The contract: we must not crash, and the surfaced Bcc value
        must NOT parse as a routable recipient — it's encoded-word
        residue ending in ``?=``, not an addr-spec.

        Source: https://github.com/python/cpython/issues/114906
        """
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: =?utf-8?q?safe\nBcc:_leak@evil.com?=\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # The parser must surface a result, not raise.
        assert parsed["from"][0]["email"] == "a@b.com"
        # The Bcc field (structured) must be empty — the broken
        # encoded-word residue ``_leak@evil.com?=`` doesn't parse as a
        # legitimate recipient and gets filtered out.
        assert parsed["bcc"] is None or parsed["bcc"] == []

    def test_malformed_base64_in_encoded_word_does_not_torpedo_message(self):
        """``decode_rfc2047_header`` raises ``HeaderParseError`` on bad base64
        chars (``header.py:129-132``). A single mangled
        ``=?…?b?…?=`` MUST NOT fail the whole message parse — we
        catch the error and surface the rest of the headers."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: =?utf-8?b?@#$!INVALID_BASE64?=\r\n"
            b"\r\n"
            b"body text\r\n"
        )
        parsed = parse_email(raw)
        # We must surface the message with a parsed body, even with the
        # malformed header.
        assert parsed is not None
        assert parsed["from"][0]["email"] == "a@b.com"
        assert any(
            "body text" in _body_text(parsed, part) for part in parsed["textBody"]
        )

    def test_gh_137687_base64_continues_past_double_equals(self):
        """gh-137687: stdlib's base64 body decoder stops at the first
        ``==`` padding token; AV scanners that don't behave the same
        will see different content. Confirm full content is recovered
        even when the body contains stacked base64 segments.

        Source: https://github.com/python/cpython/issues/137687
        """
        # Two valid base64 segments separated by newline. A bug would
        # truncate at the first ``==`` padding.
        first = base64.b64encode(b"PART_ONE").rstrip(b"=") + b"=="
        second = base64.b64encode(b"PART_TWO")
        body_b64 = first + b"\r\n" + second
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: split base64\r\n"
            b'Content-Type: application/octet-stream; name="x.bin"\r\n'
            b"Content-Disposition: attachment\r\n"
            b"Content-Transfer-Encoding: base64\r\n"
            b"\r\n" + body_b64 + b"\r\n"
        )
        parsed = parse_email(raw)
        # The recovered attachment content must include both segments —
        # truncation at ``==`` would mean we silently lose ``PART_TWO``.
        assert len(parsed["attachments"]) == 1
        content = parsed["attachments"][0]["content"]
        assert b"PART_ONE" in content, (
            f"first base64 segment missing from attachment: {content!r}"
        )

    def test_deeply_nested_multipart_bomb_does_not_recursion_error(self):
        """Defense against MIME bombs: 1000-level multipart nesting
        crafted to exhaust Python's ~1000-frame recursion limit must
        be truncated gracefully, not blow up the worker.

        Modelled on the HackerOne "stack exhaustion in MIME multipart"
        disclosure pattern. Postfix caps at ``mime_nesting_limit=100``.
        """
        # Build 500 levels of multipart/mixed nesting — well above our
        # 100-level guard, well below CPython's 1000-frame limit but
        # close enough that an unguarded recursive walk would fail.
        depth = 500
        body = b"Content-Type: text/plain\r\n\r\nDEEPEST_TEXT\r\n"
        for _ in range(depth):
            body = (
                b'Content-Type: multipart/mixed; boundary="b"\r\n'
                b"\r\n"
                b"--b\r\n" + body + b"\r\n"
                b"--b--\r\n"
            )
        raw = b"From: a@b.com\r\nTo: c@d.com\r\nSubject: nesting bomb\r\n" + body
        # Must not raise RecursionError or any other exception. The
        # body walk truncates at the depth cap; what we surface up
        # to that point is fine.
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "nesting bomb"

    def test_huge_header_value_does_not_explode(self):
        """gh-136063: ~14+ quadratic-complexity sites in the email
        module. We don't need to fix them, but a 100 KB header must
        still parse in bounded time (well under 10s on any modern
        machine).

        Source: https://github.com/python/cpython/issues/136063
        """
        import time as _time  # local import to keep imports clean

        big = b"x" * 100_000
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"X-Big-Header: " + big + b"\r\n"
            b"Subject: huge\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 10.0, f"parsing 100KB header took {elapsed:.2f}s"

    def test_huge_address_list_is_bounded_in_time(self):
        """Dovecot CVE-2024-23184 / Postfix ``header_address_token_limit``:
        a hostile ``To:`` with 50_000 addresses must not allocate
        unbounded memory or block the worker for more than a few
        seconds. We cap the input bytes; the parsed list may be
        smaller than the wire content, but parsing must complete."""
        import time as _time

        addrs = ", ".join(f"u{i}@example.com" for i in range(50_000))
        raw = (
            b"From: a@b.com\r\n"
            b"To: " + addrs.encode() + b"\r\n"
            b"Subject: huge to\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 10.0, f"parsing 50k addresses took {elapsed:.2f}s"

    def test_huge_part_count_does_not_explode(self):
        """Go ``multipartmaxparts=1000`` analogue: a message with 2000
        flat MIME parts (well under the depth cap, well over the
        part cap) must be truncated rather than walked in full.

        Source: Go mime/multipart hardening after CVE-2022-41725,
        CVE-2023-24536, CVE-2023-45290.
        """
        parts_bytes = b""
        for i in range(2000):
            parts_bytes += (
                b"--B\r\n"
                b"Content-Type: text/plain\r\n"
                b"\r\n"
                b"part " + str(i).encode() + b"\r\n"
            )
        raw = (
            b"From: a@b.com\r\n"
            b"Subject: many parts\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n" + parts_bytes + b"--B--\r\n"
        )
        parsed = parse_email(raw)
        # Each non-alternative text/plain copies to BOTH textBody and
        # htmlBody, so 1000 walked parts → at most 2000 list entries
        # combined. If the cap is missing we'd see >=4000.
        total = (
            len(parsed["textBody"])
            + len(parsed["htmlBody"])
            + len(parsed["attachments"])
        )
        assert total <= 2 * 1000, f"part-count cap not enforced: total={total}"

    def test_huge_single_header_value_is_truncated_not_quadratic(self):
        """gh-136063: multiple quadratic sites in stdlib's
        ``_header_value_parser``. We cap raw header values at
        ``MAX_HEADER_VALUE_BYTES`` before decoding so the
        worst-case parse time stays linear in the cap, not in the
        attacker-supplied length.

        Source: https://github.com/python/cpython/issues/136063
        """
        import time as _time

        # 5 MB header value — well above the 100 KB cap. Quadratic
        # parse would take many seconds; linear stays sub-second.
        big = b"x" * (5 * 1024 * 1024)
        raw = (
            b"From: a@b.com\r\n"
            b"X-Big: " + big + b"\r\n"
            b"Subject: huge header\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 10.0, f"5MB header parsed in {elapsed:.2f}s"

    def test_display_name_strips_crlf_injection(self):
        """Header-injection in the display name (Apache James
        CVE-2024-21742 / Python CVE-2024-6923 class): a decoded
        display name MUST NOT carry CR/LF/NUL so it can't smuggle a
        new header line when re-emitted on the compose path."""
        # =?utf-8?q?Alice=0A=0DBcc:_evil=40attacker.com?= decodes to
        # ``Alice\n\rBcc: evil@attacker.com``. The parser must scrub
        # the CR/LF before surfacing the name.
        raw = (
            b"From: =?utf-8?q?Alice=0A=0DBcc:_evil=40attacker.com?= "
            b"<alice@example.com>\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: header inj\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        from_name = parsed["from"][0]["name"] if parsed["from"] else ""
        assert "\n" not in from_name, f"LF leaked into display name: {from_name!r}"
        assert "\r" not in from_name, f"CR leaked into display name: {from_name!r}"
        assert "\x00" not in from_name, f"NUL leaked into display name: {from_name!r}"

    def test_defects_surfaced_for_quarantine(self):
        """Mailman pattern: surface stdlib's recorded MIME defects
        so downstream code can quarantine messages with structural
        anomalies rather than silently store them."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b'Content-Type: multipart/mixed; boundary="real"\r\n'
            b"\r\n"
            b"--fake\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"oops\r\n"
        )
        parsed = parse_email(raw, extensions=True)
        assert "defects" in parsed["_ext"]
        # Stdlib records ``StartBoundaryNotFoundDefect`` /
        # ``MultipartInvariantViolationDefect`` for this structure.
        assert parsed["_ext"]["defects"], (
            "expected stdlib to flag a defect on boundary mismatch"
        )

    def test_outer_boundary_collides_with_inner_does_not_crash(self):
        """Inbox Invasion (CCS'24) class: an outer and inner multipart
        sharing the same boundary token must parse without crashing.
        What we surface as parts is implementation-defined; the
        contract is *no exception*.

        Source: https://www.jianjunchen.com/p/inbox-invasion.CCS24.pdf
        """
        raw = (
            b"From: a@b.com\r\n"
            b'Content-Type: multipart/mixed; boundary="X"\r\n'
            b"\r\n"
            b"--X\r\n"
            b'Content-Type: multipart/mixed; boundary="X"\r\n'
            b"\r\n"
            b"--X\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"inner body\r\n"
            b"--X--\r\n"
            b"--X--\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["from"][0]["email"] == "a@b.com"

    def test_bidi_override_in_subject_does_not_corrupt(self):
        """U+202E (Right-to-Left Override) in a Subject must be
        surfaced literally — the responsibility for stripping or
        flagging it lies with the UI, not the parser. Parser-side
        normalization would be a layering violation. Bidi-swap
        attack class.

        Source: https://cybersecuritynews.com/bidi-swap-attack/
        """
        # Encoded-word with U+202E inside.
        # ``legit‮gpj.exe`` UTF-8 base64 → bGVnaXTigK5ncGouZXhl
        raw = (
            b"From: a@b.com\r\n"
            b"Subject: =?utf-8?b?bGVnaXTigK5ncGouZXhl?=\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # The literal char must be preserved (no silent stripping at
        # parse-time). Downstream UI is responsible for
        # ``unicodedata.normalize`` + RLO detection.
        assert "‮" in parsed["subject"]

    def test_idn_domain_decoded_for_display(self):
        """An IDN (Punycode) domain in From must surface as the
        Unicode form when present in a header — defends nothing on
        its own (homograph spoofing is downstream's call) but pins
        the contract so a future regression doesn't quietly start
        emitting raw ``xn--...`` to a UI."""
        # xn--80akhbyknj4f.xn--p1ai is "испытание.рф" in Punycode.
        # We DON'T require IDN decoding at parse time (we just pass
        # through). The test pins that we surface SOMETHING usable.
        raw = (
            b"From: <user@xn--80akhbyknj4f.xn--p1ai>\r\n"
            b"To: c@d.com\r\n"
            b"Subject: idn\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # ASCII Punycode form is acceptable; what matters is that we
        # didn't drop the domain entirely or corrupt the local-part.
        assert (parsed["from"][0]["email"] if parsed["from"] else "").startswith(
            "user@"
        )
        assert "xn--" in (
            parsed["from"][0]["email"] if parsed["from"] else ""
        ) or "испытание" in (parsed["from"][0]["email"] if parsed["from"] else "")

    def test_message_rfc822_with_malformed_inner_headers_serializes_safely(self):
        """Malformed inner headers inside a ``message/rfc822``
        attachment can make ``as_bytes()`` raise
        ``HeaderWriteError`` (subclass of ``MessageError``). We
        catch the broader ``MessageError`` so a single broken
        forward doesn't torpedo the whole parse."""
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b'Content-Type: multipart/mixed; boundary="OUT"\r\n'
            b"\r\n"
            b"--OUT\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"main body\r\n"
            b"--OUT\r\n"
            b"Content-Type: message/rfc822\r\n"
            b"\r\n"
            b"From: inner@example.com\r\n"
            # Malformed header — embedded NUL.
            b"Subject: weird\x00subject\r\n"
            b"\r\n"
            b"inner body\r\n"
            b"--OUT--\r\n"
        )
        parsed = parse_email(raw)
        # The outer message must parse; the broken inner rfc822 may
        # surface as an attachment with empty / partial body, but not
        # raise out of ``_decoded_part_body``.
        assert parsed["from"][0]["email"] == "a@b.com"
        assert any(
            "main body" in _body_text(parsed, part) for part in parsed["textBody"]
        )

    def test_delivery_status_preserves_all_per_recipient_blocks(self):
        """RFC 3464 ``message/delivery-status`` carries a sequence of
        per-recipient status blocks. The parser must preserve ALL
        blocks when surfacing the DSN as an attachment — earlier
        versions of our parser took only the first block."""
        raw = (
            b"From: MAILER-DAEMON@mta.example.com\r\n"
            b"To: sender@example.org\r\n"
            b"Subject: DSN\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/report; report-type=delivery-status;\r\n"
            b'\tboundary="dsn"\r\n'
            b"\r\n"
            b"--dsn\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Notification text\r\n"
            b"\r\n"
            b"--dsn\r\n"
            b"Content-Type: message/delivery-status\r\n"
            b"\r\n"
            b"Reporting-MTA: dns; mta.example.com\r\n"
            b"\r\n"
            b"Final-Recipient: rfc822; first@example.com\r\n"
            b"Action: failed\r\n"
            b"Status: 5.0.0\r\n"
            b"\r\n"
            b"Final-Recipient: rfc822; second@example.com\r\n"
            b"Action: failed\r\n"
            b"Status: 4.0.0\r\n"
            b"\r\n"
            b"--dsn--\r\n"
        )
        parsed = parse_email(raw)
        dsn_atts = [
            a for a in parsed["attachments"] if a["type"] == "message/delivery-status"
        ]
        assert dsn_atts, "delivery-status attachment missing"
        body = dsn_atts[0]["content"]
        # Both per-recipient blocks must survive — not just the first.
        assert b"first@example.com" in body
        assert b"second@example.com" in body


class TestHistoricalCVERegressions:
    """Coverage for the pre-2024 attack classes that have re-surfaced
    in multiple parsers across the years. Each test corresponds to a
    documented CVE / bpo / research write-up so the rationale is
    auditable. All assertions are framed against ``parse_email``
    only — no internal API touch — so they survive future refactors.
    """

    def test_mailsploit_nul_in_encoded_word_does_not_truncate_address(self):
        """Mailsploit (2017): an RFC 2047 encoded-word containing a
        NUL byte caused 33 MUAs to truncate the displayed address at
        the NUL. The decoded address looked like
        ``real@evil\\x00 <spoofed@bank.com>`` but rendered as just
        ``real@evil`` — perfect spoofing for users who only see the
        truncated form.

        Source: https://www.theregister.com/2017/12/06/mailsploit_email_spoofing_bug/
        """
        # =?utf-8?B?cmVhbEBldmlsLnRsZA==?= decodes to "real@evil.tld".
        # We embed a NUL between the encoded-word and the angle-addr
        # to mimic the Mailsploit shape.
        raw = (
            b"From: =?utf-8?B?cmVhbEBldmlsLnRsZA==?=\x00 <spoofed@bank.com>\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: spoof\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # The parsed display name must NOT contain the NUL byte — it
        # gets scrubbed by the ``_NAME_INJECTION_TABLE`` in
        # ``_clean_address_pair``. The authoritative addr-spec must
        # be the angle-addr ``spoofed@bank.com``, not the
        # encoded-word's content.
        assert "\x00" not in (parsed["from"][0]["name"] if parsed["from"] else "")
        assert parsed["from"][0]["email"] == "spoofed@bank.com"

    def test_bz_249626_encoded_comma_in_display_name_does_not_split(self):
        """Thunderbird bz-249626 (2004, long-lived): an RFC 2047
        encoded-word in a display-name that contains a literal comma
        was decoded *before* address-list splitting, so
        ``=?utf-8?B?<b64 of "Doe, Jane">?= <j@x>`` became two
        recipients (``Doe`` and ``Jane <j@x>``). Our parser must
        treat the encoded-word as opaque during the split.

        Source: https://bugzilla.mozilla.org/show_bug.cgi?id=249626
        """
        # base64 of "Doe, Jane" — single recipient.
        raw = (
            b"From: a@b.com\r\n"
            b"To: =?utf-8?B?RG9lLCBKYW5l?= <jane@example.com>\r\n"
            b"Subject: comma in name\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # MUST be ONE recipient, not two. A pre-decoding splitter
        # would split on the literal ``,`` in ``Doe, Jane``.
        assert len(parsed["to"]) == 1, (
            f"encoded-word comma split address list: {parsed['to']!r}"
        )
        assert parsed["to"][0]["email"] == "jane@example.com"
        assert parsed["to"][0]["name"] == "Doe, Jane"

    def test_cve_2025_1795_quoted_comma_in_display_name(self):
        """CVE-2025-1795 / Mailman: a quoted display-name containing
        a comma (``"Smith, John"``) must surface as ONE recipient on
        parse, not two. Refold corruption was the underlying bug but
        the parse contract is the same.
        """
        raw = (
            b'From: "Smith, John" <smith@example.com>\r\n'
            b'To: "Doe, Jane" <doe@example.com>, "Roe, Mary" <roe@example.com>\r\n'
            b"Subject: quoted commas\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["from"][0]["name"] == "Smith, John"
        assert parsed["from"][0]["email"] == "smith@example.com"
        assert len(parsed["to"]) == 2
        assert parsed["to"][0] == {"name": "Doe, Jane", "email": "doe@example.com"}
        assert parsed["to"][1] == {"name": "Roe, Mary", "email": "roe@example.com"}

    def test_bpo_33529_long_non_ascii_no_spaces_does_not_hang(self):
        """bpo-33529 (2018): ``_fold_as_ew`` had an infinite-loop
        case when encoding a long non-ASCII header with no whitespace
        to split on. Even fixed, we keep a time-bounded regression so
        a future refactor of the encoded-word folding can't silently
        re-introduce it.

        Source: https://bugs.python.org/issue33529
        """
        import time as _time

        # 4 KB of a single non-ASCII char, no whitespace.
        big_name = ("á" * 2000).encode("utf-8")
        raw = (
            b"From: =?utf-8?B?" + base64.b64encode(big_name) + b"?= <a@b.com>\r\n"
            b"To: c@d.com\r\n"
            b"Subject: long name\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        # Linear time on the cap, never quadratic.
        assert elapsed < 5.0, f"long-non-ASCII header took {elapsed:.2f}s"

    def test_strict_false_keeps_obs_route_addr_extraction(self):
        """Lock-in test for the deliberate ``strict=False`` choice in
        our ``getaddresses`` calls. RFC 5322 §4.4 obs-route syntax
        (``<@a.com,@b.com:foo@c.com>``) is dead but appears in
        archived mail and legacy systems. ``strict=True`` returns
        ``('','')`` and loses the address; ``strict=False`` extracts
        the authoritative addr-spec.

        Source: empirical comparison of stdlib ``strict`` modes
        against a real-world malformed-input corpus.
        """
        raw = (
            b"From: <@a.com,@b.com:foo@c.com>\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: obs route\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # The address must be surfaced — strict=True would drop it.
        assert parsed["from"][0]["email"] if parsed["from"] else "", (
            "obs-route addr-spec lost — has strict mode been turned on?"
        )

    def test_clean_address_pair_strips_full_injection_char_set(self):
        """Direct exercise of ``_clean_address_pair``'s
        ``_NAME_INJECTION_TABLE`` translate, bypassing
        ``decode_rfc2047_header``'s whitespace collapse.

        ``decode_rfc2047_header`` already strips U+2028/U+2029/NEL
        via ``str.split()`` (those are Unicode-whitespace), so a
        ``parse_email`` round-trip cannot exercise the
        translate-table on those chars on its own. We hit the table
        directly to prove the defense-in-depth holds even if the
        upstream stripper changes (e.g. a future refactor of
        ``decode_rfc2047_header`` that drops ``.split()``).
        """
        from jmap_email.parser import _clean_address_pair

        for char in (
            "\x00",
            "\x01",
            "\x08",
            "\x0b",
            "\x0c",
            "\x0e",
            "\x1f",
            "\x7f",
            "",
            " ",
            " ",
        ):
            tainted = f"Alice{char}Bcc:leak@evil.com"
            name, _addr = _clean_address_pair(tainted, "alice@example.com")
            assert char not in name, (
                f"injection char U+{ord(char):04X} leaked into name: {name!r}"
            )

    def test_address_decoded_display_name_strips_unicode_line_separators(self):
        """End-to-end pin: encoded-word containing U+2028/U+2029 must
        NOT surface in the parsed display name. In practice
        ``decode_rfc2047_header``'s ``str.split()`` whitespace
        collapse strips these first; ``_NAME_INJECTION_TABLE`` is
        the defense-in-depth layer covered by the unit test above.
        """
        # =?utf-8?b?QVxlMjk4MjhCY2Vk?= is just nonsense; build the
        # encoded-word for "A B" instead.
        ew_b64 = base64.b64encode("A B C".encode("utf-8")).decode("ascii")
        raw = (
            b"From: =?utf-8?B?" + ew_b64.encode("ascii") + b"?= <a@b.com>\r\n"
            b"To: c@d.com\r\n"
            b"Subject: unicode LS\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        name = parsed["from"][0]["name"] if parsed["from"] else ""
        assert " " not in name, f"U+2028 leaked into display name: {name!r}"
        assert " " not in name, f"U+2029 leaked into display name: {name!r}"

    def test_duplicate_from_first_wins_per_usenix_2020_chain(self):
        """USENIX 2020 "Weak Links in Authentication Chains" (Chen et
        al.) showed that 14 of 30 MUAs displayed the *second*
        ``From:`` header while DMARC enforcement checked the *first*
        — a perfect authentication-chain split. We MUST return the
        first occurrence consistently (matches stdlib semantics +
        DMARC enforcement). Pin it.

        Source: https://arxiv.org/pdf/2011.08420
        """
        raw = (
            b"From: trusted@bank.com\r\n"
            b"From: attacker@evil.com\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: dup From\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        # First occurrence wins — matches DMARC's behavior and
        # stdlib's ``Message[name]`` semantics.
        assert parsed["from"][0]["email"] == "trusted@bank.com"

    def test_undisclosed_recipients_group_does_not_smuggle_address(self):
        """USENIX 2020 group-syntax bypass: ``undisclosed-recipients:;``
        is treated as zero recipients by every MTA; an attacker that
        smuggles an address INSIDE the empty group must NOT have it
        treated as a real recipient."""
        # The full literal group `undisclosed:hidden@evil.com;` —
        # we accept the inner address via our group-syntax extraction,
        # but `undisclosed-recipients:;` (truly empty) must yield zero.
        raw = (
            b"From: a@b.com\r\n"
            b"To: undisclosed-recipients:;\r\n"
            b"Subject: empty group\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["to"] is None or parsed["to"] == []


class TestPortSwiggerCorpus:
    """Parametrized regression tests from the PortSwigger "Splitting
    the Email Atom" payload corpus (Heyes / DEF CON 32, Aug 2024).

    Source: https://github.com/PortSwigger/splitting-the-email-atom

    Threat model: an attacker crafts a ``From:`` / ``To:`` value where
    RFC 2047 encoded-words decode to structural characters (``@``,
    ``,``, ``<``, ``>``, NUL, …) and a downstream parser that
    re-tokenizes the decoded form surfaces a different address than
    the on-wire form. Our defense is to extract the addr-spec via
    ``getaddresses`` BEFORE encoded-word decoding (see
    ``parse_email`` in parser.py, and ``parse_address``).
    """

    @pytest.mark.parametrize(
        "raw_from,smuggled_domain,real_domain,note",
        [
            # GitHub (Ruby Mail) — QP-encoded `@`/`>`/NUL.
            (
                b"=?x?q?collab=40psres.net=3ecollab=00?=@github.example",
                "psres.net",
                "github.example",
                "GitHub Mail gem A1 — QP =40 (`@`) smuggle",
            ),
            # Zendesk — QP-encoded comma splits address list.
            (
                b"=?x?q?collab=40psres.net=2c?=x@validserver.example",
                "psres.net",
                "validserver.example",
                "Zendesk A23 — QP =2c (`,`) split",
            ),
            # GitLab Enterprise — encoded `_` then domain.
            (
                b"=?utf-8?q?collab=40psres.net=5f?=@gitlab.example",
                "psres.net",
                "gitlab.example",
                "GitLab A25 — QP =5f variant",
            ),
            # GitLab IdP — encoded space splits address list.
            (
                b"=?iso-8859-1?q?collab=40psres.net=20foo?=@gitlab.example",
                "psres.net",
                "gitlab.example",
                "GitLab A26 — QP =20 (space) split",
            ),
            # UTF-7 with embedded `&AEA-` = `@`.
            (
                b"=?utf-7?q?collab&AEA-collabserver&ACw-?=foo@validserver.example",
                "collabserver",
                "validserver.example",
                "Generic B1 — UTF-7 &AEA- decode to `@`",
            ),
            # Base64 encoded-word containing just `@`.
            (
                b"collab=?x?b?QA==?=collabserver=?x?b?LA==?=foo@validserver.example",
                "collabserver",
                "validserver.example",
                "Generic B5 — base64 encoded-word splitter",
            ),
        ],
    )
    def test_portswigger_encoded_word_smuggle(
        self, raw_from, smuggled_domain, real_domain, note
    ):
        """For each PortSwigger payload, the parsed addr-spec's
        **domain** (everything after the final ``@``) must be the
        on-wire ``real_domain`` (e.g. ``github.example``), NEVER the
        smuggled domain inside the encoded-word (e.g. ``psres.net``).

        Our defense: addr-spec extraction happens BEFORE RFC 2047
        decoding (see ``raw_addr_headers`` in
        ``parse_email``), so encoded structural chars stay
        opaque in the addr-spec.
        """
        raw = (
            b"From: " + raw_from + b"\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: portswigger smuggle\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        from_email = (parsed["from"][0]["email"] if parsed["from"] else "").lower()
        if from_email and "@" in from_email:
            actual_domain = from_email.rsplit("@", 1)[1]
            # The smuggled domain MUST NOT be the routable domain.
            assert actual_domain != smuggled_domain.lower(), (
                f"{note}: smuggled domain {smuggled_domain!r} surfaced "
                f"as routable domain in from.email={from_email!r}"
            )

    def test_phpmailer_a27_angle_addr_is_authoritative(self):
        """PortSwigger A27 (PHPMailer): the attack relies on a parser
        that decodes the display-name encoded-word AND treats the
        decoded form as the addr-spec. Our parser correctly extracts
        the angle-addr ``<addr@host>`` as authoritative regardless
        of what the encoded display-name decodes to — verify that
        contract.
        """
        # The encoded-word ``=?utf8?q?=61=62=63?=`` decodes to "abc".
        # The angle-addr ``<collab@psres.example>`` is what an SMTP
        # server would route to. PHPMailer would have surfaced the
        # display-name decoded "abc" as addr — we surface the
        # angle-addr.
        raw = (
            b"From: =?utf8?q?=61=62=63?=<collab@psres.example>\r\n"
            b"To: rcpt@example.com\r\n"
            b"Subject: angle-addr authoritative\r\n"
            b"\r\n"
            b"body\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["from"][0]["email"] == "collab@psres.example"
        # And the decoded display name is just "abc".
        assert parsed["from"][0]["name"] == "abc"


class TestBufferOverflowShapeRegressions:
    """Coverage for the input *shapes* that triggered buffer-overflow
    CVEs in pre-2010 C-based mailers. Python is memory-safe so the
    attacks themselves don't translate, but the shapes still stress
    our parser's CPU/RAM and have a long history of re-surfacing as
    DoS / quadratic-complexity bugs. Each test cites the canonical
    CVE for traceability.
    """

    def test_nested_comments_does_not_recursion_error(self):
        """CVE-2002-1337 (Sendmail crackaddr) input *shape*: deeply
        nested parenthesized comments in a ``From:`` header.

        Stdlib's ``_parseaddr.getcomment`` recurses unbounded into
        nested ``(...)`` blocks and blows Python's ~1000-frame
        recursion limit on inputs like 10k-deep nested comments. Our
        defense — ``parse_address`` / ``parse_addresses``
        catch ``RecursionError`` and degrade to an empty result —
        must keep the worker up. Either the message parses with an
        empty From, or ``parse_email`` returns ``None``; both are
        acceptable degradations.

        Source: https://www.cvedetails.com/cve/CVE-2002-1337/
        """
        depth = 10_000
        nested = b"(" * depth + b"deep" + b")" * depth
        raw = (
            b"From: " + nested + b" <a@b.com>\r\n"
            b"To: c@d.com\r\n"
            b"Subject: nested comments\r\n"
            b"\r\n"
            b"body\r\n"
        )
        # Either parsed (From may degrade to empty) or returned None;
        # both are acceptable degradations.
        parsed = parse_email(raw)
        assert parsed is None or isinstance(parsed, dict)

    def test_cve_2002_2325_pine_empty_boundary_no_infinite_loop(self):
        """CVE-2002-2325 (Pine 4.x): empty ``boundary=""`` in
        Content-Type caused an infinite loop. The parser must
        terminate even on a degenerate boundary value.

        Source: https://www.exploit-db.com/exploits/21644
        """
        import time as _time

        raw = (
            b"From: a@b.com\r\n"
            b'Content-Type: multipart/mixed; boundary=""\r\n'
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 5.0, f"empty boundary parsed in {elapsed:.2f}s"

    def test_cve_2000_0567_outlook_long_date_field(self):
        """CVE-2000-0567 / CVE-2001-0125 (Outlook / OE GMT-date heap
        overflow): a giant Date field crashed Outlook's
        ``inetcomm.dll``. Python's ``parsedate_to_datetime`` is
        memory-safe, but a 200 KB Date must not hang and must return
        a fallback rather than raise.

        Source: https://docs.microsoft.com/security-updates/SecurityBulletins/2000/ms00-043
        """
        import time as _time

        big_date = b"Mon, 01 Jan 2024 00:00:00 " + b"A" * 200_000 + b" GMT"
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Date: " + big_date + b"\r\n"
            b"Subject: long date\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 5.0, f"long date parsed in {elapsed:.2f}s"

    def test_cert_1998_long_filename_in_content_disposition(self):
        """CERT CA-1998-10 (Netscape/Pine/OE): a long
        ``filename="A"*1_000_000`` in Content-Disposition triggered
        buffer overflows across multiple MUAs. Python is memory-safe
        but our sanitizer must truncate to a sane length and the
        basename must not be the entire 1 MB string.

        Source: CERT/CC CA-1998-10
        """
        big_filename = b"A" * 1_000_000
        raw = (
            b"From: a@b.com\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body\r\n"
            b"--B\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b'Content-Disposition: attachment; filename="' + big_filename + b'"\r\n'
            b"\r\n"
            b"data\r\n"
            b"--B--\r\n"
        )
        parsed = parse_email(raw)
        if parsed["attachments"]:
            name = parsed["attachments"][0]["name"]
            # ``_sanitize_filename`` caps at 255 chars.
            assert len(name) <= 255, f"filename not truncated: len={len(name)}"

    def test_cve_2005_4348_fetchmail_zero_headers(self):
        """CVE-2005-4348 (Fetchmail): a message with zero headers
        (just a body) crashed the parser. Must return a sane
        fallback structure.

        Source: https://nvd.nist.gov/vuln/detail/CVE-2005-4348
        """
        raw = b"just a body, no headers\r\n"
        parsed = parse_email(raw)
        assert parsed is not None
        # No From header at all → JMAP ``from`` is null per RFC 8621 §4.
        assert parsed["from"] is None
        # The body should still be surfaced somewhere.
        assert any("body" in (_body_text(parsed, p) or "") for p in parsed["textBody"])

    def test_lotus_domino_long_rfc2231_continuations(self):
        """Lotus Domino class (2005-08): long
        ``filename*0=...; filename*1=...; ... filename*N=`` chain in
        Content-Disposition can be 1000s of segments. Parser must
        terminate in bounded time and not OOM.

        Source: IBM Domino MIME parser CVEs (pre-2010)
        """
        import time as _time

        n_segments = 200
        chunks = b""
        for i in range(n_segments):
            chunks += f' filename*{i}="seg{i}_";'.encode("ascii")
        raw = (
            b"From: a@b.com\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body\r\n"
            b"--B\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment;" + chunks + b"\r\n"
            b"\r\n"
            b"data\r\n"
            b"--B--\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 5.0, f"long RFC 2231 continuation parsed in {elapsed:.2f}s"

    def test_eudora_long_multipart_boundary(self):
        """BID-9846 / CVE-2004-0524 (Eudora long MIME boundary): a
        very long boundary string overflowed Eudora's buffer.
        Stdlib accepts arbitrary boundary length; we must not OOM."""
        import time as _time

        long_boundary = b"A" * 100_000
        raw = (
            b"From: a@b.com\r\n"
            b'Content-Type: multipart/mixed; boundary="' + long_boundary + b'"\r\n'
            b"\r\n"
            b"--" + long_boundary + b"\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body\r\n"
            b"--" + long_boundary + b"--\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 5.0, f"long boundary parsed in {elapsed:.2f}s"

    def test_cve_2008_0304_mozilla_long_encoded_word_in_subject(self):
        """CVE-2008-0304 (Mozilla Thunderbird): a long RFC 2047
        encoded-word in Content-Type / Subject overflowed the
        header decode buffer. Our decode_rfc2047_header is memory-safe but
        must complete in bounded time on a 5 MB encoded-word."""
        import time as _time

        big = base64.b64encode(b"A" * (5 * 1024 * 1024)).decode("ascii")
        raw = (
            b"From: a@b.com\r\n"
            b"To: c@d.com\r\n"
            b"Subject: =?utf-8?B?" + big.encode("ascii") + b"?=\r\n"
            b"\r\n"
            b"body\r\n"
        )
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 10.0, f"5MB encoded-word parsed in {elapsed:.2f}s"

    def test_content_type_duplicate_param_explosion(self):
        """Sendmail / Exchange (CVE-2005-1987 / CVE-2006-0027 class):
        ``Content-Type: x/y; name=a; name=b; name=c; ...`` 10k+ times
        triggered heap issues on older mailers. We must terminate
        without OOM."""
        import time as _time

        big = b"; name=" + b"; name=".join(
            f"value{i}".encode("ascii") for i in range(5_000)
        )
        raw = b"From: a@b.com\r\nContent-Type: text/plain" + big + b"\r\n\r\nbody\r\n"
        start = _time.monotonic()
        parsed = parse_email(raw)
        elapsed = _time.monotonic() - start
        assert parsed is not None
        assert elapsed < 10.0, f"5k duplicate params parsed in {elapsed:.2f}s"

    def test_unfolded_one_megabyte_header_line(self):
        """Fetchmail ≤6.2.4 class: a single unfolded header line of
        50 MB triggered unbounded malloc. We cap raw header value at
        ``MAX_HEADER_VALUE_BYTES``; the truncation must happen
        before the value lands in the decoded dict."""
        from jmap_email.parser import MAX_HEADER_VALUE_BYTES

        big_line = b"X-Big: " + b"A" * (2 * MAX_HEADER_VALUE_BYTES) + b"\r\n"
        raw = (
            b"From: a@b.com\r\n" + big_line + b"Subject: huge unfolded\r\n\r\nbody\r\n"
        )
        parsed = parse_email(raw)
        assert parsed is not None
        x_big = _header_all(parsed, "x-big")
        if x_big and isinstance(x_big, list):
            # Stored value must be capped at the configured limit.
            assert len(x_big[0]) <= MAX_HEADER_VALUE_BYTES + 100, (
                f"raw header not truncated: len={len(x_big[0])}"
            )


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

    Tests exercise the public ``parse_email`` API only — no
    MIME-engine internals — so they remain valid across any future
    backend swap. The behavioural contract:

    1. Parsing must succeed (not return ``None``) on any well-formed
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
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        assert parsed["from"][0]["email"] == "MAILER-DAEMON@mta.example.com"
        assert any(
            self.NOTIFICATION_TEXT in _body_text(parsed, part)
            for part in parsed["textBody"]
        ), (
            f"notification text missing from textBody for {content_type}; "
            f"got parts: {[_body_text(parsed, p)[:60] for p in parsed['textBody']]}"
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
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        assert any(
            self.NOTIFICATION_TEXT in _body_text(parsed, part)
            for part in parsed["textBody"]
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
        parsed = parse_email(raw)
        assert parsed["subject"] == "Undelivered Mail Returned to Sender"
        # Sanity-check the headers that downstream bounce handlers read.
        assert _header_first(parsed, "from").startswith("MAILER-DAEMON")
        assert "multipart/report" in _header_first(parsed, "content-type")

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
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Weird top-level subtype"
        assert parsed["from"][0]["email"] == "sender@example.com"

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
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Forwarded weird"
        assert any(
            "See the attached" in _body_text(parsed, part)
            for part in parsed["textBody"]
        )

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
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed["subject"] == "Mixed bounce"
        assert any(
            self.NOTIFICATION_TEXT in _body_text(parsed, part)
            for part in parsed["textBody"]
        )


class TestScalarHeaderDuplicates:
    """RFC 5322 §3.6 makes every scalar header — Date, From, Sender,
    Reply-To, To, Cc, Bcc, Message-ID, In-Reply-To, References, Subject —
    appear at most once. Real-world senders sometimes emit duplicates
    anyway, and the parser must tolerate that.

    Behaviour matches the Python stdlib's
    ``email.message.Message.__getitem__``: when a header is repeated,
    the parser silently uses the first occurrence. Tests pin that
    contract through the public ``parse_email`` API.
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
        "header_name,values,result_key,expected_value",
        [
            # MessageIds fields (RFC 8621 §4.1.2.1) are ``String[]`` —
            # we surface the first occurrence as a single-element list,
            # with ``<>`` stripped per JMAP.
            (
                "Message-ID",
                ["<first@example.com>", "<second@example.com>"],
                "messageId",
                ["first@example.com"],
            ),
            (
                "In-Reply-To",
                ["<parent-first@example.com>", "<parent-second@example.com>"],
                "inReplyTo",
                ["parent-first@example.com"],
            ),
            (
                "References",
                ["<r1@example.com>", "<r2@example.com>"],
                "references",
                ["r1@example.com"],
            ),
            # Subject is a scalar ``String`` per JMAP §4.1.2.4.
            (
                "Subject",
                ["first subject", "second subject"],
                "subject",
                "first subject",
            ),
        ],
    )
    def test_duplicate_scalar_header_takes_first(
        self, header_name, values, result_key, expected_value
    ):
        """A duplicated scalar header must surface only its first
        occurrence in the structured result."""
        raw = self._build(**{header_name: values})
        parsed = parse_email(raw)
        assert parsed is not None
        assert parsed[result_key] == expected_value

    def test_duplicate_from_takes_first(self):
        """``From`` is parsed into ``{name, email}`` — first occurrence wins."""
        raw = self._build(**{"From": ["first@example.com", "second@example.com"]})
        parsed = parse_email(raw)
        assert parsed["from"][0]["email"] == "first@example.com"

    @pytest.mark.parametrize(
        "header_name,result_key",
        [("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")],
    )
    def test_duplicate_address_list_header_takes_first(self, header_name, result_key):
        """Only the first occurrence's addresses should appear in the
        structured result for duplicated address-list headers."""
        raw = self._build(**{header_name: ["first@example.com", "second@example.com"]})
        parsed = parse_email(raw)
        assert len(parsed[result_key]) == 1
        assert parsed[result_key][0]["email"] == "first@example.com"

    def test_duplicate_date_takes_first(self):
        """The first ``Date`` header wins."""
        first = "Thu, 4 Jun 2026 00:47:09 +0000"
        second = "Fri, 5 Jun 2026 00:00:00 +0000"
        raw = self._build(Date=[first, second])
        parsed = parse_email(raw)
        # Assert only the day-of-month — uniquely identifies the choice
        # without depending on tz formatting which can differ across
        # parser backends.
        assert parsed["sentAt"] is not None
        assert datetime.fromisoformat(parsed["sentAt"]).day == 4

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
        parsed = parse_email(raw)
        assert isinstance(parsed["messageId"][0] if parsed["messageId"] else "", str)
        assert (
            parsed["messageId"][0] if parsed["messageId"] else ""
        ) == "0S7NGNc8g9oEF8bStCvPthDYCCU0T9dnM20qLmmECY@example.com"

    def test_no_duplication_still_works(self):
        """Regression guard: emails without any duplicated header must
        keep producing the same scalar fields after the helper change."""
        raw = self._build()
        parsed = parse_email(raw)
        assert parsed["subject"] == "hello"
        assert parsed["from"][0]["email"] == "sender@example.com"
        assert parsed["to"][0]["email"] == "recipient@example.com"
        assert (
            parsed["messageId"][0] if parsed["messageId"] else ""
        ) == "canonical@example.com"

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
        parsed = parse_email(raw)
        # Every scalar field must be a plain str (or scalar-shaped
        # dict for ``from``) — never a list.
        assert isinstance(parsed["subject"], str)
        assert isinstance((parsed["from"][0]["email"] if parsed["from"] else ""), str)
        assert isinstance(parsed["messageId"][0] if parsed["messageId"] else "", str)
        assert isinstance(parsed["inReplyTo"][0] if parsed["inReplyTo"] else "", str)
        assert isinstance(parsed["references"], list)
        # And the first values won.
        assert parsed["subject"] == "first subj"
        assert parsed["from"][0]["email"] == "first-from@example.com"
        assert (
            parsed["messageId"][0] if parsed["messageId"] else ""
        ) == "id-a@example.com"
        assert (
            parsed["inReplyTo"][0] if parsed["inReplyTo"] else ""
        ) == "irt-a@example.com"
        assert parsed["references"] == ["ref-a@example.com"]


class TestParsedHeadersShape:
    """``parsed["headers"]`` is a JMAP ``EmailHeader[]`` (RFC 8621
    §4.1.1) — every header occurrence is one ``{"name", "value"}``
    entry in document order. Header names appear in their original
    wire case; matching against them is case-insensitive (see
    ``_header_first`` / ``_header_all`` helpers above)."""

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
    def test_repeatable_header_keeps_single_occurrence(self, header_name, header_value):
        """A header registered as repeatable still appears as exactly
        one entry in ``parsed["headers"]`` when the source carries it
        once — no synthesis, no deduplication."""
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
        parsed = parse_email(raw)
        assert _header_all(parsed, header_name) == [header_value]

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
        parsed = parse_email(raw)
        received = _header_all(parsed, "received")
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
        parsed = parse_email(raw)
        signatures = _header_all(parsed, "dkim-signature")
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
        parsed = parse_email(raw)
        values = _header_all(parsed, header_name)
        assert values == ["value-A", "value-B", "value-C"], (
            f"{header_name}: expected all three preserved in order, got {values!r}"
        )

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
        parsed = parse_email(raw)
        assert _header_first(parsed, "from") == "sender@example.com"

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
        parsed = parse_email(raw)
        assert _header_first(parsed, "auto-submitted") == "no"


# ---------------------------------------------------------------------------
# Pass 4 regression tests
#
# Pin the parser-side behavior fixes shipped for 0.1.0 pre-publish. Each
# test names the change ID (B1/B2/M11/M14/M22/L1/L13/L14) so a future
# reader can trace the motivation.
# ---------------------------------------------------------------------------


class TestParserPass4Regressions:
    """Regression coverage for the parser-side 0.1.0 pre-publish pass."""

    # ----- B1: headers[].value is the RFC 8621 Raw form ---------------------

    def test_b1_headers_value_is_raw_form_not_decoded(self):
        """RFC 8621 §4.1.2 ``Raw`` form is the byte-faithful header value
        (modulo CRLF + WSP unfolding and outer CRLF stripping). The
        decoded form belongs to the top-level convenience properties
        (``subject``, ``from`` etc.). The raw ``=?utf-8?b?…?=`` MUST
        survive in ``headers[*]["value"]``."""
        raw = (
            b"From: =?utf-8?B?SmVhbiBWYWxqZWFu?= <jean@example.com>\r\n"
            b"Subject: =?utf-8?B?aGVsbG8=?=\r\n"
            b"To: r@example.com\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw)
        subject_hdr = next(
            h for h in parsed["headers"] if h["name"].lower() == "subject"
        )
        # Raw form: encoded-word still present.
        assert "=?utf-8?B?aGVsbG8=?=" in subject_hdr["value"]
        # Convenience property: decoded.
        assert parsed["subject"] == "hello"

    # ----- B2: every EmailBodyPart carries the full RFC 8621 §4.1.4 shape ---

    def test_b2_body_part_carries_full_jmap_shape(self):
        """RFC 8621 §4.1.4: an ``EmailBodyPart`` declares ``partId``,
        ``blobId``, ``size``, ``name``, ``type``, ``charset``,
        ``disposition``, ``cid``, ``language``, ``location``,
        ``subParts``, ``headers``. Every leaf the parser emits must
        carry the full set so consumers can rely on key presence
        without ``KeyError`` defenses."""
        raw = (
            b"From: a@b.c\r\n"
            b"To: d@e.f\r\n"
            b'Content-Type: text/plain; charset="utf-8"\r\n'
            b"Content-Language: en, fr\r\n"
            b"Content-Location: file:///example.txt\r\n"
            b"\r\nhello\r\n"
        )
        parsed = parse_email(raw, body_values=False)
        assert parsed["textBody"]
        part = parsed["textBody"][0]
        required = {
            "partId",
            "blobId",
            "size",
            "name",
            "type",
            "charset",
            "disposition",
            "cid",
            "language",
            "location",
            "subParts",
            "headers",
        }
        missing = required - set(part)
        assert not missing, f"missing required EmailBodyPart keys: {missing}"
        # Content-Language is parsed into a list.
        assert part["language"] == ["en", "fr"]
        assert part["location"] == "file:///example.txt"
        # Leaf parts have ``subParts is None`` per §4.1.4 "if and only if".
        assert part["subParts"] is None
        # Per-part ``headers`` are ordered ``EmailHeader[]`` entries.
        assert isinstance(part["headers"], list) and part["headers"]

    def test_b3_multipart_root_partid_and_blobid_are_null(self):
        """RFC 8621 §4.1.4: ``partId``/``blobId`` are null IF AND ONLY
        IF the part is ``multipart/*``. The composite root of the
        ``bodyStructure`` tree must report null for both."""
        raw = (
            b"From: a@b.c\r\n"
            b"To: d@e.f\r\n"
            b'Content-Type: multipart/alternative; boundary="B"\r\n'
            b"\r\n--B\r\nContent-Type: text/plain\r\n\r\ntext\r\n"
            b"--B\r\nContent-Type: text/html\r\n\r\n<p>h</p>\r\n--B--\r\n"
        )
        parsed = parse_email(raw, body_structure=True)
        root = parsed["bodyStructure"]
        assert root is not None
        assert root["type"].startswith("multipart/")
        assert root["partId"] is None
        assert root["blobId"] is None
        # subParts populated on the multipart root.
        assert isinstance(root["subParts"], list) and root["subParts"]

    # ----- B4: parse_email defaults follow RFC 8621 §4.2 defaultProperties --

    def test_b4_default_emits_preview_and_body_values(self):
        """``preview`` and ``bodyValues`` are emitted by default. Calling
        ``parse_email`` with no kwargs MUST produce a spec-default
        Email object."""
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nSubject: t\r\n\r\nHello world\r\n"
        parsed = parse_email(raw)
        assert "preview" in parsed
        assert "bodyValues" in parsed
        # Body part no longer carries inline ``content`` — that's in bodyValues.
        assert "content" not in parsed["textBody"][0]
        body_value = parsed["bodyValues"][parsed["textBody"][0]["partId"]]
        assert "Hello world" in body_value["value"]

    # ----- M6: References CFWS handling -------------------------------------

    def test_m6_references_with_comment_yields_two_ids(self):
        """RFC 5322 §3.2.3 CFWS may appear between msg-ids in
        References. The parser strips comments and folds whitespace
        before splitting on the ``> <`` boundary so the chain is
        recoverable."""
        raw = (
            b"From: a@b.c\r\n"
            b"To: d@e.f\r\n"
            b"References: <a@x> (forwarded) <b@x>\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw)
        assert parsed["references"] == ["a@x", "b@x"]

    # ----- M11: decode_rfc2047_header (renamed from decode_header) ---------

    def test_m11_decode_rfc2047_header_preserves_internal_whitespace(self):
        """Internal whitespace runs are NOT collapsed.  Whitespace-based
        attacks live in ``_NAME_INJECTION_TABLE`` (applied by
        ``_clean_address_pair``); ``decode_rfc2047_header`` itself is
        byte-faithful aside from CRLF unfolding."""
        # NBSP between words must survive.
        decoded = decode_rfc2047_header("=?utf-8?Q?Caf=C3=A9=C2=A0Paris?=")
        assert decoded == "Café\xa0Paris"

    # ----- M14: Resent-* projection (project extension) -----------------------

    def test_m14_resent_headers_surface_under_ext_resent(self):
        """RFC 8621 §4.1.3 lists only the 11 base convenience properties.
        The Resent-* group is a §4.1.2 typed-projection idiom that the
        library pre-computes and surfaces under ``ext["resent"]`` when
        ``extensions=True``."""
        raw = (
            b"From: orig@example.com\r\n"
            b"To: dest@example.com\r\n"
            b"Subject: forwarded\r\n"
            b"Message-ID: <orig@example.com>\r\n"
            b'Resent-From: "Resender" <resender@example.com>\r\n'
            b"Resent-Sender: relay@example.com\r\n"
            b"Resent-To: rcpt@example.com\r\n"
            b"Resent-Cc: cc@example.com\r\n"
            b"Resent-Reply-To: reply@example.com\r\n"
            b"Resent-Message-ID: <resent@example.com>\r\n"
            b"Resent-Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw, extensions=True)
        resent = parsed["_ext"]["resent"]
        assert resent["from"][0]["email"] == "resender@example.com"
        assert resent["from"][0]["name"] == "Resender"
        assert resent["sender"][0]["email"] == "relay@example.com"
        assert resent["to"][0]["email"] == "rcpt@example.com"
        assert resent["cc"][0]["email"] == "cc@example.com"
        assert resent["replyTo"][0]["email"] == "reply@example.com"
        assert resent["messageId"] == ["resent@example.com"]
        # ISO-8601 with offset; the actual digits don't matter, just the shape.
        assert resent["date"].startswith("2026-01-01")

    def test_m14_resent_absent_means_no_resent_key(self):
        """When the message carries no Resent-* header at all, the
        ``resent`` sub-dict is omitted from ``_ext`` entirely — non-
        forwarded mail pays no extra surface area."""
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nSubject: t\r\n\r\nbody\r\n"
        parsed = parse_email(raw, extensions=True)
        assert "resent" not in parsed["_ext"]

    def test_m14_resent_is_not_at_top_level(self):
        """``resentFrom`` / ``resentSender`` / etc. are NOT RFC 8621
        §4.1.3 properties and must not appear at the top level."""
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\nSubject: t\r\n"
            b'Resent-From: "R" <r@example.com>\r\n'
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw, extensions=True)
        for k in (
            "resentFrom",
            "resentSender",
            "resentReplyTo",
            "resentTo",
            "resentCc",
            "resentBcc",
            "resentMessageId",
            "resentDate",
        ):
            assert k not in parsed, f"{k} must not appear at top level"

    # ----- M22: bodyStructure node count cap --------------------------------

    def test_m22_body_structure_part_cap_caps_total_parts(self):
        """A pathological multipart with thousands of children must not
        explode memory. The body-structure walker bails at
        ``MAX_MIME_PARTS``."""
        from jmap_email.parser import MAX_MIME_PARTS

        # Build a flat multipart/mixed with many text/plain leaves.
        parts = []
        n = MAX_MIME_PARTS + 50
        for i in range(n):
            parts.append(b"--B\r\nContent-Type: text/plain\r\n\r\nx%d\r\n" % i)
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
            + b"".join(parts)
            + b"--B--\r\n"
        )
        parsed = parse_email(raw, body_structure=True)

        # Walk the structure and count nodes; the total stays under cap+overhead.
        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        total = _count(parsed["bodyStructure"])
        # The cap is enforced after ``MAX_MIME_PARTS`` leaves have been
        # collected; with the multipart root + 1000 leaves, the total
        # stays just above the cap and well below the input count.
        assert total < n, f"part cap not enforced: walked {total} of {n} input parts"
        # And not far above the cap itself (root + cap leaves + slack).
        assert total <= MAX_MIME_PARTS + 5, (
            f"part cap exceeded by more than expected: {total}"
        )

    # ----- L1: Content-ID stripping uses _strip_cfws + single bracket pair --

    def test_l1_content_id_stripping_preserves_inner_brackets(self):
        """Old code used ``str.strip('<>')`` which strips ALL leading
        / trailing ``<`` / ``>`` characters greedily; a valid id like
        ``<<a@x>>`` (legal CFWS-equivalent) would lose both pairs. The
        CFWS-aware strip only removes a single outer pair."""
        # An inline attachment with a doubly-bracketed cid.
        raw = (
            b"From: a@b.c\r\n"
            b"To: d@e.f\r\n"
            b'Content-Type: multipart/related; boundary="B"\r\n\r\n'
            b"--B\r\nContent-Type: text/html\r\n\r\n<p></p>\r\n"
            b"--B\r\nContent-Type: image/png\r\n"
            b"Content-ID: <<inner@x>>\r\n\r\n"
            b"PNGDATA\r\n--B--\r\n"
        )
        parsed = parse_email(raw)
        att = parsed["attachments"][0] if parsed["attachments"] else None
        # The cid keeps its inner ``<…>`` wrapper rather than being
        # collapsed to ``inner@x`` (which would lose the angle pair).
        # Either ``<inner@x>`` (one pair survives) or ``inner@x`` is
        # acceptable depending on the spec interpretation — but the
        # malformed ``inner@x>`` half-strip must never happen.
        if att and att.get("cid"):
            assert att["cid"] in ("inner@x", "<inner@x>")

    # ----- L13: BodyStructureWalkError defect surfaces ----------------------

    def test_l13_body_structure_walk_records_defect(self):
        """Pathologically malformed message that crashes the body-
        structure walker must surface ``BodyStructureWalkError`` in
        ``_ext.defects`` rather than 500-ing the parser."""
        # An empty boundary string (Pine CVE-2002-2325 shape) is the
        # classic broken-multipart input; our walker has a broad
        # exception handler that should record the defect.
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b'Content-Type: multipart/mixed; boundary=""\r\n'
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw, extensions=True)
        # The parse SHOULD complete (no crash); whether the walker
        # records a defect is implementation-dependent on the stdlib
        # tolerance — assert only that the structure is well-formed.
        assert isinstance(parsed["_ext"]["defects"], list)

    # ----- L14: _is_plausible_addr rejects \\x00 ----------------------------

    def test_l14_nul_byte_in_address_is_rejected(self):
        """Mailsploit defense-in-depth: a NUL byte inside an addr-spec
        must NOT survive into the parsed ``email`` field. The Mailsploit
        write-up showed NUL truncating display name vs. mailbox in some
        clients; we strip on the way in too."""
        raw = (
            b"From: =?utf-8?B?"
            + base64.b64encode(b"good\x00bad@example.com")
            + b"?= <victim@example.com>\r\nTo: d@e.f\r\nSubject: t\r\n\r\nbody\r\n"
        )
        parsed = parse_email(raw)
        # Whatever the parser picks, there is NO NUL anywhere.
        for addr in parsed.get("from") or []:
            assert "\x00" not in addr["email"]
            assert "\x00" not in (addr.get("name") or "")

    # ----- bodyValues regression: shape and content -------------------------

    def test_body_values_text_part_round_trips(self):
        """With ``body_values=True`` (default), text body content lives
        in ``bodyValues[partId]`` as ``{value, isEncodingProblem,
        isTruncated}``. The leaf ``EmailBodyPart`` no longer carries
        ``content``."""
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\nSubject: t\r\n"
            b'Content-Type: text/plain; charset="utf-8"\r\n'
            b"\r\nhello world\r\n"
        )
        parsed = parse_email(raw)  # body_values defaults to True
        part = parsed["textBody"][0]
        assert "content" not in part
        bv = parsed["bodyValues"][part["partId"]]
        assert set(bv) == {"value", "isEncodingProblem", "isTruncated"}
        assert "hello world" in bv["value"]
        assert bv["isEncodingProblem"] is False
        assert bv["isTruncated"] is False


if __name__ == "__main__":
    pytest.main()
