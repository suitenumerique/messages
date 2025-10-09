"""Test retrieve operations for MessageTemplateViewSet."""

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

    def test_unauthorized(self, mailbox):
        """Test that unauthorized users cannot retrieve templates."""
        reply_template = factories.MessageTemplateFactory(
            name="Unauthorized Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )
        client = APIClient()
        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": reply_template.id},
            )
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_no_access(self, user, mailbox):
        """Test that users without access cannot retrieve templates."""
        reply_template = factories.MessageTemplateFactory(
            name="No Access Template",
            html_body="<p>Test content</p>",
            text_body="Test content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": reply_template.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success(self, user, mailbox):
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

        response = client.get(
            reverse(
                "mailbox-message-templates-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": template.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(template.id)
        assert response.data["name"] == template.name

    def test_nonexistent(self, user, mailbox):
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
    def test_success_with_different_roles(self, user, mailbox, role):
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
