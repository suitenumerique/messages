"""Test API admin maildomains update (custom_settings)."""

from django.test import override_settings

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories


@pytest.fixture(name="authenticated_admin")
def fixture_authenticated_admin():
    """Create an authenticated admin user."""
    return factories.UserFactory(full_name="Admin User", email="admin@example.com")


@pytest.fixture(name="maildomain")
def fixture_maildomain():
    """Create a mail domain."""
    return factories.MailDomainFactory(name="test.example.com")


@pytest.mark.django_db
class TestAdminMaildomainUpdateCustomLimits:
    """Test API for updating maildomain custom_settings."""

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_custom_settings_as_admin(self, maildomain, authenticated_admin):
        """Test that a maildomain admin CANNOT update custom_settings (only superusers can)."""
        # Give admin access to the domain
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=authenticated_admin,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_admin)

        # Try to update custom_settings (should be forbidden)
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": 50}},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify the limit was NOT updated
        maildomain.refresh_from_db()
        assert maildomain.custom_settings.get("max_recipients_per_message") is None

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_custom_settings_as_superuser(self, maildomain):
        """Test that a superuser can update custom_settings."""
        superuser = factories.UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        # Update custom_settings
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": 100}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify the update
        maildomain.refresh_from_db()
        assert maildomain.custom_settings == {"max_recipients_per_message": 100}

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_custom_settings_clear_value(self, maildomain):
        """Test that custom_settings can be cleared by superuser."""
        # Set initial value
        maildomain.custom_settings = {"max_recipients_per_message": 50}
        maildomain.save()

        # Use a superuser
        superuser = factories.UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        # Clear custom_settings by setting to null
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": None}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify the update
        maildomain.refresh_from_db()
        assert maildomain.custom_settings.get("max_recipients_per_message") is None

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_custom_settings_unauthorized(self, maildomain):
        """Test that unauthenticated users cannot update custom_settings."""
        client = APIClient()

        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": 50}},
            format="json",
        )

        # Returns 404 because unauthenticated users have empty queryset
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_custom_settings_forbidden_non_admin(self, maildomain):
        """Test that non-admin users cannot update custom_settings."""
        regular_user = factories.UserFactory()

        client = APIClient()
        client.force_authenticate(user=regular_user)

        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": 50}},
            format="json",
        )

        # Returns 404 because non-admin users don't have access to this maildomain in queryset
        # This is the standard DRF behavior and is more secure (doesn't reveal object existence)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_cannot_update_other_fields(self, maildomain):
        """Test that only custom_settings can be updated by superuser, not other fields like name."""
        original_name = maildomain.name

        # Use a superuser
        superuser = factories.UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        # Try to update name (should be ignored or rejected)
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {
                "name": "hacked.example.com",
                "custom_settings": {"max_recipients_per_message": 50},
            },
            format="json",
        )

        # The request should succeed but name should not change
        assert response.status_code == status.HTTP_200_OK

        maildomain.refresh_from_db()
        assert maildomain.name == original_name  # Name unchanged
        assert maildomain.custom_settings == {
            "max_recipients_per_message": 50
        }  # Limits updated

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_cannot_exceed_global_max_recipients(self, maildomain):
        """Test that custom_settings cannot exceed MAX_RECIPIENTS_PER_MESSAGE even for superuser."""
        # Use a superuser
        superuser = factories.UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        # Try to set limit higher than global max (100)
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/",
            {"custom_settings": {"max_recipients_per_message": 150}},
            format="json",
        )

        # Should be rejected
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "custom_settings" in response.json()
        assert "100" in str(response.json()["custom_settings"])

        # Verify the limit was not updated
        maildomain.refresh_from_db()
        assert maildomain.custom_settings.get("max_recipients_per_message") != 150


@pytest.mark.django_db
class TestAdminMailboxUpdateCustomLimits:
    """Test API for updating mailbox custom_settings."""

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_update_mailbox_custom_settings_as_admin(
        self, maildomain, authenticated_admin
    ):
        """Test that a maildomain admin can update mailbox custom_settings via settings endpoint."""
        mailbox = factories.MailboxFactory(domain=maildomain)

        # Give admin access to the domain
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=authenticated_admin,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_admin)

        # Update mailbox custom_settings via the dedicated settings endpoint
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/mailboxes/{mailbox.id}/settings/",
            {"custom_settings": {"max_recipients_per_message": 25}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify the update
        mailbox.refresh_from_db()
        assert mailbox.custom_settings == {"max_recipients_per_message": 25}

    def test_mailbox_limit_takes_priority_over_domain(
        self, maildomain, authenticated_admin
    ):
        """Test that mailbox custom_settings takes priority over domain limits."""
        # Set domain limit
        maildomain.custom_settings = {"max_recipients_per_message": 100}
        maildomain.save()

        # Create mailbox with its own limit
        mailbox = factories.MailboxFactory(
            domain=maildomain, custom_settings={"max_recipients_per_message": 10}
        )

        # Give admin access
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=authenticated_admin,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_admin,
        )

        # The effective limit should be 10 (mailbox), not 100 (domain)
        assert mailbox.get_max_recipients_per_message() == 10

    @override_settings(
        MAX_RECIPIENTS_PER_MESSAGE=200, MAX_DEFAULT_RECIPIENTS_PER_MESSAGE=100
    )
    def test_domain_limit_used_when_mailbox_has_none(self):
        """Test that domain limit is used when mailbox has no custom limit."""

        # Create a mailbox with no custom limit
        mailbox = factories.MailboxFactory()
        assert mailbox.get_max_recipients_per_message() == 100

        # Set domain limit
        maildomain = mailbox.domain
        maildomain.custom_settings = {"max_recipients_per_message": 75}
        maildomain.save()

        assert mailbox.custom_settings == {}
        assert mailbox.domain.custom_settings == {"max_recipients_per_message": 75}

        # The effective limit should be 75 (from domain)
        assert mailbox.get_max_recipients_per_message() == 75

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_mailbox_cannot_exceed_global_max_recipients(
        self, maildomain, authenticated_admin
    ):
        """Test that mailbox custom_settings cannot exceed MAX_RECIPIENTS_PER_MESSAGE."""
        mailbox = factories.MailboxFactory(domain=maildomain)

        # Give admin access
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=authenticated_admin,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_admin)

        # Try to set mailbox limit higher than global max (100) via settings endpoint
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/mailboxes/{mailbox.id}/settings/",
            {"custom_settings": {"max_recipients_per_message": 150}},
            format="json",
        )

        # Should be rejected
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "custom_settings" in response.json()
        assert "100" in str(response.json()["custom_settings"])

        # Verify the limit was not updated
        mailbox.refresh_from_db()
        assert mailbox.custom_settings.get("max_recipients_per_message") != 150

    @override_settings(MAX_RECIPIENTS_PER_MESSAGE=100)
    def test_mailbox_settings_forbidden_for_mailbox_admin_only(
        self, maildomain, authenticated_admin
    ):
        """Test that mailbox admin (without domain admin) cannot update settings."""
        mailbox = factories.MailboxFactory(domain=maildomain)

        # Give mailbox admin access (but NOT domain admin)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_admin,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_admin)

        # Try to update settings (should be forbidden)
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/mailboxes/{mailbox.id}/settings/",
            {"custom_settings": {"max_recipients_per_message": 25}},
            format="json",
        )

        # Should be forbidden (need to be domain admin)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_mailbox_name_update_without_manage_settings(
        self, maildomain, authenticated_admin
    ):
        """Test that domain admin can update mailbox name without CAN_MANAGE_SETTINGS."""
        mailbox = factories.MailboxFactory(domain=maildomain, is_identity=False)
        contact = factories.ContactFactory(
            mailbox=mailbox, email=str(mailbox), name="Old Name"
        )
        mailbox.contact = contact
        mailbox.save()

        # Give domain admin access (has CAN_MANAGE_SETTINGS for mailbox)
        factories.MailDomainAccessFactory(
            maildomain=maildomain,
            user=authenticated_admin,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        client = APIClient()
        client.force_authenticate(user=authenticated_admin)

        # Update mailbox name (should work without needing settings permission)
        response = client.patch(
            f"/api/v1.0/maildomains/{maildomain.id}/mailboxes/{mailbox.id}/",
            {"metadata": {"name": "New Team Name"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify name was updated
        mailbox.refresh_from_db()
        assert mailbox.contact.name == "New Team Name"
