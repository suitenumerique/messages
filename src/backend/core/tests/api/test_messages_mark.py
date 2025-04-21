"""Test marking messages as read/unread."""

# pylint: disable=redefined-outer-name

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core.factories import MessageFactory, ThreadFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def change_read_status_url():
    """Get the url for the read status endpoint."""
    return reverse("change-read-status")


@pytest.fixture
def thread_with_messages(mailbox):
    """Create a thread with multiple messages."""
    thread = ThreadFactory(mailbox=mailbox)
    MessageFactory.create_batch(3, thread=thread)
    return thread


class TestMessageReadStatus:
    """Test marking messages as read/unread."""

    def test_mark_single_message_as_read(
        self, message, change_read_status_url, mailbox_access
    ):
        """Test marking a single message as read."""
        # Ensure message is not read
        assert message.read_at is None

        # Mark message as read
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "message_ids": str(message.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify message is read
        message.refresh_from_db()
        assert message.read_at is not None

        # Verify response data
        assert response.data["updated_messages"] == 1
        assert "read" in response.data["detail"]

        # Verify thread is read
        assert message.thread.is_read is True

    def test_mark_single_message_as_unread(
        self, message, change_read_status_url, mailbox_access
    ):
        """Test marking a single message as unread."""
        # Ensure message is read
        message.read_at = timezone.now()
        message.save()

        # Ensure thread is marked as read
        message.thread.update_read_status()

        # Mark message as unread
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 0, "message_ids": str(message.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Refresh message from database
        message.refresh_from_db()
        assert message.read_at is None

        # Verify response data
        assert response.data["updated_messages"] == 1
        assert "unread" in response.data["detail"]
        # Verify thread is unread
        assert message.thread.is_read is False

    def test_mark_thread_as_read(
        self, thread_with_messages, change_read_status_url, mailbox_access
    ):
        """Test marking an entire thread as read."""
        # Ensure all messages of the thread are unread and the thread is unread
        thread = thread_with_messages
        thread.messages.update(read_at=None)
        thread.update_read_status()
        assert thread.is_read is False

        # Mark thread as read
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "thread_ids": str(thread.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify all messages in thread are marked as read
        assert thread.messages.filter(read_at__isnull=True).count() == 0

        # Verify thread is marked as read
        thread.refresh_from_db()
        assert thread.is_read is True

    def test_mark_thread_as_unread(
        self, thread_with_messages, change_read_status_url, mailbox_access
    ):
        """Test marking an entire thread as unread."""
        # Ensure all messages of the thread are read and the thread is read
        thread = thread_with_messages
        thread.messages.update(read_at=timezone.now())
        thread.update_read_status()
        assert thread.is_read is True

        # Mark thread as unread
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 0, "thread_ids": str(thread.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify all messages in thread are marked as unread
        assert thread.messages.filter(read_at__isnull=False).count() == 0

        # Verify thread is marked as unread
        thread.refresh_from_db()
        assert thread.is_read is False

    def test_mark_multiple_messages(
        self, mailbox, change_read_status_url, mailbox_access
    ):
        """Test marking multiple messages at once."""
        # Create multiple messages using factories
        thread = ThreadFactory(mailbox=mailbox, is_read=False)
        message1 = MessageFactory(thread=thread, read_at=None)
        message2 = MessageFactory(thread=thread, read_at=None)
        message3 = MessageFactory(thread=thread, read_at=None)

        # Mark 2 messages as read
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "message_ids": f"{message1.id},{message2.id}"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_messages"] == 2
        assert response.data["detail"] == "Successfully marked 2 messages as read"

        # Verify only specified messages are marked as read
        message1.refresh_from_db()
        message2.refresh_from_db()
        message3.refresh_from_db()
        assert message1.read_at is not None
        assert message2.read_at is not None
        assert message3.read_at is None
        # Verify thread still unread
        assert thread.is_read is False

        # Mark last message as read
        response = client.post(
            change_read_status_url,
            {"status": 1, "message_ids": str(message3.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_messages"] == 1
        assert response.data["updated_threads"] == 1
        assert response.data["detail"] == "Successfully marked 1 messages as read"
        message3.refresh_from_db()
        assert message3.read_at is not None
        # Verify thread still read now
        thread.refresh_from_db()
        assert thread.is_read is True

    def test_mark_multiple_threads(
        self, mailbox, change_read_status_url, mailbox_access
    ):
        """Test marking multiple threads at once."""
        # Create multiple threads with messages using factories
        thread1 = ThreadFactory(mailbox=mailbox, is_read=False)
        message1 = MessageFactory(thread=thread1, read_at=None)

        thread2 = ThreadFactory(mailbox=mailbox, is_read=False)
        message2 = MessageFactory(thread=thread2, read_at=None)

        thread3 = ThreadFactory(mailbox=mailbox, is_read=False)
        message3 = MessageFactory(thread=thread3, read_at=None)

        # Mark 2 threads as read
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "thread_ids": f"{thread1.id},{thread2.id}"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_threads"] == 2

        # Verify only specified threads are marked as read
        thread1.refresh_from_db()
        thread2.refresh_from_db()
        thread3.refresh_from_db()
        assert thread1.is_read is True
        assert thread2.is_read is True
        assert thread3.is_read is False
        # Verify messages into thread read status
        message1.refresh_from_db()
        message2.refresh_from_db()
        message3.refresh_from_db()
        assert message1.read_at is not None
        assert message2.read_at is not None
        assert message3.read_at is None

    def test_unauthorized_access_message(
        self, message, change_read_status_url, other_user
    ):
        """Test that users without mailbox access cannot mark messages as read/unread."""
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "message_ids": str(message.id)},
            format="json",
        )

        # The endpoint should return 200 but not affect any messages
        # since the user doesn't have access to the mailbox
        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_messages"] == 0

        # Verify message wasn't marked as read
        message.refresh_from_db()
        assert message.read_at is None

    def test_unauthorized_access_thread(
        self, thread_with_messages, change_read_status_url, other_user
    ):
        """Test that users without mailbox access cannot mark threads as read/unread."""
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.post(
            change_read_status_url,
            {"status": 1, "thread_ids": str(thread_with_messages.id)},
            format="json",
        )

        # The endpoint should return 200 but not affect any messages
        # since the user doesn't have access to the mailbox
        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_threads"] == 0
        assert response.data["updated_messages"] == 0

    def test_invalid_status(self, message, change_read_status_url, mailbox_access):
        """Test providing an invalid status value."""
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(
            change_read_status_url,
            {"status": "invalid", "message_ids": str(message.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Status must be 0 or 1" in response.data["detail"]

    def test_missing_ids(self, change_read_status_url, mailbox_access):
        """Test not providing any message or thread IDs."""
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.post(change_read_status_url, {"status": 1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "Either message_ids or thread_ids must be provided"
            in response.data["detail"]
        )
