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
    def test_retry_messages_set_success(
        self, mock_send_message, message_with_recipients
    ):
        """Test retrying a single message by ID."""
        message_ids = [str(message_with_recipients.id)]

        # Mock successful send
        mock_send_message.return_value = None

        result = retry_messages_task.apply(args=[message_ids]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_ids"] == message_ids
        assert result["success_count"] == 1
        assert result["error_count"] == 0
        assert result["processed_messages"] == 1

        # Verify send_message was called
        mock_send_message.assert_called_once_with(
            message_with_recipients, force_mta_out=False
        )

    def test_retry_nonexistent_message(self):
        """Test retrying a non-existent message."""
        fake_message_id = "00000000-0000-0000-0000-000000000000"

        result = retry_messages_task.apply(args=[[fake_message_id]]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_ids"] == [fake_message_id]
        assert result["total_messages"] == 0
        assert result["success_count"] == 0
        assert result["error_count"] == 0
        assert result["processed_messages"] == 0
        assert result["message"] == "No messages ready for retry"

    def test_retry_draft_message(self, draft_message):
        """Test retrying a draft message (should fail)."""
        result = retry_messages_task.apply(args=[[str(draft_message.id)]]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_ids"] == [str(draft_message.id)]
        assert result["total_messages"] == 0
        assert result["success_count"] == 0
        assert result["error_count"] == 0
        assert result["processed_messages"] == 0
        assert result["message"] == "No messages ready for retry"

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

        result = retry_messages_task.apply(args=[[str(message.id)]]).get()

        # Verify the result
        assert result["success"] is True
        assert result["message_ids"] == [str(message.id)]
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

        result = retry_messages_task.apply(args=[[str(message.id)]]).get()

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

        result = retry_messages_task.apply(args=[[str(message.id)]]).get()

        # Verify the result - should process 2 recipients (RETRY and NULL)
        assert result["success"] is True
        assert result["message_ids"] == [str(message.id)]
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

        result = retry_messages_task.apply(args=[[str(message.id)]]).get()

        # Verify the result - should process the message but not call send_message
        assert result["success"] is True
        assert result["message_ids"] == [str(message.id)]
        assert result["success_count"] == 0  # No recipients to retry
        assert result["error_count"] == 0
        assert result["processed_messages"] == 0

        # Verify send_message was NOT called because no recipients were retryable
        mock_send_message.assert_not_called()

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_message_with_empty_message_ids_list(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test retry when message_ids list is empty."""
        sender_contact = factories.ContactFactory(mailbox=mailbox_sender)

        # Create a message that could be retried but do not include it in the message_ids list
        # It should be not processed
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="A Message without delivery status",
        )

        # Create one recipient with no delivery status
        sent_contact = factories.ContactFactory(
            mailbox=mailbox_sender, email="sent@example.com"
        )

        # SENT status - should not be retried
        factories.MessageRecipientFactory(
            message=message,
            contact=sent_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

        result = retry_messages_task.apply(args=[[]]).get()

        # Verify the result - should process the message but not call send_message
        assert result["success"] is True
        assert result["message_ids"] == []
        assert result["success_count"] == 0  # No recipients to retry
        assert result["error_count"] == 0
        assert result["processed_messages"] == 0

        # Verify send_message was NOT called because no recipients were retryable
        mock_send_message.assert_not_called()

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_update_state_called_once_per_batch(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test that update_state is called once per batch, not per message."""
        # Create 5 messages with retryable recipients
        messages = []
        for i in range(5):
            sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
            message = factories.MessageFactory(
                thread=thread,
                sender=sender_contact,
                is_draft=False,
                is_sender=True,
                subject=f"Batch State Test Message {i}",
            )

            # Add recipients ready for retry
            to_contact = factories.ContactFactory(
                mailbox=mailbox_sender, email=f"to_state{i}@example.com"
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

        # Patch update_state to track calls
        with patch.object(retry_messages_task, "update_state") as mock_update_state:
            result = retry_messages_task.apply(kwargs={"batch_size": 2}).get()

        # Verify the result
        assert result["success"] is True
        assert result["total_messages"] == 5
        assert result["success_count"] == 5

        # With 5 messages and batch_size=2, we should have 3 update_state calls:
        # - At index 0 (start of batch 1)
        # - At index 2 (start of batch 2)
        # - At index 4 (start of batch 3)
        assert mock_update_state.call_count == 3

        # Verify the calls have correct batch information
        calls = mock_update_state.call_args_list

        # First call at index 0 (batch 1)
        assert calls[0].kwargs["state"] == "PROGRESS"
        assert calls[0].kwargs["meta"]["current_batch"] == 1
        assert calls[0].kwargs["meta"]["total_batches"] == 3

        # Second call at index 2 (batch 2)
        assert calls[1].kwargs["state"] == "PROGRESS"
        assert calls[1].kwargs["meta"]["current_batch"] == 2
        assert calls[1].kwargs["meta"]["total_batches"] == 3

        # Third call at index 4 (batch 3)
        assert calls[2].kwargs["state"] == "PROGRESS"
        assert calls[2].kwargs["meta"]["current_batch"] == 3
        assert calls[2].kwargs["meta"]["total_batches"] == 3

    @patch("core.mda.outbound_tasks.send_message")
    def test_retry_update_state_not_called_every_message(
        self, mock_send_message, mailbox_sender, thread
    ):
        """Test that update_state is NOT called for every message when batch_size > 1."""
        # Create 10 messages with retryable recipients
        messages = []
        for i in range(10):
            sender_contact = factories.ContactFactory(mailbox=mailbox_sender)
            message = factories.MessageFactory(
                thread=thread,
                sender=sender_contact,
                is_draft=False,
                is_sender=True,
                subject=f"No Per-Message Update Test {i}",
            )

            to_contact = factories.ContactFactory(
                mailbox=mailbox_sender, email=f"to_noupdate{i}@example.com"
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

        mock_send_message.return_value = None

        with patch.object(retry_messages_task, "update_state") as mock_update_state:
            result = retry_messages_task.apply(kwargs={"batch_size": 3}).get()

        assert result["success"] is True
        assert result["total_messages"] == 10
        assert result["success_count"] == 10

        # With 10 messages and batch_size=3, update_state should be called 4 times:
        # At indices 0, 3, 6, 9 (not 10 times for each message)
        assert mock_update_state.call_count == 4
        # Verify it's significantly less than total messages
        assert mock_update_state.call_count < result["total_messages"]
