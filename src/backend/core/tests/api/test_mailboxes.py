"""Test the MailboxViewSet."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models


@pytest.mark.django_db
class TestMailboxViewSet:
    """Test the MailboxViewSet."""

    def test_list(self):
        """Test the list method."""
        # Create authenticated user with access to 2 mailboxes
        authenticated_user = factories.UserFactory()
        user_mailbox1 = factories.MailboxFactory()
        user_mailbox2 = factories.MailboxFactory()
        other_mailbox = factories.MailboxFactory()
        # Authenticated user has access to 2 mailboxes
        factories.MailboxAccessFactory(
            mailbox=user_mailbox1,
            user=authenticated_user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        factories.MailboxAccessFactory(
            mailbox=user_mailbox2,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        # Create an other user with access to other mailbox
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=other_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # create a thread with one unread message for user_mailbox1
        thread1 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox1,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread1, read_at=None)

        # create a thread with one read message for user_mailbox2
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox2,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread2, read_at=timezone.now())

        # create a thread with one unread message for user_mailbox2
        thread3 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox2,
            thread=thread3,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread3, read_at=None)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Get list of mailboxes
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check response data
        assert response.data == [
            {
                "id": str(user_mailbox2.id),
                "email": str(user_mailbox2),
                "role": str(models.MailboxRoleChoices.EDITOR),
                "count_unread_messages": 1,
                "count_messages": 2,
            },
            {
                "id": str(user_mailbox1.id),
                "email": str(user_mailbox1),
                "role": str(models.MailboxRoleChoices.VIEWER),
                "count_unread_messages": 1,
                "count_messages": 1,
            },
        ]

    def test_list_unauthorized(self):
        """Anonymous user cannot access the list of mailboxes."""
        client = APIClient()
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
