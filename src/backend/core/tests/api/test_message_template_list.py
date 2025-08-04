"""Test list operations for MessageTemplateViewSet."""

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


@pytest.fixture(name="maildomain_template")
def fixture_maildomain_template(maildomain):
    """Create a test template."""
    return factories.MessageTemplateFactory(
        html_body="<p>Content to list</p>",
        text_body="Content to list",
        maildomain=maildomain,
    )


@pytest.fixture(name="mailbox_template")
def fixture_mailbox_template(mailbox):
    """Create a test template."""
    return factories.MessageTemplateFactory(
        html_body="<p>Content to list</p>",
        text_body="Content to list",
        mailbox=mailbox,
    )


class TestMessageTemplateList:
    """Test list operations for MessageTemplateViewSet."""

    def test_unauthorized(self):
        """Test that unauthorized users cannot access the list."""
        client = APIClient()
        # Even with valid parameters, unauthorized users should get 401
        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_without_any_permission(self, user, maildomain_template, mailbox_template):
        """Test that users without any permission cannot see templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        # check maildomain template is not returned
        response = client.get(
            reverse("message-templates-list"),
            {"maildomain_id": str(maildomain_template.maildomain.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        # check mailbox template is not returned
        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": str(mailbox_template.mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_superuser(self, user, maildomain):
        """Test that superuser can see all templates for a maildomain."""
        user.is_superuser = True
        user.save()
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)

        # Create template for the maildomain
        template = factories.MessageTemplateFactory(maildomain=maildomain)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("message-templates-list"), {"maildomain_id": str(maildomain.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)

    def test_for_admin_manage_domain_view(self, user, mailbox):
        """Test that admin can see all templates for their accessible maildomains."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)

        # Create signature template for the maildomain
        signature = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=mailbox.domain,
        )

        # Create some other templates only linked to the mailbox,
        # (not linked to maildomain) so should not be returned
        factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=models.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )
        factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=models.MessageTemplateTypeChoices.NEW_MESSAGE,
            mailbox=mailbox,
        )

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
        # Should find our signature template for this maildomain
        assert len(response.data) == 1
        assert response.data[0]["name"] == "Signature Template"
        assert response.data[0]["id"] == str(signature.id)

    def test_message_templates_for_regular_user(self, user, mailbox):
        """Test that regular users can see templates for their accessible mailbox
        and maildomain of the mailbox."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)

        # Create signature template for the maildomain
        signature = factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=mailbox.domain,
        )

        # Create reply template for the mailbox
        reply = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=models.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )

        # Create new message template for the mailbox
        new_message = factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=models.MessageTemplateTypeChoices.NEW_MESSAGE,
            mailbox=mailbox,
        )

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
        assert response.data[0]["id"] == str(new_message.id)
        assert response.data[1]["id"] == str(reply.id)
        assert response.data[2]["id"] == str(signature.id)

    def test_list_mailbox_templates_for_admin_on_mailbox_domain(self, user, mailbox):
        """Test list mailbox templates for a user with ADMIN role on domain of mailbox."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)

        # Create template for the maildomain
        template = factories.MessageTemplateFactory(maildomain=mailbox.domain)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        # Add maildomain access
        # user is now admin of mailbox.domain so they should see the template
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_list_mailbox_templates_for_user_with_any_role_on_mailbox(
        self, user, mailbox, role
    ):
        """Test list mailbox templates for a user with any role on mailbox."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )
        # Create template for the maildomain
        template = factories.MessageTemplateFactory(maildomain=mailbox.domain)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("message-templates-list"), {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_list_maildomain_templates_for_user_with_any_role_on_mailbox_of_maildomain(
        self, user, mailbox, role
    ):
        """Test list maildomain templates for a user with any role on mailbox of maildomain.
        List should be empty because user has no access to maildomain."""
        # Create some templates for other maildomains
        other_maildomain = factories.MailDomainFactory()
        factories.MessageTemplateFactory.create_batch(3, maildomain=other_maildomain)

        # Create template for the maildomain
        factories.MessageTemplateFactory(maildomain=mailbox.domain)

        # Add mailbox access
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            reverse("message-templates-list"), {"maildomain_id": str(mailbox.domain.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_filter_by_type(self, user, mailbox):
        """Test filtering list by template type."""
        # Create mailbox access for user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        # Create reply template for the mailbox
        reply_template = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=models.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )

        # Create one signature template for the mailbox
        factories.MessageTemplateFactory(
            name="Signature Template",
            html_body="<p>Signature content</p>",
            text_body="Signature content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Filter by reply type
        response = client.get(
            reverse("message-templates-list"),
            {"type": "reply", "mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find our reply template for this mailbox
        assert len(response.data) == 1
        assert response.data[0]["type"] == "reply"
        assert response.data[0]["id"] == str(reply_template.id)

    def test_filter_by_active_status(self, user, mailbox):
        """Test filtering list by active status."""
        # Create mailbox access for user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        # Create active template for the mailbox
        active_template = factories.MessageTemplateFactory(
            name="Active Template",
            html_body="<p>Active content</p>",
            text_body="Active content",
            type=models.MessageTemplateTypeChoices.REPLY,
            is_active=True,
            mailbox=mailbox,
        )

        # Create some inactive templates for the mailbox
        factories.MessageTemplateFactory.create_batch(
            3, mailbox=mailbox, is_active=False
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Filter by active templates
        response = client.get(
            reverse("message-templates-list"),
            {"is_active": "true", "mailbox_id": str(mailbox.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find our active template for this mailbox
        assert len(response.data) == 1
        assert response.data[0]["is_active"]
        assert response.data[0]["id"] == str(active_template.id)

    def test_filter_by_forced_status(self, user, mailbox):
        """Test filtering list by forced status."""
        # Create mailbox access for user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        # Create some non forced templates for the mailbox
        factories.MessageTemplateFactory.create_batch(
            3, mailbox=mailbox, is_forced=False
        )

        # Create one forced template for the mailbox
        template = factories.MessageTemplateFactory(
            name="Forced Mailbox Template",
            html_body="<p>Forced mailbox content</p>",
            text_body="Forced mailbox content",
            type=models.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
            is_forced=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse("message-templates-list"),
            {"mailbox_id": str(mailbox.id), "is_forced": "true"},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find our forced template for this mailbox
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)
