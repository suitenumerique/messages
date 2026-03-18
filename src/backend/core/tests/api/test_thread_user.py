"""Tests for the ThreadUser API endpoints."""

import uuid

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories

pytestmark = pytest.mark.django_db


def get_thread_user_url(thread_id):
    """Helper function to get the thread user list URL."""
    return reverse("thread-user-list", kwargs={"thread_id": thread_id})


def setup_user_with_thread_access(
    thread_role=enums.ThreadAccessRoleChoices.EDITOR,
    mailbox_role=enums.MailboxRoleChoices.ADMIN,
):
    """Create a user with mailbox access and thread access.

    Returns (user, mailbox, thread).
    """
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=mailbox_role,
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=thread_role,
    )
    return user, mailbox, thread


class TestThreadUserListAuthentication:
    """Test authentication requirements for GET /threads/{thread_id}/users/."""

    def test_list_thread_users_unauthorized(self, api_client):
        """An unauthenticated request must return 401."""
        thread = factories.ThreadFactory()
        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_thread_users_nonexistent_thread(self, api_client):
        """Requesting users of a non-existent thread must return 403."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestThreadUserListPermissions:
    """Test permission matrix for GET /threads/{thread_id}/users/.

    The endpoint uses IsAllowedToManageThreadAccess which requires:
    - ThreadAccess role = EDITOR on the thread
    - MailboxAccess role in MAILBOX_ROLES_CAN_EDIT (EDITOR, SENDER, ADMIN)
    """

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.SENDER),
        ],
    )
    def test_list_thread_users_allowed(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Users with EDITOR thread access + EDITOR/SENDER/ADMIN mailbox role can list."""
        user, _mailbox, thread = setup_user_with_thread_access(
            thread_role=thread_access_role,
            mailbox_role=mailbox_access_role,
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "thread_access_role, mailbox_access_role",
        [
            # VIEWER on thread — regardless of mailbox role
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.ADMIN),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.EDITOR),
            (enums.ThreadAccessRoleChoices.VIEWER, enums.MailboxRoleChoices.SENDER),
            # EDITOR on thread but only VIEWER on mailbox
            (enums.ThreadAccessRoleChoices.EDITOR, enums.MailboxRoleChoices.VIEWER),
        ],
    )
    def test_list_thread_users_forbidden(
        self, api_client, thread_access_role, mailbox_access_role
    ):
        """Users without sufficient roles are denied."""
        user, _mailbox, thread = setup_user_with_thread_access(
            thread_role=thread_access_role,
            mailbox_role=mailbox_access_role,
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_thread_users_no_thread_access(self, api_client):
        """A user with a mailbox but no ThreadAccess at all is denied."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        # Thread exists but user's mailbox has no ThreadAccess
        thread = factories.ThreadFactory()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestThreadUserListResponse:
    """Test the response format and content of GET /threads/{thread_id}/users/."""

    def test_response_fields(self, api_client):
        """Each user object must contain id, email, full_name, custom_attributes."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) >= 1

        user_data = response.data[0]
        assert "id" in user_data
        assert "email" in user_data
        assert "full_name" in user_data
        assert "custom_attributes" in user_data
        # Must not contain abilities
        assert "abilities" not in user_data

    def test_returns_users_from_single_mailbox(self, api_client):
        """All MailboxAccess users of a thread's mailbox are returned."""
        user, mailbox, thread = setup_user_with_thread_access()

        # Add two more users on the same mailbox
        extra_user_1 = factories.UserFactory()
        extra_user_2 = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=extra_user_1, role=enums.MailboxRoleChoices.VIEWER
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=extra_user_2, role=enums.MailboxRoleChoices.EDITOR
        )

        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        returned_ids = {u["id"] for u in response.data}
        assert str(user.id) in returned_ids
        assert str(extra_user_1.id) in returned_ids
        assert str(extra_user_2.id) in returned_ids

    def test_returns_users_from_multiple_mailboxes(self, api_client):
        """Users from all mailboxes that have ThreadAccess are returned."""
        user, _mailbox, thread = setup_user_with_thread_access()

        # Second mailbox with its own users, also having access to the thread
        mailbox_b = factories.MailboxFactory()
        user_b1 = factories.UserFactory()
        user_b2 = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox_b, user=user_b1, role=enums.MailboxRoleChoices.ADMIN
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox_b, user=user_b2, role=enums.MailboxRoleChoices.VIEWER
        )
        factories.ThreadAccessFactory(
            mailbox=mailbox_b,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        returned_ids = {u["id"] for u in response.data}
        # Users from mailbox_a
        assert str(user.id) in returned_ids
        # Users from mailbox_b
        assert str(user_b1.id) in returned_ids
        assert str(user_b2.id) in returned_ids

    def test_users_are_deduplicated(self, api_client):
        """A user with MailboxAccess on multiple mailboxes sharing the same thread
        must appear only once in the result."""
        user, _mailbox, thread = setup_user_with_thread_access()

        # Same user also has access via a second mailbox
        mailbox_b = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox_b, user=user, role=enums.MailboxRoleChoices.VIEWER
        )
        factories.ThreadAccessFactory(
            mailbox=mailbox_b,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        returned_ids = [u["id"] for u in response.data]
        assert returned_ids.count(str(user.id)) == 1

    def test_users_scoped_to_thread(self, api_client):
        """Users from other threads must not appear."""
        user, _mailbox, thread = setup_user_with_thread_access()

        # Another thread with a different mailbox and users
        other_mailbox = factories.MailboxFactory()
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox, user=other_user, role=enums.MailboxRoleChoices.ADMIN
        )
        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        returned_ids = {u["id"] for u in response.data}
        assert str(other_user.id) not in returned_ids

    def test_ordering(self, api_client):
        """Users are returned ordered by full_name then email."""
        user, mailbox, thread = setup_user_with_thread_access()

        alice = factories.UserFactory(full_name="Alice Durand", email="alice@test.com")
        bob = factories.UserFactory(full_name="Bob Martin", email="bob@test.com")
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=alice, role=enums.MailboxRoleChoices.VIEWER
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=bob, role=enums.MailboxRoleChoices.VIEWER
        )

        api_client.force_authenticate(user=user)

        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        names = [u["full_name"] for u in response.data]
        assert names == sorted(names)


class TestThreadUserListQueryOptimization:
    """Test that the endpoint uses a reasonable number of queries."""

    def test_constant_queries_regardless_of_user_count(
        self, api_client, django_assert_num_queries
    ):
        """Query count should not grow with the number of users."""
        user, mailbox, thread = setup_user_with_thread_access()

        # Add many users on the mailbox
        for _ in range(15):
            extra = factories.UserFactory()
            factories.MailboxAccessFactory(
                mailbox=mailbox, user=extra, role=enums.MailboxRoleChoices.VIEWER
            )

        api_client.force_authenticate(user=user)

        # 1 query for permission check, 1 for user list
        with django_assert_num_queries(2):
            response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 16  # 15 extras + the admin user


class TestThreadUserListReadOnly:
    """Verify the endpoint only exposes the list action."""

    def test_post_not_allowed(self, api_client):
        """POST must be rejected."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.post(
            get_thread_user_url(thread.id),
            {"email": "hacker@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_put_not_allowed(self, api_client):
        """PUT must be rejected."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.put(
            get_thread_user_url(thread.id),
            {"email": "hacker@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_patch_not_allowed(self, api_client):
        """PATCH must be rejected."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            get_thread_user_url(thread.id),
            {"email": "hacker@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_delete_not_allowed(self, api_client):
        """DELETE must be rejected."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.delete(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestThreadUserListIsolation:
    """Test cross-thread and cross-mailbox isolation."""

    def test_cannot_see_users_of_foreign_thread(self, api_client):
        """A user with editor access on thread A must not be able to list
        users of thread B where they have no access."""
        user, _mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread B — user has no access
        thread_b = factories.ThreadFactory()
        other_mailbox = factories.MailboxFactory()
        factories.ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=thread_b,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(get_thread_user_url(thread_b.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_orphan_mailbox_users_not_returned(self, api_client):
        """Users from a mailbox with no MailboxAccess linked to the thread
        must not appear in the results."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        thread = factories.ThreadFactory()
        # Thread access exists for an orphan mailbox (no MailboxAccess)
        orphan_mailbox = factories.MailboxFactory()
        factories.ThreadAccessFactory(
            mailbox=orphan_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        # But user's mailbox has editor access (so permission check passes)
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=user)
        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        # Only the requesting user's mailbox has MailboxAccess → only that user
        returned_ids = {u["id"] for u in response.data}
        assert returned_ids == {str(user.id)}

    def test_mailbox_access_role_does_not_filter_users(self, api_client):
        """All users with any MailboxAccess role (VIEWER, EDITOR, SENDER, ADMIN)
        on a thread-linked mailbox should be returned, regardless of their role."""
        user, mailbox, thread = setup_user_with_thread_access()

        viewer = factories.UserFactory()
        editor = factories.UserFactory()
        sender = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=viewer, role=enums.MailboxRoleChoices.VIEWER
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=editor, role=enums.MailboxRoleChoices.EDITOR
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=sender, role=enums.MailboxRoleChoices.SENDER
        )

        api_client.force_authenticate(user=user)
        response = api_client.get(get_thread_user_url(thread.id))
        assert response.status_code == status.HTTP_200_OK

        returned_ids = {u["id"] for u in response.data}
        assert str(viewer.id) in returned_ids
        assert str(editor.id) in returned_ids
        assert str(sender.id) in returned_ids
