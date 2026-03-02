"""Tests for entitlements sync during OIDC login."""

from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest

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


class TestSyncEntitlements:  # pylint: disable=protected-access
    """Tests for _sync_entitlements called during OIDC login."""

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_creates_admin_access_for_entitled_domains(self, mock_get):
        """Admin access is created for each entitled domain."""
        user = factories.UserFactory()
        domain1 = factories.MailDomainFactory(name="domain1.com")
        domain2 = factories.MailDomainFactory(name="domain2.com")

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["domain1.com", "domain2.com"],
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(
            user=user, maildomain=domain1, role=MailDomainAccessRoleChoices.ADMIN
        ).exists()
        assert MailDomainAccess.objects.filter(
            user=user, maildomain=domain2, role=MailDomainAccessRoleChoices.ADMIN
        ).exists()

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_removes_stale_admin_access(self, mock_get):
        """Admin access for domains no longer entitled is removed."""
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
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user, maildomain=domain1).exists()
        assert not MailDomainAccess.objects.filter(
            user=user, maildomain=domain2
        ).exists()

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_skips_sync_when_can_admin_maildomains_is_none(self, mock_get):
        """If can_admin_maildomains is None (e.g. local backend), skip sync entirely."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": None,
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        # Existing access should still be there
        assert MailDomainAccess.objects.filter(user=user, maildomain=domain).exists()

    @mock.patch("core.authentication.backends.get_user_entitlements")
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
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user).count() == 0

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_handles_unavailable_error(self, mock_get):
        """On EntitlementsUnavailableError, existing accesses are preserved."""
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

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_handles_timeout(self, mock_get):
        """On timeout (also EntitlementsUnavailableError), existing accesses preserved."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.side_effect = EntitlementsUnavailableError("Connection timed out")

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user, maildomain=domain).exists()

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_skips_nonexistent_domains(self, mock_get):
        """Domains not present in the DB are silently skipped."""
        user = factories.UserFactory()

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["nonexistent.com"],
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert MailDomainAccess.objects.filter(user=user).count() == 0

    @mock.patch("core.authentication.backends.get_user_entitlements")
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
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        assert (
            MailDomainAccess.objects.filter(user=user, maildomain=domain).count() == 1
        )

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_force_refresh_is_used(self, mock_get):
        """Should call get_user_entitlements with force_refresh=True."""
        user = factories.UserFactory()

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
        }

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        mock_get.assert_called_once_with(
            user.sub, user.email, user_info=None, force_refresh=True
        )

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_passes_user_info_from_oidc(self, mock_get):
        """Should forward the stored OIDC user_info to get_user_entitlements."""
        user = factories.UserFactory()
        user_info = {"sub": user.sub, "email": user.email, "siret": "12345678901234"}

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": [],
        }

        backend = OIDCAuthenticationBackend()
        backend._user_info = user_info
        backend._sync_entitlements(user)

        mock_get.assert_called_once_with(
            user.sub, user.email, user_info=user_info, force_refresh=True
        )

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_optimistic_path_no_db_writes_when_in_sync(self, mock_get):
        """When entitled domains match existing accesses, no DB writes should occur."""
        user = factories.UserFactory()
        domain = factories.MailDomainFactory(name="domain.com")
        factories.MailDomainAccessFactory(
            user=user, maildomain=domain, role=MailDomainAccessRoleChoices.ADMIN
        )

        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["domain.com"],
        }

        backend = OIDCAuthenticationBackend()

        with CaptureQueriesContext(connection) as ctx:
            backend._sync_entitlements(user)

        # Only SELECT queries (no INSERT, UPDATE, DELETE)
        write_queries = [
            q
            for q in ctx.captured_queries
            if q["sql"].startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert write_queries == []
        assert (
            MailDomainAccess.objects.filter(user=user, maildomain=domain).count() == 1
        )

    @mock.patch("core.authentication.backends.get_user_entitlements")
    def test_login_resets_cache(self, mock_get):
        """Logging in should call with force_refresh=True, resetting cached data."""
        user = factories.UserFactory()

        # Simulate first login with domains
        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["first.com"],
        }
        factories.MailDomainFactory(name="first.com")

        backend = OIDCAuthenticationBackend()
        backend._sync_entitlements(user)

        # Simulate second login with different domains
        mock_get.return_value = {
            "can_access": True,
            "can_admin_maildomains": ["second.com"],
        }
        factories.MailDomainFactory(name="second.com")

        backend._sync_entitlements(user)

        # Both calls should have force_refresh=True
        assert mock_get.call_count == 2
        for call in mock_get.call_args_list:
            assert call.kwargs.get("force_refresh") is True

        # Only second.com should remain
        assert not MailDomainAccess.objects.filter(
            user=user, maildomain__name="first.com"
        ).exists()
        assert MailDomainAccess.objects.filter(
            user=user, maildomain__name="second.com"
        ).exists()
