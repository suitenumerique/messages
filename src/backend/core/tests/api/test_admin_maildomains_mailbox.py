"""Test suite of AdminMailDomainMailboxViewSet."""
# pylint: disable=unused-argument

from unittest.mock import patch

from django.test import override_settings
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


class TestAdminMailDomainMailboxViewSet:
    """Tests for the AdminMailDomainMailboxViewSet."""

    # Fixtures are inherited or can be passed directly to test methods
    def mailboxes_url(self, maildomain_pk):
        """Generate URL for listing mailboxes in a specific domain."""
        return reverse(
            "admin-maildomains-mailbox-list", kwargs={"maildomain_pk": maildomain_pk}
        )

    def mailbox_detail_url(self, maildomain_pk, mailbox_pk):
        """Generate URL for mailbox detail in a specific domain."""
        return reverse(
            "admin-maildomains-mailbox-detail",
            kwargs={"maildomain_pk": maildomain_pk, "pk": mailbox_pk},
        )

    # pylint: disable=too-many-arguments
    def test_admin_maildomains_mailbox_list_for_domain_success(
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
        url = self.mailboxes_url(mail_domain1.pk)
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
        assert user1_access_data["role"] == "editor"
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
        assert user2_access_data["role"] == "viewer"

        # Check that mailbox2_domain1 is also present
        mb2_data = next(
            (item for item in results if item["id"] == str(mailbox2_domain1.pk)), None
        )
        assert mb2_data is not None
        assert (
            len(mb2_data["accesses"]) == 0
        )  # No accesses created for mailbox2 in this test

    def test_admin_maildomains_mailbox_list_for_domain_forbidden_not_admin(
        self, api_client, other_user, mail_domain1
    ):
        """Test that users without domain admin access cannot list mailboxes."""
        api_client.force_authenticate(user=other_user)
        url = self.mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_maildomains_mailbox_list_for_domain_unauthenticated(
        self, api_client, mail_domain1
    ):
        """Test that unauthenticated requests to list mailboxes are rejected."""
        url = self.mailboxes_url(mail_domain1.pk)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("valid_local_part", ["valid", "valid-pa_rt09.xx"])
    def test_admin_maildomains_mailbox_create_success(
        self,
        valid_local_part,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that domain admins can create mailboxes in domains they administer."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)
        data = {"local_part": valid_local_part}
        response = api_client.post(url, data=data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == valid_local_part
        new_mailbox = models.Mailbox.objects.get(id=response.data["id"])
        assert new_mailbox.domain == mail_domain1
        assert new_mailbox.local_part == valid_local_part

    def test_admin_maildomains_mailbox_create_duplicate_local_part(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
        mailbox1_domain1,
    ):
        """Test that creating a mailbox with a duplicate local_part fails."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)
        data = {"local_part": mailbox1_domain1.local_part}  # Duplicate
        response = api_client.post(url, data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Model unique_together should enforce this, serializer might catch it too.

    @pytest.mark.parametrize(
        "invalid_local_part",
        ["invalid@example.com", "invalid part", "invalidé", "", " "],
    )
    def test_admin_maildomains_mailbox_create_invalid_local_part(
        self,
        invalid_local_part,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that creating a mailbox with an invalid local_part fails."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)
        data = {"local_part": invalid_local_part}
        response = api_client.post(url, data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "local_part" in response.data

    @patch("core.api.viewsets.maildomain.reset_keycloak_user_password")
    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_admin_maildomains_mailbox_create_personal_with_keycloak_password_reset(
        self,
        mock_reset_password,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that personal mailbox creation triggers Keycloak password reset when IDENTITY_PROVIDER is keycloak."""
        mock_reset_password.return_value = "temporary-password-123"

        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)

        data = {
            "local_part": "testuser",
            "metadata": {"type": "personal", "first_name": "Test", "last_name": "User"},
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == "testuser"
        assert response.data["one_time_password"] == "temporary-password-123"

        # Verify Keycloak password reset was called with correct email
        mock_reset_password.assert_called_once_with("testuser@admin-domain1.com")

    @patch("core.api.viewsets.maildomain.reset_keycloak_user_password")
    @override_settings(IDENTITY_PROVIDER="other_provider")
    def test_admin_maildomains_mailbox_create_personal_without_keycloak_identity_provider(
        self,
        mock_reset_password,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that personal mailbox creation doesn't trigger password reset when IDENTITY_PROVIDER is not keycloak."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)

        data = {
            "local_part": "testuser",
            "metadata": {"type": "personal", "first_name": "Test", "last_name": "User"},
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == "testuser"
        assert "one_time_password" not in response.data

        # Verify Keycloak password reset was not called
        mock_reset_password.assert_not_called()

    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_admin_maildomains_mailbox_create_non_personal_no_keycloak_integration(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that non-personal mailbox creation doesn't involve Keycloak."""
        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)

        data = {"local_part": "sharedmailbox", "metadata": {"type": "shared"}}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["local_part"] == "sharedmailbox"
        assert "one_time_password" not in response.data

    @patch("core.api.viewsets.maildomain.reset_keycloak_user_password")
    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_admin_maildomains_mailbox_create_personal_user_creation_and_access(
        self,
        mock_reset_password,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that personal mailbox creation creates user and mailbox access correctly."""
        mock_reset_password.return_value = "temporary-password-123"

        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)

        data = {
            "local_part": "newuser",
            "metadata": {"type": "personal", "first_name": "John", "last_name": "Doe"},
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        # Verify user was created with correct details
        user = models.User.objects.get(email="newuser@admin-domain1.com")
        assert user.full_name == "John Doe"
        assert user.password == "?"

        # Verify mailbox access was created
        mailbox = models.Mailbox.objects.get(local_part="newuser", domain=mail_domain1)
        mailbox_access = models.MailboxAccess.objects.get(mailbox=mailbox, user=user)
        assert mailbox_access.role == MailboxRoleChoices.ADMIN

    def test_admin_maildomains_mailbox_create_personal_user_already_exists_no_update(
        self,
        api_client,
        domain_admin_user,
        domain_admin_access1,
        mail_domain1,
    ):
        """Test that personal mailbox creation doesn't update existing user details."""
        # Create user first with different details
        factories.UserFactory(
            email="existinguser@admin-domain1.com",
            full_name="Existing User",
            password="existing-password",
        )

        api_client.force_authenticate(user=domain_admin_user)
        url = self.mailboxes_url(mail_domain1.pk)

        data = {
            "local_part": "existinguser",
            "metadata": {"type": "personal", "first_name": "New", "last_name": "Name"},
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        # Verify user details were not updated
        user = models.User.objects.get(email="existinguser@admin-domain1.com")
        assert user.full_name == "Existing User"  # Should not be updated
        assert user.password == "existing-password"  # Should not be updated

        # Verify mailbox access was created
        mailbox = models.Mailbox.objects.get(
            local_part="existinguser", domain=mail_domain1
        )
        mailbox_access = models.MailboxAccess.objects.get(mailbox=mailbox, user=user)
        assert mailbox_access.role == MailboxRoleChoices.ADMIN

    # --- EXCLUDE ABILITIES Tests ---
    def test_admin_maildomains_mailbox_list_excludes_abilities_from_nested_users(
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
        url = self.mailboxes_url(mail_domain1.pk)
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

    def test_admin_maildomains_mailbox_retrieve_excludes_abilities_from_nested_users(
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
        url = self.mailbox_detail_url(mail_domain1.pk, mailbox1_domain1.pk)
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

    def test_admin_maildomains_mailbox_excludes_abilities_with_superuser(
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

        url = self.mailboxes_url(mail_domain1.pk)
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
