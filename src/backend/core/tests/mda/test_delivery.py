"""Tests for the core.mda.delivery module."""

import smtplib
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.utils import timezone

import pytest

from core import factories, models
from core.mda import delivery


@pytest.mark.django_db
class TestFindThread:
    """Unit tests for the _find_thread_for_inbound_message helper."""

    @pytest.fixture(autouse=True)
    def setup_mailbox(self, db):
        self.mailbox = factories.MailboxFactory()

    def test_find_by_references_and_subject(self):
        """Thread found via References header and matching normalized subject."""
        initial_subject = "Original Thread Subject"
        initial_mime_id = "original.123@example.com"
        initial_thread = factories.ThreadFactory(
            mailbox=self.mailbox, subject=initial_subject
        )
        factories.MessageFactory(
            thread=initial_thread, mime_id=initial_mime_id, subject=initial_subject
        )

        # Parsed email data for the incoming reply
        parsed_reply = {
            "subject": f"Re: {initial_subject}",
            "references": f"<other.ref@example.com> <{initial_mime_id}>",
            "from": [{"email": "replier@a.com"}],
            # ... other fields
        }

        found_thread = delivery._find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread == initial_thread

    def test_find_by_in_reply_to_and_subject(self):
        """Thread found via In-Reply-To header and matching normalized subject."""
        initial_subject = "Another Subject"
        initial_mime_id = "original.456@example.com"
        initial_thread = factories.ThreadFactory(
            mailbox=self.mailbox, subject=initial_subject
        )
        factories.MessageFactory(
            thread=initial_thread, mime_id=initial_mime_id, subject=initial_subject
        )

        parsed_reply = {
            "subject": f"Fwd: {initial_subject}",  # Different prefix, should still match
            "in_reply_to": f"<{initial_mime_id}>",
            "from": [{"email": "replier@a.com"}],
        }

        found_thread = delivery._find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread == initial_thread

    def test_find_fallback_no_subject_match(self):
        """Thread found via References header, falling back when subjects don't normalize."""
        initial_subject = "Meeting Request"
        initial_mime_id = "meeting.abc@example.com"
        initial_thread = factories.ThreadFactory(
            mailbox=self.mailbox, subject=initial_subject
        )
        factories.MessageFactory(
            thread=initial_thread, mime_id=initial_mime_id, subject=initial_subject
        )

        # Reply has reference, but completely different subject
        parsed_reply = {
            "subject": "Totally Unrelated Topic",
            "references": f"<{initial_mime_id}>",
            "from": [{"email": "replier@a.com"}],
        }

        # Should still match based on the reference alone (fallback logic)
        found_thread = delivery._find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread == initial_thread

    def test_no_match_returns_none(self):
        """No thread found if no matching references exist."""
        factories.ThreadFactory(
            mailbox=self.mailbox, subject="Some Thread"
        )  # Existing thread

        parsed_reply = {
            "subject": "Re: Some Thread",
            "references": "<nonexistent.ref@example.com>",
            "in_reply_to": "<another.nonexistent@example.com>",
            "from": [{"email": "replier@a.com"}],
        }

        found_thread = delivery._find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread is None

    def test_reference_in_different_mailbox(self):
        """No thread found if referenced message is in a different mailbox."""
        initial_subject = "My Mailbox Subject"
        initial_mime_id = "mine.xyz@example.com"
        initial_thread = factories.ThreadFactory(
            mailbox=self.mailbox, subject=initial_subject
        )
        factories.MessageFactory(
            thread=initial_thread, mime_id=initial_mime_id, subject=initial_subject
        )

        # Create a message in another mailbox with the same mime_id (unlikely but for test)
        other_mailbox = factories.MailboxFactory()
        other_thread = factories.ThreadFactory(
            mailbox=other_mailbox, subject="Other Subject"
        )
        factories.MessageFactory(
            thread=other_thread, mime_id=initial_mime_id, subject="Other Subject"
        )

        parsed_reply = {
            "subject": f"Re: {initial_subject}",
            "references": f"<{initial_mime_id}>",
            "from": [{"email": "replier@a.com"}],
        }

        # Should find the thread in *our* mailbox
        found_thread = delivery._find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread == initial_thread

    def test_no_references_returns_none(self):
        """No thread found if the incoming email has no reference headers."""
        factories.ThreadFactory(mailbox=self.mailbox, subject="Some Thread")

        parsed_new_email = {
            "subject": "Brand New Topic",
            # No In-Reply-To or References
            "from": [{"email": "new@a.com"}],
        }
        found_thread = delivery._find_thread_for_inbound_message(
            parsed_new_email, self.mailbox
        )
        assert found_thread is None


# --- Unit Tests for deliver_inbound_message --- #


@pytest.mark.django_db
class TestDeliverInboundMessage:
    """Unit tests for the deliver_inbound_message function."""

    @pytest.fixture
    def sample_parsed_email(self):
        return {
            "subject": "Delivery Test Subject",
            "from": [{"name": "Test Sender", "email": "sender@test.com"}],
            "to": [{"name": "Recipient Name", "email": "recipient@deliver.test"}],
            "cc": [],
            "bcc": [],
            "textBody": [{"content": "Test body content."}],
            "message_id": "test.delivery.1@example.com",
            "date": timezone.now(),
        }

    @pytest.fixture
    def raw_email_data(self):
        return b"Raw email data placeholder"

    @pytest.fixture
    def target_mailbox(self):
        domain = factories.MailDomainFactory(name="deliver.test")
        return factories.MailboxFactory(local_part="recipient", domain=domain)

    @patch("core.mda.delivery._find_thread_for_inbound_message")
    def test_basic_delivery_new_thread(
        self, mock_find_thread, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test successful delivery creating a new thread and contacts."""
        mock_find_thread.return_value = None  # Simulate no existing thread found
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"

        assert models.Thread.objects.count() == 0
        assert models.Contact.objects.count() == 0
        assert models.Message.objects.count() == 0

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True
        mock_find_thread.assert_called_once_with(sample_parsed_email, target_mailbox)

        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 1
        # Sender + Recipient contacts created associated with the target mailbox
        assert models.Contact.objects.count() == 2

        thread = models.Thread.objects.first()
        assert thread.mailbox == target_mailbox
        assert thread.subject == sample_parsed_email["subject"]
        assert not thread.is_read
        assert thread.snippet == "Test body content."

        message = models.Message.objects.first()
        assert message.thread == thread
        assert message.subject == sample_parsed_email["subject"]
        assert message.sender.email == "sender@test.com"
        assert message.sender.name == "Test Sender"
        assert message.sender.mailbox == target_mailbox
        assert message.raw_mime == raw_email_data
        assert message.mime_id == sample_parsed_email["message_id"]
        assert message.read_at is None

        assert message.recipients.count() == 1
        msg_recipient = message.recipients.first()
        assert msg_recipient.type == models.MessageRecipientTypeChoices.TO
        assert msg_recipient.contact.email == "recipient@deliver.test"
        assert msg_recipient.contact.name == "Recipient Name"
        assert msg_recipient.contact.mailbox == target_mailbox

    @patch("core.mda.delivery._find_thread_for_inbound_message")
    def test_basic_delivery_existing_thread(
        self, mock_find_thread, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test successful delivery adding message to an existing thread."""
        existing_thread = factories.ThreadFactory(mailbox=target_mailbox)
        mock_find_thread.return_value = existing_thread
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"

        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 0

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True
        mock_find_thread.assert_called_once_with(sample_parsed_email, target_mailbox)
        assert models.Thread.objects.count() == 1  # No new thread
        assert models.Message.objects.count() == 1
        message = models.Message.objects.first()
        assert message.thread == existing_thread
        # Ensure thread is marked unread again
        existing_thread.refresh_from_db()
        assert not existing_thread.is_read

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=True)
    def test_mailbox_creation_enabled(self, sample_parsed_email, raw_email_data):
        """Test mailbox is created automatically when MESSAGES_ACCEPT_ALL_EMAILS is True."""
        recipient_addr = "newuser@autocreate.test"
        assert not models.Mailbox.objects.filter(
            local_part="newuser", domain__name="autocreate.test"
        ).exists()

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True
        assert models.Mailbox.objects.filter(
            local_part="newuser", domain__name="autocreate.test"
        ).exists()
        assert models.Message.objects.count() == 1  # Check message was delivered

    @override_settings(
        MESSAGES_ACCEPT_ALL_EMAILS=False, MESSAGES_TESTDOMAIN="something.else"
    )
    def test_mailbox_creation_disabled(self, sample_parsed_email, raw_email_data):
        """Test delivery fails if mailbox doesn't exist and auto-creation is off."""
        recipient_addr = "nonexistent@disabled.test"
        assert not models.Mailbox.objects.filter(
            local_part="nonexistent", domain__name="disabled.test"
        ).exists()

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is False
        assert not models.Mailbox.objects.filter(
            local_part="nonexistent", domain__name="disabled.test"
        ).exists()
        assert models.Message.objects.count() == 0

    def test_contact_creation(
        self, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test that sender and recipient contacts are created correctly."""
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"
        sample_parsed_email["to"] = [{"name": "Test Recip", "email": recipient_addr}]
        sample_parsed_email["cc"] = [{"name": "CC Contact", "email": "cc@example.com"}]
        sender_email = sample_parsed_email["from"][0]["email"]

        assert not models.Contact.objects.filter(
            email=sender_email, mailbox=target_mailbox
        ).exists()
        assert not models.Contact.objects.filter(
            email=recipient_addr, mailbox=target_mailbox
        ).exists()
        assert not models.Contact.objects.filter(
            email="cc@example.com", mailbox=target_mailbox
        ).exists()

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True
        assert models.Contact.objects.filter(
            email=sender_email, mailbox=target_mailbox
        ).exists()
        assert models.Contact.objects.filter(
            email=recipient_addr, mailbox=target_mailbox
        ).exists()
        assert models.Contact.objects.filter(
            email="cc@example.com", mailbox=target_mailbox
        ).exists()
        assert models.MessageRecipient.objects.count() == 2  # TO and CC

    def test_invalid_sender_email_validation(
        self, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test delivery uses fallback sender if From address is invalid."""
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"
        sample_parsed_email["from"] = [
            {"name": "Invalid Sender", "email": "invalid-email-format"}
        ]

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True  # Should still succeed using fallback
        message = models.Message.objects.first()
        assert message is not None
        fallback_sender_email = f"invalid-sender@{target_mailbox.domain.name}"
        assert message.sender.email == fallback_sender_email
        assert message.sender.name == "Invalid Sender Address"
        assert models.Contact.objects.filter(
            email=fallback_sender_email, mailbox=target_mailbox
        ).exists()

    def test_no_sender_email(self, target_mailbox, sample_parsed_email, raw_email_data):
        """Test delivery uses fallback sender if From header is missing."""
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"
        del sample_parsed_email["from"]  # Remove From header

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True
        message = models.Message.objects.first()
        assert message is not None
        fallback_sender_email = f"unknown-sender@{target_mailbox.domain.name}"
        assert message.sender.email == fallback_sender_email
        assert message.sender.name == "Unknown Sender"
        assert models.Contact.objects.filter(
            email=fallback_sender_email, mailbox=target_mailbox
        ).exists()

    def test_invalid_recipient_email_skipped(
        self, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test that recipients with invalid email formats are skipped."""
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"
        sample_parsed_email["to"] = [
            {"name": "Valid Recip", "email": recipient_addr},
            {"name": "Invalid Recip", "email": "bad-email"},  # Invalid
        ]
        sample_parsed_email["cc"] = [
            {"name": "Another Invalid", "email": "@no-localpart.com"},  # Invalid
        ]

        success = delivery.deliver_inbound_message(
            recipient_addr, sample_parsed_email, raw_email_data
        )

        assert success is True  # Delivery succeeds overall
        message = models.Message.objects.first()
        assert message is not None
        # Only the valid recipient should have a MessageRecipient link
        assert message.recipients.count() == 1
        assert message.recipients.first().contact.email == recipient_addr
        # Check contacts were not created for invalid emails
        assert not models.Contact.objects.filter(email="bad-email").exists()
        assert not models.Contact.objects.filter(email="@no-localpart.com").exists()


# --- Unit Tests for send_outbound_message --- #


@pytest.mark.django_db
class TestSendOutboundMessage:
    """Unit tests for the send_outbound_message function."""

    @pytest.fixture
    def draft_message(self):
        """Create a valid draft message with sender and recipients."""
        sender_contact = factories.ContactFactory(email="sender@sendtest.com")
        mailbox = sender_contact.mailbox
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
            mta_sent=False,
            subject="Test Outbound",
            draft_body="Outbound body",
        )
        # Add recipients
        to_contact = factories.ContactFactory(mailbox=mailbox, email="to@example.com")
        cc_contact = factories.ContactFactory(mailbox=mailbox, email="cc@example.com")
        bcc_contact = factories.ContactFactory(mailbox=mailbox, email="bcc@example.com")
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
            contact=bcc_contact,
            type=models.MessageRecipientTypeChoices.BCC,
        )
        return message

    @patch("core.mda.delivery.smtplib.SMTP")  # Mock SMTP client
    @override_settings(
        MTA_OUT_HOST="smtp.test:1025",
        MTA_OUT_SMTP_USE_TLS=False,  # Explicitly override TLS setting
        # Ensure other auth settings are None for this test
        MTA_OUT_SMTP_USERNAME=None,
        MTA_OUT_SMTP_PASSWORD=None,
    )
    def test_send_success(self, mock_smtp, draft_message):
        """Test successful sending: calls composer, dkim, smtp and updates message."""
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = (
            mock_smtp_instance  # Mock context manager
        )

        success = delivery.send_outbound_message(draft_message)

        assert success is True

        # Check raw_mime was generated and passed to dkim
        assert draft_message.raw_mime is not None

        # Check SMTP calls
        mock_smtp.assert_called_once_with("smtp.test", 1025, timeout=10)
        mock_smtp_instance.ehlo.assert_called()
        # Assume no TLS/auth configured in this test override
        mock_smtp_instance.starttls.assert_not_called()
        mock_smtp_instance.login.assert_not_called()
        mock_smtp_instance.sendmail.assert_called_once()
        # Verify sendmail arguments
        call_args, _ = mock_smtp_instance.sendmail.call_args
        assert call_args[0] == draft_message.sender.email  # envelope_from
        assert set(call_args[1]) == {
            "to@example.com",
            "cc@example.com",
            "bcc@example.com",
        }  # envelope_to
        # Check that the signed message was sent
        assert call_args[2].endswith(draft_message.raw_mime)

        # Check message object updated
        draft_message.refresh_from_db()
        assert not draft_message.is_draft
        assert draft_message.mta_sent
        assert draft_message.sent_at is not None

    @patch("core.mda.delivery.smtplib.SMTP")
    @override_settings(MTA_OUT_HOST="smtp.fail:1025")
    def test_send_smtp_failure_retries(self, mock_smtp, draft_message):
        """Test that SMTP failures are retried and eventually fail."""
        mock_smtp_instance = MagicMock()
        # Make sendmail fail repeatedly
        mock_smtp_instance.sendmail.side_effect = smtplib.SMTPException(
            "Connection failed"
        )
        mock_smtp.return_value.__enter__.return_value = mock_smtp_instance

        with patch(
            "core.mda.delivery.time.sleep"
        ) as mock_sleep:  # Mock sleep to speed up test
            success = delivery.send_outbound_message(draft_message)

        assert success is False
        assert mock_smtp_instance.sendmail.call_count == 5  # Default max retries
        assert mock_sleep.call_count == 4  # Sleeps between retries

        # Check message object state (should remain draft)
        draft_message.refresh_from_db()
        assert draft_message.is_draft
        assert not draft_message.mta_sent
        assert draft_message.sent_at is None
