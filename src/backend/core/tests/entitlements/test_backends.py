"""Unit tests for entitlements backends."""

from django.core.cache import cache
from django.test import override_settings

import pytest
import requests
import responses

from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend
from core.entitlements.backends.local import LocalEntitlementsBackend


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestLocalBackend:
    """Tests for the LocalEntitlementsBackend."""

    def test_get_user_entitlements(self):
        """Local backend grants access and returns None for admin domains."""
        backend = LocalEntitlementsBackend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result == {
            "can_access": True,
            "can_admin_maildomains": None,
        }

    def test_force_refresh_has_no_effect(self):
        """force_refresh is accepted but has no effect on local backend."""
        backend = LocalEntitlementsBackend()
        result = backend.get_user_entitlements(
            "user-sub", "user@example.com", force_refresh=True
        )
        assert result["can_access"] is True


BASE_URL = "https://deploycenter.example.com/api/v1.0/entitlements"


class TestDeployCenterBackend:
    """Tests for the DeployCenterEntitlementsBackend."""

    def _get_backend(self, **kwargs):
        defaults = {
            "base_url": BASE_URL,
            "service_id": "test-service",
            "api_key": "test-api-key",
            "timeout": 5,
        }
        defaults.update(kwargs)
        return DeployCenterEntitlementsBackend(**defaults)

    @responses.activate
    def test_get_user_entitlements_success(self):
        """Successful API call returns parsed entitlements."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": True,
                    "can_admin_maildomains": ["example.com", "test.org"],
                },
            },
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")

        assert result == {
            "can_access": True,
            "can_admin_maildomains": ["example.com", "test.org"],
        }

    @responses.activate
    def test_request_params_and_headers(self):
        """Request includes correct query params and auth header."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={"entitlements": {"can_access": True}},
            status=200,
        )

        backend = self._get_backend()
        backend.get_user_entitlements("user-sub", "user@example.com")

        request = responses.calls[0].request
        assert "service_id=test-service" in request.url
        assert "account_type=user" in request.url
        assert "account_email=user%40example.com" in request.url
        assert request.headers["X-Service-Auth"] == "Bearer test-api-key"

    @responses.activate
    def test_forwards_oidc_claims_as_query_params(self):
        """Configured OIDC claims from user_info are sent as query params."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={"entitlements": {"can_access": True}},
            status=200,
        )

        backend = self._get_backend(oidc_claims=["siret", "other_claim"])
        user_info = {"siret": "12345678901234", "other_claim": "value", "ignored": "x"}
        backend.get_user_entitlements(
            "user-sub", "user@example.com", user_info=user_info
        )

        request = responses.calls[0].request
        assert "siret=12345678901234" in request.url
        assert "other_claim=value" in request.url
        assert "ignored" not in request.url

    @responses.activate
    def test_missing_oidc_claim_not_sent(self):
        """If a configured claim is absent from user_info, it's just not sent."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={"entitlements": {"can_access": True}},
            status=200,
        )

        backend = self._get_backend(oidc_claims=["siret"])
        backend.get_user_entitlements(
            "user-sub", "user@example.com", user_info={"email": "x@y.com"}
        )

        request = responses.calls[0].request
        assert "siret" not in request.url

    @responses.activate
    def test_server_error_raises(self):
        """Server error raises EntitlementsUnavailableError."""
        responses.add(responses.GET, BASE_URL, status=500)

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")

    @responses.activate
    def test_connection_timeout_raises(self):
        """Connection timeout raises EntitlementsUnavailableError."""
        responses.add(
            responses.GET,
            BASE_URL,
            body=requests.exceptions.ConnectionError("Connection timed out"),
        )

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")

    @responses.activate
    @override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
    def test_cache_hit_returns_cached_without_http(self):
        """Second call uses cache, no HTTP request made."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": True,
                    "can_admin_maildomains": ["example.com"],
                },
            },
            status=200,
        )

        backend = self._get_backend()
        # First call populates cache
        result1 = backend.get_user_entitlements("user-sub", "user@example.com")
        # Second call should use cache — no HTTP
        result2 = backend.get_user_entitlements("user-sub", "user@example.com")

        assert result1 == result2
        assert len(responses.calls) == 1

    @responses.activate
    @override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
    def test_force_refresh_bypasses_cache(self):
        """force_refresh=True makes a new HTTP call even with cached data."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={"entitlements": {"can_access": True, "can_admin_maildomains": []}},
            status=200,
        )
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": True,
                    "can_admin_maildomains": ["new.com"],
                },
            },
            status=200,
        )

        backend = self._get_backend()
        result1 = backend.get_user_entitlements("user-sub", "user@example.com")
        result2 = backend.get_user_entitlements(
            "user-sub", "user@example.com", force_refresh=True
        )

        assert len(responses.calls) == 2
        assert result1["can_admin_maildomains"] == []
        assert result2["can_admin_maildomains"] == ["new.com"]

    @responses.activate
    @override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
    def test_failure_with_stale_cache_returns_cached(self):
        """When force_refresh fails but stale cache exists, return cached data."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": True,
                    "can_admin_maildomains": ["cached.com"],
                },
            },
            status=200,
        )
        # Second call will fail
        responses.add(responses.GET, BASE_URL, status=500)

        backend = self._get_backend()
        # Populate cache
        backend.get_user_entitlements("user-sub", "user@example.com")
        # Force refresh fails, should fall back to cache
        result = backend.get_user_entitlements(
            "user-sub", "user@example.com", force_refresh=True
        )

        assert result["can_admin_maildomains"] == ["cached.com"]
        assert len(responses.calls) == 2

    @responses.activate
    def test_failure_without_cache_raises(self):
        """API failure with no cache raises EntitlementsUnavailableError."""
        responses.add(responses.GET, BASE_URL, status=500)

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")

    @responses.activate
    def test_missing_fields_defaults(self):
        """Backend should provide sensible defaults for missing response fields."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={"entitlements": {}},
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result == {
            "can_access": False,
            "can_admin_maildomains": None,
        }

    @responses.activate
    def test_missing_entitlements_key(self):
        """Response with no entitlements key returns safe defaults."""
        responses.add(responses.GET, BASE_URL, json={}, status=200)

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result == {
            "can_access": False,
            "can_admin_maildomains": None,
        }

    @responses.activate
    @override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
    def test_different_users_have_different_cache_keys(self):
        """Each user_sub gets its own cache entry."""
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": True,
                    "can_admin_maildomains": ["a.com"],
                },
            },
            status=200,
        )
        responses.add(
            responses.GET,
            BASE_URL,
            json={
                "entitlements": {
                    "can_access": False,
                    "can_admin_maildomains": ["b.com"],
                },
            },
            status=200,
        )

        backend = self._get_backend()
        result1 = backend.get_user_entitlements("user1", "user1@example.com")
        result2 = backend.get_user_entitlements("user2", "user2@example.com")

        assert result1["can_admin_maildomains"] == ["a.com"]
        assert result2["can_admin_maildomains"] == ["b.com"]
        assert len(responses.calls) == 2

    @responses.activate
    def test_invalid_json_response_raises(self):
        """ValueError from response.json() should be handled."""
        responses.add(
            responses.GET,
            BASE_URL,
            body="not json",
            status=200,
            content_type="text/plain",
        )

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")
