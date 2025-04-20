"""Tests for MTA API endpoints."""

import datetime
import hashlib
import json

from django.conf import settings

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models


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
        domain = models.MailDomain.objects.create(name="example.com")
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': ['recipient@example.com']})}"
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
        user = models.User.objects.create_user(admin_email="test@example.com")
        models.MailboxAccess.objects.create(
            user=user,
            mailbox=message.thread.mailbox,
            permission=models.MailboxPermissionChoices.ADMIN,
        )
        api_client.force_authenticate(user=user)
        message.refresh_from_db()

        message_url = f"/api/v1.0/messages/{message.id}/"
        response = api_client.get(message_url)
        assert response.status_code == status.HTTP_200_OK
        serialized_data = response.json()

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
        domain = models.MailDomain.objects.create(name="example.com")
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=html_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(html_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        assert response.status_code == status.HTTP_200_OK

        message = models.Message.objects.first()
        assert message is not None
        assert message.subject == "HTML Test Email"
        assert message.raw_mime == html_email

        # Verify API serialization
        user = models.User.objects.create_user(admin_email="test2@example.com")
        models.MailboxAccess.objects.create(
            user=user,
            mailbox=message.thread.mailbox,
            permission=models.MailboxPermissionChoices.ADMIN,
        )
        api_client.force_authenticate(user=user)
        message.refresh_from_db()
        message_url = f"/api/v1.0/messages/{message.id}/"
        response = api_client.get(message_url)
        assert response.status_code == status.HTTP_200_OK
        serialized_data = response.json()

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
        domain = models.MailDomain.objects.create(name="example.com")
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=multipart_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(multipart_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        assert response.status_code == status.HTTP_200_OK

        message = models.Message.objects.first()
        assert message is not None
        assert message.subject == "Multipart Test Email"
        assert message.raw_mime == multipart_email

        # Verify API serialization
        user = models.User.objects.create_user(admin_email="test3@example.com")
        models.MailboxAccess.objects.create(
            user=user,
            mailbox=message.thread.mailbox,
            permission=models.MailboxPermissionChoices.ADMIN,
        )
        api_client.force_authenticate(user=user)
        message.refresh_from_db()
        message_url = f"/api/v1.0/messages/{message.id}/"
        response = api_client.get(message_url)
        assert response.status_code == status.HTTP_200_OK
        serialized_data = response.json()

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

    def test_check_recipients(self, api_client, valid_jwt_token):
        """Test checking recipients with valid JWT token."""

        body = json.dumps({"addresses": ["recipient@example.com"]}).encode("utf-8")
        token = valid_jwt_token(body, {})

        response = api_client.post(
            "/api/v1.0/mta/check-recipients/",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"recipient@example.com": True}

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
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
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
        sender = models.Contact.objects.get(email="sender@example.com")
        assert sender.name == "John Doe"

        recipient = models.Contact.objects.get(email="recipient@example.com")
        assert recipient.name == "Jane Smith"

        # Check for the second recipient
        user2 = models.Contact.objects.get(email="user2@example.com")
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
