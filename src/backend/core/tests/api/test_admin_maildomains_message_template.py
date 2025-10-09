"""Test create operations for MessageTemplateViewSet."""

import json

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.tests.api.conftest import MESSAGE_TEMPLATE_RAW_DATA as RAW_DATA_STRUCT
from core.tests.api.conftest import MESSAGE_TEMPLATE_RAW_DATA_JSON as RAW_DATA

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory(
        full_name="John Doe", custom_attributes={"job_title": "Adjointe"}
    )


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test maildomain."""
    return factories.MailDomainFactory()


@pytest.fixture(name="admin_list_url")
def fixture_admin_list_url(maildomain):
    """Url to list message templates for a maildomain."""
    return reverse(
        "admin-maildomains-message-templates-list",
        kwargs={"maildomain_pk": maildomain.id},
    )


@pytest.fixture(name="admin_detail_url")
def fixture_admin_detail_url(maildomain):
    """Url to get a message template for a maildomain."""
    return lambda template_id: reverse(
        "admin-maildomains-message-templates-detail",
        kwargs={"maildomain_pk": maildomain.id, "pk": template_id},
    )


@pytest.fixture(name="maildomain_template")
def fixture_maildomain_template(maildomain):
    """Create a test template for a maildomain."""
    return factories.MessageTemplateFactory(
        html_body="<p>Content to delete</p>",
        text_body="Content to delete",
        maildomain=maildomain,
    )


class TestAdminMailDomainMessageTemplateList:
    """Test list operations for AdminMailDomainMessageTemplateViewSet."""

    def test_unauthorized(self, admin_list_url):
        """Test that unauthenticated users cannot list templates."""
        client = APIClient()
        response = client.get(admin_list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, admin_list_url):
        """Test that users without admin access cannot list templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(admin_list_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_templates(self, user, maildomain, admin_list_url):
        """Test listing all templates."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        reply_template = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            maildomain=maildomain,
        )
        new_message_template = factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=enums.MessageTemplateTypeChoices.NEW_MESSAGE,
            maildomain=maildomain,
        )
        signature_template = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=maildomain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Test listing all templates
        response = client.get(admin_list_url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        templates_by_type = {t["type"]: t for t in response.data}
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["reply"]["id"] == str(reply_template.id)
        assert templates_by_type["new_message"]["id"] == str(new_message_template.id)

        # Test filtering by single type
        response = client.get(admin_list_url, {"type": ["signature"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["type"] == "signature"
        assert response.data[0]["id"] == str(signature_template.id)

        # Test filtering by multiple types
        response = client.get(admin_list_url, {"type": ["signature", "reply"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        templates_by_type = {t["type"]: t for t in response.data}
        assert "signature" in templates_by_type
        assert "reply" in templates_by_type
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["reply"]["id"] == str(reply_template.id)

        # Test filtering by multiple types with some invalid types (should be ignored)
        response = client.get(
            admin_list_url, {"type": ["signature", "invalid_type", "new_message"]}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        templates_by_type = {t["type"]: t for t in response.data}
        assert "signature" in templates_by_type
        assert "new_message" in templates_by_type
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["new_message"]["id"] == str(new_message_template.id)

        # Test filtering by all valid types
        response = client.get(
            admin_list_url, {"type": ["signature", "reply", "new_message"]}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        templates_by_type = {t["type"]: t for t in response.data}
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["reply"]["id"] == str(reply_template.id)
        assert templates_by_type["new_message"]["id"] == str(new_message_template.id)


class TestAdminMailDomainMessageTemplateCreate:
    """Test create operations for AdminMailDomainMessageTemplateViewSet."""

    def test_unauthorized(self, admin_list_url):
        """Test that unauthorized users cannot create templates."""
        client = APIClient()

        data = {
            "name": "Test Template",
            "type": "reply",
            "html_body": "<p>Hello {recipient_name}</p>",
            "text_body": "Hello {recipient_name}",
            "raw_body": RAW_DATA,
            "is_active": True,
            "is_forced": False,
        }
        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, admin_list_url):
        """Test that users without proper role cannot create templates."""

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Hello {recipient_name}</p>",
            "text_body": "Hello {recipient_name}",
            "raw_body": RAW_DATA,
            "type": "reply",
            "is_active": True,
            "is_forced": False,
        }

        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success(self, user, maildomain, admin_list_url):
        """Test creating a new email template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template Signature",
            "html_body": "<hr />\n<p>{name} - Mairie de Brigny</p>",
            "is_active": True,
            "is_forced": False,
            "raw_body": RAW_DATA,
            "text_body": "----\n\n{name} - Mairie de Brigny\n",
            "type": "signature",
        }
        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template Signature"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        assert template.maildomain == maildomain
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_with_invalid_type(self, user, maildomain, admin_list_url):
        """Test creating a template with invalid type."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Invalid Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "invalid_type",
        }

        response = client.post(admin_list_url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "type" in response.data

    def test_content_fields_atomic_validation(self, user, maildomain, admin_list_url):
        """Test that content fields must be created together atomically."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Try to create with only html_body - should fail
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "type": "signature",
        }

        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )
        # Try to create with only text_body - should fail
        data = {
            "name": "Test Template",
            "text_body": "Content",
            "type": "signature",
        }

        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )

        # Try to create with only raw_body - should fail
        data = {
            "name": "Test Template",
            "raw_body": RAW_DATA,
            "type": "signature",
        }

        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )

        # Create with all three fields together - should succeed
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
        }

        response = client.post(
            admin_list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Verify all fields were created
        template = models.MessageTemplate.objects.get(name="Test Template")
        assert template.html_body == "<p>Content</p>"
        assert template.text_body == "Content"
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_with_maildomain_id(self, user, maildomain, admin_list_url):
        """Test creating a template with maildomain_id."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
        }

        response = client.post(admin_list_url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.maildomain == maildomain
        assert template.mailbox is None

    def test_superuser(self, user, maildomain, admin_list_url):
        """Test creating a template with superuser."""
        user.is_superuser = True
        user.save()
        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
        }
        response = client.post(admin_list_url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.maildomain == maildomain

    def test_create_with_mailbox_and_maildomain_in_payload(
        self, user, maildomain, mailbox, admin_list_url
    ):
        """Test creating a template with mailbox and maildomain in payload
        but only maildomain is used from the context."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox": str(mailbox.id),
            "maildomain": str(maildomain.id),
        }
        response = client.post(admin_list_url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert models.MessageTemplate.objects.exists()
        # maildomain is used from the context
        assert models.MessageTemplate.objects.get().maildomain == maildomain
        assert not models.MessageTemplate.objects.get().mailbox


class TestAdminMailDomainMessageTemplateUpdate:
    """Test admin maildomain update operations for MessageTemplateViewSet."""

    def test_unauthorized(self, maildomain, admin_detail_url):
        """Test that unauthorized users cannot update templates."""
        template = factories.MessageTemplateFactory(
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=maildomain,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()

        data = {
            "name": "Updated Template",
        }

        response = client.put(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template was not updated
        template.refresh_from_db()
        assert template.name != "Updated Template"

    def test_no_access(self, user, maildomain, admin_detail_url):
        """Test that users without proper permission cannot update templates."""
        maildomain_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=maildomain,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Mailbox Test Template Updated",
        }

        response = client.put(
            admin_detail_url(maildomain_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify template was not updated
        maildomain_template.refresh_from_db()
        assert maildomain_template.name == "Mailbox Test Template"

    def test_cannot_change_maildomain(self, user, maildomain, admin_detail_url):
        """Test that we cannot change the maildomain of a template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create another maildomain
        other_maildomain = factories.MailDomainFactory()
        factories.MailDomainAccessFactory(
            maildomain=other_maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a template in the first maildomain
        template = factories.MessageTemplateFactory(
            name="Original Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            maildomain=maildomain,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Updated Template",
            "html_body": "<p>Updated content</p>",
            "text_body": "Updated content",
            "raw_body": RAW_DATA,
            "type": "reply",
            "is_active": False,
            "is_forced": False,
            "maildomain": str(other_maildomain.id),
        }

        response = client.put(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify template was not updated
        template.refresh_from_db()
        assert template.maildomain == maildomain
        assert template.name == "Updated Template"

    def test_success(self, user, maildomain, admin_detail_url):
        """Test updating an email template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a template with valid content
        maildomain_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=maildomain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Updated Template",
            "html_body": "<p>Updated content</p>",
            "text_body": "Updated content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "is_active": False,
            "is_forced": False,
        }

        response = client.put(
            admin_detail_url(maildomain_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False

        # check that the blob was updated
        maildomain_template.refresh_from_db()
        content = json.loads(maildomain_template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_partial_update(self, user, maildomain, admin_detail_url):
        """Test partially updating an message template."""
        # Create mailbox access for user
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a template with valid content
        reply_template = factories.MessageTemplateFactory(
            name="Original Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            maildomain=maildomain,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Partially Updated Template",
        }

        response = client.patch(admin_detail_url(reply_template.id), data)
        assert response.status_code == status.HTTP_200_OK
        # only name should have been updated
        assert response.data["name"] == "Partially Updated Template"
        assert response.data["type"] == "reply"
        assert response.data["html_body"] == "<p>Original content</p>"
        assert response.data["text_body"] == "Original content"
        assert response.data["is_active"]

        # check that the template has been updated
        reply_template.refresh_from_db()
        assert reply_template.name == "Partially Updated Template"

    def test_content_fields_atomic_validation(self, user, maildomain, admin_detail_url):
        """Test that content fields must be updated together atomically."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a template
        template = factories.MessageTemplateFactory(
            name="Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=maildomain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Try to update only html_body - should fail
        data = {
            "html_body": "<p>Updated content</p>",
        }

        response = client.patch(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )

        # Try to update only text_body - should fail
        data = {
            "text_body": "Updated content",
        }

        response = client.patch(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )

        # Try to update only raw_body - should fail
        data = {
            "raw_body": RAW_DATA,
        }

        response = client.patch(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided together."
            in str(response.data)
        )

        # Update all three fields together - should succeed
        data = {
            "html_body": "<p>Updated content</p>",
            "text_body": "Updated content",
            "raw_body": RAW_DATA,
        }

        response = client.patch(
            admin_detail_url(template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify all fields were updated
        template.refresh_from_db()
        assert template.html_body == "<p>Updated content</p>"
        assert template.text_body == "Updated content"
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_forced_template_becomes_inactive(self, user, maildomain, admin_detail_url):
        """Test that when a forced template is updated to be inactive, it should also become non-forced."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a forced signature template
        signature = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=maildomain,
            is_forced=True,
            is_active=True,
        )

        assert signature.is_forced is True
        assert signature.is_active is True

        # Update template to be inactive
        client = APIClient()
        client.force_authenticate(user=user)

        data = {"is_active": False}

        response = client.patch(
            admin_detail_url(signature.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that template is no longer forced and is inactive
        signature.refresh_from_db()
        assert signature.is_forced is False
        assert signature.is_active is False

    def test_is_forced_maildomain(self, user, maildomain, admin_detail_url):
        """Test that updating a template to forced sets others to not forced for the same maildomain and type."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create signature template as forced
        signature1 = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=maildomain,
            is_forced=True,
        )

        # Create second signature template as not forced
        signature2 = factories.MessageTemplateFactory(
            name="Second Signature Template",
            html_body="<p>Second signature content</p>",
            text_body="Second signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=maildomain,
            is_forced=False,
        )

        assert signature1.is_forced is True
        assert signature2.is_forced is False

        # Update second template to be forced
        client = APIClient()
        client.force_authenticate(user=user)

        data = {"is_forced": True}

        response = client.patch(
            admin_detail_url(signature2.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that first template is no longer forced
        signature1.refresh_from_db()
        signature2.refresh_from_db()

        assert signature1.is_forced is False
        assert signature2.is_forced is True


class TestAdminMailDomainMessageTemplateDelete:
    """Test delete operations for MessageTemplateViewSet."""

    def test_unauthorized(self, admin_detail_url, maildomain_template):
        """Test that unauthorized users cannot delete templates."""

        client = APIClient()

        response = client.delete(
            admin_detail_url(maildomain_template.id),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template still exists
        assert models.MessageTemplate.objects.filter(id=maildomain_template.id).exists()

    def test_without_any_permission(self, user, admin_detail_url, maildomain_template):
        """Test that users without proper permission cannot delete templates."""

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            admin_detail_url(maildomain_template.id),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.MessageTemplate.objects.filter(id=maildomain_template.id).exists()

    def test_success(self, user, maildomain, admin_detail_url, maildomain_template):
        """Test deleting an email template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            admin_detail_url(maildomain_template.id),
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(
            id=maildomain_template.id
        ).exists()

        # Verify template is deleted
        response = client.get(
            admin_detail_url(maildomain_template.id),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent(self, user, maildomain, admin_detail_url):
        """Test deleting a nonexistent template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            admin_detail_url("00000000-0000-0000-0000-000000000000")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_superuser(self, user, maildomain, admin_detail_url):
        """Test deleting a template with superuser."""
        user.is_superuser = True
        user.save()
        client = APIClient()
        client.force_authenticate(user=user)

        template = factories.MessageTemplateFactory(
            maildomain=maildomain,
        )

        response = client.delete(
            admin_detail_url(template.id),
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(id=template.id).exists()
