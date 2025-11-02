"""Tests for the ThreadEvent API endpoints."""

import uuid

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def get_thread_event_url(thread_id, event_id=None):
    """Helper function to get the thread event URL."""
    if event_id:
        return reverse(
            "thread-event-detail", kwargs={"thread_id": thread_id, "id": event_id}
        )
    return reverse("thread-event-list", kwargs={"thread_id": thread_id})


@pytest.fixture(name="mailbox_with_access")
def fixture_mailbox_with_access():
    """Create a mailbox with access for a user."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    return user, mailbox


@pytest.fixture(name="thread_with_access")
def fixture_thread_with_access(mailbox_with_access):
    """Create a thread with access for a mailbox."""
    user, mailbox = mailbox_with_access
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.VIEWER,
    )
    return user, mailbox, thread


class TestThreadEventList:
    """Test the GET /threads/{thread_id}/events/ endpoint."""

    def test_list_thread_events_success(
        self, api_client, thread_with_access, django_assert_num_queries
    ):
        """Test listing thread events of a thread."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        # Create some events for the thread
        factories.ThreadEventFactory.create_batch(5, thread=thread, type="notification")
        factories.ThreadEventFactory.create_batch(
            3, thread=thread, type="arbitrary_block"
        )

        # Create events for other threads
        other_thread = factories.ThreadFactory()
        factories.ThreadEventFactory.create_batch(2, thread=other_thread)

        with django_assert_num_queries(3):
            response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 8
        assert response.data["results"][0]["thread"] == str(thread.id)

    def test_list_thread_events_ordered_by_created_at(
        self, api_client, thread_with_access
    ):
        """Test that thread events are ordered by created_at."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        event1 = factories.ThreadEventFactory(thread=thread)
        event2 = factories.ThreadEventFactory(thread=thread)
        event3 = factories.ThreadEventFactory(thread=thread)

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 3
        # Events should be ordered by created_at (oldest first)
        assert results[0]["id"] == str(event1.id)
        assert results[1]["id"] == str(event2.id)
        assert results[2]["id"] == str(event3.id)

    def test_list_thread_events_forbidden(self, api_client):
        """Test listing thread events without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        # Create a thread that the user doesn't have access to
        thread = factories.ThreadFactory()
        factories.ThreadEventFactory(thread=thread)

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_thread_events_unauthorized(self, api_client):
        """Test listing thread events without authentication."""
        thread = factories.ThreadFactory()
        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventRetrieve:
    """Test the GET /threads/{thread_id}/events/{id}/ endpoint."""

    def test_retrieve_thread_event_success(self, api_client, thread_with_access):
        """Test retrieving a single thread event."""
        user, mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        channel = factories.ChannelFactory(mailbox=mailbox)
        event = factories.ThreadEventFactory(
            thread=thread, type="action_button", channel=channel
        )

        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(event.id)
        assert response.data["thread"] == str(thread.id)
        assert response.data["type"] == "action_button"
        assert response.data["channel"] == str(channel.id)

    def test_retrieve_thread_event_forbidden(self, api_client):
        """Test retrieving a thread event without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_retrieve_thread_event_not_found(self, api_client, thread_with_access):
        """Test retrieving a non-existent thread event."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_event_url(thread.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_thread_event_unauthorized(self, api_client):
        """Test retrieving a thread event without authentication."""
        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventCreate:
    """Test the POST /threads/{thread_id}/events/ endpoint."""

    def test_create_thread_event_success(self, api_client, thread_with_access):
        """Test creating a thread event successfully."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        data = {
            "type": "notification",
            "data": {"message": "X read message Y", "user_id": "123"},
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["thread"] == str(thread.id)
        assert response.data["type"] == "notification"
        # channel should be None since it's not set via header in regular API
        assert response.data["channel"] is None
        assert response.data["data"] == data["data"]

        # Verify the event was created in the database
        event = models.ThreadEvent.objects.get(id=response.data["id"])
        assert event.thread == thread
        assert event.type == "notification"
        assert event.channel is None

    @pytest.mark.parametrize(
        "event_type",
        ["notification", "arbitrary_block", "action_button", "custom_type"],
    )
    def test_create_thread_event_all_types(
        self, api_client, thread_with_access, event_type
    ):
        """Test creating thread events with different event types."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        data = {"type": event_type, "data": {"test": "data"}}

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["type"] == event_type
        # channel should be None since it's not set via header in regular API
        assert response.data["channel"] is None

    def test_create_thread_event_with_complex_data(
        self, api_client, thread_with_access
    ):
        """Test creating a thread event with complex JSON data."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        complex_data = {
            "type": "arbitrary_block",
            "data": {
                "type": "iframe",
                "src": "https://example.com/widget",
                "width": "100%",
                "height": "400px",
                "config": {"theme": "dark", "language": "en"},
            },
        }

        response = api_client.post(
            get_thread_event_url(thread.id), complex_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["data"] == complex_data["data"]
        # channel should be None since it's not set via header in regular API
        assert response.data["channel"] is None

    def test_create_thread_event_forbidden(self, api_client):
        """Test creating a thread event without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        data = {"type": "notification", "data": {}}

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_thread_event_type_too_long(self, api_client, thread_with_access):
        """Test creating a thread event with type that exceeds max length."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        data = {"type": "a" * 37, "data": {}}  # 37 chars, max is 36

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_thread_event_unauthorized(self, api_client):
        """Test creating a thread event without authentication."""
        thread = factories.ThreadFactory()
        data = {"type": "notification", "data": {}}

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventUpdate:
    """Test the PUT/PATCH /threads/{thread_id}/events/{id}/ endpoint."""

    def test_update_thread_event_success(self, api_client, thread_with_access):
        """Test updating a thread event successfully."""
        user, mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        channel = factories.ChannelFactory(mailbox=mailbox)
        event = factories.ThreadEventFactory(
            thread=thread, type="notification", channel=channel
        )
        original_channel_id = str(channel.id)

        data = {
            "data": {"label": "Approve", "action": "approve"},
        }

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        # type, thread, and channel should remain unchanged (read-only)
        assert response.data["type"] == "notification"
        assert response.data["thread"] == str(thread.id)
        assert response.data["channel"] == original_channel_id
        assert response.data["data"] == data["data"]

    def test_update_thread_event_partial(self, api_client, thread_with_access):
        """Test partially updating a thread event."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(
            thread=thread,
            type="notification",
            data={"old": "data"},
        )

        data = {"data": {"new": "data"}}

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["data"] == {"new": "data"}

    def test_update_thread_event_readonly_fields(self, api_client, thread_with_access):
        """Test that thread, type, and channel cannot be updated (read-only fields should return errors)."""
        user, mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        channel = factories.ChannelFactory(mailbox=mailbox)
        event = factories.ThreadEventFactory(
            thread=thread, type="notification", channel=channel
        )

        # Try to update thread - should return validation error
        different_thread = factories.ThreadFactory()
        data = {
            "thread": str(different_thread.id),
            "data": {"test": "data"},
        }
        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "thread" in response.data
        assert "cannot be updated" in str(response.data["thread"][0]).lower()

        # Try to update channel - should return validation error
        different_channel = factories.ChannelFactory(mailbox=mailbox)
        data = {
            "channel": str(different_channel.id),
            "data": {"test": "data"},
        }
        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "channel" in response.data
        assert "cannot be updated" in str(response.data["channel"][0]).lower()

        # Try to update type - should return validation error
        data = {
            "type": "action_button",
            "data": {"test": "data"},
        }
        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "type" in response.data or "Cannot change" in str(response.data)

        # Verify original values remain unchanged in database
        event.refresh_from_db()
        assert event.thread.id == thread.id
        assert event.type == "notification"
        assert event.channel.id == channel.id

    def test_update_thread_event_forbidden(self, api_client):
        """Test updating a thread event without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        data = {"data": {"new": "data"}}
        response = api_client.patch(
            get_thread_event_url(thread.id, event.id), data, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_thread_event_unauthorized(self, api_client):
        """Test updating a thread event without authentication."""
        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.patch(get_thread_event_url(thread.id, event.id), {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventDelete:
    """Test the DELETE /threads/{thread_id}/events/{id}/ endpoint."""

    def test_delete_thread_event_success(self, api_client, mailbox_with_access):
        """Test deleting a thread event successfully."""
        user, mailbox = mailbox_with_access
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify the event was deleted
        assert not models.ThreadEvent.objects.filter(id=event.id).exists()

    def test_delete_thread_event_forbidden(self, api_client):
        """Test deleting a thread event without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_thread_event_not_found(self, api_client, thread_with_access):
        """Test deleting a non-existent thread event."""
        user, _mailbox, thread = thread_with_access
        api_client.force_authenticate(user=user)

        response = api_client.delete(get_thread_event_url(thread.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_thread_event_unauthorized(self, api_client):
        """Test deleting a thread event without authentication."""
        thread = factories.ThreadFactory()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
