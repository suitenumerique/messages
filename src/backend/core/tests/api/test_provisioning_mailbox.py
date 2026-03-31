"""Tests for the provisioning mailbox and user lookup endpoints."""
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
USERS_URL = reverse("provisioning-users")


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
# Security — API key required for all service params, no bypass possible
# =============================================================================


@pytest.mark.django_db
class TestServiceAuthSecurity:
    """Verify that service query params absolutely require HasCalendarsApiKey."""

    # -- user_email param --

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

    # -- email param --

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

    def test_email_no_key_configured_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = None
        response = client.get(
            MAILBOX_URL,
            {"email": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer some-key",
        )
        assert response.status_code == 403

    # -- /users/ endpoint --

    def test_users_no_auth_returns_403(self, client):
        response = client.get(USERS_URL, {"mailbox": "a@b.com"})
        assert response.status_code == 403

    def test_users_wrong_token_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = "correct-key"
        response = client.get(
            USERS_URL,
            {"mailbox": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer wrong-key",
        )
        assert response.status_code == 403

    def test_users_no_key_configured_returns_403(self, client, settings):
        settings.CALENDARS_API_KEY = None
        response = client.get(
            USERS_URL,
            {"mailbox": "a@b.com"},
            HTTP_X_SERVICE_AUTH="Bearer some-key",
        )
        assert response.status_code == 403

    # -- OIDC user cannot use service params to see other users' data --

    def test_oidc_user_cannot_use_user_email_param(self, mailbox):
        """An OIDC-authenticated user cannot use user_email to enumerate mailboxes."""
        user = UserFactory(email="attacker@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER)

        api_client = APIClient()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            MAILBOX_URL, {"user_email": "victim@oidc.example.com"}
        )
        # Service params switch to HasCalendarsApiKey — OIDC auth is ignored
        assert response.status_code == 403

    def test_oidc_user_cannot_use_email_param(self, mailbox):
        """An OIDC-authenticated user cannot use email to look up arbitrary mailboxes."""
        user = UserFactory(email="attacker@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER)

        api_client = APIClient()
        api_client.force_authenticate(user=user)

        response = api_client.get(MAILBOX_URL, {"email": "contact@company.com"})
        assert response.status_code == 403

    def test_oidc_user_cannot_use_users_endpoint(self, mailbox):
        """An OIDC-authenticated user cannot access the /users/ service endpoint."""
        user = UserFactory(email="attacker@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)

        api_client = APIClient()
        api_client.force_authenticate(user=user)

        response = api_client.get(USERS_URL, {"mailbox": "contact@company.com"})
        assert response.status_code == 403


# =============================================================================
# GET /service/mailboxes/?user_email=...
# =============================================================================


@pytest.mark.django_db
class TestMailboxListByUser:
    """Tests for listing mailboxes by user_email (service-to-service)."""

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
        """Response includes id, email, name (MailboxLightSerializer) plus role."""
        user = UserFactory(email="alice@oidc.example.com")
        MailboxAccessFactory(mailbox=mailbox, user=user, role=MailboxRoleChoices.ADMIN)

        response = client.get(
            MAILBOX_URL,
            {"user_email": "alice@oidc.example.com"},
            **auth_header,
        )

        result = response.json()["results"][0]
        assert set(result.keys()) == {"id", "email", "name", "role"}


# =============================================================================
# GET /mailboxes/?email=...
# =============================================================================


@pytest.mark.django_db
class TestMailboxListByEmail:
    """Tests for looking up a mailbox by its email address."""

    def test_returns_mailbox_by_email(self, client, auth_header, mailbox):
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
        assert "role" not in results[0]

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
# GET /mailboxes/users/?mailbox_email=...
# =============================================================================


@pytest.mark.django_db
class TestMailboxUsers:
    """Tests for listing users of a mailbox."""

    def test_returns_users_with_roles(self, client, auth_header, mailbox):
        alice = UserFactory(email="alice@oidc.example.com")
        bob = UserFactory(email="bob@oidc.example.com")

        MailboxAccessFactory(mailbox=mailbox, user=alice, role=MailboxRoleChoices.ADMIN)
        MailboxAccessFactory(mailbox=mailbox, user=bob, role=MailboxRoleChoices.SENDER)

        response = client.get(
            USERS_URL,
            {"mailbox": "contact@company.com"},
            **auth_header,
        )

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 2

        by_email = {r["email"]: r for r in results}
        assert by_email["alice@oidc.example.com"]["role"] == "admin"
        assert by_email["bob@oidc.example.com"]["role"] == "sender"

    def test_returns_empty_for_unknown_mailbox(self, client, auth_header):
        response = client.get(
            USERS_URL,
            {"mailbox": "nope@nowhere.com"},
            **auth_header,
        )

        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_missing_param_returns_400(self, client, auth_header):
        response = client.get(USERS_URL, **auth_header)
        assert response.status_code == 400

    def test_invalid_email_returns_400(self, client, auth_header):
        response = client.get(
            USERS_URL,
            {"mailbox": "no-at-sign"},
            **auth_header,
        )
        assert response.status_code == 400
