"""Tests for the entitlements middleware."""

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


class TestEntitlementsMiddleware:
    """Tests for the EntitlementsMiddleware."""

    def test_allows_unauthenticated_requests(self, api_client):
        """Unauthenticated requests should pass through (DRF handles 401)."""
        response = api_client.get("/api/v1.0/config/")
        # Config endpoint allows any - should pass through
        assert response.status_code == 200

    def test_skips_non_api_paths(self, api_client):
        """Non-API paths should not be checked."""
        response = api_client.get("/admin/")
        # May get redirect or 404, but not 403/503 from middleware
        assert response.status_code != 403
        assert response.status_code != 503

    def test_skips_config_endpoint(self, api_client):
        """Config endpoint should be excluded from entitlements checks."""
        response = api_client.get("/api/v1.0/config/")
        assert response.status_code == 200

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_skips_entitlements_endpoint(self, mock_get, api_client):
        """Entitlements endpoint itself should be excluded."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        response = api_client.get("/api/v1.0/entitlements/")
        # The middleware should not call get_user_entitlements for this path
        mock_get.assert_not_called()

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_skips_superusers(self, mock_get, api_client):
        """Superusers should bypass entitlements checks."""
        user = factories.UserFactory(is_superuser=True)
        api_client.force_authenticate(user=user)

        response = api_client.get("/api/v1.0/users/")
        mock_get.assert_not_called()

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_allows_access_when_can_access_true(self, mock_get, api_client):
        """User with can_access=True should be allowed."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        response = api_client.get("/api/v1.0/users/")
        # Should pass through middleware (may get whatever the view returns)
        assert response.status_code != 403
        assert response.status_code != 503

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_blocks_access_when_can_access_false(self, mock_get, api_client):
        """User with can_access=False should get 403."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get.return_value = {
            "can_access": False,
            "can_admin_maildomains": [],
            "operator": None,
        }

        response = api_client.get("/api/v1.0/users/")
        assert response.status_code == 403
        assert response.json()["detail"] == "Access denied by entitlements policy"

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_returns_503_on_unavailable_error(self, mock_get, api_client):
        """Should return 503 when entitlements service is unavailable."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        mock_get.side_effect = EntitlementsUnavailableError("Backend down")

        response = api_client.get("/api/v1.0/users/")
        assert response.status_code == 503
        assert response.json()["detail"] == "Entitlements service unavailable"

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_skips_mta_endpoint(self, mock_get, api_client):
        """MTA endpoints should be excluded from entitlements checks."""
        # This endpoint requires special auth, so we won't get 403 from middleware
        response = api_client.post("/api/v1.0/mta/check-recipients/")
        mock_get.assert_not_called()

    @mock.patch("core.entitlements.middleware.get_user_entitlements")
    def test_skips_metrics_endpoint(self, mock_get, api_client):
        """Metrics endpoints should be excluded from entitlements checks."""
        response = api_client.get("/api/v1.0/metrics/maildomain_users/")
        mock_get.assert_not_called()
