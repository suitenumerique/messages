"""Tests for the entitlements caching layer."""

from unittest import mock

import pytest
from django.core.cache import cache

from core.entitlements import (
    EntitlementsUnavailableError,
    get_mailbox_entitlements,
    get_user_entitlements,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear cache before each test."""
    cache.clear()
    yield
    cache.clear()


class TestGetUserEntitlements:
    """Tests for the get_user_entitlements cached function."""

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_calls_backend_on_cache_miss(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }
        mock_get_backend.return_value = mock_backend

        result = get_user_entitlements("user-sub", "user@example.com")

        assert result["can_access"] is True
        mock_backend.get_user_entitlements.assert_called_once_with(
            "user-sub", "user@example.com", access_token=None
        )

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_returns_cached_value_on_hit(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["example.com"],
            "operator": None,
        }
        mock_get_backend.return_value = mock_backend

        # First call populates cache
        result1 = get_user_entitlements("user-sub", "user@example.com")
        # Second call should use cache
        result2 = get_user_entitlements("user-sub", "user@example.com")

        assert result1 == result2
        assert mock_backend.get_user_entitlements.call_count == 1

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_force_refresh_bypasses_cache(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }
        mock_get_backend.return_value = mock_backend

        get_user_entitlements("user-sub", "user@example.com")
        get_user_entitlements(
            "user-sub", "user@example.com", force_refresh=True
        )

        assert mock_backend.get_user_entitlements.call_count == 2

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_passes_access_token(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }
        mock_get_backend.return_value = mock_backend

        get_user_entitlements(
            "user-sub", "user@example.com", access_token="my-token"
        )

        mock_backend.get_user_entitlements.assert_called_once_with(
            "user-sub", "user@example.com", access_token="my-token"
        )

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_propagates_backend_error(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.side_effect = (
            EntitlementsUnavailableError("Backend down")
        )
        mock_get_backend.return_value = mock_backend

        with pytest.raises(EntitlementsUnavailableError):
            get_user_entitlements("user-sub", "user@example.com")

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_different_users_have_different_cache_keys(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_user_entitlements.side_effect = [
            {"can_access": True, "can_admin_maildomains": [], "operator": None},
            {
                "can_access": False,
                "can_admin_maildomains": [],
                "operator": None,
            },
        ]
        mock_get_backend.return_value = mock_backend

        result1 = get_user_entitlements("user1", "user1@example.com")
        result2 = get_user_entitlements("user2", "user2@example.com")

        assert result1["can_access"] is True
        assert result2["can_access"] is False
        assert mock_backend.get_user_entitlements.call_count == 2


class TestGetMailboxEntitlements:
    """Tests for the get_mailbox_entitlements cached function."""

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_calls_backend_on_cache_miss(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_mailbox_entitlements.return_value = {
            "max_storage": 5000000000,
            "storage_used": 1000000000,
        }
        mock_get_backend.return_value = mock_backend

        result = get_mailbox_entitlements("mailbox@example.com")

        assert result["max_storage"] == 5000000000
        mock_backend.get_mailbox_entitlements.assert_called_once_with(
            "mailbox@example.com", access_token=None
        )

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_returns_cached_value_on_hit(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_mailbox_entitlements.return_value = {
            "max_storage": 5000000000,
            "storage_used": 1000000000,
        }
        mock_get_backend.return_value = mock_backend

        get_mailbox_entitlements("mailbox@example.com")
        get_mailbox_entitlements("mailbox@example.com")

        assert mock_backend.get_mailbox_entitlements.call_count == 1

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_force_refresh_bypasses_cache(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_mailbox_entitlements.return_value = {
            "max_storage": None,
            "storage_used": None,
        }
        mock_get_backend.return_value = mock_backend

        get_mailbox_entitlements("mailbox@example.com")
        get_mailbox_entitlements("mailbox@example.com", force_refresh=True)

        assert mock_backend.get_mailbox_entitlements.call_count == 2

    @mock.patch("core.entitlements.get_entitlements_backend")
    def test_propagates_backend_error(self, mock_get_backend):
        mock_backend = mock.Mock()
        mock_backend.get_mailbox_entitlements.side_effect = (
            EntitlementsUnavailableError("Backend down")
        )
        mock_get_backend.return_value = mock_backend

        with pytest.raises(EntitlementsUnavailableError):
            get_mailbox_entitlements("mailbox@example.com")
