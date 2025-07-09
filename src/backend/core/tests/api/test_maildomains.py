"""Tests for the MailDomain Admin API endpoints."""
# pylint: disable=redefined-outer-name,unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories, models
from core.api.serializers import MailDomainAccessSerializer
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


class TestMailDomainAdminViewSet:
    """Tests for the MailDomainAdminViewSet."""

    LIST_DOMAINS_URL = reverse("maildomains-list")

    def mailboxes_url(self, maildomain_pk):
        """Generate URL for listing mailboxes in a specific domain."""
        return reverse("domainmailbox-list", kwargs={"maildomain_pk": maildomain_pk})

    def mailbox_detail_url(self, maildomain_pk, mailbox_pk):
        """Generate URL for mailbox detail in a specific domain."""
        return reverse(
            "domainmailbox-detail",
            kwargs={"maildomain_pk": maildomain_pk, "pk": mailbox_pk},
        )

    def test_list_administered_maildomains_success(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        domain_admin_access2,
        mail_domain1,
        mail_domain2,
        unmanaged_domain,
    ):
        """Test that a domain admin can list domains they have admin access to."""
        api_client.force_authenticate(user=domain_admin_user)
        response = api_client.get(self.LIST_DOMAINS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        domain_ids = [item["id"] for item in response.data["results"]]
        assert str(mail_domain1.id) in domain_ids
        assert str(mail_domain2.id) in domain_ids
        assert str(unmanaged_domain.id) not in domain_ids

    def test_list_administered_maildomains_no_admin_access(
        self, api_client, other_user, mail_domain1
    ):
        """Test that users without domain admin access get an empty list."""
        # other_user has no MailDomainAccess records
        api_client.force_authenticate(user=other_user)
        response = api_client.get(self.LIST_DOMAINS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_list_administered_maildomains_unauthenticated(self, api_client):
        """Test that unauthenticated requests to list domains are rejected."""
        response = api_client.get(self.LIST_DOMAINS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_administered_maildomains_superuser_staff(
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

    def test_list_administered_maildomains_superuser_not_staff(
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

    def test_list_administered_maildomains_staff_not_superuser(
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

    def test_list_administered_maildomains_staff_not_superuser_with_access(
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


class TestMailboxAdminViewSet:
    """Tests for the MailboxAdminViewSet."""

    # Fixtures are inherited or can be passed directly to test methods

    # pylint: disable=too-many-arguments
    def test_list_mailboxes_for_domain_success(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
        mailbox1_domain1,
        mailbox2_domain1,
        access_mailbox1_user1,
        access_mailbox1_user2,
        user_for_access1,
        user_for_access2,
    ):
        """Test that a domain admin can list mailboxes in a domain they administer."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        results = response.data["results"]

        # Find data for mailbox1_domain1 for detailed check
        mb1_data = next(
            (item for item in results if item["id"] == str(mailbox1_domain1.pk)), None
        )
        assert mb1_data is not None
        assert mb1_data["local_part"] == mailbox1_domain1.local_part
        assert mb1_data["domain_name"] == mail_domain1.name
        assert len(mb1_data["accesses"]) == 2

        user1_access_data = next(
            (
                acc
                for acc in mb1_data["accesses"]
                if acc["user"]["id"] == str(user_for_access1.pk)
            ),
            None,
        )
        assert user1_access_data is not None
        assert user1_access_data["role"] == MailboxRoleChoices.EDITOR.value
        assert user1_access_data["user"]["email"] == user_for_access1.email

        user2_access_data = next(
            (
                acc
                for acc in mb1_data["accesses"]
                if acc["user"]["id"] == str(user_for_access2.pk)
            ),
            None,
        )
        assert user2_access_data is not None
        assert user2_access_data["role"] == MailboxRoleChoices.VIEWER.value

        # Check that mailbox2_domain1 is also present
        mb2_data = next(
            (item for item in results if item["id"] == str(mailbox2_domain1.pk)), None
        )
        assert mb2_data is not None
        assert (
            len(mb2_data["accesses"]) == 0
        )  # No accesses created for mailbox2 in this test

    def test_list_mailboxes_for_domain_forbidden_not_admin(
        self, api_client, other_user, mail_domain1
    ):
        """Test that users without domain admin access cannot list mailboxes."""
        api_client.force_authenticate(user=other_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_mailboxes_for_domain_unauthenticated(self, api_client, mail_domain1):
        """Test that unauthenticated requests to list mailboxes are rejected."""
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("valid_local_part", ["valid", "valid-pa_rt09.xx"])
    def test_create_mailbox_success(
        self,
        valid_local_part,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that domain admins can create mailboxes in domains they administer."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        data = {"local_part": valid_local_part}
        response = api_client.post(url, data=data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == valid_local_part
        new_mailbox = models.Mailbox.objects.get(id=response.data["id"])
        assert new_mailbox.domain == mail_domain1
        assert new_mailbox.local_part == valid_local_part

    def test_create_mailbox_duplicate_local_part(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
        mailbox1_domain1,
    ):
        """Test that creating a mailbox with a duplicate local_part fails."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        data = {"local_part": mailbox1_domain1.local_part}  # Duplicate
        response = api_client.post(url, data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Model unique_together should enforce this, serializer might catch it too.

    @pytest.mark.parametrize(
        "invalid_local_part",
        ["invalid@example.com", "invalid part", "invalidé", "", " "],
    )
    def test_create_mailbox_invalid_local_part(
        self,
        invalid_local_part,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that creating a mailbox with an invalid local_part fails."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        data = {"local_part": invalid_local_part}
        response = api_client.post(url, data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "local_part" in response.data

    # --- EXCLUDE ABILITIES Tests ---
    def test_mailbox_admin_list_excludes_abilities_from_nested_users(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
        mailbox1_domain1,
        mailbox2_domain1,
        access_mailbox1_user1,
        access_mailbox1_user2,
        user_for_access1,
        user_for_access2,
    ):
        """Test that mailbox admin list endpoint excludes abilities from nested users."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert len(response.data["results"]) == 2

        # Find mailbox1_domain1 data for detailed check
        mb1_data = next(
            (
                item
                for item in response.data["results"]
                if item["id"] == str(mailbox1_domain1.pk)
            ),
            None,
        )
        assert mb1_data is not None
        assert "accesses" in mb1_data
        assert len(mb1_data["accesses"]) == 2

        # Verify that all nested users do NOT contain abilities
        for access_data in mb1_data["accesses"]:
            assert "user" in access_data
            user_data = access_data["user"]
            assert "abilities" not in user_data
            assert "id" in user_data
            assert "email" in user_data
            assert "full_name" in user_data
            assert "short_name" in user_data

        # Also check mailbox2_domain1 (should have 0 accesses)
        mb2_data = next(
            (
                item
                for item in response.data["results"]
                if item["id"] == str(mailbox2_domain1.pk)
            ),
            None,
        )
        assert mb2_data is not None
        assert "accesses" in mb2_data
        assert len(mb2_data["accesses"]) == 0

    def test_mailbox_admin_retrieve_excludes_abilities_from_nested_users(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
        mailbox1_domain1,
        access_mailbox1_user1,
        access_mailbox1_user2,
        user_for_access1,
        user_for_access2,
    ):
        """Test that mailbox admin retrieve endpoint excludes abilities from nested users."""
        api_client.force_authenticate(user=domain_admin_user)
        url = TestMailDomainAdminViewSet().mailbox_detail_url(
            mail_domain1.pk, mailbox1_domain1.pk
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "accesses" in response.data
        assert len(response.data["accesses"]) == 2

        # Verify that all nested users do NOT contain abilities
        for access_data in response.data["accesses"]:
            assert "user" in access_data
            user_data = access_data["user"]
            assert "abilities" not in user_data
            assert "id" in user_data
            assert "email" in user_data
            assert "full_name" in user_data
            assert "short_name" in user_data

    def test_mailbox_admin_excludes_abilities_with_superuser(
        self,
        api_client,
        mail_domain1,
        mailbox1_domain1,
        mailbox2_domain1,
        access_mailbox1_user1,
        user_for_access1,
    ):
        """Test that mailbox admin excludes abilities even when accessed by superuser."""
        # Create a superuser and give them access to the maildomain
        superuser = factories.UserFactory(is_superuser=True, is_staff=True)

        # Give superuser access to the maildomain
        models.MailDomainAccess.objects.create(
            maildomain=mail_domain1,
            user=superuser,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=superuser)

        url = TestMailDomainAdminViewSet().mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert len(response.data["results"]) == 2  # Both mailboxes in the domain

        # Find mailbox1_domain1 data for detailed check
        mb1_data = next(
            (
                item
                for item in response.data["results"]
                if item["id"] == str(mailbox1_domain1.pk)
            ),
            None,
        )
        assert mb1_data is not None
        assert "accesses" in mb1_data
        assert len(mb1_data["accesses"]) == 1

        # Verify that nested users do NOT contain abilities, even for superuser
        access_data = mb1_data["accesses"][0]
        assert "user" in access_data
        user_data = access_data["user"]
        assert "abilities" not in user_data


@pytest.fixture
def user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture
def maildomain():
    """Create a test mail domain."""
    return factories.MailDomainFactory()


class TestMailDomainAbilitiesAPI:
    """Test the abilities field in MailDomain API responses."""

    def test_maildomain_abilities_in_response(self, api_client, user, maildomain):
        """Test that abilities are included in mail domain API response."""
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=user)
        url = reverse("maildomains-detail", args=[maildomain.id])
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

    def test_maildomain_access_abilities_in_response(self, user, maildomain):
        """Test that abilities are included in mail domain access API response."""
        access = models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        # Note: This would require a specific endpoint for mail domain access
        # For now, we just test that the serializer includes abilities
        serializer = MailDomainAccessSerializer(
            access, context={"request": type("Request", (), {"user": user})()}
        )
        data = serializer.data

        assert "abilities" in data
        abilities = data["abilities"]
        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True

    def test_maildomain_list_with_abilities(self, api_client, user, maildomain):
        """Test that mail domain list includes abilities for each domain."""
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=user)
        url = reverse("maildomains-list")
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

    def test_maildomain_detail_no_access_abilities(self, api_client, user, maildomain):
        """Test that abilities are correctly set when user has no access to detail."""
        api_client.force_authenticate(user=user)
        url = reverse("maildomains-detail", args=[maildomain.id])
        response = api_client.get(url)

        # Should return 404 since user has no access to this domain
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_maildomain_list_no_access_abilities(self, api_client, user, maildomain):
        """Test that abilities are correctly set when user has no access."""
        api_client.force_authenticate(user=user)
        url = reverse("maildomains-list")
        response = api_client.get(url)

        # User has no access, so should get empty list
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0
