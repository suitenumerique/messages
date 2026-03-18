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


def setup_user_with_thread_access(role=enums.ThreadAccessRoleChoices.EDITOR):
    """Create a user with mailbox access and thread access."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=role,
    )
    return user, mailbox, thread


class TestThreadEventList:
    """Test the GET /threads/{thread_id}/events/ endpoint."""

    def test_list_thread_events_success(self, api_client):
        """Test listing thread events of a thread."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create some events for this thread
        factories.ThreadEventFactory.create_batch(3, thread=thread, author=user)
        # Create events for another thread (should not appear)
        factories.ThreadEventFactory.create_batch(2)

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

    def test_list_thread_events_viewer_access(self, api_client):
        """Test listing thread events with viewer access succeeds."""
        user, _mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.VIEWER
        )
        api_client.force_authenticate(user=user)

        factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_list_thread_events_forbidden(self, api_client):
        """Test listing thread events without thread access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_thread_events_unauthorized(self, api_client):
        """Test listing thread events without authentication."""
        thread = factories.ThreadFactory()
        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventCreate:
    """Test the POST /threads/{thread_id}/events/ endpoint."""

    def test_create_thread_event_im_success(self, api_client):
        """Test creating an IM thread event successfully."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "im",
            "data": {"content": "This is an internal comment."},
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["type"] == "im"
        assert response.data["data"]["content"] == "This is an internal comment."
        assert response.data["author"]["id"] == str(user.id)
        assert response.data["thread"] == thread.id

    def test_create_thread_event_with_invalid_type(self, api_client):
        """
        Test creating a thread event with invalid type.
        Should be forbidden, if type is not a valid choice.
        """
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "notification",
            "data": {"content": "Status changed", "status": "resolved"},
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["type"][0].code == "invalid_choice"
        assert str(response.data["type"][0]) == '"notification" is not a valid choice.'

    def test_create_thread_event_forbidden(self, api_client):
        """Test creating a thread event without thread access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        data = {"type": "im", "data": {"content": "test"}}

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_thread_event_unauthorized(self, api_client):
        """Test creating a thread event without authentication."""
        thread = factories.ThreadFactory()
        response = api_client.post(get_thread_event_url(thread.id), {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_thread_event_thread_from_url(self, api_client):
        """Test that thread is always set from URL, not request body."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_thread = factories.ThreadFactory()
        data = {
            "type": "im",
            "data": {"content": "test"},
            "thread": str(other_thread.id),
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        # Thread should be from URL, not body
        assert response.data["thread"] == thread.id


class TestThreadEventRetrieve:
    """Test the GET /threads/{thread_id}/events/{id}/ endpoint."""

    def test_retrieve_thread_event_success(self, api_client):
        """Test retrieving a thread event."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(event.id)
        assert response.data["type"] == event.type
        assert response.data["data"] == event.data

    def test_retrieve_thread_event_forbidden(self, api_client):
        """Test retrieving a thread event without access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory()
        response = api_client.get(get_thread_event_url(event.thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_retrieve_thread_event_not_found(self, api_client):
        """Test retrieving a non-existent thread event."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_event_url(thread.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestThreadEventUpdate:
    """Test the PATCH /threads/{thread_id}/events/{id}/ endpoint."""

    def test_update_thread_event_data(self, api_client):
        """Test updating thread event data."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "Updated comment"}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["data"] == {"content": "Updated comment"}

    def test_update_thread_event_type_readonly_on_update(self, api_client):
        """Test that type is read-only on update (create-only field)."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user, type="im")

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"type": "notification"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        # Type should not change (create-only)
        event.refresh_from_db()
        assert event.type == "im"


class TestThreadEventDelete:
    """Test the DELETE /threads/{thread_id}/events/{id}/ endpoint."""

    def test_delete_thread_event_success(self, api_client):
        """Test deleting a thread event."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.ThreadEvent.objects.filter(id=event.id).exists()

    def test_delete_thread_event_forbidden(self, api_client):
        """Test deleting a thread event without access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory()
        response = api_client.delete(get_thread_event_url(event.thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_thread_event_unauthorized(self, api_client):
        """Test deleting a thread event without authentication."""
        event = factories.ThreadEventFactory()
        response = api_client.delete(get_thread_event_url(event.thread.id, event.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadEventDataValidation:
    """Test that the data field is validated against the JSON schema for each event type."""

    def test_create_im_event_missing_content(self, api_client):
        """IM events require a 'content' key in data."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {"type": "im", "data": {}}
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.data

    def test_create_im_event_with_valid_mentions(self, api_client):
        """IM events should accept a valid mentions array."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        data = {
            "type": "im",
            "data": {
                "content": "Hey @[John]",
                "mentions": [{"id": str(other_user.id), "name": "John"}],
            },
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["data"]["mentions"][0]["id"] == str(other_user.id)

    def test_create_im_event_with_invalid_mention_shape(self, api_client):
        """IM events must reject mentions with missing required fields."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "im",
            "data": {
                "content": "Hey @[John]",
                "mentions": [{"name": "John"}],  # missing 'id'
            },
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.data

    def test_create_im_event_rejects_extra_fields(self, api_client):
        """IM events must reject unexpected fields in data (additionalProperties: false)."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "im",
            "data": {"content": "test", "malicious_field": "injected"},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.data

    def test_create_im_event_content_not_string(self, api_client):
        """IM events must reject non-string content."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {"type": "im", "data": {"content": 12345}}
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.data
