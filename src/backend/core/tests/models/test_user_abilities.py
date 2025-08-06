"""Tests for User model get_abilities method."""

import pytest

from core import models
from core.factories import MailDomainFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.django_db
class TestUserGetAbilities:
    """Test the get_abilities method on User model."""

    def test_abilities_superuser_staff(self):
        """Test abilities when user is superuser and staff."""
        user = UserFactory(is_superuser=True, is_staff=True)

        abilities = user.get_abilities()

        assert abilities["create_maildomains"] is True
        assert abilities["view_maildomains"] is True

    def test_abilities_superuser_not_staff(self):
        """Test abilities when user is superuser but not staff."""
        user = UserFactory(is_superuser=True, is_staff=False)

        abilities = user.get_abilities()

        assert abilities["create_maildomains"] is True
        assert abilities["view_maildomains"] is True

    def test_abilities_staff_not_superuser(self):
        """Test abilities when user is staff but not superuser."""
        user = UserFactory(is_superuser=False, is_staff=True)

        abilities = user.get_abilities()

        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is False

    def test_abilities_staff_not_superuser_with_maildomain_access(self):
        """Test abilities when user is staff but not superuser and has mail domain access."""
        user = UserFactory(is_superuser=False, is_staff=True)
        maildomain = MailDomainFactory()
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        abilities = user.get_abilities()
        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is True

    def test_abilities_regular_user(self):
        """Test abilities when user is regular user."""
        user = UserFactory(is_superuser=False, is_staff=False)

        abilities = user.get_abilities()

        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is False

    def test_abilities_with_maildomain_access(self):
        """Test abilities when user has mail domain access."""
        user = UserFactory()
        maildomain = MailDomainFactory()

        # Give user access to a mail domain
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        abilities = user.get_abilities()

        assert abilities["view_maildomains"] is True
        assert abilities["create_maildomains"] is False

    def test_abilities_without_maildomain_access(self):
        """Test abilities when user has no mail domain access."""
        user = UserFactory()
        abilities = user.get_abilities()

        assert abilities["view_maildomains"] is False
        assert abilities["create_maildomains"] is False
