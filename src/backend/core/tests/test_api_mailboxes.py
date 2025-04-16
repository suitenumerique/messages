"""Test the MailboxViewSet."""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models
from core.factories import MailboxAccessFactory, MailboxFactory, UserFactory


@pytest.mark.django_db
class TestMailboxViewSet:
    """Test the MailboxViewSet."""

    def test_list(self):
        """Test the list method."""
        # Create authenticated user with access to 2 mailboxes
        authenticated_user = UserFactory()
        user_mailbox1 = MailboxFactory()
        user_mailbox2 = MailboxFactory()
        other_mailbox = MailboxFactory()
        MailboxAccessFactory(
            mailbox=user_mailbox1,
            user=authenticated_user,
            permission=models.MailboxPermissionChoices.SEND,
        )
        MailboxAccessFactory(
            mailbox=user_mailbox1,
            user=authenticated_user,
            permission=models.MailboxPermissionChoices.READ,
        )
        MailboxAccessFactory(
            mailbox=user_mailbox2,
            user=authenticated_user,
            permission=models.MailboxPermissionChoices.READ,
        )
        MailboxAccessFactory(
            mailbox=other_mailbox,
            permission=models.MailboxPermissionChoices.SEND,
        )

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
            },
            {
                "id": str(user_mailbox1.id),
                "email": str(user_mailbox1),
                "perms": [
                    models.MailboxPermissionChoices.SEND,
                    models.MailboxPermissionChoices.READ,
                ],
            },
        ]

    def test_list_unauthorized(self):
        """Anonymous user cannot access the list of mailboxes."""
        client = APIClient()
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
