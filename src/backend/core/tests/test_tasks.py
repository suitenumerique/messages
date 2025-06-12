"""Tests for core tasks."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

import uuid
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError

import pytest

from core import models
from core.factories import MailboxFactory, UserFactory
from core.tasks import process_mbox_file_task, split_mbox_file


@pytest.fixture
def mailbox(user):
    """Create a test mailbox with admin access for the user."""
    mailbox = MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
    return mailbox


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def sample_mbox_content():
    """Create a sample MBOX file content."""
    return b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Test Message 1
From: sender1@example.com
To: recipient@example.com

This is test message 1.

From user@example.com Thu Jan 1 00:00:01 2024
Subject: Test Message 2
From: sender2@example.com
To: recipient@example.com

This is test message 2.

From user@example.com Thu Jan 1 00:00:02 2024
Subject: Test Message 3
From: sender3@example.com
To: recipient@example.com

This is test message 3.
"""


@pytest.fixture
def mock_task():
    """Create a mock task instance."""
    task = MagicMock()
    task.update_state = MagicMock()
    return task


@pytest.mark.django_db
class TestProcessMboxFileTask:
    """Test the process_mbox_file_task."""

    def test_process_mbox_file_success(self, mailbox, sample_mbox_content):
        """Test successful processing of MBOX file."""
        # Mock the deliver_inbound_message function to always succeed
        with patch("core.tasks.deliver_inbound_message", return_value=True):
            # Create a mock task instance
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            # Patch the task's update_state method
            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                # Call the task
                result = process_mbox_file_task(
                    file_content=sample_mbox_content, recipient_id=str(mailbox.id)
                )

                # Verify the result
                assert result["status"] == "completed"
                assert result["total_messages"] == 3
                assert result["success_count"] == 3
                assert result["failure_count"] == 0
                assert result["type"] == "mbox"

                # Verify progress updates were called correctly
                assert mock_task.update_state.call_count == 3
                for i in range(1, 4):
                    mock_task.update_state.assert_any_call(
                        state="PROGRESS",
                        meta={
                            "current": i,
                            "total": 3,
                            "status": f"Processing message {i} of 3",
                        },
                    )

    def test_process_mbox_file_partial_success(self, mailbox, sample_mbox_content):
        """Test MBOX processing with some messages failing."""

        # Mock deliver_inbound_message to fail for the second message
        def mock_deliver(*args, **kwargs):
            # Get the message content from args
            message_content = args[2]
            # Fail for the second message (contains "Test Message 2")
            return b"Test Message 2" not in message_content

        with patch(
            "core.tasks.deliver_inbound_message", side_effect=mock_deliver
        ) as mock_deliver:
            # Create a mock task instance
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            # Patch the task's update_state method
            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                # Call the task
                result = process_mbox_file_task(sample_mbox_content, str(mailbox.id))

                # Verify the result
                assert result["status"] == "completed"
                assert result["total_messages"] == 3
                assert result["success_count"] == 2
                assert result["failure_count"] == 1
                assert result["type"] == "mbox"

                # Verify progress updates were called for all messages
                assert mock_task.update_state.call_count == 3
                for i in range(1, 4):
                    mock_task.update_state.assert_any_call(
                        state="PROGRESS",
                        meta={
                            "current": i,
                            "total": 3,
                            "status": f"Processing message {i} of 3",
                        },
                    )

    def test_process_mbox_file_mailbox_not_found(self, sample_mbox_content):
        """Test MBOX processing with non-existent mailbox."""
        # Use a valid UUID format that doesn't exist
        non_existent_id = str(uuid.uuid4())

        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Patch the task's update_state method
        with patch.object(
            process_mbox_file_task, "update_state", mock_task.update_state
        ):
            # Call the task with non-existent mailbox ID
            result = process_mbox_file_task(sample_mbox_content, non_existent_id)

            # Verify the result
            assert result == (0, 0)  # Default return value for error case
            # Verify no progress updates were made
            assert mock_task.update_state.call_count == 0

    def test_process_mbox_file_parse_error(self, mailbox, sample_mbox_content):
        """Test MBOX processing with message parsing error."""

        # Mock parse_email_message to raise an exception for the second message
        def mock_parse(*args, **kwargs):
            message_content = args[0]
            if b"Test Message 2" in message_content:
                raise ValidationError("Invalid message format")
            # Return a properly structured dictionary for valid messages
            return {
                "headers": {
                    "from": "sender@example.com",
                    "to": "recipient@example.com",
                    "subject": "Test Message",
                    "message-id": "<test123@example.com>",
                    "references": "",
                },
                "body": "Test message body",
                "attachments": [],
            }

        # Mock deliver_inbound_message to always fail when parse succeeds
        # This ensures that even if parsing succeeds, delivery will fail
        def mock_deliver(*args, **kwargs):
            return False

        with (
            patch("core.tasks.parse_email_message", side_effect=mock_parse),
            patch("core.tasks.deliver_inbound_message", side_effect=mock_deliver),
        ):
            # Create a mock task instance
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            # Patch the task's update_state method
            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                # Call the task
                result = process_mbox_file_task(sample_mbox_content, str(mailbox.id))

                # Verify the result
                assert result["status"] == "completed"
                assert result["total_messages"] == 3
                assert result["success_count"] == 0  # All messages should fail
                assert result["failure_count"] == 3  # All messages should fail
                assert result["type"] == "mbox"

                # Verify progress updates were called for all messages
                assert mock_task.update_state.call_count == 3
                for i in range(1, 4):
                    mock_task.update_state.assert_any_call(
                        state="PROGRESS",
                        meta={
                            "current": i,
                            "total": 3,
                            "status": f"Processing message {i} of 3",
                        },
                    )

    def test_process_mbox_file_empty(self, mailbox):
        """Test processing an empty MBOX file."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Patch the task's update_state method
        with patch.object(
            process_mbox_file_task, "update_state", mock_task.update_state
        ):
            # Call the task with empty content
            result = process_mbox_file_task(b"", str(mailbox.id))

            # Verify the result
            assert result["status"] == "completed"
            assert result["total_messages"] == 0
            assert result["success_count"] == 0
            assert result["failure_count"] == 0
            assert result["type"] == "mbox"

            # Verify no progress updates were made
            assert mock_task.update_state.call_count == 0


@pytest.mark.django_db
class TestSplitMboxFile:
    """Test the split_mbox_file function."""

    def test_split_mbox_file_success(self, sample_mbox_content):
        """Test successful splitting of MBOX file."""
        messages = split_mbox_file(sample_mbox_content)
        assert len(messages) == 3
        # Messages are in reverse order (newest first) due to the [::-1] in split_mbox_file
        assert b"Test Message 3" in messages[0]
        assert b"Test Message 2" in messages[1]
        assert b"Test Message 1" in messages[2]

    def test_split_mbox_file_empty(self):
        """Test splitting an empty MBOX file."""
        messages = split_mbox_file(b"")
        assert len(messages) == 0

    def test_split_mbox_file_single_message(self):
        """Test splitting a MBOX file with a single message."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Single Message
From: sender@example.com
To: recipient@example.com

This is a single message.
"""
        messages = split_mbox_file(content)
        assert len(messages) == 1
        assert b"Single Message" in messages[0]

    def test_split_mbox_file_malformed(self):
        """Test splitting a malformed MBOX file."""
        # Content without proper From headers
        content = b"""Subject: Malformed Message
From: sender@example.com
To: recipient@example.com

This is a malformed message.
"""
        messages = split_mbox_file(content)
        assert len(messages) == 0  # No valid messages should be found
