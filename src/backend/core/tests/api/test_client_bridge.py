"""Tests for the client bridge: app passwords, per-request auth, scopes, sending."""

# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument

import hashlib
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core import models
from core.enums import (
    APP_PASSWORD_SCOPES,
    ChannelApiKeyScope,
    ChannelScopeLevel,
    ChannelTypes,
    MailboxRoleChoices,
)
from core.factories import (
    ChannelFactory,
    MailboxFactory,
    MailDomainFactory,
    MessageFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)

from messages.settings import Base

CHANNEL_URL = "/api/v1.0/client-bridge/channel/"
SUBMIT_URL = "/api/v1.0/submit/"
THREADS_URL = "/api/v1.0/threads/"
FLAG_URL = "/api/v1.0/flag/"
TASK_MOCK = "core.api.viewsets.submit.send_message_task"

# The role presets of the frontend form.
READER = [
    ChannelApiKeyScope.THREADS_READ.value,
    ChannelApiKeyScope.MESSAGES_READ.value,
    ChannelApiKeyScope.MESSAGES_READ_BODY.value,
]
EDITOR = [*READER, ChannelApiKeyScope.THREADS_WRITE.value]
SENDER = [*EDITOR, ChannelApiKeyScope.MESSAGES_SEND.value]
SENDER_ONLY = [ChannelApiKeyScope.MESSAGES_SEND.value]
PRESETS = {
    "reader": READER,
    "editor": EDITOR,
    "sender": SENDER,
    "sender_only": SENDER_ONLY,
}


@pytest.fixture
def channel_user():
    return UserFactory()


@pytest.fixture
def mailbox(channel_user):
    mailbox = MailboxFactory(
        local_part="alice", domain=MailDomainFactory(name="example.com")
    )
    mailbox.accesses.create(user=channel_user, role=MailboxRoleChoices.ADMIN)
    return mailbox


def make_channel(mailbox, user, scopes=None, **kwargs):
    """Create a client-bridge channel through rotate_secret, return (channel, password)."""
    channel = ChannelFactory.build(
        mailbox=mailbox,
        user=user,
        type=ChannelTypes.CLIENT_BRIDGE,
        settings={"scopes": SENDER if scopes is None else scopes},
        encrypted_settings={},
        **kwargs,
    )
    password = channel.rotate_secret(save=False)
    channel.save()
    return channel, password


def app_client(email, password):
    client = APIClient()
    client.credentials(HTTP_X_MAILBOX_EMAIL=email, HTTP_X_APP_PASSWORD=password)
    return client


def raw_email(mail_from, rcpt_to="bob@external.test"):
    return (
        f"From: {mail_from}\r\n"
        f"To: {rcpt_to}\r\n"
        "Subject: Hello\r\n"
        f"Message-ID: <{uuid.uuid4()}@example.com>\r\n"
        "Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Body\r\n"
    ).encode()


# =============================================================================
# Startup check of CLIENTBRIDGE_PUBLIC_CONFIG
# =============================================================================

VALID_PUBLIC_CONFIG = {
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_security": "SSL/TLS",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_security": "STARTTLS",
}


class TestPublicConfigCheck:
    """Base._check_clientbridge_public_config, run at startup when the flag is on."""

    def _check(self, config):
        with patch.object(Base, "CLIENTBRIDGE_PUBLIC_CONFIG", config):
            Base._check_clientbridge_public_config()  # pylint: disable=protected-access

    def test_valid(self):
        self._check(VALID_PUBLIC_CONFIG)

    @pytest.mark.parametrize(
        "config",
        [
            {**VALID_PUBLIC_CONFIG, "imap_hots": "typo"},
            {k: v for k, v in VALID_PUBLIC_CONFIG.items() if k != "smtp_port"},
            {**VALID_PUBLIC_CONFIG, "imap_port": "993"},
            {**VALID_PUBLIC_CONFIG, "smtp_port": True},
        ],
        ids=["unknown-key", "missing-key", "port-as-string", "port-as-bool"],
    )
    def test_malformed_rejected(self, config):
        with pytest.raises(ValueError):
            self._check(config)

    def test_empty_warns(self, caplog):
        self._check({})
        assert "CLIENTBRIDGE_PUBLIC_CONFIG is empty" in caplog.text


# =============================================================================
# e2e seed command
# =============================================================================


@pytest.mark.django_db
class TestE2EClientBridgeSeed:
    """e2e_clientbridge must be safe to run again (Playwright runs it per project)."""

    def test_rerun_resets_credentials_and_does_not_duplicate(self):
        # pylint: disable-next=import-outside-toplevel
        from e2e.management.commands.e2e_clientbridge import (
            BROWSERS,
            CLIENTBRIDGE_APP_PASSWORD,
            Command,
        )

        domain = MailDomainFactory(name="example.local")
        mailboxes = []
        for local_part in [f"user.e2e.{b}" for b in BROWSERS] + ["shared.e2e"]:
            mailbox = MailboxFactory(local_part=local_part, domain=domain)
            mailbox.accesses.create(user=UserFactory(), role=MailboxRoleChoices.ADMIN)
            mailboxes.append(mailbox)

        Command().handle()
        # Simulate a channel left by an older seed with other credentials
        models.Channel.objects.filter(type=ChannelTypes.CLIENT_BRIDGE).update(
            settings={"role": "sender"}, encrypted_settings={"password": "old"}
        )
        Command().handle()

        expected_hash = hashlib.sha256(CLIENTBRIDGE_APP_PASSWORD.encode()).hexdigest()
        for mailbox in mailboxes:
            channel = models.Channel.objects.get(
                mailbox=mailbox, type=ChannelTypes.CLIENT_BRIDGE
            )
            assert channel.encrypted_settings == {"password_hash": expected_hash}
            assert set(channel.settings["scopes"]) == APP_PASSWORD_SCOPES
        for mailbox in mailboxes[: len(BROWSERS)]:
            subjects = sorted(
                models.Thread.objects.filter(accesses__mailbox=mailbox).values_list(
                    "subject", flat=True
                )
            )
            assert subjects == ["IMAP read test", "IMAP unread test"]


# =============================================================================
# App password storage
# =============================================================================


@pytest.mark.django_db
class TestRotateSecret:
    """Channel.rotate_secret for client-bridge channels."""

    def test_password_is_base58_and_only_hash_is_stored(self, mailbox, channel_user):
        channel, password = make_channel(mailbox, channel_user)
        assert len(password) == models.Channel.CLIENT_BRIDGE_PASSWORD_LENGTH
        assert set(password) <= set(models.Channel.CLIENT_BRIDGE_PASSWORD_ALPHABET)
        channel.refresh_from_db()
        assert channel.encrypted_settings == {
            "password_hash": hashlib.sha256(password.encode()).hexdigest()
        }
        assert password not in str(channel.settings)

    def test_rotation_replaces_the_hash(self, mailbox, channel_user):
        channel, old_password = make_channel(mailbox, channel_user)
        new_password = channel.rotate_secret()
        assert new_password != old_password
        channel.refresh_from_db()
        assert channel.encrypted_settings["password_hash"] == (
            hashlib.sha256(new_password.encode()).hexdigest()
        )


# =============================================================================
# Channel API (mailbox admins)
# =============================================================================


@pytest.mark.django_db
class TestChannelApi:
    """Creating and managing client-bridge channels through the channel API."""

    @pytest.fixture(autouse=True)
    def _allow_type(self, settings):
        settings.FEATURE_MAILBOX_ADMIN_CHANNELS = ["client-bridge", "api_key"]

    @pytest.fixture
    def admin_client(self, channel_user):
        client = APIClient()
        client.force_authenticate(user=channel_user)
        return client

    def _url(self, mailbox, suffix=""):
        return f"/api/v1.0/mailboxes/{mailbox.id}/channels/{suffix}"

    def _create(self, client, mailbox, channel_settings):
        return client.post(
            self._url(mailbox),
            {
                "name": "Thunderbird",
                "type": "client-bridge",
                "settings": channel_settings,
            },
            format="json",
        )

    def test_create_returns_password_once(self, admin_client, mailbox, channel_user):
        response = self._create(admin_client, mailbox, {"scopes": SENDER})
        assert response.status_code == 201, response.content
        password = response.data["password"]
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.settings == {"scopes": SENDER}
        assert channel.user == channel_user
        assert channel.scope_level == ChannelScopeLevel.MAILBOX
        assert channel.encrypted_settings["password_hash"] == (
            hashlib.sha256(password.encode()).hexdigest()
        )

        detail = admin_client.get(self._url(mailbox, f"{channel.id}/"))
        assert detail.status_code == 200
        assert "password" not in detail.data
        assert "password_hash" not in str(detail.data)

    @pytest.mark.parametrize(
        "channel_settings",
        [
            {},
            {"scopes": "threads:read"},
            {"scopes": ["mailboxes:read"]},
            {"scopes": ["maildomains:create"]},
        ],
        ids=["missing", "not-a-list", "api-key-only", "global-only"],
    )
    def test_invalid_scopes_rejected(self, admin_client, mailbox, channel_settings):
        assert self._create(admin_client, mailbox, channel_settings).status_code == 400

    def test_api_key_cannot_hold_app_password_scopes(self, admin_client, mailbox):
        response = admin_client.post(
            self._url(mailbox),
            {"name": "K", "type": "api_key", "settings": {"scopes": ["threads:read"]}},
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("key", ["password", "password_hash"])
    def test_caller_supplied_password_rejected(self, admin_client, mailbox, key):
        response = self._create(admin_client, mailbox, {"scopes": SENDER, key: "x"})
        assert response.status_code == 400

    def test_patch_scopes(self, admin_client, mailbox, channel_user):
        channel, _ = make_channel(mailbox, channel_user, scopes=SENDER)
        response = admin_client.patch(
            self._url(mailbox, f"{channel.id}/"),
            {"settings": {"scopes": READER}},
            format="json",
        )
        assert response.status_code == 200, response.content
        channel.refresh_from_db()
        assert channel.settings["scopes"] == READER

    def test_patch_without_scopes_rejected(self, admin_client, mailbox, channel_user):
        """A settings PATCH replaces the whole JSON: dropping scopes is refused."""
        channel, _ = make_channel(mailbox, channel_user, scopes=READER)
        response = admin_client.patch(
            self._url(mailbox, f"{channel.id}/"), {"settings": {}}, format="json"
        )
        assert response.status_code == 400
        channel.refresh_from_db()
        assert channel.settings["scopes"] == READER

    def test_rejected_when_feature_disabled(self, admin_client, mailbox, settings):
        settings.FEATURE_CLIENTBRIDGE = False
        response = self._create(admin_client, mailbox, {"scopes": SENDER})
        assert response.status_code == 400

    def test_rejected_when_not_an_allowed_type(self, admin_client, mailbox, settings):
        settings.FEATURE_MAILBOX_ADMIN_CHANNELS = ["widget"]
        response = self._create(admin_client, mailbox, {"scopes": SENDER})
        assert response.status_code == 400

    def test_rejected_as_personal_channel(self, admin_client):
        response = admin_client.post(
            "/api/v1.0/users/me/channels/",
            {"name": "X", "type": "client-bridge", "settings": {"scopes": SENDER}},
            format="json",
        )
        assert response.status_code == 400

    def test_regenerate_secret_returns_new_password(
        self, admin_client, mailbox, channel_user
    ):
        channel, old_password = make_channel(mailbox, channel_user)
        response = admin_client.post(
            self._url(mailbox, f"{channel.id}/regenerate-secret/")
        )
        assert response.status_code == 200, response.content
        new_password = response.data["password"]
        assert new_password != old_password
        old = app_client(str(mailbox), old_password).get(CHANNEL_URL)
        new = app_client(str(mailbox), new_password).get(CHANNEL_URL)
        assert (old.status_code, new.status_code) == (401, 200)


# =============================================================================
# Per-request authentication
# =============================================================================


@pytest.mark.django_db
class TestAppPasswordAuthentication:
    """X-Mailbox-Email + X-App-Password, checked on every request."""

    def test_channel_endpoint(self, mailbox, channel_user):
        channel, password = make_channel(mailbox, channel_user, scopes=SENDER_ONLY)
        response = app_client("Alice@Example.com", password).get(CHANNEL_URL)
        assert response.status_code == 200, response.content
        assert response.data == {
            "channel_id": str(channel.id),
            "mailbox_id": str(mailbox.id),
            "mailbox_email": "alice@example.com",
            "scopes": SENDER_ONLY,
        }
        channel.refresh_from_db()
        assert channel.last_used_at is not None

    @pytest.mark.parametrize(
        "email,password",
        [
            ("alice@example.com", "wrong-password"),
            ("nobody@example.com", None),
            ("not-an-email", None),
        ],
    )
    def test_invalid_credentials(self, mailbox, channel_user, email, password):
        _, real_password = make_channel(mailbox, channel_user)
        response = app_client(email, password or real_password).get(CHANNEL_URL)
        assert response.status_code == 401

    def test_missing_headers(self, mailbox, channel_user):
        _, password = make_channel(mailbox, channel_user)
        client = APIClient()
        client.credentials(HTTP_X_APP_PASSWORD=password)
        assert client.get(CHANNEL_URL).status_code == 401

    def test_inactive_channel(self, mailbox, channel_user):
        _, password = make_channel(mailbox, channel_user, is_active=False)
        assert app_client(str(mailbox), password).get(CHANNEL_URL).status_code == 401

    def test_inactive_user(self, mailbox, channel_user):
        _, password = make_channel(mailbox, channel_user)
        channel_user.is_active = False
        channel_user.save()
        assert app_client(str(mailbox), password).get(CHANNEL_URL).status_code == 401

    def test_other_channel_type_ignored(self, mailbox):
        password = "same-value-as-a-hash"
        ChannelFactory(
            mailbox=mailbox,
            type="widget",
            settings={"scopes": SENDER},
            encrypted_settings={
                "password_hash": hashlib.sha256(password.encode()).hexdigest()
            },
        )
        assert app_client(str(mailbox), password).get(CHANNEL_URL).status_code == 401

    def test_ignored_when_feature_disabled(self, mailbox, channel_user, settings):
        _, password = make_channel(mailbox, channel_user)
        settings.FEATURE_CLIENTBRIDGE = False
        response = app_client(str(mailbox), password).get(
            THREADS_URL, {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestScopes:
    """Each endpoint the bridge calls requires its scope; the rest is refused."""

    def _threads(self, client, mailbox):
        return client.get(THREADS_URL, {"mailbox_id": str(mailbox.id)})

    def _flag(self, client, mailbox):
        thread = ThreadFactory()
        ThreadAccessFactory(thread=thread, mailbox=mailbox)
        return client.post(
            FLAG_URL,
            {
                "flag": "unread",
                "value": False,
                "mailbox_id": str(mailbox.id),
                "thread_ids": [str(thread.id)],
                "read_at": None,
            },
            format="json",
        )

    @pytest.mark.parametrize("preset", PRESETS)
    def test_threads_list(self, mailbox, channel_user, preset):
        _, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        response = self._threads(app_client(str(mailbox), password), mailbox)
        assert response.status_code == (403 if preset == "sender_only" else 200)

    @pytest.mark.parametrize("preset", PRESETS)
    def test_flag(self, mailbox, channel_user, preset):
        _, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        response = self._flag(app_client(str(mailbox), password), mailbox)
        assert response.status_code == (200 if preset in ("editor", "sender") else 403)

    @pytest.mark.parametrize(
        "scopes,status",
        [(READER, 200), ([ChannelApiKeyScope.MESSAGES_READ.value], 403)],
    )
    def test_eml_needs_body_scope(self, mailbox, channel_user, scopes, status):
        message = MessageFactory(raw_mime=raw_email("x@example.com"))
        ThreadAccessFactory(thread=message.thread, mailbox=mailbox)
        _, password = make_channel(mailbox, channel_user, scopes=scopes)
        response = app_client(str(mailbox), password).get(
            f"/api/v1.0/messages/{message.id}/eml/"
        )
        assert response.status_code == status

    @pytest.mark.parametrize("preset", PRESETS)
    def test_other_endpoints_ignore_app_passwords(self, mailbox, channel_user, preset):
        """Views that don't opt in never see the app password: not authenticated."""
        _, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        client = app_client(str(mailbox), password)
        assert client.get("/api/v1.0/users/me/").status_code == 401
        assert (
            client.get(f"/api/v1.0/mailboxes/{mailbox.id}/channels/").status_code == 401
        )
        assert client.post("/api/v1.0/send/", {}, format="json").status_code == 401

    @pytest.mark.parametrize("preset", PRESETS)
    def test_undeclared_actions_refused(self, mailbox, channel_user, preset):
        """Default deny on opted-in views: threads-list is declared, threads-detail isn't."""
        _, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        thread = ThreadFactory()
        ThreadAccessFactory(thread=thread, mailbox=mailbox)
        response = app_client(str(mailbox), password).get(f"{THREADS_URL}{thread.id}/")
        assert response.status_code == 403

    def test_scopes_read_from_database(self, mailbox, channel_user):
        channel, password = make_channel(mailbox, channel_user, scopes=SENDER)
        client = app_client(str(mailbox), password)
        assert self._threads(client, mailbox).status_code == 200
        channel.settings = {"scopes": SENDER_ONLY}
        channel.save()
        assert self._threads(client, mailbox).status_code == 403

    def test_app_password_scopes_match_enum(self):
        assert set(SENDER) == APP_PASSWORD_SCOPES


# =============================================================================
# Sending through /submit/
# =============================================================================


@pytest.mark.django_db
class TestSubmit:
    """POST /submit/ with an app password."""

    def _submit(self, client, mailbox, body):
        return client.post(
            SUBMIT_URL,
            data=body,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="bob@external.test",
        )

    @pytest.mark.parametrize("preset", ["sender", "sender_only"])
    def test_success(
        self, mailbox, channel_user, preset, django_capture_on_commit_callbacks
    ):
        channel, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        # Delivery is dispatched on commit.
        with patch(TASK_MOCK) as task, django_capture_on_commit_callbacks(execute=True):
            response = self._submit(
                app_client(str(mailbox), password), mailbox, raw_email(str(mailbox))
            )
        assert response.status_code == 202, response.content
        message = models.Message.objects.get(id=response.data["message_id"])
        assert message.channel == channel
        assert message.sender_user == channel_user
        assert message.is_sender
        assert not message.is_draft
        task.delay.assert_called_once_with(str(message.id))

    @pytest.mark.parametrize("preset", ["reader", "editor"])
    def test_presets_without_send(self, mailbox, channel_user, preset):
        _, password = make_channel(mailbox, channel_user, scopes=PRESETS[preset])
        response = self._submit(
            app_client(str(mailbox), password), mailbox, raw_email(str(mailbox))
        )
        assert response.status_code == 403
        assert not models.Message.objects.exists()

    def test_other_mailbox_refused(self, mailbox, channel_user):
        _, password = make_channel(mailbox, channel_user)
        other = MailboxFactory(domain=mailbox.domain)
        other.accesses.create(user=channel_user, role=MailboxRoleChoices.ADMIN)
        response = self._submit(
            app_client(str(mailbox), password), other, raw_email(str(other))
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("access_role", [None, MailboxRoleChoices.EDITOR])
    def test_user_without_send_access_refused(self, mailbox, channel_user, access_role):
        _, password = make_channel(mailbox, channel_user)
        if access_role is None:
            mailbox.accesses.all().delete()
        else:
            mailbox.accesses.update(role=access_role)
        response = self._submit(
            app_client(str(mailbox), password), mailbox, raw_email(str(mailbox))
        )
        assert response.status_code == 403

    def test_from_must_match_mailbox(self, mailbox, channel_user):
        _, password = make_channel(mailbox, channel_user)
        response = self._submit(
            app_client(str(mailbox), password),
            mailbox,
            raw_email("someone.else@example.com"),
        )
        assert response.status_code == 403
