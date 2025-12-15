"""Tests for the core.mda.outbound_tasks retry functionality."""
# pylint: disable=unused-argument

from unittest.mock import patch

from django.utils import timezone

import pytest

from core import enums, factories, models
from core.mda.outbound_tasks import retry_messages_task


@pytest.mark.django_db
class TestRetryMessagesTask:
    """Unit tests for the retry_messages_task function."""

    @pytest.fixture
    def mailbox_sender(self):
        """Create a test mailbox sender."""
        return factories.MailboxFactory()

    @pytest.fixture
    def thread(self, mailbox_sender):
        """Create a test thread."""
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox_sender,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        return thread

    @pytest.fixture
    def message_with_recipients(self, mailbox_sender, thread):
        """Create a message with recipients in various delivery states."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Test Retry Message",
        )

        # Create recipients with different delivery statuses
        to_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="to@example.com"
        )
        cc_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="cc@example.com"
        )
        bcc_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="bcc@example.com"
        )

        # Recipient with RETRY status
        factories.MessageRecipientFactory(
            message=message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            retry_at=timezone.now() - timezone.timedelta(minutes=1),  # Ready for retry
            retry_count=1,
        )

        # Recipient with null delivery_status (failed mid-route)
        factories.MessageRecipientFactory(
            message=message,
            contact=cc_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=None,  # This simulates prepare_message() done but no send
            retry_at=None,
            retry_count=0,
        )

        # Recipient with SENT status (should not be retried)
        factories.MessageRecipientFactory(
            message=message,
            contact=bcc_contact,
            type=models.MessageRecipientTypeChoices.BCC,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT,
            delivered_at=timezone.now(),
        )

        return message

    @pytest.fixture
    def draft_message(self, mailbox_sender, thread):
        """Create a draft message (should not be retryable)."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,  # Still a draft
            is_sender=True,
            subject="Draft Message",
        )
        return message

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_single_message_success(
        self, mock_send_message, message_with_recipients
    ):
        """Test retrying a single message by ID."""
        message = message_with_recipients

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(args=[str(message.id)]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_id"] == str(message.id)
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        assert result["processed_messages"] == 1

        # Verify send_message was called
        mock_send_message.assert_called_once_with(message, force_mta_out=False)

    def test_retry_nonexistent_message(self):
        """Test retrying a non-existent message."""
        fake_message_id = "00000000-0000-0000-0000-000000000000"

        result = retry_messages_task.apply(args=[fake_message_id]).get()

        # Verify the result
        assert result["success"] is False
        assert "does not exist" in result["error"]

    def test_retry_draft_message(self, draft_message):
        """Test retrying a draft message (should fail)."""
        result = retry_messages_task.apply(args=[str(draft_message.id)]).get()

        # Verify the result
        assert result["success"] is False
        assert "is still a draft" in result["error"]

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_bulk_mode(self, mock_send_message, message_with_recipients):
        """Test retrying messages in bulk mode (no message_id specified)."""
        message = message_with_recipients

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply().get()

        # Verify the result
        assert result["success"] is True
        assert result["total_messages"] == 1
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        assert result["processed_messages"] == 1

        # Verify send_message was called
        mock_send_message.assert_called_once_with(message, force_mta_out=False)

    def test_retry_no_messages_ready(self, mailbox_sender, thread):
        """Test retry when no messages are ready for retry."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="No Retry Message",
        )

        # Create recipients that are not ready for retry
        to_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="to@example.com"
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            retry_at=timezone.now() + timezone.timedelta(hours=1),  # Not ready yet
            retry_count=1,
        )

        result = retry_messages_task.apply().get()

        # Verify the result
        assert result["success"] is True
        assert result["total_messages"] == 0
        assert result["processed_messages"] == 0
        assert result["success_count"] == 0
        assert result["error_count"] == 0
        assert "No messages ready for retry" in result["message"]

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_failed_send_task_mid_route(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test retry when send_message_task() failed mid-route (null delivery_status)."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            sent_at=timezone.now() - timezone.timedelta(minutes=1),
            subject="Failed Mid-Route Message",
        )

        # Create recipients with null delivery_status (simulating prepare_message() done but no send)
        to_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="to@example.com"
        )
        cc_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="cc@example.com"
        )

        factories.MessageRecipientFactory(
            message=message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=None,  # Null status - prepare_message() done but no send
            retry_at=None,
            retry_count=0,
        )

        factories.MessageRecipientFactory(
            message=message,
            contact=cc_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=None,  # Null status - prepare_message() done but no send
            retry_at=None,
            retry_count=0,
        )

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(args=[str(message.id)]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_id"] == str(message.id)
        assert result["success_count"] == 1
        assert result["error_count"] == 0

        # Verify send_message was called
        mock_send_message.assert_called_once_with(message, force_mta_out=False)

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_timing_respect(self, mock_send_message, mailbox_sender, thread):
        """Test that retry respects retry timing (retry_at field)."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Timing Test Message",
        )

        # Create recipients with different retry timing
        ready_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="ready@example.com"
        )
        not_ready_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="notready@example.com"
        )

        # Recipient ready for retry
        factories.MessageRecipientFactory(
            message=message,
            contact=ready_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            retry_at=timezone.now() - timezone.timedelta(minutes=1),  # Ready
            retry_count=1,
        )

        # Recipient not ready for retry yet
        factories.MessageRecipientFactory(
            message=message,
            contact=not_ready_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            retry_at=timezone.now() + timezone.timedelta(hours=1),  # Not ready yet
            retry_count=1,
        )

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(args=[str(message.id)]).get()

        # Verify the result - should only process the ready recipient
        assert result["success"] is True
        assert result["success_count"] == 1

        # Verify send_message was called
        mock_send_message.assert_called_once_with(message, force_mta_out=False)

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_batch_processing(self, mock_send_message, mailbox_sender, thread):
        """Test retry batch processing functionality."""
        # Create multiple messages
        messages = []
        for i in range(5):
            sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
            message = factories.MessageFactory(
                thread=thread,
                sender=sender_contact,
                is_draft=False,
                is_sender=True,
                subject=f"Batch Test Message {i}",
            )

            # Add recipients ready for retry
            to_contact = factories.ContactFactory(
                mailbox=mailbox_sender, email=f"to{i}@example.com"
            )
            factories.MessageRecipientFactory(
                message=message,
                contact=to_contact,
                type=models.MessageRecipientTypeChoices.TO,
                delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
                retry_at=timezone.now() - timezone.timedelta(minutes=1),
                retry_count=1,
            )
            messages.append(message)

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(
            kwargs={"batch_size": 2}
        ).get()  # Process in batches of 2

        # Verify the result
        assert result["success"] is True
        assert result["total_messages"] == 5
        assert result["success_count"] == 5
        assert result["error_count"] == 0
        assert result["processed_messages"] == 5

        # Verify send_message was called for each message
        assert mock_send_message.call_count == 5

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_mixed_recipient_statuses(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test retry with recipients in various delivery states."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Mixed Status Message",
        )

        # Create recipients with different statuses
        retry_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="retry@example.com"
        )
        null_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="null@example.com"
        )
        sent_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="sent@example.com"
        )
        failed_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="failed@example.com"
        )

        # RETRY status - ready for retry
        factories.MessageRecipientFactory(
            message=message,
            contact=retry_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
            retry_at=timezone.now() - timezone.timedelta(minutes=1),
            retry_count=1,
        )

        # NULL status - failed mid-route
        factories.MessageRecipientFactory(
            message=message,
            contact=null_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=None,
            retry_at=None,
            retry_count=0,
        )

        # SENT status - should not be retried
        factories.MessageRecipientFactory(
            message=message,
            contact=sent_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT,
            delivered_at=timezone.now(),
        )

        # FAILED status - should not be retried
        factories.MessageRecipientFactory(
            message=message,
            contact=failed_contact,
            type=models.MessageRecipientTypeChoices.BCC,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            delivery_message="Permanent failure",
        )

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(args=[str(message.id)]).get()

        # Verify the result - should process 2 recipients (RETRY and NULL)
        assert result["success"] is True
        assert result["message_id"] == str(message.id)
        assert result["success_count"] == 1  # One message processed successfully
        assert result["error_count"] == 0
        assert result["processed_messages"] == 1

        # Verify send_message was called
        mock_send_message.assert_called_once_with(message, force_mta_out=False)

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_message_with_no_retryable_recipients(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test retry when message has no recipients ready for retry."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="No Retryable Recipients Message",
        )

        # Create recipients that are not retryable
        sent_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="sent@example.com"
        )
        failed_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="failed@example.com"
        )

        # SENT status - should not be retried
        factories.MessageRecipientFactory(
            message=message,
            contact=sent_contact,
            type=models.MessageRecipientTypeChoices.TO,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT,
            delivered_at=timezone.now(),
        )

        # FAILED status - should not be retried
        factories.MessageRecipientFactory(
            message=message,
            contact=failed_contact,
            type=models.MessageRecipientTypeChoices.CC,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            delivery_message="Permanent failure",
        )

        result = retry_messages_task.apply(args=[str(message.id)]).get()

        # Verify the result - should process the message but not call send_message
        assert result["success"] is True
        assert result["message_id"] == str(message.id)
        assert result["success_count"] == 0  # No recipients to retry
        assert result["error_count"] == 0
        assert result["processed_messages"] == 1

        # Verify send_message was NOT called because no recipients were retryable
        mock_send_message.assert_not_called()
