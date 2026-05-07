"""
Tests for the RFC5322 email composer module.
"""

# pylint: disable=too-many-lines
import base64
import email
import email.utils
import re
import time
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser

import pytest

import core.mda.rfc5322.composer as _composer_module
from core.mda.rfc5322.composer import (
    EmailComposeError,
    _normalize_date,
    _split_content_type,
    compose_email,
    create_attachment_part,
    create_forward_message,
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

    # The following tests mirror flanker/tests/addresslib/quote_test.py.
    # They are written against format_address (our wrapper) rather than flanker's
    # smart_quote so they survive the migration off flanker. We check only the
    # observable property — that the output round-trips through email.utils
    # back to the original (name, addr) pair.
    def test_format_address_special_chars_roundtrip(self):
        """Each RFC 5322 'special' in a display name must survive round-trip."""

        # Note: whitespace-only specials (leading/trailing space, internal tab)
        # are a separate pre-existing gap — RFC 5322 §3.2.2 folds whitespace,
        # so a literal '\t' in a display name does not survive a round-trip
        # without quoting. Out of scope for the migration regression set.
        for special_name in [
            "Doe, Jane",
            "Doe; Jane",
            "Doe<Jane>",
            "Doe: Jane",
            'He said "hi"',
            "Smith (Admin)",
        ]:
            formatted = format_address(special_name, "user@example.com")
            parsed = email.utils.getaddresses([formatted])
            assert parsed == [(special_name, "user@example.com")], (
                f"Round-trip failed for {special_name!r}: formatted={formatted!r}, parsed={parsed!r}"
            )

    def test_format_address_atext_unquoted(self):
        """Pure RFC 5322 atext names should not be wrapped in quotes."""
        formatted = format_address("Plain Name", "user@example.com")
        assert formatted == "Plain Name <user@example.com>"
        formatted = format_address("Maria Garcia", "user@example.com")
        assert not formatted.startswith('"'), (
            f"atext name should not be quoted: {formatted!r}"
        )


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
            "textBody": ["This is the plain text version.\nIt also tests CRLF."],
            "htmlBody": ["<h1>Hello</h1>\n<p>This is the HTML version</p>"],
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

        assert not re.search(r"(?<!\r)\n", result_bytes.decode("utf-8")), (
            "We don't want LF without CRLF in the body"
        )

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

    def test_compose_with_long_strings(self):
        """Test composing an email with long strings."""
        jmap_data = {
            "from": [{"name": "John Doe", "email": "john@example.com"}],
            "to": [{"name": "Jane Smith", "email": "jane@example.com"}],
            "subject": "Email with Attachment" * 100,
            "textBody": ["Email with attachment " * 100],
            "attachments": [
                {
                    "name": "test - very long" * 100 + ".txt",
                    "type": "text/plain",
                    "content": "SGVsbG8gV29ybGQ=",  # Base64 for "Hello World"
                }
            ],
        }

        result_bytes = compose_email(jmap_data)

        lines = result_bytes.decode("utf-8").split("\r\n")
        # RFC 5322 §2.1.1: lines SHOULD be ≤ 78 octets excluding CRLF.
        assert max(len(line) for line in lines) <= 78

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

        # keep_bcc=True so this contract test exercises the full address-list
        # serialization path including Bcc. Default behavior (Bcc dropped) is
        # covered by TestComposerRFCAudit.test_bcc_dropped_by_default.
        result_bytes = compose_email(jmap_data, keep_bcc=True)
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

    def test_compose_to_header_with_non_ascii_recipients_is_rfc5322_valid(self):
        """Long To header with non-ASCII display names must stay RFC 5322 + 2047 valid.

        Regression test: when any recipient has a non-ASCII display name, the
        underlying MIME library used to re-serialize the whole To header and join
        addresses with '; ' instead of ', '. Because ';' is not a valid
        address-list separator (RFC 5322 §3.4 requires ','), Python's stdlib refold
        in compose_email() then failed to recognise the structure and refolded it
        as unstructured text, hiding '<', '>', '@', ';' inside =?utf-8?q?...?=
        encoded words — which also violates RFC 2047 §5.
        """
        jmap_data = {
            "from": [{"name": "Sender", "email": "sender@example.com"}],
            "to": [
                {"name": "Alice Doe", "email": "alice@example.com"},
                {"name": "Benoît Dupont", "email": "benoit@example.com"},
                {"name": "GARCIA Chloé", "email": "chloe@example.com"},
                {"name": "david.smith", "email": "david@example.com"},
                {"name": "eve@example.com", "email": "eve@example.com"},
                {"name": "MÜLLER Frank", "email": "frank@example.com"},
                {"name": "MARTIN Géraldine", "email": "geraldine@example.com"},
                {"name": "Hélène Roux", "email": "helene@example.com"},
            ],
            "subject": "Test",
            "textBody": ["body"],
        }

        result_bytes = compose_email(jmap_data)
        raw = result_bytes.decode("utf-8")

        # Extract the folded To header.
        to_match = re.search(r"^To:(.*?)(?=^\S)", raw, re.MULTILINE | re.DOTALL)
        assert to_match, "To header not found"
        to_value = to_match.group(1).replace("\r\n", " ").replace("\n", " ").strip()

        # RFC 5322 §3.4: address-list MUST be comma-separated. ';' is only valid
        # as the terminator of a `group:` construct, which we do not use.
        assert "; " not in to_value, (
            f"To header uses ';' as separator (RFC 5322 violation): {to_value!r}"
        )

        # RFC 2047 §5: encoded-words may only appear in the phrase part of an
        # address. They must not encode addr-spec delimiters.
        forbidden = {"=3C", "=3E", "=40", "=3B", "=2C"}  # < > @ ; ,
        for ew in re.findall(r"=\?[^?]+\?[QqBb]\?[^?]*\?=", to_value):
            hits = [tok for tok in forbidden if tok.lower() in ew.lower()]
            assert not hits, (
                f"Encoded-word encodes structural delimiter(s) {hits} "
                f"(RFC 2047 §5 violation): {ew!r}"
            )

        # Round-trip: stdlib must parse back the same set of recipients.
        parsed = BytesParser().parsebytes(result_bytes)
        addrs = email.utils.getaddresses([parsed["To"]])
        emails = {addr for _, addr in addrs}
        expected = {a["email"] for a in jmap_data["to"]}
        assert emails == expected, (
            f"Recipient set mangled by header serialization. "
            f"expected={expected}, got={emails}"
        )

    def test_compose_to_header_with_comma_in_non_ascii_name(self):
        """Display names that contain ',' and non-ASCII chars must round-trip.

        ',' is the address-list separator, so a name like "Doe, Jané" must be
        quoted (RFC 5322 §3.2.4) AND any non-ASCII content must be encoded per
        RFC 2047. After composing, parsing back must yield exactly one recipient
        with the original name. Mixing both axes is the dangerous case: each
        rule alone is fine, but together they have historically broken in the
        underlying MIME library (e.g. naive encoded-word wrapping that drops
        the surrounding quotes, turning the ',' into a list separator).
        """
        jmap_data = {
            "from": [{"name": "Sender", "email": "sender@example.com"}],
            "to": [
                {"name": "Doe, Jané", "email": "jane@example.com"},
                {"name": "Müller, Frank", "email": "frank@example.com"},
                {"name": "Plain Bob", "email": "bob@example.com"},
            ],
            "subject": "Test",
            "textBody": ["body"],
        }

        result_bytes = compose_email(jmap_data)
        parsed = BytesParser().parsebytes(result_bytes)

        addrs = email.utils.getaddresses([parsed["To"]])
        # Decode any encoded-words inside the names before comparing.
        decoded = [(decode_header_string(n), e) for n, e in addrs]

        assert decoded == [
            ("Doe, Jané", "jane@example.com"),
            ("Müller, Frank", "frank@example.com"),
            ("Plain Bob", "bob@example.com"),
        ], (
            f"Comma-in-non-ASCII-name lost in round-trip. "
            f"raw To header: {parsed['To']!r}, decoded: {decoded}"
        )

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
        # Decode + charset-aware: stdlib's set_content may pick a CTE (7bit /
        # quoted-printable) where get_payload() returns the encoded bytes
        # rather than the readable text.
        payload = msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8"
        )
        assert "This email has no subject." in payload

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
        payload = msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8"
        )
        assert "Minimal email." in payload

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


class TestEmailCompositionFlankerRegression:
    """Round-trip / structural tests ported from flanker's mime/message tests.

    These are written as black-box assertions on compose_email output so they
    survive the migration off flanker. They cover the cases flanker's own test
    suite asserted, translated to compose_email-level expectations:

      - non-ASCII bodies use a transfer encoding compatible with 7-bit transport
      - long ASCII bodies stay readable after CRLF reflow
      - very long unstructured header values produce parseable output
      - newlines in user-supplied header values do not break out of the header
        (CRLF injection)
      - non-ASCII attachment filenames round-trip through MIME encoding
      - inline images with Content-ID round-trip preserving the cid
      - text + html + attachment produces multipart/mixed with multipart/
        alternative inside it
    """

    @staticmethod
    def _parse(jmap_data):
        raw = compose_email(jmap_data)
        return raw, BytesParser(policy=policy.default).parsebytes(raw)

    def test_singlepart_non_ascii_body_uses_compatible_cte(self):
        """Mirrors flanker create_singlepart_unicode_qp_test.

        Non-ASCII content cannot ride on Content-Transfer-Encoding: 7bit (the
        bytes don't fit in 7 bits). RFC 6532 / 8BITMIME makes 8bit acceptable
        for modern SMTP, and 7-bit-safe encodings (quoted-printable, base64)
        are always valid. Either is fine; 7bit on non-ASCII is not.
        """
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["Привет, курилка"],
        }
        _, parsed = self._parse(jmap_data)
        cte = (parsed["Content-Transfer-Encoding"] or "").lower()
        assert cte in ("quoted-printable", "base64", "8bit"), (
            f"non-ASCII body needs an 8-bit-aware or encoded CTE, got {cte!r}"
        )
        assert parsed.get_content().rstrip("\r\n") == "Привет, курилка"

    def test_long_ascii_body_lines_under_998_chars(self):
        """Mirrors flanker create_singlepart_ascii_long_lines_test.

        RFC 5322 §2.1.1 caps lines at 998 octets. The composer must reflow long
        lines, but the body text itself must round-trip back to the same string.
        """
        body = "very long line  " * 1000 + "preserve my newlines \r\n\r\n"
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": [body],
        }
        raw, parsed = self._parse(jmap_data)
        for line in raw.split(b"\r\n"):
            assert len(line) <= 998, f"line over RFC 5322 limit: {len(line)} octets"
        # round-trip the textual content (CRLF normalisation is acceptable)
        assert "very long line" in parsed.get_content()

    def test_long_custom_header_value_remains_parseable(self):
        """Mirrors flanker test_bug_line_is_too_long.

        A 10000-char unstructured custom header value must still produce a
        message that parses without error and yields back the original value.
        """
        long_value = "y" * 10000
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["body"],
            "headers": {"X-Long": long_value},
        }
        _, parsed = self._parse(jmap_data)
        # Header parsing must not raise and must recover the value (whitespace
        # may be folded but the y-run must come back intact).
        recovered = re.sub(r"\s+", "", parsed["X-Long"] or "")
        assert recovered == long_value

    def test_newlines_in_subject_do_not_inject_extra_headers(self):
        """Mirrors flanker create_newlines_in_headers_test.

        A user-supplied Subject containing CR/LF must not be able to break out
        of the header and inject new headers or a body separator. The composer
        either strips the newlines or the resulting message still parses with
        the textBody we asked for (i.e. no premature header/body split).
        """
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "Hello,\nInjected: yes\r\n\r\n",
            "textBody": ["legitimate body"],
        }
        _, parsed = self._parse(jmap_data)
        # No phantom header was injected:
        assert parsed["Injected"] is None
        # Body is what we asked for:
        assert "legitimate body" in parsed.get_content()

    def test_attachment_with_non_ascii_filename_roundtrips(self):
        """Mirrors flanker create_multipart_with_attachment_test.

        A binary attachment with a non-ASCII filename must round-trip: the
        filename comes back identical (RFC 2231 / RFC 2047 encoded on the
        wire), and the bytes payload is preserved.
        """
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c63000100000005000100"
            "0d0a2db40000000049454e44ae426082"
        )
        non_ascii_filename = "Мейлган картинка с пробелами.png"
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["see attached"],
            "attachments": [
                {
                    "name": non_ascii_filename,
                    "type": "image/png",
                    "content": base64.b64encode(png_bytes).decode("ascii"),
                }
            ],
        }
        _, parsed = self._parse(jmap_data)
        attachments = [
            p for p in parsed.walk() if p.get_filename() == non_ascii_filename
        ]
        assert attachments, (
            f"non-ASCII filename did not round-trip; filenames seen: "
            f"{[p.get_filename() for p in parsed.walk()]}"
        )
        assert attachments[0].get_payload(decode=True) == png_bytes

    def test_inline_image_with_cid_preserves_content_id(self):
        """Inline image with explicit cid must keep its Content-ID intact."""
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        cid = "img-12345@local"
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "htmlBody": [f'<img src="cid:{cid}">'],
            "attachments": [
                {
                    "name": "img.png",
                    "type": "image/png",
                    "disposition": "inline",
                    "cid": cid,
                    "content": base64.b64encode(png_bytes).decode("ascii"),
                }
            ],
        }
        _, parsed = self._parse(jmap_data)
        cids = [
            (p.get("Content-ID") or "").strip("<>")
            for p in parsed.walk()
            if p.get("Content-ID")
        ]
        assert cid in cids, f"cid {cid!r} not found in {cids!r}"

    def test_text_html_attachment_produces_mixed_with_alternative_inside(self):
        """Mirrors flanker create_multipart_nested_test.

        Text + HTML + a real attachment must produce multipart/mixed at the
        top level (so the attachment is a sibling of the alternative) with
        multipart/alternative nested inside (so the text and HTML are
        alternative renditions of the same content).
        """
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["text version"],
            "htmlBody": ["<p>html version</p>"],
            "attachments": [
                {
                    "name": "data.txt",
                    "type": "text/plain",
                    "content": base64.b64encode(b"raw").decode("ascii"),
                }
            ],
        }
        _, parsed = self._parse(jmap_data)
        assert parsed.get_content_type() == "multipart/mixed"
        subtypes = [p.get_content_type() for p in parsed.iter_parts()]
        assert "multipart/alternative" in subtypes, (
            f"expected multipart/alternative inside multipart/mixed, got {subtypes!r}"
        )

    def test_attachment_only_email_uses_multipart_mixed(self):
        """Email with only a body and one attachment is multipart/mixed."""
        jmap_data = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["see file"],
            "attachments": [
                {
                    "name": "data.txt",
                    "type": "text/plain",
                    "content": base64.b64encode(b"raw").decode("ascii"),
                }
            ],
        }
        _, parsed = self._parse(jmap_data)
        assert parsed.get_content_type() == "multipart/mixed"


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
                    "content": "This is the original message.\nIt also tests CRLF.",
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

        reply["from"] = {"name": "New Sender", "email": "new@example.com"}

        raw_mime = compose_email(reply)
        assert not re.search(r"(?<!\r)\n", raw_mime.decode("utf-8")), (
            "We don't want LF without CRLF in the text body"
        )

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
        assert (
            '<blockquote data-type="quote-separator">'
            in reply["htmlBody"][0]["content"]
        )
        assert "---------- In reply to ----------" in reply["htmlBody"][0]["content"]

    def test_reply_with_long_original(self):
        """Test replying to a long email, ensuring proper quoting."""
        # Create a long message with multiple paragraphs
        long_text = "\r\n\r\n".join(
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

    @pytest.mark.parametrize(
        "raw_id,expected",
        [
            ("plain@example.com", "<plain@example.com>"),
            ("<plain@example.com>", "<plain@example.com>"),
            ("<half@example.com", "<half@example.com>"),
            ("half@example.com>", "<half@example.com>"),
        ],
    )
    def test_reply_message_id_angle_brackets_normalized_per_side(
        self, raw_id, expected
    ):
        """Half-bracketed Message-IDs must be repaired on each side independently.

        Real-world inbound mail occasionally arrives with a missing opening or
        closing angle bracket; create_reply_message must produce a syntactically
        valid In-Reply-To/References regardless of which side is missing.
        """
        original_message = {
            "subject": "Threading test",
            "from": {"name": "Sender", "email": "sender@example.com"},
            "messageId": raw_id,
        }

        reply = create_reply_message(original_message, "reply")

        assert reply["headers"]["In-Reply-To"] == expected
        assert reply["headers"]["References"] == expected

    @pytest.mark.parametrize(
        "raw_id,expected",
        [
            ("plain@example.com", "<plain@example.com>"),
            ("<plain@example.com>", "<plain@example.com>"),
            ("<half@example.com", "<half@example.com>"),
            ("half@example.com>", "<half@example.com>"),
        ],
    )
    def test_compose_in_reply_to_and_message_id_angle_brackets_normalized(
        self, raw_id, expected
    ):
        """Half-bracketed Message-ID/In-Reply-To inputs must be repaired per-side."""
        jmap_data = {
            "subject": "Subject",
            "from": {"name": "S", "email": "s@example.com"},
            "to": [{"name": "R", "email": "r@example.com"}],
            "messageId": raw_id,
            "textBody": [{"partId": "p", "type": "text/plain", "content": "body"}],
        }

        raw_email = compose_email(jmap_data, in_reply_to=raw_id)
        msg = email.message_from_bytes(raw_email)

        assert msg["Message-ID"] == expected
        assert msg["In-Reply-To"] == expected

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


class TestForwardGeneration:
    """Tests for creating forward messages."""

    def test_forward_basic_structure(self):
        """Test basic forward message creation."""
        original_message = {
            "subject": "Original Subject",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "cc": [{"name": "CC Recipient", "email": "cc@example.com"}],
            "messageId": "<original-message-id-12345@example.com>",
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Original message content.",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        forward_text = "This is a forward message."

        forward = create_forward_message(original_message, forward_text)

        # Check subject
        assert forward["subject"] == "Fwd: Original Subject"

        # Check text body contains forward header and original content
        text_content = forward["textBody"][0]["content"]
        assert "This is a forward message." in text_content
        assert "---------- Forwarded message ----------" in text_content
        assert "From: Original Sender <original@example.com>" in text_content
        assert "To: Recipient <recipient@example.com>" in text_content
        assert "Cc: CC Recipient <cc@example.com>" in text_content
        assert "Subject: Original Subject" in text_content
        assert "Original message content." in text_content

    def test_forward_with_html(self):
        """Test forward message creation with HTML content."""
        original_message = {
            "subject": "HTML Original",
            "from": {"name": "HTML Sender", "email": "html@example.com"},
            "to": [{"name": "HTML Recipient", "email": "htmlrecip@example.com"}],
            "messageId": "<html-original@example.com>",
            "htmlBody": [
                {
                    "partId": "html-1",
                    "type": "text/html",
                    "content": "<p>Original <strong>HTML</strong> content.</p>",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        forward_html = "<p>Forward HTML content.</p>"

        forward = create_forward_message(original_message, "Forward text", forward_html)

        # Check HTML body
        html_content = forward["htmlBody"][0]["content"]
        assert "<p>Forward HTML content.</p>" in html_content
        assert '<blockquote data-type="quote-separator">' in html_content
        assert "---------- Forwarded message ----------" in html_content
        assert (
            "<strong>From:</strong> HTML Sender &lt;html@example.com&gt;<br/>"
            in html_content
        )
        assert "<p>Original <strong>HTML</strong> content.</p>" in html_content

    def test_forward_without_original(self):
        """Test forward message creation without including original content."""
        original_message = {
            "subject": "Original Subject",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Original message content.",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        forward_text = "This is a forward message."

        forward = create_forward_message(
            original_message, forward_text, include_original=False
        )

        # Check subject
        assert forward["subject"] == "Fwd: Original Subject"

        # Check text body contains only forward text, no original content
        text_content = forward["textBody"][0]["content"]
        assert text_content == "This is a forward message."
        assert "---------- Forwarded message ----------" not in text_content
        assert "Original message content." not in text_content

    def test_forward_already_fwd_subject(self):
        """Test forward message with subject that already starts with Fwd:."""
        original_message = {
            "subject": "Fwd: Already Forwarded",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "to": [{"name": "Recipient", "email": "recipient@example.com"}],
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Original message content.",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        forward_text = "This is a forward message."

        forward = create_forward_message(original_message, forward_text)

        # Check subject doesn't get double Fwd: prefix
        assert forward["subject"] == "Fwd: Already Forwarded"

    def test_forward_empty_recipients(self):
        """Test forward message creation with empty recipient lists."""
        original_message = {
            "subject": "Empty Recipients",
            "from": {"name": "Original Sender", "email": "original@example.com"},
            "to": [],
            "cc": [],
            "messageId": "<empty-recipients@example.com>",
            "textBody": [
                {
                    "partId": "text-1",
                    "type": "text/plain",
                    "content": "Original message content.",
                }
            ],
            "date": datetime(2023, 5, 15, 14, 30, 0, tzinfo=timezone.utc),
        }

        forward_text = "This is a forward message."

        forward = create_forward_message(original_message, forward_text)

        # Check text body doesn't crash with empty recipients
        text_content = forward["textBody"][0]["content"]
        assert "This is a forward message." in text_content
        assert "---------- Forwarded message ----------" in text_content
        assert "From: Original Sender <original@example.com>" in text_content
        assert "Subject: Empty Recipients" in text_content
        # Should not have To: or Cc: lines since they're empty
        assert "To:" not in text_content
        assert "Cc:" not in text_content


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
            assert attachment_part["Content-ID"] == case["expected"], (
                f"Content-ID not properly formatted for input '{case['cid']}'"
            )


class TestComposerSecurityAndHardening:
    """T1-T8 — security, edge-case, and contract tests for the composer.

    These tests came from a paranoid review of the stdlib migration. Every
    test here either:
      - exercises a path that no other test reaches (e.g. prepend_headers,
        _split_content_type internals), or
      - locks in a security guarantee (header injection via cid/filename/
        Unicode line-separator), or
      - locks in an API contract (compose_email is non-mutating, etc.).
    """

    @staticmethod
    def _compose_and_parse(jmap_data, **kwargs):
        raw = compose_email(jmap_data, **kwargs)
        return raw, BytesParser(policy=policy.default).parsebytes(raw)

    @staticmethod
    def _minimal_jmap(**overrides):
        base = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["body"],
        }
        base.update(overrides)
        return base

    # --- T1: prepend_headers ---------------------------------------------

    def test_t1_prepend_headers_appear_at_top_and_are_present(self):
        """prepend_headers entries must appear before From/To/Subject."""
        raw, parsed = self._compose_and_parse(
            self._minimal_jmap(),
            prepend_headers=[
                ("Auto-Submitted", "auto-replied"),
                ("Precedence", "bulk"),
            ],
        )
        assert parsed["Auto-Submitted"] == "auto-replied"
        assert parsed["Precedence"] == "bulk"
        text = raw.decode("utf-8")
        # Auto-Submitted must precede From: in the byte stream.
        assert text.index("Auto-Submitted:") < text.index("From:")

    def test_t1_prepend_headers_sanitizes_crlf_in_value(self):
        """CR/LF in a prepend_headers value must not break out of the header."""
        _, parsed = self._compose_and_parse(
            self._minimal_jmap(),
            prepend_headers=[("X-StMsg-Note", "ok\r\nX-Injected: yes\r\n")],
        )
        # Value is sanitized: no newline, and no phantom header was injected.
        assert "\r" not in (parsed["X-StMsg-Note"] or "")
        assert "\n" not in (parsed["X-StMsg-Note"] or "")
        assert parsed["X-Injected"] is None

    def test_t1_prepend_headers_rejects_reserved_names(self):
        """prepend_headers must not be allowed to shadow envelope headers."""
        _, parsed = self._compose_and_parse(
            self._minimal_jmap(subject="Real subject"),
            prepend_headers=[
                ("Subject", "Spoofed subject"),
                ("From", "Fake <attacker@evil.com>"),
                ("X-Allowed", "kept"),
            ],
        )
        # Reserved entries dropped; only one Subject and one From, with our
        # original values.
        assert parsed.get_all("Subject") == ["Real subject"]
        assert parsed.get_all("From") == ["S <s@example.com>"]
        # Non-reserved prepend_headers entries pass through.
        assert parsed["X-Allowed"] == "kept"

    # --- T2: Content-ID injection ---------------------------------------

    def test_t2_cid_with_crlf_is_sanitized(self):
        """Attacker-controlled cid must not inject extra headers."""
        jmap = self._minimal_jmap(
            attachments=[
                {
                    "name": "img.png",
                    "type": "image/png",
                    "content": base64.b64encode(b"\x89PNG\r\n").decode("ascii"),
                    "disposition": "inline",
                    "cid": "id1>\r\nX-Injected: evil\r\n<id2",
                }
            ],
        )
        _, parsed = self._compose_and_parse(jmap)
        # No phantom header.
        assert parsed["X-Injected"] is None
        # cid is preserved structurally without CR/LF in any part.
        for part in parsed.walk():
            cid = part.get("Content-ID")
            if cid:
                assert "\r" not in cid
                assert "\n" not in cid

    def test_t2_cid_with_unicode_line_separator_is_sanitized(self):
        """U+2028/U+2029 must not survive into Content-ID."""
        jmap = self._minimal_jmap(
            attachments=[
                {
                    "name": "img.png",
                    "type": "image/png",
                    "content": base64.b64encode(b"\x89PNG\r\n").decode("ascii"),
                    "disposition": "inline",
                    "cid": "id\u2028split\u2029tail",
                }
            ],
        )
        _, parsed = self._compose_and_parse(jmap)
        for part in parsed.walk():
            cid = part.get("Content-ID")
            if cid:
                assert "\u2028" not in cid
                assert "\u2029" not in cid

    # --- T3: filename injection -----------------------------------------

    def test_t3_attachment_filename_with_crlf_is_sanitized(self):
        """Attacker-controlled filename must not inject extra headers."""
        jmap = self._minimal_jmap(
            attachments=[
                {
                    "name": "ok.png\r\nX-Injected: evil\r\n",
                    "type": "image/png",
                    "content": base64.b64encode(b"\x89PNG\r\n").decode("ascii"),
                }
            ],
        )
        _, parsed = self._compose_and_parse(jmap)
        assert parsed["X-Injected"] is None
        # Filename is preserved without the CR/LF.
        attachment_filenames = [
            p.get_filename() for p in parsed.walk() if p.get_filename()
        ]
        assert any(
            name and "\r" not in name and "\n" not in name
            for name in attachment_filenames
        )

    # --- T4: _split_content_type ---------------------------------------

    def test_t4_split_content_type_strips_parameters(self):
        """_split_content_type must drop RFC 2045 params from the subtype."""
        assert _split_content_type("image/jpeg") == ("image", "jpeg")
        assert _split_content_type("image/jpeg; name=foo.jpg") == ("image", "jpeg")
        assert _split_content_type("text/plain; charset=utf-8") == ("text", "plain")
        assert _split_content_type("  image  /  jpeg  ; charset=utf-8") == (
            "image",
            "jpeg",
        )
        # Empty / malformed → fall back to application/octet-stream.
        assert _split_content_type("") == ("application", "octet-stream")
        assert _split_content_type("garbage") == ("application", "octet-stream")
        assert _split_content_type("/jpeg") == ("application", "octet-stream")
        assert _split_content_type("image/") == ("application", "octet-stream")

    # --- T5: _normalize_date with malformed inputs ----------------------

    def test_t5_normalize_date_handles_garbage(self):
        """Unrecognised date input falls back to now() with a warning."""
        before = datetime(2020, 1, 1, tzinfo=timezone.utc)
        # Truly malformed string
        result = _normalize_date("not a real date")
        assert result.tzinfo is not None
        assert result > before

    def test_t5_normalize_date_handles_int_epoch(self):
        """Integer epoch seconds are accepted (POSIX timestamp)."""
        # 2026-01-01T00:00:00Z
        result = _normalize_date(1767225600)
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_t5_normalize_date_handles_naive_datetime(self):
        """A naive datetime gets UTC tz attached."""
        naive = datetime(2024, 6, 15, 12, 0, 0)
        result = _normalize_date(naive)
        assert result.tzinfo is not None
        assert result.utcoffset() == datetime.fromtimestamp(0, timezone.utc).utcoffset()

    def test_t5_normalize_date_handles_iso_with_z(self):
        """Python 3.11+ fromisoformat accepts trailing Z directly."""
        result = _normalize_date("2024-06-15T12:00:00Z")
        assert result.year == 2024 and result.month == 6 and result.day == 15
        assert result.tzinfo is not None

    # --- T6: compose_email is non-mutating ------------------------------

    def test_t6_compose_email_does_not_mutate_input(self):
        """Calling compose_email twice with the same dict must succeed and
        not leave the dict in a different shape after the first call."""
        original = {
            "from": [{"name": "S", "email": "s@example.com"}],  # list form
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": "single string",  # str form, gets list-wrapped
            "htmlBody": "<p>html</p>",
        }
        snapshot_from = original["from"]
        snapshot_text = original["textBody"]
        snapshot_html = original["htmlBody"]

        compose_email(original)

        # The caller's references must still point at the same objects of the
        # same type. Pre-fix this would fail: 'from' was replaced with the
        # first dict, textBody/htmlBody were rewrapped into lists.
        assert original["from"] is snapshot_from
        assert original["from"] == [{"name": "S", "email": "s@example.com"}]
        assert original["textBody"] is snapshot_text
        assert original["htmlBody"] is snapshot_html

        # Second call must succeed identically.
        compose_email(original)

    # --- T7: composer module imports cleanly under the test runtime -----

    def test_t7_composer_module_imports(self):
        """Smoke test: the module loaded successfully (pytest already imported
        it to collect this test).

        Note: do NOT call importlib.reload here. After a reload, the freshly
        re-bound EmailComposeError class in the module would not match the
        EmailComposeError already imported into this test module's namespace,
        breaking every `pytest.raises(EmailComposeError)` later in the file.
        """
        assert _composer_module.compose_email is not None

    # --- T8: hypothesis property — round-trip is well-formed -----------

    def test_t8_compose_email_property_well_formed(self):
        """For a small parametrised matrix of inputs, compose_email always
        produces bytes that BytesParser can parse without defects, with the
        right From/To/Subject and a valid Content-Type."""
        cases = [
            {"subject": "ascii"},
            {"subject": "Café ☕"},
            {"textBody": ["x"]},
            {"textBody": ["Привет"]},
            {"htmlBody": ["<b>html</b>"]},
            {"textBody": ["t"], "htmlBody": ["<p>h</p>"]},
            {"to": [{"name": "Doe, Jane", "email": "j@ex.com"}]},
            {
                "to": [
                    {"name": "Müller", "email": "m@ex.com"},
                    {"name": "", "email": "x@ex.com"},
                ]
            },
            {"subject": "  whitespace  "},
            {"subject": "with =?utf-8?b?dGVzdA==?= literal encoded-word"},
            {"subject": "x" * 5000},
        ]
        for override in cases:
            jmap = self._minimal_jmap(**override)
            try:
                raw, parsed = self._compose_and_parse(jmap)
            except Exception as e:
                raise AssertionError(
                    f"compose_email raised on input override={override!r}: {e}"
                ) from e
            assert parsed["From"], f"missing From for {override!r}"
            assert parsed["To"], f"missing To for {override!r}"
            assert parsed.get_content_type(), f"missing CT for {override!r}"
            # No bare LF in the wire format (must be CRLF only).
            assert b"\r\n" in raw
            for line in raw.split(b"\r\n"):
                assert b"\n" not in line, f"bare LF in {override!r}"


class TestComposerRFCAudit:  # pylint: disable=too-many-public-methods
    """Probing tests from a deep RFC + Python-stdlib + cross-library footgun audit.

    Each test names the source of the concern (RFC §, CPython issue, CVE) so
    future maintainers can trace why we lock the behavior. A failing test here
    is either a real composer bug or a legitimate RFC compliance gap.
    """

    @staticmethod
    def _minimal(**overrides):
        base = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
            "textBody": ["body"],
        }
        base.update(overrides)
        return base

    @staticmethod
    def _compose_and_parse(jmap, **kw):
        raw = compose_email(jmap, **kw)
        return raw, BytesParser(policy=policy.default).parsebytes(raw)

    # --- A. Header name validation (RFC 5322 §3.6.8 ftext) -----------------

    def test_prepend_headers_rejects_invalid_field_name_with_space(self):
        """Field names are RFC 5322 ftext (no SP, no colon). A space must be rejected."""
        with pytest.raises(EmailComposeError):
            compose_email(
                self._minimal(),
                prepend_headers=[("X With Space", "v")],
            )

    def test_prepend_headers_rejects_invalid_field_name_with_colon(self):
        """Colon in a field name would be parsed as the name/value separator."""
        with pytest.raises(EmailComposeError):
            compose_email(
                self._minimal(),
                prepend_headers=[("X:Bad", "v")],
            )

    def test_prepend_headers_rejects_empty_field_name(self):
        """Empty field names are not RFC 5322 ftext."""
        with pytest.raises(EmailComposeError):
            compose_email(
                self._minimal(),
                prepend_headers=[("", "v")],
            )

    def test_custom_headers_rejects_invalid_field_name(self):
        """Same RFC 5322 ftext rule applies to jmap_data['headers'] keys."""
        with pytest.raises(EmailComposeError):
            compose_email(self._minimal(headers={"Bad Name": "v"}))

    # --- B. CR/LF in the email address portion (CVE-2021-23400 nodemailer) -

    def test_crlf_in_email_address_does_not_inject_header(self):
        """A \\r\\n smuggled into the email field must not survive into the wire bytes.

        format_address only .strip()s whitespace from the email field. The
        guarantee comes from _sanitize_header_value wrapping the formatted
        result. Lock that contract here.
        """
        raw, parsed = self._compose_and_parse(
            self._minimal(to=[{"name": "x", "email": "a@b.com\r\nBcc: evil@evil.tld"}])
        )
        assert b"evil@evil.tld" not in raw or b"Bcc:" not in raw
        # Bcc must not appear as a separate header
        assert parsed["Bcc"] is None

    def test_crlf_in_from_email_does_not_inject_header(self):
        """Same CRLF-in-email guard applied via the From path."""
        raw, _ = self._compose_and_parse(
            self._minimal(**{"from": {"name": "n", "email": "a@b\r\nX-Injected: 1"}})
        )
        # injected line must be folded into something parsing can't split on
        msg = email.message_from_bytes(raw)
        assert msg["X-Injected"] is None

    # --- C. Display-name with control chars (RFC 5322 atext) ---------------

    def test_display_name_with_tab_does_not_explode(self):
        """Tab in display name is FWS — composer must produce parseable output."""
        _, parsed = self._compose_and_parse(
            self._minimal(to=[{"name": "John\tDoe", "email": "j@e.com"}])
        )
        # The header just has to parse; we don't pin exact form.
        assert parsed["To"] is not None

    def test_display_name_with_control_char_does_not_inject(self):
        """A C0 control char in display name must not be embedded raw."""
        raw, _ = self._compose_and_parse(
            self._minimal(to=[{"name": "x\x01y", "email": "a@b.com"}])
        )
        # Crucially: no bare \x01 in the raw wire output (legible ASCII only).
        assert b"\x01" not in raw

    # --- D. Display-name with @ (display-name spoofing, defensive) ---------

    def test_display_name_with_at_sign_is_quoted(self):
        """A display name containing @ must be quoted-string per RFC 5322."""
        formatted = format_address("ceo@victim.com", "attacker@evil.tld")
        # Must be quoted to prevent confusion in lenient parsers
        assert formatted.startswith('"')
        assert "<attacker@evil.tld>" in formatted

    def test_display_name_with_parens_is_quoted(self):
        """RFC 5322 comments use parens; CVE-2024-24784 (Go) — must be quoted."""
        formatted = format_address("foo (bar)", "x@y.com")
        assert formatted.startswith('"foo (bar)"')

    # --- E. CTE for non-ASCII text body (RFC 2045 §6, RFC 5321 7-bit) ------

    def test_non_ascii_text_body_does_not_use_8bit_cte(self):
        """A non-ASCII body must be QP or base64 under SMTP policy (no 8bit)."""
        _, parsed = self._compose_and_parse(self._minimal(textBody=["café ☕ привет"]))
        cte = parsed.get("Content-Transfer-Encoding", "").lower()
        assert cte in {"quoted-printable", "base64"}, (
            f"non-ASCII body got CTE={cte!r}; SMTP path is 7-bit-clean"
        )

    def test_ascii_text_body_does_not_use_8bit_cte(self):
        """ASCII bodies typically end up 7bit; never 8bit."""
        _, parsed = self._compose_and_parse(self._minimal(textBody=["plain ascii"]))
        cte = parsed.get("Content-Transfer-Encoding", "").lower()
        assert cte != "8bit"

    # --- F. Long unbreakable line in body (RFC 5322 §2.1.1: 998-octet hard limit)

    def test_long_unbreakable_line_in_body_respects_998_octet_limit(self):
        """A single very long token (no spaces) in body must be wrapped under 998
        octets per line (RFC 5322 §2.1.1) — required by SMTP."""
        long_token = "x" * 5000
        raw = compose_email(self._minimal(textBody=[long_token]))
        for line in raw.split(b"\r\n"):
            assert len(line) <= 998, f"line of {len(line)} octets exceeds 998 limit"

    # --- G. Long attachment filename (RFC 2231 continuation) ---------------

    def test_long_attachment_filename_uses_rfc2231_continuation(self):
        """RFC 2231 §3 — filenames over the line limit must use *N continuations."""
        long_name = "résumé_" + "x" * 200 + ".pdf"
        attachment = {
            "content": base64.b64encode(b"data").decode(),
            "type": "application/pdf",
            "name": long_name,
            "disposition": "attachment",
        }
        raw = compose_email(self._minimal(attachments=[attachment]))
        # The output must contain RFC 2231 continuation markers (filename*0*=)
        assert b"filename*0*=" in raw or b"filename*=" in raw
        # And must round-trip: parse back the filename
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        att = next(p for p in parsed.iter_attachments())
        assert att.get_filename() == long_name

    # --- H. Empty / whitespace headers (CPython GH-136052) -----------------

    def test_empty_subject_does_not_emit_empty_encoded_word(self):
        """An empty Subject must not produce =?utf-8?Q??= (illegal per RFC 2047)."""
        raw = compose_email(self._minimal(subject=""))
        # No empty encoded-word
        assert b"=?utf-8?Q??=" not in raw.lower().replace(b"=?utf-8?b?", b"=?utf-8?q?")
        assert b"=?utf-8?b??=" not in raw

    def test_whitespace_only_subject_does_not_emit_empty_encoded_word(self):
        """Same as the empty-subject case but with whitespace-only input."""
        raw = compose_email(self._minimal(subject="   "))
        assert b"=?utf-8?q??=" not in raw.lower()
        assert b"=?utf-8?b??=" not in raw.lower()

    # --- I. Long address list (CPython GH-100884) --------------------------

    def test_long_address_list_keeps_separator_commas_bare(self):
        """Folding a long address list must keep commas as bare ASCII, not inside
        an encoded-word — would break parsers (CPython GH-100884 regression)."""
        addrs = [
            {"name": f"Müller {i}", "email": f"u{i}@example.com"} for i in range(30)
        ]
        raw = compose_email(self._minimal(to=addrs))
        # Re-parse and confirm 30 addresses come back
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        to_addrs = parsed["To"].addresses
        assert len(to_addrs) == 30, f"got {len(to_addrs)} after round-trip"

    # --- J. Multipart boundary uniqueness (RFC 2046 §5.1.1) ----------------

    def test_nested_multipart_boundaries_are_distinct(self):
        """All boundaries in a nested tree must differ — RFC 2046 §5.1.1."""
        attachment = {
            "content": base64.b64encode(b"PDF").decode(),
            "type": "application/pdf",
            "name": "a.pdf",
            "disposition": "attachment",
        }
        inline = {
            "content": base64.b64encode(b"PNG").decode(),
            "type": "image/png",
            "name": "i.png",
            "disposition": "inline",
            "cid": "abc",
        }
        raw = compose_email(
            self._minimal(
                textBody=["t"],
                htmlBody=["<p>h</p>"],
                attachments=[inline, attachment],
            )
        )
        # Extract all distinct boundary= values from the wire bytes.
        boundaries = re.findall(rb'boundary="([^"]+)"', raw)
        assert len(boundaries) >= 3, (
            f"expected nested mixed/related/alt; got {boundaries!r}"
        )
        assert len(set(boundaries)) == len(boundaries), (
            f"duplicate boundary across levels: {boundaries!r}"
        )

    def test_boundary_does_not_appear_in_body_content(self):
        """Belt-and-suspenders: chosen boundary string must not collide with body
        content (RFC 2046 §5.1.1 collision concern)."""
        body = "harmless body"
        attachment = {
            "content": base64.b64encode(b"data").decode(),
            "type": "application/octet-stream",
            "name": "f.bin",
            "disposition": "attachment",
        }
        raw = compose_email(self._minimal(textBody=[body], attachments=[attachment]))
        boundaries = re.findall(rb'boundary="([^"]+)"', raw)
        for b in boundaries:
            # The boundary string itself only appears as part of "--boundary"
            # delimiter lines, never embedded inside body content. Check the
            # substring does NOT appear in body data we control.
            assert body.encode() not in b
            # And the body itself must not contain the boundary token.
            assert b not in body.encode()

    # --- K. MIME-Version always present (RFC 2045 §4) ----------------------

    def test_mime_version_header_present(self):
        """MIMEPart does not auto-add MIME-Version (only EmailMessage does);
        set_basic_headers must add it explicitly."""
        _, parsed = self._compose_and_parse(self._minimal())
        assert parsed["MIME-Version"] == "1.0"

    # --- L. Multipart parts must use 7bit/8bit/binary CTE only -------------

    def test_multipart_parts_have_no_invalid_cte(self):
        """RFC 2045 §6.4 — multipart Content-Transfer-Encoding only 7bit/8bit/binary."""
        attachment = {
            "content": base64.b64encode(b"data").decode(),
            "type": "application/pdf",
            "name": "a.pdf",
            "disposition": "attachment",
        }
        raw = compose_email(
            self._minimal(
                textBody=["t"], htmlBody=["<p>h</p>"], attachments=[attachment]
            )
        )
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        for part in parsed.walk():
            if part.get_content_maintype() == "multipart":
                cte = (part.get("Content-Transfer-Encoding") or "").lower()
                assert cte in {"", "7bit", "8bit", "binary"}, (
                    f"multipart got CTE={cte!r}"
                )

    # --- M. Wire format must be CRLF only (RFC 5321 §2.3.7) ----------------

    def test_no_bare_lf_or_cr_anywhere_in_wire_bytes(self):
        """No bare LF (without preceding CR) and no bare CR (without following LF)."""
        attachment = {
            "content": base64.b64encode(b"\x00\x01binary\xff" * 100).decode(),
            "type": "application/octet-stream",
            "name": "bin",
            "disposition": "attachment",
        }
        raw = compose_email(
            self._minimal(
                subject="café ☕",
                textBody=["body with non-ascii café"],
                htmlBody=["<p>привет</p>"],
                attachments=[attachment],
            )
        )
        # Walk the bytes, every \n must be preceded by \r, every \r followed by \n.
        for i, ch in enumerate(raw):
            if ch == 0x0A:
                assert i > 0 and raw[i - 1] == 0x0D, f"bare LF at offset {i}"
            if ch == 0x0D:
                assert i + 1 < len(raw) and raw[i + 1] == 0x0A, f"bare CR at offset {i}"

    # --- N. SMTP smuggling: dot-CR-LF sequences in body (SMTP smuggling 2023) ---

    def test_smtp_smuggling_contract_dot_stuffing_is_smtplib_responsibility(self):
        """Composer contract: we produce RFC 5322-compliant bytes; we do NOT
        pre-stuff dots. RFC 5321 §4.5.2 dot-stuffing is the SMTP client's job
        (smtplib.SMTP.sendmail handles it). Document that contract here so
        anyone considering an alternative send path knows to dot-stuff.

        SMTP smuggling defenses (Postfix smtpd_forbid_bare_newline, etc.) live
        on the receiving side; the sender side mitigation is to (a) emit only
        canonical CRLF (verified by test_no_bare_lf_or_cr_anywhere) and (b)
        rely on the SMTP client for dot-stuffing.
        """
        evil = "before\r\n.\r\nafter"
        raw = compose_email(self._minimal(textBody=[evil]))
        # The bytes are RFC 5322-legal: a body line starting with `.` is fine
        # at this layer. Confirm CRLF-only.
        for i, ch in enumerate(raw):
            if ch == 0x0A:
                assert i > 0 and raw[i - 1] == 0x0D, f"bare LF at offset {i}"
        # And confirm the '.\r\n' literally appears (we don't pre-stuff).
        assert b"\r\n.\r\n" in raw

    # --- O. Bcc handling (RFC 5322 §3.6.3 + Office 365 incident) -----------

    def test_bcc_dropped_by_default(self):
        """RFC 5322 §3.6.3: Bcc must not be transmitted to recipients. The
        composer drops it by default. Only archive-reconstruction callers
        (PST import) opt in via keep_bcc=True."""
        raw, parsed = self._compose_and_parse(
            self._minimal(bcc=[{"name": "BCC R", "email": "bcc@example.com"}])
        )
        assert parsed["Bcc"] is None
        assert b"bcc@example.com" not in raw

    def test_bcc_emitted_when_keep_bcc_true(self):
        """Archive-reconstruction opt-in: PST import passes keep_bcc=True so
        the original Bcc list is preserved in the stored .eml."""
        raw = compose_email(
            self._minimal(bcc=[{"name": "BCC R", "email": "bcc@example.com"}]),
            keep_bcc=True,
        )
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        assert parsed["Bcc"] is not None
        assert "bcc@example.com" in parsed["Bcc"]

    # --- P. Disposition-Notification-To via custom_headers -----------------

    def test_disposition_notification_to_via_custom_headers(self):
        """RFC 8098 — composer currently allows arbitrary Disposition-Notification-To
        through custom_headers. This is a known design choice (not a bug): we
        trust the JMAP layer to validate. Lock current behavior; flip if policy
        changes."""
        _, parsed = self._compose_and_parse(
            self._minimal(headers={"Disposition-Notification-To": "x@y.com"})
        )
        assert parsed["Disposition-Notification-To"] == "x@y.com"

    # --- Q. Long References folds correctly (RFC 5322 §3.6.4) --------------

    def test_long_references_folds_only_at_whitespace(self):
        """References with 30+ msg-ids must fold at whitespace, never mid-id."""
        ids = " ".join(f"<id-{i:04}@example.com>" for i in range(40))
        raw = compose_email(
            self._minimal(references=ids), in_reply_to="<latest@example.com>"
        )
        # No line >= 998 octets
        for line in raw.split(b"\r\n"):
            assert len(line) <= 998
        # All 40 ids must round-trip
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        refs = parsed["References"]
        for i in range(40):
            assert f"<id-{i:04}@example.com>" in refs

    # --- R. Long unbroken token in Subject ---------------------------------

    def test_subject_with_long_unbreakable_token(self):
        """A 5000-char single-token Subject (no spaces) must produce wire-legal
        output (line lengths <= 998)."""
        token = "A" * 5000
        raw = compose_email(self._minimal(subject=token))
        for line in raw.split(b"\r\n"):
            assert len(line) <= 998

    # --- S. Message-ID containing whitespace is invalid -------------------

    def test_message_id_with_whitespace_is_rejected(self):
        """A Message-ID like 'foo bar' is not a valid addr-spec. Stdlib's
        _MessageIDHeader silently truncates it on serialize ('<foo bar>' ⇒
        '<foo' on the wire, with everything after the space *discarded*),
        which is silent data loss. The composer must reject upfront."""
        with pytest.raises(EmailComposeError, match="Message-ID"):
            compose_email(self._minimal(messageId="foo bar"))

    def test_in_reply_to_with_whitespace_is_rejected(self):
        """Same contract as Message-ID: invalid In-Reply-To must not silently
        truncate."""
        with pytest.raises(EmailComposeError, match="In-Reply-To"):
            compose_email(self._minimal(), in_reply_to="foo bar")

    def test_message_id_without_at_sign_is_rejected(self):
        """msg-id is <local@domain>; missing @ means parsers will mis-handle."""
        with pytest.raises(EmailComposeError, match="Message-ID"):
            compose_email(self._minimal(messageId="just-an-id"))

    # --- T. Reply builder validates inbound Message-ID ---------------------

    def test_create_reply_message_drops_malformed_inbound_message_id(self):
        """Real-world inbound mail occasionally has unparseable Message-IDs
        (whitespace inside <>, missing '@'). The reply builder must NOT
        propagate them into In-Reply-To / References — stdlib's
        ReferencesHeader silently truncates such values on parse, which is
        silent thread corruption."""
        orig = {
            "subject": "Hi",
            "from": {"name": "X", "email": "x@example.com"},
            "messageId": "broken with space",
            "references": "<a@x.com> <b@x.com>",
        }
        reply = create_reply_message(orig, "reply text")
        # Malformed id is dropped — threading lost rather than corrupted.
        assert "In-Reply-To" not in reply["headers"]
        assert "References" not in reply["headers"]

    def test_create_reply_message_keeps_valid_inbound_message_id(self):
        """Sanity counter-test: a well-formed inbound msg-id is preserved."""
        orig = {
            "subject": "Hi",
            "from": {"name": "X", "email": "x@example.com"},
            "messageId": "<abc@example.com>",
            "references": "<a@x.com>",
        }
        reply = create_reply_message(orig, "r")
        assert reply["headers"]["In-Reply-To"] == "<abc@example.com>"
        assert "<a@x.com>" in reply["headers"]["References"]
        assert "<abc@example.com>" in reply["headers"]["References"]

    # --- U. Refold preserves quotes around tricky display names -----------

    def test_long_display_name_with_comma_keeps_quotes_after_refold(self):
        """CPython gh-87720: refold can drop quotes around a phrase that
        needs them. Lock the current good behavior on a representative
        input."""
        addrs = [
            {
                "name": "Doe, Jane (Ph.D.) International",
                "email": "jane@example.com",
            }
        ]
        raw = compose_email(self._minimal(to=addrs))
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        assert len(parsed["To"].addresses) == 1
        assert (
            parsed["To"].addresses[0].display_name == "Doe, Jane (Ph.D.) International"
        )

    # --- V. Literal RFC-2047 string in Subject (CPython gh-143712 family) -

    def test_literal_encoded_word_in_subject_is_decoded_by_rfc2047(self):
        """RFC 2047: any '=?charset?...?=' in unstructured text decodes to
        its represented value at the receiver. Stdlib does this on serialize:
        a user-typed literal '=?utf-8?B?dGVzdA==?=' is treated as an
        encoded-word and decodes to 'test' on the wire. This is
        RFC-conforming, not a bug, but lock the behavior so we notice if it
        changes — and so callers know typing literal '=?…?=' won't survive."""
        raw = compose_email(self._minimal(subject="café =?utf-8?B?dGVzdA==?= more"))
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        # The receiver sees the decoded form, with 'test' substituted.
        assert "test" in str(parsed["Subject"])
        assert "=?utf-8?B?dGVzdA==?=" not in str(parsed["Subject"])

    # --- W. Long filename does not trigger CPython gh-138223 in our path --

    def test_very_long_attachment_filename_compose_completes_quickly(self):
        """gh-138223 reports an infinite-loop in _fold_mime_parameters for
        long parameter NAMES (≥ ~130 chars). Parameter VALUES (filename=)
        are not affected. Lock that this remains true: a 5000-char filename
        composes within a small time budget."""
        long_name = "x" * 5000 + ".pdf"
        attachment = {
            "content": base64.b64encode(b"data").decode(),
            "type": "application/pdf",
            "name": long_name,
            "disposition": "attachment",
        }
        t0 = time.monotonic()
        raw = compose_email(self._minimal(attachments=[attachment]))
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"compose took {elapsed:.2f}s for 5000-char filename"
        assert raw


class TestComposerMIMEStructure:  # pylint: disable=too-many-public-methods
    """Pin the EXACT MIME tree shape our composer produces.

    These tests are the safety net for any internal refactor (moving to
    stdlib's add_alternative/add_related/add_attachment, etc.). Each
    assertion describes a structural choice the composer makes today;
    breaking one means the wire output changed.
    """

    @staticmethod
    def _b64(data):
        return base64.b64encode(data).decode("ascii")

    @staticmethod
    def _att(name="f.pdf", mime="application/pdf", data=b"PDF", **kw):
        return {
            "content": TestComposerMIMEStructure._b64(data),
            "type": mime,
            "name": name,
            **kw,
        }

    @staticmethod
    def _minimal(**overrides):
        base = {
            "from": [{"name": "S", "email": "s@example.com"}],
            "to": [{"name": "R", "email": "r@example.com"}],
            "subject": "t",
        }
        base.update(overrides)
        return base

    @staticmethod
    def _parse(jmap, **kw):
        raw = compose_email(jmap, **kw)
        return raw, BytesParser(policy=policy.default).parsebytes(raw)

    @staticmethod
    def _structure(parsed):
        """Return ('content_type', [child_structure...]) for a parsed message."""
        ct = parsed.get_content_type()
        if parsed.is_multipart():
            return (
                ct,
                [TestComposerMIMEStructure._structure(p) for p in parsed.iter_parts()],
            )
        return (ct, [])

    # --- A. Body subtree shape ---------------------------------------------

    def test_no_body_yields_empty_text_plain(self):
        """No textBody and no htmlBody: top-level is single text/plain (empty).
        Stdlib's set_content always appends a trailing newline; rstrip to
        compare semantic content only."""
        _, parsed = self._parse(self._minimal())
        assert self._structure(parsed) == ("text/plain", [])
        assert parsed.get_content().rstrip() == ""

    def test_text_only_yields_text_plain(self):
        """Single text body — no multipart wrapping."""
        _, parsed = self._parse(self._minimal(textBody=["hello"]))
        assert self._structure(parsed) == ("text/plain", [])
        assert parsed.get_content().rstrip() == "hello"

    def test_html_only_yields_text_html(self):
        """Single html body — no multipart wrapping."""
        _, parsed = self._parse(self._minimal(htmlBody=["<p>hi</p>"]))
        assert self._structure(parsed) == ("text/html", [])
        assert "<p>hi</p>" in parsed.get_content()

    def test_text_and_html_yields_multipart_alternative(self):
        """RFC 2046 §5.1.4: alternatives ordered least-preferred first.
        Our composer puts text/plain first, text/html second."""
        _, parsed = self._parse(self._minimal(textBody=["t"], htmlBody=["<p>h</p>"]))
        assert self._structure(parsed) == (
            "multipart/alternative",
            [("text/plain", []), ("text/html", [])],
        )

    # --- B. Body data shapes -----------------------------------------------

    def test_text_body_accepts_dict_with_content_key(self):
        """textBody=[{'content': '...'}] works."""
        _, parsed = self._parse(self._minimal(textBody=[{"content": "from-dict"}]))
        assert "from-dict" in parsed.get_content()

    def test_text_body_accepts_raw_string(self):
        """textBody=['...'] also works."""
        _, parsed = self._parse(self._minimal(textBody=["raw-string"]))
        assert "raw-string" in parsed.get_content()

    def test_only_first_text_body_entry_is_used(self):
        """Multiple textBody entries: only the first is used; rest dropped."""
        _, parsed = self._parse(self._minimal(textBody=["first", "second", "third"]))
        body = parsed.get_content()
        assert "first" in body
        assert "second" not in body
        assert "third" not in body

    def test_only_first_html_body_entry_is_used(self):
        """Same drop-the-rest behavior for htmlBody."""
        _, parsed = self._parse(self._minimal(htmlBody=["<p>a</p>", "<p>b</p>"]))
        body = parsed.get_content()
        assert "<p>a</p>" in body
        assert "<p>b</p>" not in body

    def test_html_body_rsquo_replaced_with_apostrophe(self):
        """The composer rewrites &rsquo; → ' in HTML content. This is a quirk
        of the application's HTML pipeline; it lives here for historical
        reasons. Lock the behavior."""
        _, parsed = self._parse(self._minimal(htmlBody=["it&rsquo;s here"]))
        assert "it's here" in parsed.get_content()

    # --- C. Attachment classification --------------------------------------

    def test_attachment_with_inline_disposition_and_cid_goes_inline(self):
        """disposition='inline' + cid → multipart/related path."""
        _, parsed = self._parse(
            self._minimal(
                htmlBody=["<p>x</p>"],
                attachments=[self._att(disposition="inline", cid="abc")],
            )
        )
        assert self._structure(parsed)[0] == "multipart/related"

    def test_attachment_inline_disposition_without_cid_goes_regular(self):
        """disposition='inline' but missing cid → falls back to attachment
        path (multipart/mixed). Without a cid the part can't be referenced
        from html, so treating it as a normal attachment is correct."""
        _, parsed = self._parse(
            self._minimal(
                htmlBody=["<p>x</p>"],
                attachments=[self._att(disposition="inline")],
            )
        )
        assert self._structure(parsed)[0] == "multipart/mixed"

    def test_attachment_with_cid_but_attachment_disposition_goes_regular(self):
        """A cid alone is not enough — disposition must be 'inline' too."""
        _, parsed = self._parse(
            self._minimal(
                htmlBody=["<p>x</p>"],
                attachments=[self._att(disposition="attachment", cid="abc")],
            )
        )
        assert self._structure(parsed)[0] == "multipart/mixed"

    def test_attachment_default_disposition_is_attachment(self):
        """No disposition key → defaults to 'attachment' → regular path."""
        att = {"content": self._b64(b"x"), "type": "application/pdf", "name": "f"}
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        assert self._structure(parsed)[0] == "multipart/mixed"

    # --- D. Attachment content/parsing -------------------------------------

    def test_attachment_with_invalid_base64_is_skipped(self):
        """Invalid base64 → part build returns None → silently dropped."""
        att = self._att()
        att["content"] = "&&&not-base64&&&"
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        assert self._structure(parsed) == ("text/plain", [])

    def test_attachment_with_empty_content_is_skipped(self):
        """Empty content string → skipped."""
        att = self._att()
        att["content"] = ""
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        assert self._structure(parsed) == ("text/plain", [])

    def test_attachment_with_raw_bytes_content_works(self):
        """create_attachment_part accepts bytes as well as base64 strings."""
        att = {
            "content": b"raw bytes here",
            "type": "application/pdf",
            "name": "f.pdf",
        }
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        attachments = list(parsed.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_content() == b"raw bytes here"

    def test_all_attachments_failing_to_build_drops_the_mixed_wrapper(self):
        """Defensive: don't emit a single-child multipart/mixed wrapper when
        every attachment failed to build."""
        bad1 = self._att()
        bad1["content"] = "%not-base64%"
        bad2 = self._att()
        bad2["content"] = ""
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[bad1, bad2]))
        assert self._structure(parsed) == ("text/plain", [])

    def test_all_inline_attachments_failing_drops_the_related_wrapper(self):
        """Same defensive rule for inline images."""
        bad = self._att(disposition="inline", cid="abc")
        bad["content"] = "%not-base64%"
        _, parsed = self._parse(self._minimal(htmlBody=["<p>h</p>"], attachments=[bad]))
        assert self._structure(parsed) == ("text/html", [])

    # --- E. Attachment metadata --------------------------------------------

    def test_attachment_default_content_type_is_application_octet_stream(self):
        """No 'type' key → application/octet-stream."""
        att = {"content": self._b64(b"x"), "name": "f"}
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        attachments = list(parsed.iter_attachments())
        assert attachments[0].get_content_type() == "application/octet-stream"

    def test_attachment_content_type_with_parameters_strips_subtype_params(self):
        """A type like 'image/jpeg; name=x.jpg' → bare subtype 'jpeg'."""
        att = self._att(mime="image/jpeg; name=evil.exe", data=b"img")
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        attachments = list(parsed.iter_attachments())
        assert attachments[0].get_content_type() == "image/jpeg"

    def test_attachment_garbage_content_type_falls_back_to_octet_stream(self):
        """Type without '/' → application/octet-stream."""
        att = self._att(mime="garbage")
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        attachments = list(parsed.iter_attachments())
        assert attachments[0].get_content_type() == "application/octet-stream"

    def test_attachment_filename_with_crlf_does_not_inject_header(self):
        """CRLF in filename must not split into a smuggled header. The CRLF
        is stripped, so 'evil\\r\\nX-Smuggled: 1.pdf' becomes the literal
        filename 'evilX-Smuggled: 1.pdf' — ugly but safe (parsers see it
        as the filename, not a separate header)."""
        att = self._att(name="evil\r\nX-Smuggled: 1.pdf")
        raw, parsed = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        # No bare CRLF survived inside the header value
        assert b"\r\nX-Smuggled" not in raw
        # And the attachment parses as one attachment, not two
        attachments = list(parsed.iter_attachments())
        assert len(attachments) == 1
        # The smuggled fragment ended up inside the filename
        assert "X-Smuggled" in attachments[0].get_filename()

    def test_attachment_cid_only_set_for_inline_disposition(self):
        """A cid on a regular attachment is dropped (not emitted as Content-ID)."""
        att = self._att(disposition="attachment", cid="should-not-appear")
        raw, _ = self._parse(self._minimal(textBody=["t"], attachments=[att]))
        assert b"should-not-appear" not in raw

    def test_inline_attachment_cid_normalized_with_angle_brackets(self):
        """Bare cid 'abc' → '<abc>' on Content-ID header."""
        att = self._att(disposition="inline", cid="abc", mime="image/png", data=b"PNG")
        _, parsed = self._parse(self._minimal(htmlBody=["<p>x</p>"], attachments=[att]))
        inline_part = next(p for p in parsed.walk() if p.get("Content-ID"))
        assert inline_part["Content-ID"] == "<abc>"

    # --- F. Composite tree shapes ------------------------------------------

    def test_text_html_inline_attachment_full_tree(self):
        """Kitchen sink: text + html + inline image + attachment.

        Expected: multipart/mixed {
                    multipart/related {
                      multipart/alternative { text/plain, text/html },
                      image/png  (inline)
                    },
                    application/pdf  (attachment)
                  }
        """
        inline = self._att(
            name="i.png",
            mime="image/png",
            data=b"PNG",
            disposition="inline",
            cid="i1",
        )
        attach = self._att(name="a.pdf", mime="application/pdf", data=b"PDF")
        _, parsed = self._parse(
            self._minimal(
                textBody=["t"],
                htmlBody=["<p>h</p>"],
                attachments=[inline, attach],
            )
        )
        assert self._structure(parsed) == (
            "multipart/mixed",
            [
                (
                    "multipart/related",
                    [
                        (
                            "multipart/alternative",
                            [("text/plain", []), ("text/html", [])],
                        ),
                        ("image/png", []),
                    ],
                ),
                ("application/pdf", []),
            ],
        )

    def test_text_only_with_attachment_shape(self):
        """text + attachment → multipart/mixed { text/plain, attachment }."""
        _, parsed = self._parse(
            self._minimal(textBody=["t"], attachments=[self._att()])
        )
        assert self._structure(parsed) == (
            "multipart/mixed",
            [("text/plain", []), ("application/pdf", [])],
        )

    def test_html_only_with_inline_image_shape(self):
        """html + inline → multipart/related { text/html, inline }."""
        inline = self._att(mime="image/png", data=b"PNG", disposition="inline", cid="x")
        _, parsed = self._parse(
            self._minimal(htmlBody=["<p>h</p>"], attachments=[inline])
        )
        assert self._structure(parsed) == (
            "multipart/related",
            [("text/html", []), ("image/png", [])],
        )

    def test_inline_only_no_html_still_wraps_in_related(self):
        """An 'inline' attachment with text-only body still produces related.
        Questionable shape but locked by current behavior."""
        inline = self._att(mime="image/png", data=b"PNG", disposition="inline", cid="x")
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=[inline]))
        assert self._structure(parsed) == (
            "multipart/related",
            [("text/plain", []), ("image/png", [])],
        )

    def test_no_body_with_attachment_still_has_empty_text_plain(self):
        """No textBody/htmlBody but with attachment: composer synthesizes
        an empty text/plain body part as the first child of mixed."""
        _, parsed = self._parse(self._minimal(attachments=[self._att()]))
        assert self._structure(parsed) == (
            "multipart/mixed",
            [("text/plain", []), ("application/pdf", [])],
        )

    # --- G. Header placement -----------------------------------------------

    def test_envelope_headers_on_outermost_part_only(self):
        """From/To/Subject/Date/MIME-Version live on the outermost part,
        not on body subparts."""
        _, parsed = self._parse(self._minimal(textBody=["t"], htmlBody=["<p>h</p>"]))
        assert parsed["From"] is not None
        assert parsed["To"] is not None
        assert parsed["Subject"] is not None
        assert parsed["Date"] is not None
        assert parsed["MIME-Version"] == "1.0"
        for sub in parsed.iter_parts():
            assert sub["From"] is None
            assert sub["To"] is None
            assert sub["Subject"] is None
            assert sub["Date"] is None

    def test_content_type_and_cte_are_per_part(self):
        """Each leaf part has its own Content-Type and Content-Transfer-Encoding.
        Multipart wrappers have Content-Type but no CTE (or 7bit/8bit/binary)."""
        _, parsed = self._parse(self._minimal(textBody=["t"], htmlBody=["<p>h</p>"]))
        assert parsed.get_content_type() == "multipart/alternative"
        for sub in parsed.iter_parts():
            assert sub.get("Content-Type")
            cte = (sub.get("Content-Transfer-Encoding") or "").lower()
            assert cte in {"", "7bit", "quoted-printable", "base64"}

    def test_attachment_count_matches_input(self):
        """All attachments that build successfully appear in iter_attachments."""
        atts = [self._att(name=f"f{i}.pdf", data=f"data{i}".encode()) for i in range(5)]
        _, parsed = self._parse(self._minimal(textBody=["t"], attachments=atts))
        assert len(list(parsed.iter_attachments())) == 5


if __name__ == "__main__":
    pytest.main()
