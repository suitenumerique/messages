"""Test the MailboxViewSet."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="superuser")
def fixture_superuser():
    """Create a test superuser."""
    return factories.UserFactory(is_superuser=True, is_staff=True)


@pytest.mark.django_db
class TestMailboxViewSet:
    """Test the MailboxViewSet."""

    def test_list(self):
        """Test the list method."""
        # Create authenticated user with access to 2 mailboxes
        authenticated_user = factories.UserFactory()
        user_mailbox1 = factories.MailboxFactory()
        user_mailbox2 = factories.MailboxFactory()
        other_mailbox = factories.MailboxFactory()
        # Authenticated user has access to 2 mailboxes
        factories.MailboxAccessFactory(
            mailbox=user_mailbox1,
            user=authenticated_user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        factories.MailboxAccessFactory(
            mailbox=user_mailbox2,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        # Create an other user with access to other mailbox
        other_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=other_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # create a thread with one unread message for user_mailbox1
        thread1 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox1,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread1, read_at=None)

        # create a thread with one read message for user_mailbox2
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox2,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread2, read_at=timezone.now())

        # create a thread with one unread message for user_mailbox2
        thread3 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=user_mailbox2,
            thread=thread3,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread3, read_at=None)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Get list of mailboxes
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check response data
        assert response.data[0]["id"] == str(user_mailbox2.id)
        assert response.data[0]["email"] == str(user_mailbox2)
        assert response.data[0]["role"] == "editor"
        assert response.data[0]["count_unread_messages"] == 1
        assert response.data[0]["count_messages"] == 2

        assert response.data[1]["id"] == str(user_mailbox1.id)
        assert response.data[1]["email"] == str(user_mailbox1)
        assert response.data[1]["role"] == "viewer"
        assert response.data[1]["count_unread_messages"] == 1
        assert response.data[1]["count_messages"] == 1

    def test_list_unauthorized(self):
        """Anonymous user cannot access the list of mailboxes."""
        client = APIClient()
        response = client.get(reverse("mailboxes-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_search_mailboxes(self):
        """Test searching mailboxes by domain and query."""
        # Create authenticated user
        authenticated_user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Create mailboxes in the same domain
        domain = factories.MailDomainFactory(name="mydomain.com")
        context_contact = factories.ContactFactory(name="Context User")
        context_mailbox = factories.MailboxFactory(
            domain=domain, contact=context_contact, local_part="context"
        )

        # Create mailboxes with contacts
        john_doe_contact = factories.ContactFactory(name="John Doe")
        john_doe_mailbox = factories.MailboxFactory(
            domain=domain, contact=john_doe_contact, local_part="john.doe"
        )
        jane_doe_contact = factories.ContactFactory(name="Jane Doe")
        jane_doe_mailbox = factories.MailboxFactory(
            domain=domain, contact=jane_doe_contact, local_part="jane.doe"
        )
        john_smith_contact = factories.ContactFactory(name="John Smith")
        john_smith_mailbox = factories.MailboxFactory(
            domain=domain, contact=john_smith_contact, local_part="john.smith"
        )
        other_contact = factories.ContactFactory(name="Other User")
        factories.MailboxFactory(contact=other_contact, local_part="other")

        # Give user access to source mailbox
        factories.MailboxAccessFactory(
            mailbox=context_mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Test search by domain only (no query)
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert (
            len(response.data) == 3
        )  # All mailboxes in example.com domain except context mailbox
        assert {mailbox["id"] for mailbox in response.data} == {
            str(john_doe_mailbox.id),
            str(jane_doe_mailbox.id),
            str(john_smith_mailbox.id),
        }
        # TODO:exclude current mailbox ?

        # Test search by local part
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "john"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2  # john.doe and john.smith
        assert {mailbox["id"] for mailbox in response.data} == {
            str(john_doe_mailbox.id),
            str(john_smith_mailbox.id),
        }

        # Test search by contact name
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "doe"},
        )
        assert response.status_code == status.HTTP_200_OK

        assert len(response.data) == 2  # john.doe and jane.doe
        assert {mailbox["id"] for mailbox in response.data} == {
            str(john_doe_mailbox.id),
            str(jane_doe_mailbox.id),
        }

        # Test search by both local part and contact name
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "jane doe"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1  # only jane.doe
        assert response.data[0]["id"] == str(jane_doe_mailbox.id)

        # Test search with no matches
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "nonexistent"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_search_mailboxes_errors(self):
        """Test error cases for mailbox search."""
        authenticated_user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Test invalid UUID format
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": "invalid-uuid"}),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Test non-existent mailbox
        response = client.get(
            reverse(
                "mailboxes-search",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            ),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_search_mailboxes_unauthorized(self):
        """Test that anonymous users cannot search mailboxes."""
        client = APIClient()
        response = client.get(
            reverse(
                "mailboxes-search",
                kwargs={"pk": "00000000-0000-0000-0000-000000000000"},
            ),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_search_mailboxes_without_access(self):
        """Test that users cannot search mailboxes they don't have access to."""
        # Create two users
        user1 = factories.UserFactory()
        user2 = factories.UserFactory()

        # Create a mailbox for user1
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="user1", domain=domain)
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user1,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Try to search using user2's credentials
        client = APIClient()
        client.force_authenticate(user=user2)

        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(mailbox.id)}),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_mailbox(self):
        """Test the retrieve method with user_access_roles optimization."""
        # Create authenticated user with access to a mailbox
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Create a thread with one unread message for the mailbox
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread, read_at=None)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Get mailbox detail
        response = client.get(
            reverse("mailboxes-detail", kwargs={"pk": str(mailbox.id)})
        )
        assert response.status_code == status.HTTP_200_OK

        # Check response data
        assert response.data["id"] == str(mailbox.id)
        assert response.data["email"] == str(mailbox)
        assert response.data["role"] == "editor"
        assert response.data["count_unread_messages"] == 1
        assert response.data["count_messages"] == 1

    def test_retrieve_mailbox_unauthorized(self):
        """Test that users cannot retrieve mailboxes they don't have access to."""
        # Create user without access to mailbox
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Try to get mailbox detail
        response = client.get(
            reverse("mailboxes-detail", kwargs={"pk": str(mailbox.id)})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_search_mailboxes_case_insensitive(self):
        """Test that search is case insensitive."""
        authenticated_user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Create mailboxes with mixed case
        domain = factories.MailDomainFactory(name="example.com")
        context_mailbox = factories.MailboxFactory(domain=domain)
        pierre_bidule_contact = factories.ContactFactory(name="Pierre Bidule")
        factories.MailboxFactory(
            local_part="pierre.bidule", domain=domain, contact=pierre_bidule_contact
        )
        jane_bidule_contact = factories.ContactFactory(name="JANE BIDULE")
        factories.MailboxFactory(
            local_part="jane.bidule", domain=domain, contact=jane_bidule_contact
        )

        # Give user access to source mailbox
        factories.MailboxAccessFactory(
            mailbox=context_mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Test case insensitive search for local part
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "pierre"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        # Test case insensitive search for contact name
        response = client.get(
            reverse("mailboxes-search", kwargs={"pk": str(context_mailbox.id)}),
            {"q": "jane bidule"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1


class TestMailboxAbilitiesAPI:
    """Test the abilities field in Mailbox API responses."""

    def test_mailbox_abilities_in_response(self, api_client, user, mailbox):
        """Test that abilities are included in mailbox API response."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        api_client.force_authenticate(user=user)
        url = reverse("mailboxes-detail", args=[mailbox.id])
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
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is True
        assert abilities["manage_labels"] is True

    def test_mailbox_list_with_abilities(self, api_client, user, mailbox):
        """Test that mailbox list includes abilities for each mailbox."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=user)
        url = reverse("mailboxes-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        mailbox_data = response.data[0]
        assert "abilities" in mailbox_data
        abilities = mailbox_data["abilities"]
        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is False
        assert abilities["manage_labels"] is False

    def test_mailbox_detail_no_access_abilities(self, api_client, user, mailbox):
        """Test that abilities are correctly set when user has no access to detail."""
        api_client.force_authenticate(user=user)
        url = reverse("mailboxes-detail", args=[mailbox.id])
        response = api_client.get(url)

        # Should return 404 since user has no access to this mailbox
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # pylint: disable=unused-argument
    def test_mailbox_list_no_access_abilities(self, api_client, user, mailbox):
        """Test that abilities are correctly set when user has no access."""
        api_client.force_authenticate(user=user)
        url = reverse("mailboxes-list")
        response = api_client.get(url)

        # User has no access, so should get empty list
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_mailbox_viewer_abilities(self, api_client, user, mailbox):
        """Test that viewer role has correct abilities."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        api_client.force_authenticate(user=user)
        url = reverse("mailboxes-detail", args=[mailbox.id])
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "abilities" in response.data
        abilities = response.data["abilities"]
        assert abilities["get"] is True
        assert abilities["patch"] is False
        assert abilities["put"] is False
        assert abilities["post"] is False
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is False
        assert abilities["manage_labels"] is False

    def test_mailbox_user_role_annotation(self, api_client, user, mailbox):
        """Test that the user_role annotation works correctly."""
        # Create access for user
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        api_client.force_authenticate(user=user)

        # Test list endpoint
        url = reverse("mailboxes-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["role"] == "editor"

        # Test detail endpoint
        url = reverse("mailboxes-detail", args=[mailbox.id])
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["role"] == "editor"
