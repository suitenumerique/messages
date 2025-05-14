"""Test API messages create."""
# pylint: disable=too-many-positional-arguments, too-many-lines

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


@pytest.fixture(name="draft_detail_url")
def fixture_draft_detail_url():
    """Return the draft message detail URL with a placeholder for the message ID."""
    return lambda message_id: f"{reverse('draft-message')}{message_id}/"


@pytest.fixture(name="send_url")
def fixture_send_url():
    """Return the send message URL."""
    return reverse("send-message")


@pytest.fixture(name="authenticated_user")
def fixture_authenticated_user():
    """Create an authenticated user to authenticate."""
    return factories.UserFactory(full_name="Julie Dupont", email="julie@example.com")


@pytest.fixture(name="mailbox")
def fixture_mailbox(authenticated_user):
    """Create a mailbox for the authenticated user."""
    return factories.MailboxFactory(
        local_part=authenticated_user.email.split("@")[0],
        domain__name=authenticated_user.email.split("@")[1],
    )


@pytest.mark.django_db
class TestApiDraftAndSendMessage:
    """Test API draft and send message endpoints."""

    @patch("core.mda.outbound.send_outbound_message")
    def test_draft_and_send_message_success(
        self, mock_send_outbound_message, mailbox, authenticated_user, send_url
    ):
        """Test create draft message and then successfully send it via the service."""

        mock_send_outbound_message.side_effect = lambda recipient_emails, message: {
            recipient_email: {
                "delivered": True,
                "error": None,
            }
            for recipient_email in recipient_emails
        }

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        assert not models.ThreadAccess.objects.exists()

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        draft_content = json.dumps(
            {"arbitrary": f"json content {random.randint(0, 1000000000)}"}
        )

        subject = f"test_draft_send_success {random.randint(0, 1000000000)}"
        draft_response = client.post(
            reverse("draft-message"),
            {
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": draft_content,
                "to": ["pierre@external.com"],
                "cc": ["paul@external.com"],
                "bcc": ["jean@external.com"],
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
        assert draft_message.sent_at is None
        assert draft_message.draft_body == draft_content

        assert all(
            recipient.delivery_status is None
            for recipient in draft_message.recipients.all()
        )
        assert all(
            recipient.delivered_at is None
            for recipient in draft_message.recipients.all()
        )

        assert draft_message.thread.count_messages == 1
        assert draft_message.thread.count_sender == 1
        assert draft_message.thread.count_unread == 0
        assert draft_message.thread.count_trashed == 0
        assert draft_message.thread.count_starred == 0
        assert draft_message.thread.count_draft == 1
        assert draft_message.thread.sender_names == [draft_message.sender.name]

        # check thread access was created
        assert models.ThreadAccess.objects.filter(
            thread=draft_message.thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        ).exists()

        draft_api_message = client.get(
            reverse("messages-detail", kwargs={"id": draft_message_id})
        ).data
        assert draft_api_message["draftBody"] == draft_content
        assert draft_api_message["is_draft"] is True
        assert draft_api_message["bcc"][0]["email"] == "jean@external.com"

        send_response = client.post(
            send_url,
            {
                "messageId": draft_message_id,
                "senderId": mailbox.id,
                "textBody": "test",
            },
            format="json",
        )

        assert send_response.status_code == status.HTTP_200_OK

        # Checks on the returned task_id
        task_id = send_response.data["task_id"]
        assert task_id is not None

        # Check with an unknown task_id
        task_response = client.get(
            reverse("task-detail", kwargs={"task_id": "unknown-task-id"})
        )
        assert task_response.status_code == status.HTTP_200_OK
        assert task_response.data["status"] == "PENDING"

        # Call the task API
        task_response = client.get(reverse("task-detail", kwargs={"task_id": task_id}))
        assert task_response.status_code == status.HTTP_200_OK
        assert task_response.data["status"] == "SUCCESS"
        assert task_response.data["result"] is not None
        assert task_response.data["result"]["message_id"] == draft_message_id
        assert task_response.data["error"] is None

        mock_send_outbound_message.assert_called()

        sent_message = models.Message.objects.get(id=draft_message_id)
        assert sent_message.raw_mime
        assert subject in sent_message.raw_mime.decode("utf-8")

        assert sent_message.is_draft is False
        assert sent_message.is_sender is True
        assert sent_message.is_unread is False
        assert sent_message.is_trashed is False
        assert sent_message.is_starred is False
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


    @patch("core.mda.outbound.send_outbound_message")
    def test_draft_and_send_message_success_delegated_access(
        self, mock_send_outbound_message, mailbox, authenticated_user, send_url
    ):
        """Test create draft message and then successfully send it via the service."""
        mock_send_outbound_message.side_effect = lambda recipient_emails, message: {
            recipient_email: {
                "delivered": True,
                "error": None,
            }
            for recipient_email in recipient_emails
        }

        other_mailbox = factories.MailboxFactory(
            local_part="cantine", domain__name="tataouin.fr"
        )

        # Initialize a thread
        thread = factories.ThreadFactory()
        # First delegate access to the other mailbox
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=other_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender = factories.ContactFactory(mailbox=other_mailbox)
        # create a message in the thread
        message = factories.MessageFactory(thread=thread, sender=sender)

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        # add access for the current mailbox to the thread
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        draft_content = json.dumps(
            {"arbitrary": f"json content {random.randint(0, 1000000000)}"}
        )

        subject = f"test_draft_send_success {random.randint(0, 1000000000)}"
        draft_response = client.post(
            reverse("draft-message"),
            {
                "parentId": message.id,
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": draft_content,
                "to": ["pierre@external.com"],
                "cc": ["paul@external.com"],
                "bcc": ["jean@external.com"],
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
        assert draft_message.draft_body == draft_content

        assert draft_message.thread.count_messages == 2
        assert draft_message.thread.count_sender == 1  # fixme 2?
        assert draft_message.thread.count_unread == 0
        assert draft_message.thread.count_trashed == 0
        assert draft_message.thread.count_starred == 0
        assert draft_message.thread.count_draft == 1
        assert draft_message.thread.sender_names == [
            message.sender.name,
            draft_message.sender.name,
        ]

        message_response = client.get(
            reverse("messages-detail", kwargs={"id": draft_message_id})
        )
        draft_api_message = message_response.data
        assert message_response.status_code == status.HTTP_200_OK
        assert draft_api_message["draftBody"] == draft_content
        assert draft_api_message["is_draft"] is True

        send_response = client.post(
            send_url,
            {
                "messageId": draft_message_id,
                "senderId": mailbox.id,
            },
            format="json",
        )

        assert send_response.status_code == status.HTTP_200_OK

        mock_send_outbound_message.assert_called()

        sent_message = models.Message.objects.get(id=draft_message_id)
        assert sent_message.raw_mime
        assert subject in sent_message.raw_mime.decode("utf-8")

        assert sent_message.is_draft is False
        assert sent_message.is_sender is True
        assert sent_message.is_unread is False
        assert sent_message.is_trashed is False
        assert sent_message.is_starred is False
        assert sent_message.sent_at is not None

        # Assert the thread is updated
        assert sent_message.thread.count_messages == 2
        assert sent_message.thread.count_sender == 1
        assert sent_message.thread.count_unread == 0
        assert sent_message.thread.count_trashed == 0
        assert sent_message.thread.count_starred == 0
        assert sent_message.thread.count_draft == 0
        assert sent_message.thread.sender_names == [
            message.sender.name,
            sent_message.sender.name,
        ]
        assert sent_message.thread.messaged_at is not None

        assert all(
            recipient.delivery_status == enums.MessageDeliveryStatusChoices.SENT
            for recipient in sent_message.recipients.all()
        )
        assert all(
            recipient.delivered_at is not None
            for recipient in sent_message.recipients.all()
        )

    @patch("core.mda.outbound.send_outbound_message")
    def test_send_message_failure(
        self,
        mock_send_outbound_message,
        mailbox,
        authenticated_user,
        send_url,
    ):
        """Test sending a draft message when the delivery service fails."""

        mock_send_outbound_message.side_effect = lambda recipient_emails, message: {
            recipient_email: {
                "delivered": False,
                "error": "Custom error message",
            }
            if recipient_email == "fail@external.com"
            else {
                "delivered": True,
                "error": None,
            }
            for recipient_email in recipient_emails
        }

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        subject = f"test_draft_send_fail {random.randint(0, 1000000000)}"
        draft_response = client.post(
            reverse("draft-message"),
            {
                "senderId": mailbox.id,
                "subject": subject,
                "draftBody": "test content",
                "to": ["fail@external.com", "success@external.com"],
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
                "textBody": "test",
            },
            format="json",
        )

        assert send_response.status_code == status.HTTP_200_OK

        mock_send_outbound_message.assert_called()

        db_message = models.Message.objects.get(id=draft_message_id)
        assert db_message.is_draft is False
        assert db_message.sent_at is not None

        fail_recipient = db_message.recipients.get(contact__email="fail@external.com")
        assert (
            fail_recipient.delivery_status == enums.MessageDeliveryStatusChoices.RETRY
        )
        assert fail_recipient.retry_at is not None
        assert fail_recipient.retry_count == 1

        success_recipient = db_message.recipients.get(
            contact__email="success@external.com"
        )
        assert (
            success_recipient.delivery_status == enums.MessageDeliveryStatusChoices.SENT
        )
        assert success_recipient.delivered_at is not None
        assert success_recipient.retry_count == 0

    def test_draft_message_without_permission_required(
        self, mailbox, authenticated_user
    ):
        """Test create draft message without permission required."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new draft message
        response = client.post(
            reverse("draft-message"),
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

    # TODO: implement this test
    # def test_send_message_without_permission_required(self, authenticated_user, send_url):
    #    """Test send message without permission required."""

    def test_draft_message_not_allowed(self, authenticated_user):
        """Test create draft message not allowed."""
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new draft message
        response = client.post(
            reverse("draft-message"),
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

    # TODO: implement this test
    #   def test_send_message_not_allowed(self, authenticated_user, send_url):
    #    """Test send message not allowed."""

    def test_draft_message_unauthorized(self):
        """Test create draft message unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new draft message
        response = client.post(
            reverse("draft-message"),
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

    # TODO: implement this test
    # def test_send_message_unauthorized(self, mailbox, authenticated_user, send_url):
    #    """Test send message unauthorized."""

    def test_send_nonexistent_message(self, mailbox, authenticated_user, send_url):
        """Test sending a message that does not exist."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
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
            role=enums.MailboxRoleChoices.EDITOR,
        )

        # Create a thread with a *sent* message
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            thread=thread_access.thread,
            is_draft=False,
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

    def test_draft_and_send_reply_success(self, mailbox, authenticated_user, send_url):
        """Create draft reply to an existing message and then send it."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        # Create a thread with a message
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(thread=thread_access.thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )

        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Step 1: Create a draft reply
        draft_response = client.post(
            reverse("draft-message"),
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
        assert draft_message.parent == message

        draft_api_message = client.get(
            reverse("messages-detail", kwargs={"id": draft_message.id})
        ).data
        assert draft_api_message["is_draft"] is True
        assert draft_api_message["parent_id"] == str(message.id)

        # Step 2: Send the draft reply
        send_response = client.post(
            send_url,
            {
                "messageId": draft_message.id,
                "senderId": mailbox.id,
                "textBody": "test",
            },
            format="json",
        )

        # Assert the send response is successful
        assert send_response.status_code == status.HTTP_200_OK

        # Assert the message is now sent
        sent_message = models.Message.objects.get(id=draft_message.id)
        assert sent_message.is_draft is False
        assert sent_message.sent_at is not None

        # Assert the message and thread are created correctly
        assert models.Message.objects.count() == 2
        assert models.Thread.objects.count() == 1

        # Assert the message is correct
        assert sent_message.subject == "test reply"
        assert sent_message.thread == thread_access.thread
        assert (
            sent_message.sender.email == mailbox.local_part + "@" + mailbox.domain.name
        )
        assert sent_message.recipients.count() == 1
        assert sent_message.recipients.get().contact.email == "pierre@example.com"

        assert (
            b"In-Reply-To: <" + message.mime_id.encode("utf-8") + b">\r\n"
            in sent_message.raw_mime
        )

    @pytest.mark.parametrize(
        "thread_role",
        [
            enums.ThreadAccessRoleChoices.VIEWER,
            enums.ThreadAccessRoleChoices.EDITOR,
        ],
    )
    def test_draft_reply_without_permission_on_mailbox(
        self, mailbox, authenticated_user, thread_role
    ):
        """Create draft reply to an existing thread without permission."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        # Create a thread with a message
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=thread_role,
        )
        message = factories.MessageFactory(thread=thread_access.thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create new draft reply
        response = client.post(
            reverse("draft-message"),
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

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            enums.MailboxRoleChoices.VIEWER,
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_draft_reply_without_permission_on_thread(
        self, mailbox, authenticated_user, mailbox_role
    ):
        """Create draft reply to an existing thread without permission."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )

        # Create a thread with a message
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        message = factories.MessageFactory(thread=thread_access.thread, read_at=None)
        factories.MessageRecipientFactory(
            message=message,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create new draft reply
        response = client.post(
            reverse("draft-message"),
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

    def test_draft_reply_unauthorized(self):
        """Test draft reply unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new draft reply
        response = client.post(
            reverse("draft-message"),
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

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_update_draft_message_success(
        self,
        mailbox,
        authenticated_user,
        mailbox_role,
        draft_detail_url,
        send_url,
    ):
        """Test updating a draft message successfully."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Step 1: Create a draft message
        subject = f"test {random.randint(0, 1000000000)}"
        draft_response = client.post(
            reverse("draft-message"),
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
        assert sent_message.subject == updated_subject
        assert sent_message.is_draft is False
        assert sent_message.sent_at is not None

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
            role=enums.MailboxRoleChoices.EDITOR,
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

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            enums.MailboxRoleChoices.EDITOR,
            enums.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_update_sent_message(
        self, mailbox, authenticated_user, draft_detail_url, mailbox_role
    ):
        """Test updating an already sent message."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=mailbox_role,
        )

        # Create a thread with a sent message
        thread_access = factories.ThreadAccessFactory(
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            thread=thread_access.thread,
            is_draft=False,
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

    def test_api_email_exchange_single_thread(self, send_url):
        """Test a multi-step API email exchange results in one thread per mailbox."""
        # Setup Users and Mailboxes
        user1 = factories.UserFactory(email="user1@exchange.api")
        user2 = factories.UserFactory(email="user2@exchange.api")
        domain = factories.MailDomainFactory(name="exchange.api")
        mailbox1 = factories.MailboxFactory(local_part="user1", domain=domain)
        mailbox2 = factories.MailboxFactory(local_part="user2", domain=domain)
        factories.MailboxAccessFactory(
            mailbox=mailbox1, user=user1, role=enums.MailboxRoleChoices.EDITOR
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2, user=user2, role=enums.MailboxRoleChoices.EDITOR
        )
        addr1 = str(mailbox1)
        addr2 = str(mailbox2)
        client = APIClient()

        # --- Message 1: user1 -> user2 ---
        client.force_authenticate(user=user1)
        subject = "API Conversation Starter"
        draft1_payload = {
            "senderId": str(mailbox1.id),
            "subject": subject,
            "draftBody": "Hello User Two!",
            "to": [addr2],
        }
        draft1_response = client.post(
            reverse("draft-message"), draft1_payload, format="json"
        )
        assert draft1_response.status_code == status.HTTP_201_CREATED
        message1_id = draft1_response.data["id"]

        send1_response = client.post(
            send_url,
            {
                "messageId": message1_id,
                "senderId": str(mailbox1.id),
                "textBody": "Hello User Two!",
            },
            format="json",
        )
        assert send1_response.status_code == status.HTTP_200_OK

        # Message should be marked as sent immediately for local delivery
        message1 = models.Message.objects.get(id=message1_id)
        thread1 = message1.thread  # The thread in mailbox1
        assert thread1.accesses.filter(mailbox=mailbox1).exists()
        assert thread1.messages.count() == 1
        assert message1.is_draft is False
        assert message1.is_sender is True
        assert message1.mime_id is not None

        # Verify the received message in mailbox2
        thread2 = models.Thread.objects.get(accesses__mailbox=mailbox2)
        assert models.Thread.objects.filter(accesses__mailbox=mailbox2).count() == 1
        assert thread2.accesses.filter(mailbox=mailbox2).exists()
        assert thread2.messages.count() == 1
        message1_received = thread2.messages.first()
        assert message1_received.subject == subject
        assert message1_received.is_sender is False
        assert message1_received.is_unread is True
        assert message1_received.sender.email == addr1
        # Should use the same MIME ID
        assert message1_received.mime_id == message1.mime_id

        assert models.Thread.objects.count() == 2  # Sender + receiver threads

        # --- Message 2: user2 -> user1 (Reply) ---
        client.force_authenticate(user=user2)
        reply_subject = f"Re: {subject}"
        draft2_payload = {
            "parentId": str(
                message1_received.id
            ),  # Reply to the message user2 received
            "senderId": str(mailbox2.id),
            "subject": reply_subject,
            "draftBody": "Hi User One, thanks!",
            "to": [addr1],
        }
        draft2_response = client.post(
            reverse("draft-message"), draft2_payload, format="json"
        )
        assert draft2_response.status_code == status.HTTP_201_CREATED
        message2_id = draft2_response.data["id"]

        send2_response = client.post(
            send_url,
            {
                "messageId": message2_id,
                "senderId": str(mailbox2.id),
                "textBody": "Hi User One, thanks!",
            },
            format="json",
        )
        assert send2_response.status_code == status.HTTP_200_OK

        # Mark message as sent (local delivery)
        message2 = models.Message.objects.get(id=message2_id)

        # Verify sent message from user2
        message2.refresh_from_db()
        assert message2.is_draft is False
        assert message2.parent == message1_received
        assert message2.thread == thread2
        thread2.refresh_from_db()
        assert thread2.messages.count() == 2  # user2 sees msg1 and sent msg2

        # Verify received message in user1's mailbox
        thread1.refresh_from_db()
        assert (
            thread1.messages.count() == 2
        )  # user1 sees original sent + reply received
        message2_received = thread1.messages.exclude(id=message1.id).first()
        assert message2_received.subject == reply_subject
        assert message2_received.sender.email == addr2
        assert message2_received.is_sender is False
        assert message2_received.is_unread is True
        assert message2_received.parent == message1

        # Should use the same MIME ID
        assert message2_received.mime_id == message2.mime_id

        assert models.Thread.objects.count() == 2  # Sender + receiver threads

        # --- Message 3: user1 -> user2 (Reply to Reply) ---
        client.force_authenticate(user=user1)
        rereply_subject = f"Re: {subject}"  # Subject might stay the same
        draft3_payload = {
            "parentId": str(
                message2_received.id
            ),  # Reply to the message user1 received
            "senderId": str(mailbox1.id),
            "subject": rereply_subject,
            "draftBody": "You are welcome!",
            "to": [addr2],
        }
        draft3_response = client.post(
            reverse("draft-message"), draft3_payload, format="json"
        )
        assert draft3_response.status_code == status.HTTP_201_CREATED
        message3_id = draft3_response.data["id"]

        send3_response = client.post(
            send_url,
            {"messageId": message3_id, "senderId": str(mailbox1.id)},
            format="json",
        )
        assert send3_response.status_code == status.HTTP_200_OK

        assert models.Thread.objects.count() == 2  # Still only 2 threads

        # Mark message as sent (local delivery)
        message3 = models.Message.objects.get(id=message3_id)
        thread1.refresh_from_db()
        assert thread1.messages.count() == 3  # user1 sees msg1, msg2_received, msg3
        assert message3.is_draft is False
        assert message3.is_sender is True
        assert message3.parent == message2_received

        # Verify received message in user2's mailbox
        thread2.refresh_from_db()
        assert (
            thread2.messages.count() == 3
        )  # User2 sees msg1_received, msg2, msg3_received
        message3_received = thread2.messages.exclude(
            id__in=[message1_received.id, message2.id]
        ).first()
        assert message3_received.subject == rereply_subject
        assert message3_received.sender.email == addr1
        assert message3_received.is_sender is False
        assert message3_received.is_unread is True
        assert message3_received.mime_id == message3.mime_id
        assert message3_received.parent == message2

        # Final check: Still only one thread per mailbox
        assert models.Thread.objects.filter(accesses__mailbox=mailbox1).count() == 1
        assert models.Thread.objects.filter(accesses__mailbox=mailbox2).count() == 1
