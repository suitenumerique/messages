"""Tests for importer tasks."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

import logging
import uuid
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

from django.core.exceptions import ValidationError

import pytest

from core import models
from core.factories import MailboxFactory, UserFactory
from core.mda.inbound import deliver_inbound_message
from core.models import Message
from core.services.importer.tasks import process_mbox_file_task, stream_mbox_messages

logger = logging.getLogger(__name__)


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


def mock_storage_open(content: bytes):
    """Helper to create a mock storage that returns the given content.

    The mock allows multiple opens since the task opens the file twice:
    once for counting and once for processing.
    """

    def create_file(*args, **kwargs):
        return BytesIO(content)

    mock_storage = Mock()
    mock_storage.open = Mock(side_effect=create_file)
    return mock_storage


@pytest.mark.django_db
class TestProcessMboxFileTask:
    """Test suite for process_mbox_file_task."""

    def test_task_process_mbox_file_success(self, mailbox, sample_mbox_content):
        """Test successful MBOX file processing."""
        # Mock deliver_inbound_message to always succeed
        with patch("core.mda.inbound.deliver_inbound_message", return_value=True):
            # Create a mock task instance
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            # Mock storage
            mock_storage = mock_storage_open(sample_mbox_content)

            with (
                patch.object(
                    process_mbox_file_task, "update_state", mock_task.update_state
                ),
                patch("core.services.importer.tasks.storages") as mock_storages,
            ):
                mock_storages.__getitem__.return_value = mock_storage

                # Run the task
                task_result = process_mbox_file_task(
                    file_key="test-file-key.mbox", recipient_id=str(mailbox.id)
                )

                # Verify task result
                assert task_result["status"] == "SUCCESS"
                assert (
                    task_result["result"]["message_status"]
                    == "Completed processing messages"
                )
                assert task_result["result"]["type"] == "mbox"
                assert task_result["result"]["total_messages"] == 3
                assert task_result["result"]["success_count"] == 3
                assert task_result["result"]["failure_count"] == 0
                assert task_result["result"]["current_message"] == 3

                # Verify progress updates
                assert mock_task.update_state.call_count == 5  # 4 PROGRESS + 1 SUCCESS

                # First message
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Processing message 1 of 3",
                            "total_messages": 3,
                            "success_count": 0,
                            "failure_count": 0,
                            "type": "mbox",
                            "current_message": 1,
                        },
                        "error": None,
                    },
                )

                # Second message
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Processing message 2 of 3",
                            "total_messages": 3,
                            "success_count": 1,
                            "failure_count": 0,
                            "type": "mbox",
                            "current_message": 2,
                        },
                        "error": None,
                    },
                )

                # Third message
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Processing message 3 of 3",
                            "total_messages": 3,
                            "success_count": 2,
                            "failure_count": 0,
                            "type": "mbox",
                            "current_message": 3,
                        },
                        "error": None,
                    },
                )

                # Verify success update
                mock_task.update_state.assert_called_with(
                    state="SUCCESS",
                    meta={
                        "result": task_result["result"],
                        "error": None,
                    },
                )

                # Verify messages were created
                message_count = Message.objects.count()
                assert message_count == 3, f"Expected 3 messages, got {message_count}"
                messages = Message.objects.order_by("created_at")
                assert messages[0].subject == "Test Message 3"
                assert messages[1].subject == "Test Message 2"
                assert messages[2].subject == "Test Message 1"

    def test_task_process_mbox_file_partial_success(self, mailbox, sample_mbox_content):
        """Test MBOX processing with some messages failing."""

        # Mock deliver_inbound_message to fail for the second message
        original_deliver = deliver_inbound_message

        def mock_deliver(recipient_email, parsed_email, raw_data, **kwargs):
            # Get the subject from the parsed email dictionary
            subject = parsed_email.get("headers", {}).get("subject", "")

            # Return False for Test Message 2 without creating the message
            if subject == "Test Message 2":
                return False

            # For other messages, call the original function to create the message
            return original_deliver(recipient_email, parsed_email, raw_data, **kwargs)

        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Mock storage
        mock_storage = mock_storage_open(sample_mbox_content)

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.deliver_inbound_message",
                side_effect=mock_deliver,
            ),
            patch("core.services.importer.tasks.storages") as mock_storages,
        ):
            mock_storages.__getitem__.return_value = mock_storage
            # Call the task once
            task_result = process_mbox_file_task("test-file-key.mbox", str(mailbox.id))

            # Verify task result
            assert task_result["status"] == "SUCCESS"
            assert (
                task_result["result"]["message_status"]
                == "Completed processing messages"
            )
            assert task_result["result"]["type"] == "mbox"
            assert task_result["result"]["total_messages"] == 3
            assert task_result["result"]["success_count"] == 2
            assert task_result["result"]["failure_count"] == 1
            assert task_result["result"]["current_message"] == 3

            # Verify progress updates
            assert mock_task.update_state.call_count == 5  # 4 PROGRESS + 1 SUCCESS

            # First message (success)
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 1 of 3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 0,
                        "type": "mbox",
                        "current_message": 1,
                    },
                    "error": None,
                },
            )

            # Second message (failure)
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 2 of 3",
                        "total_messages": 3,
                        "success_count": 1,
                        "failure_count": 0,
                        "type": "mbox",
                        "current_message": 2,
                    },
                    "error": None,
                },
            )

            # Third message (success)
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 3 of 3",
                        "total_messages": 3,
                        "success_count": 1,
                        "failure_count": 1,
                        "type": "mbox",
                        "current_message": 3,
                    },
                    "error": None,
                },
            )

            # Verify success update
            mock_task.update_state.assert_called_with(
                state="SUCCESS",
                meta={
                    "result": task_result["result"],
                    "error": None,
                },
            )

            # Verify messages were created
            assert Message.objects.count() == 2
            messages = Message.objects.order_by("-created_at")
            assert messages[0].subject == "Test Message 1"
            assert messages[1].subject == "Test Message 3"

    def test_task_process_mbox_file_mailbox_not_found(self, sample_mbox_content):
        """Test MBOX processing with non-existent mailbox."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Use a valid UUID that doesn't exist in the database
        non_existent_id = str(uuid.uuid4())

        # Mock storage
        mock_storage = mock_storage_open(sample_mbox_content)

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch("core.services.importer.tasks.storages") as mock_storages,
        ):
            mock_storages.__getitem__.return_value = mock_storage
            # Run the task with non-existent mailbox
            task_result = process_mbox_file_task(
                file_key="test-file-key.mbox", recipient_id=non_existent_id
            )

            # Verify task result
            assert task_result["status"] == "FAILURE"
            assert (
                task_result["result"]["message_status"] == "Failed to process messages"
            )
            assert task_result["result"]["type"] == "mbox"
            assert task_result["result"]["total_messages"] == 0
            assert task_result["result"]["success_count"] == 0
            assert task_result["result"]["failure_count"] == 0
            assert task_result["result"]["current_message"] == 0
            assert (
                f"Recipient mailbox {non_existent_id} not found" in task_result["error"]
            )

            # Verify only failure update was called
            assert mock_task.update_state.call_count == 1
            mock_task.update_state.assert_called_once_with(
                state="FAILURE",
                meta={
                    "result": task_result["result"],
                    "error": task_result["error"],
                },
            )

            # Verify no messages were created
            assert Message.objects.count() == 0

    def test_task_process_mbox_file_parse_error(self, mailbox, sample_mbox_content):
        """Test MBOX processing with message parsing error."""

        # Mock parse_email_message to raise an exception for all messages
        def mock_parse(*args, **kwargs):
            raise ValidationError("Invalid message format")

        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Mock storage
        mock_storage = mock_storage_open(sample_mbox_content)

        with (
            patch(
                "core.services.importer.tasks.parse_email_message",
                side_effect=mock_parse,
            ),
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch("core.services.importer.tasks.storages") as mock_storages,
        ):
            mock_storages.__getitem__.return_value = mock_storage
            # Call the task
            task_result = process_mbox_file_task("test-file-key.mbox", str(mailbox.id))

            # Verify the result
            assert task_result["status"] == "SUCCESS"
            assert task_result["result"]["total_messages"] == 3
            assert (
                task_result["result"]["success_count"] == 0
            )  # All messages should fail
            assert (
                task_result["result"]["failure_count"] == 3
            )  # All messages should fail
            assert task_result["result"]["type"] == "mbox"

            # Verify progress updates were called for all messages
            assert mock_task.update_state.call_count == 5  # 4 PROGRESS + 1 SUCCESS

            # The first update should be for message 1 with failure_count 0
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 1 of 3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 0,  # No failures yet
                        "type": "mbox",
                        "current_message": 1,
                    },
                    "error": None,
                },
            )

            # The second update should be for message 2 with failure_count 1
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 2 of 3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 1,  # One failure from message 1
                        "type": "mbox",
                        "current_message": 2,
                    },
                    "error": None,
                },
            )

            # The third update should be for message 3 with failure_count 2
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 3 of 3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 2,  # Two failures from messages 1 and 2
                        "type": "mbox",
                        "current_message": 3,
                    },
                    "error": None,
                },
            )

            # Verify final success update
            mock_task.update_state.assert_called_with(
                state="SUCCESS",
                meta={
                    "result": task_result["result"],
                    "error": None,
                },
            )

            # Verify no messages were created
            assert Message.objects.count() == 0

    def test_task_process_mbox_file_empty(self, mailbox):
        """Test processing an empty MBOX file."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Mock storage with empty content
        mock_storage = mock_storage_open(b"")

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch("core.services.importer.tasks.storages") as mock_storages,
            patch("magic.Magic.from_buffer") as mock_magic_from_buffer,
        ):
            mock_magic_from_buffer.return_value = "application/mbox"
            mock_storages.__getitem__.return_value = mock_storage
            # Run the task with empty content
            task_result = process_mbox_file_task(
                file_key="test-file-key.mbox", recipient_id=str(mailbox.id)
            )

            # Verify task result
            assert task_result["status"] == "SUCCESS"
            assert (
                task_result["result"]["message_status"]
                == "Completed processing messages"
            )
            assert task_result["result"]["type"] == "mbox"
            assert task_result["result"]["total_messages"] == 0
            assert task_result["result"]["success_count"] == 0
            assert task_result["result"]["failure_count"] == 0
            assert task_result["result"]["current_message"] == 0

            # Verify 2 updates were called: 1 PROGRESS TO COUNT MESSAGES + 1 SUCCESS
            assert mock_task.update_state.call_count == 2
            mock_task.update_state.assert_called_with(
                state="SUCCESS",
                meta={
                    "result": task_result["result"],
                    "error": None,
                },
            )

            # Verify no messages were created
            assert Message.objects.count() == 0

    def test_task_process_mbox_invalid_file(self, mailbox):
        """Test processing an invalid MBOX file."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Mock storage with empty content
        mock_storage = mock_storage_open(b"")

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch("core.services.importer.tasks.storages") as mock_storages,
        ):
            mock_storages.__getitem__.return_value = mock_storage
            # Run the task with empty content
            task_result = process_mbox_file_task(
                file_key="test-file-key.mbox", recipient_id=str(mailbox.id)
            )

            # Verify task result
            assert task_result["status"] == "FAILURE"
            assert (
                task_result["result"]["message_status"] == "Failed to process messages"
            )
            assert task_result["result"]["type"] == "mbox"
            assert task_result["result"]["total_messages"] == 0
            assert task_result["result"]["success_count"] == 0
            assert task_result["result"]["failure_count"] == 0
            assert task_result["result"]["current_message"] == 0
            assert task_result["error"] == "Expected MBOX file, got application/x-empty"

            # Verify 2 updates were called: 1 PROGRESS TO COUNT MESSAGES + 1 FAILURE
            assert mock_task.update_state.call_count == 2
            mock_task.update_state.assert_called_with(
                state="FAILURE",
                meta={
                    "result": task_result["result"],
                    "error": task_result["error"],
                },
            )

            # Verify no messages were created
            assert Message.objects.count() == 0


@pytest.mark.django_db
class TestStreamMboxMessages:
    """Test the stream_mbox_messages function."""

    def test_task_stream_mbox_messages_success(self, sample_mbox_content):
        """Test successful streaming of MBOX file."""
        file = BytesIO(sample_mbox_content)
        messages = list(stream_mbox_messages(file))
        assert len(messages) == 3
        # Messages are in reverse order (newest first) due to the reversing in stream_mbox_messages
        assert b"Test Message 3" in messages[0]
        assert b"Test Message 2" in messages[1]
        assert b"Test Message 1" in messages[2]

    def test_task_stream_mbox_messages_empty(self):
        """Test streaming an empty MBOX file."""
        file = BytesIO(b"")
        messages = list(stream_mbox_messages(file))
        assert len(messages) == 0

    def test_task_stream_mbox_messages_single_message(self):
        """Test streaming a MBOX file with a single message."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Single Message
From: sender@example.com
To: recipient@example.com

This is a single message.
"""
        file = BytesIO(content)
        messages = list(stream_mbox_messages(file))
        assert len(messages) == 1
        assert b"Single Message" in messages[0]

    def test_task_stream_mbox_messages_malformed(self):
        """Test streaming a malformed MBOX file."""
        # Content without proper From headers
        content = b"""Subject: Malformed Message
From: sender@example.com
To: recipient@example.com

This is a malformed message.
"""
        file = BytesIO(content)
        messages = list(stream_mbox_messages(file))
        assert len(messages) == 0  # No valid messages should be found
