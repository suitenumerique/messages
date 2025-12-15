"""Tests for send_message_task."""

from unittest.mock import MagicMock, patch

import pytest

from core import enums, factories
from core.mda.outbound_tasks import send_message_task


@pytest.mark.django_db
class TestSendMessageTask:
    """Test suite for send_message_task."""

    @pytest.fixture
    def mailbox_with_thread(self):
        """Create a mailbox with a thread and access."""
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        return mailbox, thread

    @pytest.fixture
    def draft_message(self, mailbox_with_thread):
        """Create a draft message for testing."""
        mailbox, thread = mailbox_with_thread
        sender_contact = factories.ContactFactory(mailbox=mailbox)
        return factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
            subject="Test Message",
        )

    def test_task_send_message_with_archive_true(
        self, draft_message, mailbox_with_thread
    ):
        """Test send_message_task with must_archive=True archives all thread messages."""
        mailbox, thread = mailbox_with_thread

        # Create additional messages in the same thread
        other_message1 = factories.MessageFactory(
            thread=thread,
            sender=draft_message.sender,
            is_draft=False,
            subject="Other Message 1",
        )
        other_message2 = factories.MessageFactory(
            thread=thread,
            sender=draft_message.sender,
            is_draft=False,
            subject="Other Message 2",
        )

        # Verify messages are not archived initially
        assert draft_message.is_archived is False
        assert draft_message.archived_at is None
        assert other_message1.is_archived is False
        assert other_message1.archived_at is None
        assert other_message2.is_archived is False
        assert other_message2.archived_at is None

        # Mock the send_message function
        with patch("core.mda.outbound_tasks.send_message") as mock_mda_send:
            # Call the task with must_archive=True
            with patch.object(send_message_task, "update_state"):
                result = send_message_task(str(draft_message.id), must_archive=True)

            # Verify send_message was called
            mock_mda_send.assert_called_once_with(draft_message, False)

            # Verify the result
            assert result["success"] is True
            assert result["message_id"] == str(draft_message.id)

        # Refresh messages from database
        draft_message.refresh_from_db()
        other_message1.refresh_from_db()
        other_message2.refresh_from_db()

        # Verify all messages in the thread are archived
        assert draft_message.is_archived
        assert draft_message.archived_at is not None
        assert other_message1.is_archived
        assert other_message1.archived_at is not None
        assert other_message2.is_archived
        assert other_message2.archived_at is not None

    def test_task_send_message_with_archive_false(
        self, draft_message, mailbox_with_thread
    ):
        """Test send_message_task with must_archive=False does not archive messages."""
        mailbox, thread = mailbox_with_thread

        # Create additional messages in the same thread
        other_message = factories.MessageFactory(
            thread=thread,
            sender=draft_message.sender,
            is_draft=False,
            subject="Other Message",
        )

        # Verify messages are not archived initially
        assert draft_message.is_archived is False
        assert draft_message.archived_at is None
        assert other_message.is_archived is False
        assert other_message.archived_at is None

        # Mock the send_message function
        with patch("core.mda.outbound_tasks.send_message") as mock_mda_send:
            # Call the task with must_archive=False
            with patch.object(send_message_task, "update_state"):
                result = send_message_task(str(draft_message.id), must_archive=False)

            # Verify the result
            assert result["success"] is True
            assert result["message_id"] == str(draft_message.id)

            # Verify send_message was called
            mock_mda_send.assert_called_once_with(draft_message, False)

        # Refresh messages from database
        draft_message.refresh_from_db()
        other_message.refresh_from_db()

        # Verify messages are NOT archived
        assert not draft_message.is_archived
        assert draft_message.archived_at is None
        assert not other_message.is_archived
        assert other_message.archived_at is None

    def test_task_send_message_archive_error_does_not_fail_task(
        self, draft_message, mailbox_with_thread
    ):
        """Test that archiving error does not cause the task to fail."""
        assert draft_message.is_draft is True

        # Mock the send_message function
        with patch("core.mda.outbound_tasks.send_message") as mock_mda_send:
            # Mock the Message.objects.filter().update() to raise an exception
            with patch("core.models.Message.objects.filter") as mock_filter:
                mock_queryset = MagicMock()
                mock_queryset.update.side_effect = Exception("Database error")
                mock_filter.return_value = mock_queryset

                # Call the task with must_archive=True
                # The task should succeed even if archiving fails
                with patch.object(send_message_task, "update_state"):
                    result = send_message_task(str(draft_message.id), must_archive=True)

                # Verify send_message was called
                mock_mda_send.assert_called_once_with(draft_message, False)

                # Verify the task still succeeds
                assert result["success"] is True
                assert result["message_id"] == str(draft_message.id)

    def test_task_send_message_updates_thread_stats_after_archive(
        self, draft_message, mailbox_with_thread
    ):
        """Test that thread stats are updated after archiving."""
        # Mock the send_message function
        with patch("core.mda.outbound_tasks.send_message"):
            # Mock thread.update_stats to verify it's called
            with patch("core.models.Thread.update_stats") as mock_update_stats:
                # Call the task with must_archive=True
                with patch.object(send_message_task, "update_state"):
                    result = send_message_task(str(draft_message.id), must_archive=True)

                # Verify the result
                assert result["success"] is True

                # Verify update_stats was called (once for archiving)
                assert mock_update_stats.call_count == 1
