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
        "mailbox_role, thread_role",
        [
            (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.VIEWER),
            (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.EDITOR),
            (enums.MailboxRoleChoices.EDITOR, enums.ThreadAccessRoleChoices.VIEWER),
            (enums.MailboxRoleChoices.ADMIN, enums.ThreadAccessRoleChoices.VIEWER),
            (enums.MailboxRoleChoices.SENDER, enums.ThreadAccessRoleChoices.VIEWER),
        ],
    )
    def test_delete_message_with_bad_permission(self, mailbox_role, thread_role):
        """Test delete message with bad permission."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        message = factories.MessageFactory(subject="Test message")
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=message.thread,
            role=thread_role,
        )
        response = client.delete(reverse("messages-detail", kwargs={"id": message.id}))
        assert response.status_code == status.HTTP_403_FORBIDDEN

        assert models.Message.objects.filter(id=message.id).exists()
        message.refresh_from_db()
        assert not message.is_trashed

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            enums.MailboxRoleChoices.ADMIN,
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.SENDER,
        ],
    )
    def test_delete_message_with_delegated_permission(self, mailbox_role):
        """Test delete message with delegated permission."""
        mailbox = factories.MailboxFactory()
        message_to_delete = factories.MessageFactory(subject="Test message")
        delegated_mailbox = factories.MailboxFactory()
        authenticated_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=delegated_mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=message_to_delete.thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
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

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            enums.MailboxRoleChoices.ADMIN,
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.VIEWER,
            enums.MailboxRoleChoices.SENDER,
        ],
    )
    def test_delete_message_with_bad_delegated_permission(self, mailbox_role):
        """Test delete message with bad delegated permission."""
        mailbox = factories.MailboxFactory()
        message_to_delete = factories.MessageFactory(subject="Test message")
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=message_to_delete.thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        delegated_mailbox = factories.MailboxFactory()
        authenticated_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=delegated_mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )
        # second mailbox with delegated permission but is only viewer
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
        "mailbox_role",
        [
            enums.MailboxRoleChoices.VIEWER,
            enums.MailboxRoleChoices.ADMIN,
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.SENDER,
        ],
    )
    @pytest.mark.redis
    def test_delete_message_success(
        self,
        mailbox_role,
        redis_cache,  # pylint: disable=unused-argument
        django_capture_on_commit_callbacks,
    ):
        """Test delete message.

        Blob lifetime is now GC-driven: ``Message.post_delete`` schedules
        the message's blob ids into the GC candidate set via
        ``transaction.on_commit``; ``gc_orphan_blobs_task`` then deletes
        the rows under per-sha advisory lock + select_for_update. We
        wrap the API delete in ``django_capture_on_commit_callbacks``
        so the SADD lands within the (rolled-back) test transaction,
        then explicitly run the GC sweep before asserting blob count.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.blob_gc import gc_orphan_blobs_task

        assert models.Blob.objects.count() == 0

        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message", thread=thread, raw_mime=b"raw email content"
        )
        message2 = factories.MessageFactory(
            subject="Test message 2", thread=thread, raw_mime=b"raw email content 2"
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )

        assert models.Blob.objects.count() == 2

        # check thread stats before delete
        thread.refresh_from_db()
        thread.update_stats()
        assert thread.has_messages is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.delete(
                reverse("messages-detail", kwargs={"id": message.id})
            )

        if mailbox_role == enums.MailboxRoleChoices.VIEWER:
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert models.Message.objects.filter(id=message.id).exists()
            assert models.Thread.objects.filter(id=message.thread.id).exists()
        else:
            assert response.status_code == status.HTTP_204_NO_CONTENT
            assert not models.Message.objects.filter(id=message.id).exists()
            assert models.Message.objects.filter(id=message2.id).exists()
            assert models.Thread.objects.filter(id=message.thread.id).exists()
            # check thread stats was updated after message was deleted
            thread.refresh_from_db()
            assert thread.has_messages is True

            # Drain the GC candidate set populated by post_delete →
            # on_commit; only message1's blob is orphaned now.
            gc_orphan_blobs_task(mode="fast")
            assert models.Blob.objects.count() == 1

        # Then delete the second message
        with django_capture_on_commit_callbacks(execute=True):
            response = client.delete(
                reverse("messages-detail", kwargs={"id": message2.id})
            )
        if mailbox_role == enums.MailboxRoleChoices.VIEWER:
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert models.Message.objects.filter(id=message2.id).exists()
            assert models.Thread.objects.filter(id=message.thread.id).exists()
        else:
            assert response.status_code == status.HTTP_204_NO_CONTENT
            assert not models.Message.objects.filter(id=message2.id).exists()
            assert models.Thread.objects.count() == 0

            gc_orphan_blobs_task(mode="fast")
            assert models.Blob.objects.count() == 0

            assert models.ThreadAccess.objects.count() == 0
