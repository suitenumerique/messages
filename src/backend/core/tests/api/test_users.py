"""
Test users API endpoints in the messages core app.
"""

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories, models

pytestmark = pytest.mark.django_db


class TestUsersGetMe:
    """Test suite for the user me endpoint API."""

    def test_api_users_get_me_anonymous(self, api_client):
        """Anonymous users should not be allowed to list users."""
        factories.UserFactory.create_batch(2)

        response = api_client.get(reverse("users-me"))

        assert response.status_code == 401
        assert response.json() == {
            "detail": "Authentication credentials were not provided."
        }

    def test_api_users_get_me_authenticated(self, api_client):
        """Authenticated users should be able to retrieve their own user via the "/users/me" path."""
        user = factories.UserFactory()
        factories.UserFactory.create_batch(2)

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        assert data == {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "custom_attributes": user.custom_attributes,
            "abilities": {
                "create_maildomains": False,
                "view_maildomains": False,
                "manage_maildomain_accesses": False,
            },
        }

    def test_api_users_get_me_with_abilities_regular_user(self, api_client):
        """Test abilities for regular user without mail domain access."""
        user = factories.UserFactory()

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is False
        assert abilities["manage_maildomain_accesses"] is False

    def test_api_users_get_me_with_abilities_user_with_access(self, api_client):
        """Test abilities for user with mail domain access."""
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()

        # Give user access to a mail domain
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is True
        assert abilities["manage_maildomain_accesses"] is False

    def test_api_users_get_me_with_abilities_superuser_staff(self, api_client):
        """Test abilities for superuser and staff user."""
        user = factories.UserFactory(is_superuser=True, is_staff=True)

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is True
        assert abilities["view_maildomains"] is True
        assert abilities["manage_maildomain_accesses"] is True

    def test_api_users_get_me_with_abilities_superuser_not_staff(self, api_client):
        """Test abilities for superuser without staff status."""
        user = factories.UserFactory(is_superuser=True, is_staff=False)

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is True
        assert abilities["view_maildomains"] is True
        assert abilities["manage_maildomain_accesses"] is True

    def test_api_users_get_me_with_abilities_staff_not_superuser(self, api_client):
        """Test abilities for staff user without superuser status."""
        user = factories.UserFactory(is_superuser=False, is_staff=True)

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is False
        assert abilities["view_maildomains"] is False
        assert abilities["manage_maildomain_accesses"] is False

    def test_api_users_get_me_with_abilities_superuser_staff_with_access(
        self, api_client
    ):
        """Test abilities for superuser/staff with mail domain access."""
        user = factories.UserFactory(is_superuser=True, is_staff=True)
        maildomain = factories.MailDomainFactory()

        # Give user access to a mail domain
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        abilities = data["abilities"]
        assert abilities["create_maildomains"] is True
        assert abilities["view_maildomains"] is True
        assert abilities["manage_maildomain_accesses"] is True

    def test_api_users_get_me_includes_abilities_by_default(self, api_client):
        """Test that /users/me/ endpoint includes abilities by default (no exclude_abilities)."""
        user = factories.UserFactory()

        api_client.force_authenticate(user=user)
        response = api_client.get(reverse("users-me"))

        assert response.status_code == 200
        data = response.json()
        # Verify that abilities ARE included by default
        assert "abilities" in data
        assert data["abilities"] == user.get_abilities()
        assert "id" in data
        assert "email" in data
        assert "full_name" in data


class TestAdminUsersList:
    """Test suite for the admin user list endpoint API."""

    def test_api_admin_users_list_forbidden_unauthenticated(self, api_client):
        """Test that unauthenticated users cannot access the endpoint."""
        url = reverse("users-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_admin_users_list_forbidden_not_domain_admin_or_superuser(
        self, api_client
    ):
        """Test that a user without domain admin access or superuser cannot list users."""
        domain = factories.MailDomainFactory(name="sardine.local")
        user1 = factories.UserFactory(email="user1@sardine.local")
        user2 = factories.UserFactory(email="user2@sardine.local")
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse("users-list")
        api_client.force_authenticate(user=factories.UserFactory())
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert (
            str(response.data["detail"])
            == "You do not have permission to perform this action."
        )

    def test_api_admin_users_list_domain_admin_without_domain_pk_forbidden(
        self, api_client
    ):
        """Test that a domain admin cannot list users if no domain pk is provided."""
        domain = factories.MailDomainFactory(name="sardine.local")
        admin_user = factories.UserFactory(email="admin@sardine.local")
        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert (
            str(response.data["detail"])
            == "You do not have permission to perform this action."
        )

    def test_api_admin_users_list_allowed_domain_admin(self, api_client):
        """
        Test that domain admins can access the endpoint with a domain pk.
        """
        domain = factories.MailDomainFactory(name="sardine.local")
        other_domain = factories.MailDomainFactory(name="other.local")
        admin_user = factories.UserFactory(email="admin@sardine.local")
        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        mailbox_user = factories.UserFactory(email="mailbox_user@sardine.local")
        factories.MailboxAccessFactory(
            mailbox__domain=domain,
            user=mailbox_user,
        )
        other_domain_user = factories.UserFactory(email="other_domain_user@other.local")
        factories.MailboxAccessFactory(
            mailbox__domain=other_domain,
            user=other_domain_user,
        )

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "sardine"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        for user in response.data:
            assert user["email"] in [admin_user.email, mailbox_user.email]

    def test_api_admin_users_list_search_query_mandatory(self, api_client):
        """
        Test list users endpoint returns an empty list if no search query is provided.
        Otherwise, it returns all users active users.
        """
        super_user = factories.UserFactory(
            email="admin@sardine.local", is_superuser=True
        )
        user1 = factories.UserFactory(
            email="user1@sardine.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(
            email="user2@example.local", full_name="Bob Jones"
        )
        # Inactive users or without email are excluded
        factories.UserFactory(
            email="user3@example.local", full_name="Charlie Brown", is_active=False
        )
        factories.UserFactory(email=None, full_name="David Green")

        url = reverse("users-list")

        # With search query too short, it returns an empty list
        api_client.force_authenticate(user=super_user)
        response = api_client.get(url, {"q": "lo"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0  # admin_user + user1 + user2

        # With search query long enough (3 chars), it returns all active users
        response = api_client.get(url, {"q": "loc"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        # Check that all users are returned
        user_emails = [user["email"] for user in response.data]
        assert super_user.email in user_emails
        assert user1.email in user_emails
        assert user2.email in user_emails

    def test_api_admin_users_list_excludes_other_domain_users(self, api_client):
        """Test that users from other domains are excluded when a domain pk is provided."""
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

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain1.id, "q": "local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1  # Only admin_user
        assert response.data[0]["email"] == admin_user.email

    def test_api_admin_users_list_duplicate_users_handled(self, api_client):
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

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # admin_user + user_with_both (not duplicated)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates

    def test_api_admin_users_list_search_by_email(self, api_client):
        """Test searching users by email."""
        super_user = factories.UserFactory(
            email="admin@search.local", is_superuser=True
        )
        user1 = factories.UserFactory(
            email="a.smith@search.local", full_name="Alice Smith"
        )
        factories.UserFactory(email="b.jones@search.local", full_name="Bob Jones")

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

        # Search for "a.smith"
        response = api_client.get(url, {"q": "a.smith"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["email"] == user1.email

    def test_api_admin_users_list_search_by_full_name(self, api_client):
        """Test searching users by full name."""
        super_user = factories.UserFactory(
            email="admin@search.local", full_name="Super User", is_superuser=True
        )
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )
        factories.UserFactory(email="bob@search.local", full_name="Bob Jones")

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

        # Search for "Smith"
        response = api_client.get(url, {"q": "Smith"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["full_name"] == user1.full_name

    def test_api_admin_users_list_search_case_insensitive(self, api_client):
        """Test that search is case insensitive."""
        super_user = factories.UserFactory(
            email="admin@search.local", full_name="Super User", is_superuser=True
        )
        factories.UserFactory(email="alice@search.local", full_name="Alice Smith")
        factories.UserFactory(email="bob@search.local", full_name="Bob Jones")

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

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

    def test_api_admin_users_list_search_no_results(self, api_client):
        """Test search with no matching results."""
        super_user = factories.UserFactory(
            email="admin@search.local", full_name="Super User", is_superuser=True
        )
        factories.UserFactory(email="alice@search.local", full_name="Alice Smith")

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

        # Search for non-existent user
        response = api_client.get(url, {"q": "nonexistent"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0
        assert len(response.data) == 0

    def test_api_admin_users_list_search_partial_match(self, api_client):
        """Test that search works with partial matches."""
        super_user = factories.UserFactory(
            email="admin@search.local", full_name="Super User", is_superuser=True
        )
        user1 = factories.UserFactory(email="fred@search.local", full_name="Fred Jones")
        user2 = factories.UserFactory(
            email="fritz@search.local", full_name="Fritz Jones"
        )

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

        # Search for "jones" should match both Fred and Fritz Jones
        response = api_client.get(url, {"q": "jones"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        user_emails = [user["email"] for user in response.data]
        assert user1.email in user_emails
        assert user2.email in user_emails

    def test_admin_maildomains_user_list_filter_by_maildomain(self, api_client):
        """Test filtering users by maildomain."""
        domain = factories.MailDomainFactory(name="search.local")
        other_domain = factories.MailDomainFactory(name="other.local")
        super_user = factories.UserFactory(
            email="admin@search.local", full_name="Admin User", is_superuser=True
        )
        user1 = factories.UserFactory(
            email="alice@search.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(
            email="alice@other.local", full_name="Alice Smith"
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=other_domain, user=user2)

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)

        # Search for "Smith"
        response = api_client.get(url, {"q": "Smith", "maildomain_pk": domain.id})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["full_name"] == user1.full_name

    # ============================================================================
    # ORDERING TESTS
    # ============================================================================

    def test_api_admin_users_list_ordering(self, api_client):
        """Test that users are ordered correctly (full_name, email)."""
        domain = factories.MailDomainFactory(name="order.local")
        admin_user = factories.UserFactory(
            email="admin@order.local", full_name="Admin User"
        )
        user1 = factories.UserFactory(
            email="alice@order.local", full_name="Alice Smith"
        )
        user2 = factories.UserFactory(email="bob@order.local", full_name="Bob Jones")
        user3 = factories.UserFactory(
            email="charlie@order.local", full_name="Charlie Brown"
        )

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user3)

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "order.local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 4

        # Check ordering: Admin User, Alice Smith, Bob Jones, Charlie Brown
        results = response.data
        assert results[0]["full_name"] == "Admin User"
        assert results[1]["full_name"] == "Alice Smith"
        assert results[2]["full_name"] == "Bob Jones"
        assert results[3]["full_name"] == "Charlie Brown"

    def test_api_admin_users_list_ordering_with_null_names(self, api_client):
        """Test ordering when some users have null names."""
        domain = factories.MailDomainFactory(name="order.local")
        admin_user = factories.UserFactory(
            email="fritz@order.local", full_name="Admin User"
        )
        user1 = factories.UserFactory(email="bob@order.local", full_name=None)
        user2 = factories.UserFactory(email="alice@order.local", full_name=None)

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user1)
        factories.MailboxAccessFactory(mailbox__domain=domain, user=user2)

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "order.local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        # Users with null names should be ordered by email
        results = response.data
        user_emails = [user["email"] for user in results]
        # Should be ordered: fritz@order.local (as it has a full_name), alice@order.local, bob@order.local
        assert user_emails[0] == "fritz@order.local"
        assert user_emails[1] == "alice@order.local"
        assert user_emails[2] == "bob@order.local"

    def test_api_admin_users_list_serializer_fields(self, api_client):
        """Test that the serializer returns the correct fields."""
        super_user = factories.UserFactory(
            email="admin@serializer.local", full_name="Admin User", is_superuser=True
        )
        factories.UserFactory(email="alice@serializer.local", full_name="Alice Smith")

        url = reverse("users-list")
        api_client.force_authenticate(user=super_user)
        response = api_client.get(url, {"q": "serializer.local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check that all expected fields are present
        for user_data in response.data:
            assert set(user_data.keys()) == {
                "id",
                "email",
                "full_name",
                "custom_attributes",
            }

    def test_api_admin_users_list_serializer_null_fields(self, api_client):
        """Test that null fields are handled correctly in the serializer."""
        domain = factories.MailDomainFactory(name="null.local")
        admin_user = factories.UserFactory(email="admin@null.local", full_name=None)

        factories.MailDomainAccessFactory(
            maildomain=domain,
            user=admin_user,
            role=enums.MailDomainAccessRoleChoices.ADMIN,
        )

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "null.local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        user_data = response.data[0]
        assert user_data["id"] == str(admin_user.id)
        assert user_data["email"] == admin_user.email
        assert user_data["full_name"] is None

    def test_api_admin_users_list_multiple_mailbox_accesses(self, api_client):
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

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "multi.local"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # admin_user + user1 (not duplicated)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates

    def test_api_admin_users_list_with_alias_mailboxes(self, api_client):
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

        url = reverse("users-list")
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url, {"maildomain_pk": domain.id, "q": "alias.local"})

        assert response.status_code == status.HTTP_200_OK
        assert (
            len(response.data) == 2
        )  # admin_user + john (not duplicated due to alias)
        user_emails = [user["email"] for user in response.data]
        assert len(user_emails) == len(set(user_emails))  # No duplicates
