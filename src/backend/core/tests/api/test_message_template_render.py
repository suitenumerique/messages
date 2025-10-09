"""Test render action for MessageTemplateViewSet."""

import uuid
from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory(
        full_name="John Doe", custom_attributes={"job_title": "Adjointe"}
    )


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test mail domain."""
    return factories.MailDomainFactory()


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


class TestMessageTemplateRender:
    """Test the render_template action."""

    def test_unauthorized(self, mailbox, maildomain):
        """Test that unauthorized users cannot render templates."""
        # Mailbox template
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            mailbox=mailbox,
        )
        client = APIClient()

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": mailbox_template.id},
            )
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Maildomain template
        maildomain_template = factories.MessageTemplateFactory(
            name="Maildomain Test Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            maildomain=maildomain,
        )
        client = APIClient()

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": maildomain_template.id},
            )
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_no_access(self, user, mailbox, maildomain):
        """Test that users without access cannot render templates."""
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            mailbox=mailbox,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": mailbox_template.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        maildomain_template = factories.MessageTemplateFactory(
            name="Maildomain Test Template",
            maildomain=maildomain,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": maildomain_template.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_success(self, user, mailbox, role):
        """Test successful template rendering."""
        # Only create access for mailbox here
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )
        # Create templates for mailbox and maildomain
        mailbox_template = factories.MessageTemplateFactory(
            html_body="<p>{name} - {job_title}</p>",
            text_body="{name} - {job_title}",
            mailbox=mailbox,
        )
        maildomain_template = factories.MessageTemplateFactory(
            html_body="<p>Cordialement, {name} - {job_title}</p>",
            text_body="Cordialement, {name} - {job_title}",
            maildomain=mailbox.domain,
        )
        # Create client and authenticate user with access to mailbox
        client = APIClient()
        client.force_authenticate(user=user)

        # Try render of mailbox template
        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": mailbox_template.id},
            )
        )
        # Every thing should be ok here
        assert response.status_code == status.HTTP_200_OK
        # The template will be rendered with the user's full name
        assert "John Doe - Adjointe" in response.data["html_body"]
        assert "John Doe - Adjointe" in response.data["text_body"]

        # Try render of maildomain template. User with access
        # to a mailbox should have access to the templates of maildomain of mailbox too.
        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": maildomain_template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert "Cordialement, John Doe - Adjointe" in response.data["html_body"]
        assert "Cordialement, John Doe - Adjointe" in response.data["text_body"]

    def test_render_template_no_access_mailbox(self, user, mailbox):
        """Test rendering a template from a mailbox that doesn't exist."""
        client = APIClient()
        client.force_authenticate(user=user)
        mailbox_template = factories.MessageTemplateFactory(
            name="Mailbox Test Template",
            mailbox=mailbox,
        )
        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": uuid.uuid4(), "pk": mailbox_template.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_render_template_not_found(self, user, mailbox):
        """Test rendering a non-existent template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={
                    "mailbox_id": mailbox.id,
                    "pk": "00000000-0000-0000-0000-000000000000",
                },
            )
        )
        # get_object() will return 404 if template doesn't exist or user has no access
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_success_with_placeholders(self, user, mailbox):
        """Test successful template rendering with placeholders."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create template with valid placeholders
        template = factories.MessageTemplateFactory(
            name="Placeholder Template",
            html_body="<p>Hello {name}!</p>",
            text_body="Hello {name}!",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        # Check that placeholders are replaced
        assert user.full_name in response.data["html_body"]
        assert user.full_name in response.data["text_body"]

    def test_escapes_html(self, user, mailbox):
        """Test that HTML is escaped in the template rendering."""
        user.full_name = "<b>Alice & Co.</b>"
        user.save()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        template = factories.MessageTemplateFactory(
            html_body="<p>{name}</p>", text_body="{name}", mailbox=mailbox
        )
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["html_body"] == "<p>&lt;b&gt;Alice &amp; Co.&lt;/b&gt;</p>"
        assert resp.data["text_body"] == "<b>Alice & Co.</b>"

    def test_success_with_shared_email_contact(self, user, mailbox):
        """Test successful template rendering with contact."""
        contact = factories.ContactFactory(name="Mairie de Brigny", mailbox=mailbox)
        mailbox.contact = contact
        mailbox.save()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        template = factories.MessageTemplateFactory(
            html_body="<p>{name}</p>", text_body="{name}", mailbox=mailbox
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse(
                "mailbox-message-templates-render-template",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["html_body"] == "<p>Mairie de Brigny</p>"
        assert response.data["text_body"] == "Mairie de Brigny"
