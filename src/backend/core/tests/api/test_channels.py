"""Tests for the channel API endpoints."""

# pylint: disable=redefined-outer-name, unused-argument, too-many-public-methods

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
