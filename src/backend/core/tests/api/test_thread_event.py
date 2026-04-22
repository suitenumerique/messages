"""Tests for the ThreadEvent API endpoints."""
# pylint: disable=too-many-lines

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums, factories, models
from core.api.serializers import ThreadEventSerializer
from core.services.thread_events import UNDO_WINDOW_SECONDS

pytestmark = pytest.mark.django_db


def _force_created_at(event, created_at):
    """Bypass ``auto_now_add`` to set a past ``created_at`` on an event."""
    models.ThreadEvent.objects.filter(pk=event.pk).update(created_at=created_at)
    event.refresh_from_db()


def _force_past_undo_window(event):
    """Push ``event`` just past the undo window so it cannot be absorbed."""
    _force_created_at(
        event, timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS + 1)
    )


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

    def test_create_thread_event_assign_rejects_non_uuid_id(self, api_client):
        """An ASSIGN payload carrying a malformed assignee id must return 400.

        Without the ``FormatChecker`` wired into ``ThreadEvent.validate_data``
        the schema's ``"format": "uuid"`` would be ignored and the viewset
        would crash later on ``uuid.UUID(a["id"])``.
        """
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {
                "assignees": [
                    {"id": str(uuid.uuid4()), "name": "Alice"},
                    {"id": "not-a-uuid", "name": "Broken"},
                ]
            },
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "data" in response.data

    def test_create_thread_event_forbidden(self, api_client):
        """Test creating a thread event without thread access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        data = {"type": "im", "data": {"content": "test"}}

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_im_event_mailbox_viewer_forbidden(self, api_client):
        """A mailbox VIEWER cannot post an IM even with EDITOR ThreadAccess.

        ``HasThreadEventWriteAccess`` requires the user to hold a mailbox role
        in ``MAILBOX_ROLES_CAN_EDIT`` to author internal comments — ThreadAccess
        alone never grants commenting rights.
        """
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.VIEWER
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        api_client.force_authenticate(user=user)

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

    def test_update_cannot_reattach_event_to_another_thread(self, api_client):
        """``thread`` is read-only on the serializer: a PATCH carrying a
        different ``thread`` id must be silently ignored, preventing an
        attacker with write access on thread A from moving one of its
        events onto thread B (possibly bypassing thread B's ACL)."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        event = factories.ThreadEventFactory(thread=thread, author=user, type="im")

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"thread": str(other_thread.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.thread_id == thread.id


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

    def test_serializer_validate_dispatches_validate_data_for_im(self):
        """``ThreadEventSerializer.validate()`` routes IM payloads through
        ``ThreadEvent.validate_data``.

        Exercised on the serializer in isolation so a regression that drops
        ``validate()`` and falls back to model ``full_clean()`` would surface
        here — the API-level tests above cannot tell the two paths apart.
        """
        payload = {"type": "im", "data": {"content": "hello"}}
        with patch.object(models.ThreadEvent, "validate_data") as mock_validate:
            serializer = ThreadEventSerializer(data=payload)
            assert serializer.is_valid(), serializer.errors

        mock_validate.assert_called_once_with("im", {"content": "hello"})

    def test_serializer_validate_dispatches_validate_data_for_assign(self):
        """Same wiring for ASSIGN; covers UNASSIGN by code-path equivalence
        (both share ``_ASSIGNEES_SCHEMA`` in ``ThreadEvent.DATA_SCHEMAS``).
        """
        assignee = {"id": str(uuid.uuid4()), "name": "Jane"}
        payload = {"type": "assign", "data": {"assignees": [assignee]}}
        with patch.object(models.ThreadEvent, "validate_data") as mock_validate:
            serializer = ThreadEventSerializer(data=payload)
            assert serializer.is_valid(), serializer.errors

        mock_validate.assert_called_once_with("assign", {"assignees": [assignee]})


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


class TestThreadEventAssign:
    """Test the POST /threads/{thread_id}/events/ endpoint for ASSIGN events."""

    def test_create_assign_success(self, api_client):
        """POST type=assign with valid assignee returns 201 and creates UserEvent ASSIGN."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["type"] == "assign"

        # Verify UserEvent ASSIGN was created by signal
        assert (
            models.UserEvent.objects.filter(
                user=assignee,
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 1
        )

    def test_create_assign_multiple_assignees(self, api_client):
        """POST type=assign with multiple assignees creates UserEvent for each."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee1 = factories.UserFactory()
        assignee2 = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee1, role=enums.MailboxRoleChoices.ADMIN
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee2, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {
                "assignees": [
                    {"id": str(assignee1.id), "name": "A1"},
                    {"id": str(assignee2.id), "name": "A2"},
                ]
            },
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 2
        )

    def test_create_assign_self_assign(self, api_client):
        """POST type=assign where assignee is the author (self-assign) returns 201 per D-07."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(user.id), "name": "Self"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert (
            models.UserEvent.objects.filter(
                user=user,
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 1
        )

    def test_assign_idempotent(self, api_client):
        """POST type=assign when all assignees already assigned returns 204 without creating ThreadEvent (D-08)."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }

        # First assign
        response1 = api_client.post(
            get_thread_event_url(thread.id), data, format="json"
        )
        assert response1.status_code == status.HTTP_201_CREATED

        # Second assign (same assignee) - should be idempotent
        response2 = api_client.post(
            get_thread_event_url(thread.id), data, format="json"
        )
        assert response2.status_code == status.HTTP_204_NO_CONTENT

        # Only 1 ThreadEvent ASSIGN should exist
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 1
        )

    def test_assign_partial_idempotent(self, api_client):
        """POST type=assign with mix of already-assigned and new assignees returns 201 with only new assignees."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee_a = factories.UserFactory()
        assignee_b = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee_a, role=enums.MailboxRoleChoices.ADMIN
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee_b, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        # First assign user A
        data_a = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee_a.id), "name": "A"}]},
        }
        response1 = api_client.post(
            get_thread_event_url(thread.id), data_a, format="json"
        )
        assert response1.status_code == status.HTTP_201_CREATED

        # Assign [A, B] - A is already assigned, B is new
        data_ab = {
            "type": "assign",
            "data": {
                "assignees": [
                    {"id": str(assignee_a.id), "name": "A"},
                    {"id": str(assignee_b.id), "name": "B"},
                ]
            },
        }
        response2 = api_client.post(
            get_thread_event_url(thread.id), data_ab, format="json"
        )
        assert response2.status_code == status.HTTP_201_CREATED

        # 2 ThreadEvent ASSIGN should exist (first for A, second for B only)
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 2
        )

        # Second event data should contain only B
        second_event = (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            )
            .order_by("created_at")
            .last()
        )
        assignee_ids_in_event = [a["id"] for a in second_event.data["assignees"]]
        assert str(assignee_b.id) in assignee_ids_in_event
        assert str(assignee_a.id) not in assignee_ids_in_event

    def test_assign_visible_in_timeline(self, api_client):
        """After ASSIGN, GET events returns the ASSIGN event in the timeline."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        api_client.post(get_thread_event_url(thread.id), data, format="json")

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assign_events = [e for e in response.data if e["type"] == "assign"]
        assert len(assign_events) == 1
        assert assign_events[0]["data"]["assignees"][0]["id"] == str(assignee.id)

    def test_assign_viewer_forbidden(self, api_client):
        """Viewer role POST type=assign returns 403 per D-02."""
        user, mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.VIEWER
        )
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_assign_rejects_assignee_without_thread_access(self, api_client):
        """Assigning a user that has no ThreadAccess at all returns 400."""
        user, _mailbox, thread = setup_user_with_thread_access()
        no_access_user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(no_access_user.id), "name": "NoAccess"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # No ThreadEvent nor UserEvent created.
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 0
        )
        assert (
            models.UserEvent.objects.filter(
                user=no_access_user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )

    def test_assign_rejects_assignee_with_viewer_mailbox_role(self, api_client):
        """Assigning a user whose MailboxAccess role is VIEWER returns 400."""
        user, mailbox, thread = setup_user_with_thread_access()
        viewer_assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=viewer_assignee,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(viewer_assignee.id), "name": "Viewer"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 0
        )

    def test_assign_rejects_assignee_with_viewer_thread_access(self, api_client):
        """Assigning a user reachable only through a VIEWER ThreadAccess returns 400."""
        user, _mailbox, thread = setup_user_with_thread_access()
        viewer_mailbox = factories.MailboxFactory()
        viewer_assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=viewer_mailbox,
            user=viewer_assignee,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.ThreadAccessFactory(
            mailbox=viewer_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(viewer_assignee.id), "name": "Viewer"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestThreadEventUnassign:
    """Test the POST /threads/{thread_id}/events/ endpoint for UNASSIGN events."""

    def test_create_unassign_success(self, api_client):
        """First assign, then unassign returns 201 and deletes the ASSIGN UserEvent."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        # First assign
        assign_data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        api_client.post(get_thread_event_url(thread.id), assign_data, format="json")

        # Push the ASSIGN out of the undo window so the UNASSIGN below is
        # recorded as a distinct event instead of being absorbed.
        assign_event = models.ThreadEvent.objects.get(
            thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
        )
        _force_past_undo_window(assign_event)

        # Then unassign
        unassign_data = {
            "type": "unassign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        response = api_client.post(
            get_thread_event_url(thread.id), unassign_data, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # UserEvent ASSIGN should be deleted
        assert (
            models.UserEvent.objects.filter(
                user=assignee,
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )

    def test_unassign_idempotent(self, api_client):
        """Unassign someone who is not assigned returns 204 without creating ThreadEvent."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        data = {
            "type": "unassign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # No ThreadEvent UNASSIGN should be created
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 0
        )

    def test_unassign_visible_in_timeline(self, api_client):
        """After UNASSIGN, GET events returns the UNASSIGN event in the timeline."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        # First assign
        assign_data = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        api_client.post(get_thread_event_url(thread.id), assign_data, format="json")

        # Push the ASSIGN out of the undo window so the UNASSIGN below is not
        # absorbed as an "undo".
        assign_event = models.ThreadEvent.objects.get(
            thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
        )
        _force_past_undo_window(assign_event)

        # Then unassign
        unassign_data = {
            "type": "unassign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        api_client.post(get_thread_event_url(thread.id), unassign_data, format="json")

        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        unassign_events = [e for e in response.data if e["type"] == "unassign"]
        assert len(unassign_events) == 1

    def test_unassign_viewer_forbidden(self, api_client):
        """Viewer role POST type=unassign returns 403 per D-02."""
        user, mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.VIEWER
        )
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(mailbox=mailbox, user=assignee)
        api_client.force_authenticate(user=user)

        data = {
            "type": "unassign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unassign_mixed_payload_narrows_to_active_assignees(self, api_client):
        """UNASSIGN with a mix of assigned and non-assigned users only emits the active ones.

        Regression guard: the previous ``.exists()`` check let a non-assigned
        user slip into the emitted UNASSIGN event as long as one targeted user
        was actually assigned.
        """
        user, mailbox, thread = setup_user_with_thread_access()
        assigned = factories.UserFactory()
        not_assigned = factories.UserFactory()
        for target in (assigned, not_assigned):
            factories.MailboxAccessFactory(
                mailbox=mailbox, user=target, role=enums.MailboxRoleChoices.ADMIN
            )
        api_client.force_authenticate(user=user)

        # Only ``assigned`` gets an ASSIGN.
        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {"assignees": [{"id": str(assigned.id), "name": "A"}]},
            },
            format="json",
        )
        assign_event = models.ThreadEvent.objects.get(
            thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
        )
        _force_past_undo_window(assign_event)

        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {
                    "assignees": [
                        {"id": str(assigned.id), "name": "A"},
                        {"id": str(not_assigned.id), "name": "B"},
                    ]
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        unassign_events = list(
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            )
        )
        assert len(unassign_events) == 1
        emitted_ids = {a["id"] for a in unassign_events[0].data["assignees"]}
        assert emitted_ids == {str(assigned.id)}

    def test_unassign_only_inactive_users_returns_204(self, api_client):
        """UNASSIGN targeting only users without an active ASSIGN returns 204."""
        user, mailbox, thread = setup_user_with_thread_access()
        not_assigned = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=not_assigned, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(not_assigned.id), "name": "B"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 0
        )


class TestThreadEventUnassignUndoWindow:
    """Test the assign/unassign "undo window" that swallows back-to-back events.

    When an UNASSIGN arrives within ``UNDO_WINDOW_SECONDS`` after an ASSIGN from
    the same author for the same user, both events are elided from the
    timeline: the matching assignees are stripped from the ASSIGN event (event
    deleted if it becomes empty) and no UNASSIGN event is emitted.
    """

    def test_undo_within_window_removes_both_events(self, api_client):
        """Assign then immediately unassign: 204, no events remain, UserEvent gone."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )

        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        assert models.ThreadEvent.objects.filter(thread=thread).count() == 0
        assert (
            models.UserEvent.objects.filter(
                user=assignee,
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )

    def test_undo_different_author_does_not_apply(self, api_client):
        """An UNASSIGN by a different author leaves both events in place."""
        author_a, mailbox, thread = setup_user_with_thread_access()
        author_b = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=author_b, role=enums.MailboxRoleChoices.ADMIN
        )
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )

        # author_a assigns
        api_client.force_authenticate(user=author_a)
        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )

        # author_b unassigns within the window
        api_client.force_authenticate(user=author_b)
        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 1
        )
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 1
        )

    def test_undo_outside_window_does_not_apply(self, api_client):
        """An UNASSIGN past the undo window follows the regular path."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )

        # Push ASSIGN past the undo window
        assign_event = models.ThreadEvent.objects.get(
            thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
        )
        _force_past_undo_window(assign_event)

        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 1
        )
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 1
        )

    def test_undo_partial_trims_assignees(self, api_client):
        """Assign [A, B], then unassign A within window: ASSIGN keeps B, no UNASSIGN event."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee_a = factories.UserFactory()
        assignee_b = factories.UserFactory()
        for target in (assignee_a, assignee_b):
            factories.MailboxAccessFactory(
                mailbox=mailbox, user=target, role=enums.MailboxRoleChoices.ADMIN
            )
        api_client.force_authenticate(user=user)

        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {
                    "assignees": [
                        {"id": str(assignee_a.id), "name": "A"},
                        {"id": str(assignee_b.id), "name": "B"},
                    ]
                },
            },
            format="json",
        )

        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(assignee_a.id), "name": "A"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        assign_events = list(
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            )
        )
        assert len(assign_events) == 1
        remaining_ids = {a["id"] for a in assign_events[0].data["assignees"]}
        assert remaining_ids == {str(assignee_b.id)}
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 0
        )
        # Only B's UserEvent should survive
        surviving = set(
            models.UserEvent.objects.filter(
                thread=thread, type=enums.UserEventTypeChoices.ASSIGN
            ).values_list("user_id", flat=True)
        )
        assert surviving == {assignee_b.id}

    def test_undo_then_reassign_leaves_clean_state(self, api_client):
        """Assign, undo within window, then reassign: exactly one active ASSIGN."""
        user, mailbox, thread = setup_user_with_thread_access()
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        api_client.force_authenticate(user=user)

        assign_payload = {
            "type": "assign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }
        unassign_payload = {
            "type": "unassign",
            "data": {"assignees": [{"id": str(assignee.id), "name": "Assignee"}]},
        }

        api_client.post(get_thread_event_url(thread.id), assign_payload, format="json")
        api_client.post(
            get_thread_event_url(thread.id), unassign_payload, format="json"
        )
        response = api_client.post(
            get_thread_event_url(thread.id), assign_payload, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).count()
            == 1
        )
        assert (
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
            ).count()
            == 0
        )
        assert (
            models.UserEvent.objects.filter(
                user=assignee,
                thread=thread,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 1
        )

    def test_undo_does_not_rewrite_history_for_inactive_user(self, api_client):
        """UNASSIGN for a non-assigned user must not alter a recent ASSIGN event.

        Regression guard: the undo-window absorb used to strip any targeted
        ``assignee_id`` from the recent ``ThreadEvent(ASSIGN).data`` — even
        when that user no longer had an active ``UserEvent(ASSIGN)``, which
        silently corrupted the thread's assignment history.
        """
        user, mailbox, thread = setup_user_with_thread_access()
        assignee_a = factories.UserFactory()
        assignee_b = factories.UserFactory()
        for target in (assignee_a, assignee_b):
            factories.MailboxAccessFactory(
                mailbox=mailbox, user=target, role=enums.MailboxRoleChoices.ADMIN
            )
        api_client.force_authenticate(user=user)

        # Assign [A, B] then clear A's active UserEvent out-of-band (simulates
        # an earlier unassign whose ThreadEvent has since been archived).
        api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "assign",
                "data": {
                    "assignees": [
                        {"id": str(assignee_a.id), "name": "A"},
                        {"id": str(assignee_b.id), "name": "B"},
                    ]
                },
            },
            format="json",
        )
        models.UserEvent.objects.filter(
            user=assignee_a,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).delete()

        # UNASSIGN A within the undo window: A is no longer active, so the
        # absorb path must be skipped entirely.
        response = api_client.post(
            get_thread_event_url(thread.id),
            {
                "type": "unassign",
                "data": {"assignees": [{"id": str(assignee_a.id), "name": "A"}]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        assign_events = list(
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            )
        )
        assert len(assign_events) == 1
        preserved_ids = {a["id"] for a in assign_events[0].data["assignees"]}
        assert preserved_ids == {str(assignee_a.id), str(assignee_b.id)}
        # B's UserEvent must survive untouched.
        assert models.UserEvent.objects.filter(
            user=assignee_b,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()
