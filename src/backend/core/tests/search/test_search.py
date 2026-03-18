"""Tests for the core.services.search module."""

from unittest import mock

from django.test import override_settings
from django.utils import timezone

import pytest

from core.factories import (
    BlobFactory,
    MailboxFactory,
    MessageFactory,
    ThreadAccessFactory,
    ThreadFactory,
)
from core.services.search import (
    create_index_if_not_exists,
    delete_index,
    index_message,
    index_thread,
    reindex_all,
    reindex_mailbox,
    search_threads,
    update_thread_mailbox_flags,
)
from core.services.search.index import (
    _build_message_doc,
    _build_thread_doc,
    _compute_unread_starred_from_accesses,
)


@pytest.fixture(name="mock_es_client_search")
def fixture_mock_es_client_search():
    """Mock the OpenSearch client."""
    with mock.patch(
        "core.services.search.search.get_opensearch_client"
    ) as mock_get_opensearch_client:
        mock_es = mock.MagicMock()
        # Setup standard mock returns
        mock_es.indices.exists.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}
        mock_es.indices.delete.return_value = {"acknowledged": True}

        # Setup search mock
        mock_es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}

        mock_get_opensearch_client.return_value = mock_es
        mock_es.reset_mock()
        yield mock_es


@pytest.fixture(name="mock_es_client_index")
def fixture_mock_es_client_index():
    """Mock the OpenSearch client."""
    with mock.patch(
        "core.services.search.index.get_opensearch_client"
    ) as mock_get_opensearch_client:
        mock_es = mock.MagicMock()
        # Setup standard mock returns
        mock_es.indices.exists.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}
        mock_es.indices.delete.return_value = {"acknowledged": True}

        # Setup search mock
        mock_es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}

        mock_get_opensearch_client.return_value = mock_es
        mock_es.reset_mock()
        yield mock_es


@pytest.fixture(name="test_thread")
def fixture_test_thread(test_mailbox):
    """Create a test thread with a message."""
    thread = ThreadFactory()
    ThreadAccessFactory(mailbox=test_mailbox, thread=thread)
    MessageFactory(thread=thread)
    return thread


@pytest.fixture(name="test_mailbox")
def fixture_test_mailbox():
    """Create a test mailbox."""
    return MailboxFactory()


def test_create_index_if_not_exists(mock_es_client_index):
    """Test creating the OpenSearch index."""
    # Reset mock and configure
    mock_es_client_index.indices.exists.return_value = False

    # Call the function
    create_index_if_not_exists()

    # Verify the appropriate ES client calls were made
    mock_es_client_index.indices.exists.assert_called_once()
    mock_es_client_index.indices.create.assert_called_once()


def test_delete_index(mock_es_client_index):
    """Test deleting the OpenSearch index."""

    # Call the function
    delete_index()

    # Verify the ES client call
    mock_es_client_index.indices.delete.assert_called_once()


@pytest.mark.django_db
def test_index_thread(mock_es_client_index, test_thread):
    """Test indexing a thread."""

    # Call the function
    success = index_thread(test_thread)

    # Verify result
    assert success

    # Verify ES client was called
    assert mock_es_client_index.index.call_count > 0


@pytest.mark.django_db
def test_index_message(mock_es_client_index, test_thread):
    """Test indexing a message."""
    message = test_thread.messages.first()

    # Call the function
    success = index_message(message)

    # Verify result
    assert success

    # Verify ES client call
    mock_es_client_index.index.assert_called()


@pytest.mark.django_db
def test_reindex_all(mock_es_client_index):
    """Test reindexing all threads and messages."""
    # Reset mock
    mock_es_client_index.indices.exists.return_value = False

    with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
        # Call the function
        result = reindex_all()

    # Verify result
    assert result["status"] == "success"

    # Verify ES client calls
    mock_es_client_index.indices.create.assert_called_once()


@pytest.mark.django_db
def test_reindex_mailbox(mock_es_client_index, test_mailbox, test_thread):  # pylint: disable=unused-argument
    """Test reindexing a specific mailbox."""

    with mock.patch("core.services.search.index.bulk", return_value=(2, [])):
        result = reindex_mailbox(str(test_mailbox.id))

    # Verify result
    assert result["status"] == "success"
    assert result["mailbox"] == str(test_mailbox.id)
    assert result["indexed_threads"] == 1


def test_search_threads_with_query(mock_es_client_search):
    """Test searching for threads with a query."""
    # Reset and setup mock response
    mock_es_client_search.search.return_value = {
        "hits": {
            "total": {"value": 1},
            "hits": [{"_source": {"thread_id": "123", "subject": "Test Subject"}}],
        }
    }

    # Call the function
    result = search_threads("test query", mailbox_ids=["mailbox-id"])

    # Verify ES client call
    assert mock_es_client_search.search.called
    # Check that the mailbox filter was applied
    call_args = mock_es_client_search.search.call_args[1]

    # Find the mailbox filter in the query
    mailbox_filter_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "terms" in filter_item and "mailbox_ids" in filter_item["terms"]:
            mailbox_filter_found = True
            assert filter_item["terms"]["mailbox_ids"] == ["mailbox-id"]
    assert mailbox_filter_found, "Mailbox filter not found in query"

    # Verify result
    assert len(result["threads"]) == 1
    assert result["threads"][0]["id"] == "123"
    assert result["total"] == 1


def test_search_threads_pagination(mock_es_client_search):
    """Test pagination in thread search."""
    # Reset and setup mock response
    mock_es_client_search.search.return_value = {
        "hits": {
            "total": {"value": 30},
            "hits": [
                {"_source": {"thread_id": f"{i}", "subject": f"Subject {i}"}}
                for i in range(10)  # Return 10 results
            ],
        }
    }

    # Call with from_offset=10, size=10 (page 2)
    result = search_threads("test", from_offset=10, size=10)

    # Verify results
    assert len(result["threads"]) == 10
    assert result["total"] == 30
    assert result["from"] == 10
    assert result["size"] == 10

    # Verify pagination parameters were passed correctly
    call_args = mock_es_client_search.search.call_args[1]
    assert call_args["body"]["from"] == 10
    assert call_args["body"]["size"] == 10


@override_settings(OPENSEARCH_INDEX_THREADS=False)
def test_search_threads_disabled(mock_es_client_search):
    """Test searching threads when OpenSearch indexing is disabled."""

    # Call the function
    result = search_threads("test query")

    # Verify empty results
    assert len(result["threads"]) == 0
    assert result["total"] == 0

    # Verify ES client was not called
    mock_es_client_search.search.assert_not_called()


@pytest.mark.django_db
def test_update_thread_mailbox_flags(mock_es_client_index):
    """Test that update_thread_mailbox_flags re-indexes the thread document."""
    thread = ThreadFactory()
    mailbox = MailboxFactory()
    MessageFactory(thread=thread)
    thread.update_stats()
    thread.refresh_from_db()
    ThreadAccessFactory(thread=thread, mailbox=mailbox, read_at=None)

    # Reset mock after setup (signals may have triggered calls)
    mock_es_client_index.reset_mock()

    success = update_thread_mailbox_flags(thread)

    assert success
    mock_es_client_index.index.assert_called_once()
    call_args = mock_es_client_index.index.call_args[1]
    assert call_args["id"] == str(thread.id)
    assert str(mailbox.id) in call_args["body"]["unread_mailboxes"]
    assert "starred_mailboxes" in call_args["body"]


@pytest.mark.django_db
class TestSearchIndexBuildMessageDoc:
    """Tests for _build_message_doc."""

    def test_search_index_build_message_doc_with_correct_document(self):
        """Test that _build_message_doc builds a correct document."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        message = MessageFactory(thread=thread)

        mailbox_ids = [str(mailbox.id)]
        doc = _build_message_doc(message, mailbox_ids)

        assert doc is not None
        assert doc["message_id"] == str(message.id)
        assert doc["thread_id"] == str(thread.id)
        assert doc["mailbox_ids"] == mailbox_ids
        assert doc["relation"] == {"name": "message", "parent": str(thread.id)}
        assert doc["subject"] == message.subject
        assert doc["sender_name"] == message.sender.name
        assert doc["sender_email"] == message.sender.email

    def test_search_index_build_message_doc_with_prefetched_recipients(self):
        """Test that pre-fetched recipients are used without extra queries."""
        thread = ThreadFactory()
        message = MessageFactory(thread=thread)
        recipients = list(message.recipients.select_related("contact").all())

        doc = _build_message_doc(message, ["mb-1"], recipients=recipients)

        assert doc is not None
        assert doc["mailbox_ids"] == ["mb-1"]

    def test_search_index_build_message_doc_returns_none_on_parse_error(self):
        """Test that _build_message_doc returns None on blob parse error."""
        thread = ThreadFactory()
        message = MessageFactory(thread=thread)
        message.blob = BlobFactory()
        message.save()

        with mock.patch(
            "core.services.search.index.parse_email_message",
            side_effect=RuntimeError("parse error"),
        ):
            doc = _build_message_doc(message, ["mb-1"])

        assert doc is None


@pytest.mark.django_db
class TestBuildThreadDoc:
    """Tests for _build_thread_doc."""

    def test_search_index_build_thread_doc_with_correct_document(self):
        """Test that _build_thread_doc builds a correct document."""
        thread = ThreadFactory()
        mailbox_ids = ["mb-1", "mb-2"]
        unread = ["mb-1"]
        starred = ["mb-2"]

        doc = _build_thread_doc(thread, mailbox_ids, unread, starred)

        assert doc["relation"] == "thread"
        assert doc["thread_id"] == str(thread.id)
        assert doc["subject"] == thread.subject
        assert doc["mailbox_ids"] == mailbox_ids
        assert doc["unread_mailboxes"] == unread
        assert doc["starred_mailboxes"] == starred


@pytest.mark.django_db
class TestSearchIndexComputeUnreadStarredFromAccesses:
    """Tests for _compute_unread_starred_from_accesses."""

    def test_search_index_compute_unread_starred_from_accesses_unread_when_read_at_none(
        self,
    ):
        """An access with read_at=None on a thread with messages is unread."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        MessageFactory(thread=thread)
        thread.update_stats()
        thread.refresh_from_db()
        ThreadAccessFactory(thread=thread, mailbox=mailbox, read_at=None)

        unread, starred = _compute_unread_starred_from_accesses(thread)
        assert str(mailbox.id) in unread
        assert not starred

    def test_search_index_compute_unread_starred_from_accesses_starred_when_starred_at_set(
        self,
    ):
        """An access with starred_at set is starred."""
        thread = ThreadFactory(has_active=False)
        mailbox = MailboxFactory()
        ThreadAccessFactory(thread=thread, mailbox=mailbox, starred_at=timezone.now())

        _unread, starred = _compute_unread_starred_from_accesses(thread)
        assert str(mailbox.id) in starred

    def test_search_index_compute_unread_starred_from_accesses_read_thread_is_not_unread(
        self,
    ):
        """An access with read_at after messaged_at is not unread."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        MessageFactory(thread=thread)
        thread.update_stats()
        thread.refresh_from_db()
        ThreadAccessFactory(thread=thread, mailbox=mailbox, read_at=timezone.now())

        unread, starred = _compute_unread_starred_from_accesses(thread)
        assert str(mailbox.id) not in unread
        assert not starred

    def test_search_index_compute_unread_starred_from_accesses_thread_without_messages_is_not_unread(
        self,
    ):
        """A thread with no messages (messaged_at is None) is not unread."""
        thread = ThreadFactory(has_active=False)
        mailbox = MailboxFactory()
        ThreadAccessFactory(thread=thread, mailbox=mailbox, read_at=None)

        unread, _starred = _compute_unread_starred_from_accesses(thread)
        assert not unread

    def test_search_index_compute_unread_starred_from_accesses_multiple_mailboxes_mixed_status(
        self,
    ):
        """Different mailboxes can have different unread/starred status."""
        thread = ThreadFactory()
        mb_read = MailboxFactory()
        mb_unread = MailboxFactory()
        mb_starred = MailboxFactory()
        MessageFactory(thread=thread)
        thread.update_stats()
        thread.refresh_from_db()
        ThreadAccessFactory(thread=thread, mailbox=mb_read, read_at=timezone.now())
        ThreadAccessFactory(thread=thread, mailbox=mb_unread, read_at=None)
        ThreadAccessFactory(
            thread=thread,
            mailbox=mb_starred,
            starred_at=timezone.now(),
            read_at=timezone.now(),
        )

        unread, starred = _compute_unread_starred_from_accesses(thread)
        assert str(mb_unread.id) in unread
        assert str(mb_read.id) not in unread
        assert str(mb_starred.id) in starred


@pytest.mark.django_db
class TestSearchReindexAllBulk:
    """Tests for the bulk reindex_all implementation."""

    def test_search_reindex_all_uses_bulk_api(self, mock_es_client_index):
        """Test that reindex_all uses opensearchpy.helpers.bulk."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        with mock.patch("core.services.search.index.bulk") as mock_bulk:
            mock_bulk.return_value = (2, [])
            result = reindex_all()

        assert result["status"] == "success"
        assert result["indexed_threads"] == 1
        assert result["indexed_messages"] == 1

        mock_bulk.assert_called_once()
        _, kwargs = mock_bulk.call_args
        actions = mock_bulk.call_args[0][1]
        assert len(actions) == 2  # 1 thread doc + 1 message doc
        assert kwargs["raise_on_error"] is False

    def test_search_reindex_all_progress_callback(self, mock_es_client_index):
        """Test that the progress callback is called."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False
        progress_calls = []

        def on_progress(current, total, success_count, failure_count):
            progress_calls.append((current, total, success_count, failure_count))

        with (
            mock.patch("core.services.search.index.BULK_CHUNK_SIZE", 1),
            mock.patch("core.services.search.index.bulk", return_value=(2, [])),
        ):
            reindex_all(progress_callback=on_progress)

        assert len(progress_calls) >= 1
        last_call = progress_calls[-1]
        assert last_call[0] == 1  # current
        assert last_call[1] == 1  # total

    def test_search_reindex_all_bulk_errors_are_counted_during_chunk_flush(
        self, mock_es_client_index
    ):
        """Test that bulk errors during chunk flush are tracked in failure_count."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False
        progress_calls = []

        def on_progress(current, total, success_count, failure_count):
            progress_calls.append((current, total, success_count, failure_count))

        bulk_errors = [
            {
                "index": {
                    "_id": "fake-id",
                    "error": {
                        "type": "mapper_parsing_exception",
                        "reason": "failed to parse",
                    },
                    "status": 400,
                }
            },
        ]

        # Use BULK_CHUNK_SIZE=1 to trigger the mid-loop flush path
        with (
            mock.patch("core.services.search.index.BULK_CHUNK_SIZE", 1),
            mock.patch(
                "core.services.search.index.bulk", return_value=(1, bulk_errors)
            ),
        ):
            result = reindex_all(progress_callback=on_progress)

        assert result["status"] == "success"
        assert result["indexed_threads"] == 1
        assert result["indexed_messages"] == 1

        # failure_count is reported via the progress callback
        assert len(progress_calls) == 1
        assert progress_calls[0][3] == 1  # failure_count

    def test_bulk_errors_during_final_flush_are_logged(self, mock_es_client_index):
        """Test that bulk errors during the final flush are logged."""
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        bulk_errors = [
            {
                "index": {
                    "_id": "fake-id",
                    "error": {
                        "type": "mapper_parsing_exception",
                        "reason": "failed to parse",
                    },
                    "status": 400,
                }
            },
        ]

        # Default BULK_CHUNK_SIZE (100) > 2 actions, so all go to final flush
        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(1, bulk_errors)
            ),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            result = reindex_all()

        assert result["status"] == "success"
        mock_logger.error.assert_called_with("Bulk indexing error: %s", bulk_errors[0])
