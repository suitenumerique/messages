"""Tests for signature handling in DraftMessageView."""
# pylint: disable=unused-argument

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture(name="mailbox_sender")
def fixture_mailbox_sender():
    """Create a test mailbox sender."""
    return factories.MailboxFactory()


@pytest.fixture(name="mailbox_access")
def fixture_mailbox_access(user, mailbox_sender):
    """Create mailbox access for the user."""
    return factories.MailboxAccessFactory(
        user=user,
        mailbox=mailbox_sender,
        role=models.MailboxRoleChoices.EDITOR,
    )


@pytest.fixture(name="signature_template")
def fixture_signature_template(mailbox_sender):
    """Create a signature template in the same mailbox as the sender."""
    return factories.MessageTemplateFactory(
        name="Professional Signature",
        html_body="<p>Best regards,<br>{name}</p>",
        text_body="Best regards,\n{name}",
        type=enums.MessageTemplateTypeChoices.SIGNATURE,
        is_active=True,
        mailbox=mailbox_sender,
    )


@pytest.fixture(name="domain_signature_template")
def fixture_domain_signature_template(mailbox_sender):
    """Create a signature template in the same maildomain as the sender."""
    return factories.MessageTemplateFactory(
        name="Professional Signature",
        html_body="<p>Best regards,<br>{name}</p>",
        text_body="Best regards,\n{name}",
        type=enums.MessageTemplateTypeChoices.SIGNATURE,
        is_active=True,
        maildomain=mailbox_sender.domain,
    )


@pytest.mark.django_db
class TestDraftMessageSignature:
    """Test signature handling in DraftMessageView."""

    def test_create_draft_with_valid_mailbox_signature(
        self, user, mailbox_sender, mailbox_access, signature_template
    ):
        """Test creating a draft message with a valid signature."""
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(signature_template.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature == signature_template

    def test_create_draft_with_valid_maildomain_signature(
        self, user, mailbox_sender, mailbox_access, domain_signature_template
    ):
        """Test creating a draft message with a valid maildomain signature."""
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(domain_signature_template.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature == domain_signature_template

    def test_create_draft_with_inactive_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test creating a draft message with an inactive signature."""
        inactive_signature = factories.MessageTemplateFactory(
            name="Inactive Signature",
            html_body="<p>Inactive content</p>",
            text_body="Inactive content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox_sender,
            is_active=False,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(inactive_signature.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature is None

    def test_create_draft_with_unauthorized_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test creating a draft message with an unauthorized signature."""
        other_mailbox = factories.MailboxFactory()
        unauthorized_signature = factories.MessageTemplateFactory(
            name="Unauthorized Signature",
            html_body="<p>Unauthorized content</p>",
            text_body="Unauthorized content",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=other_mailbox,
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(unauthorized_signature.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature is None

    def test_create_draft_with_signature_outside_sender_scope(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test creating a draft message with a signature outside sender scope."""
        other_mailbox = factories.MailboxFactory()
        # Create access to the other mailbox for the user
        factories.MailboxAccessFactory(
            user=user,
            mailbox=other_mailbox,
            role=models.MailboxRoleChoices.EDITOR,
        )
        out_of_scope_signature = factories.MessageTemplateFactory(
            name="Out of Scope Signature",
            html_body="<p>Out of scope signature</p>",
            text_body="Out of scope signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=other_mailbox,
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(out_of_scope_signature.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature is None

    def test_create_draft_with_domain_signature(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test creating a draft message with a domain signature."""
        domain_signature = factories.MessageTemplateFactory(
            name="Domain Signature",
            html_body="<p>Domain signature</p>",
            text_body="Domain signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=mailbox_sender.domain,
            is_active=True,
        )
        # Create domain access for the user
        factories.MailDomainAccessFactory(
            user=user,
            maildomain=mailbox_sender.domain,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(domain_signature.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature == domain_signature

    def test_create_draft_with_non_signature_template(
        self, user, mailbox_sender, mailbox_access
    ):
        """Test creating a draft message with a non-signature template."""
        reply_template = factories.MessageTemplateFactory(
            name="Reply Template",
            html_body="<p>Reply content</p>",
            text_body="Reply content",
            type=enums.MessageTemplateTypeChoices.REPLY,
            mailbox=mailbox_sender,
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox_sender.id),
                "subject": "Test Message",
                "draftBody": "Test body",
                "signatureId": str(reply_template.id),
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        message = models.Message.objects.get(id=response.data["id"])
        assert message.signature is None
