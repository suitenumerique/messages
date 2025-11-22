"""Tests for the core.mda.outbound module."""
# pylint: disable=unused-argument,too-many-lines

import threading
import time
from unittest.mock import MagicMock, call, patch

from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

import dns.resolver
import pytest

from core import enums, factories, models
from core.mda import outbound
from core.mda.signing import generate_dkim_key, sign_message_dkim

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
        html_body="<p>Best regards,<br>{name}<br>{job_title}<br>{department}</p>",
        text_body="Best regards,\n{name}\n{job_title}\n{department}",
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
            is_sender=True,
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

        sorted_calls = sorted(mock_smtp_send.mock_calls, key=lambda x: x[2]["smtp_ip"])

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


class TestSendMessageRedisLock(TransactionTestCase):
    """Unit tests for the Redis lock functionality in send_message function."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.sender_contact = factories.ContactFactory(email="sender@sendtest.com")
        self.mailbox = self.sender_contact.mailbox
        self.thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=self.mailbox,
            thread=self.thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        self.message = factories.MessageFactory(
            thread=self.thread,
            sender=self.sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Test Lock",
        )
        # Create a blob with the raw MIME content
        blob = self.mailbox.create_blob(
            content=b"From: sender@sendtest.com\nTo: to@example.com\nSubject: Test Lock\n\nTest body",
            content_type="message/rfc822",
        )
        self.message.blob = blob
        self.message.save()
        # Add recipients
        to_contact = factories.ContactFactory(
            mailbox=self.mailbox, email="to@example.external"
        )
        factories.MessageRecipientFactory(
            message=self.message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

    def test_redis_lock_prevents_double_send_concurrent(self):
        """Test that Redis lock prevents multiple workers from sending the same message concurrently."""
        # Clear any existing locks
        cache.clear()

        # Track which workers actually called the outbound function
        outbound_calls = []
        call_lock = threading.Lock()

        def send_message_worker(worker_id, message):
            """Worker function that tries to send the message."""
            nonlocal outbound_calls

            with patch("core.mda.outbound.send_outbound_message") as mock_send_outbound:

                def mock_send_with_delay(*args, **kwargs):
                    time.sleep(1)  # Simulate actual email sending time
                    with call_lock:
                        outbound_calls.append(worker_id)
                    return {"to@example.external": {"delivered": True, "error": None}}

                mock_send_outbound.side_effect = mock_send_with_delay

                outbound.send_message(message, force_mta_out=True)

        # Start two concurrent workers
        thread1 = threading.Thread(target=send_message_worker, args=(1, self.message))
        thread1.start()

        time.sleep(0.5)

        thread2 = threading.Thread(target=send_message_worker, args=(2, self.message))
        thread2.start()

        # Wait for both threads to complete
        thread1.join()
        thread2.join()

        # Verify only one worker actually called the outbound function
        assert len(outbound_calls) == 1, (
            f"Expected 1 outbound call, got {len(outbound_calls)}: {outbound_calls}"
        )

        # Verify the message was processed
        self.message.refresh_from_db()
        assert self.message.sent_at is not None

    def test_redis_lock_released_after_success(self):
        """Test that Redis lock is released after successful message sending."""
        # Clear any existing locks
        cache.clear()

        # Mock the outbound delivery
        with patch("core.mda.outbound.send_outbound_message") as mock_send_outbound:
            mock_send_outbound.return_value = {
                "to@example.com": {"delivered": True, "error": None}
            }

            # Send the message
            outbound.send_message(self.message, force_mta_out=True)

            # Verify the lock was released by checking we can acquire it again
            lock_key = f"send_message_lock:{self.message.id}"
            assert cache.add(
                lock_key, "test", 60
            )  # Should succeed if lock was released

            # Clean up
            cache.delete(lock_key)

    def test_redis_lock_released_after_exception(self):
        """Test that Redis lock is released even when an exception occurs."""
        # Clear any existing locks
        cache.clear()

        # Mock send_outbound_message to raise an exception
        with patch("core.mda.outbound.send_outbound_message") as mock_send_outbound:
            mock_send_outbound.side_effect = Exception("Test exception")

            # Send the message (exception was raised and caught)
            outbound.send_message(self.message, force_mta_out=True)

            # Verify the lock was still released
            lock_key = f"send_message_lock:{self.message.id}"
            assert cache.add(
                lock_key, "test", 60
            )  # Should succeed if lock was released

            # Clean up
            cache.delete(lock_key)

    def test_redis_lock_timeout_prevents_deadlock(self):
        """Test that Redis lock has a timeout to prevent deadlocks."""
        # Clear any existing locks
        cache.clear()

        # Manually set a lock to simulate a stuck worker
        lock_key = f"send_message_lock:{self.message.id}"
        cache.set(lock_key, "stuck_worker", 1)  # 1 second timeout

        # Wait for the lock to expire
        time.sleep(1.1)

        # Now the message should be processable again
        with patch("core.mda.outbound.send_outbound_message") as mock_send_outbound:
            mock_send_outbound.return_value = {
                "to@example.com": {"delivered": True, "error": None}
            }

            outbound.send_message(self.message, force_mta_out=True)

            # Verify the message was processed
            self.message.refresh_from_db()
            assert self.message.sent_at is not None


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
        assert (
            "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering" in content
        )

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
        assert (
            "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering" in content
        )

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
        assert (
            "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering" in content
        )
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
        assert (
            "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering" in content
        )
        assert (
            "Best regards,<br>John Doe<br>Software Engineer<br>Engineering</p>"
            in content
        )


@pytest.mark.django_db
class TestSendMessageDKIMVerification:
    """Test DKIM verification in send_message."""

    @override_settings(MESSAGES_DKIM_VERIFY_OUTGOING=True)
    @patch("core.mda.signing.dns.resolver.resolve")
    @patch("core.mda.outbound.send_outbound_message")
    def test_dkim_verification_success(
        self, mock_send_outbound, mock_dns_resolve, mailbox_sender
    ):
        """Test that DKIM verification succeeds and message is sent."""
        # Create a message with external recipient
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox_sender,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Test DKIM",
        )

        private_key, public_key = generate_dkim_key(key_size=1024)
        dkim_key = models.DKIMKey.objects.create(
            selector="testselector",
            private_key=private_key,
            public_key=public_key,
            key_size=1024,
            is_active=True,
            domain=mailbox_sender.domain,
        )

        # Prepare and sign the message
        domain_name = mailbox_sender.domain.name
        raw_mime = f"From: test@{domain_name}\r\nTo: external@other.com\r\nSubject: Test\r\n\r\nBody\r\n".encode()
        signature_header = sign_message_dkim(raw_mime, mailbox_sender.domain)
        signed_mime = signature_header + b"\r\n" + raw_mime

        # Create blob with signed message
        blob = mailbox_sender.create_blob(
            content=signed_mime, content_type="message/rfc822"
        )
        message.blob = blob
        message.save()

        # Add external recipient
        external_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="external@other.com"
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=external_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

        # Mock DNS to return the DKIM public key
        def mock_dns_resolve_func(query_name, record_type, **kwargs):
            expected_fqdn = f"testselector._domainkey.{domain_name}"
            if record_type == "TXT" and query_name == expected_fqdn:
                mock_answer = MagicMock()
                mock_answer.strings = [
                    f"v=DKIM1; k=rsa; p={dkim_key.public_key}".encode()
                ]
                return [mock_answer]
            raise dns.resolver.NoAnswer()

        mock_dns_resolve.side_effect = mock_dns_resolve_func

        # Mock successful send
        mock_send_outbound.return_value = {"external@other.com": {"delivered": True}}

        # Send the message
        outbound.send_message(message)

        # Verify DNS was queried for DKIM record
        assert mock_dns_resolve.called

        # Verify message was sent (not marked for retry)
        message.refresh_from_db()
        recipient = message.recipients.first()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.SENT
        assert mock_send_outbound.called

    @override_settings(MESSAGES_DKIM_VERIFY_OUTGOING=True)
    @patch("core.mda.signing.dns.resolver.resolve")
    @patch("core.mda.outbound.send_outbound_message")
    def test_dkim_verification_failure_marks_for_retry(
        self, mock_send_outbound, mock_dns_resolve, mailbox_sender
    ):
        """Test that DKIM verification failure marks recipients for retry."""
        # Create a message with external recipient
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox_sender,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Test DKIM",
        )

        private_key, public_key = generate_dkim_key(key_size=1024)
        _dkim_key = models.DKIMKey.objects.create(
            selector="testselector",
            private_key=private_key,
            public_key=public_key,
            key_size=1024,
            is_active=True,
            domain=mailbox_sender.domain,
        )

        # Prepare and sign the message
        domain_name = mailbox_sender.domain.name
        raw_mime = f"From: test@{domain_name}\r\nTo: external@other.com\r\nSubject: Test\r\n\r\nBody\r\n".encode()
        signature_header = sign_message_dkim(raw_mime, mailbox_sender.domain)
        signed_mime = signature_header + b"\r\n" + raw_mime

        # Create blob with signed message
        blob = mailbox_sender.create_blob(
            content=signed_mime, content_type="message/rfc822"
        )
        message.blob = blob
        message.save()

        # Add external recipient
        external_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="external@other.com"
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            contact=external_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

        # Mock DNS to fail (no DKIM record found)
        mock_dns_resolve.side_effect = dns.resolver.NoAnswer()

        # Send the message
        outbound.send_message(message)

        # Verify DNS was queried
        assert mock_dns_resolve.called

        # Verify message was NOT sent
        assert not mock_send_outbound.called

        # Verify recipient was marked for retry
        recipient.refresh_from_db()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.RETRY
        assert recipient.retry_at is not None
        assert "DKIM verification failed" in recipient.delivery_message

    @override_settings(MESSAGES_DKIM_VERIFY_OUTGOING=True)
    @patch("core.mda.signing.dns.resolver.resolve")
    @patch("core.mda.outbound.deliver_inbound_message")
    def test_dkim_verification_skipped_for_internal_recipients(
        self, mock_deliver_inbound, mock_dns_resolve, mailbox_sender
    ):
        """Test that DKIM verification is skipped for internal recipients."""
        # Create a message with internal recipient
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox_sender,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Test DKIM",
        )

        # Create blob with message
        domain_name = mailbox_sender.domain.name
        raw_mime = f"From: test@{domain_name}\r\nTo: internal@{domain_name}\r\nSubject: Test\r\n\r\nBody\r\n".encode()
        blob = mailbox_sender.create_blob(
            content=raw_mime, content_type="message/rfc822"
        )
        message.blob = blob
        message.save()

        # Add internal recipient (same domain)
        # Create mailbox with matching local_part
        internal_mailbox = factories.MailboxFactory(
            domain=mailbox_sender.domain, local_part="internal"
        )
        internal_contact = factories.ContactFactory(
            mailbox=internal_mailbox, email=f"internal@{domain_name}"
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=internal_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

        # Mock internal delivery
        mock_deliver_inbound.return_value = True

        # Send the message
        outbound.send_message(message)

        # Verify DNS was NOT queried (DKIM verification skipped for internal)
        assert not mock_dns_resolve.called

        # Verify internal delivery was attempted
        assert mock_deliver_inbound.called
