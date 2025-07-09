"""Tests for the MailDomain permissions system based on get_abilities."""
# pylint: disable=redefined-outer-name,unused-argument

import pytest

from core import models
from core.api.permissions import MailDomainAbilitiesPermission
from core.factories import MailDomainFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def maildomain():
    """Create a test mail domain."""
    return MailDomainFactory()


@pytest.fixture
def permission():
    """Create a MailDomainAbilitiesPermission instance."""
    return MailDomainAbilitiesPermission()


class TestMailDomainAbilitiesPermission:
    """Test the MailDomainAbilitiesPermission class."""

    def test_has_permission_with_maildomain_access(self, user, maildomain, permission):
        """Test permission when user has mail domain access."""
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        request = type("Request", (), {"user": user})()
        view = type("View", (), {"action": "list"})()

        result = permission.has_permission(request, view)
        assert result is True

    def test_has_permission_without_maildomain_access(self, user, permission):
        """Test permission when user has no mail domain access."""
        request = type("Request", (), {"user": user})()
        view = type("View", (), {"action": "list"})()

        result = permission.has_permission(request, view)
        assert result is True

    def test_has_permission_non_list_action(self, user, permission):
        """Test permission for non-list actions."""
        request = type("Request", (), {"user": user})()
        view = type("View", (), {"action": "retrieve"})()

        result = permission.has_permission(request, view)
        assert result is True


class TestMailDomainModelAbilities:
    """Test the get_abilities methods on MailDomain models."""

    def test_maildomain_get_abilities_no_access(self, user, maildomain):
        """Test MailDomain.get_abilities when user has no access."""
        abilities = maildomain.get_abilities(user)

        assert abilities["get"] is False
        assert abilities["patch"] is False
        assert abilities["put"] is False
        assert abilities["post"] is False
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["manage_mailboxes"] is False

    def test_maildomain_get_abilities_admin(self, user, maildomain):
        """Test MailDomain.get_abilities when user has admin access."""
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        abilities = maildomain.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
        assert abilities["manage_accesses"] is True
        assert abilities["manage_mailboxes"] is True

    def test_maildomain_access_get_abilities(self, user, maildomain):
        """Test MailDomainAccess.get_abilities method."""
        access = models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        abilities = access.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
