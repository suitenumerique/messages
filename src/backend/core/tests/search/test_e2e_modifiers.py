"""End-to-end tests for Gmail-style search modifiers."""
# pylint: disable=unused-argument

import time

from django.conf import settings
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from core import enums
from core.factories import (
    ContactFactory,
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    MessageFactory,
    MessageRecipientFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)
from core.search import create_index_if_not_exists, delete_index, get_es_client
from core.search.mapping import MESSAGE_INDEX


@pytest.fixture(name="setup_elasticsearch")
def fixture_setup_elasticsearch():
    """Setup Elasticsearch index for testing."""
    try:
        delete_index()
        create_index_if_not_exists()

        # Check if Elasticsearch is actually available
        es = get_es_client()

        # pylint: disable=unexpected-keyword-arg
        es.cluster.health(wait_for_status="yellow", timeout="10s")
        yield
    # pylint: disable=broad-exception-caught
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Elasticsearch is not available: {e}")

    # Teardown
    try:
        delete_index()
    # pylint: disable=broad-exception-caught
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(name="test_user")
def fixture_test_user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture(name="test_mailboxes")
def fixture_test_mailboxes(test_user):
    """Create test mailboxes."""
    domain = MailDomainFactory(name="example.com")
    mailbox1 = MailboxFactory(local_part="mailbox1", domain=domain)
    mailbox2 = MailboxFactory(local_part="mailbox2", domain=domain)
    MailboxAccessFactory(user=test_user, mailbox=mailbox1)
    MailboxAccessFactory(user=test_user, mailbox=mailbox2)
    return mailbox1, mailbox2


@pytest.fixture(name="api_client")
def fixture_api_client(test_user):
    """Create an authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=test_user)
    return client


@pytest.fixture(name="test_url")
def fixture_test_url():
    """Get the thread list API URL."""
    return reverse("threads-list")


@pytest.fixture(name="wait_for_indexing")
def fixture_wait_for_indexing():
    """Fixture to create a function that waits for indexing to complete."""

    def _wait(max_retries=10, delay=0.5):
        """Wait for indexing to complete by refreshing the index."""
        es = get_es_client()
        for _ in range(max_retries):
            try:
                es.indices.refresh(index=MESSAGE_INDEX)
                return True
            # pylint: disable=broad-exception-caught
            except Exception:  # noqa: BLE001
                time.sleep(delay)
        return False

    return _wait


@pytest.fixture(name="test_threads")
def fixture_test_threads(test_mailboxes, wait_for_indexing):
    """Create test threads with various configurations for testing modifiers."""
    mailbox1, mailbox2 = test_mailboxes

    contact1 = ContactFactory(
        email="john@example.com", mailbox=mailbox1, name="John Smith"
    )
    contact2 = ContactFactory(
        email="sarah@example.com", mailbox=mailbox1, name="Sarah Johnson"
    )
    contact3 = ContactFactory(
        email="robert@example.com", mailbox=mailbox1, name="Robert Brown"
    )
    contact4 = ContactFactory(
        email="maria@example.com", mailbox=mailbox1, name="Maria Garcia"
    )

    # Thread 1: Standard thread with basic content
    thread1 = ThreadFactory(subject="Meeting Agenda")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message1 = MessageFactory(
        thread=thread1,
        subject="Meeting Agenda",
        sender=contact1,
        raw_mime=(
            f"From: {contact1.email}\r\n"
            f"To: {contact2.email}\r\n"
            f"Subject: Meeting Agenda\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"Let's discuss the project status on Monday."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message1, contact=contact2, type="to")

    # Thread 2: Thread with CC and BCC recipients
    thread2 = ThreadFactory(subject="Team Update")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message2 = MessageFactory(
        thread=thread2,
        subject="Team Update",
        sender=contact2,
        raw_mime=(
            f"From: {contact2.email}\r\n"
            f"To: {contact1.email}\r\n"
            f"Cc: {contact3.email}\r\n"
            f"Bcc: {contact4.email}\r\n"
            f"Subject: Team Update\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"Here's the weekly team update with project progress."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message2, contact=contact1, type="to")
    MessageRecipientFactory(message=message2, contact=contact3, type="cc")
    MessageRecipientFactory(message=message2, contact=contact4, type="bcc")

    # Thread 3: Draft message
    thread3 = ThreadFactory(subject="Draft Report")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread3, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message3 = MessageFactory(
        thread=thread3,
        subject="Draft Report",
        sender=contact1,
        is_draft=True,
        raw_mime=(
            f"From: {contact1.email}\r\n"
            f"To: {contact2.email}\r\n"
            f"Subject: Draft Report\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"This is a draft of the quarterly report."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message3, contact=contact2, type="to")

    # Thread 4: Trashed message
    thread4 = ThreadFactory(subject="Old Newsletter")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread4, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message4 = MessageFactory(
        thread=thread4,
        subject="Old Newsletter",
        sender=contact3,
        is_trashed=True,
        raw_mime=(
            f"From: {contact3.email}\r\n"
            f"To: {contact1.email}\r\n"
            f"Subject: Old Newsletter\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"This is last month's newsletter that should be in trash."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message4, contact=contact1, type="to")

    # Thread 5: Starred and read message
    thread5 = ThreadFactory(subject="Important Announcement")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread5, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message5 = MessageFactory(
        thread=thread5,
        subject="Important Announcement",
        sender=contact4,
        is_starred=True,
        is_unread=False,
        raw_mime=(
            f"From: {contact4.email}\r\n"
            f"To: {contact1.email}\r\n"
            f"Subject: Important Announcement\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"Please note that our office will be closed next Monday for maintenance."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message5, contact=contact1, type="to")

    # Thread 6: Unread message
    thread6 = ThreadFactory(subject="New Notification")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread6, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message6 = MessageFactory(
        thread=thread6,
        subject="New Notification",
        sender=contact3,
        is_unread=True,
        raw_mime=(
            f"From: {contact3.email}\r\n"
            f"To: {contact1.email}\r\n"
            f"Subject: New Notification\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"You have a new notification from the system."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message6, contact=contact1, type="to")

    # Thread 7: For testing exact phrases
    thread7 = ThreadFactory(subject="Project Feedback")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread7, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message7 = MessageFactory(
        thread=thread7,
        subject="Project Feedback",
        sender=contact2,
        raw_mime=(
            f"From: {contact2.email}\r\n"
            f"To: {contact1.email}\r\n"
            f"Subject: Project Feedback\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"The client provided positive feedback about the new interface design."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message7, contact=contact1, type="to")

    # Thread 8: For testing in second mailbox
    thread8 = ThreadFactory(subject="Different Mailbox Message")
    ThreadAccessFactory(
        mailbox=mailbox2, thread=thread8, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message8 = MessageFactory(
        thread=thread8,
        subject="Different Mailbox Message",
        sender=contact1,
        raw_mime=(
            f"From: {contact1.email}\r\n"
            f"To: {contact2.email}\r\n"
            f"Subject: Different Mailbox Message\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"This message is in a different mailbox for testing."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message8, contact=contact2, type="to")

    # Thread 9: For testing sent messages
    thread9 = ThreadFactory(subject="Sent Message")
    ThreadAccessFactory(
        mailbox=mailbox1, thread=thread9, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    message9 = MessageFactory(
        thread=thread9,
        subject="Sent Message",
        sender=contact1,  # Same as the user's primary contact
        is_sender=True,
        raw_mime=(
            f"From: {contact1.email}\r\n"
            f"To: {contact3.email}\r\n"
            f"Subject: Sent Message\r\n"
            f"Content-Type: text/plain\r\n\r\n"
            f"This is a message that was sent by the user."
        ).encode("utf-8"),
    )
    MessageRecipientFactory(message=message9, contact=contact3, type="to")

    # Wait for indexing to complete
    wait_for_indexing()

    return {
        "thread1": thread1,
        "thread2": thread2,
        "thread3": thread3,
        "thread4": thread4,
        "thread5": thread5,
        "thread6": thread6,
        "thread7": thread7,
        "thread8": thread8,
        "thread9": thread9,
    }


@pytest.mark.skipif(
    "elasticsearch" not in settings.ELASTICSEARCH_HOSTS[0],
    reason="Elasticsearch is not available",
)
@pytest.mark.django_db
class TestSearchModifiersE2E:
    """End-to-end tests for Gmail-style search modifiers."""

    def test_basic_searches(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with empty query."""

        # No search
        response = api_client.get(f"{test_url}?search=")
        assert response.status_code == 200
        assert len(response.data["results"]) == 9

        # Now find all
        response = api_client.get(f"{test_url}?search=example")
        assert response.status_code == 200
        assert len(response.data["results"]) == 9

        # Now find none
        response = api_client.get(f"{test_url}?search=aozeigsdpfgoidosfgi")
        assert response.status_code == 200
        assert len(response.data["results"]) == 0

    def test_from_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'from:' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=from:john@example.com")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=de:john@example.com")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

        # Test partial name search
        response = api_client.get(f"{test_url}?search=from:John")

        # Verify correct threads are found
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

    def test_to_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'to:' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=to:sarah@example.com")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=à:sarah@example.com")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

    def test_cc_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'cc:' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=cc:robert@example.com")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread2"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=copie:robert@example.com")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread2"].id) in thread_ids

    def test_bcc_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'bcc:' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=bcc:maria@example.com")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread2"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=cci:maria@example.com")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread2"].id) in thread_ids

    def test_subject_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'subject:' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=subject:Meeting")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=sujet:Meeting")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

    def test_exact_phrase_search(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with quoted exact phrases."""
        response = api_client.get(f'{test_url}?search="positive feedback"')

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread7"].id) in thread_ids

        # Test with a phrase that shouldn't match
        response = api_client.get(f'{test_url}?search="no match phrase"')

        # Verify no results
        assert response.status_code == 200
        assert len(response.data["results"]) == 0

    def test_in_trash_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'in:trash' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=in:trash")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread4"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=dans:corbeille")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread4"].id) in thread_ids

    def test_in_sent_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'in:sent' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=in:sent")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread9"].id) in thread_ids

        # Test French version with accent
        response = api_client.get(f"{test_url}?search=dans:envoyés")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread9"].id) in thread_ids

        # Test French version without accent
        response = api_client.get(f"{test_url}?search=dans:envoyes")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread9"].id) in thread_ids

    def test_in_draft_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'in:draft' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=in:draft")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread3"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=dans:brouillons")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread3"].id) in thread_ids

    def test_is_starred_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'is:starred' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=is:starred")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread5"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=est:suivi")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread5"].id) in thread_ids

    def test_is_read_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'is:read' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=is:read")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread5"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=est:lu")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread5"].id) in thread_ids

    def test_is_unread_search_modifier(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with the 'is:unread' modifier."""
        # Test English version
        response = api_client.get(f"{test_url}?search=is:unread")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread6"].id) in thread_ids

        # Test French version
        response = api_client.get(f"{test_url}?search=est:nonlu")

        # Verify the same results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread6"].id) in thread_ids

    def test_multiple_modifiers_search(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with multiple modifiers."""
        # Combine from: and subject:
        response = api_client.get(
            f"{test_url}?search=from:john@example.com subject:Meeting"
        )

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread1"].id) in thread_ids

        # Combine is:unread and keyword search
        response = api_client.get(f"{test_url}?search=is:unread notification")

        # Verify correct results
        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread6"].id) in thread_ids

    def test_combined_text_and_modifier_search(
        self, setup_elasticsearch, api_client, test_url, test_threads
    ):
        """Test searching with both free text and modifiers."""
        # Search with text and from: modifier
        response = api_client.get(f"{test_url}?search=from:sarah@example.com feedback")

        # Verify response
        assert response.status_code == 200

        # Check if the correct threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(test_threads["thread7"].id) in thread_ids

        # Search with text and a modifier that doesn't match the text
        response = api_client.get(f"{test_url}?search=from:robert@example.com feedback")

        # Verify no results (Robert didn't send feedback)
        assert response.status_code == 200
        assert len(response.data["results"]) == 0
