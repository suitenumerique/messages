"""Tests for thread filtering functionality."""
# pylint: disable=redefined-outer-name, unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def url():
    """Return the URL for the threads list endpoint."""
    return reverse("threads-list")


@pytest.fixture
def setup_threads_with_labels(api_client):
    """Set up test data with threads and labels."""
    user = factories.UserFactory()
    api_client.force_authenticate(user=user)

    # Create mailboxes
    mailbox1 = factories.MailboxFactory(users_read=[user])
    mailbox2 = factories.MailboxFactory(users_read=[user])

    # Create labels
    label1 = factories.LabelFactory(mailbox=mailbox1, name="Important")
    label2 = factories.LabelFactory(mailbox=mailbox1, name="Work")
    label3 = factories.LabelFactory(mailbox=mailbox2, name="Personal")

    # Create threads with different label combinations
    thread1 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    thread1.labels.add(label1)  # Thread1 has label1

    thread2 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    thread2.labels.add(label1, label2)  # Thread2 has both label1 and label2

    thread3 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox2,
        thread=thread3,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    thread3.labels.add(label3)  # Thread3 has label3

    thread4 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread4,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )  # Thread4 has no labels

    return {
        "user": user,
        "mailbox1": mailbox1,
        "mailbox2": mailbox2,
        "label1": label1,
        "label2": label2,
        "label3": label3,
        "thread1": thread1,
        "thread2": thread2,
        "thread3": thread3,
        "thread4": thread4,
    }


class TestThreadFilterLabel:
    """Test filtering threads by a single label."""

    def test_filter_threads_by_label(self, api_client, url, setup_threads_with_labels):
        """Test filtering threads by a single label."""
        data = setup_threads_with_labels

        # Test filtering by label1
        response = api_client.get(url, {"label_slug": str(data["label1"].slug)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2  # thread1 and thread2 have label1
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(data["thread1"].id) in thread_ids
        assert str(data["thread2"].id) in thread_ids
        assert str(data["thread3"].id) not in thread_ids
        assert str(data["thread4"].id) not in thread_ids

        # Test filtering by label2
        response = api_client.get(url, {"label_slug": str(data["label2"].slug)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # only thread2 has label2
        assert response.data["results"][0]["id"] == str(data["thread2"].id)

        # Test filtering by label3
        response = api_client.get(url, {"label_slug": str(data["label3"].slug)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # only thread3 has label3
        assert response.data["results"][0]["id"] == str(data["thread3"].id)

    def test_filter_threads_by_label_and_mailbox(
        self, api_client, url, setup_threads_with_labels
    ):
        """Test filtering threads by both label and mailbox."""
        data = setup_threads_with_labels

        # Test filtering by label1 in mailbox1
        response = api_client.get(
            url,
            {
                "label_slug": str(data["label1"].slug),
                "mailbox_id": str(data["mailbox1"].id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert (
            response.data["count"] == 2
        )  # thread1 and thread2 have label1 in mailbox1
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(data["thread1"].id) in thread_ids
        assert str(data["thread2"].id) in thread_ids

        # Test filtering by label1 in mailbox2 (should return empty)
        response = api_client.get(
            url,
            {
                "label_slug": str(data["label1"].slug),
                "mailbox_id": str(data["mailbox2"].id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_filter_threads_by_invalid_label(
        self, api_client, url, setup_threads_with_labels
    ):
        """Test filtering threads with an invalid label ID."""
        # Test with non-existent label ID
        response = api_client.get(
            url, {"label_slug": "00000000-0000-0000-0000-000000000000"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_filter_threads_by_label_no_access(self, api_client, url):
        """Test filtering threads by a label the user doesn't have access to."""
        # Create a user and a mailbox they don't have access to
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mailbox = factories.MailboxFactory()
        label = factories.LabelFactory(mailbox=mailbox)

        # Try to filter by label in mailbox user doesn't have access to
        response = api_client.get(url, {"label_slug": str(label.slug)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_filter_threads_by_label_combined_filters(
        self, api_client, url, setup_threads_with_labels
    ):
        """Test filtering threads by label combined with other filters."""
        data = setup_threads_with_labels

        # Add some messages to make threads unread/starred
        factories.MessageFactory(thread=data["thread1"])
        factories.MessageFactory(thread=data["thread2"], is_trashed=True)
        data["thread1"].update_stats()
        data["thread2"].update_stats()

        # Test filtering by label1 and has_active
        response = api_client.get(
            url,
            {
                "label_slug": data["label1"].slug,
                "has_active": "1",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # only thread1 has label1 and is not trashed
        assert response.data["results"][0]["id"] == str(data["thread1"].id)

        # Test filtering by label1 and has no active
        response = api_client.get(
            url,
            {
                "label_slug": str(data["label1"].slug),
                "has_active": "0",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # only thread2 has label1 and is not active
        assert response.data["results"][0]["id"] == str(data["thread2"].id)

        # Test filtering by label1, mailbox, and has_active
        response = api_client.get(
            url,
            {
                "label_slug": str(data["label1"].slug),
                "mailbox_id": str(data["mailbox1"].id),
                "has_active": "1",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == str(data["thread1"].id)
