"""Tests for the channel API endpoints."""

# pylint: disable=redefined-outer-name, unused-argument, too-many-public-methods, import-outside-toplevel, too-many-lines

import uuid
from unittest.mock import patch

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
from core.services.ssrf import SSRFValidationError


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
    """Validation of the outbound-webhook-specific settings fields.

    These target the non-SSRF settings validation (format / trigger /
    auth_method / secret rotation) with placeholder hosts like
    ``hook.example.com`` that don't resolve. The Test config runs DEBUG=False,
    under which the serializer does a live ``validate_hostname`` DNS lookup and
    400s on an unresolvable host — so the create/update tests pin DEBUG=True to
    skip that config-time SSRF layer (covered on its own in
    ``TestWebhookChannelCreateSSRF``)."""

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

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_create_minimal_webhook(self, api_client, mailbox):
        """A JWT webhook surfaces its one-time ``secret`` on create."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        # The signing secret is returned exactly once, at creation time.
        assert response.data.get("secret")
        assert "api_key" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_create_minimal_webhook_api_key(self, api_client, mailbox):
        """An api_key webhook surfaces its one-time ``api_key``."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "api_key",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        # api_key channels return the derived key, never the raw JWT secret.
        assert response.data.get("api_key")
        assert "secret" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_regenerate_secret_jwt_webhook(self, api_client, mailbox):
        """Rotating a JWT webhook returns a fresh ``secret``."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        channel_id = create.data["id"]
        original_secret = create.data["secret"]

        url = reverse(
            "mailbox-channels-regenerate-secret",
            kwargs={"mailbox_id": mailbox.id, "pk": channel_id},
        )
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["id"] == str(channel_id)
        new_secret = response.data["secret"]
        assert new_secret
        assert new_secret != original_secret
        assert "api_key" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_regenerate_secret_api_key_webhook(self, api_client, mailbox):
        """Rotating an api_key webhook returns a fresh ``api_key``."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "api_key",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        channel_id = create.data["id"]
        original_key = create.data["api_key"]

        url = reverse(
            "mailbox-channels-regenerate-secret",
            kwargs={"mailbox_id": mailbox.id, "pk": channel_id},
        )
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["id"] == str(channel_id)
        new_key = response.data["api_key"]
        assert new_key
        assert new_key != original_key
        assert "secret" not in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_create_with_all_dispatcher_options(self, api_client, mailbox):
        """A webhook channel accepts the full set of dispatcher options."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.inbound",
                "auth_method": "jwt",
                "format": "jmap",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.settings["trigger"] == "message.inbound"
        assert channel.settings["format"] == "jmap"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_rejects_invalid_format(self, api_client, mailbox):
        """An unknown webhook ``format`` is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
                "format": "yaml",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_accepts_jmap_metadata_format(self, api_client, mailbox):
        """The ``jmap_metadata`` format is a valid webhook format."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
                "format": "jmap_metadata",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_rejects_invalid_trigger(self, api_client, mailbox):
        """An unknown webhook ``trigger`` is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "whenever",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_rejects_missing_trigger(self, api_client, mailbox):
        """A webhook channel without a ``trigger`` is rejected with HTTP 400."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_rejects_patch_with_invalid_trigger(self, api_client, mailbox):
        """A PATCH that touches only ``settings`` must still re-run the
        webhook validator (same airtight rule as api_key scopes)."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
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
                    "trigger": "bogus",
                    "auth_method": "jwt",
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_partial_patch_preserves_auth_method(self, api_client, mailbox):
        """A settings PATCH that omits ``auth_method`` keeps the existing
        one instead of being rejected — auth_method pairs with the stored
        secret and shouldn't have to be re-sent on every unrelated edit."""
        create = self._post(
            api_client,
            mailbox,
            {
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "api_key",
            },
        )
        assert create.status_code == status.HTTP_201_CREATED, create.content
        channel_id = create.data["id"]

        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel_id},
        )
        response = api_client.patch(
            url,
            data={
                "settings": {
                    "url": "https://hook.example.com/changed",
                    "trigger": "message.delivered",
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        channel = models.Channel.objects.get(id=channel_id)
        # The new url stuck, and the omitted auth_method was carried forward
        # and persisted (not dropped on the replace-save).
        assert channel.settings["url"] == "https://hook.example.com/changed"
        assert channel.settings["auth_method"] == "api_key"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_regenerate_secret_rejects_unknown_auth_method(self, api_client, mailbox):
        """Rotating a webhook whose auth_method we can't surface is rejected
        up front, so the old secret is never invalidated for a new one the
        caller could never learn."""
        # An unknown auth_method (legacy/admin-edited row). A truthy value
        # also stops the factory from auto-filling a valid "jwt".
        channel = ChannelFactory(
            type="webhook",
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "bogus",
            },
            encrypted_settings={"secret": "whsec_original"},
        )
        url = reverse(
            "mailbox-channels-regenerate-secret",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        response = api_client.post(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        channel.refresh_from_db()
        # Old secret untouched — rotation never ran.
        assert channel.encrypted_settings["secret"] == "whsec_original"

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    def test_rejects_missing_url(self, api_client, mailbox):
        """A webhook channel without settings.url is rejected with 400."""
        response = self._post(
            api_client,
            mailbox,
            {"trigger": "message.delivered", "auth_method": "jwt"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    @pytest.mark.parametrize(
        "bad_url",
        [
            "ftp://host/x",  # non-http(s) scheme
            "javascript:alert(1)",  # script scheme
            "https:///no-host",  # no host
        ],
    )
    def test_rejects_invalid_url_scheme_or_host(self, api_client, mailbox, bad_url):
        """Non-http(s) schemes, script URLs and host-less URLs are rejected
        up front — the create-time check the SSRF guard backstops at
        dispatch."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": bad_url,
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=False)
    def test_rejects_plain_http_outside_debug(self, api_client, mailbox):
        """Plain http would leak the HMAC/JWT signing headers in transit, so
        it is rejected unless running under DEBUG (local-dev escape hatch)."""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "http://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content


@pytest.mark.django_db
class TestWebhookChannelCreateSSRF:
    """Create/update-time SSRF validation of webhook ``settings.url``.

    A fail-fast UX layer (NOT the security boundary — ``SSRFSafeSession``
    re-validates with IP pinning on every POST): outside DEBUG, the
    serializer resolves the host via ``validate_hostname`` and 400s on an
    internal / IP-literal / unresolvable target. Under DEBUG (the Test
    default) it is skipped so local-dev receivers work.
    """

    def _post(self, api_client, mailbox, settings):
        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        return api_client.post(
            url,
            data={"name": "wh", "type": "webhook", "settings": settings},
            format="json",
        )

    _SETTINGS = {
        "url": "https://hook.example.com/in",
        "trigger": "message.delivered",
        "auth_method": "jwt",
    }

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=False)
    @patch("core.api.serializers.validate_hostname")
    def test_rejects_internal_host(self, mock_validate, api_client, mailbox):
        """A host resolving to a private/internal address is a 400 with a
        ``settings`` error."""
        mock_validate.side_effect = SSRFValidationError(
            "hook.example.com resolves to private IP address"
        )
        response = self._post(api_client, mailbox, self._SETTINGS)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "settings" in response.data
        mock_validate.assert_called_once_with("hook.example.com")

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=False)
    @patch("core.api.serializers.validate_hostname")
    def test_rejects_unresolvable_host(self, mock_validate, api_client, mailbox):
        """A host that doesn't resolve is rejected (would silently stall mail
        at dispatch otherwise)."""
        mock_validate.side_effect = SSRFValidationError("Unable to resolve hostname")
        response = self._post(api_client, mailbox, self._SETTINGS)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "settings" in response.data

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=False)
    @patch("core.api.serializers.validate_hostname")
    def test_accepts_public_host(self, mock_validate, api_client, mailbox):
        """A host that validates to a public IP is accepted (201)."""
        mock_validate.return_value = ["93.184.216.34"]
        response = self._post(api_client, mailbox, self._SETTINGS)
        assert response.status_code == status.HTTP_201_CREATED, response.content
        mock_validate.assert_called_once_with("hook.example.com")

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=True)
    @patch("core.api.serializers.validate_hostname")
    def test_skipped_under_debug(self, mock_validate, api_client, mailbox):
        """Under DEBUG the SSRF lookup is skipped entirely — creation with an
        unresolvable fake host still 201s and ``validate_hostname`` is never
        called. (The Test config runs DEBUG=False, so this pins DEBUG=True to
        exercise the local-dev escape hatch.)"""
        response = self._post(
            api_client,
            mailbox,
            {
                "url": "https://totally-fake-host.invalid/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED, response.content
        mock_validate.assert_not_called()

    @override_settings(FEATURE_MAILBOX_ADMIN_CHANNELS=["webhook"], DEBUG=False)
    @patch("core.api.serializers.validate_hostname")
    def test_validation_runs_on_patch(self, mock_validate, api_client, mailbox):
        """A settings PATCH that changes the url must re-run the SSRF check —
        otherwise an admin could PATCH past the create-time guard."""
        # Build the channel directly so creation doesn't itself invoke the
        # (patched) validator we want to scope to the PATCH.
        channel = ChannelFactory(
            type="webhook",
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/in",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        mock_validate.side_effect = SSRFValidationError(
            "evil.internal resolves to private IP address"
        )
        url = reverse(
            "mailbox-channels-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        response = api_client.patch(
            url,
            data={
                "settings": {
                    "url": "https://evil.internal/in",
                    "trigger": "message.delivered",
                    "auth_method": "jwt",
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "settings" in response.data
