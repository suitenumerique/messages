"""Unit tests for entitlements backends."""

import pytest
import responses

from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend
from core.entitlements.backends.dummy import DummyEntitlementsBackend

pytestmark = pytest.mark.django_db


class TestDummyBackend:
    """Tests for the DummyEntitlementsBackend."""

    def test_get_user_entitlements(self):
        backend = DummyEntitlementsBackend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result == {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

    def test_get_mailbox_entitlements(self):
        backend = DummyEntitlementsBackend()
        result = backend.get_mailbox_entitlements("user@example.com")
        assert result == {
            "max_storage": None,
            "storage_used": None,
        }


class TestDeployCenterBackend:
    """Tests for the DeployCenterEntitlementsBackend."""

    def _get_backend(self):
        return DeployCenterEntitlementsBackend(
            base_url="https://deploycenter.example.com",
            service_id="test-service",
            api_key="test-api-key",
            timeout=5,
        )

    @responses.activate
    def test_get_user_entitlements_success(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            json={
                "can_access": True,
                "can_admin_maildomains": ["example.com", "test.org"],
                "operator": {"name": "Test Operator"},
            },
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements(
            "user-sub", "user@example.com", access_token="test-token"
        )

        assert result == {
            "can_access": True,
            "can_admin_maildomains": ["example.com", "test.org"],
            "operator": {"name": "Test Operator"},
        }

        # Verify request parameters
        request = responses.calls[0].request
        assert "service_id=test-service" in request.url
        assert "account_type=user" in request.url
        assert "account_id=user%40example.com" in request.url
        assert request.headers["X-Service-Auth"] == "ApiKey test-api-key"
        assert request.headers["Authorization"] == "Bearer test-token"

    @responses.activate
    def test_get_user_entitlements_no_access_token(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            json={"can_access": True, "can_admin_maildomains": []},
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")

        request = responses.calls[0].request
        assert "Authorization" not in request.headers

    @responses.activate
    def test_get_user_entitlements_can_access_false(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            json={"can_access": False, "can_admin_maildomains": []},
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result["can_access"] is False

    @responses.activate
    def test_get_user_entitlements_server_error_raises(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            status=500,
        )

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")

    @responses.activate
    def test_get_user_entitlements_timeout_raises(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            body=ConnectionError("Connection timed out"),
        )

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_user_entitlements("user-sub", "user@example.com")

    @responses.activate
    def test_get_mailbox_entitlements_success(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            json={
                "max_storage": 5368709120,
                "storage_used": 1073741824,
            },
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_mailbox_entitlements("mailbox@example.com")

        assert result == {
            "max_storage": 5368709120,
            "storage_used": 1073741824,
        }

        request = responses.calls[0].request
        assert "account_type=mailbox" in request.url
        assert "account_id=mailbox%40example.com" in request.url

    @responses.activate
    def test_get_mailbox_entitlements_server_error_raises(self):
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            status=503,
        )

        backend = self._get_backend()
        with pytest.raises(EntitlementsUnavailableError):
            backend.get_mailbox_entitlements("mailbox@example.com")

    @responses.activate
    def test_get_user_entitlements_missing_fields_defaults(self):
        """Backend should provide sensible defaults for missing response fields."""
        responses.add(
            responses.GET,
            "https://deploycenter.example.com/api/v1.0/entitlements",
            json={},
            status=200,
        )

        backend = self._get_backend()
        result = backend.get_user_entitlements("user-sub", "user@example.com")
        assert result == {
            "can_access": False,
            "can_admin_maildomains": [],
            "operator": None,
        }

    def test_base_url_trailing_slash_stripped(self):
        backend = DeployCenterEntitlementsBackend(
            base_url="https://deploycenter.example.com/",
            service_id="svc",
            api_key="key",
        )
        assert backend.base_url == "https://deploycenter.example.com"
