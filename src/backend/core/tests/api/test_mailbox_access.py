"""Tests for the MailboxAccessViewSet API endpoint (nested under mailboxes)."""
# pylint: disable=unused-argument

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories, models
from core.enums import MailboxRoleChoices, MailDomainAccessRoleChoices

pytestmark = pytest.mark.django_db


# --- Users ---
@pytest.fixture(name="domain_admin_user")
def fixture_domain_admin_user(mail_domain1):
    """User with ADMIN access to mail_domain1."""
    user = factories.UserFactory(email="domain.admin@example.com")
    factories.MailDomainAccessFactory(
        user=user, maildomain=mail_domain1, role=MailDomainAccessRoleChoices.ADMIN
    )
    return user


@pytest.fixture(name="mailbox1_admin_user")
def fixture_mailbox1_admin_user(mailbox1_domain1):
    """User with ADMIN access to mailbox1_domain1, but not domain admin."""
    user = factories.UserFactory(email="mailbox1.admin@example.com")
    factories.MailboxAccessFactory(
        user=user, mailbox=mailbox1_domain1, role=MailboxRoleChoices.ADMIN
    )
    return user


@pytest.fixture(name="regular_user")
def fixture_regular_user():
    """User with no specific admin rights relevant to these tests."""
    return factories.UserFactory(email="regular@example.com")


# --- Domains & Mailboxes ---
@pytest.fixture(name="mail_domain1")
def fixture_mail_domain1():
    """Create a mail domain for testing."""
    return factories.MailDomainFactory(name="domain1.com")


@pytest.fixture(name="mail_domain2")
def fixture_mail_domain2():
    """Create a second mail domain for testing."""
    return factories.MailDomainFactory(name="domain2.com")


@pytest.fixture(name="mailbox1_domain1")
def fixture_mailbox1_domain1(mail_domain1):
    """Create a mailbox in mail_domain1."""
    return factories.MailboxFactory(domain=mail_domain1, local_part="box1")


@pytest.fixture(name="mailbox2_domain1")
def fixture_mailbox2_domain1(mail_domain1):
    """Create a second mailbox in mail_domain1."""
    return factories.MailboxFactory(domain=mail_domain1, local_part="box2")


@pytest.fixture(name="mailbox1_domain2")
def fixture_mailbox1_domain2(mail_domain2):
    """Create a mailbox in mail_domain2."""
    return factories.MailboxFactory(domain=mail_domain2, local_part="boxA")


# --- Initial Mailbox Accesses ---
@pytest.fixture(name="user_alpha")
def fixture_user_alpha():
    """Create a user for testing mailbox access."""
    return factories.UserFactory(email="alpha@example.com")


@pytest.fixture(name="user_beta")
def fixture_user_beta():
    """Create another user for testing mailbox access."""
    return factories.UserFactory(email="beta@example.com")


@pytest.fixture(name="access_m1d1_alpha")
def fixture_access_m1d1_alpha(mailbox1_domain1, user_alpha):
    """Create EDITOR access for user_alpha to mailbox1_domain1."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox1_domain1, user=user_alpha, role=MailboxRoleChoices.EDITOR
    )


@pytest.fixture(name="access_m1d1_beta")
def fixture_access_m1d1_beta(mailbox1_domain1, user_beta):
    """Create VIEWER access for user_beta to mailbox1_domain1."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox1_domain1, user=user_beta, role=MailboxRoleChoices.VIEWER
    )


@pytest.fixture(name="access_m2d1_alpha")
def fixture_access_m2d1_alpha(mailbox2_domain1, user_alpha):
    """Create EDITOR access for user_alpha to mailbox2_domain1."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox2_domain1, user=user_alpha, role=MailboxRoleChoices.EDITOR
    )


class TestMailboxAccessViewSet:
    """Tests for the MailboxAccessViewSet API endpoints."""

    BASE_URL_LIST_CREATE_SUFFIX = "-list"
    BASE_URL_DETAIL_SUFFIX = "-detail"
    URL_BASENAME = "mailboxaccess"

    def list_create_url(self, mailbox_id):
        """Generate URL for listing/creating mailbox accesses."""
        # URLs are /mailboxes/{mailbox_id}/accesses/
        return reverse(
            self.URL_BASENAME + self.BASE_URL_LIST_CREATE_SUFFIX,
            kwargs={"mailbox_id": mailbox_id},
        )

    def detail_url(self, mailbox_id, pk):
        """Generate URL for operations on a specific mailbox access."""
        # URLs are /mailboxes/{mailbox_id}/accesses/{pk}/
        return reverse(
            self.URL_BASENAME + self.BASE_URL_DETAIL_SUFFIX,
            kwargs={"mailbox_id": mailbox_id, "pk": pk},
        )

    # --- LIST Tests ---
    def test_list_as_domain_admin_for_managed_mailbox(
        self,
        api_client,
        domain_admin_user,
        mailbox1_domain1,
        access_m1d1_alpha,
        access_m1d1_beta,
        access_m2d1_alpha,
    ):
        """Domain admin should see accesses for the specified mailbox in their administered domain."""
        api_client.force_authenticate(
            user=domain_admin_user
        )  # Admin for domain1, which mailbox1_domain1 is in
        response = api_client.get(self.list_create_url(mailbox_id=mailbox1_domain1.pk))

        assert response.status_code == status.HTTP_200_OK

        # access_m2d1_alpha is for a different mailbox, so should not be listed here.
        assert {item["id"] for item in response.data["results"]} == {
            str(access_m1d1_alpha.pk),
            str(access_m1d1_beta.pk),
        }
        assert response.data["count"] == 2

    def test_list_as_mailbox_admin_for_their_mailbox(
        self,
        api_client,
        domain_admin_user,
        mailbox1_admin_user,
        mailbox1_domain1,
        access_m1d1_alpha,
        access_m1d1_beta,
        access_m2d1_alpha,
    ):
        """Mailbox admin should see accesses for their specific mailbox."""
        api_client.force_authenticate(
            user=mailbox1_admin_user
        )  # Admin for mailbox1_domain1
        response = api_client.get(self.list_create_url(mailbox_id=mailbox1_domain1.pk))

        assert response.status_code == status.HTTP_200_OK

        # access_m2d1_alpha is for a different mailbox, so should not be listed here.
        # However the mailbox1_admin_user has an explicit mailboxaccess for this mailbox, so should see it.
        mailbox1_admin_user_access = models.MailboxAccess.objects.get(
            mailbox=mailbox1_domain1, user=mailbox1_admin_user
        )
        assert mailbox1_admin_user_access.role == MailboxRoleChoices.ADMIN

        assert {item["id"] for item in response.data["results"]} == {
            str(access_m1d1_alpha.pk),
            str(access_m1d1_beta.pk),
            str(mailbox1_admin_user_access.pk),
        }
        assert response.data["count"] == 3

    def test_list_as_mailbox_admin_for_other_mailbox_forbidden(
        self, api_client, mailbox1_admin_user, mailbox2_domain1
    ):
        """Mailbox admin should NOT see accesses for a mailbox they don't administer."""
        api_client.force_authenticate(user=mailbox1_admin_user)
        response = api_client.get(self.list_create_url(mailbox_id=mailbox2_domain1.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_as_regular_user_forbidden(
        self, api_client, regular_user, mailbox1_domain1
    ):
        """Regular users should not be able to list mailbox accesses."""
        api_client.force_authenticate(user=regular_user)
        response = api_client.get(self.list_create_url(mailbox_id=mailbox1_domain1.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_unauthenticated(self, api_client, mailbox1_domain1):
        """Unauthenticated requests to list mailbox accesses should be rejected."""
        response = api_client.get(self.list_create_url(mailbox_id=mailbox1_domain1.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # --- CREATE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "mailbox_admin"])
    def test_create_access_success(
        self,
        api_client,
        admin_type,
        domain_admin_user,
        mailbox1_admin_user,
        mailbox1_domain1,
        user_beta,
    ):
        """Domain and mailbox admins should be able to create new accesses."""
        user_performing_action = (
            domain_admin_user if admin_type == "domain_admin" else mailbox1_admin_user
        )
        api_client.force_authenticate(user=user_performing_action)

        data = {  # No 'mailbox' field in data, it comes from URL
            "user": str(user_beta.pk),
            "role": MailboxRoleChoices.EDITOR.value,
        }
        response = api_client.post(
            self.list_create_url(mailbox_id=mailbox1_domain1.pk), data
        )

        assert response.status_code == status.HTTP_201_CREATED
        # Serializer might return mailbox PK if not read_only=True, or nested details.
        # For now, check what's guaranteed by create.
        assert response.data["user"] == user_beta.pk
        assert response.data["role"] == MailboxRoleChoices.EDITOR.value
        assert models.MailboxAccess.objects.filter(
            mailbox=mailbox1_domain1, user=user_beta, role=MailboxRoleChoices.EDITOR
        ).exists()

        # Try creating the same access again
        response = api_client.post(
            self.list_create_url(mailbox_id=mailbox1_domain1.pk), data
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_access_by_mailbox_admin_for_unmanaged_mailbox_forbidden(
        self, api_client, mailbox1_admin_user, mailbox1_domain2, user_beta
    ):
        """Mailbox admin should not be able to create accesses for unmanaged mailboxes."""
        api_client.force_authenticate(user=mailbox1_admin_user)
        data = {"user": str(user_beta.pk), "role": MailboxRoleChoices.EDITOR.value}
        response = api_client.post(
            self.list_create_url(mailbox_id=mailbox1_domain2.pk), data
        )  # Attempt on mailbox1_domain2
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_access_by_domain_admin_for_unmanaged_domain_mailbox_forbidden(
        self, api_client, domain_admin_user, mailbox1_domain2, user_beta
    ):
        """Domain admin should not be able to create accesses for mailboxes in unmanaged domains."""
        api_client.force_authenticate(user=domain_admin_user)  # Admin for domain1
        data = {"user": str(user_beta.pk), "role": MailboxRoleChoices.EDITOR.value}
        response = api_client.post(
            self.list_create_url(mailbox_id=mailbox1_domain2.pk), data
        )  # mailbox1_domain2 is in domain2
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # --- RETRIEVE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "mailbox_admin"])
    def test_retrieve_access_success(
        self,
        api_client,
        admin_type,
        domain_admin_user,
        mailbox1_admin_user,
        mailbox1_domain1,
        access_m1d1_alpha,
    ):
        """Domain and mailbox admins should be able to retrieve mailbox access details."""
        user_performing_action = (
            domain_admin_user if admin_type == "domain_admin" else mailbox1_admin_user
        )
        api_client.force_authenticate(user=user_performing_action)
        response = api_client.get(
            self.detail_url(mailbox_id=mailbox1_domain1.pk, pk=access_m1d1_alpha.pk)
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(access_m1d1_alpha.pk)

    def test_retrieve_access_for_wrong_mailbox_forbidden(
        self,
        api_client,
        domain_admin_user,
        mailbox1_domain1,
        mailbox2_domain1,
        access_m1d1_alpha,
    ):
        """Attempting to retrieve an access using a mailbox_id in URL that doesn't match the access's actual mailbox."""
        api_client.force_authenticate(user=domain_admin_user)
        # access_m1d1_alpha belongs to mailbox1_domain1, but we use mailbox2_domain1 in URL
        response = api_client.get(
            self.detail_url(mailbox_id=mailbox2_domain1.pk, pk=access_m1d1_alpha.pk)
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # --- UPDATE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "mailbox_admin"])
    def test_update_access_role_success(
        self,
        api_client,
        admin_type,
        user_beta,
        domain_admin_user,
        mailbox1_admin_user,
        mailbox1_domain1,
        access_m1d1_alpha,
    ):
        """Test that domain and mailbox admins can update mailbox access roles."""
        user_performing_action = (
            domain_admin_user if admin_type == "domain_admin" else mailbox1_admin_user
        )
        api_client.force_authenticate(user=user_performing_action)
        data = {"role": MailboxRoleChoices.ADMIN.value}
        response = api_client.patch(
            self.detail_url(mailbox_id=mailbox1_domain1.pk, pk=access_m1d1_alpha.pk),
            data,
        )
        assert response.status_code == status.HTTP_200_OK
        access_m1d1_alpha.refresh_from_db()
        assert access_m1d1_alpha.role == MailboxRoleChoices.ADMIN

        data = {"role": "invalid"}
        response = api_client.patch(
            self.detail_url(mailbox_id=mailbox1_domain1.pk, pk=access_m1d1_alpha.pk),
            data,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        data = {"role": MailboxRoleChoices.ADMIN.value, "user": str(user_beta.pk)}
        response = api_client.patch(
            self.detail_url(mailbox_id=mailbox1_domain1.pk, pk=access_m1d1_alpha.pk),
            data,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # --- DELETE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "mailbox_admin"])
    def test_delete_access_success(
        self,
        api_client,
        admin_type,
        domain_admin_user,
        mailbox1_admin_user,
        mailbox1_domain1,
        access_m1d1_alpha,
    ):
        """Test that domain and mailbox admins can delete mailbox accesses."""
        user_performing_action = (
            domain_admin_user if admin_type == "domain_admin" else mailbox1_admin_user
        )
        api_client.force_authenticate(user=user_performing_action)
        response = api_client.delete(
            self.detail_url(mailbox_id=mailbox1_domain1.pk, pk=access_m1d1_alpha.pk)
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MailboxAccess.objects.filter(pk=access_m1d1_alpha.pk).exists()
