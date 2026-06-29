"""Tests for MessageTemplate model methods."""

from unittest.mock import patch

import pytest

from core import factories, models

pytestmark = pytest.mark.django_db


class TestResolveplaceholderValues:
    """Tests for MessageTemplate.resolve_placeholder_values()."""

    def test_resolve_placeholder_name_from_mailbox_contact(self):
        """When a mailbox has a contact, the name should come from the contact."""
        mailbox = factories.MailboxFactory()
        contact = factories.ContactFactory(name="Mairie de Brigny", mailbox=mailbox)
        mailbox.contact = contact
        mailbox.save()

        user = factories.UserFactory(full_name="John Doe")

        result = models.MessageTemplate.resolve_placeholder_values(
            mailbox=mailbox, user=user
        )
        assert result["name"] == "Mairie de Brigny"

    def test_resolve_placeholder_name_fallback_to_user_full_name(self):
        """When mailbox has no contact, the name should fallback to user's full_name."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(full_name="John Doe")

        result = models.MessageTemplate.resolve_placeholder_values(
            mailbox=mailbox, user=user
        )
        assert result["name"] == "John Doe"

    def test_resolve_placeholder_name_empty_when_no_mailbox_no_user(self):
        """When neither mailbox nor user is provided, name should be empty."""
        result = models.MessageTemplate.resolve_placeholder_values()
        assert result["name"] == ""

    def test_resolve_placeholder_user_name_from_user(self):
        """user_name resolves to the user's full_name, independent of the mailbox."""
        mailbox = factories.MailboxFactory()
        contact = factories.ContactFactory(name="Mairie de Brigny", mailbox=mailbox)
        mailbox.contact = contact
        mailbox.save()
        user = factories.UserFactory(full_name="John Doe")

        result = models.MessageTemplate.resolve_placeholder_values(
            mailbox=mailbox, user=user
        )
        assert result["name"] == "Mairie de Brigny"
        assert result["user_name"] == "John Doe"

    def test_resolve_placeholder_user_name_empty_when_no_user(self):
        """When no user is provided, user_name should be empty."""
        result = models.MessageTemplate.resolve_placeholder_values()
        assert result["user_name"] == ""

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {
            "properties": {
                "job_title": {"type": "string"},
                "department": {"type": "string"},
            }
        },
    )
    def test_resolve_placeholder_custom_attributes_from_user(self):
        """Custom attributes defined in schema should be resolved from user."""
        user = factories.UserFactory(
            custom_attributes={"job_title": "Développeur", "department": "DSI"}
        )

        result = models.MessageTemplate.resolve_placeholder_values(user=user)
        assert result["job_title"] == "Développeur"
        assert result["department"] == "DSI"

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_resolve_placeholder_custom_attributes_missing_defaults_to_empty(self):
        """Missing custom attributes should default to empty string."""
        user = factories.UserFactory(custom_attributes={})

        result = models.MessageTemplate.resolve_placeholder_values(user=user)
        assert result["job_title"] == ""

    def test_resolve_placeholder_recipient_name_from_to_recipients(self):
        """recipient_name should be resolved from TO recipients of the message."""
        mailbox = factories.MailboxFactory()
        message = factories.MessageFactory(
            sender=factories.ContactFactory(mailbox=mailbox)
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=factories.ContactFactory(name="Alice", mailbox=mailbox),
            type=models.MessageRecipientTypeChoices.TO,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=factories.ContactFactory(name="Bob", mailbox=mailbox),
            type=models.MessageRecipientTypeChoices.TO,
        )
        # CC recipients should be excluded
        factories.MessageRecipientFactory(
            message=message,
            contact=factories.ContactFactory(name="Charlie", mailbox=mailbox),
            type=models.MessageRecipientTypeChoices.CC,
        )

        result = models.MessageTemplate.resolve_placeholder_values(message=message)
        assert "Alice" in result["recipient_name"]
        assert "Bob" in result["recipient_name"]
        assert "Charlie" not in result["recipient_name"]

    def test_resolve_placeholder_no_message_no_recipient_name(self):
        """When no message is provided, recipient_name should not be in the result."""
        result = models.MessageTemplate.resolve_placeholder_values()
        assert "recipient_name" not in result


class TestRenderTemplate:
    """Tests for MessageTemplate.render_template()."""

    @patch(
        "django.conf.settings.SCHEMA_CUSTOM_ATTRIBUTES_USER",
        {"properties": {"job_title": {"type": "string"}}},
    )
    def test_render_template_placeholder_substitution(self):
        """Placeholders should be replaced with resolved values."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(
            full_name="John Doe",
            custom_attributes={"job_title": "Adjointe"},
        )
        template = factories.MessageTemplateFactory(
            html_body="<p>{name} - {job_title}</p>",
            text_body="{name} - {job_title}",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user)

        assert result["html_body"] == "<p>John Doe - Adjointe</p>"
        assert result["text_body"] == "John Doe - Adjointe"

    def test_render_template_escapes_html_in_html_body(self):
        """HTML special characters in placeholder values must be escaped in html_body."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(full_name="<b>Alice & Co.</b>")
        template = factories.MessageTemplateFactory(
            html_body="<p>{name}</p>",
            text_body="{name}",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user)

        assert result["html_body"] == "<p>&lt;b&gt;Alice &amp; Co.&lt;/b&gt;</p>"

    def test_render_template_no_escape_in_text_body(self):
        """Placeholder values should NOT be escaped in text_body."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(full_name="<b>Alice & Co.</b>")
        template = factories.MessageTemplateFactory(
            html_body="<p>{name}</p>",
            text_body="{name}",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user)

        assert result["text_body"] == "<b>Alice & Co.</b>"

    def test_render_template_with_mailbox_contact_name(self):
        """When mailbox has a contact, the contact name should be used."""
        mailbox = factories.MailboxFactory()
        contact = factories.ContactFactory(name="Mairie de Brigny", mailbox=mailbox)
        mailbox.contact = contact
        mailbox.save()
        user = factories.UserFactory(full_name="John Doe")
        template = factories.MessageTemplateFactory(
            html_body="<p>{name}</p>",
            text_body="{name}",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user)

        assert result["html_body"] == "<p>Mairie de Brigny</p>"
        assert result["text_body"] == "Mairie de Brigny"

    def test_render_template_with_recipient_name(self):
        """recipient_name should be resolved from the message's TO recipients."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(full_name="John Doe")
        message = factories.MessageFactory(
            sender=factories.ContactFactory(mailbox=mailbox)
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=factories.ContactFactory(name="Jane Smith", mailbox=mailbox),
            type=models.MessageRecipientTypeChoices.TO,
        )
        template = factories.MessageTemplateFactory(
            html_body="<p>Hello {recipient_name}!</p>",
            text_body="Hello {recipient_name}!",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user, message=message)

        assert result["html_body"] == "<p>Hello Jane Smith!</p>"
        assert result["text_body"] == "Hello Jane Smith!"

    def test_render_template_unresolved_placeholders_remain(self):
        """Placeholders without a resolved value should remain as-is."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory(full_name="John Doe")
        template = factories.MessageTemplateFactory(
            html_body="<p>{name} - {unknown_field}</p>",
            text_body="{name} - {unknown_field}",
            mailbox=mailbox,
        )

        result = template.render_template(mailbox=mailbox, user=user)

        assert "{unknown_field}" in result["html_body"]
        assert "{unknown_field}" in result["text_body"]
        assert "John Doe" in result["html_body"]
