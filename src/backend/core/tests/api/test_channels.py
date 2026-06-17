"""Tests for the channel API endpoints."""

# pylint: disable=redefined-outer-name, unused-argument, too-many-public-methods, import-outside-toplevel

import uuid

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models
from core.factories import (
    ChannelFactory,
    LabelFactory,
    MailboxFactory,
    MailDomainAccessFactory,
    MailDomainFactory,
    UserFactory,
)


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a test mailbox with admin access for the user."""
    mailbox = MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
    return mailbox


@pytest.fixture
def api_client(user):
    """Create an authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def channel(mailbox):
    """Create a test channel."""
    return ChannelFactory(mailbox=mailbox, type="widget")


@pytest.mark.django_db
class TestChannelList:
    """Test the channel list endpoint."""

    def test_list_channels(self, api_client, mailbox, channel):
        """Test listing channels for a mailbox."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(channel.id)
        assert response.data[0]["name"] == channel.name
        assert response.data[0]["type"] == "widget"

    def test_list_channels_empty(self, api_client, mailbox):
        """Test listing channels when none exist."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_list_channels_no_access(self, api_client):
        """Test listing channels for a mailbox the user has no access to."""
        other_mailbox = MailboxFactory()
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": other_mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_channels_viewer_access(self, api_client, user):
        """Test listing channels with viewer role (should fail - admin required)."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        ChannelFactory(mailbox=mailbox)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestChannelCreate:
    """Test the channel creation endpoint."""

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_widget_channel(self, api_client, mailbox):
        """Test creating a widget channel."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "name": "My Widget",
            "type": "widget",
            "settings": {
                "subject_template": "New inquiry from {referer_domain}",
                "config": {"enabled": True},
            },
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "My Widget"
        assert response.data["type"] == "widget"
        assert (
            response.data["settings"]["subject_template"]
            == "New inquiry from {referer_domain}"
        )
        assert str(response.data["mailbox"]) == str(mailbox.id)

        # Verify in database
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.mailbox == mailbox
        assert channel.type == "widget"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_with_tags(self, api_client, mailbox):
        """Test creating a widget channel with tags."""
        label = LabelFactory(mailbox=mailbox, name="Widget Inquiries")

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "name": "My Widget with Tags",
            "type": "widget",
            "settings": {
                "subject_template": "Message from {referer_domain}",
                "tags": [str(label.id)],
            },
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert str(label.id) in response.data["settings"]["tags"]

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_with_invalid_tag_uuid(self, api_client, mailbox):
        """Test creating a channel with an invalid tag UUID fails."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "name": "Widget with Invalid Tags",
            "type": "widget",
            "settings": {
                "tags": ["not-a-valid-uuid", "also-invalid"],
            },
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "settings" in response.data
        assert "tags" in response.data["settings"]

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_with_nonexistent_tag(self, api_client, mailbox):
        """Test creating a channel with a tag that doesn't exist fails."""
        nonexistent_id = str(uuid.uuid4())
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "name": "Widget with Missing Tags",
            "type": "widget",
            "settings": {
                "tags": [nonexistent_id],
            },
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "settings" in response.data
        assert "tags" in response.data["settings"]

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_with_tag_from_other_mailbox(self, api_client, mailbox):
        """Test creating a channel with a tag from another mailbox fails."""
        other_mailbox = MailboxFactory()
        other_label = LabelFactory(mailbox=other_mailbox, name="Other Label")

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "name": "Widget with Wrong Mailbox Tag",
            "type": "widget",
            "settings": {
                "tags": [str(other_label.id)],
            },
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "settings" in response.data
        assert "tags" in response.data["settings"]

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_no_access(self, api_client):
        """Test creating a channel for a mailbox the user has no access to."""
        other_mailbox = MailboxFactory()
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": other_mailbox.id})
        data = {"name": "Test", "type": "widget", "settings": {}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_viewer_access(self, api_client, user):
        """Test creating a channel with viewer role (should fail)."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {"name": "Test", "type": "widget", "settings": {}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_unauthorized_type(self, api_client, mailbox):
        """Test creating a channel with an unauthorized type."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {"name": "Test API Key", "type": "api_key", "settings": {}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "type" in response.data
        assert "not authorized" in str(response.data["type"]).lower()
        assert "api_key" in str(response.data["type"])

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget", "api_key"])
    def test_create_channel_authorized_type(self, api_client, mailbox):
        """Test creating a channel with an authorized type."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {"name": "Test Widget", "type": "widget", "settings": {}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["type"] == "widget"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_create_channel_missing_type_is_rejected(self, api_client, mailbox):
        """Omitting ``type`` on CREATE must be a 400 — never silently default
        to "mta" and bypass FEATURE_MAILBOX_ADMIN_CHANNELS. Regression lock
        for the bug where the model field default and the serializer's
        ``if channel_type:`` short-circuit combined to let "mta" channels
        through on any nested mailbox/user POST."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.post(
            url, {"name": "no type", "settings": {}}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "type" in response.data


@pytest.mark.django_db
class TestChannelRetrieve:
    """Test the channel retrieve endpoint."""

    def test_retrieve_channel(self, api_client, mailbox, channel):
        """Test retrieving a specific channel."""
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(channel.id)
        assert response.data["name"] == channel.name

    def test_retrieve_channel_not_found(self, api_client, mailbox):
        """Test retrieving a non-existent channel."""
        url = reverse(
            "mailbox-channels-detail",
            kwargs={
                "mailbox_id": mailbox.id,
                "pk": "00000000-0000-0000-0000-000000000000",
            },
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestChannelUpdate:
    """Test the channel update endpoint."""

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_update_channel(self, api_client, mailbox, channel):
        """Test updating a channel."""
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        data = {
            "name": "Updated Widget Name",
            "type": "widget",
            "settings": {
                "subject_template": "Updated subject from {referer_domain}",
            },
        }

        response = api_client.put(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Widget Name"
        assert (
            response.data["settings"]["subject_template"]
            == "Updated subject from {referer_domain}"
        )

        # Verify in database
        channel.refresh_from_db()
        assert channel.name == "Updated Widget Name"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_partial_update_channel(self, api_client, mailbox, channel):
        """Test partially updating a channel."""
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        data = {"name": "Partially Updated Name"}

        response = api_client.patch(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Partially Updated Name"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_update_channel_no_access(self, api_client, mailbox, channel):
        """Test updating a channel for a mailbox the user has no admin access to."""
        # Remove admin access
        mailbox.accesses.all().delete()

        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        data = {"name": "Should Not Update"}

        response = api_client.put(url, data, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestChannelDelete:
    """Test the channel deletion endpoint."""

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_delete_channel(self, api_client, mailbox, channel):
        """Test deleting a channel."""
        channel_id = channel.id
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

        response = api_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Channel.objects.filter(id=channel_id).exists()

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_delete_channel_no_access(self, api_client, mailbox, channel):
        """Test deleting a channel without admin access."""
        # Remove admin access
        mailbox.accesses.all().delete()

        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

        response = api_client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestChannelDomainAdminAccess:
    """Test that domain admins can also manage channels."""

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_domain_admin_can_list_channels(self, api_client, user):
        """Test that domain admin can list channels."""
        domain = MailDomainFactory()
        MailDomainAccessFactory(
            maildomain=domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        mailbox = MailboxFactory(domain=domain)
        channel = ChannelFactory(mailbox=mailbox)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(channel.id)

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["widget"])
    def test_domain_admin_can_create_channel(self, api_client, user):
        """Test that domain admin can create a channel."""
        domain = MailDomainFactory()
        MailDomainAccessFactory(
            maildomain=domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        mailbox = MailboxFactory(domain=domain)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        data = {"name": "Domain Admin Widget", "type": "widget", "settings": {}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Domain Admin Widget"


@pytest.mark.django_db
class TestChannelEncryptedSettings:
    """Test encrypted_settings and user fields on the Channel model."""

    def test_encrypted_settings_stored_on_model(self, mailbox):
        """encrypted_settings can be set and read back."""
        channel = ChannelFactory(
            mailbox=mailbox, type="widget", settings={"public": "value"}
        )
        channel.encrypted_settings = {"secret_key": "s3cret"}
        channel.save()

        channel.refresh_from_db()
        assert channel.encrypted_settings["secret_key"] == "s3cret"
        assert channel.settings["public"] == "value"

    def test_encrypted_settings_not_in_api_response(self, api_client, mailbox):
        """encrypted_settings must never leak in the REST API — neither as
        a top-level key nor smuggled into the visible ``settings`` payload."""
        channel = ChannelFactory(mailbox=mailbox, type="widget")
        channel.encrypted_settings = {"password": "s3cret"}
        channel.save()

        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "encrypted_settings" not in response.data
        assert "password" not in response.data

        # Defense in depth: a serializer bug that copied encrypted_settings
        # into the visible ``settings`` JSON would also be a leak. Inspect
        # both the secret keys and the secret values.
        settings_payload = response.data.get("settings") or {}
        assert "password" not in settings_payload
        assert "s3cret" not in settings_payload.values()

    def test_encrypted_settings_not_in_list_response(self, api_client, mailbox):
        """encrypted_settings must not appear in list responses either —
        same defense-in-depth check on each item's ``settings`` payload."""
        channel = ChannelFactory(mailbox=mailbox, type="widget")
        channel.encrypted_settings = {"token": "abc"}
        channel.save()

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        for item in response.data:
            assert "encrypted_settings" not in item
            settings_payload = item.get("settings") or {}
            assert "token" not in settings_payload
            assert "abc" not in settings_payload.values()

    def test_user_field_target_on_user_scope_channel(self, user):
        """Channel.user is the *target* user for scope_level=user channels."""
        channel = ChannelFactory(
            user=user, scope_level="user", mailbox=None, maildomain=None
        )
        channel.refresh_from_db()
        assert channel.user == user

    def test_user_field_creator_audit_on_mailbox_scope_channel(self, user, mailbox):
        """On non-user-scope channels, Channel.user is the creator audit
        — an OPTIONAL FK pointing at the User who created it via DRF."""
        channel = ChannelFactory(mailbox=mailbox, type="widget", user=user)
        channel.refresh_from_db()
        assert channel.user == user

    def test_user_field_nullable_on_mailbox_scope_channel(self, mailbox):
        """The creator FK is nullable — channels created by the CLI / data
        migration / Django admin may not have a creator stamped."""
        channel = ChannelFactory(mailbox=mailbox, type="widget")
        assert channel.user is None


@pytest.mark.django_db
class TestChannelReservedSettingsKeys:
    """The serializer rejects callers that try to write reserved settings
    keys (e.g. ``api_key_hashes``). Server-side generators write directly
    to encrypted_settings, callers cannot influence its contents."""

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["api_key"])
    def test_post_with_reserved_key_in_settings_is_rejected(self, api_client, mailbox):
        """Smuggling api_key_hashes via settings is a 400."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.post(
            url,
            data={
                "name": "Tries to inject a hash",
                "type": "api_key",
                "settings": {
                    "scopes": ["messages:send"],
                    "api_key_hashes": ["a" * 64],
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["api_key"])
    def test_unrelated_settings_keys_pass_through(self, api_client, mailbox):
        """Non-reserved keys in settings (e.g. expires_at) flow through
        the API as caller-supplied data — only the reserved list is locked
        down."""
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.post(
            url,
            data={
                "name": "Has expires_at",
                "type": "api_key",
                "settings": {
                    "scopes": ["messages:send"],
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.settings["expires_at"] == "2030-01-01T00:00:00Z"
        # And api_key_hashes is what the server generated, not what the
        # caller smuggled (the caller didn't smuggle anything here, but
        # we still assert the encrypted_settings shape).
        assert "api_key_hashes" in channel.encrypted_settings
        assert "api_key_hashes" not in channel.settings


@pytest.mark.django_db
class TestWebhookChannelSettings:
    """Validation of the outbound-webhook-specific settings fields."""

    URL_KEY = "url"

    def _post(self, api_client, mailbox, settings):
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        return api_client.post(
            url,
            data={
                "name": "wh",
                "type": "webhook",
                "settings": settings,
            },
            format="json",
        )

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_create_minimal_webhook(self, api_client, mailbox):
        """A JWT webhook surfaces its one-time ``webhook_secret`` on create."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        # The signing secret is returned exactly once, at creation time.
        assert response.data.get("webhook_secret")
        assert "webhook_api_key" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_create_minimal_webhook_api_key(self, api_client, mailbox):
        """An api_key webhook surfaces its one-time ``webhook_api_key``."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "api_key",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        # api_key channels return the derived key, never the raw JWT secret.
        assert response.data.get("webhook_api_key")
        assert "webhook_secret" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_regenerate_secret_jwt_webhook(self, api_client, mailbox):
        """Rotating a JWT webhook returns a fresh ``webhook_secret``."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        channel_id = create.data["id"]
        original_secret = create.data["webhook_secret"]

        url = reverse(
            "mailbox-channels-regenerate-secret",
            kwargs={"mailbox_id": mailbox.id, "pk": channel_id},
        )
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["id"] == str(channel_id)
        new_secret = response.data["webhook_secret"]
        assert new_secret
        assert new_secret != original_secret
        assert "webhook_api_key" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_regenerate_secret_api_key_webhook(self, api_client, mailbox):
        """Rotating an api_key webhook returns a fresh ``webhook_api_key``."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "api_key",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        channel_id = create.data["id"]
        original_key = create.data["webhook_api_key"]

        url = reverse(
            "mailbox-channels-regenerate-secret",
            kwargs={"mailbox_id": mailbox.id, "pk": channel_id},
        )
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["id"] == str(channel_id)
        new_key = response.data["webhook_api_key"]
        assert new_key
        assert new_key != original_key
        assert "webhook_secret" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_create_with_all_dispatcher_options(self, api_client, mailbox):
        """A webhook channel accepts the full set of dispatcher options."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
                "phase": "before_spam",
                "blocking": True,
                "format": "jmap",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.settings["phase"] == "before_spam"
        assert channel.settings["blocking"] is True
        assert channel.settings["format"] == "jmap"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_rejects_invalid_format(self, api_client, mailbox):
        """An unknown webhook ``format`` is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
                "format": "yaml",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_accepts_jmap_without_body_format(self, api_client, mailbox):
        """The ``jmap_without_body`` format is a valid webhook format."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
                "format": "jmap_without_body",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_rejects_invalid_phase(self, api_client, mailbox):
        """An unknown webhook ``phase`` is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
                "phase": "whenever",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_rejects_non_bool_blocking(self, api_client, mailbox):
        """A non-boolean ``blocking`` value is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
                "blocking": "yes",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"])
    def test_rejects_patch_with_invalid_phase(self, api_client, mailbox):
        """A PATCH that touches only ``settings`` must still re-run the
        webhook validator (same airtight rule as api_key scopes)."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "events": ["message.received"],
                "auth_method": "jwt",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": create.data["id"]},
        )
        response = api_client.patch(
            url,
            data={
                "settings": {
                    "url": "https://hook.example.com/in",
                    "events": ["message.received"],
                    "auth_method": "jwt",
                    "phase": "bogus",
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
