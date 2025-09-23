"""Tests for the MailDomainAccessViewSet API endpoint (nested under maildomains)."""
# pylint: disable=unused-argument

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

import pytest
from rest_framework import status

from core import factories, models
from core.enums import MailDomainAccessRoleChoices

pytestmark = pytest.mark.django_db


# --- Users ---
@pytest.fixture(name="super_user")
def fixture_super_user():
    """User with SUPERUSER access."""
    user = factories.UserFactory(is_superuser=True, is_staff=False)
    return user


@pytest.fixture(name="regular_user")
def fixture_regular_user():
    """User with no specific admin rights relevant to these tests."""
    return factories.UserFactory(email="regular@example.com")


# --- Domains ---
@pytest.fixture(name="maildomain_1")
def fixture_maildomain1():
    """Create a mail domain for testing."""
    return factories.MailDomainFactory(name="domain1.com")


@pytest.fixture(name="maildomain_2")
def fixture_maildomain2():
    """Create a second mail domain for testing."""
    return factories.MailDomainFactory(name="domain2.com")


# --- Initial Maildomain Accesses ---
@pytest.fixture(name="md1_admin_user")
def fixture_md1_admin_user():
    """Create a user for testing maildomain access."""
    return factories.UserFactory(email="alpha@example.com")


@pytest.fixture(name="md2_admin_user")
def fixture_md2_admin_user():
    """Create another user for testing maildomain access."""
    return factories.UserFactory(email="beta@example.com")


@pytest.fixture(name="md1_access")
def fixture_access_md1(maildomain_1, md1_admin_user):
    """Create ADMIN access for md1_admin_user to maildomain_1."""
    return factories.MailDomainAccessFactory(
        maildomain=maildomain_1,
        user=md1_admin_user,
        role=MailDomainAccessRoleChoices.ADMIN,
    )


@pytest.fixture(name="md2_access")
def fixture_access_md2(maildomain_2, md2_admin_user):
    """Create ADMIN access for md2_admin_user to maildomain_2."""
    return factories.MailDomainAccessFactory(
        maildomain=maildomain_2,
        user=md2_admin_user,
        role=MailDomainAccessRoleChoices.ADMIN,
    )


class TestMaildomainAccessViewSet:
    """Tests for the MaildomainAccessViewSet API endpoints."""

    BASE_URL_LIST_CREATE_SUFFIX = "-list"
    BASE_URL_DETAIL_SUFFIX = "-detail"
    URL_BASENAME = "admin-maildomains-access"

    def list_create_url(self, maildomain_pk):
        """Generate URL for listing/creating maildomain accesses."""
        # URLs are /maildomains/{maildomain_id}/accesses/
        return reverse(
            self.URL_BASENAME + self.BASE_URL_LIST_CREATE_SUFFIX,
            kwargs={"maildomain_pk": maildomain_pk},
        )

    def detail_url(self, maildomain_pk, pk):
        """Generate URL for operations on a specific maildomain access."""
        # URLs are /maildomains/{maildomain_id}/accesses/{pk}/
        return reverse(
            self.URL_BASENAME + self.BASE_URL_DETAIL_SUFFIX,
            kwargs={"maildomain_pk": maildomain_pk, "pk": pk},
        )

    # --- LIST Tests ---
    def test_admin_api_maildomain_accesses_list_by_super_user(
        self,
        api_client,
        super_user,
        maildomain_1,
        md1_access,
    ):
        """Super user should see accesses for the specified maildomain."""
        api_client.force_authenticate(
            user=super_user
        )  # Admin for domain1, which maildomain_1 is in
        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))

        assert response.status_code == status.HTTP_200_OK

        # only access_md1 should be listed here.
        assert {item["id"] for item in response.data} == {
            str(md1_access.pk),
        }
        assert len(response.data) == 1

    def test_admin_api_maildomain_accesses_list_by_domain_admin(
        self, api_client, maildomain_1, md1_admin_user, md1_access
    ):
        """Domain admin should see accesses for its maildomain."""
        # Login as admin for maildomain_1
        api_client.force_authenticate(user=md1_admin_user)

        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))
        assert response.status_code == status.HTTP_200_OK

        # Only access_md1 should be listed here.
        assert {item["id"] for item in response.data} == {str(md1_access.pk)}
        assert len(response.data) == 1

    def test_admin_api_maildomain_accesses_list_by_domain_admin_for_other_maildomain_forbidden(
        self, api_client, maildomain_1, md2_admin_user
    ):
        """Maildomain admin should NOT be able to list accesses for a maildomain they don't administer."""
        api_client.force_authenticate(user=md2_admin_user)
        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_api_maildomain_accesses_list_by_regular_user_forbidden(
        self, api_client, regular_user, maildomain_1
    ):
        """Regular users should not be able to list maildomain_1 accesses."""
        api_client.force_authenticate(user=regular_user)
        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_api_maildomain_accesses_list_unauthenticated(
        self, api_client, maildomain_1
    ):
        """Unauthenticated requests to list maildomain_1 accesses should be rejected."""
        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # --- CREATE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "super_user"])
    def test_admin_api_maildomain_accesses_create_access_success(
        self,
        api_client,
        admin_type,
        regular_user,
        super_user,
        maildomain_1,
        md1_admin_user,
        md1_access,
        md2_admin_user,
    ):
        """Domain admins and super users should be able to create new accesses."""
        user_performing_action = AnonymousUser()
        if admin_type == "super_user":
            user_performing_action = super_user
        elif admin_type == "domain_admin":
            user_performing_action = md1_admin_user

        api_client.force_authenticate(user=user_performing_action)

        data = {  # No 'maildomain' field in data, it comes from URL
            "user": str(md2_admin_user.pk),
            "role": "admin",
        }
        response = api_client.post(
            self.list_create_url(maildomain_pk=maildomain_1.pk), data
        )

        assert response.status_code == status.HTTP_201_CREATED
        # The new access should be created for the user should be returned.
        assert response.data["user"] == md2_admin_user.pk
        assert response.data["role"] == "admin"
        assert models.MailDomainAccess.objects.filter(
            maildomain=maildomain_1,
            user=md2_admin_user,
            role=MailDomainAccessRoleChoices.ADMIN,
        ).exists()

        # Try creating the same access again
        response = api_client.post(
            self.list_create_url(maildomain_pk=maildomain_1.pk), data
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # We can also create based on the email address
        # This might be temporary until we have a proper invite system
        data = {
            "user": regular_user.email,
            "role": "admin",
        }
        response = api_client.post(
            self.list_create_url(maildomain_pk=maildomain_1.pk), data
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["user"] == regular_user.pk
        assert response.data["role"] == "admin"
        assert models.MailDomainAccess.objects.filter(
            maildomain=maildomain_1,
            user=regular_user,
            role=MailDomainAccessRoleChoices.ADMIN,
        ).exists()

    def test_admin_api_maildomain_accesses_create_access_by_maildomain_admin_for_unmanaged_maildomain_forbidden(
        self, api_client, maildomain_1, md2_access, md2_admin_user, regular_user
    ):
        """Maildomain admin should not be able to create accesses for unmanaged maildomains."""
        api_client.force_authenticate(user=md2_admin_user)
        data = {"user": str(regular_user.pk), "role": "admin"}
        response = api_client.post(
            self.list_create_url(maildomain_pk=maildomain_1.pk), data
        )  # Attempt on maildomain_1
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # --- RETRIEVE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "super_user"])
    def test_admin_api_maildomain_accesses_retrieve_success(
        self,
        api_client,
        admin_type,
        super_user,
        maildomain_1,
        md1_admin_user,
        md1_access,
    ):
        """Super user and Maildomain admins should be able to retrieve maildomain access details."""
        user_performing_action = AnonymousUser()
        if admin_type == "super_user":
            user_performing_action = super_user
        elif admin_type == "domain_admin":
            user_performing_action = md1_admin_user

        api_client.force_authenticate(user=user_performing_action)
        response = api_client.get(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md1_access.pk)
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(md1_access.pk)

    def test_admin_api_maildomain_accesses_retrieve_access_for_wrong_maildomain(
        self,
        api_client,
        maildomain_1,
        md1_access,
        md2_access,
        md1_admin_user,
    ):
        """
        Attempting to retrieve an access using a maildomain_pk in URL
        that doesn't match the access's actual maildomain.
        """
        api_client.force_authenticate(user=md1_admin_user)
        # md2_access belongs to maildomain_2, but we use maildomain_1 in URL
        response = api_client.get(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md2_access.pk)
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # --- UPDATE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "super_user"])
    def test_admin_api_maildomain_accesses_update_not_allowed(
        self,
        admin_type,
        api_client,
        regular_user,
        super_user,
        maildomain_1,
        md1_admin_user,
        md1_access,
    ):
        """Update a maildomain access is not allowed (as there is only one role)."""
        user_performing_action = AnonymousUser()
        if admin_type == "super_user":
            user_performing_action = super_user
        elif admin_type == "domain_admin":
            user_performing_action = md1_admin_user

        # PUT not allowed
        api_client.force_authenticate(user=user_performing_action)
        data = {"role": "admin", "user": str(regular_user.pk)}
        response = api_client.patch(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md1_access.pk),
            data,
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        # PATCH not allowed
        api_client.force_authenticate(user=user_performing_action)
        data = {"role": "admin"}
        response = api_client.patch(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md1_access.pk),
            data,
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    # --- DELETE Tests ---
    @pytest.mark.parametrize("admin_type", ["domain_admin", "super_user"])
    def test_admin_api_maildomain_accesses_delete_success(
        self,
        api_client,
        admin_type,
        super_user,
        maildomain_1,
        md1_admin_user,
        md1_access,
    ):
        """
        Test that maildomain admin and super user can delete maildomain accesses.
        """
        user_performing_action = AnonymousUser()
        if admin_type == "super_user":
            user_performing_action = super_user
        elif admin_type == "domain_admin":
            user_performing_action = md1_admin_user

        api_client.force_authenticate(user=user_performing_action)
        response = api_client.delete(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md1_access.pk)
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MailDomainAccess.objects.filter(pk=md1_access.pk).exists()

    # --- EXCLUDE ABILITIES Tests ---
    def test_admin_api_maildomain_accesses_list_excludes_abilities_from_nested_users(
        self,
        api_client,
        maildomain_1,
        md1_access,
        md1_admin_user,
    ):
        """Test that maildomain access list endpoint excludes abilities from nested user_details."""
        api_client.force_authenticate(user=md1_admin_user)
        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        # Verify that all user_details do NOT contain abilities
        for access_data in response.data:
            assert "user" in access_data
            user_details = access_data["user"]
            assert "abilities" not in user_details
            assert "id" in user_details
            assert "email" in user_details
            assert "full_name" in user_details

    def test_admin_api_maildomain_accesses_retrieve_excludes_abilities_from_nested_user(
        self,
        api_client,
        maildomain_1,
        md1_admin_user,
        md1_access,
    ):
        """Test that maildomain access retrieve endpoint excludes abilities from nested user_details."""
        api_client.force_authenticate(user=md1_admin_user)
        response = api_client.get(
            self.detail_url(maildomain_pk=maildomain_1.pk, pk=md1_access.pk)
        )

        assert response.status_code == status.HTTP_200_OK
        assert "user" in response.data

        # Verify that user_details does NOT contain abilities
        user_details = response.data["user"]
        assert "abilities" not in user_details
        assert "id" in user_details
        assert "email" in user_details
        assert "full_name" in user_details

    def test_admin_api_maildomain_accesses_excludes_abilities_with_superuser(
        self,
        api_client,
        super_user,
        maildomain_1,
        md1_admin_user,
        md1_access,
    ):
        """Test that maildomain access excludes abilities even when accessed by superuser."""
        api_client.force_authenticate(user=super_user)

        response = api_client.get(self.list_create_url(maildomain_pk=maildomain_1.pk))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        # Verify that all user_details do NOT contain abilities
        for access_data in response.data:
            assert "user" in access_data
            user_details = access_data["user"]
            assert "abilities" not in user_details
            assert "id" in user_details
            assert "email" in user_details
            assert "full_name" in user_details
