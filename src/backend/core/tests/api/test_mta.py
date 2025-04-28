"""Tests for MTA API endpoints."""

import datetime
import hashlib
import json

from django.conf import settings
from django.test import override_settings

import jwt
import pytest
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from core import enums, factories, models


@pytest.fixture(name="api_client")
def fixture_api_client():
    """Return an API client."""
    return APIClient()


@pytest.fixture(name="sample_email")
def fixture_sample_email():
    """Return a sample email in RFC822 format."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""


@pytest.fixture(name="html_email")
def fixture_html_email():
    """Return a sample email with HTML content."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: HTML Test Email
Content-Type: text/html; charset="UTF-8"

<html><body><h1>Test HTML Email</h1><p>This is a <b>formatted</b> email.</p></body></html>
"""


@pytest.fixture(name="multipart_email")
def fixture_multipart_email():
    """Return a multipart email with both text and HTML parts."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Multipart Test Email
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary-string"

--boundary-string
Content-Type: text/plain; charset="UTF-8"

This is the plain text version.

--boundary-string
Content-Type: text/html; charset="UTF-8"

<html><body><h1>Multipart Email</h1><p>This is the <b>HTML version</b>.</p></body></html>

--boundary-string--
"""


@pytest.fixture(name="formatted_email")
def fixture_formatted_email():
    """Return a sample email with formatted From/To addresses."""
    return b"""From: John Doe <sender@example.com>
To: Jane Smith <recipient@example.com>, Another User <user2@example.com>
Subject: Email with Formatted Addresses

Testing formatted email addresses.
"""


@pytest.fixture(name="valid_jwt_token")
def fixture_valid_jwt_token():
    """Return a valid JWT token for the sample email."""

    def _get_jwt_token(body, metadata):
        body_hash = hashlib.sha256(body).hexdigest()
        payload = {
            "body_hash": body_hash,
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30),
            **metadata,
        }
        return jwt.encode(payload, settings.MDA_API_SECRET, algorithm="HS256")

    return _get_jwt_token


@pytest.mark.django_db
class TestMTAInboundEmail:
    """Test the MTA inbound email endpoint."""

    @pytest.mark.django_db
    def test_valid_email_submission(
        self, api_client: APIClient, sample_email, valid_jwt_token
    ):
        """Test submitting a valid email and verify serialized output."""

        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        email = f"{mailbox.local_part}@{mailbox.domain.name}"

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': [email]})}"
            ),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Verify database state
        assert models.Message.objects.count() == 1
        assert models.Thread.objects.count() == 1
        message = models.Message.objects.first()
        assert message.subject == "Test Email"
        assert message.raw_mime == sample_email

        # Verify API serialization
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("messages-list"), query_params={"thread_id": message.thread.id}
        )
        assert response.status_code == status.HTTP_200_OK
        serialized_data_all = response.json()
        assert serialized_data_all["count"] == 1
        assert serialized_data_all["results"][0]["id"] == str(message.id)
        serialized_data = serialized_data_all["results"][0]

        assert serialized_data["subject"] == "Test Email"
        assert len(serialized_data["textBody"]) == 1
        assert serialized_data["textBody"][0]["type"] == "text/plain"
        assert "This is a test email body." in serialized_data["textBody"][0]["content"]
        assert serialized_data["htmlBody"] == []
        assert len(serialized_data["to"]) == 1
        assert serialized_data["to"][0]["email"] == "recipient@example.com"
        assert serialized_data["cc"] == []
        assert serialized_data["bcc"] == []
        assert serialized_data["sender"]["email"] == "sender@example.com"

    def test_invalid_content_type(
        self, api_client: APIClient, sample_email, valid_jwt_token
    ):
        """Test that submitting with an incorrect content type fails."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="application/json",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_auth_header(self, api_client: APIClient, sample_email):
        """Test that submitting without an authorization header fails."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_token(self, api_client: APIClient, sample_email):
        """Test that submitting with an invalid JWT token fails."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION="Bearer invalid_token",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mismatched_body_hash(
        self, api_client: APIClient, sample_email, valid_jwt_token
    ):
        """Test that submitting with a JWT token whose hash doesn't match the body fails."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email + b"\n one more line",
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.django_db
    def test_html_email_submission(
        self, api_client: APIClient, html_email, valid_jwt_token
    ):
        """Test submitting an HTML-only email and verify serialization."""

        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        email = f"{mailbox.local_part}@{mailbox.domain.name}"

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=html_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(html_email, {'original_recipients': [email]})}"
            ),
        )
        assert response.status_code == status.HTTP_200_OK

        message = models.Message.objects.first()
        assert message is not None
        assert message.subject == "HTML Test Email"
        assert message.raw_mime == html_email

        # Verify API serialization
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": message.thread.id}
        )
        assert response.status_code == status.HTTP_200_OK
        serialized_data_all = response.json()
        assert serialized_data_all["count"] == 1
        assert serialized_data_all["results"][0]["id"] == str(message.id)
        serialized_data = serialized_data_all["results"][0]

        assert serialized_data["textBody"] == []
        assert len(serialized_data["htmlBody"]) == 1
        assert serialized_data["htmlBody"][0]["type"] == "text/html"
        assert "<h1>Test HTML Email</h1>" in serialized_data["htmlBody"][0]["content"]
        assert len(serialized_data["to"]) == 1
        assert serialized_data["to"][0]["email"] == "recipient@example.com"

    @pytest.mark.django_db
    def test_multipart_email_submission(
        self, api_client: APIClient, multipart_email, valid_jwt_token
    ):
        """Test submitting a multipart email (text and HTML) and verify serialization."""

        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        email = f"{mailbox.local_part}@{mailbox.domain.name}"

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=multipart_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(multipart_email, {'original_recipients': [email]})}"
            ),
        )
        assert response.status_code == status.HTTP_200_OK

        message = models.Message.objects.first()
        assert message is not None
        assert message.subject == "Multipart Test Email"
        assert message.raw_mime == multipart_email

        # Verify API serialization
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("messages-list"), query_params={"thread_id": message.thread.id}
        )
        assert response.status_code == status.HTTP_200_OK
        serialized_data_all = response.json()
        assert serialized_data_all["count"] == 1
        assert serialized_data_all["results"][0]["id"] == str(message.id)
        serialized_data = serialized_data_all["results"][0]

        assert len(serialized_data["textBody"]) == 1
        assert (
            "This is the plain text version."
            in serialized_data["textBody"][0]["content"]
        )
        assert len(serialized_data["htmlBody"]) == 1
        assert "<h1>Multipart Email</h1>" in serialized_data["htmlBody"][0]["content"]
        assert len(serialized_data["to"]) == 1
        assert serialized_data["to"][0]["email"] == "recipient@example.com"


@pytest.mark.django_db
class TestMTACheckRecipients:
    """Test the MTA check recipients endpoint."""

    @override_settings(MESSAGES_TESTDOMAIN="testdomain.com")
    def test_check_recipients(self, api_client, valid_jwt_token):
        """Test checking recipients with valid JWT token."""

        # Create a recipient maildomain
        maildomain = models.MailDomain.objects.create(name="validdomain.com")
        models.Mailbox.objects.create(local_part="recipient", domain=maildomain)

        body = json.dumps(
            {
                "addresses": [
                    "recipient@validdomain.com",
                    "recipient@testdomain.com",
                    "recipient@invaliddomain.com",
                    "recipient@not.validdomain.com",
                    "recipient@sub.testdomain.com",
                ]
            }
        ).encode("utf-8")
        token = valid_jwt_token(body, {})

        response = api_client.post(
            "/api/v1.0/mta/check-recipients/",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "recipient@validdomain.com": True,
            "recipient@testdomain.com": True,
            "recipient@invaliddomain.com": False,
            "recipient@not.validdomain.com": False,
            "recipient@sub.testdomain.com": False,
        }

    def test_check_recipients_invalid_token(self, api_client, valid_jwt_token):
        """Test checking recipients with invalid JWT token."""

        body = json.dumps({"addresses": ["recipient@example.com"]}).encode("utf-8")
        token = valid_jwt_token(body, {}) + "invalid"

        response = api_client.post(
            "/api/v1.0/mta/check-recipients/",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestEmailAddressParsing:
    """Test email address parsing functionality"""

    def test_formatted_email_addresses(
        self, api_client, formatted_email, valid_jwt_token
    ):
        """Test that emails with formatted addresses (Name <email>) are parsed correctly."""
        # Create the maildomain
        domain = models.MailDomain.objects.create(name="example.com")

        # Create the recipient mailbox
        mailbox = models.Mailbox.objects.create(local_part="recipient", domain=domain)
        # Also create for the other recipient mentioned in the 'To' header if needed by logic
        models.Mailbox.objects.create(local_part="user2", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=formatted_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(formatted_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Check that contacts were created with correct names and emails
        sender = models.Contact.objects.get(email="sender@example.com", owner=mailbox)
        assert sender.name == "John Doe"

        recipient = models.Contact.objects.get(
            email="recipient@example.com", owner=mailbox
        )
        assert recipient.name == "Jane Smith"

        # Check for the second recipient
        user2 = models.Contact.objects.get(email="user2@example.com", owner=mailbox)
        assert user2.name == "Another User"

        # Verify message recipients
        message = models.Message.objects.first()
        recipients = models.MessageRecipient.objects.filter(message=message)
        # Should have recipients based on the 'To' header: Jane Smith and Another User
        assert recipients.count() == 2
        assert recipients.filter(
            contact=recipient, type=models.MessageRecipientTypeChoices.TO
        ).exists()
        assert recipients.filter(
            contact=user2, type=models.MessageRecipientTypeChoices.TO
        ).exists()


@pytest.mark.django_db
class TestMTAInboundEmailThreading:
    """Test the threading logic for MTA inbound emails."""

    user: models.User
    maildomain: models.MailDomain
    mailbox: models.Mailbox
    recipient_email: str

    @pytest.fixture(autouse=True)
    def setup_mailbox(self, db):  # pylint: disable=unused-argument
        """Set up a common user, mailbox, and domain for threading tests."""
        self.user = factories.UserFactory()
        self.maildomain = factories.MailDomainFactory(name="threadtest.com")
        self.mailbox = factories.MailboxFactory(
            local_part="testuser", domain=self.maildomain
        )
        factories.MailboxAccessFactory(
            mailbox=self.mailbox,
            user=self.user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        self.recipient_email = f"{self.mailbox.local_part}@{self.maildomain.name}"

    def _create_initial_message(self, subject, mime_id):
        """Helper to create an initial message and thread."""
        sender_contact = factories.ContactFactory(
            owner=self.mailbox, email="sender@example.com"
        )
        thread = factories.ThreadFactory(mailbox=self.mailbox, subject=subject)
        message = factories.MessageFactory(
            thread=thread,
            subject=subject,
            sender=sender_contact,
            mime_id=mime_id,
            raw_mime=b"From: sender@example.com\r\nTo: testuser@threadtest.com\r\nSubject: "
            + subject.encode("utf-8")
            + b"\r\nMessage-ID: <"
            + mime_id.encode("utf-8")
            + b">\r\n\r\nInitial body.",
        )
        # Create recipients for the initial message
        recipient_contact = factories.ContactFactory(
            owner=self.mailbox, email=self.recipient_email
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=recipient_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )
        return thread, message

    def _create_reply_email(  # pylint: disable=too-many-positional-arguments
        self,
        to_email,
        subject,
        in_reply_to=None,
        references=None,
        from_email="reply@example.com",
    ):
        """Helper to construct a reply email in RFC822 format."""
        headers = f"From: {from_email}\r\nTo: {to_email}\r\nSubject: {subject}\r\n"
        if in_reply_to:
            headers += f"In-Reply-To: <{in_reply_to}>\r\n"
        if references:
            ref_str = " ".join([f"<{ref}>" for ref in references])
            headers += f"References: {ref_str}\r\n"
        body = f"{headers}\r\nThis is a reply body."
        return body.encode("utf-8")

    def test_reply_matches_thread_via_in_reply_to(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test a reply is threaded correctly using In-Reply-To and matching subject."""
        initial_subject = "Original Thread Subject"
        initial_mime_id = "original.123@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = f"Re: {initial_subject}"
        reply_email_bytes = self._create_reply_email(
            self.recipient_email, reply_subject, in_reply_to=initial_mime_id
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 1  # No new thread created
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread == initial_thread
        assert new_message.subject == reply_subject

    def test_reply_matches_thread_via_references(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test a reply is threaded correctly using References and matching subject."""
        initial_subject = "Another Subject"
        initial_mime_id = "original.456@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = f"Re: {initial_subject}"
        reply_email_bytes = self._create_reply_email(
            self.recipient_email,
            reply_subject,
            references=[initial_mime_id, "other.ref@example.com"],
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread == initial_thread
        assert new_message.subject == reply_subject

    def test_reply_matches_thread_different_subject_prefix(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test reply threads correctly even with different prefixes (Fwd:)."""
        initial_subject = "Project Update"
        initial_mime_id = "project.update.1@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = f"Fwd: {initial_subject}"  # Note: Fwd prefix
        reply_email_bytes = self._create_reply_email(
            self.recipient_email, reply_subject, in_reply_to=initial_mime_id
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread == initial_thread
        assert new_message.subject == reply_subject

    def test_reply_creates_new_thread_different_subject(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test reply creates a new thread if the canonical subject differs."""
        initial_subject = "Important Meeting"
        initial_mime_id = "meeting.789@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = "Completely Different Topic"  # Subject changed
        reply_email_bytes = self._create_reply_email(
            self.recipient_email, reply_subject, in_reply_to=initial_mime_id
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 2  # New thread created
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread != initial_thread
        assert new_message.subject == reply_subject

    def test_reply_creates_new_thread_no_matching_reference(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test reply creates a new thread if In-Reply-To/References don't match any message."""
        initial_subject = "Existing Conversation"
        initial_mime_id = "conv.1@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = f"Re: {initial_subject}"
        reply_email_bytes = self._create_reply_email(
            self.recipient_email,
            reply_subject,
            in_reply_to="nonexistent.id@example.com",  # No matching ID
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 2  # New thread created
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread != initial_thread
        assert new_message.subject == reply_subject

    def test_reply_creates_new_thread_reference_in_different_mailbox(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test reply creates a new thread if referenced message is in another mailbox."""
        # Create initial message in the main mailbox
        initial_subject = "Mailbox 1 Subject"
        initial_mime_id = "mailbox1.msg@example.com"
        initial_thread = self._create_initial_message(initial_subject, initial_mime_id)[
            0
        ]

        # Create a second mailbox and a message within it
        other_maildomain = factories.MailDomainFactory(name="otherdomain.com")
        other_mailbox = factories.MailboxFactory(
            local_part="otheruser", domain=other_maildomain
        )
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=self.user,
            permission=enums.MailboxPermissionChoices.ADMIN,
        )
        other_sender = factories.ContactFactory(
            owner=other_mailbox, email="other@sender.com"
        )
        other_thread = factories.ThreadFactory(
            mailbox=other_mailbox, subject="Other Mailbox Subject"
        )
        other_mime_id = "othermailbox.msg@example.com"
        other_message = factories.MessageFactory(
            thread=other_thread,
            subject="Other Mailbox Subject",
            sender=other_sender,
            mime_id=other_mime_id,
            raw_mime=b"From: other@sender.com\r\nTo: otheruser@otherdomain.com"
            + b"\r\nSubject: Other Mailbox Subject\r\nMessage-ID: <"
            + other_mime_id.encode("utf-8")
            + b">\r\n\r\nBody.",
        )
        # Add recipient for other message
        other_recipient_contact = factories.ContactFactory(
            owner=other_mailbox,
            email=f"{other_mailbox.local_part}@{other_maildomain.name}",
        )
        factories.MessageRecipientFactory(
            message=other_message,
            contact=other_recipient_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )

        # Create a reply intended for the *first* mailbox, but referencing the message in the *second* mailbox
        reply_subject = "Re: Other Mailbox Subject"  # Subject matches the other thread
        reply_email_bytes = self._create_reply_email(
            self.recipient_email,  # Send to the first mailbox
            reply_subject,
            in_reply_to=other_mime_id,  # Reference message in the second mailbox
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        # Should be 3 threads: initial one in mailbox1, one in mailbox2, and a *new* one in mailbox1
        assert models.Thread.objects.count() == 3
        assert models.Message.objects.count() == 3

        # Verify the new message is in mailbox1 and in a new thread
        new_message = models.Message.objects.latest(
            "created_at"
        )  # Assumes latest is the new one
        assert new_message.thread.mailbox == self.mailbox
        assert new_message.subject == reply_subject
        # Ensure it didn't get added to the thread in the *other* mailbox
        assert new_message.thread != other_thread
        # Ensure it didn't get added to the original thread in *this* mailbox either
        assert new_message.thread != initial_thread

    def test_message_id_header_used_for_threading(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test that Message-Id header is also checked for threading."""
        initial_subject = "Test Message ID Threading"
        initial_mime_id = "messageid.test.1@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        # Construct a reply that references the *Message-Id* of the first email in its *References* header
        reply_subject = f"Re: {initial_subject}"
        # Note: the reply *itself* will have a new Message-ID, but it references the old one.
        reply_email_bytes = self._create_reply_email(
            self.recipient_email, reply_subject, references=[initial_mime_id]
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 1  # Should reuse the thread
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread == initial_thread
        assert new_message.subject == reply_subject

    def test_reply_matches_thread_multiple_prefixes(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test threading works with multiple 'Re:' prefixes."""
        initial_subject = "Re: Fwd: Original Subject"  # Already has a prefix
        initial_mime_id = "multi.re.1@example.com"
        initial_thread, initial_message = self._create_initial_message(
            initial_subject, initial_mime_id
        )

        reply_subject = (
            f"Re: {initial_subject}"  # Becomes "Re: Re: Fwd: Original Subject"
        )
        reply_email_bytes = self._create_reply_email(
            self.recipient_email, reply_subject, in_reply_to=initial_mime_id
        )

        token = valid_jwt_token(
            reply_email_bytes, {"original_recipients": [self.recipient_email]}
        )

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=reply_email_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Thread.objects.count() == 1
        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=initial_message.id).first()
        assert new_message is not None
        assert new_message.thread == initial_thread
        assert new_message.subject == reply_subject

    def test_single_email_to_multiple_mailboxes(self, api_client: APIClient, valid_jwt_token):
        """Test sending one email TO multiple distinct mailboxes.

        Verifies that one thread is created per recipient mailbox.
        """

        # 1. Create a second mailbox in the same domain
        user2 = factories.UserFactory()
        mailbox2 = factories.MailboxFactory(
            local_part="testuser2", domain=self.maildomain
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2, user=self.user, permission=enums.MailboxPermissionChoices.ADMIN
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2, user=user2, permission=enums.MailboxPermissionChoices.ADMIN
        )
        recipient_email2 = f"{mailbox2.local_part}@{self.maildomain.name}"

        # 2. Create email body with both recipients in To header
        email_subject = "Multi-Mailbox Test"
        email_body_bytes = (
            f"From: sender@example.com\r\n"
            f"To: {self.recipient_email}, {recipient_email2}\r\n"
            f"Subject: {email_subject}\r\n"
            f"\r\n"
            f"This email is for two mailboxes."
        ).encode("utf-8")

        # 3. Prepare JWT with both original recipients
        recipients_list = [self.recipient_email, recipient_email2]
        token = valid_jwt_token(
            email_body_bytes, {"original_recipients": recipients_list}
        )

        # 4. Make the API call
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=email_body_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        # 5. Assertions
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Should create exactly one thread per mailbox
        assert models.Thread.objects.count() == 2
        # Should create exactly one message copy per delivery
        assert models.Message.objects.count() == 2

        # Verify message 1 in mailbox 1
        msg1 = models.Message.objects.filter(thread__mailbox=self.mailbox).first()
        assert msg1 is not None
        assert msg1.subject == email_subject
        assert msg1.thread.mailbox == self.mailbox

        # Verify message 2 in mailbox 2
        msg2 = models.Message.objects.filter(thread__mailbox=mailbox2).first()
        assert msg2 is not None
        assert msg2.subject == email_subject
        assert msg2.thread.mailbox == mailbox2

        # Verify they are different message instances in different threads
        assert msg1.id != msg2.id
        assert msg1.thread.id != msg2.thread.id
