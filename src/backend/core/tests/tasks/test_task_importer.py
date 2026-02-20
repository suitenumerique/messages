"""Tests for importer tasks."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.files.storage import storages

import pytest

from core import models
from core.factories import MailboxFactory, UserFactory
from core.mda.inbound import deliver_inbound_message
from core.models import Message
from core.services.importer.mbox_tasks import (
    extract_date_from_headers,
    index_mbox_messages,
    process_mbox_file_task,
)


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
    """Create a sample MBOX file content with dates and message IDs.

    Messages are intentionally out of chronological order to test sorting.
    """
    return b"""From user@example.com Thu Jan 3 00:00:00 2024
Message-ID: <msg3@example.com>
Subject: Test Message 3
From: sender3@example.com
To: recipient@example.com
Date: Wed, 3 Jan 2024 00:00:00 +0000

This is test message 3.

From user@example.com Thu Jan 1 00:00:00 2024
Message-ID: <msg1@example.com>
Subject: Test Message 1
From: sender1@example.com
To: recipient@example.com
Date: Mon, 1 Jan 2024 00:00:00 +0000

This is test message 1.

From user@example.com Thu Jan 2 00:00:00 2024
Message-ID: <msg2@example.com>
Subject: Test Message 2
From: sender2@example.com
To: recipient@example.com
Date: Tue, 2 Jan 2024 00:00:00 +0000
In-Reply-To: <msg1@example.com>
References: <msg1@example.com>

This is test message 2.
"""


@pytest.fixture
def mock_task():
    """Create a mock task instance."""
    task = MagicMock()
    task.update_state = MagicMock()
    return task


def _upload_to_s3(content, file_key="test-mbox-key"):
    """Upload content to the message-imports S3 bucket (real MinIO)."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=content,
    )
    return file_key, storage, s3_client


@pytest.mark.django_db
class TestExtractDateFromHeaders:
    """Test the extract_date_from_headers function."""

    def test_extract_valid_date(self):
        """Test extracting a valid RFC5322 date."""
        raw = b"From: a@b.com\r\nDate: Mon, 1 Jan 2024 00:00:00 +0000\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_extract_no_date_header(self):
        """Test message without Date header returns None."""
        raw = b"From: a@b.com\r\nSubject: Test\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is None

    def test_extract_invalid_date(self):
        """Test message with invalid date returns None."""
        raw = b"From: a@b.com\r\nDate: not-a-date\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is None

    def test_extract_date_only_reads_headers(self):
        """Test that only headers are parsed, not body."""
        raw = b"Subject: Test\r\n\r\nDate: Mon, 1 Jan 2024 00:00:00 +0000"
        result = extract_date_from_headers(raw)
        assert result is None  # Date in body should be ignored

    def test_extract_date_lf_only(self):
        """Test with LF-only line endings."""
        raw = b"From: a@b.com\nDate: Tue, 2 Jan 2024 10:00:00 +0000\n\nBody"
        result = extract_date_from_headers(raw)
        assert result is not None
        assert result.day == 2


@pytest.mark.django_db
class TestIndexMboxMessages:
    """Test the index_mbox_messages function."""

    def test_index_basic(self, sample_mbox_content):
        """Test basic indexing of mbox content."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        assert len(indices) == 3

    def test_index_has_dates(self, sample_mbox_content):
        """Test that dates are extracted during indexing."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        # All 3 messages have dates
        for idx in indices:
            assert idx.date is not None

    def test_index_byte_offsets(self, sample_mbox_content):
        """Test that byte offsets allow correct message extraction."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        # Each message should be extractable
        for idx in indices:
            file.seek(idx.start_byte)
            content = file.read(idx.end_byte - idx.start_byte + 1)
            assert b"Subject: " in content

    def test_index_empty_file(self):
        """Test indexing an empty file."""
        file = BytesIO(b"")
        indices = index_mbox_messages(file)
        assert len(indices) == 0

    def test_index_no_from_lines(self):
        """Test indexing content without From separators."""
        file = BytesIO(b"Subject: Test\nFrom: a@b.com\n\nBody\n")
        indices = index_mbox_messages(file)
        assert len(indices) == 0

    def test_index_single_message(self):
        """Test indexing a single message."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Single
From: a@b.com
Date: Mon, 1 Jan 2024 00:00:00 +0000

Body
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)
        assert len(indices) == 1
        assert indices[0].date is not None

    def test_index_message_without_date(self):
        """Test indexing a message without a Date header."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: No Date
From: a@b.com

Body
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)
        assert len(indices) == 1
        assert indices[0].date is None


@pytest.mark.django_db
class TestProcessMboxFileTask:
    """Test suite for process_mbox_file_task."""

    def test_task_process_mbox_file_success(self, mailbox, sample_mbox_content):
        """Test successful MBOX file processing."""
        file_key, storage, s3_client = _upload_to_s3(sample_mbox_content)

        try:
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                task_result = process_mbox_file_task(
                    file_key=file_key, recipient_id=str(mailbox.id)
                )

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

                # 1 "Indexing" + 3 per-message PROGRESS = 4
                assert mock_task.update_state.call_count == 4

                # Verify "Indexing messages" update
                mock_task.update_state.assert_any_call(
                    state="PROGRESS",
                    meta={
                        "result": {
                            "message_status": "Indexing messages",
                            "total_messages": None,
                            "success_count": 0,
                            "failure_count": 0,
                            "type": "mbox",
                            "current_message": 0,
                        },
                        "error": None,
                    },
                )

                # Verify per-message progress
                for i in range(1, 4):
                    mock_task.update_state.assert_any_call(
                        state="PROGRESS",
                        meta={
                            "result": {
                                "message_status": f"Processing message {i} of 3",
                                "total_messages": 3,
                                "success_count": i - 1,
                                "failure_count": 0,
                                "type": "mbox",
                                "current_message": i,
                            },
                            "error": None,
                        },
                    )

                # Verify messages were created in chronological order
                message_count = Message.objects.count()
                assert message_count == 3, f"Expected 3 messages, got {message_count}"
                messages = Message.objects.order_by("created_at")
                # Sorted by date: Jan 1, Jan 2, Jan 3
                assert messages[0].subject == "Test Message 1"
                assert messages[1].subject == "Test Message 2"
                assert messages[2].subject == "Test Message 3"

                # Verify threading: msg2 replies to msg1
                assert messages[1].thread == messages[0].thread
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_task_process_mbox_file_partial_success(self, mailbox, sample_mbox_content):
        """Test MBOX processing with some messages failing."""

        original_deliver = deliver_inbound_message

        def mock_deliver(recipient_email, parsed_email, raw_data, **kwargs):
            subject = parsed_email.get("headers", {}).get("subject", "")
            if subject == "Test Message 2":
                return False
            return original_deliver(recipient_email, parsed_email, raw_data, **kwargs)

        file_key, storage, s3_client = _upload_to_s3(sample_mbox_content)

        try:
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            with (
                patch.object(
                    process_mbox_file_task, "update_state", mock_task.update_state
                ),
                patch(
                    "core.services.importer.mbox_tasks.deliver_inbound_message",
                    side_effect=mock_deliver,
                ),
            ):
                task_result = process_mbox_file_task(file_key, str(mailbox.id))

                assert task_result["status"] == "SUCCESS"
                assert task_result["result"]["total_messages"] == 3
                assert task_result["result"]["success_count"] == 2
                assert task_result["result"]["failure_count"] == 1
                assert task_result["result"]["current_message"] == 3

                # 1 indexing + 3 per-message PROGRESS = 4
                assert mock_task.update_state.call_count == 4

                # Verify messages: msg1 and msg3 created, msg2 failed
                assert Message.objects.count() == 2
                subjects = sorted(Message.objects.values_list("subject", flat=True))
                assert "Test Message 1" in subjects
                assert "Test Message 3" in subjects
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_task_process_mbox_file_mailbox_not_found(self, sample_mbox_content):  # pylint: disable=unused-argument
        """Test MBOX processing with non-existent mailbox."""
        mock_task = MagicMock()
        mock_task.update_state = MagicMock()

        non_existent_id = str(uuid.uuid4())

        with patch.object(
            process_mbox_file_task, "update_state", mock_task.update_state
        ):
            task_result = process_mbox_file_task(
                file_key="test-file-key.mbox", recipient_id=non_existent_id
            )

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

            # No update_state calls — failure status is in the returned dict
            mock_task.update_state.assert_not_called()

            assert Message.objects.count() == 0

    def test_task_process_mbox_file_parse_error(self, mailbox, sample_mbox_content):
        """Test MBOX processing with message parsing error."""

        def mock_parse(*args, **kwargs):
            raise ValidationError("Invalid message format")

        file_key, storage, s3_client = _upload_to_s3(sample_mbox_content)

        try:
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            with (
                patch(
                    "core.services.importer.mbox_tasks.parse_email_message",
                    side_effect=mock_parse,
                ),
                patch.object(
                    process_mbox_file_task, "update_state", mock_task.update_state
                ),
            ):
                task_result = process_mbox_file_task(file_key, str(mailbox.id))

                assert task_result["status"] == "SUCCESS"
                assert task_result["result"]["total_messages"] == 3
                assert task_result["result"]["success_count"] == 0
                assert task_result["result"]["failure_count"] == 3

                # 1 indexing + 3 per-message PROGRESS = 4
                assert mock_task.update_state.call_count == 4

                assert Message.objects.count() == 0
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_task_process_mbox_file_empty(self, mailbox):
        """Test processing an empty MBOX file — returns success with zero messages."""
        file_key, storage, s3_client = _upload_to_s3(b"")

        try:
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                task_result = process_mbox_file_task(
                    file_key=file_key, recipient_id=str(mailbox.id)
                )

                assert task_result["status"] == "SUCCESS"
                assert task_result["result"]["total_messages"] == 0
                assert Message.objects.count() == 0
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_task_process_mbox_invalid_file(self, mailbox):
        """Test processing a non-text file (JPEG) — no messages found, returns success."""
        # JPEG magic bytes
        jpeg_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100

        file_key, storage, s3_client = _upload_to_s3(jpeg_content)

        try:
            mock_task = MagicMock()
            mock_task.update_state = MagicMock()

            with patch.object(
                process_mbox_file_task, "update_state", mock_task.update_state
            ):
                task_result = process_mbox_file_task(
                    file_key=file_key, recipient_id=str(mailbox.id)
                )

                # MIME validation is done upstream in service.py;
                # the task just finds zero messages in invalid content
                assert task_result["status"] == "SUCCESS"
                assert task_result["result"]["total_messages"] == 0
                assert Message.objects.count() == 0
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)
