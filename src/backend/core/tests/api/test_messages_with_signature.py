"""Test API messages create with signature."""
# pylint: disable=unused-argument

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
        full_name="John Doe", custom_attributes={"job_title": "Software Engineer"}
    )


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="mailbox_access")
def fixture_mailbox_access(user, mailbox):
    """Create mailbox access for the user with SENDER role."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.SENDER
    )


@pytest.fixture(name="draft_detail_url")
def fixture_draft_detail_url():
    """Return the draft message detail URL with a placeholder for the message ID."""
    return lambda message_id: f"{reverse('draft-message')}{message_id}/"


class TestApiDraftMessageWithSignature:
    """Test API draft message creation with signature."""

    def test_draft_with_forced_domain_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a forced domain signature."""
        # Create a forced signature for the domain
        forced_signature = factories.MessageTemplateFactory(
            name="Forced Domain Signature",
            html_body="<p>Best regards,<br>{name}<br>{job_title}</p>",
            text_body="Best regards,\n{name}\n{job_title}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            maildomain=mailbox.domain,
        )

        # Create a non-forced signature for the domain
        non_forced_signature = factories.MessageTemplateFactory(
            name="Non-Forced Domain Signature",
            html_body="<p>Regards,<br>{name}</p>",
            text_body="Regards,\n{name}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            maildomain=mailbox.domain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                # No signatureId provided
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the forced signature was automatically assigned
        assert draft_message.signature == forced_signature

        # Try to create a draft message with a non-forced signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with non-forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(non_forced_signature.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the forced signature was still assigned
        assert draft_message.signature == forced_signature

    def test_draft_with_forced_mailbox_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a forced mailbox signature."""
        # Create a forced signature for the mailbox
        forced_signature = factories.MessageTemplateFactory(
            name="Forced Mailbox Signature",
            html_body="<p>Best regards,<br>{name}<br>{job_title}</p>",
            text_body="Best regards,\n{name}\n{job_title}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            mailbox=mailbox,
        )

        # Create a non-forced signature for the mailbox
        non_forced_signature = factories.MessageTemplateFactory(
            name="Non-Forced Mailbox Signature",
            html_body="<p>Regards,<br>{name}</p>",
            text_body="Regards,\n{name}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the forced signature was automatically assigned
        assert draft_message.signature == forced_signature

        # Try to create a draft message with a non-forced signature

        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with non-forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(non_forced_signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the forced signature was still assigned
        assert draft_message.signature == forced_signature

    def test_draft_with_selected_mailbox_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a selected signature."""
        # Create a non-forced signature for the mailbox
        signature = factories.MessageTemplateFactory(
            name="Selected Signature",
            html_body="<p>Best regards,<br>{name}<br>{job_title}</p>",
            text_body="Best regards,\n{name}\n{job_title}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message with selected signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with selected signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the selected signature was assigned
        assert draft_message.signature == signature

    def test_draft_with_selected_domain_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a selected signature."""
        # Create a non-forced signature for the mailbox
        signature = factories.MessageTemplateFactory(
            name="Selected Signature",
            html_body="<p>Best regards,<br>{name}<br>{job_title}</p>",
            text_body="Best regards,\n{name}\n{job_title}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            maildomain=mailbox.domain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message with selected signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with selected signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the selected signature was assigned
        assert draft_message.signature == signature

    def test_draft_with_inactive_forced_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message when forced signature exists but is inactive."""
        # Create an inactive forced signature for the domain
        factories.MessageTemplateFactory(
            name="Inactive Forced Domain Signature",
            html_body="<p>Best regards,<br>{name}<br>{job_title}</p>",
            text_body="Best regards,\n{name}\n{job_title}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=False,  # Inactive
            is_forced=True,
            maildomain=mailbox.domain,
        )

        # Create an active non-forced signature
        active_signature = factories.MessageTemplateFactory(
            name="Active Non-Forced Signature",
            html_body="<p>Regards,<br>{name}</p>",
            text_body="Regards,\n{name}",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message with selected signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with inactive forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(active_signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the selected signature was assigned (since forced signature is inactive)
        assert draft_message.signature == active_signature

    def test_draft_with_forced_signatures_priority(self, user, mailbox, mailbox_access):
        """Test that domain forced signature takes priority over mailbox forced signature."""
        # Create a forced signature for the mailbox
        mailbox_signature = factories.MessageTemplateFactory(
            name="Forced Mailbox Signature",
            html_body="<p>Mailbox signature</p>",
            text_body="Mailbox signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            mailbox=mailbox,
        )
        # Create a forced signature for the domain
        domain_signature = factories.MessageTemplateFactory(
            name="Forced Domain Signature",
            html_body="<p>Domain signature</p>",
            text_body="Domain signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            maildomain=mailbox.domain,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test signature priority",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                # No signatureId provided
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the domain forced signature was assigned (takes priority)
        assert draft_message.signature != mailbox_signature
        assert draft_message.signature == domain_signature

    def test_draft_with_other_mailbox_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a signature from another mailbox."""
        # Create a signature for another mailbox
        other_mailbox = factories.MailboxFactory()
        other_signature = factories.MessageTemplateFactory(
            name="Other Mailbox Signature",
            html_body="<p>Other mailbox signature</p>",
            text_body="Other mailbox signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=other_mailbox,
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message with selected signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with other mailbox signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(other_signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the other mailbox signature was not assigned
        assert draft_message.signature is None

    def test_draft_with_other_domain_signature(self, user, mailbox, mailbox_access):
        """Test creating a draft message with a signature from another domain."""
        # Create a signature for another domain
        other_domain = factories.MailDomainFactory()
        other_signature = factories.MessageTemplateFactory(
            name="Other Domain Signature",
            html_body="<p>Other domain signature</p>",
            text_body="Other domain signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=other_domain,
            is_active=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Create a draft message with selected signature
        response = client.post(
            reverse("draft-message"),
            {
                "senderId": str(mailbox.id),
                "subject": "Test with other domain signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": str(other_signature.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        draft_message = models.Message.objects.get(id=response.data["id"])

        # Verify the other domain signature was not assigned
        assert draft_message.signature is None

    def test_update_draft_with_no_signature_but_forced_signature(
        self, user, mailbox, mailbox_access, draft_detail_url
    ):
        """Test updating a draft message with a none selected signature and a forced signature."""

        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        signature = factories.MessageTemplateFactory(
            name="Mailbox Signature",
            html_body="<p>Mailbox signature</p>",
            text_body="Mailbox signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )

        # Create a draft message
        sender = factories.ContactFactory(mailbox=mailbox)
        # create a message in the thread
        draft_message = factories.MessageFactory(
            thread=thread,
            sender=sender,
            is_draft=True,
            signature=signature,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Update a draft message
        response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "draftBody": "Test content updated 1",
                "to": ["recipient@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        draft_message.refresh_from_db()

        # Verify the signature is still assigned
        assert draft_message.signature == signature

        # Create a forced signature for the mailbox
        forced_signature = factories.MessageTemplateFactory(
            name="Forced Mailbox Signature",
            html_body="<p>Mailbox signature</p>",
            text_body="Mailbox signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            mailbox=mailbox,
        )
        # Update a draft message
        response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "draftBody": "Test content updated 2",
                "to": ["recipient@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        draft_message.refresh_from_db()

        # Verify the forced signature was assigned
        assert draft_message.signature == forced_signature

    def test_update_draft_with_none_selected_but_forced_signature(
        self, user, mailbox, mailbox_access, draft_detail_url
    ):
        """Test updating a draft message with a none selected signature and a forced signature."""
        # Create a forced signature for the mailbox
        forced_signature = factories.MessageTemplateFactory(
            name="Forced Mailbox Signature",
            html_body="<p>Mailbox signature</p>",
            text_body="Mailbox signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=True,
            mailbox=mailbox,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        sender = factories.ContactFactory(mailbox=mailbox)
        draft_message = factories.MessageFactory(
            sender=sender,
            is_draft=True,
            thread=thread,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Update a draft message
        response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "subject": "Test with forced signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        draft_message.refresh_from_db()

        # Verify the forced signature
        assert draft_message.signature == forced_signature

        # try to update with an other signature
        signature = factories.MessageTemplateFactory(
            name="Other Signature",
            html_body="<p>Other signature</p>",
            text_body="Other signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )
        response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "subject": "Test with other signature",
                "draftBody": "Test content",
                "signatureId": str(signature.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        draft_message.refresh_from_db()

        # Verify the other signature was not assigned
        assert draft_message.signature == forced_signature

    def test_update_draft_in_order_to_clear_signature(
        self, user, mailbox, mailbox_access, draft_detail_url
    ):
        """Test updating a draft message in order to clear the signature."""
        # Create a signature for the mailbox
        signature = factories.MessageTemplateFactory(
            name="Signature",
            html_body="<p>Signature</p>",
            text_body="Signature",
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            is_active=True,
            is_forced=False,
            mailbox=mailbox,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender = factories.ContactFactory(mailbox=mailbox)
        draft_message = factories.MessageFactory(
            sender=sender,
            is_draft=True,
            thread=thread,
            signature=signature,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # Update a draft message
        response = client.put(
            draft_detail_url(draft_message.id),
            {
                "senderId": mailbox.id,
                "subject": "Test with signature",
                "draftBody": "Test content",
                "to": ["recipient@example.com"],
                "signatureId": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        draft_message.refresh_from_db()

        # Verify the signature was cleared
        assert draft_message.signature is None
