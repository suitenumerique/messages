"""End-to-End tests for message sending and receiving flow."""
# pylint: disable=too-many-positional-arguments

import json
import random
import time

from django.test import override_settings
from django.urls import reverse

import pytest
import requests
from dkim import verify as dkim_verify
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.mda.signing import generate_dkim_key

# --- Fixtures (copied/adapted from test_messages_create.py) --- #


@pytest.fixture
def draft_detail_url():
    """Return the URL to update a draft message."""

    def _detail_url_factory(draft_id):
        # Assuming the detail view is registered like 'draft-message-detail'
        return reverse("draft-message-detail", kwargs={"message_id": draft_id})

    return _detail_url_factory


@pytest.fixture(name="authenticated_user")
def fixture_authenticated_user():
    """Return an authenticated user."""
    return factories.UserFactory()


@pytest.fixture(name="mailbox")
def fixture_mailbox(authenticated_user):
    """Return a mailbox associated with the authenticated user."""
    # Ensure the domain exists
    maildomain, _ = models.MailDomain.objects.get_or_create(name="example.com")
    # Create mailbox
    return factories.MailboxFactory(
        local_part=authenticated_user.email.split("@")[0],  # Use email local part
        domain=maildomain,
    )


@pytest.fixture(name="sender_contact")
def fixture_sender_contact(mailbox, authenticated_user):
    """Ensure a Contact exists representing the Mailbox owner."""
    mailbox_email = f"{mailbox.local_part}@{mailbox.domain.name}"
    contact, _ = models.Contact.objects.get_or_create(
        mailbox=mailbox,
        email__iexact=mailbox_email,
        defaults={
            "email": mailbox_email,
            "name": authenticated_user.full_name
            or authenticated_user.email.split("@")[0],  # Use full_name or fallback
        },
    )
    return contact


# Test private key for DKIM - using centralized generation
test_private_key, test_public_key = generate_dkim_key(key_size=1024)

# --- E2E Test Class --- #


@pytest.mark.django_db
class TestE2EMessageOutboundFlow:
    """Test the outbound flow: API -> MDA -> Mailcatcher -> Verification."""

    @override_settings(
        # Ensure MTA-OUT is configured to point to Mailcatcher
        MTA_OUT_HOST="mailcatcher:1025",
        MTA_OUT_SMTP_USERNAME=None,
        MTA_OUT_SMTP_PASSWORD=None,
        MTA_OUT_SMTP_USE_TLS=False,
    )
    def test_draft_send_receive_verify(
        self, mailbox, sender_contact, authenticated_user
    ):
        """Test creating a draft, sending it, receiving via mailcatcher, and verifying content/DKIM."""
        # --- Setup --- #
        # Create and configure DKIM key for the domain
        dkim_key = models.DKIMKey.objects.create(
            selector="testselector",
            private_key=test_private_key,
            public_key=test_public_key,
            key_size=1024,
            is_active=True,
            domain=mailbox.domain,
        )

        # Grant necessary permissions
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=enums.MailboxRoleChoices.EDITOR,  # Needed by send view permission
        )

        local_mailbox = factories.MailboxFactory(
            local_part="other-user",
            domain=mailbox.domain,
        )

        # Ensure sender contact exists (handled by fixture)
        assert sender_contact is not None

        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # --- Step 1: Create Draft --- #
        subject = f"e2e_test_{random.randint(0, 1000000000)}"
        draft_body_content = json.dumps({"test": "json content"})
        to_email = "recipient1@external-test.com"
        to_email_local = str(local_mailbox)
        cc_email = "recipient2@external-test.com"
        bcc_email = "recipient3@external-test.com"

        draft_payload = {
            "senderId": str(mailbox.id),
            "subject": subject,
            "draftBody": draft_body_content,
            "to": [to_email, to_email_local],
            "cc": [cc_email],
            "bcc": [bcc_email],
        }
        draft_response = client.post(
            reverse("draft-message"), draft_payload, format="json"
        )
        assert draft_response.status_code == status.HTTP_201_CREATED, (
            draft_response.content
        )
        draft_message_id = draft_response.data["id"]

        assert (
            models.MessageRecipient.objects.filter(message__id=draft_message_id).count()
            == 4
        )

        # --- Step 2: Send Draft --- #
        send_payload = {
            "messageId": draft_message_id,
            "senderId": str(mailbox.id),
            "textBody": "This is the E2E test body.",
            "htmlBody": "<p>This is the E2E test body.</p>",
        }
        send_response = client.post(
            reverse("send-message"), send_payload, format="json"
        )
        assert send_response.status_code == status.HTTP_200_OK, send_response.content

        assert (
            models.MessageRecipient.objects.filter(
                message__id=draft_message_id,
                delivery_status=enums.MessageDeliveryStatusChoices.INTERNAL,
            ).count()
            == 1
        )
        assert (
            models.MessageRecipient.objects.filter(
                message__id=draft_message_id,
                delivery_status=enums.MessageDeliveryStatusChoices.SENT,
            ).count()
            == 3
        )

        # Verify DB state after sending
        sent_message = models.Message.objects.get(id=draft_message_id)
        assert not sent_message.is_draft
        assert sent_message.sent_at is not None
        assert sent_message.blob  # Ensure blob was generated

        # --- Step 3: Wait for and Fetch from Mailcatcher --- #
        # Increased wait time for E2E test involving network/docker
        received_email = None
        mailcatcher_url = "http://mailcatcher:1080"
        max_wait_seconds = 20
        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            try:
                all_emails_resp = requests.get(f"{mailcatcher_url}/email", timeout=2)
                all_emails_resp.raise_for_status()
                all_emails = all_emails_resp.json()
                # Find email by subject
                emails = [e for e in all_emails if e.get("subject") == subject]
                if len(emails) > 0:
                    received_email = emails[0]
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(0.1)

        assert received_email is not None, (
            f"Email with subject '{subject}' not found in Mailcatcher after {max_wait_seconds}s"
        )

        # --- Step 4: Verify Received Email Content --- #
        mailcatcher_id = received_email["id"]
        email_source_resp = requests.get(
            f"{mailcatcher_url}/email/{mailcatcher_id}/source", timeout=3
        )
        email_source_resp.raise_for_status()
        email_source: bytes = email_source_resp.content

        # Check basic content (adapt based on compose_email output format)
        assert f"<{sender_contact.email}>".encode() in email_source
        assert to_email.encode() in email_source
        assert cc_email.encode() in email_source

        # BCC should NOT be in raw mime
        assert bcc_email.encode() not in email_source

        assert "Bcc: ".encode() not in email_source
        assert f"Subject: {subject}".encode() in email_source
        assert "This is the E2E test body".encode() in email_source

        # Check envelope recipients reported by mailcatcher
        envelope_from = received_email.get("envelope", {}).get("from", {})
        assert sender_contact.email == envelope_from["address"]
        envelope_to = [
            x["address"] for x in received_email.get("envelope", {}).get("to", [])
        ]
        # NO BCC in envelope
        assert sorted(envelope_to) == sorted([to_email, cc_email, bcc_email])

        # --- Step 5: Verify DKIM Signature --- #
        def get_dns_txt(fqdn, **kwargs):
            # Mock DNS lookup for the public key
            if fqdn == b"testselector._domainkey.example.com.":
                # Use the public key stored in the DKIM key model
                return f"v=DKIM1; k=rsa; p={dkim_key.public_key}".encode()
            return None

        # Ensure the DKIM-Signature header is present
        assert b"\nDKIM-Signature:" in email_source or email_source.startswith(
            b"DKIM-Signature:"
        )

        # Verify the signature
        assert dkim_verify(email_source, dnsfunc=get_dns_txt), (
            "DKIM verification failed"
        )

        # Ensure the local mailbox received the email
        local_mailbox_messages = models.Message.objects.filter(
            is_sender=False,
            thread__accesses__mailbox=local_mailbox,
        )

        assert local_mailbox_messages.count() == 1
        local_message = local_mailbox_messages.first()
        assert local_message.subject == subject
        assert local_message.blob.get_content() == sent_message.blob.get_content()
        assert local_message.sender.email == sender_contact.email
        assert local_message.parent is None
        assert local_message.is_draft is False

        assert models.Message.objects.all().count() == 2
