"""Test update operations for AdminMailDomainMessageTemplateViewSet."""

import json

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
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
    """Create a test mail domain."""
    return factories.MailDomainFactory()


class TestAdminMailDomainMessageTemplate:
    """Test update operations for AdminMailDomainMessageTemplateViewSet."""

    def test_unauthorized(self, maildomain):
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
            reverse(
                "admin-maildomains-message-templates-detail",
                kwargs={"maildomain_pk": maildomain.id, "pk": template.id},
            ),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify template was not updated
        template.refresh_from_db()
        assert template.name != "Updated Template"

    def test_no_access(self, user, maildomain):
        """Test that users without proper permission cannot update templates."""
        template = factories.MessageTemplateFactory(
            name="Test Template",
            html_body="<p>Original content</p>",
            text_body="Original content",
            maildomain=maildomain,
            raw_body=RAW_DATA_STRUCT,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Updated Template",
        }

        response = client.put(
            reverse(
                "admin-maildomains-message-templates-detail",
                kwargs={"maildomain_pk": maildomain.id, "pk": template.id},
            ),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify template was not updated
        template.refresh_from_db()
        assert template.name == "Test Template"

    def test_success(self, user, maildomain):
        """Test updating an email template."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a template with valid content
        template = factories.MessageTemplateFactory(
            name="Test Template",
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
            reverse(
                "admin-maildomains-message-templates-detail",
                kwargs={"maildomain_pk": maildomain.id, "pk": template.id},
            ),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Template"
        assert response.data["type"] == "signature"
        assert response.data["is_active"] is False

        # check that the blob was updated
        template.refresh_from_db()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
