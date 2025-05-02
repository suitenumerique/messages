"""Test messages delete."""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


@pytest.mark.django_db
class TestMessagesDelete:
    """Test messages delete."""

    def test_delete_message_anonymous(self):
        """Test delete message with anonymous user."""
        message = factories.MessageFactory(subject="Test message")
        client = APIClient()
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_message_without_permissions(self):
        """Test delete message without permissions."""
        authenticated_user = factories.UserFactory()
        message = factories.MessageFactory(subject="Test message")
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert models.Message.objects.filter(id=message.id).exists()
        message.refresh_from_db()
        assert not message.is_trashed

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.EDIT,
            enums.MailboxPermissionChoices.SEND,
        ],
    )
    def test_delete_message_with_bad_permission(self, permission):
        """Test delete message with bad permission."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        message = factories.MessageFactory(subject="Test message")
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert models.Message.objects.filter(id=message.id).exists()
        message.refresh_from_db()
        assert not message.is_trashed

    def test_delete_message_with_delegated_permission(self):
        """Test delete message with delegated permission."""
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        message_to_delete = factories.MessageFactory(subject="Test message")
        delegated_mailbox = factories.MailboxFactory()
        authenticated_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=delegated_mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        # second mailbox with delegated delete permission
        factories.ThreadAccessFactory(
            mailbox=delegated_mailbox,
            thread=message_to_delete.thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        response = client.delete(
            reverse("messages-detail", kwargs={"id": message_to_delete.id})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Message.objects.filter(id=message_to_delete.id).exists()

    def test_delete_message_with_bad_delegated_permission(self):
        """Test delete message with bad delegated permission."""
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        message_to_delete = factories.MessageFactory(subject="Test message")
        delegated_mailbox = factories.MailboxFactory()
        authenticated_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=delegated_mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        # second mailbox with delegated permission but not delete permission
        factories.ThreadAccessFactory(
            mailbox=delegated_mailbox,
            thread=message_to_delete.thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        response = client.delete(
            reverse("messages-detail", kwargs={"id": message_to_delete.id})
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        assert models.Message.objects.filter(id=message_to_delete.id).exists()
        message_to_delete.refresh_from_db()
        assert not message_to_delete.is_trashed

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.DELETE,
            enums.MailboxPermissionChoices.ADMIN,
        ],
    )
    def test_delete_message_success(self, permission):
        """Test delete message."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(subject="Test message", thread=thread)
        message2 = factories.MessageFactory(subject="Test message 2", thread=thread)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Message.objects.filter(id=message.id).exists()
        assert models.Message.objects.filter(id=message2.id).exists()
        assert models.Thread.objects.filter(id=message.thread.id).exists()

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.DELETE,
            enums.MailboxPermissionChoices.ADMIN,
        ],
    )
    def test_delete_last_message_of_thread_success(self, permission):
        """Test delete last message of thread."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        message = factories.MessageFactory(subject="Test message", thread=thread)
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Message.objects.filter(id=message.id).exists()
        assert not models.Thread.objects.filter(id=message.thread.id).exists()
