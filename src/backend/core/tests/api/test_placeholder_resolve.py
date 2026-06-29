"""Test draft message resolve placeholder api endpoint."""

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


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


def _create_draft(mailbox):
    """Create a draft message owned by the given mailbox with thread editor access."""
    sender_contact = factories.ContactFactory(
        name="Sender", email="sender@example.com", mailbox=mailbox
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        thread=thread,
        mailbox=mailbox,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return factories.MessageFactory(sender=sender_contact, thread=thread, is_draft=True)


def resolve_url(message_id):
    """Build the URL for the draft-placeholders endpoint."""
    return reverse("draft-placeholders", kwargs={"message_id": message_id})


class TestResolvePlaceholder:
    """Test the resolve placeholder endpoint under draft/{message_id}/."""

    def test_api_draft_placeholder_resolve_unauthorized(self, mailbox):
        """Test that unauthenticated users cannot resolve placeholders."""
        draft = _create_draft(mailbox)
        client = APIClient()
        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_draft_placeholder_resolve_no_mailbox_access(self, user, mailbox):
        """Test that users without mailbox access get 404."""
        draft = _create_draft(mailbox)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_api_draft_placeholder_resolve_viewer_role_denied(self, user, mailbox):
        """Test that VIEWER role on mailbox is not sufficient (need editor-level)."""
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_api_draft_placeholder_resolve_success_editor_roles(
        self, user, mailbox, role
    ):
        """Test that EDITOR/SENDER/ADMIN roles can resolve placeholders."""
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(mailbox=mailbox, user=user, role=role)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "John Doe"
        assert response.data["job_title"] == "Adjointe"

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_api_draft_placeholder_resolve_name_from_mailbox_contact(
        self, user, mailbox
    ):
        """Test that name is resolved from mailbox contact when available."""
        contact = factories.ContactFactory(
            name="Mairie de Brigny", email="mairie@brigny.fr", mailbox=mailbox
        )
        mailbox.contact = contact
        mailbox.save()
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Mairie de Brigny"
        # user_name resolves to the authenticated user, distinct from the mailbox.
        assert response.data["user_name"] == "John Doe"
        assert response.data["job_title"] == "Adjointe"

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {}},
    )
    def test_api_draft_placeholder_resolve_name_fallback_to_user_full_name(
        self, user, mailbox
    ):
        """Test that name falls back to user full_name when mailbox has no contact."""
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "John Doe"

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {}},
    )
    def test_api_draft_placeholder_resolve_recipient_name_from_to_recipients(
        self, user, mailbox
    ):
        """Test recipient_name resolution from TO recipients of the draft."""
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        contact_jane = factories.ContactFactory(
            name="Jane Smith", email="jane@example.com", mailbox=mailbox
        )
        contact_bob = factories.ContactFactory(
            name="Bob Martin", email="bob@example.com", mailbox=mailbox
        )
        factories.MessageRecipientFactory(
            message=draft,
            contact=contact_jane,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        factories.MessageRecipientFactory(
            message=draft,
            contact=contact_bob,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        # CC recipient should NOT appear in recipient_name
        contact_cc = factories.ContactFactory(
            name="CC Person", email="cc@example.com", mailbox=mailbox
        )
        factories.MessageRecipientFactory(
            message=draft,
            contact=contact_cc,
            type=enums.MessageRecipientTypeChoices.CC,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_200_OK
        assert "Jane Smith" in response.data["recipient_name"]
        assert "Bob Martin" in response.data["recipient_name"]
        assert "CC Person" not in response.data["recipient_name"]

    def test_api_draft_placeholder_resolve_nonexistent_draft(self, user):
        """Test that a non-existent draft returns 404."""
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(resolve_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_api_draft_placeholder_resolve_non_draft_message_denied(
        self, user, mailbox
    ):
        """Test that a non-draft (sent) message returns 404."""
        sender_contact = factories.ContactFactory(
            name="Sender", email="sender@test.com", mailbox=mailbox
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sent_message = factories.MessageFactory(
            sender=sender_contact, thread=thread, is_draft=False
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(resolve_url(sent_message.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_api_draft_placeholder_resolve_thread_viewer_access_denied(
        self, user, mailbox
    ):
        """Test that thread VIEWER access (not EDITOR) is denied."""
        sender_contact = factories.ContactFactory(
            name="Sender", email="sender@viewer.com", mailbox=mailbox
        )
        thread = factories.ThreadFactory()
        # Thread access is VIEWER, not EDITOR
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        draft = factories.MessageFactory(
            sender=sender_contact, thread=thread, is_draft=True
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {
            "properties": {
                "job_title": {"type": "string"},
                "department": {"type": "string"},
            }
        },
    )
    def test_api_draft_placeholder_resolve_custom_attributes_empty_values(
        self, user, mailbox
    ):
        """Test that missing custom attributes resolve to empty strings."""
        draft = _create_draft(mailbox)
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(resolve_url(draft.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["job_title"] == "Adjointe"
        assert response.data["department"] == ""
