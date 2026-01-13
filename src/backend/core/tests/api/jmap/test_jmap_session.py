"""Tests for the JMAP session endpoint."""

from django.conf import settings

import pytest

from core import factories

pytestmark = pytest.mark.django_db


class TestJMAPSession:
    """Tests for GET /api/v1.0/jmap/session."""

    def test_session_returns_capabilities(self, api_client, user):
        """Test that the session endpoint returns JMAP capabilities."""
        api_client.force_authenticate(user=user)

        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 200
        assert "capabilities" in response.data
        assert "urn:ietf:params:jmap:core" in response.data["capabilities"]
        assert "urn:ietf:params:jmap:mail" in response.data["capabilities"]

    def test_session_returns_account(self, api_client, user, mailbox):
        """Test that the session includes the user's account."""
        api_client.force_authenticate(user=user)

        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 200
        assert "accounts" in response.data
        assert str(user.id) in response.data["accounts"]

        account = response.data["accounts"][str(user.id)]
        assert account["isPersonal"] is True
        assert "urn:ietf:params:jmap:mail" in account["accountCapabilities"]

    def test_session_returns_primary_account(self, api_client, user, mailbox):
        """Test that the session includes primary accounts."""
        api_client.force_authenticate(user=user)

        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 200
        assert "primaryAccounts" in response.data
        assert response.data["primaryAccounts"]["urn:ietf:params:jmap:mail"] == str(
            user.id
        )

    def test_session_returns_api_url(self, api_client, user):
        """Test that the session includes the API URL."""
        api_client.force_authenticate(user=user)

        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 200
        assert "apiUrl" in response.data
        assert f"/api/{settings.API_VERSION}/jmap/" in response.data["apiUrl"]

    def test_session_requires_authentication(self, api_client):
        """Test that the session endpoint requires authentication."""
        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 401

    def test_session_uses_mailbox_email_as_name(self, api_client, user):
        """Test that account name uses the mailbox email address."""
        factories.MailboxFactory(
            local_part="john.doe",
            domain__name="example.com",
            users_read=[user],
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(f"/api/{settings.API_VERSION}/jmap/session")

        assert response.status_code == 200
        account = response.data["accounts"][str(user.id)]
        assert account["name"] == "john.doe@example.com"
