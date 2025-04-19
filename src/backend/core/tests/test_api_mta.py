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
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=10),
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

        # Create the maildomain
        domain = models.MailDomain.objects.create(name="example.com")

        # Create the recipient mailbox using the fetched domain
        models.Mailbox.objects.create(
            local_part="recipient",
            domain=domain,
        )

        # Check mailbox exists BEFORE posting
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        # Post the email
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    sample_email, {'original_recipients': ['recipient@example.com']}
                )
            }",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "ok"})

        # Verify database state
        self.assertEqual(models.Message.objects.count(), 1)
        self.assertEqual(models.Thread.objects.count(), 1)
        message = models.Message.objects.first()
        self.assertEqual(message.subject, "Test Email")
        self.assertEqual(message.raw_mime, sample_email)

        # Verify API serialization
        user = models.User.objects.create_user(
            username="testuser", email="test@example.com"
        )
        models.MailboxAccess.objects.create(
            user=user,
            mailbox=message.thread.mailbox,
            permission=models.MailboxPermissionChoices.READ_WRITE,
        )
        api_client.force_authenticate(user=user)

        message_url = f"/api/v1.0/messages/{message.id}/"
        response = api_client.get(message_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        serialized_data = response.json()

        self.assertEqual(serialized_data["subject"], "Test Email")
        self.assertEqual(len(serialized_data["textBody"]), 1)
        self.assertEqual(serialized_data["textBody"][0]["type"], "text/plain")
        self.assertIn(
            "This is a test email body.", serialized_data["textBody"][0]["content"]
        )
        self.assertEqual(serialized_data["htmlBody"], [])

        self.assertEqual(len(serialized_data["to"]), 1)
        self.assertEqual(serialized_data["to"][0]["email"], "recipient@example.com")
        self.assertEqual(serialized_data["to"][0]["name"], "")

        self.assertEqual(len(serialized_data["cc"]), 0)
        self.assertEqual(len(serialized_data["bcc"]), 0)

        self.assertIsNotNone(serialized_data["sender"])
        self.assertEqual(serialized_data["sender"]["email"], "sender@example.com")

    def test_invalid_content_type(self, api_client, sample_email, valid_jwt_token):
        """Test submitting with wrong content type."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    sample_email, {'original_recipients': ['recipient@example.com']}
                )
            }",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_auth_header(self, api_client, sample_email):
        """Test submitting without authorization header."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_token(self, api_client, sample_email):
        """Test submitting with invalid JWT token."""
        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION="Bearer invalid_token",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mismatched_body_hash(self, api_client, sample_email, valid_jwt_token):
        """Test submitting with JWT token containing wrong email hash."""

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=sample_email + b"\n one more line",
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    sample_email, {'original_recipients': ['recipient@example.com']}
                )
            }",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_html_email_submission(self, api_client, html_email, valid_jwt_token):
        """Test submitting an email with HTML content."""
        # Create the maildomain
        domain = models.MailDomain.objects.create(name="example.com")

        # Create the recipient mailbox
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=html_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    html_email, {'original_recipients': ['recipient@example.com']}
                )
            }",
        )

        assert response.status_code == status.HTTP_200_OK
        message = models.Message.objects.first()
        assert message is not None  # Add check message exists
        assert message.subject == "HTML Test Email"
        assert "<h1>Test HTML Email</h1>" in message.body_html
        # HTML should be used for text when no text part exists
        assert "<h1>Test HTML Email</h1>" in message.body_text

    def test_multipart_email_submission(
        self, api_client, multipart_email, valid_jwt_token
    ):
        """Test submitting a multipart email with both text and HTML parts."""
        # Create the maildomain
        domain = models.MailDomain.objects.create(name="example.com")

        # Create the recipient mailbox
        models.Mailbox.objects.create(local_part="recipient", domain=domain)
        assert models.Mailbox.objects.filter(
            local_part="recipient", domain=domain
        ).exists()

        response = api_client.post(
            "/api/v1.0/mta/inbound-email/",
            data=multipart_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    multipart_email, {'original_recipients': ['recipient@example.com']}
                )
            }",
        )

        assert response.status_code == status.HTTP_200_OK
        message = models.Message.objects.first()
        assert message is not None  # Add check message exists
        assert message.subject == "Multipart Test Email"
        assert "<h1>Multipart Email</h1>" in message.body_html
        assert "This is the plain text version." in message.body_text


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
            HTTP_AUTHORIZATION=f"Bearer {
                valid_jwt_token(
                    # Ensure JWT uses the recipient the view should process
                    formatted_email,
                    {'original_recipients': ['recipient@example.com']},
                )
            }",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Check that contacts were created with correct names and emails
        sender = models.Contact.objects.get(email="sender@example.com")
        assert sender.name == "John Doe"

        recipient = models.Contact.objects.get(email="recipient@example.com")
        assert recipient.name == "Jane Smith"

        # Check for the second recipient contact creation
        user2 = models.Contact.objects.get(email="user2@example.com")
        assert user2.name == "Another User"

        # Verify message recipients for the created message
        message = models.Message.objects.filter(
            thread__mailbox__local_part="recipient", thread__mailbox__domain=domain
        ).first()
        assert message is not None  # Check message was created for this mailbox
        recipients = models.MessageRecipient.objects.filter(message=message)
        # Should have recipients based on the 'To' header: Jane Smith and Another User
        assert recipients.count() == 2
        assert recipients.filter(
            contact=recipient, type=models.MessageRecipientTypeChoices.TO
        ).exists()
        assert recipients.filter(
            contact=user2, type=models.MessageRecipientTypeChoices.TO
        ).exists()
