"""Test suite for the admin maildomains user list endpoint API."""
# pylint: disable=too-many-public-methods

import uuid

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories

pytestmark = pytest.mark.django_db


class TestAdminMaildomainsUserList:
    """Test suite for the admin maildomains user list endpoint API."""

    def test_admin_maildomains_user_list_forbidden_not_domain_admin(self, api_client):
        """Test that a user without domain admin access cannot list users."""
        domain = factories.MailDomainFactory(name="sardine.local")
        user1 = factories.UserFactory(email="user1@sardine.local")
        user2 = factories.UserFactory(email="user2@sardine.local")
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=factories.UserFactory())
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert (
            str(response.data["detail"])
            == "You do not have administrative rights for this mail domain."
        )

    def test_admin_maildomains_user_list_forbidden_unauthenticated(self, api_client):
        """Test that unauthenticated users cannot access the endpoint."""
        domain = factories.MailDomainFactory(name="sardine.local")
        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_admin_maildomains_user_list_forbidden_invalid_domain_id(self, api_client):
        """Test that invalid domain IDs return 403 (not 404) due to permission check."""

        fake_domain_id = uuid.uuid4()
        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": fake_domain_id}
        )
        api_client.force_authenticate(user=factories.UserFactory())
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_maildomains_user_list_allowed_domain_admin(self, api_client):
        """Test that domain admins can access the endpoint."""
        domain = factories.MailDomainFactory(name="sardine.local")
        admin_user = factories.UserFactory(email="admin@sardine.local")
        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_admin_maildomains_user_list_basic(self, api_client):
        """
        Test list users endpoint returns all users
        with an access to a mailbox or an admin access to the maildomain.
        """
        domain = factories.MailDomainFactory(name="sardine.local")
        admin_user = factories.UserFactory(email="admin@sardine.local")
        user1 = factories.UserFactory(
            email="user1@sardine.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(
            email="user2@sardine.local", full_name="Bob Jones"
        )

        # Create domain admin access for the admin_user
        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create mailbox accesses for 2 other users in the domain
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3  # admin_user + user1 + user2

        # Check that all users are returned
        user_emails = [user["email"] for user in response.data]
        assert admin_user.email in user_emails
        assert user1.email in user_emails
        assert user2.email in user_emails

    def test_admin_maildomains_user_list_excludes_other_domain_users(self, api_client):
        """Test that users from other domains are excluded."""
        domain1 = factories.MailDomainFactory(name="domain1.local")
        domain2 = factories.MailDomainFactory(name="domain2.local")
        admin_user = factories.UserFactory(email="admin@domain1.local")
        other_domain_user = factories.UserFactory(email="user@domain2.local")

        factories.MailDomainAccessFactory(
            maildomain=domain1,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create user in other domain
        factories.MailboxAccessFactory(mailbox__domain=domain2, user=other_domain_user)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain1.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1  # Only admin_user
        assert response.data[0]["email"] == admin_user.email

    def test_admin_maildomains_user_list_duplicate_users_handled(self, api_client):
        """Test that users with both mailbox access and domain admin access are not duplicated."""
        domain = factories.MailDomainFactory(name="test.local")
        admin_user = factories.UserFactory(email="admin@test.local")
        user_with_both = factories.UserFactory(email="user@test.local")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # User has both mailbox access and domain admin access
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user_with_both)
        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=user_with_both,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # admin_user + user_with_both (not duplicated)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates

    def test_admin_maildomains_user_list_search_by_email(self, api_client):
        """Test searching users by email."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(email="bob@search.local", full_name="Bob Jones")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search for "alice"
        response = api_client.get(url, {"q": "alice"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["email"] == user1.email

    def test_admin_maildomains_user_list_search_by_full_name(self, api_client):
        """Test searching users by full name."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(email="bob@search.local", full_name="Bob Jones")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search for "Smith"
        response = api_client.get(url, {"q": "Smith"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["full_name"] == user1.full_name

    def test_admin_maildomains_user_list_search_by_short_name(self, api_client):
        """Test searching users by short name."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith", short_name="Alice"
        )
        user2 = factories.UserFactory(
            email="bob@search.local", full_name="Bob Jones", short_name="Bob"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search for "Alice"
        response = api_client.get(url, {"q": "Alice"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["short_name"] == user1.short_name

    def test_admin_maildomains_user_list_search_case_insensitive(self, api_client):
        """Test that search is case insensitive."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search with different cases
        response1 = api_client.get(url, {"q": "ALICE"})
        response2 = api_client.get(url, {"q": "alice"})
        response3 = api_client.get(url, {"q": "Alice"})

        assert response1.status_code == status.HTTP_200_OK
        assert response2.status_code == status.HTTP_200_OK
        assert response3.status_code == status.HTTP_200_OK
        assert len(response1.data) == 1
        assert len(response2.data) == 1
        assert len(response3.data) == 1

    def test_admin_maildomains_user_list_search_no_results(self, api_client):
        """Test search with no matching results."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search for non-existent user
        response = api_client.get(url, {"q": "nonexistent"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0
        assert len(response.data) == 0

    def test_admin_maildomains_user_list_search_partial_match(self, api_client):
        """Test that search works with partial matches."""
        domain = factories.MailDomainFactory(name="search.local")
        admin_user = factories.UserFactory(email="admin@search.local")
        user1 = factories.UserFactory(email="fred@search.local", full_name="Fred Smith")
        user2 = factories.UserFactory(
            email="Fritz@search.local", full_name="Fritz Johnson"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)

        # Search for "fr" should match both Fred and Fritz
        response = api_client.get(url, {"q": "fr"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        user_emails = [user["email"] for user in response.data]
        assert user1.email in user_emails
        assert user2.email in user_emails

    # ============================================================================
    # ORDERING TESTS
    # ============================================================================

    def test_admin_maildomains_user_list_ordering(self, api_client):
        """Test that users are ordered correctly (full_name, short_name, email)."""
        domain = factories.MailDomainFactory(name="order.local")
        admin_user = factories.UserFactory(
            email="admin@order.local", full_name="Admin User", short_name="Admin"
        )
        user1 = factories.UserFactory(
            email="alice@order.local", full_name="Alice Smith", short_name="Alice"
        )
        user2 = factories.UserFactory(
            email="bob@order.local", full_name="Bob Jones", short_name="Bob"
        )
        user3 = factories.UserFactory(
            email="charlie@order.local", full_name="Charlie Brown", short_name="Charlie"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user3)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 4

        # Check ordering: Admin User, Alice Smith, Bob Jones, Charlie Brown
        results = response.data
        assert results[0]["full_name"] == "Admin User"
        assert results[1]["full_name"] == "Alice Smith"
        assert results[2]["full_name"] == "Bob Jones"
        assert results[3]["full_name"] == "Charlie Brown"

    def test_admin_maildomains_user_list_ordering_with_null_names(self, api_client):
        """Test ordering when some users have null names."""
        domain = factories.MailDomainFactory(name="order.local")
        admin_user = factories.UserFactory(
            email="fritz@order.local", full_name="Admin User", short_name="Admin"
        )
        user1 = factories.UserFactory(
            email="bob@order.local", full_name=None, short_name=None
        )
        user2 = factories.UserFactory(
            email="alice@order.local", full_name=None, short_name=None
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        # Users with null names should be ordered by email
        results = response.data
        user_emails = [user["email"] for user in results]
        # Should be ordered: fritz@order.local (as it has a full_name), alice@order.local, bob@order.local
        assert user_emails[0] == "fritz@order.local"
        assert user_emails[1] == "alice@order.local"
        assert user_emails[2] == "bob@order.local"

    def test_admin_maildomains_user_list_serializer_fields(self, api_client):
        """Test that the serializer returns the correct fields."""
        domain = factories.MailDomainFactory(name="serializer.local")
        admin_user = factories.UserFactory(
            email="admin@serializer.local", full_name="Admin User", short_name="Admin"
        )
        user1 = factories.UserFactory(
            email="alice@serializer.local", full_name="Alice Smith", short_name="Alice"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check that all expected fields are present
        for user_data in response.data:
            assert "id" in user_data
            assert "email" in user_data
            assert "full_name" in user_data
            assert "short_name" in user_data
            assert "abilities" not in user_data
            assert len(user_data.keys()) == 4

    def test_admin_maildomains_user_list_serializer_null_fields(self, api_client):
        """Test that null fields are handled correctly in the serializer."""
        domain = factories.MailDomainFactory(name="null.local")
        admin_user = factories.UserFactory(
            email="admin@null.local", full_name=None, short_name=None
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        user_data = response.data[0]
        assert user_data["id"] == str(admin_user.id)
        assert user_data["email"] == admin_user.email
        assert user_data["full_name"] is None
        assert user_data["short_name"] is None

    def test_admin_maildomains_user_list_user_without_email(self, api_client):
        """Test handling of users without email addresses."""
        domain = factories.MailDomainFactory(name="noemail.local")
        admin_user = factories.UserFactory(email="admin@noemail.local")
        user_no_email = factories.UserFactory(email=None, full_name="No Email User")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user_no_email)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check that user without email is included
        user_ids = [user["id"] for user in response.data]
        assert str(user_no_email.id) in user_ids

    def test_admin_maildomains_user_list_multiple_mailbox_accesses(self, api_client):
        """Test that users with multiple mailbox accesses in the same domain are not duplicated."""
        domain = factories.MailDomainFactory(name="multi.local")
        admin_user = factories.UserFactory(email="admin@multi.local")
        user1 = factories.UserFactory(email="user@multi.local")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # User has access to multiple mailboxes in the same domain
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # admin_user + user1 (not duplicated)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates

    def test_admin_maildomains_user_list_inactive_users(self, api_client):
        """Test that inactive users are still included in the list."""
        domain = factories.MailDomainFactory(name="inactive.local")
        admin_user = factories.UserFactory(email="admin@inactive.local")
        inactive_user = factories.UserFactory(
            email="inactive@inactive.local", is_active=False
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=inactive_user)

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # Both active and inactive users included
        user_emails = [user["email"] for user in response.data]
        assert inactive_user.email in user_emails

    def test_admin_maildomains_user_list_with_alias_mailboxes(self, api_client):
        """Test that users with alias mailboxes are handled correctly."""
        domain = factories.MailDomainFactory(name="alias.local")
        admin_user = factories.UserFactory(email="admin@alias.local")

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        # Create a main mailbox and an alias
        user = factories.UserFactory(email="john@alias.local")
        mailbox_acccess = factories.MailboxAccessFactory(
            mailbox__domain=domain, user=user
        )
        factories.MailboxFactory(
            domain=domain, local_part="john.doe", alias_of=mailbox_acccess.mailbox
        )

        url = reverse(
            "admin-maildomains-user-list", kwargs={"maildomain_pk": domain.id}
        )
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert (
            len(response.data) == 2
        )  # admin_user + john (not duplicated due to alias)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates
