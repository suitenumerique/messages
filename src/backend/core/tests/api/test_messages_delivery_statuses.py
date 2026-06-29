"""Test messages delivery statuses endpoint."""

# pylint: disable=too-many-lines

from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories

pytestmark = pytest.mark.django_db


@pytest.mark.django_db
# pylint: disable=too-many-public-methods
class TestMessagesDeliveryStatuses:
    """Test messages delivery statuses endpoint."""

    def test_api_messages_delivery_statuses_anonymous(self):
        """Test delivery statuses update with anonymous user."""
        message = factories.MessageFactory(subject="Test message")
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        client = APIClient()
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_messages_delivery_statuses_without_permissions(self):
        """Test delivery statuses update without permissions."""
        authenticated_user = factories.UserFactory()
        message = factories.MessageFactory(subject="Test message")
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_api_messages_delivery_statuses_viewer_role_forbidden(self):
        """Test delivery statuses update with viewer role is forbidden."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_api_messages_delivery_statuses_not_sender(self):
        """Test delivery statuses update on a received message (is_sender=False) is forbidden."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Message received (not sent by user)
        message = factories.MessageFactory(
            subject="Received message",
            thread=thread,
            is_sender=False,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "received" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_draft_message(self):
        """Test delivery statuses update on a draft message is forbidden."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Draft message
        message = factories.MessageFactory(
            subject="Draft message",
            thread=thread,
            is_sender=True,
            is_draft=True,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "draft" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_trashed_message(self):
        """Test delivery statuses update on a trashed message is forbidden."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Trashed message
        message = factories.MessageFactory(
            subject="Trashed message",
            thread=thread,
            is_sender=True,
            is_draft=False,
            is_trashed=True,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "trashed" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_empty_body(self):
        """Test delivery statuses update with empty body returns error."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "non-empty" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_invalid_recipient_id(self):
        """Test delivery statuses update with invalid recipient ID returns error."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={"00000000-0000-0000-0000-000000000000": "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not found" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_malformed_uuid(self):
        """Test delivery statuses update with malformed UUID returns 400 error."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={"not-a-valid-uuid": "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "valid uuid" in response.json().get("error", "").lower()

    def test_api_messages_delivery_statuses_invalid_target_status(self):
        """Test delivery statuses update with invalid target status returns error."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "invalid_status"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid" in str(response.json().get("error", "")).lower()

    def test_api_messages_delivery_statuses_failed_to_cancelled(self):
        """Test FAILED -> CANCELLED transition."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        thread.update_stats()
        thread.refresh_from_db()
        assert thread.has_delivery_failed is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"updated_count": 1}

        recipient.refresh_from_db()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.CANCELLED

        thread.refresh_from_db()
        assert thread.has_delivery_failed is False

    def test_api_messages_delivery_statuses_failed_to_retry_success(self):
        """Test FAILED -> RETRY transition succeeds for recent messages."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Message sent 1 day ago (within the 7 day limit)
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
            sent_at=timezone.now() - timedelta(days=1),
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            retry_count=3,
            retry_at=timezone.now(),
            delivery_message="Previous error message",
        )

        thread.update_stats()
        thread.refresh_from_db()
        assert thread.has_delivery_failed is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "retry"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"updated_count": 1}

        recipient.refresh_from_db()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.RETRY
        assert recipient.retry_count == 0
        assert recipient.retry_at is None
        assert recipient.delivery_message is None

        thread.refresh_from_db()
        assert thread.has_delivery_failed is False
        assert thread.has_delivery_pending is True

    @override_settings(MESSAGES_MANUAL_RETRY_MAX_AGE=7200)  # 2 hours
    def test_api_messages_delivery_statuses_failed_to_retry_too_old(self):
        """Test FAILED -> RETRY transition fails for messages older than max age."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Message sent 3 hours ago (beyond the 2 hour limit)
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
            sent_at=timezone.now() - timedelta(hours=3),
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "retry"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json().get("error") == [
            "Message sent more than 2:00:00 ago. Manual retry not allowed."
        ]

        # Verify the status was not changed
        recipient.refresh_from_db()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.FAILED

    def test_api_messages_delivery_statuses_retry_to_cancelled(self):
        """Test RETRY -> CANCELLED transition."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
        )

        thread.update_stats()
        thread.refresh_from_db()
        assert thread.has_delivery_pending is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"updated_count": 1}

        recipient.refresh_from_db()
        assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.CANCELLED

        thread.refresh_from_db()
        assert thread.has_delivery_pending is False

    def test_api_messages_delivery_statuses_invalid_transition_retry_to_failed(self):
        """Test RETRY -> FAILED transition is not allowed."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "failed"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not allowed" in str(response.json().get("error", "")).lower()

    def test_api_messages_delivery_statuses_invalid_transition_sent_to_cancelled(self):
        """Test SENT_EXTERNAL -> CANCELLED transition is not allowed."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT_EXTERNAL,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot update" in str(response.json().get("error", "")).lower()

    def test_api_messages_delivery_statuses_invalid_transition_sent_internal_to_cancelled(
        self,
    ):
        """Test SENT_INTERNAL -> CANCELLED transition is not allowed.

        SENT_INTERNAL is the other terminal "delivered"-class status (see
        the footgun note on ``MessageDeliveryStatusChoices``). Both must
        reject the same invalid transitions so internal mail isn't
        silently mishandled.
        """
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT_INTERNAL,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "cancelled"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot update" in str(response.json().get("error", "")).lower()

    def test_api_messages_delivery_statuses_multiple_recipients(self):
        """Test updating multiple recipients at once."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        recipient_failed_1 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        recipient_failed_2 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        recipient_retry = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.RETRY,
        )
        recipient_sent = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT_EXTERNAL,
        )

        thread.update_stats()
        thread.refresh_from_db()
        assert thread.has_delivery_failed is True
        assert thread.has_delivery_pending is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={
                str(recipient_failed_1.id): "cancelled",
                str(recipient_failed_2.id): "cancelled",
                str(recipient_retry.id): "cancelled",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"updated_count": 3}

        recipient_failed_1.refresh_from_db()
        recipient_failed_2.refresh_from_db()
        recipient_retry.refresh_from_db()
        recipient_sent.refresh_from_db()

        assert (
            recipient_failed_1.delivery_status
            == enums.MessageDeliveryStatusChoices.CANCELLED
        )
        assert (
            recipient_failed_2.delivery_status
            == enums.MessageDeliveryStatusChoices.CANCELLED
        )
        assert (
            recipient_retry.delivery_status
            == enums.MessageDeliveryStatusChoices.CANCELLED
        )
        assert (
            recipient_sent.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_EXTERNAL
        )

        thread.refresh_from_db()
        assert thread.has_delivery_failed is False
        assert thread.has_delivery_pending is False

    def test_api_messages_delivery_statuses_partial_failure(self):
        """Test that if any transition is invalid, no updates are applied."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        recipient_failed = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        recipient_sent = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT_EXTERNAL,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={
                str(recipient_failed.id): "cancelled",
                str(recipient_sent.id): "cancelled",  # Invalid transition
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Verify no updates were applied
        recipient_failed.refresh_from_db()
        recipient_sent.refresh_from_db()
        assert (
            recipient_failed.delivery_status
            == enums.MessageDeliveryStatusChoices.FAILED
        )
        assert (
            recipient_sent.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_EXTERNAL
        )

    def test_api_messages_delivery_statuses_retry_multiple_failed_recipients(self):
        """Test retry of multiple failed recipients at once."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Recent message (within the 7 day limit)
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
            sent_at=timezone.now() - timedelta(days=1),
        )

        recipient_failed_1 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            retry_count=2,
            delivery_message="Error 1",
        )
        recipient_failed_2 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
            retry_count=5,
            delivery_message="Error 2",
        )
        recipient_sent = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.SENT_EXTERNAL,
        )

        thread.update_stats()
        thread.refresh_from_db()
        assert thread.has_delivery_failed is True

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={
                str(recipient_failed_1.id): "retry",
                str(recipient_failed_2.id): "retry",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"updated_count": 2}

        recipient_failed_1.refresh_from_db()
        recipient_failed_2.refresh_from_db()
        recipient_sent.refresh_from_db()

        assert (
            recipient_failed_1.delivery_status
            == enums.MessageDeliveryStatusChoices.RETRY
        )
        assert recipient_failed_1.retry_count == 0
        assert recipient_failed_1.retry_at is None
        assert recipient_failed_1.delivery_message is None

        assert (
            recipient_failed_2.delivery_status
            == enums.MessageDeliveryStatusChoices.RETRY
        )
        assert recipient_failed_2.retry_count == 0
        assert recipient_failed_2.retry_at is None
        assert recipient_failed_2.delivery_message is None

        assert (
            recipient_sent.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_EXTERNAL
        )

        thread.refresh_from_db()
        assert thread.has_delivery_failed is False
        assert thread.has_delivery_pending is True

    @pytest.mark.parametrize(
        "max_age_seconds,message_age_days,should_succeed",
        [
            (7 * 24 * 60 * 60, 1, True),  # 7 days max, 1 day old -> success
            (7 * 24 * 60 * 60, 6, True),  # 7 days max, 6 days old -> success
            (7 * 24 * 60 * 60, 8, False),  # 7 days max, 8 days old -> fail
            (1 * 24 * 60 * 60, 0, True),  # 1 day max, same day -> success
            (1 * 24 * 60 * 60, 2, False),  # 1 day max, 2 days old -> fail
            (14 * 24 * 60 * 60, 10, True),  # 14 days max, 10 days old -> success
            (14 * 24 * 60 * 60, 15, False),  # 14 days max, 15 days old -> fail
        ],
    )
    def test_api_messages_delivery_statuses_retry_age_parametrized(
        self, max_age_seconds, message_age_days, should_succeed, settings
    ):
        """Test FAILED -> RETRY with various MESSAGES_MANUAL_RETRY_MAX_AGE values."""
        settings.MESSAGES_MANUAL_RETRY_MAX_AGE = max_age_seconds

        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
            sent_at=timezone.now() - timedelta(days=message_age_days),
        )
        recipient = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.patch(
            reverse("messages-delivery-statuses", kwargs={"id": message.id}),
            data={str(recipient.id): "retry"},
            format="json",
        )

        if should_succeed:
            assert response.status_code == status.HTTP_200_OK
            recipient.refresh_from_db()
            assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.RETRY
        else:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert (
                "manual retry not allowed"
                in str(response.json().get("error", "")).lower()
            )
            recipient.refresh_from_db()
            assert (
                recipient.delivery_status == enums.MessageDeliveryStatusChoices.FAILED
            )

    def test_api_messages_delivery_statuses_is_atomic(self):
        """Test that database errors trigger a rollback - no partial updates are persisted."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            subject="Test message",
            thread=thread,
            is_sender=True,
            is_draft=False,
        )

        recipient_1 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        recipient_2 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )
        recipient_3 = factories.MessageRecipientFactory(
            message=message,
            delivery_status=enums.MessageDeliveryStatusChoices.FAILED,
        )

        # Track save calls to fail on the second one
        class_model = type(recipient_1)
        original_save = class_model.save
        save_call_count = {"count": 0}

        def mock_failing_save(self, *args, **kwargs):
            save_call_count["count"] += 1
            if save_call_count["count"] == 2:
                raise DatabaseError("Simulated database error")
            return original_save(self, *args, **kwargs)

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        with patch.object(class_model, "save", mock_failing_save):
            with pytest.raises(DatabaseError):
                client.patch(
                    reverse("messages-delivery-statuses", kwargs={"id": message.id}),
                    data={
                        str(recipient_1.id): "cancelled",
                        str(recipient_2.id): "cancelled",
                        str(recipient_3.id): "cancelled",
                    },
                    format="json",
                )

        # Verify NO updates were applied due to atomic transaction rollback
        recipient_1.refresh_from_db()
        recipient_2.refresh_from_db()
        recipient_3.refresh_from_db()

        assert (
            recipient_1.delivery_status == enums.MessageDeliveryStatusChoices.FAILED
        ), "recipient_1 should not have been updated due to transaction rollback"
        assert (
            recipient_2.delivery_status == enums.MessageDeliveryStatusChoices.FAILED
        ), "recipient_2 should not have been updated due to transaction rollback"
        assert (
            recipient_3.delivery_status == enums.MessageDeliveryStatusChoices.FAILED
        ), "recipient_3 should not have been updated due to transaction rollback"
