"""Test API messages create."""
# pylint: disable=redefined-outer-name

import uuid

from django.conf import settings

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models


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


@pytest.fixture
def sender(authenticated_user):
    """Create a contact for the authenticated user, required to send a message."""
    return factories.ContactFactory(
        name="Julie Dupont", user=authenticated_user, email="julie@example.com"
    )


@pytest.mark.django_db
class TestApiMessageNewCreate:
    """Test API messages create."""

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.SEND],
    )
    def test_create_message_success(
        self, permission, mailbox, sender, authenticated_user
    ):
        """Test create first message without existing thread."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create new message without existing thread.
        # This is the first message of the thread!
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "senderId": sender.id,
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
                "cc": ["paul@example.com"],
                "bcc": ["jean@example.com"],
            },
            format="json",
        )
        # Assert the response is successful
        assert response.status_code == status.HTTP_201_CREATED
        # Assert the message and thread are created
        assert models.Message.objects.count() == 1
        assert models.Thread.objects.count() == 1
        # Assert the message is correct
        message = models.Message.objects.get(id=response.data["id"])
        assert message.subject == "test"
        assert len(message.raw_mime) > 0
        assert b"test" in message.raw_mime
        assert message.get_parsed_field("textBody")[0]["content"] == "test"
        assert message.get_parsed_field("htmlBody")[0]["content"] == "<p>test</p>"

        assert message.sender.email == authenticated_user.email
        recipient_to = message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.TO
        ).get()
        assert recipient_to.contact.email == "pierre@example.com"
        recipient_cc = message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.CC
        ).get()
        assert recipient_cc.contact.email == "paul@example.com"
        recipient_bcc = message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.BCC
        ).get()
        assert recipient_bcc.contact.email == "jean@example.com"
        # Assert the thread is correct
        thread = models.Thread.objects.get(id=response.data["thread"])
        assert thread.mailbox == mailbox
        assert thread.subject == "test"
        assert thread.snippet == "test"
        assert thread.messages.count() == 1
        assert thread.messages.get().id == message.id

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.READ, enums.MailboxPermissionChoices.EDIT],
    )
    def test_create_message_without_permission_required(
        self, permission, mailbox, sender, authenticated_user
    ):
        """Test create message without permission required."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
        )
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new message
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "senderId": sender.id,
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_message_not_allowed(self, authenticated_user):
        """Test create message not allowed."""
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a new message
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "senderId": uuid.uuid4(),
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is forbidden, there is no mailbox access
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_message_unauthorized(self):
        """Test create message unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new message
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "senderId": uuid.uuid4(),
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is unauthorized
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestApiMessageReply:
    """Test API message reply."""

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.SEND],
    )
    def test_reply_success(self, permission, mailbox, sender, authenticated_user):
        """Create message replying to an existing message in a thread."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
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
        # Create new message with existing thread
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "parentId": message.id,  # ID of the message we're replying to
                "senderId": sender.id,
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        # Assert the message and thread are created
        assert models.Message.objects.count() == 2
        assert models.Thread.objects.count() == 1
        # Assert the message is correct
        message = models.Message.objects.get(id=response.data["id"])
        assert message.subject == "test"
        assert message.thread == thread
        # assert message.parent == message
        assert message.sender.email == sender.email
        assert message.recipients.count() == 1
        assert message.recipients.get().contact.email == "pierre@example.com"

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.READ, enums.MailboxPermissionChoices.EDIT],
    )
    def test_reply_without_permission(
        self, permission, mailbox, sender, authenticated_user
    ):
        """Create message replying to an existing thread without permission."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=permission,
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
        # Create new message with existing thread
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "parentId": message.id,
                "senderId": sender.id,
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_reply_unauthorized(self):
        """Test reply unauthorized."""
        # Create a client
        client = APIClient()
        # No one is authenticated
        # Try to create a new message
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "parentId": uuid.uuid4(),
                "senderId": uuid.uuid4(),
                "subject": "test",
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
