"""Test API threads and messages."""

import uuid

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
        # Create 10 threads to populate the database
        factories.ThreadFactory.create_batch(10)
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
        # Need sender and recipient contacts for the thread serializer
        recipient_contact = factories.ContactFactory()
        factories.MessageRecipientFactory(
            message=message,
            contact=recipient_contact,
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
        assert response.data["count"] == 1
        assert len(response.data["results"]) == 1

        # Assert the thread data is correct
        thread_data = response.data["results"][0]
        assert thread_data["id"] == str(thread.id)
        assert thread_data["subject"] == thread.subject
        assert thread_data["snippet"] == thread.snippet
        assert thread_data["messages"] == [str(message.id)]
        assert thread_data["is_read"] is False  # Based on message read_at=None
        assert thread_data["updated_at"] == thread.updated_at.isoformat().replace(
            "+00:00", "Z"
        )

    def test_list_threads_unauthorized(self):
        """Test list threads unauthorized."""
        client = APIClient()
        response = client.get(reverse("threads-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_threads_not_allowed(self):
        """Test list threads not allowed."""
        # Create other mailbox and thread
        jean = factories.UserFactory()
        jean_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=jean_mailbox,
            user=jean,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        factories.ThreadFactory(mailbox=jean_mailbox)  # Create a thread for jean

        # Create authenticated user and their mailbox/thread
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.READ,
        )
        factories.ThreadFactory(
            mailbox=mailbox
        )  # Create a thread for authenticated user

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Try to access jean's mailbox threads
        response = client.get(
            reverse("threads-list"), query_params={"mailbox_id": jean_mailbox.id}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestApiMessages:
    """Test API messages."""

    def test_list_messages(self):
        """Test list messages."""
        # Create 10 messages to populate the database
        factories.MessageFactory.create_batch(10)
        # Setup: User, Mailbox, Thread, 2 Messages
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.READ,
        )
        thread = factories.ThreadFactory(mailbox=mailbox)

        # Contacts
        sender_contact1 = factories.ContactFactory(email="sender1@example.com")
        to_contact1 = factories.ContactFactory(email="to1@example.com")
        cc_contact1 = factories.ContactFactory(email="cc1@example.com")
        sender_contact2 = factories.ContactFactory(email="sender2@example.com")
        to_contact2 = factories.ContactFactory(email="to2@example.com")

        # Message 1 Raw Mime with Headers
        raw_mime_1 = f"""From: {sender_contact1.email}
To: {to_contact1.email}
Cc: {cc_contact1.email}
Subject: Test Subject 1
Content-Type: text/plain

Body 1""".encode("utf-8")

        # Message 2 Raw Mime with Headers
        raw_mime_2 = f"""From: {sender_contact2.email}
To: {to_contact2.email}
Subject: Test Subject 2
Content-Type: text/html

<p>Body 2</p>""".encode("utf-8")

        # Create message 1 using raw_mime_1
        message1 = factories.MessageFactory(
            thread=thread,
            sender=sender_contact1,
            subject="Test Subject 1",  # Subject is also in raw_mime, ensure consistency
            raw_mime=raw_mime_1,
            read_at=None,
        )
        # MessageRecipient objects are primarily for DB relations if needed,
        # the serializer now parses from raw_mime. Keep them if other logic depends on them.
        factories.MessageRecipientFactory(
            message=message1,
            contact=to_contact1,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        factories.MessageRecipientFactory(
            message=message1,
            contact=cc_contact1,
            type=enums.MessageRecipientTypeChoices.CC,
        )

        # Create message 2 using raw_mime_2
        message2 = factories.MessageFactory(
            thread=thread,
            sender=sender_contact2,
            subject="Test Subject 2",
            raw_mime=raw_mime_2,
            read_at=None,
        )
        factories.MessageRecipientFactory(
            message=message2,
            contact=to_contact2,
            type=enums.MessageRecipientTypeChoices.TO,
        )

        # Create other threads/messages to ensure filtering works
        factories.MessageFactory(thread=factories.ThreadFactory(mailbox=mailbox))

        # --- Test ---
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": thread.id}
        )

        # --- Assertions ---
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        assert len(response.data["results"]) == 2

        # Assert message 2 (newest)
        msg2_data = response.data["results"][0]
        assert msg2_data["id"] == str(message2.id)
        # Subject assertion remains, assuming it's correct in both model and raw_mime
        assert msg2_data["subject"] == message2.subject
        assert msg2_data["sender"]["id"] == str(sender_contact2.id)

        # Check JMAP bodies (parsed from raw_mime)
        assert msg2_data["textBody"] == []
        assert len(msg2_data["htmlBody"]) == 1
        assert msg2_data["htmlBody"][0]["type"] == "text/html"
        assert (
            msg2_data["htmlBody"][0]["content"] == "<p>Body 2</p>"
        )  # Check content without headers

        # Check JMAP recipients (parsed from raw_mime)
        assert len(msg2_data["to"]) == 1
        # We check the *email* parsed from the header now, not the contact ID directly
        assert msg2_data["to"][0]["email"] == to_contact2.email
        assert msg2_data["cc"] == []
        assert msg2_data["bcc"] == []

        # Assert message 1 (older)
        msg1_data = response.data["results"][1]
        assert msg1_data["id"] == str(message1.id)
        assert msg1_data["subject"] == message1.subject
        assert msg1_data["sender"]["id"] == str(sender_contact1.id)

        # Check JMAP bodies (parsed from raw_mime)
        assert len(msg1_data["textBody"]) == 1
        assert msg1_data["textBody"][0]["type"] == "text/plain"
        assert (
            msg1_data["textBody"][0]["content"] == "Body 1"
        )  # Check content without headers
        assert msg1_data["htmlBody"] == []

        # Check JMAP recipients (parsed from raw_mime)
        assert len(msg1_data["to"]) == 1
        assert msg1_data["to"][0]["email"] == to_contact1.email
        assert len(msg1_data["cc"]) == 1
        assert msg1_data["cc"][0]["email"] == cc_contact1.email
        assert msg1_data["bcc"] == []

    def test_list_messages_unauthorized(self):
        """Test list messages unauthorized."""
        client = APIClient()
        response = client.get(reverse("messages-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_messages_not_allowed(self):
        """Test list messages not allowed."""
        # Create other user/mailbox/thread
        jean = factories.UserFactory()
        jean_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(mailbox=jean_mailbox, user=jean)
        jean_thread = factories.ThreadFactory(mailbox=jean_mailbox)
        factories.MessageFactory(thread=jean_thread)  # Create message for jean

        # Create authenticated user and their mailbox/thread
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(mailbox=mailbox, user=authenticated_user)
        factories.ThreadFactory(mailbox=mailbox)  # Create thread for auth user

        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Try to access messages in jean's thread
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": jean_thread.id}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_messages_thread_not_existing(self):
        """Test list messages thread not existing."""
        authenticated_user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Test with a non existing thread id
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": uuid.uuid4()}
        )
        # Expecting 403 because the permission check happens before 404 usually
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Test with a thread that exists but user has no access
        unrelated_thread = factories.ThreadFactory()
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": unrelated_thread.id}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_mailbox_not_existing(self):
        """Test mailbox not existing (for threads list)."""
        authenticated_user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Test with a non existing mailbox id
        response = client.get(
            reverse("threads-list"), query_params={"mailbox_id": uuid.uuid4()}
        )
        # Expecting 403 because the permission check happens before 404
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Test with a mailbox that exists but user has no access
        unrelated_mailbox = factories.MailboxFactory()
        response = client.get(
            reverse("threads-list"), query_params={"mailbox_id": unrelated_mailbox.id}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
