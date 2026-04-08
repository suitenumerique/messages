"""Tests for the ThreadEvent API endpoints."""

import uuid
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def _force_created_at(event, created_at):
    """Bypass ``auto_now_add`` to set a past ``created_at`` on an event."""
    models.ThreadEvent.objects.filter(pk=event.pk).update(created_at=created_at)
    event.refresh_from_db()


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


def _grant_thread_access(thread):
    """Create a user with edit access to ``thread`` so they can be mentioned."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return user


class TestThreadEventMentionSyncOnUpdate:
    """Test that editing an IM ThreadEvent syncs UserEvent MENTION records."""

    def test_edit_adds_new_mention(self, api_client):
        """Adding a mention on edit should create a UserEvent MENTION."""
        author, _mailbox, thread = setup_user_with_thread_access()
        alice = _grant_thread_access(thread)
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type=enums.ThreadEventTypeChoices.IM,
            data={"content": "Hello"},
        )
        assert not models.UserEvent.objects.filter(thread_event=event).exists()

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {
                "data": {
                    "content": "Hello @[Alice]",
                    "mentions": [{"id": str(alice.id), "name": "Alice"}],
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user_events = models.UserEvent.objects.filter(
            thread_event=event, type=enums.UserEventTypeChoices.MENTION
        )
        assert user_events.count() == 1
        assert user_events.first().user_id == alice.id

    def test_edit_removes_dropped_mention(self, api_client):
        """Removing a mention on edit should delete the stale UserEvent MENTION."""
        author, _mailbox, thread = setup_user_with_thread_access()
        alice = _grant_thread_access(thread)
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type=enums.ThreadEventTypeChoices.IM,
            data={
                "content": "Hey @[Alice]",
                "mentions": [{"id": str(alice.id), "name": "Alice"}],
            },
        )
        # Signal created the UserEvent on save.
        assert models.UserEvent.objects.filter(thread_event=event, user=alice).exists()

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "Never mind"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert not models.UserEvent.objects.filter(
            thread_event=event, type=enums.UserEventTypeChoices.MENTION
        ).exists()

    def test_edit_preserves_read_at_for_unchanged_mention(self, api_client):
        """Editing content without touching a mention must preserve read_at."""
        author, _mailbox, thread = setup_user_with_thread_access()
        alice = _grant_thread_access(thread)
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type=enums.ThreadEventTypeChoices.IM,
            data={
                "content": "Hey @[Alice]",
                "mentions": [{"id": str(alice.id), "name": "Alice"}],
            },
        )
        user_event = models.UserEvent.objects.get(
            thread_event=event, user=alice, type=enums.UserEventTypeChoices.MENTION
        )
        # Simulate Alice having already read the mention.
        read_at = timezone.now()
        user_event.read_at = read_at
        user_event.save()

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {
                "data": {
                    "content": "Hey @[Alice], updated",
                    "mentions": [{"id": str(alice.id), "name": "Alice"}],
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user_event.refresh_from_db()
        assert user_event.read_at == read_at

    def test_edit_swaps_mentioned_user(self, api_client):
        """Replacing a mention should delete the old UserEvent and create the new one."""
        author, _mailbox, thread = setup_user_with_thread_access()
        alice = _grant_thread_access(thread)
        bob = _grant_thread_access(thread)
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type=enums.ThreadEventTypeChoices.IM,
            data={
                "content": "Hey @[Alice]",
                "mentions": [{"id": str(alice.id), "name": "Alice"}],
            },
        )

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {
                "data": {
                    "content": "Hey @[Bob]",
                    "mentions": [{"id": str(bob.id), "name": "Bob"}],
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mention_user_ids = set(
            models.UserEvent.objects.filter(
                thread_event=event, type=enums.UserEventTypeChoices.MENTION
            ).values_list("user_id", flat=True)
        )
        assert mention_user_ids == {bob.id}


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


class TestThreadEventEditDelay:
    """Test the MAX_THREAD_EVENT_EDIT_DELAY window on update and delete."""

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=24 * 60 * 60)
    def test_update_within_delay_allowed(self, api_client):
        """An event within the edit delay can still be updated."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "Edited"}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=24 * 60 * 60)
    def test_update_after_delay_forbidden(self, api_client):
        """An event older than the edit delay cannot be updated."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        _force_created_at(event, timezone.now() - timedelta(hours=25))

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "Too late"}},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        event.refresh_from_db()
        # Content must be unchanged
        assert event.data.get("content") != "Too late"

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=24 * 60 * 60)
    def test_delete_after_delay_forbidden(self, api_client):
        """An event older than the edit delay cannot be deleted."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        _force_created_at(event, timezone.now() - timedelta(hours=25))

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.ThreadEvent.objects.filter(pk=event.pk).exists()

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=0)
    def test_update_when_delay_disabled(self, api_client):
        """A zero delay disables the restriction entirely."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        _force_created_at(event, timezone.now() - timedelta(days=365))

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "Still editable"}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=0)
    def test_delete_when_delay_disabled(self, api_client):
        """A zero delay allows deletion regardless of age."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        _force_created_at(event, timezone.now() - timedelta(days=365))

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    @override_settings(MAX_THREAD_EVENT_EDIT_DELAY=24 * 60 * 60)
    def test_is_editable_field_in_response(self, api_client):
        """``is_editable`` reflects the current delay state in the payload."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        fresh = factories.ThreadEventFactory(thread=thread, author=user)
        stale = factories.ThreadEventFactory(thread=thread, author=user)
        _force_created_at(stale, timezone.now() - timedelta(hours=25))

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        by_id = {item["id"]: item for item in response.data}
        assert by_id[str(fresh.id)]["is_editable"] is True
        assert by_id[str(stale.id)]["is_editable"] is False


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


def get_read_mention_url(thread_id, event_id):
    """Helper to build the read-mention URL for a thread event."""
    return reverse(
        "thread-event-read-mention",
        kwargs={"thread_id": thread_id, "id": event_id},
    )


class TestThreadEventReadMention:
    """Test the PATCH /threads/{thread_id}/events/{id}/read-mention/ endpoint."""

    def test_read_mention_success(self, api_client):
        """PATCH marks the current user's unread MENTION on the event as read."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        user_event = factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        user_event.refresh_from_db()
        assert user_event.read_at is not None

    def test_read_mention_viewer_access(self, api_client):
        """A viewer can acknowledge their own mention (no edit access required)."""
        user, _mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.VIEWER
        )
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        user_event = factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        user_event.refresh_from_db()
        assert user_event.read_at is not None

    def test_read_mention_without_thread_access_forbidden(self, api_client):
        """PATCH without access to the thread is forbidden."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        _other_user, _mailbox, thread = setup_user_with_thread_access()
        event = factories.ThreadEventFactory(thread=thread, author=_other_user)

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_read_mention_does_not_affect_other_users(self, api_client):
        """PATCH must never touch other users' UserEvents."""
        user, _mailbox, thread = setup_user_with_thread_access()
        other_user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        other_ue = factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        other_ue.refresh_from_db()
        assert other_ue.read_at is None

    def test_read_mention_idempotent(self, api_client):
        """PATCH on an already-read mention is a no-op but still returns 204."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        original_read_at = event.created_at
        user_event = factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
            read_at=original_read_at,
        )

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        user_event.refresh_from_db()
        assert user_event.read_at == original_read_at

    def test_read_mention_non_existent_event(self, api_client):
        """PATCH on a non-existent event returns 404."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.patch(get_read_mention_url(thread.id, uuid.uuid4()))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_read_mention_unauthenticated(self, api_client):
        """Unauthenticated request should be rejected."""
        _user, _mailbox, thread = setup_user_with_thread_access()
        event = factories.ThreadEventFactory(thread=thread)

        response = api_client.patch(get_read_mention_url(thread.id, event.id))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
