"""Tests for the MailDomain Admin API endpoints."""
# pylint: disable=redefined-outer-name, unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories, models
from core.enums import MailboxRoleChoices, MailDomainAccessRoleChoices

pytestmark = pytest.mark.django_db


@pytest.fixture(name="domain_admin_user")
def fixture_domain_admin_user():
    """Create a user for domain administration testing."""
    return factories.UserFactory()


@pytest.fixture(name="other_user")
def fixture_other_user():
    """Create another user without admin privileges."""
    return factories.UserFactory()


@pytest.fixture(name="mail_domain1")
def fixture_mail_domain1():
    """Create the first mail domain for testing."""
    return factories.MailDomainFactory(name="admin-domain1.com")


@pytest.fixture(name="mail_domain2")
def fixture_mail_domain2():
    """Create the second mail domain for testing."""
    return factories.MailDomainFactory(name="admin-domain2.com")


@pytest.fixture(name="unmanaged_domain")
def fixture_unmanaged_domain():
    """Create a mail domain that has no admin access set up."""
    return factories.MailDomainFactory(name="unmanaged-domain.com")


@pytest.fixture(name="domain_admin_access1")
def fixture_domain_admin_access1(domain_admin_user, mail_domain1):
    """Create admin access for domain_admin_user to mail_domain1."""
    return factories.MailDomainAccessFactory(
        user=domain_admin_user,
        maildomain=mail_domain1,
        role=MailDomainAccessRoleChoices.ADMIN,
    )


@pytest.fixture(name="domain_admin_access2")
def fixture_domain_admin_access2(domain_admin_user, mail_domain2):
    """Create admin access for domain_admin_user to mail_domain2."""
    return factories.MailDomainAccessFactory(
        user=domain_admin_user,
        maildomain=mail_domain2,
        role=MailDomainAccessRoleChoices.ADMIN,
    )


@pytest.fixture(name="mailbox1_domain1")
def fixture_mailbox1_domain1(mail_domain1):
    """Create the first mailbox in mail_domain1."""
    return factories.MailboxFactory(domain=mail_domain1, local_part="box1")


@pytest.fixture(name="mailbox2_domain1")
def fixture_mailbox2_domain1(mail_domain1):
    """Create the second mailbox in mail_domain1."""
    return factories.MailboxFactory(domain=mail_domain1, local_part="box2")


@pytest.fixture(name="mailbox1_domain2")
def fixture_mailbox1_domain2(mail_domain2):
    """Create a mailbox in mail_domain2."""
    return factories.MailboxFactory(domain=mail_domain2, local_part="boxA")


@pytest.fixture(name="user_for_access1")
def fixture_user_for_access1():
    """Create a user for mailbox access testing."""
    return factories.UserFactory(email="access.user1@example.com")


@pytest.fixture(name="user_for_access2")
def fixture_user_for_access2():
    """Create another user for mailbox access testing."""
    return factories.UserFactory(email="access.user2@example.com")


@pytest.fixture(name="access_mailbox1_user1")
def fixture_access_mailbox1_user1(mailbox1_domain1, user_for_access1):
    """Create EDITOR access for user_for_access1 to mailbox1_domain1."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox1_domain1, user=user_for_access1, role=MailboxRoleChoices.EDITOR
    )


@pytest.fixture(name="access_mailbox1_user2")
def fixture_access_mailbox1_user2(mailbox1_domain1, user_for_access2):
    """Create VIEWER access for user_for_access2 to mailbox1_domain1."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox1_domain1, user=user_for_access2, role=MailboxRoleChoices.VIEWER
    )


class TestAdminMailDomainViewSet:
    """Tests for the AdminMailDomainViewSet."""

    LIST_DOMAINS_URL = reverse("admin-maildomains-list")

    def test_admin_maildomains_list_administered_maildomains_success(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        domain_admin_access2,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
        django_assert_num_queries,
    ):
        """Test that a domain admin can list domains they have admin access to."""
        api_client.force_authenticate(user=domain_admin_user)
        with django_assert_num_queries(2):  # 1 for list + 1 for pagination
            response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        domain_ids = [item["id"] for item in response.data["results"]]
        assert str(mail_domain1.id) in domain_ids
        assert str(mail_domain2.id) in domain_ids
        assert str(unmanaged_domain.id) not in domain_ids

    def test_admin_maildomains_list_administered_maildomains_no_admin_access(
        self, api_client, other_user, mail_domain1
    ):
        """Test that users without domain admin access get an empty list."""
        # other_user has no MailDomainAccess records
        api_client.force_authenticate(user=other_user)
        response = api_client.get(self.LIST_DOMAINS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_admin_maildomains_list_administered_maildomains_unauthenticated(
        self, api_client
    ):
        """Test that unauthenticated requests to list domains are rejected."""
        response = api_client.get(self.LIST_DOMAINS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_admin_maildomains_list_administered_maildomains_superuser_staff(
        self,
        api_client,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
    ):
        """Test that superuser with staff status can list all domains."""
        superuser_staff = factories.UserFactory(is_superuser=True, is_staff=True)
        api_client.force_authenticate(user=superuser_staff)
        response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        domain_ids = [item["id"] for item in response.data["results"]]
        assert str(mail_domain1.id) in domain_ids
        assert str(mail_domain2.id) in domain_ids
        assert str(unmanaged_domain.id) in domain_ids

    def test_admin_maildomains_listadministered_maildomains_superuser_not_staff(
        self,
        api_client,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
    ):
        """Test that superuser without staff status cannot list all domains."""
        superuser_not_staff = factories.UserFactory(is_superuser=True, is_staff=False)
        api_client.force_authenticate(user=superuser_not_staff)
        response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_admin_maildomains_listadministered_maildomains_staff_not_superuser(
        self,
        api_client,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
    ):
        """Test that staff without superuser status cannot list all domains."""
        staff_not_superuser = factories.UserFactory(is_superuser=False, is_staff=True)
        api_client.force_authenticate(user=staff_not_superuser)
        response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_admin_maildomains_listadministered_maildomains_staff_not_superuser_with_access(
        self,
        api_client,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
    ):
        """Test that staff without superuser status can only see domains they have access to."""
        staff_not_superuser = factories.UserFactory(is_superuser=False, is_staff=True)

        # Give access to only one domain
        models.MailDomainAccess.objects.create(
            maildomain=mail_domain1,
            user=staff_not_superuser,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=staff_not_superuser)
        response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        domain_ids = [item["id"] for item in response.data["results"]]
        assert str(mail_domain1.id) in domain_ids
        assert str(mail_domain2.id) not in domain_ids
        assert str(unmanaged_domain.id) not in domain_ids

    def test_list_administered_maildomains_query_optimization(
        self,
        api_client,
        domain_admin_user,
        django_assert_num_queries,
    ):
        """Test that the query optimization works with multiple maildomains."""
        # Create several maildomains with access
        maildomains = []
        for i in range(5):
            maildomain = factories.MailDomainFactory(name=f"domain{i}.com")
            models.MailDomainAccess.objects.create(
                maildomain=maildomain,
                user=domain_admin_user,
                role=models.MailDomainAccessRoleChoices.ADMIN,
            )
            maildomains.append(maildomain)

        # Create some maildomains without access
        for i in range(3):
            factories.MailDomainFactory(name=f"noaccess{i}.com")

        api_client.force_authenticate(user=domain_admin_user)

        with django_assert_num_queries(2):  # 1 for list + 1 for pagination
            response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 5

        # Verify that all maildomains with access are present
        domain_ids = [item["id"] for item in response.data["results"]]
        for maildomain in maildomains:
            assert str(maildomain.id) in domain_ids

    def test_list_administered_maildomains_superuser_query_optimization(
        self,
        api_client,
        django_assert_num_queries,
    ):
        """Test that superuser query is also optimized."""
        # Create several maildomains
        maildomains = []
        for i in range(10):
            maildomain = factories.MailDomainFactory(name=f"domain{i}.com")
            maildomains.append(maildomain)

        superuser = factories.UserFactory(is_superuser=True, is_staff=True)
        api_client.force_authenticate(user=superuser)
        with django_assert_num_queries(
            3
        ):  # 1 for list + 1 for pagination + 1 for abilities
            response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 10

    def test_maildomain_expected_dns_records_in_response(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that expected_dns_records field is only included in detail views, not list views."""
        api_client.force_authenticate(user=domain_admin_user)

        # Test list endpoint - should NOT include DNS records
        response = api_client.get(self.LIST_DOMAINS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

        domain_data = response.data["results"][0]
        assert "expected_dns_records" in domain_data
        assert (
            domain_data["expected_dns_records"] is None
        )  # Should be None in list view

        # Test detail endpoint - should include DNS records
        detail_response = api_client.get(f"{self.LIST_DOMAINS_URL}{mail_domain1.id}/")
        assert detail_response.status_code == status.HTTP_200_OK
        assert "expected_dns_records" in detail_response.data

        dns_records = detail_response.data["expected_dns_records"]
        assert dns_records is not None  # Should have DNS records in detail view

        # Verify DNS records structure
        assert isinstance(dns_records, list)
        assert (
            len(dns_records) >= 4
        )  # At least MX (2), SPF, DMARC, and potentially DKIM

        # Verify MX records (should have 2)
        mx_records = [record for record in dns_records if record["type"] == "mx"]
        assert len(mx_records) == 2
        assert any("mx1." in record["value"] for record in mx_records)
        assert any("mx2." in record["value"] for record in mx_records)

        # Verify SPF record
        spf_records = [
            record
            for record in dns_records
            if record["type"] == "txt" and "spf1" in record["value"]
        ]
        assert len(spf_records) == 1
        assert spf_records[0]["target"] == ""
        assert "v=spf1" in spf_records[0]["value"]

        # Verify DMARC record
        dmarc_records = [
            record
            for record in dns_records
            if record["type"] == "txt" and "DMARC1" in record["value"]
        ]
        assert len(dmarc_records) == 1
        assert dmarc_records[0]["target"] == "_dmarc"
        assert "v=DMARC1" in dmarc_records[0]["value"]

        # Verify DKIM record (should be present since we auto-generate DKIM keys)
        dkim_records = [
            record
            for record in dns_records
            if record["type"] == "txt" and "DKIM1" in record["value"]
        ]
        assert len(dkim_records) == 1
        assert dkim_records[0]["target"].endswith("._domainkey")
        assert "v=DKIM1" in dkim_records[0]["value"]

        # Test detail endpoint
        detail_response = api_client.get(f"{self.LIST_DOMAINS_URL}{mail_domain1.id}/")
        assert detail_response.status_code == status.HTTP_200_OK
        assert "expected_dns_records" in detail_response.data
        assert detail_response.data["expected_dns_records"] == dns_records


class TestMailDomainAbilitiesAPI:
    """Test the abilities field in MailDomain API responses."""

    def test_maildomain_abilities_in_response(
        self, api_client, domain_admin_user, domain_admin_access1
    ):
        """Test that abilities are included in mail domain API response."""
        api_client.force_authenticate(user=domain_admin_user)
        url = reverse(
            "admin-maildomains-detail", args=[domain_admin_access1.maildomain.id]
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "abilities" in response.data
        abilities = response.data["abilities"]
        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
        assert abilities["manage_accesses"] is True
        assert abilities["manage_mailboxes"] is True

    def test_maildomain_list_with_abilities(
        self, api_client, domain_admin_user, domain_admin_access1
    ):
        """Test that mail domain list includes abilities for each domain."""
        api_client.force_authenticate(user=domain_admin_user)
        url = reverse("admin-maildomains-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        domain_data = response.data["results"][0]
        assert "abilities" in domain_data
        abilities = domain_data["abilities"]
        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
        assert abilities["manage_accesses"] is True
        assert abilities["manage_mailboxes"] is True

    def test_maildomain_detail_no_access_abilities(
        self, api_client, other_user, mail_domain1
    ):
        """Test that abilities are correctly set when user has no access to detail."""
        api_client.force_authenticate(user=other_user)
        url = reverse("admin-maildomains-detail", args=[mail_domain1.id])
        response = api_client.get(url)

        # Should return 404 since user has no access to this domain
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_maildomain_list_no_access_abilities(
        self, api_client, other_user, mail_domain1, mail_domain2
    ):
        """Test that abilities are correctly set when user has no access."""
        api_client.force_authenticate(user=other_user)
        url = reverse("admin-maildomains-list")
        response = api_client.get(url)

        # User has no access, so should get empty list
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0
