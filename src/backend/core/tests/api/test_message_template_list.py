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


@pytest.fixture(name="available_mailbox_message_template_url")
def fixture_available_mailbox_message_template_url(mailbox):
    """Url to list message templates available for a mailbox."""
    return reverse(
        "available-mailbox-message-templates-list", kwargs={"mailbox_id": mailbox.id}
    )


class TestAvailableMailboxMessageTemplateList:
    """Test list operations for MessageTemplateViewSet."""

    def test_unauthorized(self, available_mailbox_message_template_url):
        """Test that unauthorized users cannot access the list."""
        client = APIClient()
        response = client.get(available_mailbox_message_template_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_without_any_permission(self, user, available_mailbox_message_template_url):
        """Test that users without any permission cannot see templates."""
        client = APIClient()
        client.force_authenticate(user=user)
        # check template is not returned
        response = client.get(available_mailbox_message_template_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_message_templates_for_regular_user(
        self, user, mailbox, available_mailbox_message_template_url
    ):
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
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )

        # Create new message template for the mailbox
        new_message = factories.MessageTemplateFactory(
            name="New Message Template",
            html_body="<p>New message content</p>",
            text_body="New message content",
            type=enums.MessageTemplateTypeChoices.NEW_MESSAGE,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # First try with no access for user authenticated. Should return 403
        # because no access to mailbox.
        response = client.get(available_mailbox_message_template_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Then try with mailbox access. Should return templates of this mailbox
        # and maildomain of this mailbox (new message, reply, signature)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        response = client.get(available_mailbox_message_template_url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert response.data[0]["name"] == "New Message Template"
        assert response.data[1]["name"] == "Reply Template"
        assert response.data[2]["name"] == "Signature Template"
        assert response.data[0]["id"] == str(new_message.id)
        assert response.data[1]["id"] == str(reply.id)
        assert response.data[2]["id"] == str(signature.id)

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
        self, user, mailbox, role, available_mailbox_message_template_url
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
        response = client.get(available_mailbox_message_template_url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)

    def test_filter_by_type(
        self, user, mailbox, available_mailbox_message_template_url
    ):
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
            type=enums.MessageTemplateTypeChoices.REPLY,
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
            available_mailbox_message_template_url,
            {"type": "reply"},
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find our reply template for this mailbox
        assert len(response.data) == 1
        assert response.data[0]["type"] == "reply"
        assert response.data[0]["id"] == str(reply_template.id)

    def test_filter_by_forced_status(
        self, user, mailbox, available_mailbox_message_template_url
    ):
        """Test filtering list by forced status."""
        # Create mailbox access for user
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )
        # Create some non forced templates for the mailbox
        factories.MessageTemplateFactory.create_batch(
            3,
            mailbox=mailbox,
            is_forced=False,
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
        )

        # Create one forced template for the mailbox
        template = factories.MessageTemplateFactory(
            name="Forced Mailbox Template",
            html_body="<p>Forced mailbox content</p>",
            text_body="Forced mailbox content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_forced=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            available_mailbox_message_template_url, {"type": "signature"}
        )
        assert response.status_code == status.HTTP_200_OK
        # Should find our forced template for this mailbox
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(template.id)
