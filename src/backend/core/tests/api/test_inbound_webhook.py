"""Tests for webhook channel implementation."""

import uuid

from django.test import RequestFactory

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from core import factories
from core.api.viewsets.inbound.webhook import (
    WebhookAuthentication,
)


@pytest.fixture(name="api_client")
def fixture_api_client():
    """Create an API client for testing."""
    return APIClient()


@pytest.fixture(name="channel_with_api_key")
def fixture_channel_with_api_key():
    """Create a channel with API key authentication configured."""
    mailbox = factories.MailboxFactory()
    return factories.ChannelFactory(
        type="webhook",
        mailbox=mailbox,
        settings={"auth_method": "api_key", "api_key": "test-api-key-123"},
    )


@pytest.fixture(name="channel_without_api_key")
def fixture_channel_without_api_key():
    """Create a channel without API key configured."""
    mailbox = factories.MailboxFactory()
    return factories.ChannelFactory(
        type="webhook",
        mailbox=mailbox,
        settings={
            "auth_method": "api_key",
        },
    )


class TestWebhookAuthentication:
    """Test webhook authentication functionality."""

    def test_authenticate_missing_channel_id(self):
        """Test authentication fails when channel ID is missing."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post("/test/")

        with pytest.raises(AuthenticationFailed, match="Missing X-Channel-ID header"):
            auth.authenticate(request)

    @pytest.mark.django_db
    def test_authenticate_invalid_channel_id(self):
        """Test authentication fails with invalid channel ID."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post("/test/", HTTP_X_CHANNEL_ID=str(uuid.uuid4()))

        with pytest.raises(AuthenticationFailed, match="Invalid channel ID"):
            auth.authenticate(request)

    @pytest.mark.django_db
    def test_authenticate_missing_api_key_header(self, channel_with_api_key):
        """Test authentication fails when API key header is missing."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post("/test/", HTTP_X_CHANNEL_ID=str(channel_with_api_key.id))

        with pytest.raises(AuthenticationFailed, match="Missing X-API-Key header"):
            auth.authenticate(request)

    @pytest.mark.django_db
    def test_authenticate_missing_api_key_config(self, channel_without_api_key):
        """Test authentication fails when API key is not configured."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post(
            "/test/",
            HTTP_X_CHANNEL_ID=str(channel_without_api_key.id),
            HTTP_X_API_KEY="some-key",
        )

        with pytest.raises(AuthenticationFailed, match="API key not configured"):
            auth.authenticate(request)

    @pytest.mark.django_db
    def test_authenticate_invalid_api_key(self, channel_with_api_key):
        """Test authentication fails with invalid API key."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post(
            "/test/",
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="wrong-key",
        )

        with pytest.raises(AuthenticationFailed, match="Invalid API key"):
            auth.authenticate(request)

    @pytest.mark.django_db
    def test_authenticate_success(self, channel_with_api_key):
        """Test successful authentication with valid API key."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post(
            "/test/",
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        user, auth_data = auth.authenticate(request)

        assert user is None
        assert auth_data["channel"] == channel_with_api_key
        assert auth_data["auth_method"] == "api_key"

    def test_authenticate_header(self):
        """Test authenticate header method."""
        auth = WebhookAuthentication()
        factory = RequestFactory()
        request = factory.post("/test/")

        assert auth.authenticate_header(request) == 'ApiKey realm="Webhook"'


class TestInboundWebhookDeliver:
    """Test webhook deliver endpoint."""

    @pytest.mark.django_db
    def test_deliver_success(self, api_client, channel_with_api_key):
        """Test successful message delivery."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/deliver/",
            data={
                "email": "test@example.com",
                "message": "Test message from webhook",
                "subject": "Test Subject",
                "name": "Test User",
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Message delivered successfully"

    @pytest.mark.django_db
    def test_deliver_missing_email(self, api_client, channel_with_api_key):
        """Test delivery fails when email is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/deliver/",
            data={"message": "Test message"},
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 400
        assert "Missing email" in response.json()["detail"]

    @pytest.mark.django_db
    def test_deliver_invalid_email(self, api_client, channel_with_api_key):
        """Test delivery fails with invalid email format."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/deliver/",
            data={
                "email": "invalid-email",
                "message": "Test message",
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 400
        assert "Invalid email format" in response.json()["detail"]

    @pytest.mark.django_db
    def test_deliver_missing_message(self, api_client, channel_with_api_key):
        """Test delivery fails when message is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/deliver/",
            data={"email": "test@example.com"},
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 400
        assert "Missing message" in response.json()["detail"]

    @pytest.mark.django_db
    def test_deliver_unauthorized(self, api_client, channel_with_api_key):
        """Test deliver endpoint requires authentication."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/deliver/",
            data={
                "email": "test@example.com",
                "message": "Test message",
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
        )

        assert response.status_code == 401
