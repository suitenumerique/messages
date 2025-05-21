"""Tests for the ThreadAccess API endpoints."""

import uuid

from django.urls import reverse

import pytest
from rest_framework import status

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
            "role": enums.ThreadAccessRoleChoices.VIEWER,
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["thread"] == thread.id
        assert response.data["mailbox"] == delegated_mailbox.id
        assert response.data["role"] == enums.ThreadAccessRoleChoices.VIEWER

    def test_create_thread_access_duplicate(
        self, api_client, thread_with_editor_access
    ):
        """Test creating a duplicate thread access."""
        user, mailbox, thread, _ = thread_with_editor_access
        api_client.force_authenticate(user=user)

        data = {
            "mailbox": str(mailbox.id),
            "role": enums.ThreadAccessRoleChoices.EDITOR,
        }

        response = api_client.post(get_thread_access_url(thread.id), data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
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
            "role": enums.ThreadAccessRoleChoices.VIEWER,
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


class TestThreadAccessUpdate:
    """Test the PUT/PATCH /threads/{thread_id}/accesses/{id}/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
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
        data = {"role": enums.ThreadAccessRoleChoices.EDITOR}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["role"] == enums.ThreadAccessRoleChoices.EDITOR

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
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
        data = {"role": enums.ThreadAccessRoleChoices.EDITOR}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Create a thread access that the user doesn't have any role to modify
        thread = factories.ThreadFactory()
        thread_access = factories.ThreadAccessFactory()

        url = get_thread_access_url(thread.id, thread_access.id)
        data = {"role": enums.ThreadAccessRoleChoices.EDITOR}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_thread_access_not_found(self, api_client, mailbox_with_access):
        """Test updating a non-existent thread access."""
        user, _ = mailbox_with_access
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()

        url = get_thread_access_url(thread.id, uuid.uuid4())
        data = {"role": enums.ThreadAccessRoleChoices.EDITOR}

        response = api_client.patch(url, data)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_thread_access_unauthorized(self, api_client):
        """Test updating a thread access without authentication."""
        thread = factories.ThreadFactory()
        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.patch(url, {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestThreadAccessDelete:
    """Test the DELETE /threads/{thread_id}/accesses/{id}/ endpoint."""

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
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
        api_client.force_authenticate(user=user)

        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify the thread access was deleted
        assert not models.ThreadAccess.objects.filter(id=thread_access.id).exists()

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_delete_thread_access_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Test deleting a thread access without permission."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        # Create a thread access that the user doesn't have any role to delete
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
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Create a thread access that the user doesn't have any role to delete
        thread_access = factories.ThreadAccessFactory()
        url = get_thread_access_url(thread.id, thread_access.id)
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_thread_access_not_found(self, api_client, mailbox_with_access):
        """Test deleting a non-existent thread access."""
        user, _ = mailbox_with_access
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()

        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_thread_access_unauthorized(self, api_client):
        """Test deleting a thread access without authentication."""
        thread = factories.ThreadFactory()
        url = get_thread_access_url(thread.id, uuid.uuid4())
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
