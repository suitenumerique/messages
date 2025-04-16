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
        message = factories.MessageFactory(thread=thread, read_at=None)
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
        assert response.data["results"][0]["subject"] == thread.subject
        assert response.data["results"][0]["snippet"] == thread.snippet
        assert response.data["results"][0]["recipients"] == [
            {
                "id": str(message.recipients.get().contact.id),
                "name": message.recipients.get().contact.name,
                "email": message.recipients.get().contact.email,
            }
        ]
        assert response.data["results"][0]["messages"] == [str(message.id)]
        assert response.data["results"][0]["is_read"] is False
        assert response.data["results"][0][
            "updated_at"
        ] == thread.updated_at.isoformat().replace("+00:00", "Z")

    def test_list_threads_unauthorized(self):
        """Test list threads unauthorized."""
        client = APIClient()
        response = client.get(reverse("threads-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestApiMessages:
    """Test API messages."""

    def test_list_messages(self):
        """Test list messages."""
        # Create authenticated user with access to a mailbox
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.READ,
        )
        # Create a thread with a 2 messages
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(thread=thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        message2 = factories.MessageFactory(thread=thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message2,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        # create other threads with a message
        thread2 = factories.ThreadFactory(mailbox=mailbox)
        message3 = factories.MessageFactory(thread=thread2, read_at=None)
        factories.MessageRecipientFactory(
            message=message3,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        other_thread = factories.ThreadFactory(mailbox=mailbox)
        other_message = factories.MessageFactory(thread=other_thread, read_at=None)
        factories.MessageRecipientFactory(
            message=other_message,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        # Create a client and authenticate
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Get the list of threads
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": thread.id}
        )
        # Assert the response is successful
        assert response.status_code == status.HTTP_200_OK
        # Assert the number of messages is correct
        assert len(response.data["results"]) == 2
        assert response.data["count"] == 2
        # Assert the messages are correct
        assert response.data["results"][0]["id"] == str(message2.id)
        assert response.data["results"][0][
            "received_at"
        ] == message2.received_at.isoformat().replace("+00:00", "Z")
        assert response.data["results"][0]["subject"] == message2.subject
        assert response.data["results"][0]["sender"] == {
            "id": str(message2.sender.id),
            "name": message2.sender.name,
            "email": message2.sender.email,
        }
        assert response.data["results"][0]["recipients"] == [
            {
                "id": str(message2.recipients.get().id),
                "contact": {
                    "id": str(message2.recipients.get().contact.id),
                    "name": message2.recipients.get().contact.name,
                    "email": message2.recipients.get().contact.email,
                },
                "type": enums.MessageRecipientTypeChoices.TO.value,
            }
        ]
        assert response.data["results"][0]["is_read"] is False
        assert response.data["results"][0]["raw_html_body"] == message2.body_html
        assert response.data["results"][0]["raw_text_body"] == message2.body_text

        assert response.data["results"][1]["id"] == str(message.id)
        assert response.data["results"][1][
            "received_at"
        ] == message.received_at.isoformat().replace("+00:00", "Z")
        assert response.data["results"][1]["subject"] == message.subject
        assert response.data["results"][1]["sender"] == {
            "id": str(message.sender.id),
            "name": message.sender.name,
            "email": message.sender.email,
        }
        assert response.data["results"][1]["recipients"] == [
            {
                "id": str(message.recipients.get().id),
                "contact": {
                    "id": str(message.recipients.get().contact.id),
                    "name": message.recipients.get().contact.name,
                    "email": message.recipients.get().contact.email,
                },
                "type": enums.MessageRecipientTypeChoices.TO.value,
            }
        ]

    def test_list_messages_unauthorized(self):
        """Test list messages unauthorized."""
        client = APIClient()
        response = client.get(reverse("messages-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
