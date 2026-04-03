"""Tests for the provisioning mailbox lookup endpoint."""
# pylint: disable=redefined-outer-name,missing-function-docstring

from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from core.enums import MailboxRoleChoices
from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    UserFactory,
)

MAILBOX_URL = reverse("provisioning-mailboxes")


@pytest.fixture
def auth_header(settings):
    """Returns the authentication header for service-to-service calls."""
    settings.CALENDARS_API_KEY = "test-calendar-key"
    return {"HTTP_X_SERVICE_AUTH": "Bearer test-calendar-key"}


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
    """Verify that the provisioning endpoint requires HasCalendarsApiKey."""

    def test_user_email_no_auth_returns_403(self, client):
        response = client.get(MAILBOX_URL, {"user_email": "a@b.com"})
        assert response.status_code == 403

    def test_user_email_wrong_token_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = "correct-key"
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer wrong-key",
        )
        assert response.status_code == 403

    def test_user_email_no_key_configured_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = None
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer some-key",
        )
        assert response.status_code == 403

    def test_user_email_empty_key_configured_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = ""
        response = client.get(
            MAILBOX_URL,
            {"user_email": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer ",
        )
        assert response.status_code == 403

    def test_email_no_auth_returns_403(self, client):
        response = client.get(MAILBOX_URL, {"email": "a@b.com"})
        assert response.status_code == 403

    def test_email_wrong_token_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = "correct-key"
        response = client.get(
            MAILBOX_URL,
            {"email": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer wrong-key",
        )
        assert response.status_code == 403

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
        assert set(result.keys()) == {"id", "email", "name", "role", "users"}

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
