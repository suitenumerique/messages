"""Tests for widget inbound API endpoints."""

from unittest.mock import patch

from django.core.exceptions import ValidationError

import pytest
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from core import factories
from core.api.viewsets.inbound.widget import WidgetAuthentication


@pytest.fixture(name="api_client")
def fixture_api_client():
    """Return an API client."""
    return APIClient()


@pytest.fixture(name="channel")
def fixture_channel():
    """Create a test channel with mailbox."""
    mailbox = factories.MailboxFactory()
    return factories.ChannelFactory(
        type="widget",
        mailbox=mailbox,
        settings={
            "config": {"enabled": True, "theme": "light"},
            "default_sender_email": "widget@example.com",
            "default_sender_name": "Widget Sender",
            "intro_text": "Message from widget:",
        },
    )


@pytest.fixture(name="channel_with_mailbox_contact")
def fixture_channel_with_mailbox_contact():
    """Create a test channel with mailbox."""
    contact = factories.ContactFactory(email="widget@example.com", name="Widget Sender")
    mailbox = factories.MailboxFactory(contact=contact)
    return factories.ChannelFactory(
        type="widget",
        mailbox=mailbox,
        settings={
            "config": {"enabled": True, "theme": "light"},
            "default_sender_email": "widget@example.com",
            "default_sender_name": "Widget Sender",
            "intro_text": "Message from widget:",
        },
    )


@pytest.fixture(name="channel_without_mailbox")
def fixture_channel_without_mailbox():
    """Create a test channel without mailbox."""
    return factories.ChannelFactory(
        type="widget",
        mailbox=None,
        maildomain=factories.MailDomainFactory(),
    )


@pytest.mark.django_db
def test_channel_model():
    """Test the Channel model."""
    with pytest.raises(ValidationError):
        factories.ChannelFactory(
            mailbox=factories.MailboxFactory(),
            maildomain=factories.MailDomainFactory(),
        )

    with pytest.raises(ValidationError):
        factories.ChannelFactory(
            mailbox=None,
            maildomain=None,
        )


@pytest.mark.django_db
class TestWidgetAuthentication:
    """Test the WidgetAuthentication class."""

    def test_authenticate_with_valid_channel_id(self, channel):
        """Test authentication with valid channel ID."""
        auth = WidgetAuthentication()

        # Create a mock request with valid channel ID
        class MockRequest:
            """Mock request."""

            def __init__(self, channel_id):
                """Initialize the mock request."""
                self.headers = {"X-Channel-ID": str(channel_id)}
                self.META = {}  # pylint: disable=invalid-name

        request = MockRequest(channel.id)
        user, auth_data = auth.authenticate(request)

        assert user is None
        assert auth_data["channel"] == channel

    def test_authenticate_with_missing_channel_id(self):
        """Test authentication fails with missing channel ID."""
        auth = WidgetAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self):
                """Initialize the mock request."""
                self.headers = {}
                self.META = {}  # pylint: disable=invalid-name

        request = MockRequest()

        with pytest.raises(AuthenticationFailed, match="Missing channel_id"):
            auth.authenticate(request)

    def test_authenticate_with_invalid_channel_id(self):
        """Test authentication fails with invalid channel ID."""
        auth = WidgetAuthentication()

        class MockRequest:
            """Mock request."""

            def __init__(self, channel_id):
                """Initialize the mock request."""
                self.headers = {"X-Channel-ID": str(channel_id)}
                self.META = {}  # pylint: disable=invalid-name

        request = MockRequest("invalid-uuid")

        with pytest.raises(ValidationError):
            auth.authenticate(request)


@pytest.mark.django_db
class TestInboundWidgetConfig:
    """Test the config endpoint."""

    def test_config_success(self, api_client, channel):
        """Test successful config retrieval."""
        response = api_client.get(
            "/api/v1.0/inbound/widget/config/",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "success": True,
            "config": {"enabled": True, "theme": "light"},
        }

    def test_config_with_empty_settings(self, api_client):
        """Test config with empty settings."""
        channel = factories.ChannelFactory(type="widget", settings={})

        response = api_client.get(
            "/api/v1.0/inbound/widget/config/",
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"success": True, "config": {}}

    def test_config_without_authentication(self, api_client):
        """Test config endpoint without authentication."""
        response = api_client.get("/api/v1.0/inbound/widget/config/")

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestInboundWidgetDeliver:
    """Test the deliver endpoint."""

    @patch("core.api.viewsets.inbound.widget.deliver_inbound_message")
    def test_deliver_success(
        self, mock_deliver, api_client, channel, channel_with_mailbox_contact
    ):
        """Test successful message delivery."""
        mock_deliver.return_value = True

        data = {
            "email": "sender@example.com",
            "textBody": "This is a test message from the widget.",
        }

        for _channel in [channel, channel_with_mailbox_contact]:
            response = api_client.post(
                "/api/v1.0/inbound/widget/deliver/",
                data=data,
                HTTP_X_CHANNEL_ID=str(_channel.id),
                HTTP_REFERER="https://example.com/contact",
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.json() == {"success": True}

            # Verify deliver_inbound_message was called
            mock_deliver.assert_called_once()
            call_args = mock_deliver.call_args[0]
            call_kwargs = mock_deliver.call_args[1]
            assert call_kwargs["channel"] == _channel
            if _channel.mailbox.contact:
                assert call_args[0] == str(_channel.mailbox.contact.email)
            else:
                assert call_args[0] == str(_channel.mailbox)

            mock_deliver.reset_mock()

    def test_deliver_missing_email(self, api_client, channel):
        """Test deliver with missing email."""
        data = {"textBody": "This is a test message."}

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Missing email"}

    def test_deliver_invalid_email(self, api_client, channel):
        """Test deliver with invalid email format."""
        data = {
            "email": "invalid-email",
            "textBody": "This is a test message.",
        }

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Invalid email format"}

    def test_deliver_missing_message(self, api_client, channel):
        """Test deliver with missing message."""
        data = {"email": "sender@example.com"}

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel.id),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Missing message"}

    def test_deliver_no_mailbox_configured(self, api_client, channel_without_mailbox):
        """Test deliver when no mailbox is configured for the channel."""
        data = {
            "email": "sender@example.com",
            "textBody": "This is a test message.",
        }

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel_without_mailbox.id),
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json() == {"detail": "No mailbox configured for this channel"}

    @patch("core.api.viewsets.inbound.widget.deliver_inbound_message")
    def test_deliver_with_custom_settings(self, mock_deliver, api_client):
        """Test deliver with custom channel settings."""
        mock_deliver.return_value = True

        channel = factories.ChannelFactory(
            type="widget",
            mailbox=factories.MailboxFactory(),
            settings={
                "default_sender_email": "custom@widget.com",
                "default_sender_name": "Custom Widget",
                "intro_text": "Custom intro text:",
            },
        )

        data = {
            "email": "sender@example.com",
            "textBody": "Test message with custom settings.",
        }

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_REFERER="https://example.com/contact",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify the parsed email structure
        call_args = mock_deliver.call_args[0]
        parsed_email = call_args[1]

        assert parsed_email["from"]["email"] == "custom@widget.com"
        assert parsed_email["from"]["name"] == "Custom Widget"
        assert "Custom intro text:" in parsed_email["htmlBody"][0]["content"]

    @patch("core.api.viewsets.inbound.widget.deliver_inbound_message")
    def test_deliver_message_formatting(self, mock_deliver, api_client, channel):
        """Test that message is properly formatted with HTML and signature."""
        mock_deliver.return_value = True

        data = {
            "email": "sender@example.com",
            "textBody": "Line 1\nLine 2\nLine 3",
        }

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_REFERER="https://example.com/contact",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify the parsed email structure and formatting
        call_args = mock_deliver.call_args[0]
        parsed_email = call_args[1]

        html_content = parsed_email["htmlBody"][0]["content"]

        # Check that newlines are converted to <br/> tags
        assert "Line 1<br/>Line 2<br/>Line 3" in html_content

        # Check that signature is included
        assert "Sender" in html_content
        assert "sender@example.com" in html_content
        assert "❌ Unverified" in html_content
        assert "IP" in html_content
        assert "Page" in html_content
        assert "https://example.com/contact" in html_content

    def test_deliver_without_authentication(self, api_client):
        """Test deliver endpoint without authentication."""
        data = {
            "email": "sender@example.com",
            "textBody": "This is a test message.",
        }

        response = api_client.post(
            "/api/v1.0/inbound/widget/deliver/",
            data=data,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
