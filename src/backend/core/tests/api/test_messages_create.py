"""Test API messages create."""
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-positional-arguments

import json
import random
import uuid
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.mda.delivery import _mark_message_as_sent


@pytest.fixture
def draft_url():
    """Return the draft message URL."""
    return reverse("draft-message")


@pytest.fixture
def draft_detail_url(draft_url):
    """Return the draft message detail URL with a placeholder for the message ID."""
    return lambda message_id: f"{draft_url}{message_id}/"


@pytest.fixture
def send_url():
    """Return the send message URL."""
    return reverse("send-message")


@pytest.fixture
def authenticated_user():
    """Create an authenticated user to authenticate."""
    return factories.UserFactory(full_name="Julie Dupont", email="julie@example.com")


@pytest.fixture
def mailbox(authenticated_user):
    """Create a mailbox for the authenticated user."""
    return factories.MailboxFactory(
        local_part=authenticated_user.email.split("@")[0],
        domain__name=authenticated_user.email.split("@")[1],
    )


@pytest.mark.django_db
class TestApiDraftAndSendMessage:
    """Test API draft and send message endpoints."""

    @patch("core.api.viewsets.send.send_outbound_message")
    def test_draft_and_send_message_success(
        self,
        mock_send_outbound,
        mailbox,
        authenticated_user,
        draft_url,
        send_url,
    ):
        """Test create draft message and then successfully send it via the service."""
        mock_send_outbound.return_value = True

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        subject = f"test_draft_send_success {random.randint(0, 1000000000)}"
        draft_response = client.post(
            draft_url,
            {
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": json.dumps({"arbitrary": "json content"}),
                "to": ["pierre@example.com"],
                "cc": ["paul@example.com"],
                "bcc": ["jean@example.com"],
            },
            format="json",
        )
        assert draft_response.status_code == status.HTTP_201_CREATED
        draft_message_id = draft_response.data["id"]

        # Test that the message is a draft
        draft_message = models.Message.objects.get(id=draft_message_id)
        assert draft_message.is_draft is True
        assert draft_message.is_sender is True
        assert draft_message.is_unread is False
        assert draft_message.is_trashed is False
        assert draft_message.is_starred is False
        assert draft_message.mta_sent is False

        assert draft_message.thread.count_messages == 1
        assert draft_message.thread.count_sender == 1
        assert draft_message.thread.count_unread == 0
        assert draft_message.thread.count_trashed == 0
        assert draft_message.thread.count_starred == 0
        assert draft_message.thread.count_draft == 1
        assert draft_message.thread.sender_names == [draft_message.sender.name]

        send_response = client.post(
            send_url,
            {
                "messageId": draft_message_id,
                "senderId": mailbox.id,
            },
            format="json",
        )

        assert send_response.status_code == status.HTTP_200_OK

        mock_send_outbound.assert_called_once()
        call_args, _ = mock_send_outbound.call_args
        sent_message_arg = call_args[0]
        assert isinstance(sent_message_arg, models.Message)
        assert str(sent_message_arg.id) == draft_message_id

        sent_message_data = send_response.data
        assert sent_message_data["id"] == draft_message_id

        # TODO: remove this once we have background tasks
        _mark_message_as_sent(sent_message_arg)

        sent_message = models.Message.objects.get(id=draft_message_id)
        assert sent_message.raw_mime
        assert subject in sent_message.raw_mime.decode("utf-8")

        assert sent_message.is_draft is False
        assert sent_message.is_sender is True
        assert sent_message.is_unread is False
        assert sent_message.is_trashed is False
        assert sent_message.is_starred is False
        assert sent_message.mta_sent is True
        assert sent_message.sent_at is not None

        # Assert the thread is updated
        assert sent_message.thread.count_messages == 1
        assert sent_message.thread.count_sender == 1
        assert sent_message.thread.count_unread == 0
        assert sent_message.thread.count_trashed == 0
        assert sent_message.thread.count_starred == 0
        assert sent_message.thread.count_draft == 0
        assert sent_message.thread.sender_names == [sent_message.sender.name]
        assert sent_message.thread.messaged_at is not None

    @patch("core.api.viewsets.send.send_outbound_message")
    def test_send_message_failure(
        self,
        mock_send_outbound,
        mailbox,
        authenticated_user,
        draft_url,
        send_url,
    ):
        """Test sending a draft message when the delivery service fails."""
        mock_send_outbound.return_value = False

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        subject = f"test_draft_send_fail {random.randint(0, 1000000000)}"
        draft_response = client.post(
            draft_url,
            {
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": "test content",
                "to": ["fail@example.com"],
            },
            format="json",
        )
        assert draft_response.status_code == status.HTTP_201_CREATED
        draft_message_id = draft_response.data["id"]

        send_response = client.post(
            send_url,
            {
                "messageId": draft_message_id,
                "senderId": str(mailbox.id),
            },
            format="json",
        )

        assert send_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        mock_send_outbound.assert_called_once()
        call_args, _ = mock_send_outbound.call_args
        sent_message_arg = call_args[0]
        assert isinstance(sent_message_arg, models.Message)
        assert str(sent_message_arg.id) == draft_message_id

        # For now the message is still a draft
        # When we'll have workers, we'll set the is_draft to False and wait for a worker
        # to process and retry if needed.
        db_message = models.Message.objects.get(id=draft_message_id)
        assert db_message.is_draft is True
        assert db_message.mta_sent is False
        assert db_message.sent_at is None

    def test_draft_message_without_permission_required(
        self,
        mailbox,
        authenticated_user,
        draft_url,
    ):
        """Test create draft message without permission required."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.READ,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new draft message
        response = client.post(
            draft_url,
            {
                "senderId": mailbox.id,
                "subject": "test",
                "draftBody": "<p>test</p> or test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_draft_message_not_allowed(self, authenticated_user, draft_url):
        """Test create draft message not allowed."""
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new draft message
        response = client.post(
            draft_url,
            {
                "senderId": uuid.uuid4(),
                "subject": "test",
                "draftBody": "<p>test</p> or test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is forbidden, there is no mailbox access
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_draft_message_unauthorized(self, draft_url):
        """Test create draft message unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new draft message
        response = client.post(
            draft_url,
            {
                "senderId": uuid.uuid4(),
                "subject": "test",
                "draftBody": "<p>test</p> or test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is unauthorized
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_send_nonexistent_message(self, mailbox, authenticated_user, send_url):
        """Test sending a message that does not exist."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Try to send a non-existent message ID
        response = client.post(
            send_url,
            {
                "messageId": uuid.uuid4(),
                "senderId": str(mailbox.id),
            },
            format="json",
        )

        # Assert the response is not found
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_send_already_sent_message(self, mailbox, authenticated_user, send_url):
        """Test sending a message that is not a draft (already sent)."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )

        # Create a thread with a *sent* message
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            is_draft=False,
            mta_sent=True,
            sent_at=timezone.now(),
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Try to send the non-draft message
        response = client.post(
            send_url,
            {
                "messageId": str(message.id),
                "senderId": str(mailbox.id),
            },
            format="json",
        )

        # Assert the response is not found (as we query for is_draft=True)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestApiDraftAndSendReply:
    """Test API draft and send reply endpoints."""

    def test_draft_and_send_reply_success(
        self, mailbox, authenticated_user, draft_url, send_url
    ):
        """Create draft reply to an existing message and then send it."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.SEND,
        )
        # Create a thread with a message
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(thread=thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )

        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Step 1: Create a draft reply
        draft_response = client.post(
            draft_url,
            {
                "parentId": message.id,  # ID of the message we're replying to
                "senderId": mailbox.id,
                "subject": "test reply",
                "draftBody": "<p>test reply</p> or test reply",
                "to": ["pierre@example.com"],
            },
            format="json",
        )

        # Assert the draft response is successful
        assert draft_response.status_code == status.HTTP_201_CREATED

        # Assert the draft message is created
        draft_message = models.Message.objects.get(id=draft_response.data["id"])
        assert draft_message.is_draft is True
        assert draft_message.mta_sent is False

        # Step 2: Send the draft reply
        send_response = client.post(
            send_url,
            {
                "messageId": draft_message.id,
                "senderId": mailbox.id,
            },
            format="json",
        )

        # Assert the send response is successful
        assert send_response.status_code == status.HTTP_200_OK

        # Assert the message is now sent
        sent_message = models.Message.objects.get(id=draft_message.id)
        assert sent_message.is_draft is False
        assert sent_message.mta_sent is True
        assert sent_message.sent_at is not None

        # Assert the message and thread are created correctly
        assert models.Message.objects.count() == 2
        assert models.Thread.objects.count() == 1

        # Assert the message is correct
        assert sent_message.subject == "test reply"
        assert sent_message.thread == thread
        assert (
            sent_message.sender.email == mailbox.local_part + "@" + mailbox.domain.name
        )
        assert sent_message.recipients.count() == 1
        assert sent_message.recipients.get().contact.email == "pierre@example.com"

        assert (
            b"In-Reply-To: <" + message.mime_id.encode("utf-8") + b">\r\n"
            in sent_message.raw_mime
        )

    def test_draft_reply_without_permission(
        self, mailbox, authenticated_user, draft_url
    ):
        """Create draft reply to an existing thread without permission."""
        # Create a mailbox access on this mailbox for the authenticated user
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
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create new draft reply
        response = client.post(
            draft_url,
            {
                "parentId": message.id,
                "senderId": mailbox.id,
                "subject": "test",
                "draftBody": "<p>test</p> or test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_draft_reply_unauthorized(self, draft_url):
        """Test draft reply unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new draft reply
        response = client.post(
            draft_url,
            {
                "parentId": uuid.uuid4(),
                "senderId": uuid.uuid4(),
                "subject": "test",
                "draftBody": "<p>test</p> or test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_draft_message_success(
        self,
        mailbox,
        authenticated_user,
        draft_url,
        draft_detail_url,
        send_url,
    ):
        """Test updating a draft message successfully."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.SEND,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Step 1: Create a draft message
        subject = f"test {random.randint(0, 1000000000)}"
        draft_response = client.post(
            draft_url,
            {
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )

        # Assert the draft response is successful
        assert draft_response.status_code == status.HTTP_201_CREATED

        # Assert the draft message is created
        assert models.Message.objects.count() == 1
        draft_message = models.Message.objects.get(id=draft_response.data["id"])
        assert draft_message.is_draft is True

        # Step 2: Update the draft message using PUT
        updated_subject = f"updated {random.randint(0, 1000000000)}"
        update_response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "subject": updated_subject,
                "draftBody": "updated content",
                "to": ["pierre@example.com", "jacques@example.com"],
                "cc": ["paul@example.com"],
            },
            format="json",
        )

        # Assert the update response is successful
        assert update_response.status_code == status.HTTP_200_OK

        # Assert the message is updated
        updated_message = models.Message.objects.get(id=draft_message.id)
        assert updated_message.is_draft is True
        assert updated_message.subject == updated_subject
        assert updated_message.draft_body == "updated content"

        # Assert recipients are updated
        assert updated_message.recipients.count() == 3
        to_recipients = updated_message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.TO
        )
        assert to_recipients.count() == 2
        assert {r.contact.email for r in to_recipients} == {
            "pierre@example.com",
            "jacques@example.com",
        }

        cc_recipients = updated_message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.CC
        )
        assert cc_recipients.count() == 1
        assert cc_recipients.first().contact.email == "paul@example.com"

        # Assert thread is updated
        thread = models.Thread.objects.get(id=updated_message.thread.id)
        assert thread.subject == updated_subject
        # Verify thread snippet updated (Optional based on requirements)
        # assert thread.snippet == "updated content"[:100]

        # Step 3: Send the updated draft message
        send_response = client.post(
            send_url,
            {
                "messageId": updated_message.id,
                "senderId": mailbox.id,
            },
            format="json",
        )

        # Assert the send response is successful
        assert send_response.status_code == status.HTTP_200_OK

        # Assert the message is now sent with the updated content
        sent_message = models.Message.objects.get(id=updated_message.id)
        assert sent_message.mta_sent is True
        assert sent_message.subject == updated_subject
        assert sent_message.is_draft is False

    def test_update_nonexistent_draft(
        self, mailbox, authenticated_user, draft_detail_url
    ):
        """Test updating a non-existent draft message."""
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )

        # Try to update a non-existent draft
        random_id = uuid.uuid4()
        response = client.put(
            draft_detail_url(random_id),
            {
                "senderId": mailbox.id,
                "subject": "updated subject",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_sent_message(self, mailbox, authenticated_user, draft_detail_url):
        """Test updating an already sent message."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
        )

        # Create a thread with a sent message
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            is_draft=False,
            mta_sent=True,
            sent_at=timezone.now(),
        )

        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Try to update an already sent message
        response = client.put(
            draft_detail_url(message.id),
            {
                "senderId": mailbox.id,
                "subject": "updated subject",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_draft_unauthorized(self, draft_detail_url):
        """Test updating a draft message without authentication."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to update a draft message
        response = client.put(
            draft_detail_url(uuid.uuid4()),
            {
                "subject": "updated subject",
            },
            format="json",
        )
        # Assert the response is unauthorized
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
