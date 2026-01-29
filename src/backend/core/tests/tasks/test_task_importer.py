"""Tests for importer tasks."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

import logging
import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError

import pytest

from core import models
from core.factories import MailboxFactory, UserFactory
from core.mda.inbound import deliver_inbound_message
from core.models import Message, Thread
from core.services.importer.tasks import (
    extract_date_from_headers,
    index_mbox_messages,
    process_mbox_file_task,
)

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
    """Create a sample MBOX file content with a threaded conversation.

    The messages form a thread:
    - Message 1: Original message
    - Message 2: Reply to Message 1
    - Message 3: Reply to Message 2

    Messages are voluntarily not in chronological order to test the sorting logic.
    """
    return b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Test Message 1
From: sender1@example.com
To: recipient@example.com
Message-ID: <msg1@example.com>
Date: Thu, 1 Jan 2024 00:00:00 +0000

This is test message 1.

From user@example.com Thu Jan 1 00:00:02 2024
Subject: Re: Re: Test Message 1
From: sender3@example.com
To: recipient@example.com
Message-ID: <msg3@example.com>
In-Reply-To: <msg2@example.com>
References: <msg1@example.com> <msg2@example.com>
Date: Thu, 1 Jan 2024 00:00:02 +0000

This is test message 3, a reply to message 2.

From user@example.com Thu Jan 1 00:00:01 2024
Subject: Re: Test Message 1
From: sender2@example.com
To: recipient@example.com
Message-ID: <msg2@example.com>
In-Reply-To: <msg1@example.com>
References: <msg1@example.com>
Date: Thu, 1 Jan 2024 00:00:01 +0000

This is test message 2, a reply to message 1.
"""


@pytest.fixture
def mock_task():
    """Create a mock task instance."""
    task = MagicMock()
    task.update_state = MagicMock()
    return task


def mock_s3_streaming_body(content: bytes):
    """Helper to create a mock that returns a streaming body with the given content.

    This mocks the get_s3_streaming_body function to return a BytesIO object
    that simulates an S3 StreamingBody.
    """
    return BytesIO(content)


@pytest.mark.django_db
class TestProcessMboxFileTask:
    """Test suite for process_mbox_file_task."""

    def test_task_process_mbox_file_success(self, mailbox, sample_mbox_content):
        """Test successful MBOX file processing with 2-pass approach."""
        # Mock deliver_inbound_message to always succeed
        with patch("core.mda.inbound.deliver_inbound_message", return_value=True):
            # Create a mock task instance
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            # Mock get_s3_byte_range to return message content based on byte range
            def mock_byte_range(storage, file_key, start, end):
                return sample_mbox_content[start : end + 1]

            with (
                patch.object(
                    process_mbox_file_task, "update_state", mock_task.update_state
                ),
                patch(
                    "core.services.importer.tasks.get_s3_streaming_body",
                    return_value=mock_s3_streaming_body(sample_mbox_content),
                ),
                patch(
                    "core.services.importer.tasks.get_s3_byte_range",
                    side_effect=mock_byte_range,
                ),
            ):
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

                # Verify progress updates:
                # 1 PROGRESS (indexing) + 1 PROGRESS (after indexing) + 3 PROGRESS (per message) + 1 SUCCESS
                assert mock_task.update_state.call_count == 6

                # First message (total_messages is now known after indexing)
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Indexing messages",
                            "type": "mbox",
                        },
                        "error": None,
                    },
                )

                # First message (total_messages is now known after indexing)
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Processing message 1/3",
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
                            "message_status": "Processing message 2/3",
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
                            "message_status": "Processing message 3/3",
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
                assert messages[0].subject == "Test Message 1"
                assert messages[1].subject == "Re: Test Message 1"
                assert messages[2].subject == "Re: Re: Test Message 1"

                # Verify all messages are in the same thread (threaded conversation)
                thread_count = Thread.objects.count()
                assert thread_count == 1, f"Expected 1 thread, got {thread_count}"

                thread = Thread.objects.first()
                assert thread.messages.count() == 3, (
                    f"Expected 3 messages in thread, got {thread.messages.count()}"
                )

                # Verify thread subject matches the original message and messaged_at is the latest message
                assert thread.subject == "Test Message 1"
                assert thread.messaged_at == messages[2].created_at

    def test_task_process_mbox_file_partial_success(self, mailbox, sample_mbox_content):
        """Test MBOX processing with some messages failing."""

        # Track call count to fail on the second message
        call_count = [0]

        def mock_deliver(recipient_email, parsed_email, raw_data, **kwargs):
            call_count[0] += 1
            # Fail on the second message
            if call_count[0] == 2:
                return False
            # For other messages, call the original function
            return deliver_inbound_message(
                recipient_email, parsed_email, raw_data, **kwargs
            )

        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Mock get_s3_byte_range to return message content based on byte range
        def mock_byte_range(storage, file_key, start, end):
            return sample_mbox_content[start : end + 1]

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.get_s3_streaming_body",
                return_value=mock_s3_streaming_body(sample_mbox_content),
            ),
            patch(
                "core.services.importer.tasks.get_s3_byte_range",
                side_effect=mock_byte_range,
            ),
            patch(
                "core.services.importer.tasks.deliver_inbound_message",
                side_effect=mock_deliver,
            ),
        ):
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
            # 1 PROGRESS (indexing) + 1 PROGRESS (after indexing) + 3 PROGRESS (per message) + 1 SUCCESS
            assert mock_task.update_state.call_count == 6

            # First message (success)
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 1/3",
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
                        "message_status": "Processing message 2/3",
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
                        "message_status": "Processing message 3/3",
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

            # Verify messages were created (now in chronological order)
            # Message 1 and 3 succeeded, message 2 failed
            assert Message.objects.count() == 2
            messages = Message.objects.order_by("created_at")
            assert messages[0].subject == "Test Message 1"
            assert messages[1].subject == "Re: Re: Test Message 1"  # Message 3

    def test_task_process_mbox_file_mailbox_not_found(self, sample_mbox_content):
        """Test MBOX processing with non-existent mailbox."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        # Use a valid UUID that doesn't exist in the database
        non_existent_id = str(uuid.uuid4())

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.get_s3_streaming_body",
                return_value=mock_s3_streaming_body(sample_mbox_content),
            ),
        ):
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

        # Mock get_s3_byte_range to return message content based on byte range
        def mock_byte_range(storage, file_key, start, end):
            return sample_mbox_content[start : end + 1]

        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        with (
            patch(
                "core.services.importer.tasks.parse_email_message",
                side_effect=mock_parse,
            ),
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.get_s3_streaming_body",
                return_value=mock_s3_streaming_body(sample_mbox_content),
            ),
            patch(
                "core.services.importer.tasks.get_s3_byte_range",
                side_effect=mock_byte_range,
            ),
        ):
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
            # 1 PROGRESS (indexing) + 1 PROGRESS (after indexing) + 3 PROGRESS (per message) + 1 SUCCESS
            assert mock_task.update_state.call_count == 6

            # The first message update should be with failure_count 0
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 1/3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 0,  # No failures yet
                        "type": "mbox",
                        "current_message": 1,
                    },
                    "error": None,
                },
            )

            # The second message update should be with failure_count 1
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 2/3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": 1,  # One failure from message 1
                        "type": "mbox",
                        "current_message": 2,
                    },
                    "error": None,
                },
            )

            # The third message update should be with failure_count 2
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Processing message 3/3",
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
        """Test processing an empty MBOX file (valid MIME type but no messages)."""
        # Create a mock task instance
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.get_s3_streaming_body",
                return_value=mock_s3_streaming_body(b""),
            ),
            patch("magic.from_buffer", return_value="application/mbox"),
        ):
            # Run the task with empty content
            task_result = process_mbox_file_task(
                file_key="test-file-key.mbox", recipient_id=str(mailbox.id)
            )

            # Verify task result
            assert task_result["status"] == "SUCCESS"
            assert (
                task_result["result"]["message_status"] == "No messages found in file"
            )
            assert task_result["result"]["type"] == "mbox"
            assert task_result["result"]["total_messages"] == 0
            assert task_result["result"]["success_count"] == 0
            assert task_result["result"]["failure_count"] == 0
            assert task_result["result"]["current_message"] == 0

            # Verify 2 updates were called: 1 PROGRESS (indexing) + 1 SUCCESS
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

        with (
            patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ),
            patch(
                "core.services.importer.tasks.get_s3_streaming_body",
                return_value=mock_s3_streaming_body(b""),
            ),
        ):
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

            # Verify 2 updates were called: 1 PROGRESS (initializing) + 1 FAILURE
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


class TestExtractDateFromHeaders:
    """Test the extract_date_from_headers function."""

    def test_extract_date_basic(self):
        """Test extracting date from standard email headers."""
        headers = b"""Subject: Test Message
From: sender@example.com
To: recipient@example.com
Date: Thu, 1 Jan 2024 12:00:00 +0000

Body content"""
        result = extract_date_from_headers(headers)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 12
        assert result.tzinfo is not None

    def test_extract_date_with_timezone(self):
        """Test extracting date with timezone offset."""
        headers = b"""Date: Mon, 26 May 2025 20:13:44 +0200
Subject: Test

Body"""
        result = extract_date_from_headers(headers)
        assert result is not None
        # Should be converted to UTC-aware datetime
        assert result.tzinfo is not None

    def test_extract_date_naive_becomes_utc(self):
        """Test that naive datetimes become UTC-aware."""
        # Some malformed dates might parse as naive
        headers = b"""Date: 01 Jan 2024 12:00:00
Subject: Test

Body"""
        result = extract_date_from_headers(headers)
        if result:
            # If parsed successfully, it should be timezone-aware
            assert result.tzinfo is not None

    def test_extract_date_no_date_header(self):
        """Test handling of message without Date header."""
        headers = b"""Subject: No Date
From: sender@example.com
To: recipient@example.com

Body"""
        result = extract_date_from_headers(headers)
        assert result is None

    def test_extract_date_invalid_date(self):
        """Test handling of invalid date format."""
        headers = b"""Date: not a valid date
Subject: Test

Body"""
        result = extract_date_from_headers(headers)
        assert result is None

    def test_extract_date_empty_content(self):
        """Test handling of empty content."""
        result = extract_date_from_headers(b"")
        assert result is None

    def test_extract_date_headers_only(self):
        """Test extraction with headers that end with proper separator."""
        headers = b"""Date: Thu, 1 Jan 2024 10:00:00 +0000
Subject: Test

"""
        result = extract_date_from_headers(headers)
        assert result is not None
        assert result.year == 2024


class TestIndexMboxMessages:
    """Test the index_mbox_messages function."""

    def test_index_single_message(self):
        """Test indexing a single message."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Single Message
From: sender@example.com
To: recipient@example.com
Date: Thu, 1 Jan 2024 10:00:00 +0000

This is a single message.
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)

        assert len(indices) == 1
        assert indices[0].start_byte > 0  # After "From " line
        assert indices[0].end_byte > indices[0].start_byte
        assert indices[0].date is not None
        assert indices[0].date.year == 2024

    def test_index_multiple_messages(self):
        """Test indexing multiple messages."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Message 1
From: sender1@example.com
Date: Thu, 1 Jan 2024 10:00:00 +0000

Body 1

From user@example.com Thu Jan 1 00:00:01 2024
Subject: Message 2
From: sender2@example.com
Date: Thu, 1 Jan 2024 11:00:00 +0000

Body 2

From user@example.com Thu Jan 1 00:00:02 2024
Subject: Message 3
From: sender3@example.com
Date: Thu, 1 Jan 2024 12:00:00 +0000

Body 3
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)

        assert len(indices) == 3
        # All indices should have valid byte ranges
        for idx in indices:
            assert idx.start_byte >= 0
            assert idx.end_byte >= idx.start_byte
            assert idx.date is not None

        # Verify indices are in file order (not sorted by date yet)
        assert indices[0].date.hour == 10
        assert indices[1].date.hour == 11
        assert indices[2].date.hour == 12

    def test_index_messages_with_initial_buffer(self):
        """Test indexing with initial buffer (simulating MIME detection)."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Message 1
Date: Thu, 1 Jan 2024 10:00:00 +0000

Body 1

From user@example.com Thu Jan 1 00:00:01 2024
Subject: Message 2
Date: Thu, 1 Jan 2024 11:00:00 +0000

Body 2
"""
        # Simulate reading first 50 bytes for MIME detection
        initial_bytes = content[:50]
        remaining = content[50:]

        file = BytesIO(remaining)
        indices = index_mbox_messages(
            file, initial_buffer=initial_bytes, initial_offset=0
        )

        assert len(indices) == 2

    def test_index_empty_file(self):
        """Test indexing an empty file."""
        file = BytesIO(b"")
        indices = index_mbox_messages(file)
        assert len(indices) == 0

    def test_index_messages_without_dates(self):
        """Test indexing messages that don't have Date headers."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: No Date Message
From: sender@example.com

Body without date
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)

        assert len(indices) == 1
        assert indices[0].date is None  # No date should be extracted

    def test_index_verifies_byte_ranges(self):
        """Test that byte ranges correctly point to message content."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Test Message
Date: Thu, 1 Jan 2024 10:00:00 +0000

Message body content here.
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)

        assert len(indices) == 1
        # Extract content using the byte range
        extracted = content[indices[0].start_byte : indices[0].end_byte + 1]
        # Should contain the headers and body but NOT the "From " line
        assert b"Subject: Test Message" in extracted
        assert b"Message body content here." in extracted
        assert not extracted.startswith(b"From ")
