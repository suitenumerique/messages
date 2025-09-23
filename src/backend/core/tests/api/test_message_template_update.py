"""Test update operations for MessageTemplateViewSet."""

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


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test mail domain."""
    return factories.MailDomainFactory()


class TestMessageTemplateUpdate:
    """Test update operations for MessageTemplateViewSet."""

    def test_unauthorized(self, mailbox):
        """Test that unauthorized users cannot update templates."""
        template = factories.MessageTemplateFactory(
            html_body="<p>Original content</p>",
            text_body="Original content",
            mailbox=mailbox,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()

        data = {
            "name": "Updated Template",
        }

        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template was not updated
        template.refresh_from_db()
        assert template.name != "Updated Template"

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_no_access(self, user, mailbox, role):
        """Test that users without proper permission cannot update templates."""
        # Give user any role but ADMIN
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )

        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            mailbox=mailbox,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Mailbox Test Template Updated",
        }

        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify template was not updated
        mailbox_template.refresh_from_db()
        assert mailbox_template.name == "Mailbox Test Template"

        # some on maildomain of mailbox template
        maildomain_template = factories.MessageTemplateFactory(
            name="Maildomain Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=mailbox.domain,
        )
        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": maildomain_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert (
            "You do not have permission to manage message templates for this mailbox or domain"
            in response.data["detail"]
        )
        maildomain_template.refresh_from_db()
        assert maildomain_template.name == "Maildomain Test Template"

    def test_success(self, user, mailbox):
        """Test updating an email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template with valid content
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            mailbox=mailbox,
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
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False

        # check that the blob was updated
        mailbox_template.refresh_from_db()
        content = json.loads(mailbox_template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        # same with user with ADMIN role on maildomain of the mailbox
        admin_on_maildomain = factories.UserFactory(
            full_name="Jane Doee", custom_attributes={"job_title": "Adjointe"}
        )
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=admin_on_maildomain,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        client.force_authenticate(user=admin_on_maildomain)
        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False

        # assert response.data["raw_body"] == json.dumps("updated raw content")
        mailbox_template.refresh_from_db()
        mailbox_template.mailbox = mailbox
        content = json.loads(mailbox_template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_success_with_mailbox_id(self, user, mailbox):
        """Test trying to update a template with mailbox_id."""
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            mailbox=mailbox,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
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
            "mailbox_id": str(other_mailbox.id),
        }
        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False

        # check that the blob was updated
        mailbox_template.refresh_from_db()
        content = json.loads(mailbox_template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        # check that the mailbox was not updated because it's not allowed to change mailbox_id
        assert mailbox_template.mailbox == mailbox
        assert mailbox_template.maildomain is None

    def test_success_with_maildomain_id(self, user, maildomain):
        """Test trying to update a template with maildomain_id."""
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=maildomain,
        )
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        other_maildomain = factories.MailDomainFactory()
        factories.MailDomainAccessFactory(
            maildomain=other_maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
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
            "maildomain_id": str(other_maildomain.id),
        }
        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False
        # check that the maildomain was not updated because it's not allowed to change maildomain_id
        assert mailbox_template.mailbox is None
        assert mailbox_template.maildomain == maildomain

    def test_partial_update(self, user, mailbox):
        """Test partially updating an message template."""
        # Create mailbox access for user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template with valid content
        reply_template = factories.MessageTemplateFactory(
            name="Original Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Partially Updated Template",
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": reply_template.id}), data
        )
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

    def test_content_fields_atomic_validation(self, user, mailbox):
        """Test that content fields must be updated together atomically."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template
        template = factories.MessageTemplateFactory(
            name="Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Try to update only html_body - should fail
        data = {
            "html_body": "<p>Updated content</p>",
            "mailbox_id": str(mailbox.id),
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )

        # Try to update only text_body - should fail
        data = {
            "text_body": "Updated content",
            "mailbox_id": str(mailbox.id),
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )

        # Try to update only raw_body - should fail
        data = {
            "raw_body": RAW_DATA,
            "mailbox_id": str(mailbox.id),
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )

        # Update all three fields together - should succeed
        data = {
            "html_body": "<p>Updated content</p>",
            "text_body": "Updated content",
            "raw_body": RAW_DATA,
            "mailbox_id": str(mailbox.id),
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
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

    def test_is_forced_maildomain(self, user, maildomain):
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
            reverse("message-templates-detail", kwargs={"pk": signature2.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that first template is no longer forced
        signature1.refresh_from_db()
        signature2.refresh_from_db()

        assert signature1.is_forced is False
        assert signature2.is_forced is True

    def test_is_forced_mailbox(self, user, mailbox):
        """Test that updating a template to forced sets others to not forced for the same mailbox and type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create first template as forced
        reply1 = factories.MessageTemplateFactory(
            name="First Forced Reply Template",
            html_body="<p>First reply content</p>",
            text_body="First reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
            is_forced=True,
        )

        # Create second template as not forced
        reply2 = factories.MessageTemplateFactory(
            name="Second Reply Template",
            html_body="<p>Second reply content</p>",
            text_body="Second reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
            is_forced=False,
        )

        assert reply1.is_forced is True
        assert reply2.is_forced is False

        # Update second template to be forced
        client = APIClient()
        client.force_authenticate(user=user)

        data = {"is_forced": True}

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": reply2.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that first template is no longer forced
        reply1.refresh_from_db()
        reply2.refresh_from_db()

        assert reply1.is_forced is False
        assert reply2.is_forced is True
