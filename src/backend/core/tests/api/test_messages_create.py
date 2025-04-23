"""Test API messages create."""
# pylint: disable=redefined-outer-name

import base64
import random
import time
import uuid

from django.conf import settings
from django.test import override_settings

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import verify as dkim_verify
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
def sender_contact(mailbox, authenticated_user):
    """Create a contact for the authenticated user, required to send a message."""
    return factories.ContactFactory(
        name=authenticated_user.full_name, owner=mailbox, email=str(mailbox)
    )


private_key_for_tests = rsa.generate_private_key(public_exponent=3, key_size=1024)


@pytest.mark.django_db
class TestApiMessageNewCreate:
    """Test API messages create."""

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.SEND],
    )
    @override_settings(
        MESSAGES_DKIM_DOMAINS=["example.com"],
        MESSAGES_DKIM_SELECTOR="testselector",
        MESSAGES_DKIM_PRIVATE_KEY_B64=base64.b64encode(
            private_key_for_tests.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("utf-8"),
    )
    def test_create_message_success(
        self, permission, mailbox, sender_contact, authenticated_user
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
        subject = f"test {random.randint(0, 1000000000)}"
        response = client.post(
            f"/api/{settings.API_VERSION}/message-create/",
            {
                "senderId": mailbox.id,
                "subject": subject,
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
        # Assert the sender contact owner is ok
        assert models.Contact.objects.get(id=sender_contact.id).owner == mailbox
        # Assert recipient contacts owner is set
        assert models.Contact.objects.get(email="pierre@example.com").owner == mailbox
        assert models.Contact.objects.get(email="paul@example.com").owner == mailbox
        assert models.Contact.objects.get(email="jean@example.com").owner == mailbox
        # Assert the message is correct
        message = models.Message.objects.get(id=response.data["id"])
        assert message.subject == subject
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
        assert thread.subject == subject
        assert thread.snippet == "test"
        assert thread.messages.count() == 1
        assert thread.messages.get().id == message.id
        assert thread.messages.get().sender.email == sender_contact.email
        assert thread.is_read is True

        # Give some time for the message to be sent to the MTA-out
        for _ in range(50):
            all_emails = requests.get("http://mailcatcher:1080/email", timeout=3).json()
            emails = [e for e in all_emails if e.get("subject") == subject]
            if len(emails) > 0:
                break
            time.sleep(0.1)
        # print(all_emails)

        assert len(emails) > 0
        recv_email = emails[0]

        # Now we do checks on the actual email content, as received by the mailcatcher.
        assert recv_email["envelope"]["from"]["address"] == sender_contact.email
        assert {x["address"] for x in recv_email["envelope"]["to"]} == {
            "pierre@example.com",
            "paul@example.com",
            "jean@example.com",
        }
        assert recv_email.get("text") == "test"
        assert recv_email.get("html") == "<p>test</p>"
        assert set(recv_email["headers"].keys()) == {
            "dkim-signature",
            "date",
            "cc",
            "to",
            "from",
            "subject",
            "mime-version",
            "content-type",
            "message-id",
        }
        assert recv_email["headers"]["message-id"].endswith("@_lst.example.com>")

        source = requests.get(
            f"http://mailcatcher:1080/email/{recv_email['id']}/source",
            timeout=3,
        ).content
        assert b"\r\nSubject: " + subject.encode("utf-8") + b"\r\n" in source

        # Check DKIM signature, with a custom DNS function that returns the DKIM public key
        dkim_public_der = private_key_for_tests.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def get_dns_txt(fqdn, **kwargs):
            assert fqdn == b"testselector._domainkey.example.com."
            return b"v=DKIM1; p=%s" % base64.b64encode(dkim_public_der)

        assert dkim_verify(source, dnsfunc=get_dns_txt)

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.READ, enums.MailboxPermissionChoices.EDIT],
    )
    def test_create_message_without_permission_required(
        self, permission, mailbox, authenticated_user
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
                "senderId": mailbox.id,
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
    def test_reply_success(
        self, permission, mailbox, sender_contact, authenticated_user
    ):
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
                "senderId": mailbox.id,
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
        posted_message = models.Message.objects.get(id=response.data["id"])
        assert posted_message.subject == "test"
        assert posted_message.thread == thread
        # assert message.parent == message
        assert posted_message.sender.email == sender_contact.email
        assert posted_message.recipients.count() == 1
        assert posted_message.recipients.get().contact.email == "pierre@example.com"

        assert (
            b"In-Reply-To: <" + message.mime_id.encode("utf-8") + b">\r\n"
            in posted_message.raw_mime
        )

    @pytest.mark.parametrize(
        "permission",
        [enums.MailboxPermissionChoices.READ, enums.MailboxPermissionChoices.EDIT],
    )
    def test_reply_without_permission(self, permission, mailbox, authenticated_user):
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
                "senderId": mailbox.id,
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
