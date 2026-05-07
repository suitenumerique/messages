"""End-to-end tests for the OpenSearch search functionality."""

# pylint: disable=too-many-positional-arguments,unused-argument
import time
from unittest.mock import patch

from django.conf import settings
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from core import enums, models
from core.factories import (
    ContactFactory,
    MailboxAccessFactory,
    MailboxFactory,
    MessageFactory,
    MessageRecipientFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)
from core.services.search import (
    create_index_if_not_exists,
    delete_index,
    get_opensearch_client,
)
from core.services.search.coalescer import process_pending_reindex
from core.services.search.index import reindex_bulk_threads
from core.services.search.mapping import MESSAGE_INDEX


@pytest.fixture(name="setup_search")
def fixture_setup_search():
    """Setup OpenSearch index for testing."""

    delete_index()
    create_index_if_not_exists()

    # Check if OpenSearch is actually available
    es = get_opensearch_client()

    # pylint: disable=unexpected-keyword-arg
    es.cluster.health(wait_for_status="yellow", timeout=10)
    yield

    # Teardown
    try:
        delete_index()
    # pylint: disable=broad-exception-caught
    except Exception:
        pass


@pytest.fixture(name="test_user")
def fixture_test_user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture(name="test_mailbox")
def fixture_test_mailbox(test_user):
    """Create a test mailbox with user access."""
    mailbox = MailboxFactory()
    MailboxAccessFactory(user=test_user, mailbox=mailbox)
    return mailbox


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
        """Wait for indexing to complete by refreshing the index.

        Drains the coalescing buffers (reindex + delete) first so any thread
        IDs queued by signal handlers are handed off to the bulk tasks. Under
        ``CELERY_TASK_ALWAYS_EAGER=True`` the tasks run synchronously, which
        makes the documents visible as soon as OpenSearch refreshes.
        """
        process_pending_reindex()
        es = get_opensearch_client()
        for _ in range(max_retries):
            try:
                es.indices.refresh(index=MESSAGE_INDEX)
                return True
            # pylint: disable=broad-exception-caught
            except Exception:
                time.sleep(delay)
        return False

    return _wait


@pytest.fixture(name="create_test_thread")
def fixture_create_test_thread(test_mailbox, wait_for_indexing):
    """Create a function to create test threads with messages."""

    def _create_thread_with_message(
        subject="Test Subject", content="Test content for search", mailbox=None
    ):
        """Create a thread with a message containing the given subject and content."""
        contact1 = ContactFactory(
            name="John Doe", email="john@example.com", mailbox=mailbox or test_mailbox
        )
        contact2 = ContactFactory(
            name="Jane Smith", email="jane@example.com", mailbox=mailbox or test_mailbox
        )

        thread = ThreadFactory(subject=subject)
        ThreadAccessFactory(thread=thread, mailbox=mailbox or test_mailbox)

        message = MessageFactory(
            thread=thread,
            subject=subject,
            sender=contact1,
            raw_mime=(
                f"From: {contact1.email}\r\n"
                f"To: {contact2.email}\r\n"
                f"Subject: {subject}\r\n"
                f"Content-Type: text/plain\r\n\r\n"
                f"{content}"
            ).encode("utf-8"),
        )

        MessageRecipientFactory(
            message=message, contact=contact2, type=enums.MessageRecipientTypeChoices.TO
        )

        # Wait for indexing to complete
        wait_for_indexing()

        return thread, message

    return _create_thread_with_message


@pytest.mark.skipif(
    len(settings.OPENSEARCH_HOSTS) == 0,
    reason="OpenSearch is not configured",
)
@pytest.mark.redis
@pytest.mark.django_db(transaction=True)
class TestSearchE2E:
    """End-to-end tests for OpenSearch search functionality.

    Marked ``@pytest.mark.redis``: ``wait_for_indexing`` invokes
    ``process_pending_reindex``, which drains the Redis-backed
    coalescing buffers populated by signal handlers during the test
    body. Without ``redis_cache`` the enqueues silently warn-and-skip
    and OpenSearch never sees the new threads.
    """

    @pytest.fixture(autouse=True)
    def _redis_cache(self, redis_cache):
        pass

    def test_search_thread_by_subject(
        self, setup_search, api_client, test_url, create_test_thread
    ):
        """Test searching for a thread by its subject."""
        # Create a thread with a specific subject
        thread, _ = create_test_thread(
            subject="Meeting Agenda", content="Let's discuss the project status"
        )

        # Search for the thread
        response = api_client.get(f"{test_url}?search=Meeting")

        # Verify response
        assert response.status_code == 200

        # Check if the thread is found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids

    def test_search_thread_by_message_content(
        self, setup_search, api_client, test_url, create_test_thread
    ):
        """Test searching for a thread by message content."""
        # Create a thread with specific content
        thread, _ = create_test_thread(
            subject="Status Update", content="The project is making good progress"
        )

        # Search for the thread
        response = api_client.get(f"{test_url}?search=progress")

        # Verify response
        assert response.status_code == 200

        # Check if the thread is found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids

    def test_search_with_filters(
        self,
        setup_search,
        api_client,
        test_url,
        create_test_thread,
        wait_for_indexing,
    ):
        """Test searching with is:unread filter.

        The thread's ThreadAccess has read_at=None by default (unread),
        so the has_parent filter on unread_mailboxes should match it.
        """
        thread, _ = create_test_thread(
            subject="Important Notification", content="Please review the document"
        )
        response = api_client.get(f"{test_url}?search=Notification is:unread")

        assert response.status_code == 200
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids

    def test_reindex_preserves_message_when_doc_build_fails(
        self,
        setup_search,
        api_client,
        test_url,
        create_test_thread,
        test_mailbox,
        wait_for_indexing,
    ):
        """A message whose doc cannot be rebuilt must not be evicted from the index.

        ``reindex_bulk_threads`` is now pure upsert: it never deletes.
        When ``_build_message_doc`` returns ``None`` (parse error on a
        blob, for instance), the existing index entry stays untouched —
        which is closer to the truth than dropping the message until the
        blob becomes parsable again. Deletes only fire from
        ``post_delete`` signals via ``bulk_delete_messages_task``.
        """
        # pylint: disable-next=import-outside-toplevel

        thread, message = create_test_thread(
            subject="Purge Guard", content="A specific phrase we can search for"
        )

        # Sanity check: the message is searchable before the simulated failure.
        response = api_client.get(f"{test_url}?search=specific")
        assert str(thread.id) in [t["id"] for t in response.data["results"]]

        failing_message_id = str(message.id)

        def selective_build(msg, *args, **kwargs):  # pylint: disable=unused-argument
            if str(msg.id) == failing_message_id:
                return None
            return {"dummy": True}

        with patch(
            "core.services.search.index._build_message_doc",
            side_effect=selective_build,
        ):
            reindex_bulk_threads(models.Thread.objects.filter(id=thread.id))

        wait_for_indexing()

        es = get_opensearch_client()
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(
            index=MESSAGE_INDEX, id=failing_message_id, routing=str(thread.id)
        ), "Message doc was purged despite its DB row still existing"

    def test_delete_message_removes_only_that_doc_from_index(
        self,
        setup_search,
        create_test_thread,
        wait_for_indexing,
        test_mailbox,
    ):
        """Suppression d'un Message : son doc enfant disparaît, le reste survit.

        Régression-guard sur le nouveau pipeline ``Message.post_delete`` →
        ``enqueue_message_delete`` → ``bulk_delete_messages_task`` (bulk
        delete by ``_id`` avec ``_routing=thread_id``). Remplace l'ancien
        ``_purge_orphan_docs`` qui passait par ``delete_by_query``.
        """
        thread, kept_message = create_test_thread(
            subject="Delete Probe", content="Keep me"
        )

        contact = ContactFactory(
            name="Carol", email="carol@example.com", mailbox=test_mailbox
        )
        deleted_message = MessageFactory(
            thread=thread,
            subject="Doomed",
            sender=contact,
            raw_mime=(
                f"From: {contact.email}\r\n"
                f"To: {contact.email}\r\n"
                f"Subject: Doomed\r\n"
                f"Content-Type: text/plain\r\n\r\n"
                f"This message will be deleted"
            ).encode("utf-8"),
        )
        MessageRecipientFactory(
            message=deleted_message,
            contact=contact,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        wait_for_indexing()

        es = get_opensearch_client()
        thread_id = str(thread.id)
        deleted_id = str(deleted_message.id)
        kept_id = str(kept_message.id)

        # Sanity: parent + both children visible before the delete.
        assert es.exists(index=MESSAGE_INDEX, id=thread_id)
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(index=MESSAGE_INDEX, id=deleted_id, routing=thread_id)
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(index=MESSAGE_INDEX, id=kept_id, routing=thread_id)

        deleted_message.delete()
        wait_for_indexing()

        # pylint: disable-next=unexpected-keyword-arg
        assert not es.exists(index=MESSAGE_INDEX, id=deleted_id, routing=thread_id), (
            "Deleted message doc should be gone after the bulk delete pass"
        )
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(index=MESSAGE_INDEX, id=kept_id, routing=thread_id), (
            "Sibling message doc must not be touched"
        )
        assert es.exists(index=MESSAGE_INDEX, id=thread_id), (
            "Parent thread doc must not be touched"
        )

    def test_delete_thread_cascades_to_child_message_docs(
        self,
        setup_search,
        create_test_thread,
        wait_for_indexing,
        test_mailbox,
    ):
        """Suppression d'un Thread : le doc parent ET tous les docs enfants partent.

        ``bulk_delete_threads_task`` n'efface plus que le parent par ``_id`` ;
        les enfants doivent partir via les ``post_delete`` cascadés sur
        ``Message`` qui alimentent ``bulk_delete_messages_task``. En bonus,
        ce test couvre l'invariant « delete wins » : le ``post_delete`` sur
        ``ThreadAccess`` (cascadé lui aussi) enqueue un reindex pour ce
        thread, qui doit être filtré par le pass delete dans le même cycle
        — sinon on recréerait le doc parent juste après l'avoir supprimé.
        """
        thread, first_message = create_test_thread(
            subject="Cascade Probe", content="First message"
        )

        contact = ContactFactory(
            name="Dave", email="dave@example.com", mailbox=test_mailbox
        )
        second_message = MessageFactory(
            thread=thread,
            subject="Cascade Probe",
            sender=contact,
            raw_mime=(
                f"From: {contact.email}\r\n"
                f"To: {contact.email}\r\n"
                f"Subject: Cascade Probe\r\n"
                f"Content-Type: text/plain\r\n\r\n"
                f"Second message"
            ).encode("utf-8"),
        )
        MessageRecipientFactory(
            message=second_message,
            contact=contact,
            type=enums.MessageRecipientTypeChoices.TO,
        )
        wait_for_indexing()

        es = get_opensearch_client()
        thread_id = str(thread.id)
        first_id = str(first_message.id)
        second_id = str(second_message.id)

        assert es.exists(index=MESSAGE_INDEX, id=thread_id)
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(index=MESSAGE_INDEX, id=first_id, routing=thread_id)
        # pylint: disable-next=unexpected-keyword-arg
        assert es.exists(index=MESSAGE_INDEX, id=second_id, routing=thread_id)

        thread.delete()
        wait_for_indexing()

        assert not es.exists(index=MESSAGE_INDEX, id=thread_id), (
            "Parent thread doc should be gone"
        )
        # pylint: disable-next=unexpected-keyword-arg
        assert not es.exists(index=MESSAGE_INDEX, id=first_id, routing=thread_id), (
            "First child message doc should be swept by cascaded post_delete"
        )
        # pylint: disable-next=unexpected-keyword-arg
        assert not es.exists(index=MESSAGE_INDEX, id=second_id, routing=thread_id), (
            "Second child message doc should be swept by cascaded post_delete"
        )

    def test_multiple_threads_in_search_results(
        self,
        setup_search,
        api_client,
        test_url,
        create_test_thread,
        test_mailbox,
    ):
        """Test that multiple relevant threads are returned in search results."""
        # Create two threads with the same keyword
        thread1, _ = create_test_thread(
            subject="Project Alpha",
            content="This is about project",
            mailbox=test_mailbox,
        )
        thread2, _ = create_test_thread(
            subject="Project Beta",
            content="Another project update",
            mailbox=test_mailbox,
        )

        # Search for the threads
        response = api_client.get(f"{test_url}?search=project")

        # Verify response
        assert response.status_code == 200

        # Check if both threads are found
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread1.id) in thread_ids
        assert str(thread2.id) in thread_ids
        assert len(thread_ids) >= 2
