"""Test the render action of MailboxMessageTemplateViewSet."""

import uuid
from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db

# A template exercising mailbox/user placeholders (name, job_title) and the
# message-level one (recipient_name) that only resolves when a draft is given.
TEMPLATE_HTML = "<p>{name} - {job_title} - {recipient_name}</p>"
TEMPLATE_TEXT = "{name} - {job_title} - {recipient_name}"


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


def render_url(mailbox_id, template_id):
    """Build the URL for the message template render endpoint."""
    return reverse(
        "mailbox-message-templates-render",
        kwargs={"mailbox_id": mailbox_id, "pk": template_id},
    )


def _create_draft_with_recipient(mailbox, recipient_name):
    """Create a draft owned by the mailbox with a single TO recipient."""
    sender_contact = factories.ContactFactory(
        name="Sender", email="sender@example.com", mailbox=mailbox
    )
    draft = factories.MessageFactory(
        sender=sender_contact, thread=factories.ThreadFactory(), is_draft=True
    )
    recipient = factories.ContactFactory(
        name=recipient_name, email="recipient@example.com", mailbox=mailbox
    )
    factories.MessageRecipientFactory(
        message=draft, contact=recipient, type=enums.MessageRecipientTypeChoices.TO
    )
    return draft


class TestMessageTemplateRender:
    """Test the render action under mailboxes/{id}/message-templates/{id}/render/."""

    def test_message_template_render_unauthorized(self, mailbox):
        """Unauthenticated users cannot render a template."""
        template = factories.MessageTemplateFactory(mailbox=mailbox)
        client = APIClient()
        response = client.get(render_url(mailbox.id, template.id))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_message_template_render_no_access(self, user, mailbox):
        """Users without mailbox access cannot render a template."""
        template = factories.MessageTemplateFactory(mailbox=mailbox)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(render_url(mailbox.id, template.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_message_template_render_without_message_keeps_recipient_token(
        self, user, mailbox
    ):
        """Without a draft, mailbox/user placeholders resolve but recipient_name
        stays an unresolved token — a viewer role is enough."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        template = factories.MessageTemplateFactory(
            mailbox=mailbox, html_body=TEMPLATE_HTML, text_body=TEMPLATE_TEXT
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(render_url(mailbox.id, template.id))

        assert response.status_code == status.HTTP_200_OK
        assert "John Doe" in response.data["html_body"]
        assert "Adjointe" in response.data["html_body"]
        # recipient_name has no value without a draft: its token is left intact.
        assert "{recipient_name}" in response.data["html_body"]

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_message_template_render_with_message_resolves_recipient(
        self, user, mailbox
    ):
        """With a draft from this mailbox, recipient_name is resolved."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        template = factories.MessageTemplateFactory(
            mailbox=mailbox, html_body=TEMPLATE_HTML, text_body=TEMPLATE_TEXT
        )
        draft = _create_draft_with_recipient(mailbox, "Jane Smith")

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            render_url(mailbox.id, template.id), {"message_id": str(draft.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "Jane Smith" in response.data["html_body"]
        assert "{recipient_name}" not in response.data["html_body"]

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_message_template_render_ignores_foreign_draft(self, user, mailbox):
        """A draft owned by another mailbox is ignored: recipient_name is not
        resolved, preventing recipient probing across mailboxes."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        template = factories.MessageTemplateFactory(
            mailbox=mailbox, html_body=TEMPLATE_HTML, text_body=TEMPLATE_TEXT
        )
        foreign_draft = _create_draft_with_recipient(
            factories.MailboxFactory(), "Secret Recipient"
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            render_url(mailbox.id, template.id), {"message_id": str(foreign_draft.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "Secret Recipient" not in response.data["html_body"]
        assert "{recipient_name}" in response.data["html_body"]

    def test_message_template_render_maildomain_template(self, user, mailbox):
        """A domain-level template renders through the mailbox endpoint."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        template = factories.MessageTemplateFactory(
            maildomain=mailbox.domain,
            html_body="<p>Domain signature</p>",
            text_body="Domain signature",
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(render_url(mailbox.id, template.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["html_body"] == "<p>Domain signature</p>"

    def test_message_template_render_nonexistent(self, user, mailbox):
        """Rendering a nonexistent template returns 404."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(render_url(mailbox.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_message_template_render_foreign_mailbox_template(self, user, mailbox):
        """A template owned by another mailbox (not this mailbox nor its domain)
        is not reachable through this mailbox: it returns 404."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
        )
        foreign_template = factories.MessageTemplateFactory(
            mailbox=factories.MailboxFactory(),
            html_body="<p>Foreign</p>",
            text_body="Foreign",
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(render_url(mailbox.id, foreign_template.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_message_template_render_escapes_html_in_html_body_only(
        self, user, mailbox
    ):
        """Resolved values are HTML-escaped in html_body to prevent injection,
        but kept raw in text_body."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        template = factories.MessageTemplateFactory(
            mailbox=mailbox, html_body=TEMPLATE_HTML, text_body=TEMPLATE_TEXT
        )
        draft = _create_draft_with_recipient(mailbox, "<script>alert(1)</script>")

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            render_url(mailbox.id, template.id), {"message_id": str(draft.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        # The raw markup must never appear in the html body.
        assert "<script>" not in response.data["html_body"]
        assert "&lt;script&gt;" in response.data["html_body"]
        # The text body keeps the value verbatim (no HTML context to escape).
        assert "<script>alert(1)</script>" in response.data["text_body"]

    def test_message_template_render_invalid_message_id(self, user, mailbox):
        """A malformed (non-UUID) message_id is rejected with a 400 rather than
        crashing the endpoint."""
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        template = factories.MessageTemplateFactory(
            mailbox=mailbox, html_body=TEMPLATE_HTML, text_body=TEMPLATE_TEXT
        )

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(
            render_url(mailbox.id, template.id), {"message_id": "not-a-uuid"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
