"""Test retrieve operations for MessageTemplateViewSet."""

import json

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


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


class TestMessageTemplateRetrieve:
    """Test retrieve operations for MessageTemplateViewSet."""

    def test_message_template_retrieve_unauthorized(self, mailbox):
        """Test that unauthorized users cannot retrieve templates."""
        message_template = factories.MessageTemplateFactory(
            name="Unauthorized Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
        )
        client = APIClient()
        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": message_template.id},
            )
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_message_template_retrieve_no_access(self, user, mailbox):
        """Test that users without access cannot retrieve templates."""
        message_template = factories.MessageTemplateFactory(
            name="No Access Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": message_template.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_message_template_retrieve_success(self, user, mailbox):
        """Test retrieving a single email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        # Create a template with valid content
        template = factories.MessageTemplateFactory(
            html_body="<p>Test content</p>",
            text_body="Test content",
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse(
            "mailbox-message-templates-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": template.id},
        )

        # Without ?bodies, no body fields should be returned
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(template.id)
        assert response.data["name"] == template.name
        assert "raw_body" not in response.data
        assert "html_body" not in response.data
        assert "text_body" not in response.data

        # With ?bodies=raw,html,text all body fields are returned
        response = client.get(url, {"bodies": "raw,html,text"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["html_body"] == "<p>Test content</p>"
        assert response.data["text_body"] == "Test content"
        assert json.loads(response.data["raw_body"]) == {"key": "value"}

    def test_message_template_retrieve_nonexistent(self, user, mailbox):
        """Test retrieving a nonexistent template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={
                    "mailbox_id": mailbox.id,
                    "pk": "00000000-0000-0000-0000-000000000000",
                },
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_message_template_retrieve_success_with_different_roles(
        self, user, mailbox, role
    ):
        """Test retrieving templates with different access roles."""

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )

        template = factories.MessageTemplateFactory(
            name="Role Test Template",
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == template.name

    def test_message_template_retrieve_success_maildomain_template(self, user, mailbox):
        """Test retrieving a maildomain template through the mailbox endpoint."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = factories.MessageTemplateFactory(
            name="Domain Template",
            html_body="<p>Domain content</p>",
            text_body="Domain content",
            maildomain=mailbox.domain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(template.id)
        assert response.data["name"] == "Domain Template"

    def test_message_template_retrieve_maildomain_template_not_accessible_from_other_domain(
        self, user
    ):
        """Test that a maildomain template is not accessible from a mailbox on
        a different domain."""
        mailbox = factories.MailboxFactory()
        other_mailbox = factories.MailboxFactory()

        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = factories.MessageTemplateFactory(
            name="Other Domain Template",
            html_body="<p>Other domain content</p>",
            text_body="Other domain content",
            maildomain=mailbox.domain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": other_mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_message_template_retrieve_other_mailbox_template_not_accessible(
        self, user, mailbox
    ):
        """Test that a template belonging to another mailbox on the same domain
        is not accessible."""
        other_mailbox = factories.MailboxFactory(domain=mailbox.domain)

        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        template = factories.MessageTemplateFactory(
            name="Other Mailbox Template",
            html_body="<p>Other mailbox content</p>",
            text_body="Other mailbox content",
            mailbox=other_mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
