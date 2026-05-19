"""Tests for the ThreadAccess API endpoints."""

import threading
import uuid

from django.db import connection
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def get_thread_access_url(thread_id, access_id=None):
    """Helper function to get the thread access URL."""
    if access_id:
        return reverse(
            "thread-access-detail", kwargs={"thread_id": thread_id, "id": access_id}
        )
    return reverse("thread-access-list", kwargs={"thread_id": thread_id})


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


@pytest.fixture(name="thread_with_editor_access")
def fixture_thread_with_editor_access(mailbox_with_access):
    """Create a thread with access for a mailbox."""
    user, mailbox = mailbox_with_access
    thread = factories.ThreadFactory()
    thread_access = factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return user, mailbox, thread, thread_access


class TestThreadAccessList:
    """Test the GET /threads/{thread_id}/accesses/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_list_thread_access_success(
        self,
        api_client,
        thread_access_role,
        mailbox_access_role,
        django_assert_num_queries,
    ):
        """Test listing thread accesses of a thread."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        api_client.force_authenticate(user=user)
        # Create other accesses for thread
        factories.ThreadAccessFactory.create_batch(10, thread=thread)
        # Create others thread accesses for different threads
        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=other_thread,
        )
        factories.ThreadAccessFactory.create_batch(5, thread=other_thread)

        with django_assert_num_queries(3):
            response = api_client.get(get_thread_access_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 11
        assert response.data["results"][0]["thread"] == thread.id

    def test_list_thread_access_filter_by_mailbox(
        self, api_client, thread_with_editor_access, django_assert_num_queries
    ):
        """Test listing thread accesses filtered by mailbox."""
        user, mailbox, thread, _ = thread_with_editor_access
        api_client.force_authenticate(user=user)

        # Create another thread access for a different mailbox
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        with django_assert_num_queries(3):
            response = api_client.get(
                f"{get_thread_access_url(thread.id)}?mailbox_id={mailbox.id}"
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["mailbox"] == mailbox.id

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_list_thread_access_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test listing thread accesses without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        # Create a mailbox and thread access that the user doesn't have access to manage
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )

        # Test that user cannot access thread accesses for a thread they don't have proper access to
        response = api_client.get(get_thread_access_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Test that user cannot access thread accesses for a non-existent thread
        response = api_client.get(get_thread_access_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_thread_access_unauthorized(self, api_client):
        """Test listing thread accesses without authentication."""
        thread = factories.ThreadFactory()
        response = api_client.get(get_thread_access_url(thread.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadAccessCreate:
    """Test the POST /threads/{thread_id}/accesses/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_create_thread_access_success(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test creating a thread access successfully."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        api_client.force_authenticate(user=user)

        delegated_mailbox = factories.MailboxFactory()
        data = {
            "mailbox": str(delegated_mailbox.id),
            "role": "viewer",
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["thread"] == thread.id
        assert response.data["mailbox"] == delegated_mailbox.id
        assert response.data["role"] == "viewer"

    def test_create_thread_access_duplicate(
        self, api_client, thread_with_editor_access
    ):
        """Test creating a duplicate thread access."""
        user, mailbox, thread, _ = thread_with_editor_access
        api_client.force_authenticate(user=user)

        data = {
            "mailbox": str(mailbox.id),
            "role": "editor",
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_create_thread_access_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test creating a thread access without permission."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        api_client.force_authenticate(user=user)

        delegated_mailbox = factories.MailboxFactory()
        data = {
            "mailbox": str(delegated_mailbox.id),
            "role": "viewer",
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_thread_access_invalid_data(
        self, api_client, thread_with_editor_access
    ):
        """Test creating a thread access with invalid data."""
        user, mailbox, thread, _ = thread_with_editor_access
        api_client.force_authenticate(user=user)

        data = {
            "mailbox": str(mailbox.id),
            "role": "invalid_role",  # Invalid role
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_thread_access_unauthorized(self, api_client):
        """Test creating a thread access without authentication."""
        thread = factories.ThreadFactory()
        response = api_client.post(get_thread_access_url(thread.id), {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_thread_access_on_foreign_thread(self, api_client):
        """An authenticated user must not be able to create a ThreadAccess
        on a thread they have no access to."""
        user_1 = factories.UserFactory()
        user_1_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=user_1_mailbox,
            user=user_1,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        # Another thread — user_1 has no ThreadAccess on it
        not_owned_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=not_owned_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=user_1)

        delegated_mailbox = factories.MailboxFactory()
        response = api_client.post(
            get_thread_access_url(not_owned_thread.id),
            {"mailbox": str(delegated_mailbox.id), "role": "viewer"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify no ThreadAccess was created for the not owned thread
        assert not models.ThreadAccess.objects.filter(
            thread=not_owned_thread, mailbox=delegated_mailbox
        ).exists()

    def test_create_thread_access_body_thread_ignored(self, api_client):
        """POST body 'thread' field must not override the URL thread_id.

        A user could POST to their own thread URL but send a another user's
        thread ID in the body, hoping the serializer uses it instead.
        The created ThreadAccess must always belong to the URL thread.
        """
        user_1 = factories.UserFactory()
        user_1_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=user_1_mailbox,
            user=user_1,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        user_1_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_1_mailbox,
            thread=user_1_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        not_owned_thread = factories.ThreadFactory()

        api_client.force_authenticate(user=user_1)

        delegated_mailbox = factories.MailboxFactory()
        response = api_client.post(
            get_thread_access_url(user_1_thread.id),
            {
                "thread": str(not_owned_thread.id),
                "mailbox": str(delegated_mailbox.id),
                "role": "viewer",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        # The created ThreadAccess must point to the URL thread, not the body thread
        assert response.data["thread"] == user_1_thread.id
        assert not models.ThreadAccess.objects.filter(
            thread=not_owned_thread, mailbox=delegated_mailbox
        ).exists()


class TestThreadAccessUpdate:
    """Test the PUT/PATCH /threads/{thread_id}/accesses/{id}/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_update_thread_access_success(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test updating a thread access successfully."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        api_client.force_authenticate(user=user)

        thread_access = factories.ThreadAccessFactory(
            thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
        )

        url = get_thread_access_url(thread.id, thread_access.id)
        data = {"role": "editor"}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["role"] == "editor"

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_update_thread_access_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test updating a thread access without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        # Create a thread access that the user doesn't have right role to modify
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        url = get_thread_access_url(thread.id, thread_access.id)
        data = {"role": "editor"}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Create a thread access that the user doesn't have any role to modify
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory()

        url = get_thread_access_url(thread.id, thread_access.id)
        data = {"role": "editor"}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_thread_access_not_found(self, api_client, mailbox_with_access):
        """Test updating a non-existent thread access."""
        user, _ = mailbox_with_access
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()

        url = get_thread_access_url(thread.id, uuid.uuid4())
        data = {"role": "editor"}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_thread_access_unauthorized(self, api_client):
        """Test updating a thread access without authentication."""
        thread = factories.ThreadFactory()
        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.patch(url, {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_thread_access_cannot_pivot_thread(self, api_client):
        """PATCH must not allow changing the thread FK (IDOR).

        A user with editor access on their own thread could PATCH
        the ThreadAccess record to point to another user's thread, gaining
        full access to it. The 'thread' and 'mailbox' fields must be
        read-only on update.
        """
        # Setup : user with mailbox and a thread they own
        attacker = factories.UserFactory()
        attacker_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=attacker_mailbox,
            user=attacker,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        attacker_thread = factories.ThreadFactory()
        attacker_access = factories.ThreadAccessFactory(
            mailbox=attacker_mailbox,
            thread=attacker_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Setup : another user with a private thread
        victim_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=victim_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=attacker)

        # Attempt to pivot the thread FK to the second user's thread
        url = get_thread_access_url(attacker_thread.id, attacker_access.id)
        api_client.patch(
            url,
            {"thread": str(victim_thread.id), "role": "editor"},
        )

        # The request may succeed (200) but must NOT change the thread
        attacker_access.refresh_from_db()
        assert attacker_access.thread_id == attacker_thread.id, (
            "IDOR: ThreadAccess.thread was changed to another thread via PATCH"
        )

    def test_update_thread_access_cannot_pivot_mailbox(self, api_client):
        """PATCH must not allow changing the mailbox FK on a ThreadAccess."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        other_mailbox = factories.MailboxFactory()

        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, thread_access.id)
        api_client.patch(
            url,
            {"mailbox": str(other_mailbox.id), "role": "editor"},
        )

        thread_access.refresh_from_db()
        assert thread_access.mailbox_id == mailbox.id, (
            "IDOR: ThreadAccess.mailbox was changed via PATCH"
        )


class TestThreadAccessDelete:
    """Test the DELETE /threads/{thread_id}/accesses/{id}/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_delete_thread_access_success(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test deleting a thread access successfully."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        # Ensure another editor exists so the last-editor guard doesn't block
        factories.ThreadAccessFactory(
            thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify the thread access was deleted
        assert not models.ThreadAccess.objects.filter(id=thread_access.id).exists()

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            # A user with viewer rights on the thread but edit-level rights on
            # the mailbox can still leave: destroying the shared ThreadAccess
            # affects the whole mailbox, so mailbox authority is what matters.
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_delete_thread_access_self_removal(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """A user with edit-level rights on the mailbox can remove its own
        ThreadAccess regardless of the thread role."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        # Ensure another editor exists so the last-editor guard doesn't block
        factories.ThreadAccessFactory(
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.ThreadAccess.objects.filter(id=thread_access.id).exists()

    @pytest.mark.parametrize(
        "thread_access_role",
        [
            enums.ThreadAccessRoleChoices.VIEWER,
            enums.ThreadAccessRoleChoices.EDITOR,
        ],
    )
    def test_delete_thread_access_mailbox_viewer_forbidden(
        self, api_client, thread_access_role
    ):
        """A mailbox viewer cannot destroy the mailbox's ThreadAccess — that
        would revoke the thread for every other member of the mailbox."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.ThreadAccess.objects.filter(id=thread_access.id).exists()

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_delete_thread_access_other_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """A user without full edit rights cannot delete another user's thread access."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=thread_access_role,
        )

        # Another user's thread access on the same thread
        other_thread_access = factories.ThreadAccessFactory(thread=thread)

        url = get_thread_access_url(thread.id, other_thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.ThreadAccess.objects.filter(id=other_thread_access.id).exists()

        # Non-existent thread access
        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "mailbox_access_role",
        [
            enums.MailboxRoleChoices.ADMIN,
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.SENDER,
        ],
    )
    def test_delete_other_mailbox_thread_access_allowed_when_manager(
        self, api_client, mailbox_access_role
    ):
        """A user with full edit rights on the thread can revoke another
        mailbox's ThreadAccess — symmetric with the create permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=mailbox_access_role,
        )
        thread = factories.ThreadFactory()
        # User's own EDITOR access — grants management rights on the thread
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Another mailbox's access on the same thread that the user wants to revoke
        other_thread_access = factories.ThreadAccessFactory(
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        url = get_thread_access_url(thread.id, other_thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.ThreadAccess.objects.filter(
            id=other_thread_access.id
        ).exists()

    def test_delete_thread_access_not_found(self, api_client, mailbox_with_access):
        """Test deleting a non-existent thread access."""
        user, _ = mailbox_with_access
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()

        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_thread_access_last_editor_rejected(self, api_client):
        """Deleting the last editor access on a thread must be rejected."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert models.ThreadAccess.objects.filter(id=thread_access.id).exists()

    def test_delete_thread_access_last_editor_with_viewers_rejected(self, api_client):
        """Deleting the last editor is rejected even if viewers remain."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        editor_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Add viewers — they don't count as editors
        factories.ThreadAccessFactory(
            thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
        )
        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, editor_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert models.ThreadAccess.objects.filter(id=editor_access.id).exists()

    def test_delete_thread_access_editor_allowed_when_others_remain(self, api_client):
        """Deleting an editor access is allowed when other editors remain."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        editor_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Another editor on the thread
        factories.ThreadAccessFactory(
            thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, editor_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.ThreadAccess.objects.filter(id=editor_access.id).exists()

    def test_delete_thread_access_unauthorized(self, api_client):
        """Test deleting a thread access without authentication."""
        thread = factories.ThreadFactory()
        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.django_db(transaction=True)
    def test_delete_thread_access_concurrent_last_editors(self):
        """Two concurrent deletes of the last two editors must not orphan the thread.

        Without select_for_update, both requests can see the other editor still
        present and both proceed, leaving zero editors.
        """
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        editor_a = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        editor_b = factories.ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        url_a = get_thread_access_url(thread.id, editor_a.id)
        url_b = get_thread_access_url(thread.id, editor_b.id)

        results = {}

        def delete_access(name, url):
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                results[name] = client.delete(url)
            finally:
                connection.close()

        t1 = threading.Thread(target=delete_access, args=("a", url_a))
        t2 = threading.Thread(target=delete_access, args=("b", url_b))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        status_codes = {results["a"].status_code, results["b"].status_code}

        # One must succeed (204), the other must be rejected (400)
        assert status_codes == {
            status.HTTP_204_NO_CONTENT,
            status.HTTP_400_BAD_REQUEST,
        }, f"Expected one 204 and one 400, got {status_codes}"

        # At least one editor must remain
        remaining = models.ThreadAccess.objects.filter(
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        ).count()
        assert remaining == 1
