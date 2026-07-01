"""Tests for MTA API endpoints."""

# pylint: disable=too-many-positional-arguments,too-many-lines

import datetime
import hashlib
import json
from unittest.mock import ANY, patch

from django.conf import settings
from django.test import override_settings

import jwt
import pytest
from rest_framework import status
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


@pytest.fixture(name="jwt_token_without_exp")
def fixture_jwt_token_without_exp():
    """Return a valid JWT token for the sample email without exp."""

    def _get_jwt_token(body, metadata):
        body_hash = hashlib.sha256(body).hexdigest()
        payload = {
            "body_hash": body_hash,
            **metadata,
        }
        return jwt.encode(payload, settings.MDA_API_SECRET, algorithm="HS256")

    return _get_jwt_token


@pytest.mark.django_db
class TestMTAInboundEmail:
    """Test the MTA inbound email endpoint."""

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    @pytest.mark.django_db
    def test_valid_email_submission(
        self,
        mock_parse,
        mock_deliver,
        api_client: APIClient,
        sample_email,
        valid_jwt_token,
    ):
        """Test submitting a valid email calls the delivery service correctly."""
        parsed_email_mock = {
            "subject": "Test Email",
            "from": [{"email": "sender@example.com"}],
            "to": [],
        }
        mock_parse.return_value = parsed_email_mock
        mock_deliver.return_value = True

        mailbox = factories.MailboxFactory()
        email = f"{mailbox.local_part}@{mailbox.domain.name}"

        recipients = [email, "another@example.com"]
        token = valid_jwt_token(sample_email, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok", "delivered": len(recipients)}

        # The MTA path bakes an authoritative Return-Path (envelope MAIL FROM;
        # "<>" for the null sender here) onto the bytes before parsing.
        mock_parse.assert_called_once_with(b"Return-Path: <>\r\n" + sample_email)

        assert mock_deliver.call_count == len(recipients)
        # Each recipient gets the SMTP envelope (origin=mta + per-recipient
        # RCPT TO); the SMTP fields default to "" when the MTA omits them.
        for rcpt in recipients:
            mock_deliver.assert_any_call(
                rcpt,
                ANY,
                b"Return-Path: <>\r\n" + sample_email,
                envelope={
                    "origin": "mta",
                    "mail_from": "",
                    "ip": "",
                    "helo": "",
                    "hostname": "",
                    "rcpt_to": rcpt,
                },
            )

        first_call_args = mock_deliver.call_args_list[0][0]
        second_call_args = mock_deliver.call_args_list[1][0]
        assert first_call_args[1]["subject"] == "Test Email"
        assert second_call_args[1]["subject"] == "Test Email"

    @patch("core.mda.inbound.process_inbound_message_task.delay")
    def test_multi_recipient_delivery_shares_one_blob(
        self, _mock_delay, api_client, valid_jwt_token
    ):
        """One body delivered to N recipients dedups to a single blob — E2E
        through the MTA endpoint.

        The endpoint fans one ``deliver`` (N ``original_recipients``, one body)
        out to N ``InboundMessage`` rows; blob-at-ingest dedups the identical
        bytes by content hash to ONE blob, while each row keeps its own
        per-recipient RCPT envelope. Nothing duplicated, nothing in plaintext.
        """
        mailboxes = [factories.MailboxFactory() for _ in range(3)]
        recipients = [f"{m.local_part}@{m.domain.name}" for m in mailboxes]
        raw = b"From: sender@example.com\r\nSubject: shared\r\n\r\nbody"
        token = valid_jwt_token(raw, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=raw,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        rows = list(models.InboundMessage.objects.all())
        assert len(rows) == 3
        assert len({r.blob_id for r in rows}) == 1  # one shared blob
        assert {r.envelope["rcpt_to"] for r in rows} == set(recipients)

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    def test_forged_return_path_stripped_and_rewritten(
        self, mock_parse, mock_deliver, api_client, valid_jwt_token
    ):
        """A sender-supplied Return-Path is stripped and replaced with our
        authoritative one (the real envelope MAIL FROM) at ingest."""
        mock_parse.return_value = {"subject": "t"}
        mock_deliver.return_value = True
        forged = (
            b"Return-Path: <evil@attacker.test>\r\n"
            b"From: s@example.com\r\nSubject: t\r\n\r\nbody"
        )
        token = valid_jwt_token(
            forged,
            {
                "original_recipients": ["rcpt@example.com"],
                "sender": "real@example.com",
            },
        )

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=forged,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        baked = mock_parse.call_args[0][0]
        # Our authoritative Return-Path is first; the forged one is gone.
        assert baked.startswith(b"Return-Path: <real@example.com>\r\n")
        assert b"evil@attacker.test" not in baked
        # The MAIL FROM also reaches the envelope for the pipeline.
        assert mock_deliver.call_args[1]["envelope"]["mail_from"] == "real@example.com"

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    def test_partial_client_metadata_skips_received_no_error(
        self, mock_parse, mock_deliver, api_client, valid_jwt_token
    ):
        """Regression: ``client_helo`` present but ``client_hostname`` /
        ``client_address`` absent must not raise a KeyError. The partial trace
        is skipped (no ``Received`` baked), delivery still succeeds."""
        mock_parse.return_value = {"subject": "t"}
        mock_deliver.return_value = True
        raw = b"From: s@example.com\r\nSubject: t\r\n\r\nbody"
        token = valid_jwt_token(
            raw,
            {
                "original_recipients": ["rcpt@example.com"],
                "sender": "real@example.com",
                "client_helo": "mail.example.com",
                # client_hostname / client_address deliberately omitted.
            },
        )

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=raw,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        baked = mock_parse.call_args[0][0]
        # Return-Path is still baked; no malformed Received line was added.
        assert baked.startswith(b"Return-Path: <real@example.com>\r\n")
        assert b"Received:" not in baked

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    def test_email_parse_failure(
        self,
        mock_parse,
        mock_deliver,
        api_client: APIClient,
        sample_email,
        valid_jwt_token,
    ):
        """Test that if email parsing fails, a 400 is returned."""
        mock_parse.return_value = None

        email = "recipient@example.com"
        token = valid_jwt_token(sample_email, {"original_recipients": [email]})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"status": "error", "detail": "Failed to parse email"}
        # The MTA path bakes an authoritative Return-Path (envelope MAIL FROM;
        # "<>" for the null sender here) onto the bytes before parsing.
        mock_parse.assert_called_once_with(b"Return-Path: <>\r\n" + sample_email)
        mock_deliver.assert_not_called()  # Delivery should not be attempted

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    def test_delivery_partial_failure(
        self,
        mock_parse,
        mock_deliver,
        api_client: APIClient,
        sample_email,
        valid_jwt_token,
    ):
        """Test that if some deliveries fail, a 207 is returned."""
        parsed_email_mock = {"subject": "Test"}
        mock_parse.return_value = parsed_email_mock

        recipients = ["success@example.com", "fail@example.com"]
        # Make deliver return False for the second recipient
        mock_deliver.side_effect = [True, False]  # First succeeds, second fails

        token = valid_jwt_token(sample_email, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_207_MULTI_STATUS
        # Check the specific response structure
        expected_results = {
            "success@example.com": "Success",
            "fail@example.com": "Failed",
        }
        assert response.json() == {
            "status": "partial_success",
            "delivered": 1,
            "failed": 1,
            "results": expected_results,
        }
        # The MTA path bakes an authoritative Return-Path (envelope MAIL FROM;
        # "<>" for the null sender here) onto the bytes before parsing.
        mock_parse.assert_called_once_with(b"Return-Path: <>\r\n" + sample_email)
        assert mock_deliver.call_count == 2  # Called for both recipients

    @patch("core.api.viewsets.inbound.mta.deliver_inbound_message")
    @patch("core.api.viewsets.inbound.mta.parse_email")
    def test_delivery_total_failure(
        self,
        mock_parse,
        mock_deliver,
        api_client: APIClient,
        sample_email,
        valid_jwt_token,
    ):
        """Test that if all deliveries fail, a 500 is returned."""
        parsed_email_mock = {"subject": "Test"}
        mock_parse.return_value = parsed_email_mock
        mock_deliver.return_value = False  # Fails for all calls

        recipients = ["fail1@example.com", "fail2@example.com"]
        token = valid_jwt_token(sample_email, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        expected_results = {
            "fail1@example.com": "Failed",
            "fail2@example.com": "Failed",
        }
        assert response.json() == {
            "status": "error",
            "detail": "Failed to deliver message to any recipient",
            "results": expected_results,
        }
        # The MTA path bakes an authoritative Return-Path (envelope MAIL FROM;
        # "<>" for the null sender here) onto the bytes before parsing.
        mock_parse.assert_called_once_with(b"Return-Path: <>\r\n" + sample_email)
        assert mock_deliver.call_count == 2  # Called for both

    def test_invalid_content_type(
        self, api_client: APIClient, sample_email, valid_jwt_token
    ):
        """Test that submitting with an incorrect content type fails (415)."""
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,  # Data format doesn't matter if content type rejected
            content_type="application/json",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        # Assert 415 Unsupported Media Type due to strict check
        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_missing_auth_header(self, api_client: APIClient, sample_email):
        """Test that submitting without an authorization header fails."""
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_token(self, api_client: APIClient, sample_email):
        """Test that submitting with an invalid JWT token fails."""
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION="Bearer invalid_token",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_jwt_token_without_exp(
        self, api_client: APIClient, sample_email, jwt_token_without_exp
    ):
        """Test that submitting with an invalid JWT token fails."""

        http_token = jwt_token_without_exp(
            sample_email, {"original_recipients": ["recipient@example.com"]}
        )
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {http_token}",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_mismatched_body_hash(
        self, api_client: APIClient, sample_email, valid_jwt_token
    ):
        """Test that submitting with a JWT token whose hash doesn't match the body fails."""
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=sample_email + b"\n one more line",
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(sample_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @override_settings(MAX_INCOMING_EMAIL_SIZE=1024)  # Set a small limit (1 KB)
    def test_incoming_email_size_limit_exceeded(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test that incoming emails exceeding the size limit are rejected."""
        # Create a large email body (2 KB) that exceeds the limit
        large_email_body = (
            b"From: sender@example.com\r\n"
            b"To: recipient@example.com\r\n"
            b"Subject: Large Email Test\r\n"
            b"\r\n" + b"x" * 2048  # Large body content
        )

        recipients = ["recipient@example.com"]
        token = valid_jwt_token(large_email_body, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=large_email_body,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        # Should fail with 413 Request Entity Too Large
        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        assert response.json()["status"] == "error"
        assert "exceeds maximum allowed size" in response.json()["detail"]

    @override_settings(MAX_INCOMING_EMAIL_SIZE=10240)  # Set limit to 10 KB
    def test_incoming_email_within_size_limit(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test that incoming emails within the size limit are accepted."""
        mailbox = factories.MailboxFactory()
        email = f"{mailbox.local_part}@{mailbox.domain.name}"

        # Create an email body (5 KB) that is within the limit
        email_body = (
            f"From: sender@example.com\r\n"
            f"To: {email}\r\n"
            f"Subject: Normal Email Test\r\n"
            f"\r\n"
        ).encode("utf-8") + b"x" * 5000

        recipients = [email]
        token = valid_jwt_token(email_body, {"original_recipients": recipients})

        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=email_body,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        # Should succeed
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok", "delivered": 1}


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
            "/api/v1.0/inbound/mta/check/",
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
            "/api/v1.0/inbound/mta/check/",
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
            "/api/v1.0/inbound/mta/deliver/",
            data=formatted_email,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=(
                f"Bearer {valid_jwt_token(formatted_email, {'original_recipients': ['recipient@example.com']})}"
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok", "delivered": 1}

        # Check that contacts were created with correct names and emails
        sender = models.Contact.objects.get(email="sender@example.com", mailbox=mailbox)
        assert sender.name == "John Doe"

        recipient = models.Contact.objects.get(
            email="recipient@example.com", mailbox=mailbox
        )
        assert recipient.name == "Jane Smith"

        # Check for the second recipient
        user2 = models.Contact.objects.get(email="user2@example.com", mailbox=mailbox)
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
class TestMTAJWTHardening:
    """exp / body-hash guards on the shared-secret-authenticated MTA JWT."""

    @staticmethod
    def _token(body):
        """Mint a token binding the given body, signed with the shared secret."""
        payload = {
            "body_hash": hashlib.sha256(body).hexdigest(),
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30),
            "original_recipients": ["recipient@example.com"],
        }
        return jwt.encode(payload, settings.MDA_API_SECRET, algorithm="HS256")

    def test_body_hash_enforced_on_empty_body(self, api_client):
        """body_hash is checked even when the request body is empty.

        A token minted for a non-empty body but presented with an empty body
        must fail — closing the old ``if request.body:`` bypass that skipped
        the hash check (and let the bodyless /check path accept any token).
        """
        token = self._token(b"some real body")
        response = api_client.post(
            "/api/v1.0/inbound/mta/deliver/",
            data=b"",
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


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
            role=enums.MailboxRoleChoices.ADMIN,
        )
        self.recipient_email = f"{self.mailbox.local_part}@{self.maildomain.name}"

    def _create_initial_message(self, subject, mime_id):
        """Helper to create an initial message and thread."""
        sender_contact = factories.ContactFactory(
            mailbox=self.mailbox, email="sender@example.com"
        )
        thread = factories.ThreadFactory(subject=subject)
        factories.ThreadAccessFactory(
            mailbox=self.mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(
            thread=thread,
            subject=subject,
            sender=sender_contact,
            mime_id=mime_id,
        )
        # Create a blob for the message
        blob = factories.BlobFactory(
            mailbox=self.mailbox,
            content=b"From: sender@example.com\r\nTo: testuser@threadtest.com\r\nSubject: "
            + subject.encode("utf-8")
            + b"\r\nMessage-ID: <"
            + mime_id.encode("utf-8")
            + b">\r\n\r\nInitial body.",
            content_type="message/rfc822",
        )
        message.blob = blob
        message.save()
        # Create recipients for the initial message
        recipient_contact = factories.ContactFactory(
            mailbox=self.mailbox, email=self.recipient_email
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
            "/api/v1.0/inbound/mta/deliver/",
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
            "/api/v1.0/inbound/mta/deliver/",
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
            "/api/v1.0/inbound/mta/deliver/",
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
            "/api/v1.0/inbound/mta/deliver/",
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
            "/api/v1.0/inbound/mta/deliver/",
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
            role=enums.MailboxRoleChoices.ADMIN,
        )
        other_sender = factories.ContactFactory(
            mailbox=other_mailbox, email="other@sender.com"
        )
        other_thread = factories.ThreadFactory(subject="Other Mailbox Subject")
        factories.ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        other_mime_id = "othermailbox.msg@example.com"
        other_message = factories.MessageFactory(
            thread=other_thread,
            subject="Other Mailbox Subject",
            sender=other_sender,
            mime_id=other_mime_id,
        )
        # Create a blob for the message
        blob = factories.BlobFactory(
            mailbox=other_mailbox,
            content=b"From: other@sender.com\r\nTo: otheruser@otherdomain.com"
            + b"\r\nSubject: Other Mailbox Subject\r\nMessage-ID: <"
            + other_mime_id.encode("utf-8")
            + b">\r\n\r\nBody.",
            content_type="message/rfc822",
        )
        other_message.blob = blob
        other_message.save()
        # Add recipient for other message
        other_recipient_contact = factories.ContactFactory(
            mailbox=other_mailbox,
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
            "/api/v1.0/inbound/mta/deliver/",
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
        assert new_message.thread.accesses.get().mailbox == self.mailbox
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
            "/api/v1.0/inbound/mta/deliver/",
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
            "/api/v1.0/inbound/mta/deliver/",
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

    def test_single_email_to_multiple_mailboxes(
        self, api_client: APIClient, valid_jwt_token
    ):
        """Test sending one email TO multiple distinct mailboxes.

        Verifies that one thread is created per recipient mailbox.
        """

        # 1. Create a second mailbox in the same domain
        user2 = factories.UserFactory()
        mailbox2 = factories.MailboxFactory(
            local_part="testuser2", domain=self.maildomain
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2,
            user=self.user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2,
            user=user2,
            role=enums.MailboxRoleChoices.ADMIN,
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
            "/api/v1.0/inbound/mta/deliver/",
            data=email_body_bytes,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        # 5. Assertions
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok", "delivered": 2}

        # Should create exactly one thread per mailbox
        assert models.Thread.objects.count() == 2
        # Should create exactly one message copy per delivery
        assert models.Message.objects.count() == 2

        # Verify message 1 in mailbox 1
        msg1 = models.Message.objects.filter(
            thread__accesses__mailbox=self.mailbox
        ).first()
        assert msg1 is not None
        assert msg1.subject == email_subject
        assert msg1.thread.accesses.get().mailbox == self.mailbox

        # Verify message 2 in mailbox 2
        msg2 = models.Message.objects.filter(thread__accesses__mailbox=mailbox2).first()
        assert msg2 is not None
        assert msg2.subject == email_subject
        assert msg2.thread.accesses.get().mailbox == mailbox2

        # Verify they are different message instances in different threads
        assert msg1.id != msg2.id
        assert msg1.thread.id != msg2.thread.id
