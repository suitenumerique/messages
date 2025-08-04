# pylint: disable=too-many-lines
"""Test the MessageTemplateViewSet."""

from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory(
        full_name="John Doe", custom_attributes={"job_title": "Adjointe"}
    )


@pytest.fixture(name="superuser")
def fixture_superuser():
    """Create a test superuser."""
    return factories.UserFactory(is_superuser=True, is_staff=True)


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test mail domain."""
    return factories.MailDomainFactory()


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


class TestMessageTemplateViewSet:
    """Test the MessageTemplateViewSet."""

    def test_list_for_admin_manage_domain(self, user, mailbox):
        """Test that admin can see all templates for their accessible maildomains.
        Is case 1 of the viewset when admin manage domain."""

        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        for _ in range(3):
            template = factories.MessageTemplateFactory()
            template.maildomains.add(other_maildomain)

        # Create signature template for the maildomain
        signature = models.MessageTemplate.objects.create(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        signature.maildomains.add(mailbox.domain)

        # Create reply template for the mailbox
        reply = models.MessageTemplate.objects.create(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        reply.mailboxes.add(mailbox)

        # Create new message template for the mailbox
        new_message = models.MessageTemplate.objects.create(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            kind=models.MessageTemplateKindChoices.NEW_MESSAGE,
        )
        new_message.mailboxes.add(mailbox)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # First try with no access for user authenticated. Should return no templates
        # because no access to maildomain.
        response = client.get(
            reverse("message-templates-list"),
            {"maildomain_id": str(mailbox.domain.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        # Then try with only mailbox access. Should return no templates
        # because no access to maildomain.
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        response = client.get(
            reverse("message-templates-list"),
            {"maildomain_id": str(mailbox.domain.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        # Then try with maildomain access. Should return signature template
        # because user is admin of this maildomain.
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        response = client.get(
            reverse("message-templates-list"),
            {"maildomain_id": str(mailbox.domain.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["name"] == "Signature Template"

    def test_list_for_new_message_creation(self, user, mailbox):
        """Test that regular users can see templates for his accessible mailbox
        and maildomain of the mailbox. Is case 2 of the viewset when user write a new message."""

        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        for _ in range(3):
            template = factories.MessageTemplateFactory()
            template.maildomains.add(other_maildomain)

        # Create signature template for the maildomain
        signature = models.MessageTemplate.objects.create(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        signature.maildomains.add(mailbox.domain)

        # Create reply template for the mailbox
        reply = models.MessageTemplate.objects.create(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        reply.mailboxes.add(mailbox)

        # Create new message template for the mailbox
        new_message = models.MessageTemplate.objects.create(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            kind=models.MessageTemplateKindChoices.NEW_MESSAGE,
        )
        new_message.mailboxes.add(mailbox)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # First try with no access for user authenticated. Should return no templates
        # because no access to mailbox.
        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        # Then try with mailbox access. Should return templates of this mailbox
        # and maildomain of this mailbox (new message, reply, signature)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert response.data[0]["name"] == "New Message Template"
        assert response.data[1]["name"] == "Reply Template"
        assert response.data[2]["name"] == "Signature Template"

    def test_list_for_superuser(self, superuser, maildomain):
        """Test that superuser can see all templates for a maildomain."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        for _ in range(3):
            template = factories.MessageTemplateFactory()
            template.maildomains.add(other_maildomain)

        # Create template for the maildomain
        template = factories.MessageTemplateFactory()
        template.maildomains.add(maildomain)

        client = APIClient()
        client.force_authenticate(user=superuser)
        response = client.get(
            reverse("message-templates-list"), {"maildomain_id": str(maildomain.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_list_unauthorized(self):
        """Test that unauthorized users cannot access the list."""
        client = APIClient()
        # Even with valid parameters, unauthorized users should get 401
        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_filter_by_kind(self, user, mailbox):
        """Test filtering by template kind."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        reply_template = models.MessageTemplate.objects.create(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        reply_template.mailboxes.add(mailbox)

        signature_template = models.MessageTemplate.objects.create(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        signature_template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        # Filter by reply kind
        response = client.get(
            reverse("message-templates-list"),
            {"kind": "reply", "mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our reply template
        assert len(response.data) >= 1
        # Check that all results are reply kind
        for result in response.data:
            assert result["kind"] == "reply"

    def test_filter_by_is_active(self, user, mailbox):
        """Test filtering by active status."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        active_template = models.MessageTemplate.objects.create(
            name="Active Template",
            html_body="<p>Active content</p>",
            text_body="Active content",
            kind=models.MessageTemplateKindChoices.REPLY,
            is_active=True,
        )
        active_template.mailboxes.add(mailbox)

        inactive_template = models.MessageTemplate.objects.create(
            name="Inactive Template",
            html_body="<p>Inactive content</p>",
            text_body="Inactive content",
            kind=models.MessageTemplateKindChoices.REPLY,
            is_active=False,
        )
        inactive_template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        # Filter by active templates
        response = client.get(
            reverse("message-templates-list"),
            {"is_active": "true", "mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our active template
        assert len(response.data) >= 1
        # Check that all results are active
        for result in response.data:
            assert result["is_active"] is True

    def test_filter_by_mailbox(self, user, mailbox):
        """Test filtering by mailbox."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = models.MessageTemplate.objects.create(
            name="Mailbox Template",
            html_body="<p>Mailbox content</p>",
            text_body="Mailbox content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our template for this mailbox
        assert len(response.data) >= 1

    def test_filter_by_maildomain(self, user, maildomain):
        """Test filtering by mail domain."""
        # Create mailbox access for user
        mailbox = factories.MailboxFactory(domain=maildomain)

        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        template = models.MessageTemplate.objects.create(
            name="Maildomain Template",
            html_body="<p>Maildomain content</p>",
            text_body="Maildomain content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        # FIXME!
        template.maildomains.add(maildomain)
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"), {"maildomain_id": str(maildomain.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our template for this maildomain
        assert len(response.data) >= 1

    def test_filter_by_is_default_mailbox(self, user, mailbox):
        """Test filtering by default status for mailbox."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = models.MessageTemplate.objects.create(
            name="Default Mailbox Template",
            html_body="<p>Default mailbox content</p>",
            text_body="Default mailbox content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        # Create the through model directly to set is_default
        models.MessageTemplateMailbox.objects.create(
            template=template,
            mailbox=mailbox,
            is_default=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": str(mailbox.id), "is_default": "true"},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our default template for this mailbox
        assert len(response.data) >= 1

    def test_filter_by_is_default_maildomain(self, user, maildomain):
        """Test filtering by default status for mail domain."""
        mailbox = factories.MailboxFactory(domain=maildomain)
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = models.MessageTemplate.objects.create(
            name="Default Maildomain Template",
            html_body="<p>Default maildomain content</p>",
            text_body="Default maildomain content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        # Create the through model directly to set is_default
        models.MessageTemplateMailDomain.objects.create(
            template=template,
            maildomain=maildomain,
            is_default=True,
        )
        # Also associate the template with the mailbox so the user can see it
        models.MessageTemplateMailbox.objects.create(
            template=template,
            mailbox=mailbox,
            is_default=False,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"),
            {"maildomain_id": str(maildomain.id), "is_default": "true"},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find at least our default template for this maildomain
        assert len(response.data) >= 1

    def test_update_template_sets_others_to_not_default_maildomain(
        self, user, maildomain
    ):
        """Test that updating a template to default sets others to not default for the same maildomain and kind."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create signature template as default
        signature1 = models.MessageTemplate.objects.create(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        models.MessageTemplateMailDomain.objects.create(
            template=signature1,
            maildomain=maildomain,
            is_default=True,
        )

        # Create second signature template as not default
        signature2 = models.MessageTemplate.objects.create(
            name="Second Signature Template",
            html_body="<p>Second signature content</p>",
            text_body="Second signature content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        models.MessageTemplateMailDomain.objects.create(
            template=signature2,
            maildomain=maildomain,
            is_default=False,
        )

        assert (
            signature1.template_maildomains.get(maildomain=maildomain).is_default
            is True
        )
        assert (
            signature2.template_maildomains.get(maildomain=maildomain).is_default
            is False
        )

        # Update second template to be default
        client = APIClient()
        client.force_authenticate(user=user)

        data = {"maildomain_id": str(maildomain.id), "is_default": True}

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": signature2.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that first template is no longer default
        signature1.refresh_from_db()
        signature2.refresh_from_db()

        assert (
            signature1.template_maildomains.get(maildomain=maildomain).is_default
            is False
        )
        assert (
            signature2.template_maildomains.get(maildomain=maildomain).is_default
            is True
        )

    def test_update_template_sets_others_to_not_default_mailbox(self, user, mailbox):
        """Test that updating a template to default sets others to not default for the same mailbox and kind."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create first template as default
        reply1 = models.MessageTemplate.objects.create(
            name="First Default Reply Template",
            html_body="<p>First reply content</p>",
            text_body="First reply content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        models.MessageTemplateMailbox.objects.create(
            template=reply1,
            mailbox=mailbox,
            is_default=True,
        )

        # Create second template as not default
        reply2 = models.MessageTemplate.objects.create(
            name="Second Reply Template",
            html_body="<p>Second reply content</p>",
            text_body="Second reply content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        models.MessageTemplateMailbox.objects.create(
            template=reply2,
            mailbox=mailbox,
            is_default=False,
        )

        assert reply1.template_mailboxes.get(mailbox=mailbox).is_default is True
        assert reply2.template_mailboxes.get(mailbox=mailbox).is_default is False

        # Update second template to be default
        client = APIClient()
        client.force_authenticate(user=user)

        data = {"mailbox_id": str(mailbox.id), "is_default": True}

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": reply2.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify that first template is no longer default
        reply1.refresh_from_db()
        reply2.refresh_from_db()

        assert reply1.template_mailboxes.get(mailbox=mailbox).is_default is False
        assert reply2.template_mailboxes.get(mailbox=mailbox).is_default is True

    def test_retrieve(self, user, mailbox):
        """Test retrieving a single email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create a template with valid content
        email_template = factories.MessageTemplateFactory(
            html_body="<p>Test content</p>",
            text_body="Test content",
        )
        email_template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": email_template.id})
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(email_template.id)
        assert response.data["name"] == email_template.name

    def test_retrieve_unauthorized(self):
        """Test that unauthorized users cannot retrieve templates."""
        email_template = models.MessageTemplate.objects.create(
            name="Unauthorized Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        client = APIClient()
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": email_template.id})
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_retrieve_no_access(self, user):
        """Test that users without access cannot retrieve templates."""
        email_template = models.MessageTemplate.objects.create(
            name="No Access Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": email_template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create(self, user, mailbox):
        """Test creating a new email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "description": "A test template",
            "html_body": "<p>Hello {recipient_name}</p>",
            "text_body": "Hello {recipient_name}",
            "raw_blob": "raw content",
            "kind": "reply",
            "is_active": True,
            "mailbox_id": str(mailbox.id),
            "is_default": False,
        }
        response = client.post(reverse("message-templates-list"), data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert response.data["kind"] == "reply"
        assert response.data["raw_blob"] == "raw content"

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        assert template.raw_blob.get_content().decode("utf-8") == "raw content"

    def test_create_unauthorized(self):
        """Test that unauthorized users cannot create templates."""
        client = APIClient()

        data = {
            "name": "Test Template",
            "kind": "reply",
        }

        response = client.post(reverse("message-templates-list"), data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update(self, user, mailbox):
        """Test updating an email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template with valid content
        template = factories.MessageTemplateFactory(
            html_body="<p>Original content</p>",
            text_body="Original content",
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Updated Template",
            "description": "Updated description",
            "html_body": "<p>Updated content</p>",
            "text_body": "Updated content",
            "raw_blob": "updated raw content",
            "kind": "signature",
            "is_active": False,
            "mailbox_id": str(mailbox.id),
            "is_default": False,
        }

        response = client.put(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["kind"] == "signature"
        assert response.data["is_active"] is False
        assert response.data["raw_blob"] == "updated raw content"

        # check that the blob was updated
        template.refresh_from_db()
        assert template.raw_blob.get_content().decode("utf-8") == "updated raw content"

    def test_partial_update(self, user, mailbox):
        """Test partially updating an email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template with valid content
        reply_template = models.MessageTemplate.objects.create(
            name="Original Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        reply_template.mailboxes.add(mailbox)

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
        assert response.data["kind"] == "reply"
        assert response.data["html_body"] == "<p>Original content</p>"
        assert response.data["text_body"] == "Original content"
        assert response.data["is_active"] is True

        # check that the template has been updated
        reply_template.refresh_from_db()
        assert reply_template.name == "Partially Updated Template"
        assert reply_template.template_mailboxes.count() == 1
        assert reply_template.template_mailboxes.first().mailbox == mailbox
        assert reply_template.template_mailboxes.first().is_default is False

    def test_delete(self, user, mailbox):
        """Test deleting an email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create a template with valid content
        template = factories.MessageTemplateFactory(
            html_body="<p>Content to delete</p>",
            text_body="Content to delete",
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify template is deleted
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMessageTemplateRenderAction:
    """Test the render_template action."""

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_render_template_success(self, user, mailbox):
        """Test successful template rendering."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = factories.MessageTemplateFactory(
            html_body="<p>{full_name} - {job_title}</p>",
            text_body="{full_name} - {job_title}",
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"mailbox_id": str(mailbox.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        # The template will be rendered with the user's full name
        assert "John Doe - Adjointe" in response.data["html_body"]
        assert "John Doe - Adjointe" in response.data["text_body"]

    def test_render_template_missing_mailbox_id(self, user, mailbox):
        """Test template rendering with missing mailbox_id and maildomain_id."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create template with specific content that requires context
        template = models.MessageTemplate.objects.create(
            name="Test Template",
            html_body="<p>Hello {full_name}!</p>",
            text_body="Hello {full_name}!",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        # Send request without mailbox_id or maildomain_id
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "mailbox_id or maildomain_id is required" in response.data["error"]

    def test_render_template_with_maildomain_id(self, user, maildomain):
        """Test template rendering with maildomain_id."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create template linked to maildomain
        template = models.MessageTemplate.objects.create(
            name="Maildomain Template",
            html_body="<p>Hello {full_name}!</p>",
            text_body="Hello {full_name}!",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.maildomains.add(maildomain)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"maildomain_id": str(maildomain.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert "Hello" in response.data["html_body"]
        assert "Hello" in response.data["text_body"]

    def test_render_template_unauthorized(self):
        """Test that unauthorized users cannot render templates."""
        email_template = models.MessageTemplate.objects.create(
            name="Test Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        client = APIClient()

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": email_template.id})
            + "render/"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_render_template_no_access(self, user, mailbox):
        """Test that users without access cannot render templates."""
        template = models.MessageTemplate.objects.create(
            name="Test Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"mailbox_id": str(mailbox.id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

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
                "message-templates-detail",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            )
            + "render/",
            {"mailbox_id": str(mailbox.id)},
        )
        # get_object() will return 404 if template doesn't exist or user has no access
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_render_template_with_mailbox_and_maildomain_access(
        self, user, mailbox, maildomain
    ):
        """Test template rendering when user has access to both mailbox and maildomain."""
        # Ensure mailbox belongs to maildomain
        mailbox.domain = maildomain
        mailbox.save()

        # User has access to mailbox
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # User has access to maildomain
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create template linked to maildomain
        template = models.MessageTemplate.objects.create(
            name="Domain Template",
            html_body="<p>Hello {full_name}!</p>",
            text_body="Hello {full_name}!",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.maildomains.add(maildomain)

        client = APIClient()
        client.force_authenticate(user=user)

        # Should work with maildomain_id
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"maildomain_id": str(maildomain.id)},
        )
        assert response.status_code == status.HTTP_200_OK

        # Should also work with mailbox_id (because mailbox belongs to maildomain)
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_render_template_success_with_placeholders(self, user, mailbox):
        """Test successful template rendering with placeholders."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create template with valid placeholders
        template = models.MessageTemplate.objects.create(
            name="Placeholder Template",
            html_body="<p>Hello {full_name}!</p>",
            text_body="Hello {full_name}!",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id}) + "render/",
            {"mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        # Check that placeholders are replaced
        assert user.full_name in response.data["html_body"]
        assert user.full_name in response.data["text_body"]

    def test_raw_blob_creates_blob_on_create(self, user, mailbox):
        """Test that raw_blob creates a blob when creating a template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create template with raw_blob content
        data = {
            "name": "Blob Template",
            "description": "Template with raw_blob content",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_blob": "test blob content",
            "kind": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # raw_blob should contain the content
        assert response.data["raw_blob"] == "test blob content"

        # check that blob was created
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        assert template.raw_blob.get_content().decode("utf-8") == "test blob content"


class TestMessageTemplateAbilities:
    """Test message template abilities."""

    def test_template_abilities_in_response(self, user, mailbox):
        """Test that template abilities are included in the response."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        template = models.MessageTemplate.objects.create(
            name="Abilities Template",
            html_body="<p>Abilities content</p>",
            text_body="Abilities content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_200_OK
        assert "abilities" in response.data

    def test_template_list_with_abilities(self, user, mailbox):
        """Test that template list includes abilities."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        template = models.MessageTemplate.objects.create(
            name="List Abilities Template",
            html_body="<p>List abilities content</p>",
            text_body="List abilities content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert "abilities" in response.data[0]

    def test_template_no_access_abilities(self, user):
        """Test that users without access get no abilities."""
        email_template = models.MessageTemplate.objects.create(
            name="No Access Abilities Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            kind=models.MessageTemplateKindChoices.REPLY,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": email_template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMessageTemplateValidation:
    """Test message template validation."""

    def test_create_template_without_html_or_text(self, user, mailbox):
        """Test creating a template without html_body or text_body."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Invalid Template",
            "kind": "reply",
            "html_body": "",
            "text_body": "",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "html_body" in response.data
            or "text_body" in response.data
            or "non_field_errors" in response.data
        )

    def test_create_template_with_invalid_type(self, user, mailbox):
        """Test creating a template with invalid type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Invalid Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "kind": "invalid_type",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "kind" in response.data

    def test_create_template_with_raw_blob(self, user, mailbox):
        """Test creating a template with raw_blob field."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Blob Template",
            "description": "Template with blob",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_blob": "test blob content",
            "kind": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        # raw_blob should contain the content
        assert response.data["raw_blob"] == "test blob content"

        # check that blob was created
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        assert template.raw_blob.get_content().decode("utf-8") == "test blob content"

    def test_create_template_with_empty_raw_blob(self, user, mailbox):
        """Test creating a template with empty raw_blob content."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Empty Blob Template",
            "description": "Template with empty blob",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_blob": "",  # Empty content
            "kind": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["raw_blob"] is None

        # check that blob was not created
        assert models.Blob.objects.count() == 0
        template = models.MessageTemplate.objects.get()
        assert template.raw_blob is None

    def test_update_template_raw_blob(self, user, mailbox):
        """Test updating a template with raw_blob."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        # Create template with blob
        template = models.MessageTemplate.objects.create(
            name="Blob Template",
            html_body="<p>Content</p>",
            text_body="Content",
            kind=models.MessageTemplateKindChoices.SIGNATURE,
        )
        template.mailboxes.add(mailbox)

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "raw_blob": "new blob content",
        }

        response = client.patch(
            reverse("message-templates-detail", kwargs={"pk": template.id}),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        # raw_blob should contain the new content
        assert response.data["raw_blob"] == "new blob content"

        # check that blob was created with new content
        template.refresh_from_db()
        assert template.raw_blob.get_content().decode("utf-8") == "new blob content"
