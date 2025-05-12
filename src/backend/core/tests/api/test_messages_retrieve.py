"""Test retrieving a message."""
# pylint: disable=redefined-outer-name

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db


@pytest.fixture
def message_url(message):
    """Get the url for a message."""
    return reverse("messages-detail", kwargs={"id": message.id})


class TestRetrieveMessage:
    """Test retrieving a message."""

    def test_retrieve_message(self, message, message_url, mailbox_access):
        """Test retrieving a message."""
        client = APIClient()
        client.force_authenticate(user=mailbox_access.user)
        response = client.get(message_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(message.id)

    def test_retrieve_message_delegated_to_other_mailbox(
        self, message, message_url, other_user
    ):
        """Test retrieving a message."""
        client = APIClient()
        client.force_authenticate(user=other_user)
        # create a mailbox access for the other user
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=other_user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        # create a thread access for the other user
        factories.ThreadAccessFactory(
            thread=message.thread,
            mailbox=other_mailbox,
            role=models.ThreadAccessRoleChoices.VIEWER,
        )
        response = client.get(message_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(message.id)

    def test_retrieve_message_unauthorized(self, message_url, other_user):
        """Test retrieving a message."""
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(message_url)
        # we should get a 404 because the message is not accessible by the other user
        assert response.status_code == status.HTTP_404_NOT_FOUND
