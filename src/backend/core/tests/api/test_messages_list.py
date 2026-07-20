"""Test API threads and messages."""

import uuid

from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from core import enums, factories


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mailbox_role, thread_role",
    [
        (enums.MailboxRoleChoices.EDITOR, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.ADMIN, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.EDITOR, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.ADMIN, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.SENDER, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.SENDER, enums.ThreadAccessRoleChoices.EDITOR),
    ],
)
def test_list_threads(mailbox_role, thread_role):
    """Test list threads."""
    # Create 10 threads to populate the database
    factories.ThreadFactory.create_batch(10)

    # Create authenticated user
    authenticated_user = factories.UserFactory()

    # Create a client and authenticate
    client = APIClient()
    client.force_authenticate(user=authenticated_user)

    # Create a mailbox for the authenticated user with access
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=authenticated_user,
        role=mailbox_role,
    )
    # Create an other mailbox for the authenticated user with access
    other_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=other_mailbox,
        user=authenticated_user,
        role=mailbox_role,
    )

    # Create 3 threads with messages in the mailbox
    thread1 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=thread_role,
    )
    factories.MessageFactory(thread=thread1)
    thread1.update_stats()

    thread2 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=thread_role,
    )
    message2 = factories.MessageFactory(thread=thread2)
    thread2.update_stats()

    thread3 = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread3,
        role=thread_role,
    )
    factories.MessageFactory(thread=thread3)
    thread3.update_stats()

    def fetch_threads_and_assert_order(mailbox_id, thread_ids):
        response = client.get(
            reverse("threads-list"), query_params={"mailbox_id": mailbox_id}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == len(thread_ids)
        assert [thread["id"] for thread in response.data["results"]] == [
            str(thread_id) for thread_id in thread_ids
        ]
        return response

    fetch_threads_and_assert_order(mailbox.id, [thread3.id, thread2.id, thread1.id])
    fetch_threads_and_assert_order(other_mailbox.id, [])

    # Create a new message for the second thread to pull it up in the list
    new_message2 = factories.MessageFactory(thread=thread2)
    thread2.update_stats()

    fetch_threads_and_assert_order(mailbox.id, [thread2.id, thread3.id, thread1.id])

    # Create a thread with a message in the other mailbox
    other_thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=other_mailbox,
        thread=other_thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    factories.MessageFactory(thread=other_thread)
    other_thread.update_stats()

    # Need sender and recipient contacts for the thread serializer
    recipient_contact = factories.ContactFactory()
    factories.MessageRecipientFactory(
        message=new_message2,
        contact=recipient_contact,
        type=enums.MessageRecipientTypeChoices.TO,
    )

    fetch_threads_and_assert_order(other_mailbox.id, [other_thread.id])

    response = fetch_threads_and_assert_order(
        mailbox.id, [thread2.id, thread3.id, thread1.id]
    )

    # Assert the threads are sorted by latest message
    assert [thread["id"] for thread in response.data["results"]] == [
        str(thread2.id),
        str(thread3.id),
        str(thread1.id),
    ]

    # Assert the thread data is correct (first thread is the one with the last new message)
    thread_data = response.data["results"][0]
    assert thread_data["id"] == str(thread2.id)
    assert thread_data["subject"] == thread2.subject
    assert thread_data["snippet"] == thread2.snippet
    assert thread_data["messages"] == [str(message2.id), str(new_message2.id)]
    assert thread_data["updated_at"] == thread2.updated_at.isoformat().replace(
        "+00:00", "Z"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mailbox_role, thread_role",
    [
        (enums.MailboxRoleChoices.EDITOR, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.ADMIN, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.EDITOR, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.VIEWER, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.ADMIN, enums.ThreadAccessRoleChoices.EDITOR),
        (enums.MailboxRoleChoices.SENDER, enums.ThreadAccessRoleChoices.VIEWER),
        (enums.MailboxRoleChoices.SENDER, enums.ThreadAccessRoleChoices.EDITOR),
    ],
)
def test_list_threads_delegated_mailbox(mailbox_role, thread_role):
    """Test list threads delegated mailbox."""
    # First create Thread for a initial mailbox
    message = factories.MessageFactory()
    initial_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=initial_mailbox,
        role=mailbox_role,
    )
    factories.ThreadAccessFactory(
        mailbox=initial_mailbox,
        thread=message.thread,
        role=thread_role,
    )

    # Create an other mailbox to delegate access to
    user_to_delegate = factories.UserFactory()
    mailbox_to_delegate = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox_to_delegate,
        user=user_to_delegate,
        role=mailbox_role,
    )

    # Try to access the threads list
    client = APIClient()
    client.force_authenticate(user=user_to_delegate)
    response = client.get(
        reverse("threads-list"), query_params={"mailbox_id": mailbox_to_delegate.id}
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 0

    # Delegate access to the new mailbox
    factories.ThreadAccessFactory(
        mailbox=mailbox_to_delegate,
        thread=message.thread,
        role=thread_role,
    )
    # Try to access the threads list again
    response = client.get(
        reverse("threads-list"), query_params={"mailbox_id": mailbox_to_delegate.id}
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(message.thread.id)


@pytest.mark.django_db
def test_list_threads_unauthorized():
    """Test list threads unauthorized."""
    client = APIClient()
    response = client.get(reverse("threads-list"))
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_list_threads_not_allowed():
    """Test list threads not allowed."""
    # Create other mailbox and thread
    jean = factories.UserFactory()
    jean_mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=jean_mailbox,
        user=jean,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=jean_mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    # Create authenticated user and their mailbox/thread
    authenticated_user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=authenticated_user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    # Create a thread for authenticated user
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    thread_ids = [thread.id]

    client = APIClient()
    client.force_authenticate(user=authenticated_user)
    # Try to access jean's mailbox threads
    response = client.get(reverse("threads-list"))
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == len(thread_ids)
    assert [thread["id"] for thread in response.data["results"]] == [
        str(thread_id) for thread_id in thread_ids
    ]


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
            role=enums.MailboxRoleChoices.VIEWER,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Contacts
        sender_contact1 = factories.ContactFactory(
            email="sender1@example.com", mailbox=mailbox
        )
        to_contact1 = factories.ContactFactory(email="to1@example.com", mailbox=mailbox)
        cc_contact1 = factories.ContactFactory(email="cc1@example.com", mailbox=mailbox)
        sender_contact2 = factories.ContactFactory(
            email="sender2@example.com", mailbox=mailbox
        )
        to_contact2 = factories.ContactFactory(email="to2@example.com", mailbox=mailbox)

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
        )
        factories.MessageRecipientFactory(
            message=message2,
            contact=to_contact2,
            type=enums.MessageRecipientTypeChoices.TO,
        )

        # Create other threads/messages to ensure filtering works
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread2)

        # --- Test ---
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": thread.id}
        )

        # --- Assertions ---
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Assert message 2 (newest)
        msg2_data = response.data[1]
        assert msg2_data["id"] == str(message2.id)
        # Subject assertion remains, assuming it's correct in both model and raw_mime
        assert msg2_data["subject"] == message2.subject
        assert msg2_data["sender"]["id"] == str(sender_contact2.id)

        # Check JMAP bodies (parsed from raw_mime)

        assert len(msg2_data["htmlBody"]) == 1
        assert msg2_data["htmlBody"][0]["type"] == "text/html"
        assert msg2_data["htmlBody"][0]["content"] == "<p>Body 2</p>"
        # Per JMAP spec, text/html parts go to htmlBody AND textBody if no text/plain part is present
        assert msg2_data["textBody"] == msg2_data["htmlBody"]

        # Check JMAP recipients (parsed from raw_mime)
        assert len(msg2_data["to"]) == 1
        # We check the *email* parsed from the header now, not the contact ID directly
        assert msg2_data["to"][0]["contact"]["email"] == to_contact2.email
        assert msg2_data["cc"] == []
        assert msg2_data["bcc"] == []

        # Assert message 1 (older)
        msg1_data = response.data[0]
        assert msg1_data["id"] == str(message1.id)
        assert msg1_data["subject"] == message1.subject
        assert msg1_data["sender"]["id"] == str(sender_contact1.id)

        # Check JMAP bodies (parsed from raw_mime)
        assert len(msg1_data["textBody"]) == 1
        assert msg1_data["textBody"][0]["type"] == "text/plain"
        assert msg1_data["textBody"][0]["content"] == "Body 1"
        # Per JMAP spec, text/plain parts go to textBody AND htmlBody if no text/html part is present
        assert msg1_data["htmlBody"] == msg1_data["textBody"]

        # Check JMAP recipients (parsed from raw_mime)
        assert len(msg1_data["to"]) == 1
        assert msg1_data["to"][0]["contact"]["email"] == to_contact1.email
        assert len(msg1_data["cc"]) == 1
        assert msg1_data["cc"][0]["contact"]["email"] == cc_contact1.email
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
        jean_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=jean_mailbox,
            thread=jean_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=jean_thread)  # Create message for jean

        # Create authenticated user and their mailbox/thread
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(mailbox=mailbox, user=authenticated_user)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

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


@pytest.mark.django_db
class TestMessageIsUnread:
    """Test the is_unread field computed from ThreadAccess.read_at."""

    def _setup(self):
        """Create a user, mailbox, thread with two messages."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        msg1 = factories.MessageFactory(thread=thread)
        msg2 = factories.MessageFactory(thread=thread)

        client = APIClient()
        client.force_authenticate(user=user)

        return client, mailbox, thread, access, msg1, msg2

    def _list_messages(self, client, mailbox, thread):
        """Fetch messages list and return results keyed by message id."""
        response = client.get(
            reverse("messages-list"),
            query_params={
                "mailbox_id": str(mailbox.id),
                "thread_id": str(thread.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        return {str(m["id"]): m for m in response.data}

    def test_messages_unread_when_read_at_is_none(self):
        """All non-draft messages should be unread when read_at is None."""
        client, mailbox, thread, access, msg1, msg2 = self._setup()

        assert access.read_at is None

        messages = self._list_messages(client, mailbox, thread)
        assert messages[str(msg1.id)]["is_unread"] is True
        assert messages[str(msg2.id)]["is_unread"] is True

    def test_messages_read_when_read_at_after_all(self):
        """All messages should be read when read_at is after all message dates."""
        client, mailbox, thread, access, msg1, msg2 = self._setup()

        access.read_at = timezone.now()
        access.save(update_fields=["read_at"])

        messages = self._list_messages(client, mailbox, thread)
        assert messages[str(msg1.id)]["is_unread"] is False
        assert messages[str(msg2.id)]["is_unread"] is False

    def test_messages_partially_read(self):
        """Only messages created after read_at should be unread."""
        client, mailbox, thread, access, msg1, msg2 = self._setup()

        # Set read_at between the two messages
        access.read_at = msg1.created_at
        access.save(update_fields=["read_at"])

        messages = self._list_messages(client, mailbox, thread)
        assert messages[str(msg1.id)]["is_unread"] is False
        assert messages[str(msg2.id)]["is_unread"] is True

    def test_draft_message_not_unread_after_creation(self):
        """A draft is not unread because draft creation sets read_at on ThreadAccess."""
        client, mailbox, thread, access, _msg1, _msg2 = self._setup()

        draft = factories.MessageFactory(thread=thread, is_draft=True)

        # Simulate real create_draft flow which sets read_at = message.created_at
        access.read_at = draft.created_at
        access.save(update_fields=["read_at"])

        messages = self._list_messages(client, mailbox, thread)
        assert messages[str(draft.id)]["is_unread"] is False

    def test_is_unread_isolation_between_mailboxes(self):
        """Two mailboxes sharing a thread should see independent is_unread states."""
        user = factories.UserFactory()

        # Create two mailboxes for the same user
        mailbox_a = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox_a,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        mailbox_b = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox_b,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )

        # Create a shared thread with two messages
        thread = factories.ThreadFactory()
        access_a = factories.ThreadAccessFactory(
            mailbox=mailbox_a,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        access_b = factories.ThreadAccessFactory(
            mailbox=mailbox_b,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        msg1 = factories.MessageFactory(thread=thread)
        msg2 = factories.MessageFactory(thread=thread)

        # Mailbox A has read up to msg1, mailbox B has not read anything
        access_a.read_at = msg1.created_at
        access_a.save(update_fields=["read_at"])
        assert access_b.read_at is None

        client = APIClient()
        client.force_authenticate(user=user)

        # From mailbox A's perspective: msg1 read, msg2 unread
        messages_a = self._list_messages(client, mailbox_a, thread)
        assert messages_a[str(msg1.id)]["is_unread"] is False
        assert messages_a[str(msg2.id)]["is_unread"] is True

        # From mailbox B's perspective: both messages unread
        messages_b = self._list_messages(client, mailbox_b, thread)
        assert messages_b[str(msg1.id)]["is_unread"] is True
        assert messages_b[str(msg2.id)]["is_unread"] is True

        # Now mailbox B reads everything
        access_b.read_at = timezone.now()
        access_b.save(update_fields=["read_at"])

        # Mailbox A should still see msg2 as unread
        messages_a = self._list_messages(client, mailbox_a, thread)
        assert messages_a[str(msg1.id)]["is_unread"] is False
        assert messages_a[str(msg2.id)]["is_unread"] is True

        # Mailbox B should see everything as read
        messages_b = self._list_messages(client, mailbox_b, thread)
        assert messages_b[str(msg1.id)]["is_unread"] is False
        assert messages_b[str(msg2.id)]["is_unread"] is False

    def test_is_unread_defaults_to_false_without_mailbox(self):
        """is_unread should default to False when no mailbox_id is provided."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        msg = factories.MessageFactory(thread=thread)

        client = APIClient()
        client.force_authenticate(user=user)

        # Request without mailbox_id → no annotation → is_unread defaults to False
        response = client.get(
            reverse("messages-list"),
            query_params={"thread_id": str(thread.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        messages = {str(m["id"]): m for m in response.data}
        assert messages[str(msg.id)]["is_unread"] is False


@pytest.mark.django_db
class TestMessageListSummaryParam:
    """?summary=true on the messages list endpoint returns the lightweight shape."""

    def test_summary_true_returns_summary_fields_only(self, api_client):
        """With summary=true, the response is the lightweight MessageSummarySerializer shape."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.EDITOR
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        message = factories.MessageFactory(thread=thread, snippet="Preview text")
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("messages-list"),
            {"thread_id": str(thread.id), "summary": "true"},
        )

        assert response.status_code == status.HTTP_200_OK
        payload = next(m for m in response.data if m["id"] == str(message.id))
        assert set(payload.keys()) == {
            "id", "sender", "sent_at", "is_unread", "is_draft", "has_attachments", "snippet",
        }
        assert payload["snippet"] == "Preview text"

    def test_without_summary_param_returns_full_message(self, api_client):
        """Without summary=true, behavior is unchanged (full MessageSerializer)."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.EDITOR
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        message = factories.MessageFactory(thread=thread)
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("messages-list"), {"thread_id": str(thread.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        payload = next(m for m in response.data if m["id"] == str(message.id))
        assert "textBody" in payload
