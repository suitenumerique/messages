"""Tests for webhook channel implementation."""

import uuid

from django.test import RequestFactory

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from core import enums, factories, models
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


class TestInboundWebhookMessage:
    """Test webhook message endpoint."""

    @pytest.mark.django_db
    def test_message_success(self, api_client, channel_with_api_key):
        """Test successful message delivery."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/message/",
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
        assert "message_id" in data
        assert "thread_id" in data
        # Verify IDs are valid UUIDs
        uuid.UUID(data["message_id"])
        uuid.UUID(data["thread_id"])

    @pytest.mark.django_db
    def test_message_missing_email(self, api_client, channel_with_api_key):
        """Test delivery fails when email is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/message/",
            data={"message": "Test message"},
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 400
        assert "Missing email" in response.json()["detail"]

    @pytest.mark.django_db
    def test_message_invalid_email(self, api_client, channel_with_api_key):
        """Test delivery fails with invalid email format."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/message/",
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
    def test_message_missing_message(self, api_client, channel_with_api_key):
        """Test delivery fails when message is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/message/",
            data={"email": "test@example.com"},
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
        )

        assert response.status_code == 400
        assert "Missing message" in response.json()["detail"]

    @pytest.mark.django_db
    def test_message_unauthorized(self, api_client, channel_with_api_key):
        """Test message endpoint requires authentication."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/message/",
            data={
                "email": "test@example.com",
                "message": "Test message",
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
        )

        assert response.status_code == 401


class TestInboundWebhookThreadEvent:
    """Test webhook threadevent endpoint."""

    @pytest.fixture(name="thread_with_access")
    def fixture_thread_with_access(self, channel_with_api_key):
        """Create a thread with access for the channel's mailbox."""
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=channel_with_api_key.mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        return thread

    @pytest.mark.django_db
    def test_threadevent_success(
        self, api_client, channel_with_api_key, thread_with_access
    ):
        """Test successful thread event creation."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread_with_access.id),
                "type": "notification",
                "data": {"message": "X read message Y", "user_id": "123"},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Thread event created successfully"
        assert "event_id" in data

        # Verify the event was created
        event = models.ThreadEvent.objects.get(id=data["event_id"])
        assert event.thread == thread_with_access
        assert event.type == "notification"
        assert event.channel == channel_with_api_key
        assert event.data == {"message": "X read message Y", "user_id": "123"}

    @pytest.mark.django_db
    def test_threadevent_missing_thread_id(self, api_client, channel_with_api_key):
        """Test threadevent fails when thread_id is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "type": "notification",
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 400
        assert "Missing thread_id" in response.json()["detail"]

    @pytest.mark.django_db
    def test_threadevent_missing_type(
        self, api_client, channel_with_api_key, thread_with_access
    ):
        """Test threadevent fails when type is missing."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread_with_access.id),
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 400
        assert "Missing type" in response.json()["detail"]

    @pytest.mark.django_db
    def test_threadevent_type_too_long(
        self, api_client, channel_with_api_key, thread_with_access
    ):
        """Test threadevent fails when type exceeds max length."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread_with_access.id),
                "type": "a" * 37,  # 37 chars, max is 36
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 400
        assert "exceeds maximum length" in response.json()["detail"]

    @pytest.mark.django_db
    def test_threadevent_thread_not_found(self, api_client, channel_with_api_key):
        """Test threadevent fails when thread does not exist."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(uuid.uuid4()),
                "type": "notification",
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 404
        assert "Thread not found" in response.json()["detail"]

    @pytest.mark.django_db
    def test_threadevent_no_access(self, api_client, channel_with_api_key):
        """Test threadevent fails when mailbox has no access to thread."""
        thread = factories.ThreadFactory()
        # Don't create ThreadAccess, so mailbox has no access

        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread.id),
                "type": "notification",
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 403
        assert "does not have access" in response.json()["detail"]

    @pytest.mark.django_db
    def test_threadevent_unauthorized(
        self, api_client, channel_with_api_key, thread_with_access
    ):
        """Test threadevent endpoint requires authentication."""
        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread_with_access.id),
                "type": "notification",
                "data": {},
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            format="json",
        )

        assert response.status_code == 401

    @pytest.mark.django_db
    def test_threadevent_with_complex_data(
        self, api_client, channel_with_api_key, thread_with_access
    ):
        """Test threadevent with complex JSON data."""
        complex_data = {
            "type": "arbitrary_block",
            "data": {
                "type": "iframe",
                "src": "https://example.com/widget",
                "width": "100%",
                "height": "400px",
                "config": {"theme": "dark", "language": "en"},
            },
        }

        response = api_client.post(
            "/api/v1.0/inbound/webhook/threadevent/",
            data={
                "thread_id": str(thread_with_access.id),
                **complex_data,
            },
            HTTP_X_CHANNEL_ID=str(channel_with_api_key.id),
            HTTP_X_API_KEY="test-api-key-123",
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True

        # Verify the complex data was stored
        event = models.ThreadEvent.objects.get(id=data["event_id"])
        assert event.data == complex_data["data"]
