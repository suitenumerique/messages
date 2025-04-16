"""Test API threads."""

import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from core import enums, factories


@pytest.mark.django_db
class TestApiThreads:
    """Test API threads."""

    def test_list_threads(self):
        """Test list threads."""
        # Create authenticated user with access to a mailbox
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.READ,
        )
        # Create a thread with a message
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(thread=thread)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )

        # Create a client and authenticate
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Get the list of threads
        response = client.get(
            reverse("threads-list"), query_params={"mailbox_id": mailbox.id}
        )
        # Assert the response is successful
        assert response.status_code == status.HTTP_200_OK
        # Assert the number of threads is correct
        assert len(response.data["results"]) == 1
        assert response.data["count"] == 1
        # Assert the thread is correct
        assert response.data["results"][0]["id"] == str(thread.id)
        # Assert the message is correct
        assert response.data["results"][0]["messages"][0]["id"] == str(message.id)

    def test_list_threads_unauthorized(self):
        """Test list threads unauthorized."""
        client = APIClient()
        response = client.get(reverse("threads-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
