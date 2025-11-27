"""Tests for spam processing with rspamd."""

from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils import timezone

import pytest
import requests

from core import factories, models
from core.mda.inbound import deliver_inbound_message
from core.mda.tasks import (
    _check_spam_with_rspamd,
    process_inbound_message_task,
    process_inbound_messages_queue_task,
)


@pytest.mark.django_db
class TestDeliverInboundMessageQueueing:
    """Test that deliver_inbound_message queues messages instead of creating them directly."""

    @patch("core.mda.tasks.process_inbound_message_task.delay")
    def test_deliver_inbound_message_queues_message(self, mock_task_delay):
        """Test that deliver_inbound_message creates an InboundMessage in the queue."""
        mailbox = factories.MailboxFactory()
        recipient_email = f"{mailbox.local_part}@{mailbox.domain.name}"

        parsed_email = {
            "subject": "Test Email",
            "from": {"email": "sender@example.com", "name": "Test Sender"},
            "to": [{"email": recipient_email}],
            "date": timezone.now(),
        }
        raw_data = (
            b"From: sender@example.com\r\nTo: "
            + recipient_email.encode()
            + b"\r\n\r\nTest"
        )

        result = deliver_inbound_message(recipient_email, parsed_email, raw_data)

        assert result is True

        # Check that an InboundMessage was created
        inbound_message = models.InboundMessage.objects.get(mailbox=mailbox)
        assert inbound_message.raw_data == raw_data
        assert inbound_message.mailbox == mailbox

        # Check that the task was queued
        mock_task_delay.assert_called_once_with(str(inbound_message.id))

        # Check that no Message was created yet
        assert models.Message.objects.count() == 0

    def test_deliver_inbound_message_handles_duplicate(self):
        """Test that duplicate messages are handled correctly."""
        mailbox = factories.MailboxFactory()
        recipient_email = f"{mailbox.local_part}@{mailbox.domain.name}"

        # Create an existing message
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        mime_id = "test-message-id@example.com"
        factories.MessageFactory(thread=thread, mime_id=mime_id)

        parsed_email = {
            "messageId": mime_id,
            "subject": "Test Email",
            "from": {"email": "sender@example.com"},
            "to": [{"email": recipient_email}],
        }
        raw_data = b"Test email"

        result = deliver_inbound_message(recipient_email, parsed_email, raw_data)

        assert result is True

        # Check that no InboundMessage was created for duplicate
        assert models.InboundMessage.objects.count() == 0


@pytest.mark.django_db
class TestRspamdSpamCheck:
    """Test rspamd spam checking functionality."""

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks.requests.post")
    def test_check_spam_with_rspamd_spam(self, mock_post):
        """Test that spam messages are correctly identified."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "reject",
            "score": 20.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Spam email content"
        is_spam, error = _check_spam_with_rspamd(raw_data)

        assert is_spam is True
        assert error is None
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://rspamd:8010/_api/checkv2"
        assert call_args[1]["data"] == raw_data

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks.requests.post")
    def test_check_spam_with_rspamd_not_spam(self, mock_post):
        """Test that non-spam messages are correctly identified."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 5.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Legitimate email content"
        is_spam, error = _check_spam_with_rspamd(raw_data)

        assert is_spam is False
        assert error is None

    @override_settings(
        RSPAMD_URL="http://rspamd:8010/_api", RSPAMD_AUTH="Bearer token123"
    )
    @patch("core.mda.tasks.requests.post")
    def test_check_spam_with_rspamd_auth_header(self, mock_post):
        """Test that Authorization header is included when configured."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 5.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Email content"
        _check_spam_with_rspamd(raw_data)

        call_args = mock_post.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer token123"

    @override_settings(RSPAMD_URL=None)
    def test_check_spam_without_rspamd_config(self):
        """Test that spam check is skipped when rspamd is not configured."""
        raw_data = b"Email content"
        is_spam, error = _check_spam_with_rspamd(raw_data)

        assert is_spam is False
        assert error is None

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks.requests.post")
    def test_check_spam_with_rspamd_error(self, mock_post):
        """Test that errors in rspamd check are handled gracefully."""
        mock_post.side_effect = requests.exceptions.RequestException("Connection error")

        raw_data = b"Email content"
        is_spam, error = _check_spam_with_rspamd(raw_data)

        # On error, treat as not spam to avoid blocking legitimate messages
        assert is_spam is False
        assert error is not None


@pytest.mark.django_db
class TestProcessInboundMessageTask:
    """Test the process_inbound_message_task."""

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks._check_spam_with_rspamd")
    @patch("core.mda.tasks._create_message_from_inbound")
    def test_process_inbound_message_task_spam(
        self, mock_create_message, mock_check_spam
    ):
        """Test processing an inbound message that is spam."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Spam content"

        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=raw_data,
        )

        mock_check_spam.return_value = (True, None)  # is_spam=True
        mock_create_message.return_value = True

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        assert result["is_spam"] is True

        # Check that message was created with is_spam=True
        mock_create_message.assert_called_once()
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["is_spam"] is True

        # Check that inbound message was deleted after successful processing
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks._check_spam_with_rspamd")
    @patch("core.mda.tasks._create_message_from_inbound")
    def test_process_inbound_message_task_not_spam(
        self, mock_create_message, mock_check_spam
    ):
        """Test processing an inbound message that is not spam."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Legitimate content"

        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=raw_data,
        )

        mock_check_spam.return_value = (False, None)  # is_spam=False
        mock_create_message.return_value = True

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        assert result["is_spam"] is False

        # Check that message was created with is_spam=False
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["is_spam"] is False

    @override_settings(RSPAMD_URL="http://rspamd:8010/_api")
    @patch("core.mda.tasks._check_spam_with_rspamd")
    @patch("core.mda.tasks._create_message_from_inbound")
    def test_process_inbound_message_task_failure(
        self, mock_create_message, mock_check_spam
    ):
        """Test handling of failures in message creation."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Test content"

        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=raw_data,
        )

        mock_check_spam.return_value = (False, None)
        mock_create_message.return_value = False  # Creation failed

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is False
        assert "error" in result

        # Check that inbound message was kept for retry (not deleted)
        inbound_message.refresh_from_db()
        assert inbound_message.error_message is not None


@pytest.mark.django_db
class TestProcessInboundMessagesQueueTask:
    """Test the process_inbound_messages_queue_task."""

    @patch("core.mda.tasks.process_inbound_message_task.delay")
    def test_process_inbound_messages_queue_task(self, mock_task_delay):
        """Test that the queue processing task triggers individual message processing."""
        mailbox = factories.MailboxFactory()

        # Create multiple pending messages older than 5 minutes (for retry processing)
        old_time = timezone.now() - timezone.timedelta(minutes=6)
        for _ in range(3):
            inbound_message = models.InboundMessage.objects.create(
                mailbox=mailbox,
                raw_data=b"Content",
            )
            # Update created_at to make it old enough for retry
            models.InboundMessage.objects.filter(id=inbound_message.id).update(
                created_at=old_time
            )

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_messages_queue_task, "update_state", Mock()):
            result = process_inbound_messages_queue_task.run(10)

        assert result["success"] is True
        assert result["processed"] == 3
        assert result["total"] == 3
        assert mock_task_delay.call_count == 3
