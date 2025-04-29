"""Test threads delete."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

# Remove APIClient import if not used elsewhere after removing classes
# from rest_framework.test import APIClient
from core import (
    factories,  # Renamed import
    models,  # Keep if models are used in remaining tests
)

pytestmark = pytest.mark.django_db

FLAG_API_URL = reverse("change-flag")

# Removed TestThreadsDelete class
# Removed TestThreadsBulkDelete class


def test_trash_single_thread_success(api_client):
    """Test marking a single thread as trashed successfully via flag endpoint."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = factories.MailboxFactory(users_read=[user])
    thread = factories.ThreadFactory(mailbox=mailbox)
    msg1 = factories.MessageFactory(thread=thread, is_trashed=False)
    msg2 = factories.MessageFactory(thread=thread, is_trashed=False)

    thread.refresh_from_db()
    thread.update_counters()
    assert thread.count_trashed == 0
    assert msg1.is_trashed is False
    assert msg2.is_trashed is False

    data = {"flag": "trashed", "value": "true", "thread_ids": str(thread.id)}
    response = api_client.post(FLAG_API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    # Check that the response indicates update for messages within the thread
    assert response.data["updated_threads"] == 1

    # Verify thread trash counter is updated
    thread.refresh_from_db()
    assert thread.count_trashed == 2

    # Verify all messages in the thread are marked as trashed
    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_trashed is True
    assert msg1.trashed_at is not None
    assert msg2.is_trashed is True
    assert msg2.trashed_at is not None


def test_untrash_single_thread_success(api_client):
    """Test marking a single thread as untrashed successfully via flag endpoint."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = factories.MailboxFactory(users_read=[user])
    thread = factories.ThreadFactory(mailbox=mailbox)
    trashed_time = timezone.now()
    msg1 = factories.MessageFactory(
        thread=thread, is_trashed=True, trashed_at=trashed_time
    )
    msg2 = factories.MessageFactory(
        thread=thread, is_trashed=True, trashed_at=trashed_time
    )

    thread.refresh_from_db()
    thread.update_counters()
    assert thread.count_trashed == 2
    assert msg1.is_trashed is True
    assert msg2.is_trashed is True

    data = {"flag": "trashed", "value": "false", "thread_ids": str(thread.id)}
    response = api_client.post(FLAG_API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    # Verify thread trash counter is updated
    thread.refresh_from_db()
    assert thread.count_trashed == 0

    # Verify all messages in the thread are marked as untrashed
    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_trashed is False
    assert msg1.trashed_at is None
    assert msg2.is_trashed is False
    assert msg2.trashed_at is None


def test_trash_multiple_threads_success(api_client):
    """Test marking multiple threads as trashed successfully."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = factories.MailboxFactory(users_read=[user])
    thread1 = factories.ThreadFactory(mailbox=mailbox)
    factories.MessageFactory(thread=thread1, is_trashed=False)
    thread2 = factories.ThreadFactory(mailbox=mailbox)
    factories.MessageFactory(thread=thread2, is_trashed=False)
    thread3 = factories.ThreadFactory(
        mailbox=mailbox
    )  # Already trashed (should be unaffected by value=true)
    msg3 = factories.MessageFactory(
        thread=thread3, is_trashed=True, trashed_at=timezone.now()
    )

    thread1.refresh_from_db()
    thread1.update_counters()
    thread2.refresh_from_db()
    thread2.update_counters()
    thread3.refresh_from_db()
    thread3.update_counters()
    assert thread1.count_trashed == 0
    assert thread2.count_trashed == 0
    assert thread3.count_trashed == 1

    thread_ids = f"{thread1.id},{thread2.id},{thread3.id}"
    data = {"flag": "trashed", "value": "true", "thread_ids": thread_ids}
    response = api_client.post(FLAG_API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 3  # All 3 threads were targeted

    # Verify counters
    thread1.refresh_from_db()
    thread2.refresh_from_db()
    thread3.refresh_from_db()
    assert thread1.count_trashed == 1
    assert thread2.count_trashed == 1
    assert thread3.count_trashed == 1  # Remains 1

    # Verify messages
    assert thread1.messages.first().is_trashed is True
    assert thread2.messages.first().is_trashed is True
    msg3.refresh_from_db()
    assert msg3.is_trashed is True  # Remained trashed


def test_trash_thread_unauthorized(api_client):
    """Test trashing a thread without authentication."""
    thread = factories.ThreadFactory()
    data = {"flag": "trashed", "value": "true", "thread_ids": str(thread.id)}
    response = api_client.post(FLAG_API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_trash_thread_no_permission(api_client):
    """Test trashing a thread the user doesn't have access to."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)
    other_mailbox = factories.MailboxFactory()  # User does not have access
    thread = factories.ThreadFactory(mailbox=other_mailbox)
    factories.MessageFactory(thread=thread)

    initial_count = models.Thread.objects.count()

    data = {"flag": "trashed", "value": "true", "thread_ids": str(thread.id)}
    response = api_client.post(FLAG_API_URL, data=data, format="json")

    # Should succeed but update nothing
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 0

    # Verify thread and its messages are not marked as trashed
    thread.refresh_from_db()
    assert thread.count_trashed == 0
    assert thread.messages.first().is_trashed is False
    assert (
        models.Thread.objects.count() == initial_count
    )  # Verify thread wasn't deleted


def test_trash_non_existent_thread(api_client):
    """Test trashing a thread that does not exist."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)
    non_existent_uuid = "123e4567-e89b-12d3-a456-426614174000"

    data = {"flag": "trashed", "value": "true", "thread_ids": non_existent_uuid}
    response = api_client.post(FLAG_API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 0
