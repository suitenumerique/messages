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
from core.factories import ChannelFactory, MailboxFactory, UserFactory

SERVICE_SECRET = "my-shared-secret-clientbridge-at-least-32-bytes"


def _make_jwt(
    channel,
    mailbox,
    role="sender",
    secret=SERVICE_SECRET,
    expires_in=3600,
    **extra_claims,
):
    """Generate a JWT token for testing, matching what ClientBridgeAuthView issues."""
    payload = {
        "channel_id": str(channel.id),
        "mailbox_id": str(mailbox.id),
        "mailbox_email": str(mailbox),
        "role": role,
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
        type="client-bridge",
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
        assert payload["role"] == "sender"
        assert "exp" in payload

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
            type="client-bridge",
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
    """Test the client-bridge submit endpoint.

    The submit view uses ClientBridgeChannelAuthentication (JWT via
    X-Channel-Token) to resolve the channel and enforce roles.
    """

    @pytest.fixture(autouse=True)
    def _enable_clientbridge(self, settings):
        settings.FEATURE_CLIENTBRIDGE = True
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET

    @patch("core.api.viewsets.client_bridge.send_message_task")
    def test_submit_success(self, mock_send_task, client_bridge_channel, mailbox):
        """Test successful message submission creates message and dispatches delivery."""
        mailbox_email = str(mailbox)
        rcpt_to = "recipient@example.com"
        raw_email = _build_raw_email(mailbox_email, rcpt_to, subject="Hello")
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/client-bridge/submit/",
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

    @patch("core.api.viewsets.client_bridge.send_message_task")
    def test_submit_sender_mismatch(
        self, mock_send_task, client_bridge_channel, mailbox
    ):
        """Test submission fails when From header doesn't match the mailbox."""
        raw_email = _build_raw_email("wrong@example.com", "recipient@example.com")
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/client-bridge/submit/",
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
            "/api/v1.0/client-bridge/submit/",
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
            "/api/v1.0/client-bridge/submit/",
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
            "/api/v1.0/client-bridge/submit/",
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
            "/api/v1.0/client-bridge/submit/",
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
            "/api/v1.0/client-bridge/submit/",
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

    @patch("core.api.viewsets.client_bridge.send_message_task")
    def test_submit_unparseable_email(
        self, mock_send_task, client_bridge_channel, mailbox
    ):
        """Test submission fails when the email body cannot be parsed or has no valid sender."""
        token = _make_jwt(client_bridge_channel, mailbox)

        client = APIClient()
        response = client.post(
            "/api/v1.0/client-bridge/submit/",
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
            type="client-bridge",
            encrypted_settings={"password": "secret-password"},
        )
        channel.refresh_from_db()
        assert channel.encrypted_settings["password"] == "secret-password"

    def test_encrypted_settings_roundtrip(self, mailbox):
        """Test that encrypted_settings survives save/load roundtrip."""
        channel = ChannelFactory(
            mailbox=mailbox,
            type="client-bridge",
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
            type="client-bridge",
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
            type="client-bridge",
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
class TestClientBridgeChannelAuthentication:
    """Test that ClientBridgeChannelAuthentication (JWT) lets the client-bridge
    access regular API endpoints as the channel's user."""

    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET
        settings.FEATURE_CLIENTBRIDGE = True

    def test_list_threads_with_jwt(
        self,
        client_bridge_channel,
        mailbox,
        channel_user,  # pylint: disable=unused-argument
    ):
        """JWT token should authenticate as the channel's user."""
        token = _make_jwt(client_bridge_channel, mailbox)
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code == status.HTTP_200_OK

    def test_rejects_without_token(self):
        """No X-Channel-Token header should not authenticate."""
        client = APIClient()

        response = client.get("/api/v1.0/threads/")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_rejects_expired_token(self, client_bridge_channel, mailbox):
        """Expired JWT should fail with 401."""
        token = _make_jwt(client_bridge_channel, mailbox, expires_in=-10)
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_rejects_tampered_token(self, client_bridge_channel, mailbox):
        """JWT signed with wrong secret should not authenticate."""
        token = _make_jwt(
            client_bridge_channel,
            mailbox,
            secret="wrong-secret-that-is-at-least-32-bytes-long",
        )
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_rejects_nonexistent_channel(self, mailbox):
        """JWT referencing a nonexistent channel should fail."""
        fake_channel = type("FakeChannel", (), {"id": uuid.uuid4()})()
        token = _make_jwt(fake_channel, mailbox)
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_channel_without_user_rejected(self, mailbox):
        """A client-bridge channel with no user should not authenticate."""
        channel = ChannelFactory(
            mailbox=mailbox,
            type="client-bridge",
            encrypted_settings={"password": "test"},
            user=None,
        )
        token = _make_jwt(channel, mailbox)
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_rejects_invalid_token_format(self):
        """Garbage token string should not authenticate."""
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            HTTP_X_CHANNEL_TOKEN="not.a.valid.jwt",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_rejects_token_without_exp(self, client_bridge_channel, mailbox):
        """A JWT missing the 'exp' claim should be rejected."""
        payload = {
            "channel_id": str(client_bridge_channel.id),
            "mailbox_id": str(mailbox.id),
            "role": "sender",
        }
        token = jwt.encode(payload, SERVICE_SECRET, algorithm="HS256")
        client = APIClient()

        response = client.get(
            "/api/v1.0/threads/",
            HTTP_X_CHANNEL_TOKEN=token,
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.django_db
class TestClientBridgeRoleEnforcement:
    """Test that channel roles are enforced on regular API endpoints, send, and submit.

    Role matrix (auth class):
    - reader:      GET ✓  POST ✗  PATCH ✗  DELETE ✗
    - editor:      GET ✓  POST ✓  PATCH ✓  DELETE ✓
    - sender:      GET ✓  POST ✓  PATCH ✓  DELETE ✓
    - sender_only: GET ✗  POST ✓  PATCH ✗  DELETE ✗

    POST is allowed for both CAN_EDIT and CAN_SEND roles. The /send/ and
    /submit/ endpoints additionally enforce CAN_SEND.
    """

    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.CLIENTBRIDGE_API_SECRET = SERVICE_SECRET
        settings.FEATURE_CLIENTBRIDGE = True

    @pytest.fixture
    def _make_channel(self, mailbox, channel_user):
        """Helper to create a client-bridge channel with a given role."""

        def _factory(role):
            return ChannelFactory(
                mailbox=mailbox,
                user=channel_user,
                type="client-bridge",
                encrypted_settings={"password": "test-pass"},
                settings={"role": role},
            )

        return _factory

    def _jwt_client(self, channel, mailbox, role=None):
        """Create an APIClient with a JWT for the given channel/role."""
        role = role or (channel.settings or {}).get("role", "sender")
        token = _make_jwt(channel, mailbox, role=role)
        client = APIClient()
        client.credentials(HTTP_X_CHANNEL_TOKEN=token)
        return client

    # ── GET (read) ──────────────────────────────────────────────────────

    @pytest.mark.parametrize("role", ["reader", "editor", "sender"])
    def test_get_allowed(self, _make_channel, mailbox, role):
        """Roles with CAN_READ should be allowed to GET."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_get_blocked_sender_only(self, _make_channel, mailbox):
        """sender_only should not be allowed to GET."""
        channel = _make_channel("sender_only")
        client = self._jwt_client(channel, mailbox, role="sender_only")
        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ── POST (edit or send) ────────────────────────────────────────────

    def test_post_blocked_sender_only_on_generic_api(self, _make_channel, mailbox):
        """sender_only should NOT be allowed to POST on generic API endpoints."""
        channel = _make_channel("sender_only")
        client = self._jwt_client(channel, mailbox, role="sender_only")
        response = client.post(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize("role", ["editor", "sender"])
    def test_post_allowed(self, _make_channel, mailbox, role):
        """Roles with CAN_EDIT should be allowed to POST."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        # POST to threads — will fail with 405 (no CreateModelMixin) but should not be 403
        response = client.post(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_post_blocked_reader(self, _make_channel, mailbox):
        """reader should not be allowed to POST (not in CAN_EDIT or CAN_SEND)."""
        channel = _make_channel("reader")
        client = self._jwt_client(channel, mailbox, role="reader")
        response = client.post(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ── PATCH (edit) ────────────────────────────────────────────────────

    @pytest.mark.parametrize("role", ["editor", "sender"])
    def test_patch_allowed(self, _make_channel, mailbox, role):
        """Roles with CAN_EDIT should be allowed to PATCH."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.patch(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("role", ["reader", "sender_only"])
    def test_patch_blocked(self, _make_channel, mailbox, role):
        """reader and sender_only should not be allowed to PATCH."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.patch(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ── DELETE (edit) ───────────────────────────────────────────────────

    @pytest.mark.parametrize("role", ["editor", "sender"])
    def test_delete_allowed(self, _make_channel, mailbox, role):
        """Roles with CAN_EDIT should be allowed to DELETE."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.delete(
            "/api/v1.0/threads/",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("role", ["reader", "sender_only"])
    def test_delete_blocked(self, _make_channel, mailbox, role):
        """reader and sender_only should not be allowed to DELETE."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.delete(
            "/api/v1.0/threads/",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # ── /api/v1.0/send/ (send endpoint) ────────────────────────────────

    def test_send_endpoint_allows_sender(self, _make_channel, mailbox):
        """sender should pass role checks on /send/ (will fail on draft lookup)."""
        channel = _make_channel("sender")
        client = self._jwt_client(channel, mailbox, role="sender")
        response = client.post(
            "/api/v1.0/send/",
            {"messageId": str(uuid.uuid4()), "senderId": str(mailbox.id)},
            format="json",
        )
        # Should fail on draft lookup (404), not on role enforcement (403)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", ["reader", "sender_only"])
    def test_send_endpoint_blocked_no_post(self, _make_channel, mailbox, role):
        """reader and sender_only are blocked from POST on /send/ by the auth class."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        response = client.post(
            "/api/v1.0/send/",
            {"messageId": str(uuid.uuid4()), "senderId": str(mailbox.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_send_endpoint_blocks_editor(self, _make_channel, mailbox):
        """editor can POST (passes auth class) but /send/ endpoint rejects non-CAN_SEND."""
        channel = _make_channel("editor")
        client = self._jwt_client(channel, mailbox, role="editor")
        response = client.post(
            "/api/v1.0/send/",
            {"messageId": str(uuid.uuid4()), "senderId": str(mailbox.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "send access" in response.data["detail"]

    # ── /api/v1.0/client-bridge/submit/ (submit endpoint) ─────────────

    def test_submit_blocked_reader(self, _make_channel, mailbox):
        """reader is blocked from POST by the auth class (not CAN_EDIT or CAN_SEND)."""
        channel = _make_channel("reader")
        client = self._jwt_client(channel, mailbox, role="reader")
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/client-bridge/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_submit_blocked_editor(self, _make_channel, mailbox):
        """editor passes auth class but submit endpoint rejects non-CAN_SEND."""
        channel = _make_channel("editor")
        client = self._jwt_client(channel, mailbox, role="editor")
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/client-bridge/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("core.api.viewsets.client_bridge.send_message_task")
    @pytest.mark.parametrize("role", ["sender", "sender_only"])
    def test_submit_allows_sender_roles(
        self,
        mock_send_task,  # pylint: disable=unused-argument
        _make_channel,
        mailbox,
        role,  # pylint: disable=unused-argument
    ):
        """Submit endpoint should allow channels with CAN_SEND."""
        channel = _make_channel(role)
        client = self._jwt_client(channel, mailbox, role=role)
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/client-bridge/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_202_ACCEPTED

    @patch("core.api.viewsets.client_bridge.send_message_task")
    def test_submit_sets_sender_user(
        self,
        mock_send_task,  # pylint: disable=unused-argument
        _make_channel,
        mailbox,
    ):
        """Submit should set message.sender_user to the channel's user."""
        channel = _make_channel("sender")
        client = self._jwt_client(channel, mailbox, role="sender")
        mailbox_email = str(mailbox)
        raw_email = _build_raw_email(mailbox_email, "recipient@example.com")

        response = client.post(
            "/api/v1.0/client-bridge/submit/",
            raw_email,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=mailbox_email,
            HTTP_X_RCPT_TO="recipient@example.com",
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        message = models.Message.objects.get(id=response.data["message_id"])
        assert message.sender_user == channel.user

    # ── Auth endpoint returns role ──────────────────────────────────────

    @pytest.mark.parametrize("role", ["reader", "editor", "sender", "sender_only"])
    def test_auth_returns_role(self, api_client, _make_channel, mailbox, role):
        """Auth endpoint JWT should contain the channel's role."""
        _make_channel(role)
        response = api_client.post(
            "/api/v1.0/client-bridge/auth/",
            {"username": str(mailbox), "password": "test-pass"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        payload = jwt.decode(
            response.data["token"], SERVICE_SECRET, algorithms=["HS256"]
        )
        assert payload["role"] == role

    # ── Default role ────────────────────────────────────────────────────

    def test_default_role_is_sender(self, mailbox, channel_user):
        """A channel with no explicit role should default to sender (full access)."""
        channel = ChannelFactory(
            mailbox=mailbox,
            user=channel_user,
            type="client-bridge",
            encrypted_settings={"password": "test-pass"},
            settings={},
        )
        client = self._jwt_client(channel, mailbox)

        # GET should work (sender can read)
        response = client.get(
            "/api/v1.0/threads/",
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        # POST should work (sender can edit) — 405 because ThreadViewSet has no CreateModelMixin
        response = client.post(
            "/api/v1.0/threads/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
