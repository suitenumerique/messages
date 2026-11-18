"""Test the ContactViewSet."""

from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.mda.inbound import deliver_inbound_message
from core.mda.inbound_tasks import process_inbound_message_task


def written_to(contact):
    """Send a message from the mailbox of `contact` to it, so it gets suggested."""
    message = factories.MessageFactory(
        sender=factories.ContactFactory(mailbox=contact.mailbox),
        is_sender=True,
        is_draft=False,
    )
    factories.MessageRecipientFactory(message=message, contact=contact)
    return contact


@pytest.mark.django_db
class TestContactViewSet:
    """Test the ContactViewSet."""

    def test_list_only_suggests_the_addresses_the_mailbox_wrote_to(self):
        """Receiving a message, as sender or in copy, or drafting one is not enough."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        written_to(
            factories.ContactFactory(mailbox=mailbox, email="written@example.com")
        )
        sender = factories.ContactFactory(mailbox=mailbox, email="sender@example.com")
        received = factories.MessageFactory(sender=sender, is_sender=False)
        factories.MessageRecipientFactory(
            message=received,
            contact=factories.ContactFactory(mailbox=mailbox, email="cc@example.com"),
        )
        factories.MessageRecipientFactory(
            message=factories.MessageFactory(
                sender=factories.ContactFactory(mailbox=mailbox),
                is_sender=True,
                is_draft=True,
            ),
            contact=factories.ContactFactory(
                mailbox=mailbox, email="draft@example.com"
            ),
        )
        factories.ContactFactory(mailbox=mailbox, email="orphan@example.com")
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(reverse("contacts-list"), {"mailbox_id": str(mailbox.id)})

        assert response.status_code == status.HTTP_200_OK
        assert [c["email"] for c in response.data] == ["written@example.com"]

    def test_list_contacts_unauthorized(self):
        """Anonymous user cannot access the list of contacts."""
        client = APIClient()
        response = client.get(reverse("contacts-list"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_contacts_no_access(self):
        """User without access to the mailbox cannot access the list of contacts."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(reverse("contacts-list"), {"mailbox_id": str(mailbox.id)})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_contacts(self):
        """Test listing all contacts for user's mailboxes."""
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

        # Create contacts for user's mailboxes
        contact1 = factories.ContactFactory(
            mailbox=user_mailbox1, name="John Doe", email="john@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=user_mailbox2, name="Jane Smith", email="jane@example.com"
        )
        contact3 = factories.ContactFactory(
            mailbox=user_mailbox2, name="Bob Wilson", email="bob@example.com"
        )

        # Create contact for other mailbox (should not appear in results)
        other_contact = factories.ContactFactory(
            mailbox=other_mailbox, name="Other User", email="other@example.com"
        )
        for contact in (contact1, contact2, contact3, other_contact):
            written_to(contact)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Get list of contacts
        response = client.get(reverse("contacts-list"))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        # Check response data (ordered by name, then email)
        expected_contacts = [
            {
                "id": str(contact3.id),
                "name": "Bob Wilson",
                "email": "bob@example.com",
                "is_suggested": True,
                "can_change_suggestion": True,
            },
            {
                "id": str(contact2.id),
                "name": "Jane Smith",
                "email": "jane@example.com",
                "is_suggested": True,
                "can_change_suggestion": True,
            },
            {
                "id": str(contact1.id),
                "name": "John Doe",
                "email": "john@example.com",
                "is_suggested": True,
                "can_change_suggestion": False,
            },
        ]
        assert response.data == expected_contacts

    def test_list_contacts_filter_by_mailbox(self):
        """Test filtering contacts by mailbox ID."""
        # Create authenticated user with access to 2 mailboxes
        authenticated_user = factories.UserFactory()
        user_mailbox1 = factories.MailboxFactory()
        user_mailbox2 = factories.MailboxFactory()

        # Authenticated user has access to both mailboxes
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

        # Create contacts for each mailbox
        contact1 = factories.ContactFactory(
            mailbox=user_mailbox1, name="John Doe", email="john@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=user_mailbox2, name="Jane Smith", email="jane@example.com"
        )
        written_to(contact1)
        written_to(contact2)

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Filter by first mailbox
        response = client.get(
            reverse("contacts-list"), {"mailbox_id": str(user_mailbox1.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)

        # Filter by second mailbox
        response = client.get(
            reverse("contacts-list"), {"mailbox_id": str(user_mailbox2.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact2.id)

    def test_list_contacts_filter_by_mailbox_includes_domain_contacts(
        self, django_assert_num_queries
    ):
        """Test filtering contacts by mailbox ID also includes contacts from other mailboxes in the same domain."""
        # create a domain for authenticated user
        brigny_domain = factories.MailDomainFactory(name="brigny.fr")

        # Create authenticated user with access to mailboxes
        dominique = factories.UserFactory()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=dominique)

        # create a mailbox for authenticated user
        dominique_mailbox = factories.MailboxFactory(
            local_part="dominique.durand", domain=brigny_domain
        )
        factories.MailboxAccessFactory(
            mailbox=dominique_mailbox,
            user=dominique,
            role=models.MailboxRoleChoices.ADMIN,
        )
        dominique_contact = factories.ContactFactory(
            name="Dominique Durand", email="dominique.durand@brigny.fr"
        )
        dominique_mailbox.contact = dominique_contact
        dominique_mailbox.save()

        # Create 2 other mailboxes in the same domain
        alain_mailbox = factories.MailboxFactory(
            local_part="alain.verse", domain=brigny_domain
        )
        alain_contact = factories.ContactFactory(
            name="Alain Verse", email="alain@brigny.fr"
        )
        alain_mailbox.contact = alain_contact
        alain_mailbox.save()
        factories.ContactFactory.create_batch(
            3, mailbox=alain_mailbox
        )  # alain has 3 contacts in his mailbox

        michel_mailbox = factories.MailboxFactory(
            local_part="michel.chatel", domain=brigny_domain
        )
        michel_contact = factories.ContactFactory(
            name="Michel Chatel", email="michel@brigny.fr"
        )
        michel_mailbox.contact = michel_contact
        michel_mailbox.save()
        factories.ContactFactory.create_batch(
            3, mailbox=michel_mailbox
        )  # michel has 3 contacts in his mailbox

        sam_mailbox = factories.MailboxFactory(
            local_part="sam.suffit", domain=brigny_domain
        )
        sam_contact = factories.ContactFactory(name="Sam Suffit", email="sam@brigny.fr")
        sam_mailbox.contact = sam_contact
        sam_mailbox.save()
        factories.ContactFactory.create_batch(
            3, mailbox=sam_mailbox
        )  # sam has 3 contacts in his mailbox
        sam_contact_of_dominique = factories.ContactFactory(
            name="Sam Suffit :)", email="sam@brigny.fr"
        )
        # Sam is Dominique contact too
        dominique_mailbox.contacts.add(sam_contact_of_dominique)

        # Dominique has some external contacts (other domains)
        cecile_contact = factories.ContactFactory(
            name="Cecile Troyen", email="cecile@otherdomain1.com"
        )
        dominique_mailbox.contacts.add(cecile_contact)
        jean_contact = factories.ContactFactory(
            name="Jean Bon", email="jean@otherdomain2.com"
        )
        dominique_mailbox.contacts.add(jean_contact)
        # Dominique wrote to them all; the mailboxes of the domain need not.
        for contact in (sam_contact_of_dominique, cecile_contact, jean_contact):
            written_to(contact)

        # create random contacts
        factories.ContactFactory.create_batch(10)

        # Filter by first mailbox - should return all mailboxes contacts and contacts
        # from same domain as user authenticated
        # search by name
        with django_assert_num_queries(2):
            response = client.get(
                reverse("contacts-list"), {"mailbox_id": str(dominique_mailbox.id)}
            )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 6  # 4 internal contacts + 2 external contacts
        contact_ids = {c["id"] for c in response.data}
        assert contact_ids == {
            str(dominique_contact.id),
            str(alain_contact.id),
            str(michel_contact.id),
            str(cecile_contact.id),
            str(jean_contact.id),
            str(sam_contact_of_dominique.id),
        }

        # search by name
        response = client.get(reverse("contacts-list"), {"q": "Verse"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        with django_assert_num_queries(2):
            response = client.get(
                reverse("contacts-list"),
                {"mailbox_id": str(dominique_mailbox.id), "q": "Troyen"},
            )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        contact_ids = {c["id"] for c in response.data}
        assert contact_ids == {str(cecile_contact.id)}

    def test_list_contacts_search_by_name(self):
        """Test searching contacts by name (multi-words)."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        contact1 = factories.ContactFactory(
            mailbox=mailbox, name="John Doe", email="john@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=mailbox, name="Jane Doe", email="jane@example.com"
        )
        contact3 = factories.ContactFactory(
            mailbox=mailbox, name="Bob Smith", email="bob@example.com"
        )
        for contact in (contact1, contact2, contact3):
            written_to(contact)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # One word : "Doe" => both Doe
        response = client.get(reverse("contacts-list"), {"q": "Doe"})
        assert response.status_code == 200
        assert {c["id"] for c in response.data} == {str(contact1.id), str(contact2.id)}
        # Two words : "Jane Doe" => only Jane Doe
        response = client.get(reverse("contacts-list"), {"q": "Jane Doe"})
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact2.id)
        # Two words : "Doe John" => only John Doe
        response = client.get(reverse("contacts-list"), {"q": "Doe John"})
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)

    def test_list_contacts_search_by_email(self):
        """Test searching contacts by email (multi-words)."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        contact1 = factories.ContactFactory(
            mailbox=mailbox, name="John Doe", email="john.doe@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=mailbox, name="Jane Smith", email="jane.smith@example.com"
        )
        contact3 = factories.ContactFactory(
            mailbox=mailbox, name="Bob Wilson", email="bob.wilson@test.com"
        )
        for contact in (contact1, contact2, contact3):
            written_to(contact)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # One word : "example.com" => both Doe
        response = client.get(reverse("contacts-list"), {"q": "example.com"})
        assert response.status_code == 200
        assert {c["id"] for c in response.data} == {str(contact1.id), str(contact2.id)}
        # Two words : "jane example.com" => only Jane Smith
        response = client.get(reverse("contacts-list"), {"q": "jane example.com"})
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact2.id)
        # Two words : "john example.com" => only John Doe
        response = client.get(reverse("contacts-list"), {"q": "john example.com"})
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)

    def test_list_contacts_search_combined(self):
        """Test searching contacts with both mailbox filter and multi-words search query."""
        authenticated_user = factories.UserFactory()
        mailbox1 = factories.MailboxFactory()
        mailbox2 = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox1,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        factories.MailboxAccessFactory(
            mailbox=mailbox2,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        contact1 = factories.ContactFactory(
            mailbox=mailbox1, name="John Doe", email="john@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=mailbox2, name="John Smith", email="john@test.com"
        )
        written_to(contact1)
        written_to(contact2)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # One word : "John" in mailbox1 => John Doe
        response = client.get(
            reverse("contacts-list"), {"mailbox_id": str(mailbox1.id), "q": "John"}
        )
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)
        # Two words : "John Doe" in mailbox1 => John Doe
        response = client.get(
            reverse("contacts-list"), {"mailbox_id": str(mailbox1.id), "q": "John Doe"}
        )
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)
        # Two words : "John test.com" in mailbox2 => John Smith
        response = client.get(
            reverse("contacts-list"),
            {"mailbox_id": str(mailbox2.id), "q": "John test.com"},
        )
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact2.id)

    def test_list_contacts_search_multiword(self):
        """Test searching contacts with three words (first name, last name, email domain)."""
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )
        contact1 = factories.ContactFactory(
            mailbox=mailbox, name="John Doe", email="john.doe@example.com"
        )
        contact2 = factories.ContactFactory(
            mailbox=mailbox, name="Jane Doe", email="jane.doe@example.com"
        )
        written_to(contact1)
        written_to(contact2)
        client = APIClient()
        client.force_authenticate(user=authenticated_user)
        # Three words : "John Doe example.com" => only John Doe
        response = client.get(reverse("contacts-list"), {"q": "John Doe example.com"})
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(contact1.id)

    def test_list_contacts_no_results(self):
        """Test when no contacts match the search criteria."""
        # Create authenticated user with access to a mailbox
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Create a contact
        written_to(
            factories.ContactFactory(
                mailbox=mailbox, name="John Doe", email="john@example.com"
            )
        )

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Search for non-existent contact
        response = client.get(reverse("contacts-list"), {"q": "nonexistent"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_retrieve_contact(self):
        """Test retrieving a specific contact."""
        # Create authenticated user with access to a mailbox
        authenticated_user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=authenticated_user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        # Create a contact
        contact = factories.ContactFactory(
            mailbox=mailbox, name="John Doe", email="john@example.com"
        )

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=authenticated_user)

        # Retrieve the contact
        response = client.get(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)})
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "id": str(contact.id),
            "name": "John Doe",
            "email": "john@example.com",
        }

    def test_retrieve_contact_unauthorized(self):
        """Anonymous user cannot retrieve a contact."""
        contact = factories.ContactFactory()
        client = APIClient()
        response = client.get(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)})
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_retrieve_contact_no_access(self):
        """User without access to the mailbox cannot retrieve the contact."""
        # Create user without access to the mailbox
        user = factories.UserFactory()
        contact = factories.ContactFactory()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Try to retrieve the contact
        response = client.get(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestContactHide:
    """Test hiding a contact from the recipient suggestions, and showing it again."""

    @staticmethod
    def _client_for_mailbox(mailbox, role=models.MailboxRoleChoices.EDITOR):
        """Return a client authenticated as a user holding `role` on `mailbox`."""
        user = factories.UserFactory()
        factories.MailboxAccessFactory(mailbox=mailbox, user=user, role=role)
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    @staticmethod
    def _patch(client, contact, data):
        """Call the partial update endpoint on `contact`."""
        return client.patch(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)}),
            data,
            format="json",
        )

    def _hide(self, client, contact):
        """Hide `contact` from the suggestions."""
        return self._patch(client, contact, {"is_suggested": False})

    @staticmethod
    def _listed(client, mailbox):
        """Return the `is_suggested` flag of each email the list endpoint returns."""
        response = client.get(reverse("contacts-list"), {"mailbox_id": str(mailbox.id)})
        assert response.status_code == status.HTTP_200_OK
        return {contact["email"]: contact["is_suggested"] for contact in response.data}

    def _suggested_emails(self, client, mailbox):
        """Return the emails the list endpoint suggests for `mailbox`."""
        return [
            email
            for email, suggested in self._listed(client, mailbox).items()
            if suggested
        ]

    def test_show_contact_again(self):
        """Showing a hidden contact suggests it again."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = written_to(
            factories.ContactFactory(mailbox=mailbox, email="old@example.com")
        )
        self._hide(client, contact)

        response = self._patch(client, contact, {"is_suggested": True})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_suggested"] is True
        assert self._suggested_emails(client, mailbox) == ["old@example.com"]

    def test_hidden_contact_is_listed_for_search(self):
        """A hidden contact stays listed, flagged, so a search can still find it."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = written_to(
            factories.ContactFactory(mailbox=mailbox, email="old@example.com")
        )

        response = self._hide(client, contact)

        assert response.data == {
            "id": str(contact.id),
            "name": contact.name,
            "email": "old@example.com",
            "is_suggested": False,
            "can_change_suggestion": True,
        }
        assert self._listed(client, mailbox) == {"old@example.com": False}

    def test_update_contact_payload(self):
        """Only the flag can change: a missing one is a no-op, a wrong one a 400."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox, email="old@example.com")

        response = self._patch(client, contact, {"email": "new@example.com"})
        assert response.status_code == status.HTTP_200_OK
        response = self._patch(client, contact, {"is_suggested": "maybe"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        contact.refresh_from_db()
        assert contact.email == "old@example.com"
        assert contact.is_suggested is True

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_contact_cannot_be_replaced_or_deleted(self, method):
        """A contact is referenced by its messages: deleting it would delete them."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox)

        response = getattr(client, method)(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)})
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert models.Contact.objects.filter(pk=contact.pk).exists()

    def test_hide_contact_anonymous(self):
        """Anonymous user cannot hide a contact."""
        contact = factories.ContactFactory()
        response = self._hide(APIClient(), contact)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        contact.refresh_from_db()
        assert contact.is_suggested is True

    def test_hide_contact_no_access(self):
        """A contact of a mailbox the user cannot access does not exist for them."""
        client = self._client_for_mailbox(factories.MailboxFactory())
        contact = factories.ContactFactory()
        response = self._hide(client, contact)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        contact.refresh_from_db()
        assert contact.is_suggested is True

    def test_hide_contact_viewer(self):
        """A viewer of the mailbox cannot change what its users get suggested."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox, models.MailboxRoleChoices.VIEWER)
        contact = factories.ContactFactory(mailbox=mailbox)
        response = self._hide(client, contact)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        contact.refresh_from_db()
        assert contact.is_suggested is True

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
            models.MailboxRoleChoices.ADMIN,
        ],
    )
    def test_hide_contact_hides_it_from_suggestions(self, role):
        """The contact stops being suggested, for every user of the mailbox."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox, role)
        other_client = self._client_for_mailbox(mailbox)
        contact = written_to(
            factories.ContactFactory(mailbox=mailbox, email="old@example.com")
        )
        written_to(factories.ContactFactory(mailbox=mailbox, email="kept@example.com"))

        response = self._hide(client, contact)

        assert response.status_code == status.HTTP_200_OK
        contact.refresh_from_db()
        assert contact.is_suggested is False
        assert self._suggested_emails(client, mailbox) == ["kept@example.com"]
        assert self._suggested_emails(other_client, mailbox) == ["kept@example.com"]

    def test_hide_contact_keeps_its_messages(self):
        """Neither the messages it sent nor the ones it received are affected."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox)
        thread = factories.ThreadFactory()
        sent = factories.MessageFactory(thread=thread, sender=contact)
        received = factories.MessageFactory(thread=thread)
        recipient = factories.MessageRecipientFactory(message=received, contact=contact)

        response = self._hide(client, contact)

        assert response.status_code == status.HTTP_200_OK
        assert models.Contact.objects.filter(pk=contact.pk).exists()
        assert models.Message.objects.filter(pk=sent.pk).exists()
        assert models.MessageRecipient.objects.filter(pk=recipient.pk).exists()

    def test_hide_contact_with_a_legacy_invalid_address(self):
        """An address the current validator refuses can still be hidden."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox)
        models.Contact.objects.filter(pk=contact.pk).update(email="not, an address")

        response = self._hide(client, contact)

        assert response.status_code == status.HTTP_200_OK
        contact.refresh_from_db()
        assert contact.is_suggested is False

    def test_hide_contact_already_hidden(self):
        """Hiding twice (e.g. from two tabs) is not an error."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox)

        for _ in range(2):
            response = self._hide(client, contact)
            assert response.status_code == status.HTTP_200_OK

    def test_hidden_contact_is_still_retrievable(self):
        """Hiding only concerns suggestions."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox)
        contact = factories.ContactFactory(mailbox=mailbox)
        self._hide(client, contact)

        response = client.get(
            reverse("contacts-detail", kwargs={"pk": str(contact.id)})
        )
        assert response.status_code == status.HTTP_200_OK

    def test_saving_a_draft_does_not_bring_a_hidden_contact_back(self):
        """Only sending counts: a draft may never leave, or still carry a typo."""
        mailbox = factories.MailboxFactory()
        client = self._client_for_mailbox(mailbox, models.MailboxRoleChoices.SENDER)
        contact = factories.ContactFactory(mailbox=mailbox, email="old@example.com")
        self._hide(client, contact)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        draft = factories.MessageFactory(
            thread=thread,
            sender=factories.ContactFactory(mailbox=mailbox),
            is_draft=True,
        )

        response = client.put(
            reverse("draft-message-detail", kwargs={"message_id": draft.id}),
            {
                "senderId": str(mailbox.id),
                "subject": "Draft",
                "to": ["old@example.com"],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert draft.recipients.get().contact_id == contact.id
        contact.refresh_from_db()
        assert contact.is_suggested is False

    @patch("core.mda.spam.call_rspamd", return_value=("no action", None, None))
    def test_receiving_a_message_does_not_bring_a_hidden_contact_back(self, _rspamd):
        """A sender the user got rid of must not return by writing again."""
        mailbox = factories.MailboxFactory()
        contact = factories.ContactFactory(mailbox=mailbox, email="old@example.com")
        self._hide(self._client_for_mailbox(mailbox), contact)
        recipient = str(mailbox)

        with patch("core.mda.inbound.process_inbound_message_task.delay"):
            delivered = deliver_inbound_message(
                recipient,
                {
                    "subject": "Hello again",
                    "from": [{"email": "old@example.com"}],
                    "to": [{"email": recipient}],
                    "messageId": ["hello.again@example.com"],
                    "sentAt": timezone.now().isoformat(),
                    "headers": [],
                    "ext": {"headersBlocks": []},
                },
                (
                    f"From: old@example.com\r\nTo: {recipient}\r\n"
                    "Subject: Hello again\r\n\r\nBody\r\n"
                ).encode(),
            )
        assert delivered is True
        inbound = models.InboundMessage.objects.get(mailbox=mailbox)
        process_inbound_message_task.apply(args=[str(inbound.id)])

        message = models.Message.objects.get(thread__accesses__mailbox=mailbox)
        assert message.sender_id == contact.id
        contact.refresh_from_db()
        assert contact.is_suggested is False

    def test_hidden_address_does_not_come_back_through_the_domain(self):
        """The copy a colleague's mailbox holds must not replace the hidden one."""
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        colleague = factories.MailboxFactory(domain=domain)
        colleague.contact = factories.ContactFactory(
            mailbox=colleague, email="colleague@example.com"
        )
        colleague.save()
        local_copy = written_to(
            factories.ContactFactory(mailbox=mailbox, email="colleague@example.com")
        )
        client = self._client_for_mailbox(mailbox)
        assert self._suggested_emails(client, mailbox) == ["colleague@example.com"]

        self._hide(client, local_copy)

        assert self._suggested_emails(client, mailbox) == []

    def test_hiding_stays_scoped_to_the_mailbox(self):
        """A colleague hiding their own address leaves the domain's directory as is."""
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        colleague = factories.MailboxFactory(domain=domain)
        colleague.contact = factories.ContactFactory(
            mailbox=colleague, email="colleague@example.com"
        )
        colleague.save()
        self._hide(self._client_for_mailbox(colleague), colleague.contact)

        client = self._client_for_mailbox(mailbox)
        # Listed as suggested: it must not land among the hidden suggestions.
        assert self._listed(client, mailbox) == {"colleague@example.com": True}

    def test_list_tells_which_contacts_can_be_hidden(self):
        """Only the contacts of the selected mailbox, for those who can edit it."""
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        colleague = factories.MailboxFactory(domain=domain)
        colleague.contact = factories.ContactFactory(
            mailbox=colleague, email="colleague@example.com"
        )
        colleague.save()
        written_to(factories.ContactFactory(mailbox=mailbox, email="own@example.com"))

        user = factories.UserFactory()
        access = factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        # Editing the mailbox owning the domain entry does not make it hideable
        # from another mailbox: it would not leave the list of the latter.
        factories.MailboxAccessFactory(
            mailbox=colleague, user=user, role=models.MailboxRoleChoices.EDITOR
        )
        client = APIClient()
        client.force_authenticate(user=user)

        def can_change_by_email():
            response = client.get(
                reverse("contacts-list"), {"mailbox_id": str(mailbox.id)}
            )
            return {c["email"]: c["can_change_suggestion"] for c in response.data}

        assert can_change_by_email() == {
            "colleague@example.com": False,
            "own@example.com": True,
        }

        access.role = models.MailboxRoleChoices.VIEWER
        access.save()
        assert can_change_by_email() == {
            "colleague@example.com": False,
            "own@example.com": False,
        }
