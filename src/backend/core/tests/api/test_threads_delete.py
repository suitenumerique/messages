"""Test threads delete."""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


@pytest.mark.django_db
class TestThreadsDelete:
    """Test threads delete."""

    def test_delete_thread_anonymous(self):
        """Test delete thread with anonymous user."""
        thread = factories.ThreadFactory()
        client = APIClient()
        response = client.delete(reverse("threads-detail", kwargs={"id": thread.id}))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_thread_without_permissions(self):
        """Test delete thread without permissions."""
        authenticated_user = factories.UserFactory()
        thread = factories.ThreadFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.delete(reverse("threads-detail", kwargs={"id": thread.id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert models.Thread.objects.filter(id=thread.id).exists()

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.EDIT,
            enums.MailboxPermissionChoices.SEND,
        ],
    )
    def test_delete_thread_with_bad_permission(self, permission):
        """Test delete thread with bad permission."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory(mailbox=mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.delete(reverse("threads-detail", kwargs={"id": thread.id}))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.Thread.objects.filter(id=thread.id).exists()

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.DELETE,
            enums.MailboxPermissionChoices.ADMIN,
        ],
    )
    def test_delete_thread_success(self, permission):
        """Test delete thread."""
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
        response = client.delete(reverse("threads-detail", kwargs={"id": thread.id}))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Thread.objects.filter(id=thread.id).exists()
        assert not models.Message.objects.filter(id=message.id).exists()
        assert not models.Message.objects.filter(id=message2.id).exists()


class TestThreadsBulkDelete:
    """Test threads bulk delete."""

    def test_delete_thread_bulk_anonymous(self):
        """Test delete thread bulk with anonymous user."""
        client = APIClient()
        response = client.post(reverse("threads-bulk-delete"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_thread_bulk_without_permissions(self):
        """Test delete thread bulk without permissions."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory(mailbox=mailbox)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.post(
            reverse("threads-bulk-delete"), {"thread_ids": [thread.id]}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.EDIT,
            enums.MailboxPermissionChoices.SEND,
        ],
    )
    def test_delete_thread_bulk_with_bad_permission(self, permission):
        """Test delete thread bulk with bad permission."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        thread = factories.ThreadFactory(mailbox=mailbox)
        thread2 = factories.ThreadFactory(mailbox=mailbox)
        thread3 = factories.ThreadFactory(mailbox=mailbox)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.post(
            reverse("threads-bulk-delete"),
            {"thread_ids": [thread.id, thread2.id, thread3.id]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "permission",
        [
            enums.MailboxPermissionChoices.DELETE,
            enums.MailboxPermissionChoices.ADMIN,
        ],
    )
    def test_delete_thread_bulk(self, permission):
        """Test delete thread bulk."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory(mailbox=mailbox)
        thread2 = factories.ThreadFactory(mailbox=mailbox)
        thread3 = factories.ThreadFactory(mailbox=mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.post(
            reverse("threads-bulk-delete"),
            {"thread_ids": [thread.id, thread2.id, thread3.id]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert not models.Thread.objects.filter(id=thread.id).exists()
        assert not models.Thread.objects.filter(id=thread2.id).exists()
        assert not models.Thread.objects.filter(id=thread3.id).exists()
