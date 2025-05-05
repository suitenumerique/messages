"""Tests for the core.mda.inbound module."""

from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

import pytest

from core import factories, models
from core.mda import inbound


@pytest.mark.django_db
class TestFindThread:
    """Unit tests for the find_thread_for_inbound_message helper."""

    mailbox = None

    @pytest.fixture(autouse=True)
    def setup_mailbox(self):
        """Create a mailbox for testing thread finding."""
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
            "headers": {
                "references": f"<other.ref@example.com> <{initial_mime_id}>",
            },
            "from": {"email": "replier@a.com"},
        }

        found_thread = inbound.find_thread_for_inbound_message(
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
            "in_reply_to": f"{initial_mime_id}",
            "from": {"email": "replier@a.com"},
        }

        found_thread = inbound.find_thread_for_inbound_message(
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
            "headers": {
                "references": f"<{initial_mime_id}>",
            },
            "from": {"email": "replier@a.com"},
        }

        # Create a new thread
        found_thread = inbound.find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread is None

    def test_no_match_returns_none(self):
        """No thread found if no matching references exist."""
        factories.ThreadFactory(
            mailbox=self.mailbox, subject="Some Thread"
        )  # Existing thread

        parsed_reply = {
            "subject": "Re: Some Thread",
            "headers": {
                "references": "<nonexistent.ref@example.com>",
            },
            "in_reply_to": "another.nonexistent@example.com",
            "from": {"email": "replier@a.com"},
        }

        found_thread = inbound.find_thread_for_inbound_message(
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
            "headers": {
                "references": f"<{initial_mime_id}>",
            },
            "from": {"email": "replier@a.com"},
        }

        # Should find the thread in *our* mailbox
        found_thread = inbound.find_thread_for_inbound_message(
            parsed_reply, self.mailbox
        )
        assert found_thread == initial_thread

    def test_no_references_returns_none(self):
        """No thread found if the incoming email has no reference headers."""
        factories.ThreadFactory(mailbox=self.mailbox, subject="Some Thread")

        parsed_new_email = {
            "subject": "Brand New Topic",
            # No In-Reply-To or References
            "from": {"email": "new@a.com"},
        }
        found_thread = inbound.find_thread_for_inbound_message(
            parsed_new_email, self.mailbox
        )
        assert found_thread is None


@pytest.mark.django_db
class TestDeliverInboundMessage:
    """Unit tests for the deliver_inbound_message function."""

    @pytest.fixture
    def sample_parsed_email(self):
        """Sample parsed email data for testing delivery."""
        return {
            "subject": "Delivery Test Subject",
            "from": {"name": "Test Sender", "email": "sender@test.com"},
            "to": [{"name": "Recipient Name", "email": "recipient@deliver.test"}],
            "cc": [],
            "bcc": [],
            "textBody": [{"content": "Test body content."}],
            "message_id": "test.delivery.1@example.com",
            "date": timezone.now(),
        }

    @pytest.fixture
    def raw_email_data(self):
        """Raw email data placeholder."""
        return b"Raw email data placeholder"

    @pytest.fixture
    def target_mailbox(self):
        """Create a mailbox for testing delivery."""
        domain = factories.MailDomainFactory(name="deliver.test")
        return factories.MailboxFactory(local_part="recipient", domain=domain)

    @patch("core.mda.inbound.find_thread_for_inbound_message")
    def test_basic_delivery_new_thread(
        self, mock_find_thread, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test successful delivery creating a new thread and contacts."""
        mock_find_thread.return_value = None  # Simulate no existing thread found
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"

        assert models.Thread.objects.count() == 0
        assert models.Contact.objects.count() == 0
        assert models.Message.objects.count() == 0

        success = inbound.deliver_inbound_message(
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
        assert thread.count_unread == 1
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

    @patch("core.mda.inbound.find_thread_for_inbound_message")
    def test_basic_delivery_existing_thread(
        self, mock_find_thread, target_mailbox, sample_parsed_email, raw_email_data
    ):
        """Test successful delivery adding message to an existing thread."""
        existing_thread = factories.ThreadFactory(mailbox=target_mailbox)
        mock_find_thread.return_value = existing_thread
        recipient_addr = f"{target_mailbox.local_part}@{target_mailbox.domain.name}"

        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 0

        success = inbound.deliver_inbound_message(
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
        assert existing_thread.count_unread == 1

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=True)
    def test_mailbox_creation_enabled(self, sample_parsed_email, raw_email_data):
        """Test mailbox is created automatically when MESSAGES_ACCEPT_ALL_EMAILS is True."""
        recipient_addr = "newuser@autocreate.test"
        assert not models.Mailbox.objects.filter(
            local_part="newuser", domain__name="autocreate.test"
        ).exists()

        success = inbound.deliver_inbound_message(
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

        success = inbound.deliver_inbound_message(
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
        sender_email = sample_parsed_email["from"]["email"]

        assert not models.Contact.objects.filter(
            email=sender_email, mailbox=target_mailbox
        ).exists()
        assert not models.Contact.objects.filter(
            email=recipient_addr, mailbox=target_mailbox
        ).exists()
        assert not models.Contact.objects.filter(
            email="cc@example.com", mailbox=target_mailbox
        ).exists()

        success = inbound.deliver_inbound_message(
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
        sample_parsed_email["from"] = {
            "name": "Invalid Sender",
            "email": "invalid-email-format",
        }

        success = inbound.deliver_inbound_message(
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

        success = inbound.deliver_inbound_message(
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

        success = inbound.deliver_inbound_message(
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

    def test_email_exchange_single_thread(self):
        """Test a multi-step email exchange results in one thread per mailbox."""
        # Setup mailboxes
        domain = factories.MailDomainFactory(name="exchange.test")
        mailbox1 = factories.MailboxFactory(local_part="user1", domain=domain)
        mailbox2 = factories.MailboxFactory(local_part="user2", domain=domain)
        addr1 = str(mailbox1)
        addr2 = str(mailbox2)

        # 1. user1 -> user2
        subject = "Conversation Starter"
        parsed_email_1 = {
            "subject": subject,
            "from": {"name": "User One", "email": addr1},
            "to": [{"name": "User Two", "email": addr2}],
            "textBody": [{"content": "Hello User Two!"}],
            "message_id": "msg1.part1@exchange.test",
            "date": timezone.now(),
        }
        raw_email_1 = b"Raw for message 1"

        success1 = inbound.deliver_inbound_message(addr2, parsed_email_1, raw_email_1)
        assert success1 is True
        assert models.Thread.objects.filter(mailbox=mailbox1).count() == 0
        assert models.Thread.objects.filter(mailbox=mailbox2).count() == 1
        thread2 = models.Thread.objects.get(mailbox=mailbox2)
        assert thread2.messages.count() == 1
        assert thread2.subject == subject
        message1 = thread2.messages.first()
        assert message1.mime_id == parsed_email_1["message_id"]

        # 2. user2 -> user1 (Reply)
        parsed_email_2 = {
            "subject": f"Re: {subject}",
            "from": {"name": "User Two", "email": addr2},
            "to": [{"name": "User One", "email": addr1}],
            "textBody": [{"content": "Hi User One, thanks!"}],
            "message_id": "msg2.part2.reply@exchange.test",
            "in_reply_to": message1.mime_id,  # Link to previous message
            "headers": {"references": f"<{message1.mime_id}>"},
            "date": timezone.now(),
        }
        raw_email_2 = b"Raw for message 2"

        success2 = inbound.deliver_inbound_message(addr1, parsed_email_2, raw_email_2)
        assert success2 is True
        assert models.Thread.objects.filter(mailbox=mailbox1).count() == 1
        assert models.Thread.objects.filter(mailbox=mailbox2).count() == 1
        thread1 = models.Thread.objects.get(mailbox=mailbox1)
        assert thread1.messages.count() == 1
        message2 = thread1.messages.first()
        assert message2.mime_id == parsed_email_2["message_id"]
        assert thread1.subject == f"Re: {subject}"

        # 3. user1 -> user2 (Reply to Reply)
        parsed_email_3 = {
            "subject": f"Re: {subject}",
            "from": {"name": "User One", "email": addr1},
            "to": [{"name": "User Two", "email": addr2}],
            "textBody": [{"content": "You are welcome!"}],
            "message_id": "msg3.part3.rereply@exchange.test",
            "in_reply_to": message2.mime_id,  # Link to user2's reply
            "headers": {
                "references": f"<{message1.mime_id}> <{message2.mime_id}>"
            },  # Full chain
            "date": timezone.now(),
        }
        raw_email_3 = b"Raw for message 3"

        success3 = inbound.deliver_inbound_message(addr2, parsed_email_3, raw_email_3)
        assert success3 is True
        # Counts should remain 1 thread per mailbox
        assert models.Thread.objects.filter(mailbox=mailbox1).count() == 1
        assert models.Thread.objects.filter(mailbox=mailbox2).count() == 1

        # Verify message3 landed in thread2
        thread1.refresh_from_db()
        thread2.refresh_from_db()
        assert thread1.messages.count() == 1  # Still just message 2
        assert thread2.messages.count() == 2  # Now message 1 and message 3
        message3 = thread2.messages.exclude(id=message1.id).first()
        assert thread2.subject == subject  # Make sure the original subject is kept
        assert message3.mime_id == parsed_email_3["message_id"]
