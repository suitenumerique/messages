"""Test API messages create."""
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-positional-arguments

import base64
import random
import time
import uuid

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import verify as dkim_verify
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models


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


@pytest.fixture
def sender_contact(mailbox, authenticated_user):
    """Create a contact for the authenticated user, required to send a message."""
    return factories.ContactFactory(
        name=authenticated_user.full_name, owner=mailbox, email=str(mailbox)
    )


private_key_for_tests = rsa.generate_private_key(public_exponent=3, key_size=1024)


@pytest.mark.django_db
class TestApiDraftAndSendMessage:
    """Test API draft and send message endpoints."""

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
    def test_draft_and_send_message_success(
        self, mailbox, sender_contact, authenticated_user, draft_url, send_url
    ):
        """Test create draft message and then send it."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.SEND,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.EDIT,
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
                "cc": ["paul@example.com"],
                "bcc": ["jean@example.com"],
            },
            format="json",
        )

        # Assert the draft response is successful
        assert draft_response.status_code == status.HTTP_201_CREATED

        # Assert the draft message is created
        assert models.Message.objects.count() == 1
        draft_message = models.Message.objects.get(id=draft_response.data["id"])
        assert draft_message.is_draft is True
        assert draft_message.mta_sent is False

        # Step 2: Send the draft message
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
        assert models.Thread.objects.count() == 1

        # Assert the sender contact owner is ok
        assert models.Contact.objects.get(id=sender_contact.id).owner == mailbox
        # Assert recipient contacts owner is set
        assert models.Contact.objects.get(email="pierre@example.com").owner == mailbox
        assert models.Contact.objects.get(email="paul@example.com").owner == mailbox
        assert models.Contact.objects.get(email="jean@example.com").owner == mailbox

        # Assert the message is correct
        assert sent_message.subject == subject
        assert len(sent_message.raw_mime) > 0
        assert b"test" in sent_message.raw_mime

        assert sent_message.get_parsed_field("textBody")[0]["content"] == "test"
        assert sent_message.get_parsed_field("htmlBody")[0]["content"] == "<p>test</p>"

        assert sent_message.sender.email == authenticated_user.email
        recipient_to = sent_message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.TO
        ).get()
        assert recipient_to.contact.email == "pierre@example.com"
        recipient_cc = sent_message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.CC
        ).get()
        assert recipient_cc.contact.email == "paul@example.com"
        recipient_bcc = sent_message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.BCC
        ).get()
        assert recipient_bcc.contact.email == "jean@example.com"

        # Assert the thread is correct
        thread = models.Thread.objects.get(id=sent_message.thread.id)
        assert thread.mailbox == mailbox
        assert thread.subject == subject
        assert thread.snippet == "test"
        assert thread.messages.count() == 1
        assert thread.messages.get().id == sent_message.id
        assert thread.messages.get().sender.email == sender_contact.email
        assert thread.is_read is True

        # Give some time for the message to be sent to the MTA-out
        for _ in range(50):
            all_emails = requests.get("http://mailcatcher:1080/email", timeout=3).json()
            emails = [e for e in all_emails if e.get("subject") == subject]
            if len(emails) > 0:
                break
            time.sleep(0.1)

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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        # Assert the response is unauthorized
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_send_nonexistent_message(self, mailbox, authenticated_user, send_url):
        """Test sending a non-existent message."""
        # Create a client and authenticate the user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.SEND,
        )
        # Try to send a non-existent message
        response = client.post(
            send_url,
            {
                "messageId": uuid.uuid4(),
                "senderId": mailbox.id,
            },
            format="json",
        )
        # Assert the response is bad request
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_send_already_sent_message(self, mailbox, authenticated_user, send_url):
        """Test sending an already sent message."""
        # Create a mailbox access on this mailbox for the authenticated user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            permission=enums.MailboxPermissionChoices.SEND,
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

        # Try to send an already sent message
        response = client.post(
            send_url,
            {
                "messageId": message.id,
                "senderId": mailbox.id,
            },
            format="json",
        )

        # Assert the response is bad request
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestApiDraftAndSendReply:
    """Test API draft and send reply endpoints."""

    def test_draft_and_send_reply_success(
        self, mailbox, sender_contact, authenticated_user, draft_url, send_url
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
                "htmlBody": "<p>test reply</p>",
                "textBody": "test reply",
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
        assert sent_message.sender.email == sender_contact.email
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
                "to": ["pierre@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_draft_message_success(
        self, mailbox, sender_contact, authenticated_user, draft_url, draft_detail_url, send_url
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
                "htmlBody": "<p>test</p>",
                "textBody": "test",
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
                "htmlBody": "<p>updated content</p>",
                "textBody": "updated content",
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
        assert (
            updated_message.get_parsed_field("textBody")[0]["content"]
            == "updated content"
        )
        assert (
            updated_message.get_parsed_field("htmlBody")[0]["content"]
            == "<p>updated content</p>"
        )

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
        assert thread.snippet == "updated content"

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

        # Give some time for the message to be sent to the MTA-out
        for _ in range(50):
            all_emails = requests.get("http://mailcatcher:1080/email", timeout=3).json()
            emails = [e for e in all_emails if e.get("subject") == updated_subject]
            if len(emails) > 0:
                break
            time.sleep(0.1)

        assert len(emails) > 0
        recv_email = emails[0]

        # Check the email content matches the updated draft
        assert recv_email.get("text") == "updated content"
        assert recv_email.get("html") == "<p>updated content</p>"
        assert {x["address"] for x in recv_email["envelope"]["to"]} == {
            "pierre@example.com",
            "jacques@example.com",
            "paul@example.com",
        }

    def test_update_nonexistent_draft(self, mailbox, authenticated_user, draft_detail_url):
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

        # Assert the response is bad request
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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

        # Assert the response is bad request
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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
