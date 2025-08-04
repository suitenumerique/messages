"""Test create operations for MessageTemplateViewSet."""

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


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a test maildomain."""
    return factories.MailDomainFactory()


class TestMessageTemplateCreate:
    """Test create operations for MessageTemplateViewSet."""

    def test_unauthorized(self, mailbox):
        """Test that unauthorized users cannot create templates."""
        client = APIClient()

        data = {
            "name": "Test Template",
            "type": "reply",
            "html_body": "<p>Hello {recipient_name}</p>",
            "text_body": "Hello {recipient_name}",
            "raw_body": RAW_DATA,
            "is_active": True,
            "mailbox_id": str(mailbox.id),
            "is_forced": False,
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden(self, user, mailbox):
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
            "mailbox_id": str(mailbox.id),
            "is_forced": False,
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_success(self, user, mailbox):
        """Test creating a new email template."""
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template Signature",
            "html_body": "<hr />\n<p>{full_name} - Mairie de Brigny</p>",
            "is_active": True,
            "is_forced": False,
            "maildomain_id": str(mailbox.domain.id),
            "raw_body": RAW_DATA,
            "text_body": "----\n\n{full_name} - Mairie de Brigny\n",
            "type": "signature",
        }
        response = client.post(reverse("message-templates-list"), data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template Signature"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_success_with_mailbox_id(self, user, mailbox):
        """Test creating a new email template."""
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template Signature",
            "html_body": "<hr />\n<p>{full_name} - Mairie de Brigny</p>",
            "is_active": True,
            "is_forced": False,
            "mailbox_id": str(mailbox.id),
            "raw_body": RAW_DATA,
            "text_body": "----\n\n{full_name} - Mairie de Brigny\n",
            "type": "signature",
        }
        response = client.post(reverse("message-templates-list"), data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template Signature"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_success_with_mailbox_id_mailbox_access(self, user, mailbox):
        """Test creating a new email template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template Signature",
            "html_body": "<hr />\n<p>{full_name} - Mairie de Brigny</p>",
            "is_active": True,
            "is_forced": False,
            "mailbox_id": str(mailbox.id),
            "raw_body": RAW_DATA,
            "text_body": "----\n\n{full_name} - Mairie de Brigny\n",
            "type": "signature",
        }
        response = client.post(reverse("message-templates-list"), data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template Signature"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_with_invalid_type(self, user, mailbox):
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
            "raw_body": RAW_DATA,
            "type": "invalid_type",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "type" in response.data

    def test_content_fields_atomic_validation(self, user, mailbox):
        """Test that content fields must be created together atomically."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Try to create with only html_body - should fail
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(
            reverse("message-templates-list"),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )
        # Try to create with only text_body - should fail
        data = {
            "name": "Test Template",
            "text_body": "Content",
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(
            reverse("message-templates-list"),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )

        # Try to create with only raw_body - should fail
        data = {
            "name": "Test Template",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(
            reverse("message-templates-list"),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "All content fields (html_body, text_body, raw_body) must be provided for creation."
            in str(response.data)
        )

        # Create with all three fields together - should succeed
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }

        response = client.post(
            reverse("message-templates-list"),
            data,
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Verify all fields were created
        template = models.MessageTemplate.objects.get(name="Test Template")
        assert template.html_body == "<p>Content</p>"
        assert template.text_body == "Content"
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT

    def test_with_maildomain_id(self, user, maildomain):
        """Test creating a template with maildomain_id."""
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "maildomain_id": str(maildomain.id),
        }

        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.maildomain == maildomain
        assert template.mailbox is None

    def test_with_mailbox_id(self, user, mailbox):
        """Test creating a template with mailbox_id."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.mailbox == mailbox
        assert template.maildomain is None

    def test_with_mailbox_id_and_maildomain_id(self, user, mailbox, maildomain):
        """Test creating a template with mailbox_id and maildomain_id."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
            "maildomain_id": str(maildomain.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        # should fail because mailbox.domain and maildomain are different
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Only one of mailbox_id or maildomain_id can be provided" in str(
            response.data
        )

    def test_admin_access_to_mailbox(self, user, mailbox):
        """Test create template with admin access to mailbox."""
        client = APIClient()
        client.force_authenticate(user=user)
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }

        # test without permissions
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # test with permissions
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        assert response.data["name"] == "Test Template"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.mailbox == mailbox
        assert template.maildomain is None

    def test_admin_access_to_maildomain_of_mailbox(self, user, mailbox):
        """Test create template with admin access to maildomain of mailbox."""
        factories.MailDomainAccessFactory(
            maildomain=mailbox.domain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.mailbox == mailbox
        assert template.maildomain is None

    def test_admin_access_to_maildomain(self, user, mailbox):
        """Test create template with admin access to maildomain."""
        maildomain = mailbox.domain
        # create maildomain access
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        # test create template for a maildomain
        data = {
            "name": "Test Template for maildomain",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "maildomain_id": str(maildomain.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template for maildomain"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.name == "Test Template for maildomain"
        assert template.maildomain == maildomain
        assert template.mailbox is None

        # test create template for a mailbox with access to the maildomain
        data = {
            "name": "Test Template for mailbox",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template for mailbox"
        assert response.data["type"] == "signature"
        assert "- Mairie de Brigny" in response.data["raw_body"]

        # check template and blob are created
        assert models.MessageTemplate.objects.count() == 2
        assert models.Blob.objects.count() == 2
        template = models.MessageTemplate.objects.get(name="Test Template for mailbox")
        assert str(template.id) == response.data["id"]
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.mailbox == mailbox
        assert template.maildomain is None

    def test_superuser(self, user, mailbox):
        """Test creating a template with superuser."""
        user.is_superuser = True
        user.save()
        client = APIClient()
        client.force_authenticate(user=user)

        data = {
            "name": "Test Template",
            "html_body": "<p>Content</p>",
            "text_body": "Content",
            "raw_body": RAW_DATA,
            "type": "signature",
            "mailbox_id": str(mailbox.id),
        }
        response = client.post(reverse("message-templates-list"), data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Test Template"
        assert models.MessageTemplate.objects.count() == 1
        assert models.Blob.objects.count() == 1
        template = models.MessageTemplate.objects.get()
        content = json.loads(template.blob.get_content().decode("utf-8"))
        assert content["raw"] == RAW_DATA_STRUCT
        assert template.mailbox == mailbox
        assert template.maildomain is None
