"""Test retrieving a message."""
# pylint: disable=redefined-outer-name

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

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

    def test_retrieve_message_unauthorized(self, message_url, other_user):
        """Test retrieving a message."""
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(message_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
