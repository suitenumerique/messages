"""Test delete operations for MessageTemplateViewSet."""

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


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test maildomain."""
    return factories.MailDomainFactory()


@pytest.fixture(name="mailbox_template")
def fixture_mailbox_template(mailbox):
    """Create a test template."""
    return factories.MessageTemplateFactory(
        html_body="<p>Content to delete</p>",
        text_body="Content to delete",
        mailbox=mailbox,
    )


@pytest.fixture(name="maildomain_template")
def fixture_maildomain_template(maildomain):
    """Create a test template."""
    return factories.MessageTemplateFactory(
        html_body="<p>Content to delete</p>",
        text_body="Content to delete",
        maildomain=maildomain,
    )


class TestMessageTemplateDelete:
    """Test delete operations for MessageTemplateViewSet."""

    def test_unauthorized(self, mailbox):
        """Test that unauthorized users cannot delete templates."""
        template = factories.MessageTemplateFactory(
            html_body="<p>Content to delete</p>",
            text_body="Content to delete",
            mailbox=mailbox,
        )

        client = APIClient()

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template still exists
        assert models.MessageTemplate.objects.filter(id=template.id).exists()

    # FIXME: return 403 instead of 404?????
    def test_without_any_permission(self, user, maildomain_template, mailbox_template):
        """Test that users without proper permission cannot delete templates."""

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": maildomain_template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert models.MessageTemplate.objects.filter(id=maildomain_template.id).exists()
        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": mailbox_template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert models.MessageTemplate.objects.filter(id=mailbox_template.id).exists()

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_with_bad_mailbox_permissions(self, user, mailbox, role):
        """Test that users without proper permission cannot delete templates."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=role,
        )

        template = factories.MessageTemplateFactory(
            html_body="<p>Content to delete</p>",
            text_body="Content to delete",
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify template still exists
        assert models.MessageTemplate.objects.filter(id=template.id).exists()

    def test_success(self, user, mailbox):
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
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(id=template.id).exists()

        # Verify template is deleted
        response = client.get(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent(self, user, mailbox):
        """Test deleting a nonexistent template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete(
            reverse(
                "message-templates-detail",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_superuser(self, user, mailbox):
        """Test deleting a template with superuser."""
        user.is_superuser = True
        user.save()
        client = APIClient()
        client.force_authenticate(user=user)

        template = factories.MessageTemplateFactory(
            html_body="<p>Content to delete</p>",
            text_body="Content to delete",
            mailbox=mailbox,
        )

        response = client.delete(
            reverse("message-templates-detail", kwargs={"pk": template.id})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(id=template.id).exists()
