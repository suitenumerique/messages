"""Tests for the MailDomain Admin API endpoints."""
# pylint: disable=redefined-outer-name, unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories, models

pytestmark = pytest.mark.django_db


@pytest.fixture(name="domain_superuser_user")
def fixture_domain_superuser_user():
    """Create a user for domain superuser testing."""
    return factories.UserFactory(is_superuser=True)


@pytest.fixture(name="domain_admin_user")
def fixture_domain_admin_user():
    """Create a user for domain administration testing."""
    return factories.UserFactory()


@pytest.fixture(name="other_user")
def fixture_other_user():
    """Create another user without admin privileges."""
    return factories.UserFactory()


class TestAdminMailDomainsCreate:
    """Tests for the MailDomain Admin API create endpoint."""

    CREATE_DOMAIN_URL = reverse("admin-maildomains-list")

    def test_create_mail_domain_as_superuser(self, api_client, domain_superuser_user):
        """Test creating a mail domain as a superuser."""
        api_client.force_authenticate(user=domain_superuser_user)
        url = self.CREATE_DOMAIN_URL
        data = {"name": "super-user-domain.com"}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert models.MailDomain.objects.filter(name="super-user-domain.com").exists()
        payload = response.json()
        assert payload["name"] == "super-user-domain.com"
        assert "id" in payload
        assert payload["oidc_autojoin"] is False
        assert payload["identity_sync"] is False

    def test_create_mail_domain_as_admin(self, api_client, domain_admin_user):
        """Test creating a mail domain as an admin user."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.CREATE_DOMAIN_URL
        data = {"name": "unauthorized-admin-domain.com"}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not models.MailDomain.objects.filter(name="unauthorized-admin-domain.com").exists()

    def test_create_mail_domain_as_non_admin(self, api_client, other_user):
        """Test creating a mail domain as a non-admin user."""
        api_client.force_authenticate(user=other_user)
        url = self.CREATE_DOMAIN_URL
        data = {"name": "unauthorized-user-domain.com"}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not models.MailDomain.objects.filter(name="unauthorized-user-domain.com").exists()

    def test_create_mail_domain_invalid_name(self, api_client, domain_superuser_user):
        """Test creating a mail domain with an invalid name."""
        api_client.force_authenticate(user=domain_superuser_user)
        # Uppercase and trailing dash should be rejected by model validator
        data = {"name": "Bad-Domain-.COM"}
        response = api_client.post(self.CREATE_DOMAIN_URL, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not models.MailDomain.objects.filter(name__iexact="bad-domain-.com").exists()

    def test_create_mail_domain_duplicate_name(self, api_client, domain_superuser_user):
        """Test creating a mail domain with a duplicate name."""
        api_client.force_authenticate(user=domain_superuser_user)
        models.MailDomain.objects.create(name="dup.com")
        response = api_client.post(self.CREATE_DOMAIN_URL, {"name": "dup.com"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
