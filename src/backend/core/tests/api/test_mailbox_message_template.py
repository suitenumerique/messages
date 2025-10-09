"""Test CRUD operations for MailboxMessageTemplateViewSet."""

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


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="mailbox_template")
def fixture_mailbox_template(mailbox):
    """Create a test template for a mailbox."""
    return factories.MessageTemplateFactory(
        html_body="<p>Template Content</p>",
        text_body="Template Content",
        mailbox=mailbox,
    )


@pytest.fixture(name="list_url")
def fixture_list_url(mailbox):
    """Url to list message templates for a mailbox."""
    return reverse(
        "mailbox-message-templates-list",
        kwargs={"mailbox_id": mailbox.id},
    )


@pytest.fixture(name="detail_url")
def fixture_detail_url(mailbox):
    """Url to get a message template for a mailbox."""
    return lambda template_id: reverse(
        "mailbox-message-templates-detail",
        kwargs={"mailbox_id": mailbox.id, "pk": template_id},
    )


class TestMailboxMessageTemplateList:
    """Test list operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, list_url):
        """Test that unauthenticated users cannot list templates."""
        client = APIClient()
        response = client.get(list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, list_url):
        """Test that users without mailbox access cannot list templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(list_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_templates(self, user, mailbox, list_url):
        """Test listing all templates."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        reply_template = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )
        new_message_template = factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=enums.MessageTemplateTypeChoices.NEW_MESSAGE,
            mailbox=mailbox,
        )
        signature_template = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(list_url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        templates_by_type = {t["type"]: t for t in response.data}
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["reply"]["id"] == str(reply_template.id)
        assert templates_by_type["new_message"]["id"] == str(new_message_template.id)

    def test_filter_by_type(self, user, mailbox, list_url):
        """Test filtering list by template type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        reply_template = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )
        new_message_template = factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=enums.MessageTemplateTypeChoices.NEW_MESSAGE,
            mailbox=mailbox,
        )
        signature_template = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(list_url, {"type": "reply"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["type"] == "reply"
        assert response.data[0]["id"] == str(reply_template.id)

        response = client.get(list_url, {"type": ["new_message"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["type"] == "new_message"
        assert response.data[0]["id"] == str(new_message_template.id)

        response = client.get(list_url, {"type": ["signature"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["type"] == "signature"
        assert response.data[0]["id"] == str(signature_template.id)

        response = client.get(list_url, {"type": ["reply", "new_message", "signature"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        templates_by_type = {t["type"]: t for t in response.data}
        assert templates_by_type["signature"]["id"] == str(signature_template.id)
        assert templates_by_type["reply"]["id"] == str(reply_template.id)
        assert templates_by_type["new_message"]["id"] == str(new_message_template.id)

        # test with invalid type
        response = client.get(list_url, {"type": ["reply", "invalid_type"]})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["type"] == "reply"
        assert response.data[0]["id"] == str(reply_template.id)


class TestMailboxMessageTemplateCreate:
    """Test create operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, list_url):
        """Test that unauthorized users cannot create templates."""
        client = APIClient()

        data = {
            "name": "Test Template",
            "type": "reply",
            "html_body": "<p>Hello {recipient_name}</p>",
            "text_body": "Hello {recipient_name}",
            "raw_body": RAW_DATA,
            "is_active": True,
        }
        response = client.post(
            list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, list_url):
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
        }

        response = client.post(
            list_url,
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success(self, user, mailbox, list_url):
        """Test creating a new template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template Signature",
            "html_body": "<hr />\n<p>{name} - Mairie de Brigny</p>",
            "is_active": True,
            "raw_body": RAW_DATA,
            "text_body": "----\n\n{name} - Mairie de Brigny\n",
            "type": "signature",
        }
        response = client.post(
            list_url,
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
        assert template.mailbox == mailbox
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_create_with_mailbox_and_maildomain_in_payload(
        self, user, mailbox, list_url
    ):
        """Test creating a template with mailbox and maildomain in payload but only mailbox is used from the context."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        other_mailbox = factories.MailboxFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox": str(other_mailbox.id),
            "maildomain": str(mailbox.domain.id),
        }
        response = client.post(list_url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        template = models.MessageTemplate.objects.get()
        # mailbox is used from the context
        assert template.mailbox == mailbox
        assert not template.maildomain
        assert template.name == "Test Template"


class TestMailboxMessageTemplateUpdate:
    """Test update operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, mailbox_template, detail_url):
        """Test that unauthorized users cannot update templates."""
        client = APIClient()

        data = {
            "name": "Updated Template",
        }

        response = client.put(
            detail_url(mailbox_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template was not updated
        mailbox_template.refresh_from_db()
        assert mailbox_template.name != "Updated Template"

    def test_forbidden(self, user, mailbox_template, detail_url):
        """Test that users without proper role cannot update templates."""
        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Updated Template",
        }

        response = client.put(
            detail_url(mailbox_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify template was not updated
        mailbox_template.refresh_from_db()
        assert mailbox_template.name != "Updated Template"

    def test_cannot_change_mailbox(self, user, mailbox, mailbox_template, detail_url):
        """Test that we cannot change the mailbox of a template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        # Create another mailbox
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
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
            "mailbox": str(other_mailbox.id),
        }

        response = client.put(
            detail_url(mailbox_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify template was not updated
        mailbox_template.refresh_from_db()
        assert mailbox_template.mailbox == mailbox
        assert mailbox_template.name == "Updated Template"

    def test_success(self, user, mailbox, mailbox_template, detail_url):
        """Test updating a template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
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
        }

        response = client.put(
            detail_url(mailbox_template.id),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "reply"
        assert response.data["is_active"] is False

        # check that the blob was updated
        mailbox_template.refresh_from_db()
        content = json.loads(mailbox_template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT


class TestMailboxMessageTemplateDelete:
    """Test delete operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, mailbox_template, detail_url):
        """Test that unauthorized users cannot delete templates."""
        client = APIClient()

        response = client.delete(
            detail_url(mailbox_template.id),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template still exists
        assert models.MessageTemplate.objects.filter(id=mailbox_template.id).exists()

    def test_forbidden(self, user, mailbox_template, detail_url):
        """Test that users without proper role cannot delete templates."""
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            detail_url(mailbox_template.id),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert models.MessageTemplate.objects.filter(id=mailbox_template.id).exists()

    def test_success(self, user, mailbox, mailbox_template, detail_url):
        """Test deleting a template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            detail_url(mailbox_template.id),
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(
            id=mailbox_template.id
        ).exists()

        # Verify template is deleted
        response = client.get(
            detail_url(mailbox_template.id),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMailboxMessageTemplateRetrieve:
    """Test retrieve operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, mailbox_template, detail_url):
        """Test that unauthenticated users cannot retrieve templates."""
        client = APIClient()
        response = client.get(detail_url(mailbox_template.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, mailbox_template, detail_url):
        """Test that users without mailbox access cannot retrieve templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(detail_url(mailbox_template.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success(self, user, mailbox, mailbox_template, detail_url):
        """Test retrieving a single template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(detail_url(mailbox_template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(mailbox_template.id)
        assert response.data["name"] == mailbox_template.name


class TestMailboxMessageTemplatePartialUpdate:
    """Test partial update (PATCH) operations for MailboxMessageTemplateViewSet."""

    def test_unauthorized(self, mailbox_template, detail_url):
        """Test that unauthorized users cannot partially update templates."""
        client = APIClient()
        response = client.patch(
            detail_url(mailbox_template.id), {"name": "Patched Name"}, format="json"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, mailbox_template, detail_url):
        """Test that users without proper role cannot partially update templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.patch(
            detail_url(mailbox_template.id), {"name": "Patched Name"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success_patch_name_only(self, user, mailbox, mailbox_template, detail_url):
        """Test partially updating only the name field."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.patch(
            detail_url(mailbox_template.id),
            {"name": "Patched Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Patched Name"

        # Verify other fields unchanged
        mailbox_template.refresh_from_db()
        assert mailbox_template.name == "Patched Name"
