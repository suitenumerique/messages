"""Tests for the DNS check action in AdminMailDomainViewSet."""
# pylint: disable=redefined-outer-name, unused-argument

from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories
from core.enums import MailDomainAccessRoleChoices

pytestmark = pytest.mark.django_db


@pytest.fixture(name="domain_admin_user")
def fixture_domain_admin_user():
    """Create a user for domain administration testing."""
    return factories.UserFactory()


@pytest.fixture(name="other_user")
def fixture_other_user():
    """Create another user without admin privileges."""
    return factories.UserFactory()


@pytest.fixture(name="mail_domain")
def fixture_mail_domain():
    """Create a mail domain for testing."""
    return factories.MailDomainFactory(name="test-domain.com")


@pytest.fixture(name="unmanaged_domain")
def fixture_unmanaged_domain():
    """Create a mail domain that has no admin access set up."""
    return factories.MailDomainFactory(name="unmanaged-domain.com")


@pytest.fixture(name="domain_admin_access")
def fixture_domain_admin_access(domain_admin_user, mail_domain):
    """Create admin access for domain_admin_user to mail_domain."""
    return factories.MailDomainAccessFactory(
        user=domain_admin_user,
        maildomain=mail_domain,
        role=MailDomainAccessRoleChoices.ADMIN,
    )


class TestAdminMailDomainDNSCheck:
    """Tests for the DNS check action in AdminMailDomainViewSet."""

    def test_check_dns_success(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access,
        mail_domain,
    ):
        """Test successful DNS check for a domain."""
        api_client.force_authenticate(user=domain_admin_user)

        # Mock the DNS check to return expected results
        mock_check_results = [
            {
                "type": "MX",
                "target": "@",
                "value": "10 mail.test-domain.com",
                "_check": {"status": "correct", "found": ["10 mail.test-domain.com"]},
            },
            {
                "type": "TXT",
                "target": "@",
                "value": "v=spf1 include:_spf.test-domain.com ~all",
                "_check": {
                    "status": "incorrect",
                    "found": ["v=spf1 include:_spf.other-domain.com ~all"],
                },
            },
            {
                "type": "CNAME",
                "target": "mail",
                "value": "mail.test-domain.com",
                "_check": {"status": "missing", "error": "No records found"},
            },
        ]

        # Mock the check_dns_records function that's imported in the viewset
        with patch(
            "core.api.viewsets.maildomain.check_dns_records",
            return_value=mock_check_results,
        ):
            url = reverse(
                "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
            )
            response = api_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["domain"] == mail_domain.name
        assert len(response.data["records"]) == 3

        # Check first record (correct)
        record1 = response.data["records"][0]
        assert record1["type"] == "MX"
        assert record1["target"] == "@"
        assert record1["value"] == "10 mail.test-domain.com"
        assert record1["_check"]["status"] == "correct"
        assert record1["_check"]["found"] == ["10 mail.test-domain.com"]

        # Check second record (incorrect)
        record2 = response.data["records"][1]
        assert record2["type"] == "TXT"
        assert record2["target"] == "@"
        assert record2["value"] == "v=spf1 include:_spf.test-domain.com ~all"
        assert record2["_check"]["status"] == "incorrect"
        assert record2["_check"]["found"] == [
            "v=spf1 include:_spf.other-domain.com ~all"
        ]

        # Check third record (missing)
        record3 = response.data["records"][2]
        assert record3["type"] == "CNAME"
        assert record3["target"] == "mail"
        assert record3["value"] == "mail.test-domain.com"
        assert record3["_check"]["status"] == "missing"
        assert record3["_check"]["error"] == "No records found"

    def test_check_dns_no_admin_access(
        self,
        api_client,
        other_user,
        mail_domain,
    ):
        """Test that users without domain admin access cannot check DNS."""
        api_client.force_authenticate(user=other_user)
        url = reverse(
            "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
        )
        response = api_client.post(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_check_dns_unauthenticated(
        self,
        api_client,
        mail_domain,
    ):
        """Test that unauthenticated requests are rejected."""
        url = reverse(
            "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
        )
        response = api_client.post(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_check_dns_domain_not_found(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access,
    ):
        """Test DNS check for non-existent domain."""
        api_client.force_authenticate(user=domain_admin_user)

        # Use a non-existent domain ID
        non_existent_id = "00000000-0000-0000-0000-000000000000"
        url = reverse(
            "admin-maildomains-check-dns", kwargs={"maildomain_pk": non_existent_id}
        )
        response = api_client.post(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_check_dns_dns_error(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access,
        mail_domain,
    ):
        """Test DNS check when DNS query fails."""
        api_client.force_authenticate(user=domain_admin_user)

        # Mock check_dns_records to raise an exception
        with patch(
            "core.api.viewsets.maildomain.check_dns_records",
            side_effect=Exception("DNS error"),
        ):
            url = reverse(
                "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
            )
            # The exception should be raised and not handled by the viewset
            with pytest.raises(Exception, match="DNS error"):
                api_client.post(url)

    def test_check_dns_superuser_not_staff(
        self,
        api_client,
        mail_domain,
    ):
        """Test that superuser without staff status can check DNS."""
        superuser_not_staff = factories.UserFactory(is_superuser=True, is_staff=False)
        api_client.force_authenticate(user=superuser_not_staff)

        mock_check_results = [
            {
                "type": "MX",
                "target": "@",
                "value": "10 mail.test-domain.com",
                "_check": {"status": "correct", "found": ["10 mail.test-domain.com"]},
            }
        ]

        with patch(
            "core.api.viewsets.maildomain.check_dns_records",
            return_value=mock_check_results,
        ):
            url = reverse(
                "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
            )
            response = api_client.post(url)

        # Superusers (regardless of staff status) should have access
        assert response.status_code == status.HTTP_200_OK
        assert response.data["domain"] == mail_domain.name

    def test_check_dns_staff_not_superuser(
        self,
        api_client,
        mail_domain,
    ):
        """Test that staff without superuser status cannot check DNS."""
        staff_not_superuser = factories.UserFactory(is_superuser=False, is_staff=True)
        api_client.force_authenticate(user=staff_not_superuser)

        url = reverse(
            "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
        )
        response = api_client.post(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_check_dns_empty_records(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access,
        mail_domain,
    ):
        """Test DNS check when domain has no expected DNS records."""
        api_client.force_authenticate(user=domain_admin_user)

        # Mock DNS check to return empty results
        with patch("core.api.viewsets.maildomain.check_dns_records", return_value=[]):
            url = reverse(
                "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
            )
            response = api_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["domain"] == mail_domain.name
        assert response.data["records"] == []

    def test_check_dns_error_records(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access,
        mail_domain,
    ):
        """Test DNS check with error status records."""
        api_client.force_authenticate(user=domain_admin_user)

        mock_check_results = [
            {
                "type": "MX",
                "target": "@",
                "value": "10 mail.test-domain.com",
                "_check": {"status": "error", "error": "DNS query timeout"},
            }
        ]

        with patch(
            "core.api.viewsets.maildomain.check_dns_records",
            return_value=mock_check_results,
        ):
            url = reverse(
                "admin-maildomains-check-dns", kwargs={"maildomain_pk": mail_domain.id}
            )
            response = api_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        record = response.data["records"][0]
        assert record["_check"]["status"] == "error"
        assert record["_check"]["error"] == "DNS query timeout"
