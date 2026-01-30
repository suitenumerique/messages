"""Tests for the entitlements API endpoint."""

from unittest import mock

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from core import factories
from core.entitlements import EntitlementsUnavailableError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


class TestEntitlementsEndpoint:
    """Tests for GET /api/v1.0/entitlements/."""

    def test_anonymous_user_gets_401(self, api_client):
        response = api_client.get("/api/v1.0/entitlements/")
        assert response.status_code == 401

    @mock.patch("core.api.viewsets.entitlements.get_user_entitlements")
    def test_returns_user_entitlements(self, mock_get_user, api_client):
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get_user.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["example.com"],
            "operator": {"name": "Test Op"},
        }

        response = api_client.get("/api/v1.0/entitlements/")

        assert response.status_code == 200
        data = response.json()
        assert data["can_access"] is True
        assert data["can_admin_maildomains"] == ["example.com"]
        assert data["operator"] == {"name": "Test Op"}
        assert data["mailbox"] is None

    @mock.patch("core.api.viewsets.entitlements.get_mailbox_entitlements")
    @mock.patch("core.api.viewsets.entitlements.get_user_entitlements")
    def test_returns_mailbox_entitlements(
        self, mock_get_user, mock_get_mailbox, api_client
    ):
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(
            local_part="john", domain=domain, users_admin=[user]
        )
        api_client.force_authenticate(user=user)

        mock_get_user.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }
        mock_get_mailbox.return_value = {
            "max_storage": 5368709120,
            "storage_used": 1073741824,
        }

        response = api_client.get(
            "/api/v1.0/entitlements/", {"mailbox_id": str(mailbox.id)}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["mailbox"] == {
            "max_storage": 5368709120,
            "storage_used": 1073741824,
        }
        mock_get_mailbox.assert_called_once_with(f"john@example.com")

    @mock.patch("core.api.viewsets.entitlements.get_user_entitlements")
    def test_invalid_mailbox_id_returns_null_mailbox(
        self, mock_get_user, api_client
    ):
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get_user.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        response = api_client.get(
            "/api/v1.0/entitlements/",
            {"mailbox_id": "00000000-0000-0000-0000-000000000000"},
        )

        assert response.status_code == 200
        assert response.json()["mailbox"] is None

    @mock.patch("core.api.viewsets.entitlements.get_user_entitlements")
    def test_returns_503_when_user_entitlements_unavailable(
        self, mock_get_user, api_client
    ):
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get_user.side_effect = EntitlementsUnavailableError("Down")

        response = api_client.get("/api/v1.0/entitlements/")
        assert response.status_code == 503
        assert response.json()["detail"] == "Entitlements service unavailable"

    @mock.patch("core.api.viewsets.entitlements.get_mailbox_entitlements")
    @mock.patch("core.api.viewsets.entitlements.get_user_entitlements")
    def test_returns_503_when_mailbox_entitlements_unavailable(
        self, mock_get_user, mock_get_mailbox, api_client
    ):
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(
            local_part="john", domain=domain, users_admin=[user]
        )
        api_client.force_authenticate(user=user)

        mock_get_user.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }
        mock_get_mailbox.side_effect = EntitlementsUnavailableError("Down")

        response = api_client.get(
            "/api/v1.0/entitlements/", {"mailbox_id": str(mailbox.id)}
        )
        assert response.status_code == 503
