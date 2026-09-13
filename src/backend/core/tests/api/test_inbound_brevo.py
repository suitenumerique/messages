"""Tests for Brevo inbound API endpoints."""

import hashlib
import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from core import factories, models
from core.api.viewsets.inbound.brevo import (
    BrevoAuthentication,
    convert_brevo_payload_to_parsed_email,
)


@pytest.fixture(name="api_client")
def fixture_api_client():
    """Return an API client."""
    return APIClient()


@pytest.fixture(name="channel")
def fixture_channel():
    """Create a test channel for Brevo."""
    mailbox = factories.MailboxFactory()
    return factories.ChannelFactory(
        type="brevo",
        mailbox=mailbox,
        settings={
            "config": {"enabled": True},
        },
    )


@pytest.fixture(name="channel_with_settings")
def fixture_channel_with_settings():
    """Create a test channel with settings."""
    mailbox = factories.MailboxFactory()
    return factories.ChannelFactory(
        type="brevo",
        mailbox=mailbox,
        settings={
            "config": {"enabled": True},
            "tags": [],
        },
    )


@pytest.fixture(name="brevo_payload")
def fixture_brevo_payload():
    """Return a sample Brevo webhook payload."""
    return {
        "items": [
            {
                "Uuid": ["1a825d56-029b-4a41-b8e4-61670463431b"],
                "MessageId": "<test-message-id@example.com>",
                "InReplyTo": None,
                "From": {
                    "Name": "Test Sender",
                    "Address": "sender@example.com",
                },
                "To": [
                    {
                        "Name": "Test Recipient",
                        "Address": "recipient@test.com",
                    }
                ],
                "Recipients": ["recipient@test.com"],
                "Cc": [],
                "ReplyTo": None,
                "SentAtDate": "Mon, 15 Jan 2024 10:00:00 +0000",
                "Subject": "Test Subject",
                "RawHtmlBody": "<p>Test message body</p>",
                "RawTextBody": "Test message body",
                "ExtractedMarkdownMessage": "Test message body",
                "ExtractedMarkdownSignature": "-- \nTest Sender",
                "SpamScore": 1.0,
                "Attachments": [],
                "Headers": {},
            }
        ]
    }


class TestConvertBrevoPayload:
    """Test the Brevo payload conversion function."""

    def test_convert_full_payload(self):
        """Test conversion of a full Brevo payload."""
        item = {
            "From": {"Address": "sender@example.com", "Name": "Sender"},
            "To": [{"Address": "recipient@example.com", "Name": "Recipient"}],
            "Cc": [{"Address": "cc@example.com", "Name": "CC"}],
            "Subject": "Test Subject",
            "ExtractedMarkdownMessage": "Hello World",
            "RawHtmlBody": "<p>Hello World</p>",
            "RawTextBody": "Hello World",
            "SentAtDate": "Mon, 15 Jan 2024 10:00:00 +0000",
            "InReplyTo": "<original@example.com>",
        }

        result = convert_brevo_payload_to_parsed_email(item)

        assert result["subject"] == "Test Subject"
        assert result["from"]["email"] == "sender@example.com"
        assert result["from"]["name"] == "Sender"
        assert len(result["to"]) == 1
        assert result["to"][0]["email"] == "recipient@example.com"
        assert result["in_reply_to"] == "<original@example.com>"
        assert "htmlBody" in result
        assert "textBody" in result

    def test_convert_minimal_payload(self):
        """Test conversion with minimal payload."""
        item = {
            "From": {"Address": "sender@example.com"},
            "Subject": "Test",
        }

        result = convert_brevo_payload_to_parsed_email(item)

        assert result["subject"] == "Test"
        assert result["from"]["email"] == "sender@example.com"
        assert result["from"]["name"] is None

    def test_convert_without_html_uses_markdown(self):
        """Test that markdown is used when HTML is not available."""
        item = {
            "From": {"Address": "sender@example.com"},
            "Subject": "Test",
            "ExtractedMarkdownMessage": "Markdown **content**",
        }

        result = convert_brevo_payload_to_parsed_email(item)

        assert "htmlBody" in result
        assert "**content**" in result["htmlBody"][0]["content"]


@pytest.mark.django_db
class TestBrevoAuthentication:
    """Test the BrevoAuthentication class."""

    def test_authenticate_with_valid_channel_id(self, channel):
        """Test authentication with valid channel ID."""
        auth = BrevoAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self, channel_id):
                self.headers = {"X-Channel-ID": str(channel_id)}

        request = MockRequest(channel.id)
        user, auth_data = auth.authenticate(request)

        assert user is None
        assert auth_data["channel"] == channel
        assert auth_data["auth_type"] == "channel_id"

    def test_authenticate_with_missing_channel_id(self):
        """Test authentication fails with missing credentials."""
        auth = BrevoAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                self.headers = {}

        request = MockRequest()

        with pytest.raises(AuthenticationFailed, match="Missing authentication"):
            auth.authenticate(request)

    def test_authenticate_with_invalid_channel_id(self):
        """Test authentication fails with invalid channel ID."""
        auth = BrevoAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                self.headers = {"X-Channel-ID": "invalid-uuid"}

        request = MockRequest()

        with pytest.raises(AuthenticationFailed, match="Invalid channel_id"):
            auth.authenticate(request)

    @override_settings(BREVO_WEBHOOK_SECRET="test-secret")
    def test_authenticate_with_valid_hmac(self):
        """Test authentication with valid HMAC signature."""
        auth = BrevoAuthentication()

        secret = "test-secret"
        body = b'{"items": []}'
        signature = hashlib.sha256(secret.encode() + body).hexdigest()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                self.headers = {"X-Brevo-Signature": signature}
                self.body = body

        request = MockRequest()
        user, auth_data = auth.authenticate(request)

        assert user is None
        assert auth_data["auth_type"] == "hmac"

    @override_settings(BREVO_WEBHOOK_SECRET="test-secret")
    def test_authenticate_with_invalid_hmac(self):
        """Test authentication fails with invalid HMAC signature."""
        auth = BrevoAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                self.headers = {"X-Brevo-Signature": "invalid-signature"}
                self.body = b'{"items": []}'

        request = MockRequest()

        with pytest.raises(AuthenticationFailed, match="Invalid signature"):
            auth.authenticate(request)

    def test_authenticate_without_secret(self):
        """Test authentication fails when secret is not configured."""
        auth = BrevoAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                self.headers = {"X-Brevo-Signature": "some-signature"}
                self.body = b'{"items": []}'

        request = MockRequest()

        with pytest.raises(AuthenticationFailed, match="not configured"):
            auth.authenticate(request)


@pytest.mark.django_db
class TestInboundBrevoWebhook:
    """Test the webhook endpoint."""

    @patch("core.api.viewsets.inbound.brevo.deliver_inbound_message")
    def test_webhook_success(self, mock_deliver, api_client, channel, brevo_payload):
        """Test successful message delivery."""
        mock_deliver.return_value = True

        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data=brevo_payload,
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ok"
        assert data["processed"] == 1

    def test_webhook_empty_items(self, api_client, channel):
        """Test webhook with empty items list."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data={"items": []},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "No items in payload"

    def test_webhook_missing_items(self, api_client, channel):
        """Test webhook with missing items field."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data={},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_webhook_invalid_items_type(self, api_client, channel):
        """Test webhook with invalid items type."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data={"items": "not a list"},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Items must be a list"

    def test_webhook_without_authentication(self, api_client, brevo_payload):
        """Test webhook endpoint without authentication."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data=brevo_payload,
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_webhook_item_missing_from(self, api_client, channel):
        """Test webhook with item missing From address."""
        payload = {
            "items": [
                {
                    "To": [{"Address": "recipient@test.com"}],
                    "Subject": "Test",
                }
            ]
        }

        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data=payload,
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["processed"] == 0
        assert data["results"][0]["success"] is False


@pytest.mark.django_db
class TestInboundBrevoCheck:
    """Test the check endpoint."""

    def test_check_success(self, api_client, channel):
        """Test successful recipient check."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/check/",
            data={"addresses": ["recipient@test.com"]},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["recipient@test.com"] is True

    def test_check_missing_addresses(self, api_client, channel):
        """Test check with missing addresses."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/check/",
            data={},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_check_empty_addresses(self, api_client, channel):
        """Test check with empty addresses list."""
        response = api_client.post(
            "/api/v1.0/inbound/brevo/check/",
            data={"addresses": []},
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestInboundBrevoE2E:
    """End-to-end tests for Brevo inbound channel."""

    def test_webhook_message_e2e(self, api_client):
        """Test that message is properly created from Brevo payload."""
        assert models.Message.objects.count() == 0

        mailbox = factories.MailboxFactory()
        label = factories.LabelFactory(mailbox=mailbox, name="Brevo")
        channel = factories.ChannelFactory(
            type="brevo",
            mailbox=mailbox,
            settings={
                "config": {"enabled": True},
                "tags": [str(label.id)],
            },
        )

        payload = {
            "items": [
                {
                    "MessageId": "<brevo-test-123@example.com>",
                    "From": {
                        "Name": "External Sender",
                        "Address": "external@example.com",
                    },
                    "To": [
                        {
                            "Name": mailbox.address,
                            "Address": mailbox.address,
                        }
                    ],
                    "Recipients": [mailbox.address],
                    "Cc": [],
                    "SentAtDate": "Mon, 15 Jan 2024 10:00:00 +0000",
                    "Subject": "Brevo Test Email",
                    "RawHtmlBody": "<p>Hello from Brevo</p>",
                    "RawTextBody": "Hello from Brevo",
                    "ExtractedMarkdownMessage": "Hello from Brevo",
                    "Attachments": [],
                    "Headers": {},
                }
            ]
        }

        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data=payload,
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK

        assert models.Message.objects.count() == 1
        message = models.Message.objects.first()

        assert message.thread.accesses.first().mailbox == mailbox
        assert message.sender.email == "external@example.com"
        assert message.subject == "Brevo Test Email"

        thread_label_ids = set(message.thread.labels.values_list("id", flat=True))
        assert label.id in thread_label_ids

    def test_webhook_with_reply(self, api_client):
        """Test that reply is properly threaded."""
        mailbox = factories.MailboxFactory()
        channel = factories.ChannelFactory(
            type="brevo",
            mailbox=mailbox,
        )

        existing_message = factories.MessageFactory(
            thread__mailbox=mailbox,
            mime_id="original-message-id@example.com",
        )

        payload = {
            "items": [
                {
                    "MessageId": "<reply-message-id@example.com>",
                    "InReplyTo": "<original-message-id@example.com>",
                    "From": {
                        "Address": "reply@example.com",
                    },
                    "To": [{"Address": mailbox.address}],
                    "Recipients": [mailbox.address],
                    "Subject": "Re: Original Subject",
                    "ExtractedMarkdownMessage": "This is a reply",
                    "SentAtDate": "Mon, 15 Jan 2024 11:00:00 +0000",
                }
            ]
        }

        response = api_client.post(
            "/api/v1.0/inbound/brevo/webhook/",
            data=payload,
            format="json",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK

        assert models.Message.objects.count() == 2
        new_message = models.Message.objects.exclude(id=existing_message.id).first()

        assert new_message.thread == existing_message.thread
