"""Tests for the client-bridge API endpoints (auth and submit)."""

# pylint: disable=redefined-outer-name,too-many-lines

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import override_settings

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models
from core.enums import CLIENT_BRIDGE_ROLE_SCOPES, ChannelScope, ChannelTypes
from core.factories import ChannelFactory, MailboxFactory, UserFactory

SERVICE_SECRET = "my-shared-secret-clientbridge-at-least-32-bytes"

# Shorthand scopes for parametrized tests
SCOPES_READER = list(CLIENT_BRIDGE_ROLE_SCOPES["reader"])
SCOPES_EDITOR = list(CLIENT_BRIDGE_ROLE_SCOPES["editor"])
SCOPES_SENDER = list(CLIENT_BRIDGE_ROLE_SCOPES["sender"])
SCOPES_SEND_ONLY = list(CLIENT_BRIDGE_ROLE_SCOPES["sender_only"])


def _make_jwt(
    channel,
    mailbox,
    secret=SERVICE_SECRET,
    expires_in=3600,
    **extra_claims,
):
    """Generate a JWT token for testing, matching what ClientBridgeAuthView issues."""
    payload = {
        "channel_id": str(channel.id),
        "mailbox_id": str(mailbox.id),
        "mailbox_email": str(mailbox),
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
        **extra_claims,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def channel_user():
    """Create a user who owns the client-bridge channel."""
    return UserFactory()


@pytest.fixture
def mailbox(channel_user):
    """Create a test mailbox with access for the channel user."""
    mb = MailboxFactory()
    mb.accesses.create(user=channel_user, role=models.MailboxRoleChoices.ADMIN)
    return mb


@pytest.fixture
def client_bridge_channel(mailbox, channel_user):
    """Create a client-bridge channel with an encrypted app-specific password."""
    return ChannelFactory(
        mailbox=mailbox,
        user=channel_user,
        type=ChannelTypes.CLIENT_BRIDGE,
        settings={"scopes": SCOPES_SENDER},
        encrypted_settings={"password": "test-app-password-123"},
    )


@pytest.fixture
def api_client():
    """Provide an API client with the client-bridge service token."""
    client = APIClient()
    client.credentials(HTTP_X_SERVICE_AUTH=f"Bearer {SERVICE_SECRET}")
    return client


def _build_raw_email(mail_from, rcpt_to, subject="Test Subject"):
    """Build a minimal RFC 5322 email for testing."""
    return (
        f"From: {mail_from}\r\n"
        f"To: {rcpt_to}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <test-{uuid.uuid4()}@example.com>\r\n"
        f"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"Test body\r\n"
    ).encode()


@pytest.mark.django_db
class TestClientBridgeAuth:
    """Test the client-bridge auth endpoint."""

    @pytest.fixture(autouse=True)
    def _enable_clientbridge(self, settings):
        settings.FEATURE_CLIENTBRIDGE = True
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET

    def test_auth_success(self, api_client, client_bridge_channel, mailbox):
        """Test successful authentication returns a valid JWT token."""
        mailbox_email = str(mailbox)
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": mailbox_email,
                "password": "test-app-password-123",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "token" in response.data

        # All session data is in the JWT
        payload = jwt.decode(
            response.data["token"], SERVICE_SECRET, algorithms=["HS256"]
        )
        assert payload["channel_id"] == str(client_bridge_channel.id)
        assert payload["mailbox_id"] == str(mailbox.id)
        assert payload["mailbox_email"] == mailbox_email
        assert "exp" in payload
        # Scopes are NOT in the JWT — read from the database
        assert "scopes" not in payload
        assert "role" not in payload

    def test_auth_wrong_password(self, api_client, client_bridge_channel, mailbox):  # pylint: disable=unused-argument
        """Test authentication fails with incorrect password."""
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "wrong-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data["detail"] == "Invalid credentials."

    def test_auth_nonexistent_mailbox(self, api_client):
        """Test authentication fails with nonexistent email address."""
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": "nobody@nonexistent.example",
                "password": "any-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data["detail"] == "Invalid credentials."

    def test_auth_missing_fields(self, api_client):
        """Test authentication fails when required fields are missing."""
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_auth_missing_password(self, api_client, client_bridge_channel, mailbox):  # pylint: disable=unused-argument
        """Test authentication fails when password is missing."""
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {"username": str(mailbox)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_auth_invalid_username_format(self, api_client):
        """Test authentication fails with non-email username."""
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": "not-an-email",
                "password": "any-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_auth_no_client_bridge_channel(self, api_client, mailbox):
        """Test authentication fails when mailbox has no client-bridge channels."""
        # Mailbox exists but has no client-bridge channel
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "any-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_auth_wrong_channel_type_ignored(self, api_client, mailbox):
        """Test authentication ignores non-client-bridge channel types."""
        ChannelFactory(
            mailbox=mailbox,
            type="widget",
            encrypted_settings={"password": "test-password"},
        )

        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "test-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_auth_empty_password_in_settings(self, api_client, mailbox):
        """Test authentication fails when channel has no password set."""
        ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={},
        )

        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "any-password",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_auth_rejects_missing_service_token(self, client_bridge_channel, mailbox):  # pylint: disable=unused-argument
        """Test that requests without a service token are rejected."""
        client = APIClient()
        response = client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "test-app-password-123",
            },
            format="json",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_auth_rejects_invalid_service_token(self, client_bridge_channel, mailbox):  # pylint: disable=unused-argument
        """Test that requests with an invalid service token are rejected."""
        client = APIClient()
        client.credentials(HTTP_X_SERVICE_AUTH="wrong-secret")
        response = client.post(
            "/api/v1.0/client-bridge/auth/",
            {
                "username": str(mailbox),
                "password": "test-app-password-123",
            },
            format="json",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.django_db
class TestClientBridgeSubmit:
    """Test submitting messages via client-bridge JWT on the unified /submit/ endpoint.

    The SubmitRawEmailView accepts both API-key and client-bridge JWT auth.
    These tests exercise the client-bridge path (X-Channel-Token).
    """

    @pytest.fixture(autouse=True)
    def _enable_clientbridge(self, settings):
        settings.FEATURE_CLIENTBRIDGE = True
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET

    @patch("core.api.viewsets.submit.send_message_task")
    def test_submit_success(self, mock_send_task, client_bridge_channel, mailbox):
        """Test successful message submission creates message and dispatches delivery."""
        mailbox_email = str(mailbox)
        rcpt_to = "recipient@example.com"
        raw_email = _build_raw_email(mailbox_email, rcpt_to, subject="Hello")
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO=rcpt_to,
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] == "accepted"

        # Verify message was actually created in the database
        message = models.Message.objects.get(id=response.data["message_id"])
        assert message.subject == "Hello"
        assert message.is_sender is True
        assert message.is_draft is False
        assert message.sender.email == mailbox_email
        assert message.channel == client_bridge_channel
        assert message.blob is not None

        # Verify thread was created
        assert message.thread is not None
        assert models.ThreadAccess.objects.filter(
            thread=message.thread, mailbox=mailbox
        ).exists()

        # Verify recipient was created
        assert message.recipients.filter(contact__email=rcpt_to).exists()

        # Verify async delivery was dispatched
        mock_send_task.delay.assert_called_once_with(str(message.id))

    @patch("core.api.viewsets.submit.send_message_task")
    def test_submit_sender_mismatch(
        self, mock_send_task, client_bridge_channel, mailbox
    ):
        """Test submission fails when From header doesn't match the mailbox."""
        raw_email = _build_raw_email("wrong@example.com", "recipient@example.com")
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM="wrong@example.com",
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        mock_send_task.delay.assert_not_called()

    def test_submit_missing_headers(self, client_bridge_channel, mailbox):
        """Test submission fails when required headers are missing."""
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_submit_no_token(self, client_bridge_channel, mailbox):  # pylint: disable=unused-argument
        """Test submission fails without a JWT token."""
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_submit_expired_token(self, client_bridge_channel, mailbox):
        """Test submission fails with an expired JWT token."""
        token = _make_jwt(client_bridge_channel, mailbox, expires_in=-10)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM=str(mailbox),
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_submit_tampered_token(self, client_bridge_channel, mailbox):
        """Test submission fails with a JWT signed with wrong secret."""
        token = _make_jwt(
            client_bridge_channel,
            mailbox,
            secret="wrong-secret-that-is-at-least-32-bytes-long",
        )

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM=str(mailbox),
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_submit_nonexistent_channel_in_token(self, mailbox):
        """Test submission fails when JWT references a nonexistent channel."""
        # Create a fake channel object with a random UUID
        fake_channel = type("FakeChannel", (), {"id": uuid.uuid4()})()
        token = _make_jwt(fake_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM=str(mailbox),
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("core.api.viewsets.submit.send_message_task")
    def test_submit_unparseable_email(
        self, mock_send_task, client_bridge_channel, mailbox
    ):
        """Test submission fails when the email body cannot be parsed or has no valid sender."""
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_MAIL_FROM=str(mailbox),
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_send_task.delay.assert_not_called()


@pytest.mark.django_db
class TestEncryptedSettings:
    """Test the encrypted_settings field on the Channel model."""

    def test_encrypted_settings_default(self, mailbox):
        """Test that encrypted_settings defaults to empty dict."""
        channel = ChannelFactory(mailbox=mailbox, type="widget")
        channel.refresh_from_db()
        assert channel.encrypted_settings == {}

    def test_encrypted_settings_stores_password(self, mailbox):
        """Test that encrypted_settings stores and retrieves password correctly."""
        channel = ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={"password": "secret-password"},
        )
        channel.refresh_from_db()
        assert channel.encrypted_settings["password"] == "secret-password"

    def test_encrypted_settings_roundtrip(self, mailbox):
        """Test that encrypted_settings survives save/load roundtrip."""
        channel = ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={"password": "test123", "extra": "data"},
        )
        channel.refresh_from_db()
        assert channel.encrypted_settings == {"password": "test123", "extra": "data"}

    def test_encrypted_settings_not_stored_plaintext(self, mailbox):
        """Test that encrypted_settings values are not stored as plaintext in the database."""
        from django.db import connection  # pylint: disable=import-outside-toplevel

        password = "super-secret-password-12345"
        channel = ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={"password": password},
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT encrypted_settings FROM messages_channel WHERE id = %s",
                [str(channel.id)],
            )
            raw_value = cursor.fetchone()[0]

        assert password not in raw_value


@pytest.mark.django_db
class TestChannelSerializerPasswordExtraction:
    """Test that the ChannelSerializer extracts password to encrypted_settings."""

    @override_settings(
        FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"], FEATURE_CLIENTBRIDGE=True
    )
    def test_create_client_bridge_auto_generates_password(self, mailbox):
        """Creating a client-bridge channel should auto-generate a password and return it once."""
        user = UserFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/",
            {
                "name": "My Bridge",
                "type": "client-bridge",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        # Password should be returned in response
        assert "password" in response.data
        generated_password = response.data["password"]
        assert len(generated_password) == 16

        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.encrypted_settings["password"] == generated_password
        # Password should NOT be in plain settings
        assert "password" not in (channel.settings or {})

    @override_settings(
        FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"], FEATURE_CLIENTBRIDGE=True
    )
    def test_rotate_password(self, mailbox):
        """Rotating a client-bridge channel password should generate a new one."""
        user = UserFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)

        channel = ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={"password": "old-password"},
        )

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/{channel.id}/rotate-password/",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "password" in response.data
        new_password = response.data["password"]
        assert new_password != "old-password"
        assert len(new_password) == 16

        channel.refresh_from_db()
        assert channel.encrypted_settings["password"] == new_password

    @override_settings(
        FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"], FEATURE_CLIENTBRIDGE=True
    )
    def test_rotate_password_rejects_non_client_bridge(self, mailbox):
        """Rotating password should fail for non-client-bridge channels."""
        user = UserFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)

        channel = ChannelFactory(mailbox=mailbox, type="widget")

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/{channel.id}/rotate-password/",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=[], FEATURE_CLIENTBRIDGE=True)
    def test_client_bridge_type_rejected_when_not_in_admin_channels(self, mailbox):
        """Creating a client-bridge channel should fail when not in FEATURE_MAILBOX_ADMIN_CHANNELS."""
        user = UserFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/",
            {
                "name": "My Bridge",
                "type": "client-bridge",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(
        FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"], FEATURE_CLIENTBRIDGE=False
    )
    def test_client_bridge_type_rejected_when_backend_feature_disabled(self, mailbox):
        """Creating a client-bridge channel should fail when FEATURE_CLIENTBRIDGE is False."""
        user = UserFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/",
            {
                "name": "My Bridge",
                "type": "client-bridge",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(
        FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"], FEATURE_CLIENTBRIDGE=True
    )
    def test_create_client_bridge_saves_user(self, mailbox, channel_user):
        """Creating a client-bridge channel via API should save request.user on the channel."""
        client = APIClient()
        client.force_authenticate(user=channel_user)

        response = client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/channels/",
            {
                "name": "My Bridge",
                "type": "client-bridge",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.user == channel_user


@pytest.mark.django_db
class TestChannelJwtAuthentication:
    """Test ChannelJwtAuthentication on the /submit/ endpoint (the only
    view that whitelists it).  The auth class is NOT in
    DEFAULT_AUTHENTICATION_CLASSES — views must opt in explicitly."""

    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET

    def test_expired_token_rejected(self, client_bridge_channel, mailbox):
        """Expired JWT should fail with 401."""
        token = _make_jwt(client_bridge_channel, mailbox, expires_in=-10)
        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_tampered_token_rejected(self, client_bridge_channel, mailbox):
        """JWT signed with wrong secret should not authenticate."""
        token = _make_jwt(
            client_bridge_channel,
            mailbox,
            secret="wrong-secret-that-is-at-least-32-bytes-long",
        )
        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_nonexistent_channel_rejected(self, mailbox):
        """JWT referencing a nonexistent channel should fail."""
        fake_channel = type("FakeChannel", (), {"id": uuid.uuid4()})()
        token = _make_jwt(fake_channel, mailbox)
        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_token_without_exp_rejected(self, client_bridge_channel, mailbox):
        """A JWT missing the 'exp' claim should be rejected."""
        payload = {
            "channel_id": str(client_bridge_channel.id),
            "mailbox_id": str(mailbox.id),
        }
        token = jwt.encode(payload, SERVICE_SECRET, algorithm="HS256")
        client = APIClient()
        response = client.post(
            "/api/v1.0/submit/",
            b"raw email",
            content_type="message/rfc822",
            HTTP_X_CHANNEL_TOKEN=token,
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_jwt_not_accepted_on_generic_endpoints(self, client_bridge_channel, mailbox):
        """JWT should NOT authenticate on endpoints that don't whitelist it."""
        token = _make_jwt(client_bridge_channel, mailbox)
        client = APIClient()
        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
            HTTP_X_CHANNEL_TOKEN=token,
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.django_db
class TestChannelJwtScopeEnforcement:
    """Test that channel scopes are enforced on /submit/ via the
    CanSubmitMessage permission class."""

    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET

    @pytest.fixture
    def _make_channel(self, mailbox, channel_user):
        """Helper to create a client-bridge channel with given scopes."""

        def _factory(scopes):
            return ChannelFactory(
                mailbox=mailbox,
                user=channel_user,
                type=ChannelTypes.CLIENT_BRIDGE,
                encrypted_settings={"password": "test-pass"},
                settings={"scopes": list(scopes)},
            )

        return _factory

    def _jwt_client(self, channel, mailbox):
        """Create an APIClient with a JWT for the given channel."""
        token = _make_jwt(channel, mailbox)
        client = APIClient()
        client.credentials(HTTP_X_CHANNEL_TOKEN=token)
        return client

    # ── /api/v1.0/submit/ scope enforcement ────────────────────────────

    def test_submit_blocked_reader(self, _make_channel, mailbox):
        """Reader channels lack messages:send — blocked by CanSubmitMessage."""
        channel = _make_channel(SCOPES_READER)
        client = self._jwt_client(channel, mailbox)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_submit_blocked_editor(self, _make_channel, mailbox):
        """Editor channels lack messages:send — blocked by CanSubmitMessage."""
        channel = _make_channel(SCOPES_EDITOR)
        client = self._jwt_client(channel, mailbox)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("core.api.viewsets.submit.send_message_task")
    @pytest.mark.parametrize("scopes", [SCOPES_SENDER, SCOPES_SEND_ONLY])
    def test_submit_allows_sender_scopes(
        self,
        mock_send_task,  # pylint: disable=unused-argument
        _make_channel,
        mailbox,
        scopes,  # pylint: disable=unused-argument
    ):
        """Channels with messages:send should be allowed to submit."""
        channel = _make_channel(scopes)
        client = self._jwt_client(channel, mailbox)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_202_ACCEPTED

    @patch("core.api.viewsets.submit.send_message_task")
    def test_submit_sets_sender_user(
        self,
        mock_send_task,  # pylint: disable=unused-argument
        _make_channel,
        mailbox,
    ):
        """Submit should set message.sender_user to the channel's user."""
        channel = _make_channel(SCOPES_SENDER)
        client = self._jwt_client(channel, mailbox)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        message = models.Message.objects.get(id=response.data["message_id"])
        assert message.sender_user == channel.user

    def test_no_scopes_blocked(self, mailbox, channel_user):
        """A channel with no scopes should be blocked on /submit/."""
        channel = ChannelFactory(
            mailbox=mailbox,
            user=channel_user,
            type=ChannelTypes.CLIENT_BRIDGE,
            encrypted_settings={"password": "test-pass"},
            settings={},
        )
        client = self._jwt_client(channel, mailbox)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="recipient@example.com",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ── Auth endpoint JWT is minimal ──────────────────────────────────

    @pytest.mark.parametrize("scopes", [SCOPES_READER, SCOPES_EDITOR, SCOPES_SENDER, SCOPES_SEND_ONLY])
    def test_auth_jwt_has_no_scopes(
        self, api_client, _make_channel, mailbox, scopes
    ):
        """Auth endpoint JWT should only contain channel_id, mailbox_id, mailbox_email, exp."""
        _make_channel(scopes)
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {"username": str(mailbox), "password": "test-pass"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        payload = jwt.decode(
            response.data["token"], SERVICE_SECRET, algorithms=["HS256"]
        )
        assert set(payload.keys()) == {"channel_id", "mailbox_id", "mailbox_email", "exp"}
