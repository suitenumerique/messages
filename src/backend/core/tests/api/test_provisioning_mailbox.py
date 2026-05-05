"""Tests for the provisioning mailbox lookup endpoint."""
# pylint: disable=redefined-outer-name,missing-function-docstring

import uuid

from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from core.enums import (
    ChannelApiKeyScope,
    ChannelScopeLevel,
    MailboxRoleChoices,
)
from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    UserFactory,
    make_api_key_channel,
)

MAILBOX_URL = reverse("provisioning-mailboxes")


def _make_api_key_channel(scopes=(ChannelApiKeyScope.MAILBOXES_READ.value,), **kwargs):
    """Wrapper around the shared factory pre-loaded with the
    provisioning-endpoint default scope (mailboxes:read). Callers can
    still override ``scopes`` and any other kwarg."""
    return make_api_key_channel(scopes=scopes, **kwargs)


@pytest.fixture
def auth_header():
    """Global-scope api_key with mailboxes:read."""
    channel, plaintext = _make_api_key_channel()
    return {
        "HTTP_X_CHANNEL_ID": str(channel.id),
        "HTTP_X_API_KEY": plaintext,
    }


@pytest.fixture
def domain():
    return MailDomainFactory(name="company.com")


@pytest.fixture
def mailbox(domain):
    return MailboxFactory(local_part="contact", domain=domain)


# =============================================================================
# Security — API key required, no bypass possible
# =============================================================================


@pytest.mark.django_db
class TestServiceAuthSecurity:
    """Verify that the provisioning endpoint requires ChannelApiKeyScope.MAILBOXES_READ."""

    def test_user_email_no_auth_returns_401(self, client):
        response = client.get(MAILBOX_URL, {"user_email": "a@b.com"})
        assert response.status_code == 401

    def test_user_email_wrong_token_returns_401(self, client):
        channel, _plaintext = _make_api_key_channel()
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY="not-the-real-key",
        )
        assert response.status_code == 401

    def test_user_email_unknown_channel_returns_401(self, client):
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_CHANNEL_ID=str(uuid.uuid4()),
            HTTP_X_API_KEY="anything",
        )
        assert response.status_code == 401

    def test_user_email_malformed_channel_returns_401(self, client):
        """A non-UUID X-Channel-Id must be rejected by the auth class
        before any DB lookup. Exercises the ValueError/ValidationError
        branch in ChannelApiKeyAuthentication, distinct from the
        DoesNotExist branch covered by the unknown-channel test above."""
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_CHANNEL_ID="not-a-uuid",
            HTTP_X_API_KEY="anything",
        )
        assert response.status_code == 401

    def test_user_email_wrong_scope_returns_403(self, client):
        channel, plaintext = _make_api_key_channel(
            scopes=(ChannelApiKeyScope.METRICS_READ.value,),
        )
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 403

    def test_email_no_auth_returns_401(self, client):
        response = client.get(MAILBOX_URL, {"email": "a@b.com"})
        assert response.status_code == 401

    def test_oidc_user_cannot_use_user_email_param(self, mailbox):
        user = UserFactory(email="attacker@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER)

        api_client = APIClient()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            MAILBOX_URL, {"user_email": "victim@oidc.example.com"}
        )
        assert response.status_code == 403

    def test_oidc_user_cannot_use_email_param(self, mailbox):
        user = UserFactory(email="attacker@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER)

        api_client = APIClient()
        api_client.force_authenticate(user=user)

        response = api_client.get(MAILBOX_URL, {"email": "contact@company.com"})
        assert response.status_code == 403

    def test_no_query_param_returns_400(self, client, auth_header):
        response = client.get(MAILBOX_URL, **auth_header)
        assert response.status_code == 400


# =============================================================================
# GET /provisioning/mailboxes/?user_email=...
# =============================================================================


@pytest.mark.django_db
class TestMailboxListByUser:
    """Tests for listing mailboxes by user_email."""

    def test_returns_mailboxes_for_user(self, client, auth_header, mailbox):
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            **auth_header,
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(mailbox.id)
        assert results[0]["email"] == "contact@company.com"
        assert results[0]["role"] == "admin"

    def test_returns_multiple_mailboxes_with_roles(self, client, auth_header, domain):
        user = UserFactory(email="bob@oidc.example.com")
        mb1 = MailboxFactory(local_part="info", domain=domain)
        mb2 = MailboxFactory(local_part="support", domain=domain)

        MailboxAccessFactory(mailbox=mb1, user=user, role=MailboxRoleChoices.SENDER)
        MailboxAccessFactory(mailbox=mb2, user=user, role=MailboxRoleChoices.VIEWER)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "bob@oidc.example.com"},
            **auth_header,
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 2

        by_email = {r["email"]: r for r in results}
        assert by_email["info@company.com"]["role"] == "sender"
        assert by_email["support@company.com"]["role"] == "viewer"

    def test_returns_empty_for_unknown_user(self, client, auth_header):
        response = client.get(
            MAILBOX_URL,
            {"user_email": "nobody@nowhere.com"},
            **auth_header,
        )

        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_response_fields(self, client, auth_header, mailbox):
        """Response includes id, email, name, role, and users."""
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            **auth_header,
        )

        result = response.json()["results"][0]
        assert set(result.keys()) == {
            "id",
            "email",
            "name",
            "role",
            "users",
            "is_identity",
        }

    def test_users_includes_all_mailbox_users(self, client, auth_header, mailbox):
        """The users array lists ALL users with access, not just the queried one."""
        alice = UserFactory(email="alice@oidc.example.com")
        bob = UserFactory(email="bob@oidc.example.com")

        MailboxAccessFactory(mailbox=mailbox, user=alice, role=MailboxRoleChoices.ADMIN)
        MailboxAccessFactory(mailbox=mailbox, user=bob, role=MailboxRoleChoices.SENDER)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            **auth_header,
        )

        result = response.json()["results"][0]
        users_by_email = {u["email"]: u for u in result["users"]}
        assert len(users_by_email) == 2
        assert users_by_email["alice@oidc.example.com"]["role"] == "admin"
        assert users_by_email["bob@oidc.example.com"]["role"] == "sender"


# =============================================================================
# GET /provisioning/mailboxes/?email=...
# =============================================================================


@pytest.mark.django_db
class TestMailboxListByEmail:
    """Tests for looking up a mailbox by its email address."""

    def test_returns_mailbox_by_email(self, client, auth_header, mailbox):
        alice = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=alice, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {"email": "contact@company.com"},
            **auth_header,
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(mailbox.id)
        assert results[0]["email"] == "contact@company.com"
        # No top-level role in email lookup mode
        assert "role" not in results[0]

    def test_email_lookup_includes_users(self, client, auth_header, mailbox):
        """Email lookup also includes the users array."""
        alice = UserFactory(email="alice@oidc.example.com")
        bob = UserFactory(email="bob@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=alice, role=MailboxRoleChoices.ADMIN)
        MailboxAccessFactory(mailbox=mailbox, user=bob, role=MailboxRoleChoices.VIEWER)

        response = client.get(
            MAILBOX_URL,
            {"email": "contact@company.com"},
            **auth_header,
        )

        result = response.json()["results"][0]
        assert "users" in result
        users_by_email = {u["email"]: u for u in result["users"]}
        assert users_by_email["alice@oidc.example.com"]["role"] == "admin"
        assert users_by_email["bob@oidc.example.com"]["role"] == "viewer"

    def test_returns_empty_for_unknown_email(self, client, auth_header):
        response = client.get(
            MAILBOX_URL,
            {"email": "nope@nowhere.com"},
            **auth_header,
        )

        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_returns_empty_for_invalid_email(self, client, auth_header):
        response = client.get(
            MAILBOX_URL,
            {"email": "no-at-sign"},
            **auth_header,
        )

        assert response.status_code == 200
        assert response.json()["results"] == []


# =============================================================================
# add_maildomain_custom_attributes
# =============================================================================


@pytest.mark.django_db
class TestMaildomainCustomAttributes:
    """Test the add_maildomain_custom_attributes query parameter."""

    def test_user_email_with_custom_attributes(self, client, auth_header, domain):
        domain.custom_attributes = {"siret": "123456789", "org_name": "ACME"}
        domain.save()
        mb = MailboxFactory(local_part="info", domain=domain)
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mb, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {
                "user_email": "alice@oidc.example.com",
                "add_maildomain_custom_attributes": "siret,org_name",
            },
            **auth_header,
        )

        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["maildomain_custom_attributes"]["siret"] == "123456789"
        assert result["maildomain_custom_attributes"]["org_name"] == "ACME"

    def test_email_with_custom_attributes(self, client, auth_header, domain):
        domain.custom_attributes = {"siret": "987654321"}
        domain.save()
        MailboxFactory(local_part="info", domain=domain)

        response = client.get(
            MAILBOX_URL,
            {
                "email": "info@company.com",
                "add_maildomain_custom_attributes": "siret",
            },
            **auth_header,
        )

        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["maildomain_custom_attributes"]["siret"] == "987654321"

    def test_missing_key_returns_none(self, client, auth_header, domain):
        domain.custom_attributes = {"siret": "123"}
        domain.save()
        mb = MailboxFactory(local_part="info", domain=domain)
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mb, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {
                "user_email": "alice@oidc.example.com",
                "add_maildomain_custom_attributes": "siret,nonexistent",
            },
            **auth_header,
        )

        result = response.json()["results"][0]
        assert result["maildomain_custom_attributes"]["siret"] == "123"
        assert result["maildomain_custom_attributes"]["nonexistent"] is None

    def test_no_param_means_no_field(self, client, auth_header, domain):
        """Without the param, maildomain_custom_attributes is absent."""
        domain.custom_attributes = {"siret": "123"}
        domain.save()
        mb = MailboxFactory(local_part="info", domain=domain)
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mb, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            **auth_header,
        )

        result = response.json()["results"][0]
        assert "maildomain_custom_attributes" not in result


# =============================================================================
# ProvisioningMailboxView is global-only — non-global api_key channels are
# rejected, regardless of which scope filter would otherwise narrow results.
# =============================================================================


@pytest.mark.django_db
class TestMailboxListGlobalOnly:
    """The endpoint refuses any non-global api_key channel."""

    def test_maildomain_scope_returns_403(self, client, domain):
        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.MAILDOMAIN,
            maildomain=domain,
        )
        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 403

    def test_mailbox_scope_returns_403(self, client, mailbox):
        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
        )
        response = client.get(
            MAILBOX_URL,
            {"email": "contact@company.com"},
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 403
