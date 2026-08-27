"""Address case handling at the API boundaries.

Three boundaries share one rule: fold what we own, echo back what the
caller sent.

- MTA-in ``/check``: verdicts are keyed by the exact RCPT string sent.
- Admin mailbox creation: the stored local part is folded, and the
  uniqueness and denylist checks run on the folded form.
- Provisioning lookups: resolve regardless of the caller's casing.
"""
# pylint: disable=redefined-outer-name,unused-argument

import hashlib
import json

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
from core.enums import ChannelApiKeyScope, MailDomainAccessRoleChoices
from core.tests.mda.test_addresses import KELVIN_SIGN

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def domain():
    return factories.MailDomainFactory(name="example.com")


class TestInboundMTACheckEndpoint:
    """``/inbound/mta/check/`` resolves folded, answers verbatim."""

    URL = "/api/v1.0/inbound/mta/check/"

    def _post(self, api_client, addresses):
        body = json.dumps({"addresses": addresses}).encode("utf-8")
        token = jwt.encode(
            {
                "exp": 9999999999,
                "body_hash": hashlib.sha256(body).hexdigest(),
            },
            settings.MDA_API_SECRET,
            algorithm="HS256",
        )
        return api_client.post(
            self.URL,
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=False)
    def test_verdict_is_keyed_by_the_exact_rcpt_string(self, api_client, domain):
        """MTA-in looks the verdict up by the string it sent; folding must not leak."""
        factories.MailboxFactory(local_part="john.doe", domain=domain)

        response = self._post(
            api_client,
            [
                "John.Doe@EXAMPLE.COM",
                "john.doe@example.com",
                "nobody@example.com",
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "John.Doe@EXAMPLE.COM": True,
            "john.doe@example.com": True,
            "nobody@example.com": False,
        }

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=False)
    def test_lookalike_rcpt_is_rejected(self, api_client, domain):
        """U+212A KELVIN SIGN must not be accepted for ``nick@``."""
        factories.MailboxFactory(local_part="nick", domain=domain)

        response = self._post(api_client, [f"nic{KELVIN_SIGN}@example.com"])

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {f"nic{KELVIN_SIGN}@example.com": False}


class TestInboundMTADeliver:
    """A mixed-case RCPT lands in the folded mailbox, and only there."""

    URL = "/api/v1.0/inbound/mta/deliver/"

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=False)
    def test_mixed_case_recipient_lands_in_the_folded_mailbox(self, api_client, domain):
        mailbox = factories.MailboxFactory(local_part="john.doe", domain=domain)
        raw = (
            b"From: Sender <Sender@Other.Example>\r\n"
            b"To: John.Doe@EXAMPLE.COM\r\n"
            b"Subject: Case test\r\n"
            b"Message-ID: <case-test@other.example>\r\n"
            b"\r\n"
            b"Body.\r\n"
        )
        token = jwt.encode(
            {
                "exp": 9999999999,
                "body_hash": hashlib.sha256(raw).hexdigest(),
                "original_recipients": ["John.Doe@EXAMPLE.COM"],
                "sender": "Sender@Other.Example",
            },
            settings.MDA_API_SECRET,
            algorithm="HS256",
        )

        response = api_client.post(
            self.URL,
            data=raw,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["delivered"] == 1
        # No second mailbox was conjured for the uppercase spelling
        assert models.Mailbox.objects.filter(domain=domain).count() == 1
        # ...and the message really landed in the existing one
        assert models.Message.objects.filter(
            thread__accesses__mailbox=mailbox, mime_id="case-test@other.example"
        ).exists()

    @override_settings(MESSAGES_ACCEPT_ALL_EMAILS=False)
    def test_sender_contact_keeps_the_case_it_was_received_in(self, api_client, domain):
        """Addresses we merely carry are not folded."""
        factories.MailboxFactory(local_part="john.doe", domain=domain)
        raw = (
            b"From: Sender <Sender.Name@Other.Example>\r\n"
            b"To: john.doe@example.com\r\n"
            b"Subject: Case test\r\n"
            b"Message-ID: <case-keep@other.example>\r\n"
            b"\r\n"
            b"Body.\r\n"
        )
        token = jwt.encode(
            {
                "exp": 9999999999,
                "body_hash": hashlib.sha256(raw).hexdigest(),
                "original_recipients": ["john.doe@example.com"],
                "sender": "Sender.Name@Other.Example",
            },
            settings.MDA_API_SECRET,
            algorithm="HS256",
        )

        response = api_client.post(
            self.URL,
            data=raw,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        assert response.status_code == status.HTTP_200_OK
        assert models.Contact.objects.filter(email="Sender.Name@Other.Example").exists()


class TestAdminMailboxCreation:
    """The admin create endpoint stores a folded local part."""

    @pytest.fixture
    def admin_user(self, domain):
        user = factories.UserFactory()
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )
        return user

    def url(self, domain):
        return reverse(
            "admin-maildomains-mailbox-list", kwargs={"maildomain_pk": domain.pk}
        )

    def test_local_part_is_folded(self, api_client, admin_user, domain):
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            self.url(domain),
            {
                "local_part": "John.DOE",
                "metadata": {
                    "type": "personal",
                    "first_name": "John",
                    "last_name": "Doe",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == "john.doe"
        mailbox = models.Mailbox.objects.get(domain=domain)
        assert mailbox.local_part == "john.doe"
        # The identity records we mint alongside carry the same folded address
        assert mailbox.contact.email == "john.doe@example.com"
        assert models.User.objects.filter(email="john.doe@example.com").exists()

    def test_case_variant_of_an_existing_mailbox_is_rejected(
        self, api_client, admin_user, domain
    ):
        factories.MailboxFactory(local_part="john.doe", domain=domain)
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            self.url(domain),
            {
                "local_part": "John.Doe",
                "metadata": {"type": "shared", "name": "Shared"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "local_part" in response.data
        assert models.Mailbox.objects.filter(domain=domain).count() == 1

    @override_settings(MESSAGES_MAILBOX_LOCALPART_DENYLIST_PERSONAL=["contact"])
    def test_denylist_cannot_be_bypassed_with_case(
        self, api_client, admin_user, domain
    ):
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            self.url(domain),
            {
                "local_part": "CoNtAcT",
                "metadata": {
                    "type": "personal",
                    "first_name": "A",
                    "last_name": "B",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "local_part_denied" in response.data
        assert not models.Mailbox.objects.filter(domain=domain).exists()

    def test_non_ascii_local_part_is_rejected(self, api_client, admin_user, domain):
        """We stay ASCII-only for now; an accented prefix is a 400, not a mailbox."""
        api_client.force_authenticate(user=admin_user)

        response = api_client.post(
            self.url(domain),
            {
                "local_part": "josé",
                "metadata": {"type": "shared", "name": "Jose"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not models.Mailbox.objects.filter(domain=domain).exists()


class TestProvisioningLookup:
    """Provisioning resolves mailboxes and users whatever the caller's casing."""

    URL = "/api/v1.0/provisioning/mailboxes/"

    @pytest.fixture
    def auth_header(self):
        """Global-scope api_key channel with mailboxes:read."""
        channel, plaintext = factories.make_api_key_channel(
            scopes=(ChannelApiKeyScope.MAILBOXES_READ.value,)
        )
        return {
            "HTTP_X_CHANNEL_ID": str(channel.id),
            "HTTP_X_API_KEY": plaintext,
        }

    def test_lookup_by_email_is_case_insensitive(self, client, auth_header, domain):
        factories.MailboxFactory(local_part="john.doe", domain=domain)

        response = client.get(f"{self.URL}?email=John.Doe@EXAMPLE.COM", **auth_header)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["results"]) == 1

    def test_lookup_by_user_email_is_case_insensitive(
        self, client, auth_header, domain
    ):
        user = factories.UserFactory(email="john.doe@example.com")
        mailbox = factories.MailboxFactory(local_part="john.doe", domain=domain)
        factories.MailboxAccessFactory(mailbox=mailbox, user=user)

        response = client.get(
            f"{self.URL}?user_email=John.Doe@EXAMPLE.COM", **auth_header
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["results"]) == 1
