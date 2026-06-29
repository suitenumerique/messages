"""Tests for PST file import functionality."""

# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter
# pylint: disable=too-many-lines, too-many-arguments, broad-exception-caught

import email
import email.policy
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

from django.core.files.storage import storages

import pytest
from jmap_email import parse_email

from core.models import Mailbox, MailDomain, Message
from core.services.importer.pst import (
    FLAG_STATUS_FOLLOWUP,
    FOLDER_TYPE_DELETED,
    FOLDER_TYPE_OUTBOX,
    FOLDER_TYPE_SENT,
    MAX_FOLDER_DEPTH,
    MSGFLAG_READ,
    MSGFLAG_UNSENT,
    PR_ADDRTYPE,
    PR_ATTACH_CONTENT_ID,
    PR_ATTACH_FILENAME,
    PR_ATTACH_LONG_FILENAME,
    PR_ATTACH_METHOD,
    PR_ATTACH_MIME_TAG,
    PR_CONTAINER_CLASS,
    PR_CREATOR_SMTP_ADDRESS,
    PR_DISPLAY_BCC,
    PR_DISPLAY_CC,
    PR_DISPLAY_NAME,
    PR_DISPLAY_TO,
    PR_EMAIL_ADDRESS,
    PR_FLAG_STATUS,
    PR_INTERNET_MESSAGE_ID,
    PR_LAST_MODIFIER_SMTP_ADDRESS,
    PR_MESSAGE_FLAGS,
    PR_RECIPIENT_TYPE,
    PR_SENDER_ADDRTYPE,
    PR_SENDER_EMAIL_ADDRESS,
    PR_SENDER_SMTP_ADDRESS,
    PR_SENT_REPRESENTING_ADDRTYPE,
    PR_SENT_REPRESENTING_EMAIL_ADDRESS,
    PR_SENT_REPRESENTING_NAME,
    PR_SENT_REPRESENTING_SMTP_ADDRESS,
    PR_SMTP_ADDRESS,
    PSTFileUnreadableError,
    _decode_html_bytes,
    _extract_display_recipients_from_mapi,
    _extract_recipients_from_mapi,
    _extract_sender_from_mapi,
    _parse_display_recipients,
    assert_pst_readable,
    count_pst_messages,
    get_mapi_property,
    reconstruct_eml,
    sanitize_folder_name,
    walk_pst_messages,
)
from core.services.importer.pst_tasks import process_pst_file_task

pytestmark = pytest.mark.django_db


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def mailbox(domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


def _make_mapi_entry(entry_type, data=None, data_as_string=None, data_as_integer=None):
    """Helper to create a mock MAPI property entry."""
    entry = Mock()
    entry.entry_type = entry_type
    entry.data = data
    if data_as_string is not None:
        entry.data_as_string = data_as_string
    else:
        entry.data_as_string = Mock(side_effect=Exception("no string"))
    if data_as_integer is not None:
        entry.data_as_integer = data_as_integer
    else:
        entry.data_as_integer = Mock(side_effect=Exception("no integer"))
    return entry


def _make_record_set(entries):
    """Helper to create a mock record set."""
    rs = Mock()
    rs.get_number_of_entries.return_value = len(entries)
    rs.get_entry = lambda idx: entries[idx]
    return rs


def _make_item_with_properties(properties):
    """Helper to create a mock item with MAPI properties.

    properties: list of (entry_type, kwargs_for_make_mapi_entry)
    """
    entries = [_make_mapi_entry(et, **kwargs) for et, kwargs in properties]
    rs = _make_record_set(entries)
    item = Mock()
    item.number_of_record_sets = 1
    item.get_record_set = lambda idx: rs
    return item


def _make_recipient(display_name, email_addr, recip_type=1, addr_type="SMTP"):
    """Helper to create a mock MAPI recipient."""
    entries = [
        _make_mapi_entry(PR_DISPLAY_NAME, data_as_string=display_name),
        _make_mapi_entry(PR_EMAIL_ADDRESS, data_as_string=email_addr),
        _make_mapi_entry(PR_RECIPIENT_TYPE, data_as_integer=recip_type),
        _make_mapi_entry(PR_ADDRTYPE, data_as_string=addr_type),
    ]
    rs = _make_record_set(entries)
    recip = Mock()
    recip.number_of_record_sets = 1
    recip.get_record_set = lambda idx: rs
    return recip


def _make_message(
    subject="Test Subject",
    sender_name="sender@example.com",
    transport_headers=None,
    plain_text_body="Test body",
    html_body=None,
    delivery_time=None,
    client_submit_time=None,
    num_attachments=0,
    attachments=None,
    message_flags=0,
    flag_status=None,
    recipients=None,
    sender_mapi_entries=None,
):
    """Helper to create a mock pypff message."""
    msg = Mock()
    msg.subject = subject
    msg.sender_name = sender_name
    msg.transport_headers = transport_headers
    msg.plain_text_body = plain_text_body
    msg.html_body = html_body
    msg.delivery_time = delivery_time
    msg.client_submit_time = client_submit_time
    msg.number_of_attachments = num_attachments

    if attachments:
        msg.get_attachment = lambda i: attachments[i]
    else:
        msg.get_attachment = Mock(side_effect=Exception("no attachments"))

    # Recipients
    if recipients:
        msg.number_of_recipients = len(recipients)
        msg.get_recipient = lambda i: recipients[i]
    else:
        msg.number_of_recipients = Mock(side_effect=AttributeError("no recipients"))
        msg.get_recipient = Mock(side_effect=Exception("no recipients"))

    # Add MAPI properties for message flags and optional sender properties
    entries = [_make_mapi_entry(PR_MESSAGE_FLAGS, data_as_integer=message_flags)]
    if flag_status is not None:
        entries.append(_make_mapi_entry(PR_FLAG_STATUS, data_as_integer=flag_status))
    if sender_mapi_entries:
        entries.extend(sender_mapi_entries)
    rs = _make_record_set(entries)
    msg.number_of_record_sets = 1
    msg.get_record_set = lambda idx: rs

    return msg


def _make_attachment(
    data=b"data",
    long_filename="file.dat",
    short_filename=None,
    mime_type=None,
    content_id=None,
    attach_method=1,
):
    """Helper to create a mock pypff attachment."""
    att = Mock()
    att.get_size.return_value = len(data)
    att.read_buffer.return_value = data

    # MAPI properties for attachment
    entries = []
    if long_filename:
        entries.append(
            _make_mapi_entry(PR_ATTACH_LONG_FILENAME, data_as_string=long_filename)
        )
    if short_filename:
        entries.append(
            _make_mapi_entry(PR_ATTACH_FILENAME, data_as_string=short_filename)
        )
    if mime_type:
        entries.append(_make_mapi_entry(PR_ATTACH_MIME_TAG, data_as_string=mime_type))
    if content_id:
        entries.append(
            _make_mapi_entry(PR_ATTACH_CONTENT_ID, data_as_string=content_id)
        )
    entries.append(_make_mapi_entry(PR_ATTACH_METHOD, data_as_integer=attach_method))

    if entries:
        rs = _make_record_set(entries)
        att.number_of_record_sets = 1
        att.get_record_set = lambda idx: rs
    else:
        att.number_of_record_sets = 0
        att.get_record_set = Mock(side_effect=Exception("no record sets"))

    return att


def _make_folder(
    name="TestFolder",
    messages=None,
    subfolders=None,
    container_class=None,
    folder_id=None,
):
    """Helper to create a mock pypff folder."""
    folder = Mock()
    folder.name = name
    folder.get_identifier = Mock(return_value=folder_id)
    messages = messages or []
    subfolders = subfolders or []
    folder.number_of_sub_messages = len(messages)
    folder.number_of_sub_folders = len(subfolders)
    folder.get_sub_message = lambda i: messages[i]
    folder.get_sub_folder = lambda i: subfolders[i]

    # Set up MAPI properties
    entries = []
    if container_class is not None:
        entries.append(
            _make_mapi_entry(PR_CONTAINER_CLASS, data_as_string=container_class)
        )

    if entries:
        rs = _make_record_set(entries)
        folder.number_of_record_sets = 1
        folder.get_record_set = lambda idx: rs
    else:
        folder.number_of_record_sets = 0
        folder.get_record_set = Mock(side_effect=Exception("no record sets"))

    return folder


# --- reconstruct_eml tests ---


# pylint: disable=too-many-public-methods
class TestReconstructEml:
    """Tests for EML reconstruction from pypff messages."""

    def test_reconstruct_with_transport_headers(self):
        """Test EML reconstruction when transport_headers are present."""
        transport = (
            "From: sender@example.com\r\n"
            "To: recipient@example.com\r\n"
            "Subject: Test Message\r\n"
            "Date: Mon, 26 May 2025 10:00:00 +0000\r\n"
            "Message-ID: <test123@example.com>\r\n"
            "In-Reply-To: <parent@example.com>\r\n"
            "References: <parent@example.com>\r\n"
        )
        msg = _make_message(
            subject="Test Message",
            transport_headers=transport,
            plain_text_body="Hello world",
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        assert parsed["From"] == "sender@example.com"
        assert parsed["To"] == "recipient@example.com"
        assert parsed["Subject"] == "Test Message"
        assert parsed["Message-ID"] == "<test123@example.com>"
        assert parsed["In-Reply-To"] == "<parent@example.com>"
        assert parsed["References"] == "<parent@example.com>"
        # Check body content (may be base64 encoded in MIME)
        body_parts = list(parsed.walk())
        text_parts = [p for p in body_parts if p.get_content_type() == "text/plain"]
        assert len(text_parts) >= 1
        assert "Hello world" in text_parts[0].get_payload(decode=True).decode()

    def test_reconstruct_preserves_rfc5322_date(self):
        """Test that RFC 5322 date from transport headers is preserved correctly."""
        transport = (
            "From: sender@example.com\r\nDate: Mon, 26 May 2025 10:00:00 +0000\r\n"
        )
        msg = _make_message(transport_headers=transport)
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        # The date should contain 2025, not the current year
        assert "2025" in parsed["Date"]

    def test_reconstruct_without_transport_headers(self):
        """Test EML reconstruction from MAPI properties (no transport_headers)."""
        msg = _make_message(
            subject="Draft Subject",
            sender_name="draft-author@example.com",
            transport_headers=None,
            plain_text_body="Draft body",
            delivery_time=datetime(2025, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        assert parsed["Subject"] == "Draft Subject"
        assert parsed["From"] == "draft-author@example.com"
        body_parts = [p for p in parsed.walk() if p.get_content_type() == "text/plain"]
        assert "Draft body" in body_parts[0].get_payload(decode=True).decode()

    def test_reconstruct_uses_pr_internet_message_id_when_header_missing(self):
        """PR_INTERNET_MESSAGE_ID is the fallback when no header carries it."""
        msg = _make_message(
            transport_headers=None,
            plain_text_body="body",
            sender_mapi_entries=[
                _make_mapi_entry(
                    PR_INTERNET_MESSAGE_ID, data_as_string="<native@example.com>"
                ),
            ],
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="dest@example.com")
        parsed = email.message_from_bytes(eml_bytes)
        assert parsed["Message-ID"] == "<native@example.com>"

    def test_reconstruct_synthesizes_message_id_for_drafts(self):
        """Drafts without transport_headers nor native ID get a deterministic ID.

        Two reconstructions of the same message must produce the same value
        so the inbound dedup check skips re-imports.
        """
        kwargs = {
            "subject": "Draft",
            "sender_name": "author@example.com",
            "transport_headers": None,
            "plain_text_body": "Hello",
            "delivery_time": datetime(2025, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
        }
        eml_a = reconstruct_eml(
            _make_message(**kwargs), recipient_email="dest@example.com"
        )
        eml_b = reconstruct_eml(
            _make_message(**kwargs), recipient_email="dest@example.com"
        )
        id_a = email.message_from_bytes(eml_a)["Message-ID"]
        id_b = email.message_from_bytes(eml_b)["Message-ID"]

        assert id_a == id_b
        assert id_a.startswith("<pst-synth-")
        assert id_a.endswith("@example.com>")

    def test_reconstruct_synthesized_ids_differ_for_distinct_messages(self):
        """Two different drafts must not collide on the synthesized Message-ID."""
        msg_a = _make_message(
            subject="A", transport_headers=None, plain_text_body="content A"
        )
        msg_b = _make_message(
            subject="B", transport_headers=None, plain_text_body="content B"
        )
        id_a = email.message_from_bytes(
            reconstruct_eml(msg_a, recipient_email="dest@example.com")
        )["Message-ID"]
        id_b = email.message_from_bytes(
            reconstruct_eml(msg_b, recipient_email="dest@example.com")
        )["Message-ID"]
        assert id_a != id_b

    def test_reconstruct_with_mapi_sender_email(self):
        """Test sender extraction from MAPI properties instead of sender_name."""
        sender_entries = [
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS, data_as_string="real@example.com"
            ),
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="SMTP"),
        ]
        msg = _make_message(
            sender_name="John Doe",
            transport_headers=None,
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        assert "real@example.com" in parsed["From"]
        assert "John Doe" in parsed["From"]

    def test_reconstruct_with_exchange_ex_sender(self):
        """Test sender extraction with Exchange EX address type."""
        sender_entries = [
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="EX"),
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS,
                data_as_string="/O=ORG/OU=Group/cn=Recipients/cn=jsmith",
            ),
            _make_mapi_entry(
                PR_SENDER_SMTP_ADDRESS, data_as_string="jsmith@example.com"
            ),
        ]
        msg = _make_message(
            sender_name="John Smith",
            transport_headers=None,
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        # Should use SMTP address, not X.500 DN
        assert "jsmith@example.com" in parsed["From"]

    def test_reconstruct_with_mapi_recipients(self):
        """Test recipient extraction from MAPI recipient table."""
        recipients = [
            _make_recipient("Alice", "alice@example.com", recip_type=1),  # To
            _make_recipient("Bob", "bob@example.com", recip_type=2),  # Cc
            _make_recipient("Charlie", "charlie@example.com", recip_type=3),  # Bcc
        ]
        msg = _make_message(
            transport_headers=None,
            recipients=recipients,
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        assert "alice@example.com" in parsed["To"]
        assert "bob@example.com" in parsed["Cc"]
        # BCC is typically stripped from headers in MIME, but we set it
        assert "charlie@example.com" in parsed["Bcc"]

    def test_reconstruct_with_ex_recipients(self):
        """Test recipient extraction with Exchange EX address type."""
        # Create a recipient with EX address and SMTP fallback
        entries = [
            _make_mapi_entry(PR_DISPLAY_NAME, data_as_string="Jane Doe"),
            _make_mapi_entry(
                PR_EMAIL_ADDRESS,
                data_as_string="/O=ORG/OU=Group/cn=Recipients/cn=jdoe",
            ),
            _make_mapi_entry(PR_RECIPIENT_TYPE, data_as_integer=1),
            _make_mapi_entry(PR_ADDRTYPE, data_as_string="EX"),
            _make_mapi_entry(PR_SMTP_ADDRESS, data_as_string="jane@example.com"),
        ]
        rs = _make_record_set(entries)
        recip = Mock()
        recip.number_of_record_sets = 1
        recip.get_record_set = lambda idx: rs

        msg = _make_message(transport_headers=None, recipients=[recip])
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        assert "jane@example.com" in parsed["To"]

    def test_reconstruct_with_html_body(self):
        """Test EML reconstruction with HTML body."""
        msg = _make_message(
            subject="HTML Message",
            transport_headers="From: sender@example.com\r\nSubject: HTML Message\r\n",
            plain_text_body="Plain text",
            html_body="<html><body><b>Bold text</b></body></html>",
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        text_parts = [p for p in parsed.walk() if p.get_content_type() == "text/plain"]
        html_parts = [p for p in parsed.walk() if p.get_content_type() == "text/html"]
        assert len(text_parts) >= 1
        assert "Plain text" in text_parts[0].get_payload(decode=True).decode()
        assert len(html_parts) >= 1
        assert "Bold text" in html_parts[0].get_payload(decode=True).decode()

    def test_reconstruct_with_attachments(self):
        """Test EML reconstruction with attachments."""
        att = _make_attachment(
            data=b"Hello",
            long_filename="test.txt",
            mime_type="text/plain",
        )

        msg = _make_message(
            subject="With Attachment",
            transport_headers=(
                "From: sender@example.com\r\nSubject: With Attachment\r\n"
            ),
            plain_text_body="See attached",
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        # Check attachment is present
        att_parts = [
            p for p in parsed.walk() if p.get_content_disposition() == "attachment"
        ]
        assert len(att_parts) == 1
        assert att_parts[0].get_filename() == "test.txt"
        assert att_parts[0].get_content_type() == "text/plain"

        # Check body
        text_parts = [p for p in parsed.walk() if p.get_content_type() == "text/plain"]
        assert any(
            "See attached" in p.get_payload(decode=True).decode() for p in text_parts
        )

    def test_reconstruct_with_inline_image(self):
        """Test EML reconstruction with inline image (CID)."""
        att = _make_attachment(
            data=b"\x89PNG\r\n\x1a\n",
            long_filename="image.png",
            mime_type="image/png",
            content_id="img001@example.com",
            attach_method=1,  # ATTACH_BY_VALUE
        )

        msg = _make_message(
            subject="Inline Image",
            transport_headers=("From: sender@example.com\r\nSubject: Inline Image\r\n"),
            html_body='<html><body><img src="cid:img001@example.com"></body></html>',
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        # Check inline attachment
        inline_parts = [
            p for p in parsed.walk() if p.get_content_disposition() == "inline"
        ]
        assert len(inline_parts) == 1
        assert inline_parts[0].get_content_type() == "image/png"
        assert "img001@example.com" in (inline_parts[0]["Content-ID"] or "")

    def test_reconstruct_attachment_mime_type(self):
        """Test that attachment MIME type is read from MAPI properties."""
        att = _make_attachment(
            data=b"%PDF-1.4",
            long_filename="document.pdf",
            mime_type="application/pdf",
        )
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        att_parts = [
            p for p in parsed.walk() if p.get_content_disposition() == "attachment"
        ]
        assert len(att_parts) == 1
        assert att_parts[0].get_content_type() == "application/pdf"

    def test_reconstruct_attachment_no_mime_type_defaults(self):
        """Test that missing MIME type defaults to application/octet-stream."""
        att = _make_attachment(
            data=b"\x00\x01\x02",
            long_filename="binary.dat",
            mime_type=None,
        )
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        att_parts = [
            p for p in parsed.walk() if p.get_content_disposition() == "attachment"
        ]
        assert len(att_parts) == 1
        assert att_parts[0].get_content_type() == "application/octet-stream"

    def test_reconstruct_delivery_status_attachment_does_not_crash(self):
        """A DSN delivery-status part imports without crashing the composer.

        Regression: bounce/read-receipt reports in a PST carry the
        delivery-status body as a flat-bytes MAPI attachment. Fed to the strict
        composer as message/delivery-status, email.generator walked the base64
        string character by character and raised "'str' object has no attribute
        'policy'", dropping the whole message. The composer now relabels the
        part to text/plain (content preserved, readable), so reconstructing the
        report no longer crashes.
        """
        dsn = (
            b"Reporting-MTA: dns; mx.example.com\r\n"
            b"Final-Recipient: rfc822; nobody@example.com\r\n"
            b"Action: failed\r\nStatus: 5.1.1\r\n"
        )
        att = _make_attachment(
            data=dsn,
            long_filename="details.txt",
            mime_type="message/delivery-status",
        )
        msg = _make_message(
            transport_headers="From: daemon@example.com\r\n",
            plain_text_body="Delivery failed",
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes, policy=email.policy.default)

        part = next(p for p in parsed.walk() if p.get_filename() == "details.txt")
        # Relabelled to a safe leaf type; content preserved verbatim.
        assert part.get_content_type() == "text/plain"
        assert part.get_payload(decode=True) == dsn
        # The original message/delivery-status type is never emitted.
        assert b"message/delivery-status" not in eml_bytes

    def test_reconstruct_skips_empty_attachment(self):
        """Blank/whitespace-only attachments (e.g. empty DSN parts) are dropped.

        libpff surfaces empty diagnostic report parts as attachments; importing
        them produces 0-byte attachments that render as broken in the UI while
        carrying no information.
        """
        empty = _make_attachment(
            data=b"\r\n",
            long_filename="empty.txt",
            mime_type="text/rfc822-headers",
        )
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            plain_text_body="body",
            num_attachments=1,
            attachments=[empty],
        )
        eml_bytes = reconstruct_eml(msg)
        parsed = email.message_from_bytes(eml_bytes)

        att_parts = [
            p for p in parsed.walk() if p.get_content_disposition() == "attachment"
        ]
        assert att_parts == []

    def test_reconstruct_rfc822_headers_attachment_is_displayable(self):
        """text/rfc822-headers is relabelled so it doesn't show as 0 bytes.

        Regression: the original-headers part of a DSN composes fine, but
        on re-parse the display parser previously dropped the body of
        text/rfc822-headers, so the UI showed a 0-byte attachment. The
        importer normalizes the label to text/plain, which round-trips
        with its content intact.
        """

        headers = (
            b"Return-Path: <sender@example.com>\r\n"
            b"From: Sender <sender@example.com>\r\n"
            b"To: Recipient <rcpt@example.com>\r\n"
            b"Subject: original\r\n"
        )
        att = _make_attachment(
            data=headers,
            long_filename="Message Headers.txt",
            mime_type="text/rfc822-headers",
        )
        msg = _make_message(
            transport_headers="From: daemon@example.com\r\n",
            plain_text_body="Delivered",
            num_attachments=1,
            attachments=[att],
        )
        eml_bytes = reconstruct_eml(msg)

        # Re-parse the stored .eml the way the message view does.
        parsed = parse_email(eml_bytes)
        headers_att = next(
            a for a in parsed["attachments"] if a["name"] == "Message Headers.txt"
        )
        assert headers_att["type"] == "text/plain"
        assert headers_att["size"] == len(headers)

    def test_reconstruct_empty_message(self):
        """Test EML reconstruction with no body."""
        msg = _make_message(
            subject="Empty",
            transport_headers="From: sender@example.com\r\nSubject: Empty\r\n",
            plain_text_body=None,
            html_body=None,
        )
        eml_bytes = reconstruct_eml(msg)
        # Should not raise and should produce valid bytes
        assert isinstance(eml_bytes, bytes)
        assert len(eml_bytes) > 0

    def test_reconstruct_no_sender_uses_recipient_domain_fallback(self):
        """Without any extractable sender, fall back to recipient's domain.

        Regression: previously an empty 'from' was set, which made
        compose_email raise and the importer silently dropped messages.
        """
        msg = _make_message(
            subject="Orphan",
            sender_name=None,
            transport_headers=None,
            plain_text_body="No sender here",
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="target@example.org")
        parsed = email.message_from_bytes(eml_bytes)

        assert "unknown-sender@example.org" in parsed["From"]
        assert "Unknown Sender" in parsed["From"]

    def test_reconstruct_shared_mailbox_delegate_uses_creator_smtp(
        self, shared_mailbox_delegate_send_message
    ):
        """Reproduces the real-world PST dump: an Contact shared mailbox
        Sent Item where every SENDER/SENT_REPRESENTING address is an X.500
        DN. The only resolvable SMTP is PR_CREATOR_SMTP_ADDRESS — that's
        what must end up in the EML From, keeping 'Contact' as the name."""
        eml_bytes = reconstruct_eml(
            shared_mailbox_delegate_send_message,
            recipient_email="target@example.org",
        )
        parsed = email.message_from_bytes(eml_bytes)

        assert "john@acme.org" in parsed["From"]
        assert "Contact" in parsed["From"]
        # The X.500 garbage must NOT bleed through
        assert "EXCHANGELABS" not in parsed["From"]
        # And we must not have hit the unknown-sender fallback
        assert "unknown-sender" not in parsed["From"]
        # PR_DISPLAY_TO must surface as the To: header since the recipient
        # table is empty in this Exchange Online export.
        assert "recipient@hotmail.fr" in parsed["To"]

    def test_reconstruct_transport_x500_from_resolves_via_sent_representing(self):
        """Exchange shared-mailbox case: transport_headers has the X.500 DN
        but PR_SENT_REPRESENTING_SMTP_ADDRESS holds the real SMTP.

        Regression: previously the X.500 DN flowed straight into the EML
        From header, producing an invalid contact downstream.
        """
        transport = (
            "From: Contact </O=EXCHANGELABS/OU=GRP/CN=RECIPIENTS/CN=ABC123>\r\n"
            "To: user@example.org\r\n"
            "Subject: Internal\r\n"
        )
        sender_entries = [
            _make_mapi_entry(
                PR_SENT_REPRESENTING_SMTP_ADDRESS,
                data_as_string="Contact@example.com",
            ),
            _make_mapi_entry(
                PR_SENT_REPRESENTING_NAME, data_as_string="Contact (MAPI)"
            ),
        ]
        msg = _make_message(
            transport_headers=transport,
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="user@example.org")
        parsed = email.message_from_bytes(eml_bytes)

        assert "Contact@example.com" in parsed["From"]
        # Name from transport header wins over MAPI name
        assert "Contact" in parsed["From"]
        assert "EXCHANGELABS" not in parsed["From"]


# --- HTML encoding tests ---


class TestHtmlDecoding:
    """Tests for HTML body encoding detection."""

    def test_decode_utf8_html(self):
        """Test decoding UTF-8 HTML bytes."""
        html = "<html><body>Héllo wörld</body></html>".encode("utf-8")
        result = _decode_html_bytes(html)
        assert "Héllo wörld" in result

    def test_decode_cp1252_html(self):
        """Test decoding Windows-1252 HTML bytes (common for Outlook)."""
        # \x93 and \x94 are left/right double quotes in cp1252
        html = b"<html><body>\x93Hello\x94</body></html>"
        result = _decode_html_bytes(html)
        assert "Hello" in result
        # Should not have replacement characters
        assert "\ufffd" not in result

    def test_decode_html_with_meta_charset(self):
        """Test encoding detection from HTML meta charset tag."""
        html = (
            b'<html><head><meta charset="iso-8859-1"></head>'
            b"<body>\xe9l\xe8ve</body></html>"
        )
        result = _decode_html_bytes(html)
        assert "élève" in result

    def test_decode_html_string_passthrough(self):
        """Test that string HTML body passes through without decoding."""
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            plain_text_body=None,
            html_body="<html>Already a string</html>",
        )
        eml_bytes = reconstruct_eml(msg)
        assert b"Already a string" in eml_bytes


# --- Sender extraction tests ---


class TestSenderExtraction:
    """Tests for MAPI sender property extraction."""

    def test_smtp_sender(self):
        """Test extracting SMTP sender from MAPI properties."""
        entries = [
            _make_mapi_entry(PR_SENDER_EMAIL_ADDRESS, data_as_string="user@test.com"),
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="SMTP"),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.sender_name = "Test User"
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_sender_from_mapi(msg)
        assert result is not None
        assert result["email"] == "user@test.com"
        assert result["name"] == "Test User"

    def test_ex_sender_with_smtp_fallback(self):
        """Test extracting Exchange sender with SMTP fallback."""
        entries = [
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="EX"),
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS,
                data_as_string="/O=ORG/OU=Group/cn=jdoe",
            ),
            _make_mapi_entry(PR_SENDER_SMTP_ADDRESS, data_as_string="jdoe@company.com"),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.sender_name = "John Doe"
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_sender_from_mapi(msg)
        assert result is not None
        assert result["email"] == "jdoe@company.com"

    def test_sender_name_as_email_fallback(self):
        """Test sender_name parsed as email when MAPI props missing."""
        msg = Mock()
        msg.sender_name = "fallback@test.com"
        msg.number_of_record_sets = 0

        result = _extract_sender_from_mapi(msg)
        assert result is not None
        assert result["email"] == "fallback@test.com"

    def test_sender_display_name_only_returns_none(self):
        """Test that display-name-only sender (no @) returns None."""
        msg = Mock()
        msg.sender_name = "John Doe"
        msg.number_of_record_sets = 0

        result = _extract_sender_from_mapi(msg)
        assert result is None

    def test_sent_representing_smtp_used_when_sender_is_x500(self):
        """Shared-mailbox sender: PR_SENDER_* is X.500, SMTP only on
        PR_SENT_REPRESENTING_*. The representing SMTP must win."""
        entries = [
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="EX"),
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS,
                data_as_string="/O=EXCHANGELABS/OU=GRP/CN=RECIPIENTS/CN=ABC123",
            ),
            _make_mapi_entry(
                PR_SENT_REPRESENTING_SMTP_ADDRESS,
                data_as_string="Contact@example.com",
            ),
            _make_mapi_entry(PR_SENT_REPRESENTING_NAME, data_as_string="Contact"),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.sender_name = "Some User"
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_sender_from_mapi(msg)
        assert result is not None
        assert result["email"] == "Contact@example.com"
        assert result["name"] == "Contact"

    def test_sent_representing_email_used_when_smtp_missing(self):
        """Fallback to PR_SENT_REPRESENTING_EMAIL_ADDRESS if it looks SMTP."""
        entries = [
            _make_mapi_entry(PR_SENDER_EMAIL_ADDRESS, data_as_string="/O=ORG/CN=user"),
            _make_mapi_entry(
                PR_SENT_REPRESENTING_EMAIL_ADDRESS,
                data_as_string="rep@example.com",
            ),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.sender_name = ""
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_sender_from_mapi(msg)
        assert result is not None
        assert result["email"] == "rep@example.com"

    def test_preferred_name_overrides_mapi_name(self):
        """A transport-header display name takes precedence over MAPI names
        (used when transport_headers' From had a valid name but X.500 email)."""
        entries = [
            _make_mapi_entry(
                PR_SENT_REPRESENTING_SMTP_ADDRESS,
                data_as_string="Contact@example.com",
            ),
            _make_mapi_entry(PR_SENT_REPRESENTING_NAME, data_as_string="Wrong Name"),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.sender_name = ""
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_sender_from_mapi(msg, preferred_name="Contact RH")
        assert result is not None
        assert result["email"] == "Contact@example.com"
        assert result["name"] == "Contact RH"


# --- Recipient extraction tests ---


class TestRecipientExtraction:
    """Tests for MAPI recipient table extraction."""

    def test_extract_to_cc_bcc(self):
        """Test extracting To, Cc, Bcc from recipient table."""
        recipients = [
            _make_recipient("To User", "to@test.com", recip_type=1),
            _make_recipient("Cc User", "cc@test.com", recip_type=2),
            _make_recipient("Bcc User", "bcc@test.com", recip_type=3),
        ]
        msg = Mock()
        msg.number_of_recipients = 3
        msg.get_recipient = lambda i: recipients[i]

        result = _extract_recipients_from_mapi(msg)
        assert len(result["to"]) == 1
        assert result["to"][0]["email"] == "to@test.com"
        assert len(result["cc"]) == 1
        assert result["cc"][0]["email"] == "cc@test.com"
        assert len(result["bcc"]) == 1
        assert result["bcc"][0]["email"] == "bcc@test.com"

    def test_no_recipient_support(self):
        """Test graceful handling when pypff doesn't expose recipients."""
        msg = Mock(spec=[])  # empty spec — no attributes at all
        # number_of_recipients will raise AttributeError since spec is empty

        result = _extract_recipients_from_mapi(msg)
        assert result == {"to": [], "cc": [], "bcc": []}

    def test_ex_recipient_resolved(self):
        """Test Exchange EX recipient resolved via PR_SMTP_ADDRESS."""
        entries = [
            _make_mapi_entry(PR_DISPLAY_NAME, data_as_string="Jane"),
            _make_mapi_entry(
                PR_EMAIL_ADDRESS,
                data_as_string="/O=ORG/cn=Recipients/cn=jane",
            ),
            _make_mapi_entry(PR_RECIPIENT_TYPE, data_as_integer=1),
            _make_mapi_entry(PR_ADDRTYPE, data_as_string="EX"),
            _make_mapi_entry(PR_SMTP_ADDRESS, data_as_string="jane@corp.com"),
        ]
        rs = _make_record_set(entries)
        recip = Mock()
        recip.number_of_record_sets = 1
        recip.get_record_set = lambda idx: rs

        msg = Mock()
        msg.number_of_recipients = 1
        msg.get_recipient = lambda i: recip

        result = _extract_recipients_from_mapi(msg)
        assert len(result["to"]) == 1
        assert result["to"][0]["email"] == "jane@corp.com"


# --- Display recipient (PR_DISPLAY_TO/CC/BCC) tests ---


class TestDisplayRecipients:
    """Tests for the PR_DISPLAY_TO/CC/BCC fallback parser."""

    def test_parse_single_email(self):
        """A bare email address yields a single recipient."""
        result = _parse_display_recipients("user@example.com")
        assert result == [{"name": "", "email": "user@example.com"}]

    def test_parse_semicolon_separated(self):
        """Outlook uses ';' between recipients (not ',')."""
        result = _parse_display_recipients("alice@example.com; bob@example.com")
        emails = [r["email"] for r in result]
        assert emails == ["alice@example.com", "bob@example.com"]

    def test_parse_name_with_email(self):
        """RFC-style `Name <email>` is honoured."""
        result = _parse_display_recipients("Alice <alice@example.com>")
        assert result == [{"name": "Alice", "email": "alice@example.com"}]

    def test_parse_drops_name_only_entries(self):
        """Name-only tokens (no '@') are intentionally dropped — creating a
        Contact without an email would fail downstream validation."""
        result = _parse_display_recipients("John Doe; jane@example.com")
        assert len(result) == 1
        assert result[0]["email"] == "jane@example.com"

    def test_parse_empty_or_none(self):
        """Empty / None input returns an empty list, not an exception."""
        assert not _parse_display_recipients(None)
        assert not _parse_display_recipients("")
        assert not _parse_display_recipients("   ")

    def test_parse_recovers_bare_email_when_parser_returns_empty_addr(self):
        """If parse_address returns no usable address but the original
        token contains '@', the token itself is salvaged as the email — guards
        against parser quirks where the address ends up in the name slot."""
        with patch(
            "core.services.importer.pst.parse_address",
            return_value=("", ""),
        ):
            result = _parse_display_recipients("salvage@example.com")
        assert result == [{"name": "", "email": "salvage@example.com"}]

    def test_extract_display_recipients_all_buckets(self):
        """PR_DISPLAY_TO/CC/BCC each populate their bucket."""
        entries = [
            _make_mapi_entry(PR_DISPLAY_TO, data_as_string="to@example.com"),
            _make_mapi_entry(PR_DISPLAY_CC, data_as_string="cc@example.com"),
            _make_mapi_entry(PR_DISPLAY_BCC, data_as_string="bcc@example.com"),
        ]
        rs = _make_record_set(entries)
        msg = Mock()
        msg.number_of_record_sets = 1
        msg.get_record_set = lambda idx: rs

        result = _extract_display_recipients_from_mapi(msg)
        assert result["to"][0]["email"] == "to@example.com"
        assert result["cc"][0]["email"] == "cc@example.com"
        assert result["bcc"][0]["email"] == "bcc@example.com"

    def test_extract_display_recipients_when_absent(self):
        """Missing display strings yield empty buckets (no exception)."""
        msg = Mock()
        msg.number_of_record_sets = 0
        result = _extract_display_recipients_from_mapi(msg)
        assert result == {"to": [], "cc": [], "bcc": []}

    def test_reconstruct_falls_back_to_display_to_when_table_empty(self):
        """End-to-end: empty MAPI recipient table + PR_DISPLAY_TO → To set."""
        sender_entries = [
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS, data_as_string="sender@example.com"
            ),
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="SMTP"),
            _make_mapi_entry(PR_DISPLAY_TO, data_as_string="target@example.org"),
        ]
        msg = _make_message(
            sender_name="Sender",
            transport_headers=None,
            plain_text_body="hi",
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="me@example.org")
        parsed = email.message_from_bytes(eml_bytes)
        assert "target@example.org" in parsed["To"]

    def test_reconstruct_recipient_table_wins_over_display(self):
        """When the MAPI recipient table is populated, PR_DISPLAY_TO is
        ignored (the table is the authoritative source)."""
        sender_entries = [
            _make_mapi_entry(
                PR_SENDER_EMAIL_ADDRESS, data_as_string="sender@example.com"
            ),
            _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="SMTP"),
            _make_mapi_entry(PR_DISPLAY_TO, data_as_string="display@example.org"),
        ]
        recipient = _make_recipient("Real", "real@example.org", recip_type=1)
        msg = _make_message(
            sender_name="Sender",
            transport_headers=None,
            plain_text_body="hi",
            recipients=[recipient],
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="me@example.org")
        parsed = email.message_from_bytes(eml_bytes)
        assert "real@example.org" in parsed["To"]
        assert "display@example.org" not in parsed["To"]

    def test_reconstruct_transport_headers_fall_back_to_display_when_empty(self):
        """transport_headers exist but carry no To/Cc/Bcc — fall back to the
        PR_DISPLAY_TO/CC/BCC strings (same chain as the no-headers branch)."""
        transport = "From: sender@example.com\r\nSubject: Headers w/o recipients\r\n"
        sender_entries = [
            _make_mapi_entry(PR_DISPLAY_TO, data_as_string="target@example.org"),
            _make_mapi_entry(PR_DISPLAY_CC, data_as_string="copy@example.org"),
        ]
        msg = _make_message(
            transport_headers=transport,
            plain_text_body="hi",
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="me@example.org")
        parsed = email.message_from_bytes(eml_bytes)
        assert "target@example.org" in parsed["To"]
        assert "copy@example.org" in parsed["Cc"]

    def test_reconstruct_transport_recipients_win_over_display_fallback(self):
        """When transport_headers carry recipients, PR_DISPLAY_* is ignored —
        the header is authoritative, the display strings are only a fallback."""
        transport = (
            "From: sender@example.com\r\n"
            "To: header@example.org\r\n"
            "Subject: Headers with recipients\r\n"
        )
        sender_entries = [
            _make_mapi_entry(PR_DISPLAY_TO, data_as_string="display@example.org"),
        ]
        msg = _make_message(
            transport_headers=transport,
            plain_text_body="hi",
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="me@example.org")
        parsed = email.message_from_bytes(eml_bytes)
        assert "header@example.org" in parsed["To"]
        assert "display@example.org" not in parsed["To"]

    def test_reconstruct_fills_missing_cc_bcc_when_to_present_in_headers(self):
        """Header gives To only — Cc must come from MAPI table, Bcc from
        PR_DISPLAY_BCC. Each field is filled independently."""
        transport = (
            "From: sender@example.com\r\n"
            "To: header@example.org\r\n"
            "Subject: Partial headers\r\n"
        )
        sender_entries = [
            _make_mapi_entry(PR_DISPLAY_BCC, data_as_string="bcc@example.org"),
        ]
        cc_recipient = _make_recipient("CC", "cc@example.org", recip_type=2)
        msg = _make_message(
            transport_headers=transport,
            plain_text_body="hi",
            recipients=[cc_recipient],
            sender_mapi_entries=sender_entries,
        )
        eml_bytes = reconstruct_eml(msg, recipient_email="me@example.org")
        parsed = email.message_from_bytes(eml_bytes)
        assert "header@example.org" in parsed["To"]
        assert "cc@example.org" in parsed["Cc"]
        assert "bcc@example.org" in parsed["Bcc"]


# --- MAPI property tests ---


class TestMAPIProperties:
    """Tests for MAPI property reading."""

    def test_get_mapi_property_found(self):
        """Test finding a MAPI property by tag."""
        item = _make_item_with_properties([(0x3613, {"data_as_string": "IPF.Note"})])
        entry = get_mapi_property(item, 0x3613)
        assert entry is not None
        assert entry.data_as_string == "IPF.Note"

    def test_get_mapi_property_not_found(self):
        """Test when MAPI property is not present."""
        item = _make_item_with_properties([(0x3613, {"data_as_string": "IPF.Note"})])
        entry = get_mapi_property(item, 0x9999)
        assert entry is None

    def test_get_mapi_property_empty_record_sets(self):
        """Test with an item that has no record sets."""
        item = Mock()
        item.number_of_record_sets = 0
        entry = get_mapi_property(item, 0x3613)
        assert entry is None


class TestAssertPSTReadable:
    """Tests for the early sanity probe on PST archives."""

    def test_passes_on_healthy_archive(self):
        """A PST with usable root and store passes silently."""
        pst = Mock()
        pst.get_root_folder.return_value = Mock(number_of_sub_folders=3)
        pst.get_message_store.return_value = Mock(number_of_record_sets=1)
        assert_pst_readable(pst)  # must not raise

    def test_raises_when_root_folder_attribute_is_missing(self):
        """A None root folder (corrupt header) surfaces as PSTFileUnreadableError."""
        pst = Mock()
        pst.get_root_folder.return_value = None
        pst.get_message_store.return_value = Mock(number_of_record_sets=1)
        with pytest.raises(PSTFileUnreadableError):
            assert_pst_readable(pst)

    def test_raises_when_message_store_is_unusable(self):
        """A None message store also surfaces as PSTFileUnreadableError."""
        pst = Mock()
        pst.get_root_folder.return_value = Mock(number_of_sub_folders=3)
        pst.get_message_store.return_value = None
        with pytest.raises(PSTFileUnreadableError):
            assert_pst_readable(pst)

    def test_wraps_underlying_pypff_exception(self):
        """Exceptions raised by pypff during the probe are wrapped, not leaked."""
        pst = Mock()
        pst.get_root_folder.side_effect = OSError("corrupt header")
        with pytest.raises(PSTFileUnreadableError):
            assert_pst_readable(pst)


# --- Folder identification tests ---


class TestFolderIdentification:
    """Tests for MAPI-based folder identification."""

    def test_skip_calendar_folder(self):
        """Test that Calendar (IPF.Appointment) folders are skipped."""
        folder = _make_folder(
            name="Calendar",
            messages=[_make_message()],
            container_class="IPF.Appointment",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 0

    def test_skip_contact_folder(self):
        """Test that Contact folders are skipped."""
        folder = _make_folder(
            name="Contacts",
            messages=[_make_message()],
            container_class="IPF.Contact",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 0

    def test_process_email_folder(self):
        """Test that IPF.Note folders are processed."""
        msg = _make_message(delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        folder = _make_folder(
            name="Inbox",
            messages=[msg],
            container_class="IPF.Note",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 1

    def test_process_email_subfolder_class(self):
        """Test that IPF.Note.* subclasses are processed."""
        msg = _make_message(delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        folder = _make_folder(
            name="Conversations",
            messages=[msg],
            container_class="IPF.Note.Microsoft.Conversation",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 1

    def test_process_folder_no_container_class(self):
        """Test that folders without container class are processed (safe default)."""
        msg = _make_message(delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        folder = _make_folder(name="CustomFolder", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 1

    def test_process_imap_folder(self):
        """IMAP-archived PSTs tag mail folders IPF.Imap — they must be counted."""
        msg = _make_message(delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        folder = _make_folder(
            name="Boîte de réception",
            messages=[msg],
            container_class="IPF.Imap",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        count = count_pst_messages(pst, {})
        assert count == 1

    def test_walk_yields_messages_from_imap_folder(self):
        """walk_pst_messages (the import path) must yield IPF.Imap messages."""
        msg = _make_message(
            subject="IMAP message",
            transport_headers="From: a@example.com\r\nTo: b@example.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(
            name="Boîte de réception",
            messages=[msg],
            container_class="IPF.Imap",
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)
        pst.get_name_to_id_map.side_effect = Exception("no named props")

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        assert results[0][4] is not None  # eml_bytes reconstructed

    def test_sent_folder_identification_via_entry_id(self):
        """Test identifying Sent Items via message store folder identifier."""
        special_map = {100: FOLDER_TYPE_SENT}

        msg = _make_message(
            subject="Sent message",
            transport_headers=(
                "From: me@example.com\r\nTo: other@example.com\r\n"
                "Subject: Sent message\r\n"
            ),
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(
            name="Sent Items",
            messages=[msg],
            container_class="IPF.Note",
            folder_id=100,
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, special_map))
        assert len(results) == 1
        folder_type = results[0][0]
        assert folder_type == FOLDER_TYPE_SENT

    def test_deleted_folder_identification(self):
        """Test identifying Deleted Items via message store folder identifier."""
        special_map = {200: FOLDER_TYPE_DELETED}

        msg = _make_message(
            subject="Deleted message",
            transport_headers=("From: me@example.com\r\nSubject: Deleted message\r\n"),
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(
            name="Deleted Items",
            messages=[msg],
            folder_id=200,
        )
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, special_map))
        assert len(results) == 1
        assert results[0][0] == FOLDER_TYPE_DELETED


# --- Message flags tests ---


class TestMessageFlags:
    """Tests for per-message MAPI flag detection."""

    def test_draft_flag_detection(self):
        """Test that MSGFLAG_UNSENT is correctly detected."""
        msg = _make_message(
            subject="Draft",
            transport_headers="From: me@example.com\r\nSubject: Draft\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            message_flags=MSGFLAG_UNSENT,
        )
        folder = _make_folder(name="Drafts", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        flags = results[0][2]
        assert flags & MSGFLAG_UNSENT

    def test_read_flag_detection(self):
        """Test that MSGFLAG_READ is correctly detected."""
        msg = _make_message(
            subject="Read Message",
            transport_headers="From: me@example.com\r\nSubject: Read Message\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            message_flags=MSGFLAG_READ,
        )
        folder = _make_folder(name="Inbox", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        flags = results[0][2]
        assert flags & MSGFLAG_READ

    def test_flagged_status_detection(self):
        """Test that PR_FLAG_STATUS (follow-up flag) is correctly detected."""
        msg = _make_message(
            subject="Flagged",
            transport_headers="From: me@example.com\r\nSubject: Flagged\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            flag_status=FLAG_STATUS_FOLLOWUP,
        )
        folder = _make_folder(name="Inbox", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        flag_status = results[0][3]
        assert flag_status == FLAG_STATUS_FOLLOWUP

    def test_no_flag_status(self):
        """Test that None flag_status is returned when property is missing."""
        msg = _make_message(
            subject="Normal",
            transport_headers="From: me@example.com\r\nSubject: Normal\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(name="Inbox", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        flag_status = results[0][3]
        assert flag_status is None


# --- Folder path tests ---


class TestFolderPaths:
    """Tests for hierarchical folder path building."""

    def test_top_level_folder_path(self):
        """Test that top-level folders get their name as path."""
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(name="MyFolder", messages=[msg])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        folder_path = results[0][1]
        assert folder_path == "MyFolder"

    def test_nested_folder_path(self):
        """Test that subfolders of special folders start a fresh path.

        Inbox is detected as a special folder, so its children don't inherit
        the "Inbox/" prefix. The path is "Projects/Work", not "Inbox/Projects/Work".
        """
        msg = _make_message(
            transport_headers="From: a@b.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        child = _make_folder(name="Work", messages=[msg])
        parent = _make_folder(name="Projects", subfolders=[child])
        inbox = _make_folder(name="Inbox", subfolders=[parent])
        root = _make_folder(name="Root", subfolders=[inbox])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        assert len(results) == 1
        folder_path = results[0][1]
        assert folder_path == "Projects/Work"

    def test_subfolder_of_sent_inherits_type_and_gets_own_label(self):
        """Subfolders of Sent Items inherit FOLDER_TYPE_SENT.

        They keep the special treatment (is_import_sender=True in pst_tasks)
        and also get their own subfolder name as folder_path for labeling.
        """
        msg = _make_message(
            transport_headers="From: me@example.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        child = _make_folder(name="Archives 2024", messages=[msg])
        sent = _make_folder(name="Sent Items", subfolders=[child], folder_id=100)
        root = _make_folder(name="Root", subfolders=[sent])

        special_map = {100: FOLDER_TYPE_SENT}
        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, special_map))
        assert len(results) == 1
        folder_type, folder_path = results[0][0], results[0][1]
        # Inherits the parent's special type
        assert folder_type == FOLDER_TYPE_SENT
        # Label is just the subfolder name, no "Sent Items/" prefix
        assert folder_path == "Archives 2024"


# --- is_sender marking tests ---


class TestIsSenderMarking:
    """Tests for is_sender marking in pst_tasks."""

    def test_sent_folder_is_sender(self):
        """Test that messages in Sent Items are marked as is_sender."""
        special_map = {100: FOLDER_TYPE_SENT}

        msg = _make_message(
            transport_headers="From: me@example.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(name="Sent Items", messages=[msg], folder_id=100)
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, special_map))
        assert results[0][0] == FOLDER_TYPE_SENT

    def test_outbox_folder_type(self):
        """Test that messages in Outbox get FOLDER_TYPE_OUTBOX."""
        special_map = {300: FOLDER_TYPE_OUTBOX}

        msg = _make_message(
            transport_headers="From: me@example.com\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        folder = _make_folder(name="Outbox", messages=[msg], folder_id=300)
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, special_map))
        assert results[0][0] == FOLDER_TYPE_OUTBOX


# --- Chronological ordering tests ---


class TestChronologicalOrdering:
    """Tests for chronological message ordering."""

    def test_messages_sorted_oldest_first(self):
        """Test that messages are yielded in chronological order (oldest first)."""
        msg1 = _make_message(
            subject="Oldest",
            transport_headers="From: a@example.com\r\nSubject: Oldest\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        msg2 = _make_message(
            subject="Middle",
            transport_headers="From: b@example.com\r\nSubject: Middle\r\n",
            delivery_time=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        msg3 = _make_message(
            subject="Newest",
            transport_headers="From: c@example.com\r\nSubject: Newest\r\n",
            delivery_time=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )

        # Put them in reverse order in the folder
        folder = _make_folder(name="Inbox", messages=[msg3, msg1, msg2])
        root = _make_folder(name="Root", subfolders=[folder])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        subjects = [
            r[4]  # eml_bytes is now at index 4
            .decode("utf-8", errors="replace")
            .split("Subject: ")[1]
            .split("\n")[0]
            .strip()
            for r in results
        ]
        assert subjects == ["Oldest", "Middle", "Newest"]

    def test_messages_across_folders_sorted(self):
        """Test chronological ordering across multiple folders."""
        msg_inbox = _make_message(
            subject="Inbox Message",
            transport_headers=("From: a@example.com\r\nSubject: Inbox Message\r\n"),
            delivery_time=datetime(2025, 3, 1, tzinfo=timezone.utc),
        )
        msg_sent = _make_message(
            subject="Sent Message",
            transport_headers=("From: b@example.com\r\nSubject: Sent Message\r\n"),
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        inbox = _make_folder(name="Inbox", messages=[msg_inbox])
        sent = _make_folder(name="Sent Items", messages=[msg_sent])
        root = _make_folder(name="Root", subfolders=[sent, inbox])

        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        results = list(walk_pst_messages(pst, {}))
        # Sent message (Jan) should come before Inbox message (Mar)
        assert len(results) == 2
        eml1 = results[0][4].decode("utf-8", errors="replace")
        eml2 = results[1][4].decode("utf-8", errors="replace")
        assert "Sent Message" in eml1
        assert "Inbox Message" in eml2


# --- PST task tests (E2E with real PST files) ---


def _upload_pst_to_s3(filename):
    """Upload a test PST file to the message-imports S3 bucket."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client

    with open(f"core/tests/resources/{filename}", "rb") as f:
        file_content = f.read()

    file_key = f"test-pst-{filename}"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=file_content,
        ContentType="application/vnd.ms-outlook",
    )
    return file_key, storage, s3_client


class TestProcessPstFileTask:
    """Tests for the process_pst_file_task Celery task using real PST files."""

    def test_nonexistent_mailbox(self):
        """Test task with non-existent mailbox returns failure."""
        mock_task = MagicMock()
        with patch.object(
            process_pst_file_task, "update_state", mock_task.update_state
        ):
            result = process_pst_file_task(
                file_key="test.pst",
                recipient_id="00000000-0000-0000-0000-000000000000",
            )
            assert result["status"] == "FAILURE"
            assert result["result"]["type"] == "pst"
            assert "not found" in result["error"]

    def test_process_sample_pst(self, mailbox):
        """Test processing sample.pst — 1 message in myInbox with transport headers."""
        file_key, storage, s3_client = _upload_pst_to_s3("sample.pst")

        try:
            mock_task = MagicMock()
            with patch.object(
                process_pst_file_task, "update_state", mock_task.update_state
            ):
                result = process_pst_file_task(
                    file_key=file_key,
                    recipient_id=str(mailbox.id),
                )

            assert result["status"] == "SUCCESS"
            assert result["result"]["type"] == "pst"
            assert result["result"]["total_messages"] == 1
            assert result["result"]["success_count"] == 1
            assert result["result"]["failure_count"] == 0

            # Verify message was created with correct data from the PST
            assert Message.objects.count() == 1
            message = Message.objects.first()
            assert (
                message.subject == "New message created by Aspose.Email"
                " for Java(Aspose.Email Evaluation)"
            )
            assert message.sender.email == "from@domain.com"
            # Check recipients
            recipient_emails = sorted(r.contact.email for r in message.recipients.all())
            assert "to1@domain.com" in recipient_emails
            assert "to2@domain.com" in recipient_emails
            assert "cc1@domain.com" in recipient_emails
            assert "cc2@domain.com" in recipient_emails

        finally:
            try:
                s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)
            except Exception:
                pass  # Already cleaned up

    def test_process_outlook_pst(self, mailbox):
        """Test processing Outlook.pst — 8 Inbox messages + 6 Sent Items,
        Calendar/Contacts/Tasks folders should be skipped."""
        file_key, storage, s3_client = _upload_pst_to_s3("Outlook.pst")

        try:
            mock_task = MagicMock()
            with patch.object(
                process_pst_file_task, "update_state", mock_task.update_state
            ):
                result = process_pst_file_task(
                    file_key=file_key,
                    recipient_id=str(mailbox.id),
                )

            assert result["status"] == "SUCCESS"
            assert result["result"]["type"] == "pst"
            # 8 Inbox + 6 Sent Items = 14 email messages
            # Calendar, Contacts, Tasks, Notes, Journal and root-level
            # internal folders (Freebusy Data) are skipped
            assert result["result"]["total_messages"] == 14
            assert result["result"]["success_count"] > 0
            assert result["result"]["failure_count"] == 0

            # Verify some known messages from Inbox
            subjects = list(Message.objects.values_list("subject", flat=True))
            assert "Multiple attachments" in subjects
            assert "HTML body" in subjects
            assert "message 1" in subjects

            # Verify attachments on a "Multiple attachments" message
            msg = Message.objects.filter(subject="Multiple attachments").first()
            assert msg.has_attachments is True
            assert msg.sender.email == "saqib.razzaq@xp.local"
        finally:
            try:
                s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)
            except Exception:
                pass

    def test_process_pst_unreadable_surfaces_dedicated_error(self, mailbox):
        """When the readability probe rejects the archive, the task must:
            - return FAILURE,
            - prefix the error with ``PST_UNREADABLE:`` so the frontend can
              show its dedicated message (instead of the generic fallback),
            - leave success/failure counters at 0 (we never started walking).

        We feed a real, valid PST so the upstream ``pypff.open_file_object``
        call succeeds and we exercise the new ``except PSTFileUnreadableError``
        branch specifically, not the generic ``except Exception`` path.
        """
        file_key, storage, s3_client = _upload_pst_to_s3("sample.pst")

        try:
            mock_task = MagicMock()
            with (
                patch(
                    "core.services.importer.pst_tasks.assert_pst_readable",
                    side_effect=PSTFileUnreadableError(
                        "PST archive is unreadable: missing root folder or message store."
                    ),
                ),
                patch.object(
                    process_pst_file_task, "update_state", mock_task.update_state
                ),
            ):
                result = process_pst_file_task(
                    file_key=file_key,
                    recipient_id=str(mailbox.id),
                )

            assert result["status"] == "FAILURE"
            assert result["result"]["type"] == "pst"
            assert result["error"].startswith("PST_UNREADABLE:")
            # No traversal happened, so the counters must be untouched.
            assert result["result"]["success_count"] == 0
            assert result["result"]["failure_count"] == 0
            assert Message.objects.count() == 0
        finally:
            try:
                s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)
            except Exception:
                pass

    def test_process_malformed_pst(self, mailbox):
        """Test that random bytes as PST file returns FAILURE gracefully."""
        storage = storages["message-imports"]
        s3_client = storage.connection.meta.client
        file_key = "test-pst-malformed"
        s3_client.put_object(
            Bucket=storage.bucket_name,
            Key=file_key,
            Body=b"this is not a valid PST file at all" * 100,
            ContentType="application/vnd.ms-outlook",
        )

        try:
            mock_task = MagicMock()
            with patch.object(
                process_pst_file_task, "update_state", mock_task.update_state
            ):
                result = process_pst_file_task(
                    file_key=file_key,
                    recipient_id=str(mailbox.id),
                )

            assert result["status"] == "FAILURE"
            assert result["result"]["type"] == "pst"
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)


# --- Folder name sanitization tests ---


class TestFolderNameSanitization:
    """Tests for PST folder name sanitization."""

    def test_sanitize_normal_name(self):
        """Test that normal folder names pass through unchanged."""
        assert sanitize_folder_name("Inbox") == "Inbox"
        assert sanitize_folder_name("Sent Items") == "Sent Items"

    def test_sanitize_control_characters(self):
        """Test that control characters are removed."""
        assert sanitize_folder_name("Inbox\x00\x01\x02") == "Inbox"
        assert sanitize_folder_name("\tTest\nFolder\r") == "TestFolder"

    def test_sanitize_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        assert sanitize_folder_name("  Inbox  ") == "Inbox"

    def test_sanitize_empty_name(self):
        """Test that empty names get a default."""
        assert sanitize_folder_name("") == "Unknown"
        assert sanitize_folder_name("   ") == "Unknown"
        assert sanitize_folder_name("\x00\x01") == "Unknown"

    def test_sanitize_long_name(self):
        """Test that very long names are truncated."""
        long_name = "A" * 500
        result = sanitize_folder_name(long_name)
        assert len(result) == 255

    def test_sanitize_custom_max_length(self):
        """Test custom max_length parameter."""
        result = sanitize_folder_name("A" * 100, max_length=50)
        assert len(result) == 50


# --- Recursion depth limit tests ---


class TestRecursionDepthLimit:
    """Tests for PST folder recursion depth limit."""

    def test_deep_nesting_stops_at_limit(self):
        """Test that deeply nested folders stop at MAX_FOLDER_DEPTH."""
        # Build a chain of folders deeper than MAX_FOLDER_DEPTH
        msg = _make_message(
            subject="Deep Message",
            transport_headers="From: a@example.com\r\nSubject: Deep Message\r\n",
            delivery_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        # Create a chain of folders, only the deepest has a message
        deepest = _make_folder(name=f"Level{MAX_FOLDER_DEPTH + 5}", messages=[msg])
        current = deepest
        for i in range(MAX_FOLDER_DEPTH + 4, -1, -1):
            current = _make_folder(name=f"Level{i}", subfolders=[current])

        root = _make_folder(name="Root", subfolders=[current])
        pst = Mock()
        pst.get_root_folder.return_value = root
        pst.get_message_store.return_value = Mock(number_of_record_sets=0)

        # The message should not be reachable due to depth limit
        count = count_pst_messages(pst, {})
        # The message is beyond MAX_FOLDER_DEPTH, so it should be excluded
        assert count == 0


@pytest.fixture
def shared_mailbox_delegate_send_message():
    """Replays the real-world PST dump shared by the user.

    A Sent Item from a shared mailbox ("Contact") composed by a delegate
    (john@acme.org). Every PR_SENDER_* and PR_SENT_REPRESENTING_* slot is
    an Exchange X.500 DN; only PR_CREATOR_SMTP_ADDRESS (0x5D0A) and
    PR_LAST_MODIFIER_SMTP_ADDRESS (0x5D0B) carry a resolvable SMTP — the
    delegate's. ``transport_headers`` is absent (typical for items kept
    purely in Exchange's MAPI store), and the recipient table is empty.
    """
    x500_dn = (
        "/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP "
        "(FDSW829FE)/CN=RECIPIENTS/CN=B5FE3C24D9C646DC84FFFF1AB4ACC2A7"
    )
    sender_entries = [
        _make_mapi_entry(PR_SENDER_ADDRTYPE, data_as_string="EX"),
        _make_mapi_entry(PR_SENDER_EMAIL_ADDRESS, data_as_string=x500_dn),
        _make_mapi_entry(PR_SENT_REPRESENTING_NAME, data_as_string="Contact"),
        _make_mapi_entry(PR_SENT_REPRESENTING_ADDRTYPE, data_as_string="EX"),
        _make_mapi_entry(PR_SENT_REPRESENTING_EMAIL_ADDRESS, data_as_string=x500_dn),
        _make_mapi_entry(PR_CREATOR_SMTP_ADDRESS, data_as_string="john@acme.org"),
        _make_mapi_entry(PR_LAST_MODIFIER_SMTP_ADDRESS, data_as_string="john@acme.org"),
        # Recipient table is empty in the real dump; PR_DISPLAY_TO carries
        # the addressee as a header-style string.
        _make_mapi_entry(PR_DISPLAY_TO, data_as_string="recipient@hotmail.fr"),
    ]
    return _make_message(
        subject="Devis repassage",
        sender_name="Contact",
        transport_headers=None,
        plain_text_body="Bonjour, veuillez trouver ci-joint le devis signé.",
        sender_mapi_entries=sender_entries,
    )
