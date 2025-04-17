"""Test the MailboxViewSet."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models


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
            permission=models.MailboxPermissionChoices.SEND,
        )
        factories.MailboxAccessFactory(
            mailbox=user_mailbox1,
            user=authenticated_user,
            permission=models.MailboxPermissionChoices.READ,
        )
        factories.MailboxAccessFactory(
            mailbox=user_mailbox2,
            user=authenticated_user,
            permission=models.MailboxPermissionChoices.READ,
        )
        # Create an other user with access to other mailbox
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=other_user,
            permission=models.MailboxPermissionChoices.SEND,
        )

        # create a thread with one unread message for user_mailbox1
        thread1 = factories.ThreadFactory(mailbox=user_mailbox1)
        factories.MessageFactory(thread=thread1, read_at=None)

        # create a thread with one read message for user_mailbox2
        thread2 = factories.ThreadFactory(mailbox=user_mailbox2)
        factories.MessageFactory(thread=thread2, read_at=timezone.now())

        # create a thread with one unread message for user_mailbox2
        thread3 = factories.ThreadFactory(mailbox=user_mailbox2)
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
                "perms": [models.MailboxPermissionChoices.READ],
                "count_unread_messages": 1,
                "count_messages": 2,
            },
            {
                "id": str(user_mailbox1.id),
                "email": str(user_mailbox1),
                "perms": [
                    models.MailboxPermissionChoices.SEND,
                    models.MailboxPermissionChoices.READ,
                ],
                "count_unread_messages": 1,
                "count_messages": 1,
            },
        ]

    def test_list_unauthorized(self):
        """Anonymous user cannot access the list of mailboxes."""
        client = APIClient()
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
