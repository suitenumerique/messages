"""Tests for the core.mda.outbound module."""
# pylint: disable=unused-argument

from unittest.mock import MagicMock, call, patch

from django.test import override_settings

import dns.resolver
import pytest

from core import enums, factories, models
from core.mda import outbound

SCHEMA_CUSTOM_ATTRIBUTES = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/user",
    "type": "object",
    "title": "User custom fields",
    "additionalProperties": False,
    "properties": {
        "job_title": {"type": "string"},
        "department": {"type": "string"},
    },
    "required": [],
}


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user with custom attributes."""
    return factories.UserFactory(
        full_name="John Doe",
        custom_attributes={
            "job_title": "Software Engineer",
            "department": "Engineering",
        },
    )


@pytest.fixture(name="signature_template")
def fixture_signature_template(mailbox_sender):
    """Create a signature template in the same mailbox as the sender."""
    return factories.MessageTemplateFactory(
        name="Professional Signature",
        html_body="<p>Best regards,<br>{full_name}<br>{job_title}<br>{department}</p>",
        text_body="Best regards,\n{full_name}\n{job_title}\n{department}",
        type=enums.MessageTemplateTypeChoices.SIGNATURE,
        is_active=True,
        mailbox=mailbox_sender,
    )


@pytest.fixture(name="mailbox_sender")
def fixture_mailbox_sender():
    """Create a test mailbox sender."""
    return factories.MailboxFactory()


@pytest.fixture(name="mailbox_access")
def fixture_mailbox_access(user, mailbox_sender):
    """Create mailbox access for the user."""
    return factories.MailboxAccessFactory(
        user=user,
        mailbox=mailbox_sender,
        role=models.MailboxRoleChoices.EDITOR,
    )


@pytest.fixture(name="other_user")
def fixture_other_user():
    """Create another user for testing unauthorized access."""
    return factories.UserFactory()


@pytest.fixture(name="message")
def fixture_message(mailbox_sender, signature_template=None):
    """Create a test message."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox_sender,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    sender_contact = factories.ContactFactory(mailbox=mailbox_sender)

    return factories.MessageFactory(
        thread=thread,
        sender=sender_contact,
        is_draft=True,
        subject="Test Message",
        signature=signature_template,
    )


@pytest.mark.django_db
class TestSendOutboundMessage:
    """Unit tests for the send_outbound_message function."""

    @pytest.fixture
    def draft_message(self):
        """Create a valid (not actually draft) message with sender and recipients."""
        sender_contact = factories.ContactFactory(email="sender@sendtest.com")
        mailbox = sender_contact.mailbox
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            subject="Test Outbound",
        )
        # Create a blob with the raw MIME content
        blob = mailbox.create_blob(
            content=b"From: sender@sendtest.com\nTo: to@example.com\nSubject: Test Outbound\n\nTest body",
            content_type="message/rfc822",
        )
        message.blob = blob
        message.save()
        # Add recipients
        to_contact = factories.ContactFactory(mailbox=mailbox, email="to@example.com")
        cc_contact = factories.ContactFactory(mailbox=mailbox, email="cc@example.com")
        cc_contact2 = factories.ContactFactory(mailbox=mailbox, email="cc2@example.com")
        bcc_contact = factories.ContactFactory(
            mailbox=mailbox, email="bcc@example2.com"
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=cc_contact,
            type=models.MessageRecipientTypeChoices.CC,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=cc_contact2,
            type=models.MessageRecipientTypeChoices.CC,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=bcc_contact,
            type=models.MessageRecipientTypeChoices.BCC,
        )
        return message

    @patch("core.mda.outbound.send_smtp_mail")  # Mock SMTP client
    @override_settings(
        MTA_OUT_MODE="relay",
        MTA_OUT_RELAY_HOST="smtp.test:1025",
        # Ensure other auth settings are None for this test
        MTA_OUT_RELAY_USERNAME="smtp_user",
        MTA_OUT_RELAY_PASSWORD="smtp_pass",
        OPENSEARCH_INDEX_THREADS=False,
    )
    def test_outbound_send_relay(self, mock_smtp_send, draft_message):
        """Test sending via SMTP relay."""

        mock_smtp_send.return_value = {
            "to@example.com": {
                "delivered": True,
                "error": None,
            },
            "cc@example.com": {
                "delivered": False,
                "error": "Temp refused",
                "retry": True,
            },
            "cc2@example.com": {
                "delivered": False,
                "error": "Not good this one",
            },
            "bcc@example2.com": {
                "delivered": True,
                "error": None,
            },
        }

        outbound.send_message(draft_message)

        # Check SMTP calls
        mock_smtp_send.assert_called_once_with(
            smtp_host="smtp.test",
            smtp_port=1025,
            envelope_from=draft_message.sender.email,
            recipient_emails={
                "to@example.com",
                "cc@example.com",
                "cc2@example.com",
                "bcc@example2.com",
            },
            message_content=draft_message.blob.get_content(),
            smtp_username="smtp_user",
            smtp_password="smtp_pass",
        )

        # Check message object updated
        draft_message.refresh_from_db()
        assert not draft_message.is_draft
        assert draft_message.sent_at is not None

        assert draft_message.recipients.count() == 4
        assert (
            draft_message.recipients.filter(
                delivery_status=enums.MessageDeliveryStatusChoices.SENT
            ).count()
            == 2
        )
        assert (
            draft_message.recipients.filter(
                contact__email="cc@example.com",
                delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            ).count()
            == 1
        )
        assert (
            draft_message.recipients.filter(
                contact__email="cc2@example.com",
                delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            ).count()
            == 1
        )

    @patch("core.mda.outbound_direct.dns.resolver.resolve")
    @patch("core.mda.outbound_direct.send_smtp_mail")
    @override_settings(
        MTA_OUT_MODE="direct",
        MTA_OUT_DIRECT_PROXIES=["socks5://proxyuser:proxyuser@smtp.proxy:1080"],
        OPENSEARCH_INDEX_THREADS=False,
    )
    def test_outbound_send_direct(self, mock_smtp_send, mock_resolve, draft_message):
        """Test sending via direct connection with MX fallback logic."""

        def smtp_return_value(*args, **kwargs):
            if kwargs["recipient_emails"] == {
                "to@example.com",
                "cc@example.com",
                "cc2@example.com",
            }:
                return {
                    "to@example.com": {
                        "delivered": False,
                        "error": "Temp refused",
                        "retry": True,
                    },
                    "cc@example.com": {
                        "delivered": False,
                        "error": "Temp refused",
                        "retry": True,
                    },
                    "cc2@example.com": {
                        "delivered": False,
                        "error": "Not good this one",
                    },
                }
            if kwargs["recipient_emails"] == {"bcc@example2.com"}:
                return {
                    "bcc@example2.com": {"delivered": True},
                }
            if kwargs["recipient_emails"] == {"cc@example.com", "to@example.com"}:
                # This is the retry attempt on the second MX
                return {
                    "cc@example.com": {
                        "delivered": True,  # Success on retry
                        "error": None,
                    },
                    "to@example.com": {
                        "delivered": False,
                        "error": "Temp refused",
                        "retry": True,
                    },
                }
            return {}

        mock_smtp_send.side_effect = smtp_return_value

        def resolve_return_value(domain, record_type, **kwargs):
            lookup_data = {
                ("example.com", "MX"): [
                    MagicMock(preference=10, exchange="mx1.example.com"),
                    MagicMock(preference=15, exchange="mx1-5.example.com"),
                    MagicMock(preference=20, exchange="mx2.example.com"),
                    MagicMock(preference=30, exchange="mx3.example.com"),
                ],
                ("example2.com", "MX"): [
                    MagicMock(preference=10, exchange="mx1.example2.com"),
                    MagicMock(preference=20, exchange="mx2.example2.com"),
                ],
                ("mx1.example.com", "A"): ["1.1.0.9"],
                ("mx2.example.com", "A"): ["1.2.0.9"],
                ("mx3.example.com", "A"): None,
                ("mx1-5.example.com", "A"): None,
                ("mx1.example2.com", "A"): ["2.1.0.9"],
                ("mx2.example2.com", "A"): ["2.2.0.9"],
            }
            return lookup_data.get((domain, record_type))

        mock_resolve.side_effect = resolve_return_value

        outbound.send_message(draft_message)

        # Check message object updated
        draft_message.refresh_from_db()
        assert not draft_message.is_draft
        assert draft_message.sent_at is not None

        assert draft_message.recipients.count() == 4
        assert (
            draft_message.recipients.filter(
                delivery_status=enums.MessageDeliveryStatusChoices.SENT
            ).count()
            == 2
        )
        assert (
            draft_message.recipients.filter(
                contact__email="to@example.com",
                delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            ).count()
            == 1
        )
        assert (
            draft_message.recipients.filter(
                contact__email="cc2@example.com",
                delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            ).count()
            == 1
        )

        # Check SMTP calls
        # 1. bcc@example2.com to mx1.example2.com (success)
        # 2. (to@example.com success, cc@example.com retry, cc2@example.com failed) to mx1.example.com
        # 3. cc@example.com to mx2.example.com (retry attempt)
        assert len(mock_smtp_send.mock_calls) == 3

        sorted_calls = sorted(mock_smtp_send.mock_calls, key=lambda x: x.smtp_host)

        # Check first call - to@example.com, cc@example.com, cc2@example.com to mx1.example.com
        assert sorted_calls[0] == call(
            smtp_host="mx1.example.com",
            smtp_ip="1.1.0.9",
            smtp_port=25,
            envelope_from=draft_message.sender.email,
            recipient_emails={"to@example.com", "cc@example.com", "cc2@example.com"},
            message_content=draft_message.blob.get_content(),
            proxy_host="smtp.proxy",
            proxy_port=1080,
            proxy_username="proxyuser",
            proxy_password="proxyuser",
            sender_hostname="smtp.proxy",
        )

        # Check second call - cc@example.com, to@example.com retry to mx2.example.com
        assert sorted_calls[1] == call(
            smtp_host="mx2.example.com",
            smtp_ip="1.2.0.9",
            smtp_port=25,
            envelope_from=draft_message.sender.email,
            recipient_emails={"cc@example.com", "to@example.com"},
            message_content=draft_message.blob.get_content(),
            proxy_host="smtp.proxy",
            proxy_port=1080,
            proxy_username="proxyuser",
            proxy_password="proxyuser",
            sender_hostname="smtp.proxy",
        )

        # Check third call - bcc@example2.com to mx1.example2.com
        assert sorted_calls[2] == call(
            smtp_host="mx1.example2.com",
            smtp_ip="2.1.0.9",
            smtp_port=25,
            envelope_from=draft_message.sender.email,
            recipient_emails={"bcc@example2.com"},
            message_content=draft_message.blob.get_content(),
            proxy_host="smtp.proxy",
            proxy_port=1080,
            proxy_username="proxyuser",
            proxy_password="proxyuser",
            sender_hostname="smtp.proxy",
        )

    @patch("core.mda.outbound_direct.dns.resolver.resolve")
    @patch("core.mda.outbound_direct.send_smtp_mail")
    @override_settings(
        MTA_OUT_MODE="direct",
        OPENSEARCH_INDEX_THREADS=False,
    )
    def test_outbound_send_direct_no_mx(
        self, mock_smtp_send, mock_resolve, draft_message
    ):
        """Test sending via direct connection with no MX records."""

        def resolve_return_value(domain, record_type, **kwargs):
            # Without MX records, we should retry on the A record
            if domain == "example2.com" and record_type == "MX":
                raise dns.resolver.NoAnswer()
            return {("example.com", "MX"): [], ("example2.com", "A"): ["1.2.0.8"]}[
                (domain, record_type)
            ]

        mock_resolve.side_effect = resolve_return_value

        def smtp_return_value(*args, **kwargs):
            if kwargs["recipient_emails"] == {"bcc@example2.com"}:
                return {
                    "bcc@example2.com": {"delivered": True},
                }
            raise ValueError("Should not be called")

        mock_smtp_send.side_effect = smtp_return_value

        outbound.send_message(draft_message)

        mock_smtp_send.assert_called_once_with(
            smtp_host="example2.com",
            smtp_ip="1.2.0.8",
            smtp_port=25,
            envelope_from=draft_message.sender.email,
            recipient_emails={"bcc@example2.com"},
            message_content=draft_message.blob.get_content(),
        )

        # Check message object updated
        draft_message.refresh_from_db()
        assert not draft_message.is_draft
        assert draft_message.sent_at is not None

        assert (
            draft_message.recipients.filter(
                delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            ).count()
            == 3
        )
        assert (
            draft_message.recipients.filter(
                contact__email="bcc@example2.com",
                delivery_status=enums.MessageDeliveryStatusChoices.SENT,
            ).count()
            == 1
        )


@pytest.mark.django_db
class TestPrepareOutboundMessageSignature:
    """Test signature handling in prepare_outbound_message function."""

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_html_signature(
        self, user, signature_template, mailbox_sender, mailbox_access
    ):
        """Test preparing message with HTML signature."""
        html_body = "<p>Hello world!</p>"
        text_body = "Hello world!"
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=signature_template,
        )

        outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Hello world!" in content
        assert (
            "Best regards,<br>John Doe<br>Software Engineer<br>Engineering</p>"
            in content
        )

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_text_signature(
        self, user, signature_template, mailbox_sender, mailbox_access
    ):
        """Test preparing message with text signature."""
        html_body = None
        text_body = "Hello world!"
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=signature_template,
        )

        outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Hello world!" in content
        assert "Best regards,\nJohn Doe\nSoftware Engineer\nEngineering" in content

    def test_prepare_outbound_message_without_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test preparing message without signature."""
        html_body = "<p>Hello world!</p>"
        text_body = "Hello world!"
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=None,
        )

        result = outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )

        assert result is True
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Hello world!" in content
        assert "Best regards" not in content

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_reply_and_signature(
        self, user, signature_template, mailbox_sender, mailbox_access
    ):
        """Test preparing message with both reply content and signature."""
        # Create a parent message for reply
        parent_message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            subject="Original Subject",
        )
        message = factories.MessageFactory(
            thread=parent_message.thread,
            sender=parent_message.sender,
            subject="Re: Original Subject",
            parent=parent_message,
            signature=signature_template,
        )

        html_body = "<p>This is a reply</p>"
        text_body = "This is a reply"

        outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "This is a reply" in content
        assert "Best regards,\nJohn Doe\nSoftware Engineer\nEngineering" in content

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_inactive_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test preparing message with inactive signature."""
        inactive_signature = factories.MessageTemplateFactory(
            name="Inactive Signature",
            html_body="<p>Inactive content</p>",
            text_body="Inactive content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox_sender,
            is_active=False,
        )

        html_body = "<p>Hello world!</p>"
        text_body = "Hello world!"
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=inactive_signature,
        )

        result = outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )

        assert result is True
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Hello world!" in content
        assert "Inactive content" not in content

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_unauthorized_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test preparing message with unauthorized signature."""
        other_mailbox = factories.MailboxFactory()
        unauthorized_signature = factories.MessageTemplateFactory(
            name="Unauthorized Signature",
            html_body="<p>Unauthorized content</p>",
            text_body="Unauthorized content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=other_mailbox,
            is_active=True,
        )

        html_body = "<p>Hello world!</p>"
        text_body = "Hello world!"
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=unauthorized_signature,
        )

        result = outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )

        assert result is True
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Hello world!" in content
        assert "Unauthorized content" not in content

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_prepare_outbound_message_with_only_signature(
        self, user, signature_template, mailbox_sender, mailbox_access
    ):
        """Test preparing message with only signature."""
        html_body = None
        text_body = None
        message = factories.MessageFactory(
            thread=factories.ThreadFactory(),
            sender=factories.ContactFactory(mailbox=mailbox_sender),
            is_draft=True,
            subject="Test Message",
            signature=signature_template,
        )

        outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )

        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Best regards,\nJohn Doe\nSoftware Engineer\nEngineering" in content
        assert (
            "Best regards,<br>John Doe<br>Software Engineer<br>Engineering</p>"
            in content
        )

        # Same with empty text and html bodies
        html_body = ""
        text_body = ""
        outbound.prepare_outbound_message(
            mailbox_sender, message, text_body, html_body, user
        )
        message.refresh_from_db()
        content = message.blob.get_content().decode()
        assert "Best regards,\nJohn Doe\nSoftware Engineer\nEngineering" in content
        assert (
            "Best regards,<br>John Doe<br>Software Engineer<br>Engineering</p>"
            in content
        )
