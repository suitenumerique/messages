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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        thread = factories.ThreadFactory(mailbox=mailbox)
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
        thread = factories.ThreadFactory(mailbox=mailbox)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        message = factories.MessageFactory(subject="Test message", thread=thread)
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Message.objects.filter(id=message.id).exists()
        assert not models.Thread.objects.filter(id=message.thread.id).exists()
