"""Security tests for the ThreadEvent API — edge cases, IDOR, privilege escalation."""

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


class TestCrossThreadEventAccess:
    """Test that events from one thread cannot be accessed via another thread's URL."""

    def test_retrieve_event_from_different_thread_returns_404(self, api_client):
        """GET /threads/T1/events/E2_id/ where E2 belongs to T2 should return 404."""
        user, mailbox, thread_a = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create a second thread the user also has access to
        thread_b = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_b)

        # Create event in thread B
        event_b = factories.ThreadEventFactory(thread=thread_b, author=user)

        # Try to access event_b via thread A's URL
        response = api_client.get(get_thread_event_url(thread_a.id, event_b.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_event_from_different_thread_returns_404(self, api_client):
        """PATCH /threads/T1/events/E2_id/ where E2 belongs to T2 should return 404."""
        user, mailbox, thread_a = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        thread_b = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_b)
        event_b = factories.ThreadEventFactory(thread=thread_b, author=user)

        response = api_client.patch(
            get_thread_event_url(thread_a.id, event_b.id),
            {"data": {"content": "hijacked"}},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Verify event was not modified
        event_b.refresh_from_db()
        assert event_b.data.get("content") != "hijacked"

    def test_delete_event_from_different_thread_returns_404(self, api_client):
        """DELETE /threads/T1/events/E2_id/ where E2 belongs to T2 should return 404."""
        user, mailbox, thread_a = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        thread_b = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_b)
        event_b = factories.ThreadEventFactory(thread=thread_b, author=user)

        response = api_client.delete(get_thread_event_url(thread_a.id, event_b.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Verify event still exists
        assert models.ThreadEvent.objects.filter(id=event_b.id).exists()


class TestReadOnlyFieldManipulation:
    """Test that read-only and create-only fields cannot be manipulated."""

    def test_create_cannot_set_author(self, api_client):
        """Author should always be set from request.user, not from body."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        impersonated_user = factories.UserFactory()
        data = {
            "type": "im",
            "data": {"content": "test"},
            "author": str(impersonated_user.id),
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["author"]["id"] == str(user.id)

    def test_update_cannot_change_author(self, api_client):
        """PATCH should not allow changing the author field."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"author": str(other_user.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.author_id == user.id

    def test_update_cannot_change_thread(self, api_client):
        """PATCH should not allow moving an event to a different thread."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=other_thread)
        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"thread": str(other_thread.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.thread_id == thread.id

    def test_update_cannot_change_channel(self, api_client):
        """PATCH should not allow changing the channel field."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        channel = factories.ChannelFactory()
        event = factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"channel": str(channel.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.channel_id is None

    def test_update_cannot_change_type(self, api_client):
        """PATCH should not allow changing the type (create-only)."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user, type="im")

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"type": "notification"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.type == "im"

    def test_update_cannot_change_message(self, api_client):
        """PATCH should not allow changing the message FK (create-only)."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        message = factories.MessageFactory(thread=thread)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"message": str(message.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        event.refresh_from_db()
        assert event.message_id is None

    def test_create_cannot_set_timestamps(self, api_client):
        """created_at and updated_at should not be user-controlled."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        data = {
            "type": "im",
            "data": {"content": "test"},
            "created_at": "2000-01-01T00:00:00Z",
            "updated_at": "2000-01-01T00:00:00Z",
        }

        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert not response.data["created_at"].startswith("2000")
        assert not response.data["updated_at"].startswith("2000")


class TestNonAuthorEventManipulation:
    """Test that users other than the author cannot edit/delete events."""

    def test_other_user_cannot_update_event(self, api_client):
        """A user who is not the author should not be able to update an event."""
        author, mailbox, thread = setup_user_with_thread_access()
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=other_user, role=enums.MailboxRoleChoices.ADMIN
        )

        event = factories.ThreadEventFactory(thread=thread, author=author)

        api_client.force_authenticate(user=other_user)
        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "hijacked by other user"}},
            format="json",
        )
        # Should be forbidden — only author should update their own events
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_other_user_cannot_delete_event(self, api_client):
        """A user who is not the author should not be able to delete an event."""
        author, mailbox, thread = setup_user_with_thread_access()
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=other_user, role=enums.MailboxRoleChoices.ADMIN
        )

        event = factories.ThreadEventFactory(thread=thread, author=author)

        api_client.force_authenticate(user=other_user)
        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        # Should be forbidden — only author should delete their own events
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify event still exists
        assert models.ThreadEvent.objects.filter(id=event.id).exists()

    def test_author_can_update_own_event(self, api_client):
        """The author should be able to update their own event."""
        author, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(thread=thread, author=author)

        response = api_client.patch(
            get_thread_event_url(thread.id, event.id),
            {"data": {"content": "updated by author"}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["data"]["content"] == "updated by author"

    def test_author_can_delete_own_event(self, api_client):
        """The author should be able to delete their own event."""
        author, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=author)

        event = factories.ThreadEventFactory(thread=thread, author=author)

        response = api_client.delete(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT


class TestViewerCannotCreateEvents:
    """Test that VIEWER-role users cannot create events (only EDITOR+)."""

    def test_viewer_cannot_create_event(self, api_client):
        """Users with only VIEWER role on a thread should not create events."""
        user, _mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.VIEWER
        )
        api_client.force_authenticate(user=user)

        data = {"type": "im", "data": {"content": "from a viewer"}}
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_editor_can_create_event(self, api_client):
        """Users with EDITOR role should be able to create events (sanity check)."""
        user, _mailbox, thread = setup_user_with_thread_access(
            role=enums.ThreadAccessRoleChoices.EDITOR
        )
        api_client.force_authenticate(user=user)

        data = {"type": "im", "data": {"content": "from an editor"}}
        response = api_client.post(get_thread_event_url(thread.id), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED


class TestParameterConfusionAttack:
    """Test that conflicting thread_id in URL path vs query params can't bypass permissions."""

    def test_url_thread_id_ignores_query_param_thread_id(self, api_client):
        """Passing ?thread_id=X on a nested /threads/Y/events/ should not affect results."""
        user, _mailbox, thread_a = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread B the user has no access to
        thread_b = factories.ThreadFactory()
        factories.ThreadEventFactory(thread=thread_b)

        # Try to list events with confusing params
        url = f"{get_thread_event_url(thread_a.id)}?thread_id={thread_b.id}"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        # Should only return events from thread_a (the URL path), not thread_b
        for event in response.data:
            assert event["thread"] == thread_a.id

    def test_url_thread_id_ignores_query_param_mailbox_id(self, api_client):
        """Passing ?mailbox_id=X on a nested /threads/Y/events/ should not affect permissions."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Another mailbox the user has no access to
        other_mailbox = factories.MailboxFactory()

        url = f"{get_thread_event_url(thread.id)}?mailbox_id={other_mailbox.id}"
        response = api_client.get(url)
        # Permission should be based on URL thread_id, not the query param
        assert response.status_code == status.HTTP_200_OK


class TestAccessRevocation:
    """Test that access revocation properly blocks subsequent operations."""

    def test_revoked_mailbox_access_blocks_event_listing(self, api_client):
        """After mailbox access is revoked, user cannot list events."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        factories.ThreadEventFactory(thread=thread, author=user)

        # Verify access works
        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        # Revoke access
        models.MailboxAccess.objects.filter(mailbox=mailbox, user=user).delete()

        # Verify access is blocked
        response = api_client.get(get_thread_event_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_revoked_access_blocks_event_retrieval(self, api_client):
        """After access revocation, user cannot retrieve specific events."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)

        # Verify access works
        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_200_OK

        # Revoke access
        models.MailboxAccess.objects.filter(mailbox=mailbox, user=user).delete()

        # Verify access is blocked
        response = api_client.get(get_thread_event_url(thread.id, event.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
