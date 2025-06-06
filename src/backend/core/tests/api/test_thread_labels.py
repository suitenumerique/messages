"""Tests for label functionality in thread responses."""
# pylint: disable=redefined-outer-name, unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a mailbox with user access."""
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.EDITOR,
    )
    return mailbox


@pytest.fixture
def thread(mailbox):
    """Create a thread with mailbox access and a message."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    # Add a message to the thread
    factories.MessageFactory(thread=thread)
    thread.update_stats()
    return thread


@pytest.fixture
def label(mailbox):
    """Create a label in the mailbox."""
    return factories.LabelFactory(mailbox=mailbox)


def test_thread_includes_labels(api_client, user, thread, label, mailbox):
    """Test that thread responses include labels."""
    # Add 2 labels to the thread
    thread.labels.add(label)
    thread.labels.add(factories.LabelFactory(mailbox=mailbox))

    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("threads-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1  # We should have exactly one thread
    thread_data = response.data["results"][0]  # Get the first (and only) thread
    assert "labels" in thread_data
    assert len(thread_data["labels"]) == 2
    label_data = thread_data["labels"][0]
    assert label_data["id"] == str(label.id)
    assert label_data["name"] == label.name
    assert label_data["slug"] == label.slug
    assert label_data["color"] == label.color


def test_thread_labels_filtered_by_access(api_client, user, thread, mailbox):
    """Test that thread responses only include labels from mailboxes the user has access to."""
    # Create a label in a mailbox the user has access to
    accessible_label = factories.LabelFactory(mailbox=mailbox)

    # Create a label in a mailbox the user doesn't have access to
    other_mailbox = factories.MailboxFactory()
    inaccessible_label = factories.LabelFactory(mailbox=other_mailbox)

    # Add both labels to the thread
    thread.labels.add(accessible_label, inaccessible_label)

    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("threads-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1  # We should have exactly one thread
    thread_data = response.data["results"][0]  # Get the first (and only) thread
    assert "labels" in thread_data
    assert len(thread_data["labels"]) == 1
    assert thread_data["labels"][0]["id"] == str(accessible_label.id)


def test_thread_labels_empty_when_no_labels(api_client, user, thread):
    """Test that thread responses include an empty labels list when the thread has no labels."""
    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("threads-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1  # We should have exactly one thread
    thread_data = response.data["results"][0]  # Get the first (and only) thread
    assert "labels" in thread_data
    assert thread_data["labels"] == []


def test_thread_labels_updated_after_label_changes(api_client, user, thread, label):
    """Test that thread responses reflect label changes."""
    # Add the label to the thread
    thread.labels.add(label)

    api_client.force_authenticate(user=user)

    # Check initial state
    response = api_client.get(reverse("threads-list"))
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1  # We should have exactly one thread
    thread_data = response.data["results"][0]  # Get the first (and only) thread
    assert len(thread_data["labels"]) == 1

    # Remove the label
    thread.labels.remove(label)

    # Check updated state
    response = api_client.get(reverse("threads-list"))
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1  # We should have exactly one thread
    thread_data = response.data["results"][0]  # Get the first (and only) thread
    assert thread_data["labels"] == []


def test_thread_labels_in_detail_view(api_client, user, thread, label):
    """Test that labels are included in thread detail view."""
    # Add the label to the thread
    thread.labels.add(label)

    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("threads-detail", args=[thread.id]))

    assert response.status_code == status.HTTP_200_OK
    assert "labels" in response.data
    assert len(response.data["labels"]) == 1
    label_data = response.data["labels"][0]
    assert label_data["id"] == str(label.id)
    assert label_data["name"] == label.name
    assert label_data["slug"] == label.slug
    assert label_data["color"] == label.color
