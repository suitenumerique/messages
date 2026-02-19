"""Tests for entitlements sync and access check during OIDC login."""

from unittest import mock

import pytest
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation

from core import factories
from core.authentication.backends import OIDCAuthenticationBackend
from core.entitlements import EntitlementsUnavailableError
from core.enums import MailDomainAccessRoleChoices
from core.models import MailDomainAccess

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestOIDCSyncEntitlements:
    """Tests for _sync_entitlements called during OIDC login."""

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_creates_admin_access_for_entitled_domains(self, mock_get):
        user = factories.UserFactory()
        domain1 = factories.MailDomainFactory(name="domain1.com")
        domain2 = factories.MailDomainFactory(name="domain2.com")

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["domain1.com", "domain2.com"],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(
            user=user, maildomain=domain1, role=MailDomainAccessRoleChoices.ADMIN
        ).exists()
        assert MailDomainAccess.objects.filter(
            user=user, maildomain=domain2, role=MailDomainAccessRoleChoices.ADMIN
        ).exists()

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_removes_stale_admin_access(self, mock_get):
        user = factories.UserFactory()
        domain1 = factories.MailDomainFactory(name="domain1.com")
        domain2 = factories.MailDomainFactory(name="domain2.com")

        # User currently has admin access to both domains
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain1, role=MailDomainAccessRoleChoices.ADMIN
        )
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain2, role=MailDomainAccessRoleChoices.ADMIN
        )

        # Entitlements now only include domain1
        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["domain1.com"],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(
            user=user, maildomain=domain1
        ).exists()
        assert not MailDomainAccess.objects.filter(
            user=user, maildomain=domain2
        ).exists()

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_skips_nonexistent_domains(self, mock_get):
        user = factories.UserFactory()

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["nonexistent.com"],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user).count() == 0

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_handles_unavailable_error(self, mock_get):
        """On EntitlementsUnavailableError, sync should be skipped without crash."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.side_effect = EntitlementsUnavailableError("Backend down")

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        # Existing access should NOT be removed
        assert MailDomainAccess.objects.filter(user=user, maildomain=domain).exists()

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_skips_sync_when_field_not_in_response(self, mock_get):
        """If can_admin_maildomains is None (e.g. dummy backend), skip sync entirely."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": None,
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        # Existing access should still be there
        assert MailDomainAccess.objects.filter(user=user, maildomain=domain).exists()

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_empty_list_removes_all_admin_accesses(self, mock_get):
        """An empty list means the user has no admin access to any domain."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user).count() == 0

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_does_not_duplicate_existing_access(self, mock_get):
        """Should not create duplicate MailDomainAccess records."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["domain.com"],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user, maildomain=domain).count() == 1

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_force_refresh_is_used(self, mock_get):
        """Should call get_user_entitlements with force_refresh=True."""
        user = factories.UserFactory()

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        mock_get.assert_called_once_with(
            user.sub, user.email, force_refresh=True
        )


class TestOIDCCheckCanAccess:
    """Tests for _check_can_access called during OIDC login."""

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_allows_access_when_can_access_true(self, mock_get):
        user = factories.UserFactory()
        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        # Should not raise
        backend._check_can_access(user)

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_denies_access_when_can_access_false(self, mock_get):
        user = factories.UserFactory()
        mock_get.return_value = {
            "can_access": False,
            "can_admin_maildomains": [],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        with pytest.raises(SuspiciousOperation, match="Access denied"):
            backend._check_can_access(user)

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_fails_open_when_service_unavailable(self, mock_get):
        """If entitlements service is down, allow login (fail open)."""
        user = factories.UserFactory()
        mock_get.side_effect = EntitlementsUnavailableError("Backend down")

        backend = OIDCAuthenticationBackend()
        # Should not raise - fail open at login
        backend._check_can_access(user)

    @mock.patch("core.entitlements.get_user_entitlements")
    def test_uses_cached_entitlements(self, mock_get):
        """Should call get_user_entitlements without force_refresh (uses cache from sync)."""
        user = factories.UserFactory()
        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
            "operator": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._check_can_access(user)

        mock_get.assert_called_once_with(user.sub, user.email)
