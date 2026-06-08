"""Tests for the core.services.search module."""
# pylint: disable=too-many-lines

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

import pytest
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError
from opensearchpy.exceptions import TransportError

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
from core.services.search.exceptions import (
    RETRYABLE_TRANSPORT_STATUS,
    TransientTransportError,
)
from core.services.search.index import (
    _build_message_doc,
    _build_thread_doc,
    _compute_unread_starred_from_accesses,
)
from core.services.search.mapping import MESSAGE_INDEX


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
    """Mock the OpenSearch client.

    Also resets the per-process ``ensure_index_exists.done`` flag so each
    test starts with a clean slate — otherwise the first test to run a
    hot-path task would set the flag for the whole pytest session and
    subsequent tests would observe a stale "index already ensured" state.
    """
    # pylint: disable-next=import-outside-toplevel
    from core.services.search.index import ensure_index_exists

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
        if hasattr(ensure_index_exists, "done"):
            del ensure_index_exists.done
        yield mock_es
        if hasattr(ensure_index_exists, "done"):
            del ensure_index_exists.done


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


def test_get_opensearch_client_forwards_max_retries():
    """The OpenSearch client must carry the configured ``max_retries``.

    This is the single source of truth for transport-level retries on
    transient statuses (502/503/504, opensearch-py's
    ``DEFAULT_RETRY_ON_STATUS``). Without it we silently fall back to
    the library default of 3, and a brief cluster overload bypasses
    our outer Celery autoretry without anyone noticing.
    """
    # pylint: disable-next=import-outside-toplevel
    from core.services.search.index import get_opensearch_client

    if hasattr(get_opensearch_client, "cached_client"):
        del get_opensearch_client.cached_client

    with (
        override_settings(OPENSEARCH_MAX_RETRIES=7),
        mock.patch("core.services.search.index.OpenSearch") as mock_opensearch,
    ):
        get_opensearch_client()

    _, kwargs = mock_opensearch.call_args
    assert kwargs["max_retries"] == 7
    assert kwargs["retry_on_timeout"] is True

    # Reset the cached client so the next test re-instantiates against
    # the (mocked) module-level fixture.
    if hasattr(get_opensearch_client, "cached_client"):
        del get_opensearch_client.cached_client


def test_create_index_if_not_exists(mock_es_client_index):
    """Test creating the OpenSearch index."""
    # Reset mock and configure
    mock_es_client_index.indices.exists.return_value = False

    # Call the function
    create_index_if_not_exists()

    # Verify the appropriate ES client calls were made
    mock_es_client_index.indices.exists.assert_called_once()
    mock_es_client_index.indices.create.assert_called_once()
    # ``refresh_interval`` is hardcoded in MESSAGE_MAPPING — pinning it
    # here surfaces accidental drift to OpenSearch's 1s default, which
    # would silently triple the refresh load on the cluster.
    create_kwargs = mock_es_client_index.indices.create.call_args.kwargs
    assert create_kwargs["body"]["settings"]["refresh_interval"] == "5s"


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


@pytest.mark.django_db
def test_reindex_all_from_date_filters_threads(mock_es_client_index):  # pylint: disable=unused-argument
    """``from_date`` skips threads whose ``updated_at`` is before the cutoff.

    Backstop for the ``--from-date`` management option: the queryset filter
    must be applied at the source so old threads never reach the bulk loop.
    """
    cutoff = timezone.now()

    old_thread = ThreadFactory()
    MessageFactory(thread=old_thread)
    type(old_thread).objects.filter(pk=old_thread.pk).update(
        updated_at=cutoff - timedelta(days=2)
    )

    fresh_thread = ThreadFactory()
    MessageFactory(thread=fresh_thread)
    type(fresh_thread).objects.filter(pk=fresh_thread.pk).update(
        updated_at=cutoff + timedelta(hours=1)
    )

    with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
        result = reindex_all(from_date=cutoff)

    # Only the fresh thread (and its message) should have been indexed.
    assert result["indexed_threads"] == 1
    assert result["indexed_messages"] == 1


@pytest.mark.django_db
def test_reindex_mailbox_from_date_filters_threads(  # pylint: disable=unused-argument
    mock_es_client_index,
    test_mailbox,
):
    """Same cutoff semantics on the per-mailbox path."""
    cutoff = timezone.now()

    old_thread = ThreadFactory()
    ThreadAccessFactory(mailbox=test_mailbox, thread=old_thread)
    MessageFactory(thread=old_thread)
    type(old_thread).objects.filter(pk=old_thread.pk).update(
        updated_at=cutoff - timedelta(days=2)
    )

    fresh_thread = ThreadFactory()
    ThreadAccessFactory(mailbox=test_mailbox, thread=fresh_thread)
    MessageFactory(thread=fresh_thread)
    type(fresh_thread).objects.filter(pk=fresh_thread.pk).update(
        updated_at=cutoff + timedelta(hours=1)
    )

    with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
        result = reindex_mailbox(str(test_mailbox.id), from_date=cutoff)

    assert result["indexed_threads"] == 1
    assert result["indexed_messages"] == 1


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
            "core.services.search.index.parse_email",
            return_value=None,
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
        # Timeout and payload-size cap must be forwarded so that large
        # reindex runs don't hit opensearch-py defaults (10s timeout).
        # Transient-status retries (502/503/504) are handled by the
        # transport layer of the OpenSearch client, not by the bulk
        # helper — passing ``max_retries`` here would only cover 429.
        assert kwargs["request_timeout"] == settings.OPENSEARCH_BULK_TIMEOUT
        assert kwargs["max_chunk_bytes"] == settings.OPENSEARCH_BULK_MAX_BYTES
        assert "max_retries" not in kwargs
        assert "initial_backoff" not in kwargs

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
            override_settings(OPENSEARCH_BULK_CHUNK_SIZE=1),
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

        # OPENSEARCH_BULK_CHUNK_SIZE=1 forces the mid-loop flush path
        with (
            override_settings(OPENSEARCH_BULK_CHUNK_SIZE=1),
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

        # Default OPENSEARCH_BULK_CHUNK_SIZE > 2 actions, so all go to final flush
        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(1, bulk_errors)
            ),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            result = reindex_all()

        assert result["status"] == "success"
        mock_logger.error.assert_called_with("Bulk indexing error: %s", bulk_errors[0])

    def test_bulk_4xx_transport_error_is_caught_and_counted_as_failure(
        self, mock_es_client_index
    ):
        """A 4xx TransportError must not abort the reindex loop.

        4xx errors are caller bugs (bad mapping, malformed query). Retrying
        them only burns worker time, so we swallow and count them as
        failures so the outer loop can keep draining the coalescer buffer.
        """
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(400, "bad_request", {}),
            ),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            result = reindex_all()

        assert result["status"] == "success"
        assert result["failure_count"] == 2  # 1 thread doc + 1 message doc
        mock_logger.exception.assert_called_once()

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_bulk_retryable_transport_error_is_reraised(
        self, mock_es_client_index, status_code
    ):
        """Retryable codes propagate as ``TransientTransportError`` so Celery
        autoretry kicks in. Swallowing them would silently drop the chunk
        and emit one Sentry event per chunk during an outage instead of one
        per task.
        """
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "unavailable", {}),
            ),
            pytest.raises(TransientTransportError),
        ):
            reindex_all()

    @pytest.mark.parametrize("status_code", [500, 501])
    def test_bulk_non_retryable_5xx_is_not_reraised_as_transient(
        self, mock_es_client_index, status_code
    ):
        """5xx codes outside ``RETRYABLE_TRANSPORT_STATUS`` are cluster bugs
        that should surface in Sentry, not burn retries. The reindex loop
        swallows them as per-request failures so the outer drain keeps
        making progress; this guards against accidentally widening the
        allowlist to all 5xx codes.
        """
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "server_error", {}),
            ),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            result = reindex_all()

        assert result["status"] == "success"
        assert result["failure_count"] == 2  # 1 thread doc + 1 message doc
        mock_logger.exception.assert_called_once()

    def test_reindex_no_longer_calls_delete_by_query(self, mock_es_client_index):
        """Reindex is now pure upsert — orphan cleanup lives elsewhere.

        Previously each chunk fired a ``delete_by_query`` to sweep stale
        message docs (the per-chunk purge that triggered cluster 503s
        under load). Orphans are now handled by the dedicated
        ``bulk_delete_messages_task`` queue fed by ``post_delete``
        signals; the reindex loop must not touch ``delete_by_query`` at
        all.
        """
        thread = ThreadFactory()
        mailbox = MailboxFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        MessageFactory(thread=thread)

        mock_es_client_index.indices.exists.return_value = False

        with mock.patch("core.services.search.index.bulk", return_value=(2, [])):
            reindex_all()

        mock_es_client_index.delete_by_query.assert_not_called()


@pytest.mark.django_db
class TestBulkDeleteThreadsTask:
    """Tests for the rewritten bulk_delete_threads_task (bulk delete by _id).

    Replaces the previous ``delete_by_query`` implementation. The new
    behaviour issues one ``DELETE`` action per thread parent doc through
    ``opensearchpy.helpers.bulk`` — child message docs are handled by
    ``bulk_delete_messages_task`` via cascaded ``Message.post_delete``
    signals.
    """

    def test_uses_bulk_with_delete_actions(self):
        """Each thread ID produces one bulk ``delete`` action by ``_id``."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_threads_task

        thread_ids = ["thread-a", "thread-b"]

        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(2, [])
            ) as mock_bulk,
            mock.patch("core.services.search.index.get_opensearch_client"),
        ):
            result = bulk_delete_threads_task.run(thread_ids)

        assert result == {"success": True, "deleted_threads": 2}
        mock_bulk.assert_called_once()
        actions = mock_bulk.call_args[0][1]
        assert actions == [
            {"_op_type": "delete", "_index": MESSAGE_INDEX, "_id": "thread-a"},
            {"_op_type": "delete", "_index": MESSAGE_INDEX, "_id": "thread-b"},
        ]
        # Same timeout / chunk size knobs as the reindex bulk so a deletion
        # storm cannot fall back to opensearch-py defaults (10s).
        kwargs = mock_bulk.call_args[1]
        assert kwargs["request_timeout"] == settings.OPENSEARCH_BULK_TIMEOUT
        assert kwargs["max_chunk_bytes"] == settings.OPENSEARCH_BULK_MAX_BYTES
        assert "max_retries" not in kwargs

    def test_no_call_when_thread_ids_empty(self):
        """An empty list is a no-op — no bulk request fired."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_threads_task

        with mock.patch("core.services.search.index.bulk") as mock_bulk:
            result = bulk_delete_threads_task.run([])

        assert result == {"success": True, "deleted_threads": 0}
        mock_bulk.assert_not_called()

    def test_disabled_setting_short_circuits(self):
        """``OPENSEARCH_INDEX_THREADS=False`` disables the task entirely."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_threads_task

        with (
            override_settings(OPENSEARCH_INDEX_THREADS=False),
            mock.patch("core.services.search.index.bulk") as mock_bulk,
        ):
            result = bulk_delete_threads_task.run(["thread-a"])

        assert result == {"success": False, "reason": "disabled"}
        mock_bulk.assert_not_called()

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_retryable_transport_error_propagates(self, status_code):
        """Retryable codes surface as ``TransientTransportError`` so Celery autoretry kicks in."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_threads_task

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "unavailable", {}),
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            pytest.raises(TransientTransportError),
        ):
            bulk_delete_threads_task.run(["thread-a"])

    @pytest.mark.parametrize("status_code", [400, 500, 501])
    def test_non_retryable_transport_error_does_not_trigger_retry(self, status_code):
        """Codes outside ``RETRYABLE_TRANSPORT_STATUS`` reach the caller
        untouched and land in Sentry on the first hit — no retry, no wrap.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_threads_task

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "error", {}),
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            pytest.raises(TransportError) as exc_info,
        ):
            bulk_delete_threads_task.run(["thread-a"])

        assert not isinstance(exc_info.value, TransientTransportError)


@pytest.mark.django_db
class TestBulkDeleteMessagesTask:
    """Tests for the new bulk_delete_messages_task (bulk delete by _id).

    Replaces the per-chunk ``_purge_orphan_docs`` ``delete_by_query`` that
    the previous reindex loop fired to sweep stale message docs. Now we
    track the explicit ``(thread_id, message_id)`` pairs at signal time
    and bulk-delete them by ``_id`` — far cheaper for the cluster.
    """

    def test_uses_bulk_with_delete_actions_and_routing(self):
        """Each pair produces a delete action with the parent ``_routing`` set."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        pairs = ["thread-a:msg-1", "thread-a:msg-2", "thread-b:msg-3"]

        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(3, [])
            ) as mock_bulk,
            mock.patch("core.services.search.index.get_opensearch_client"),
        ):
            result = bulk_delete_messages_task.run(pairs)

        assert result == {"success": True, "deleted_messages": 3}
        actions = mock_bulk.call_args[0][1]
        assert actions == [
            {
                "_op_type": "delete",
                "_index": MESSAGE_INDEX,
                "_id": "msg-1",
                "_routing": "thread-a",
            },
            {
                "_op_type": "delete",
                "_index": MESSAGE_INDEX,
                "_id": "msg-2",
                "_routing": "thread-a",
            },
            {
                "_op_type": "delete",
                "_index": MESSAGE_INDEX,
                "_id": "msg-3",
                "_routing": "thread-b",
            },
        ]

    def test_no_call_when_pairs_empty(self):
        """An empty list is a no-op."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        with mock.patch("core.services.search.index.bulk") as mock_bulk:
            result = bulk_delete_messages_task.run([])

        assert result == {"success": True, "deleted_messages": 0}
        mock_bulk.assert_not_called()

    def test_malformed_pair_skipped_with_warning(self):
        """A pair without ``thread_id:message_id`` shape is dropped with a log line."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        pairs = ["thread-a:msg-1", "no-separator", ":missing-thread", "missing-msg:"]

        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(1, [])
            ) as mock_bulk,
            mock.patch("core.services.search.index.get_opensearch_client"),
            mock.patch("core.services.search.tasks.logger") as mock_logger,
        ):
            result = bulk_delete_messages_task.run(pairs)

        assert result == {"success": True, "deleted_messages": 1}
        actions = mock_bulk.call_args[0][1]
        assert actions == [
            {
                "_op_type": "delete",
                "_index": MESSAGE_INDEX,
                "_id": "msg-1",
                "_routing": "thread-a",
            }
        ]
        # Each malformed pair gets a warning so misuse is visible at triage.
        assert mock_logger.warning.call_count == 3

    def test_disabled_setting_short_circuits(self):
        """``OPENSEARCH_INDEX_THREADS=False`` disables the task entirely."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        with (
            override_settings(OPENSEARCH_INDEX_THREADS=False),
            mock.patch("core.services.search.index.bulk") as mock_bulk,
        ):
            result = bulk_delete_messages_task.run(["thread-a:msg-1"])

        assert result == {"success": False, "reason": "disabled"}
        mock_bulk.assert_not_called()

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_retryable_transport_error_propagates(self, status_code):
        """Retryable codes surface as ``TransientTransportError`` so Celery autoretry kicks in."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "unavailable", {}),
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            pytest.raises(TransientTransportError),
        ):
            bulk_delete_messages_task.run(["thread-a:msg-1"])

    @pytest.mark.parametrize("status_code", [400, 500, 501])
    def test_non_retryable_transport_error_does_not_trigger_retry(self, status_code):
        """Mirrors the ``bulk_delete_threads_task`` contract: codes outside
        ``RETRYABLE_TRANSPORT_STATUS`` reach the caller untouched.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        with (
            mock.patch(
                "core.services.search.index.bulk",
                side_effect=TransportError(status_code, "error", {}),
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            pytest.raises(TransportError) as exc_info,
        ):
            bulk_delete_messages_task.run(["thread-a:msg-1"])

        assert not isinstance(exc_info.value, TransientTransportError)

    def test_per_doc_404_is_swallowed_without_logging(self):
        """Per-document 404s on delete are benign (already gone) and must
        not flood Sentry.

        Cascaded ``Message.post_delete`` signals routinely enqueue deletes
        for messages whose parent thread doc was never indexed or has just
        been purged by ``bulk_delete_threads_task``; OpenSearch then returns
        ``status: 404 / not_found`` per item. Logging those at ``error`` was
        creating ~2400 Sentry events per day for a non-issue.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        bulk_errors = [
            {
                "delete": {
                    "_index": "messages",
                    "_id": "msg-1",
                    "result": "not_found",
                    "status": 404,
                }
            }
        ]

        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(0, bulk_errors)
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            result = bulk_delete_messages_task.run(["thread-a:msg-1"])

        assert result == {"success": True, "deleted_messages": 1}
        mock_logger.error.assert_not_called()

    def test_per_doc_non_404_error_is_still_logged(self):
        """Other per-document errors must still surface — only 404 is benign."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_delete_messages_task

        bulk_errors = [
            {
                "delete": {
                    "_index": "messages",
                    "_id": "msg-1",
                    "error": {"type": "version_conflict_engine_exception"},
                    "status": 409,
                }
            }
        ]

        with (
            mock.patch(
                "core.services.search.index.bulk", return_value=(0, bulk_errors)
            ),
            mock.patch("core.services.search.index.get_opensearch_client"),
            mock.patch("core.services.search.index.logger") as mock_logger,
        ):
            bulk_delete_messages_task.run(["thread-a:msg-1"])

        mock_logger.error.assert_called_with("Bulk indexing error: %s", bulk_errors[0])


class TestRunRequestRetryFilter:
    """Unitary OpenSearch calls participate in the shared retry contract.

    Without this filter, a transient ``TransportError`` on a unitary call
    (e.g., ``es.indices.exists``) would not be in ``RETRYABLE_EXCEPTIONS``
    and would abort the surrounding Celery task — that was the root cause
    of the residual Sentry issues that survived the ``delete_by_query``
    removal.
    """

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_retryable_status_codes_are_translated(self, status_code):
        """Codes in ``RETRYABLE_TRANSPORT_STATUS`` surface as ``TransientTransportError``."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import _run_request

        def boom():
            raise TransportError(status_code, "unavailable", {"info": "x"})

        with pytest.raises(TransientTransportError) as exc_info:
            _run_request(boom)

        assert exc_info.value.status_code == status_code

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 500, 501])
    def test_non_retryable_status_codes_propagate_untouched(self, status_code):
        """Other transport errors keep their original type so caller bugs and
        ``NotFoundError`` catches keep working unchanged.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import _run_request

        def boom():
            raise TransportError(status_code, "error", {})

        with pytest.raises(TransportError) as exc_info:
            _run_request(boom)

        assert not isinstance(exc_info.value, TransientTransportError)
        assert exc_info.value.status_code == status_code

    def test_happy_path_returns_callable_result(self):
        """The wrapper is a passthrough on success."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import _run_request

        assert _run_request(lambda x, y: x + y, 1, y=2) == 3

    def test_connection_error_propagates_untouched(self):
        """``ConnectionError`` is a ``TransportError`` subclass with a
        non-numeric ``status_code``; it must fall through to the bare ``raise``
        so Celery matches it directly via ``RETRYABLE_EXCEPTIONS`` (it is
        unambiguously retryable on its own — no need to wrap it).
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import _run_request

        def boom():
            raise OpenSearchConnectionError("N/A", "Connection refused", {})

        with pytest.raises(OpenSearchConnectionError) as exc_info:
            _run_request(boom)

        assert not isinstance(exc_info.value, TransientTransportError)


class TestCreateIndexIfNotExistsRetry:
    """Index existence / create calls participate in the autoretry contract.

    The Sentry issue that motivated this work was raised by
    ``es.indices.exists`` inside ``create_index_if_not_exists`` — the call
    used to throw a raw ``TransportError`` that bypassed Celery autoretry
    entirely and aborted whatever bulk task came after it.
    """

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_retryable_status_on_indices_exists_translated(
        self, status_code, mock_es_client_index
    ):
        """Retryable codes on the existence check surface as ``TransientTransportError``."""
        mock_es_client_index.indices.exists.side_effect = TransportError(
            status_code, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            create_index_if_not_exists()

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_retryable_status_on_indices_create_translated(
        self, status_code, mock_es_client_index
    ):
        """Retryable codes on the create call surface as ``TransientTransportError`` too."""
        mock_es_client_index.indices.exists.return_value = False
        mock_es_client_index.indices.create.side_effect = TransportError(
            status_code, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            create_index_if_not_exists()

    def test_non_retryable_400_propagates_as_is(self, mock_es_client_index):
        """Caller bugs (4xx) reach Sentry on the first hit, no retry."""
        mock_es_client_index.indices.exists.side_effect = TransportError(
            400, "bad_request", {}
        )

        with pytest.raises(TransportError) as exc_info:
            create_index_if_not_exists()

        assert not isinstance(exc_info.value, TransientTransportError)


class TestEnsureIndexExists:
    """One-shot guard around ``create_index_if_not_exists`` for hot paths.

    One HEAD per worker process at first task, none after. The flag is set
    *after* a successful call so a transient failure on the first probe
    leaves the flag unset and the next task retries.
    """

    def test_subsequent_calls_short_circuit(self, mock_es_client_index):
        """Second call must not touch the cluster."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import ensure_index_exists

        mock_es_client_index.indices.exists.return_value = True

        ensure_index_exists()
        ensure_index_exists()
        ensure_index_exists()

        assert mock_es_client_index.indices.exists.call_count == 1

    def test_failure_does_not_set_cache(self, mock_es_client_index):
        """If the probe raises, the flag stays unset so the next call
        retries — otherwise a transient on the first task would permanently
        skip bootstrap for the rest of the worker's lifetime.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.index import ensure_index_exists

        mock_es_client_index.indices.exists.side_effect = TransportError(
            503, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            ensure_index_exists()

        mock_es_client_index.indices.exists.side_effect = None
        mock_es_client_index.indices.exists.return_value = True

        ensure_index_exists()
        assert mock_es_client_index.indices.exists.call_count == 2

        # Third call short-circuits via the now-set flag.
        ensure_index_exists()
        assert mock_es_client_index.indices.exists.call_count == 2


@pytest.mark.django_db
class TestSingleDocIndexRetry:
    """``index_message`` / ``update_thread_mailbox_flags`` / ``index_thread``
    propagate ``RETRYABLE_EXCEPTIONS`` instead of swallowing them in the broad
    ``except Exception`` — otherwise the surrounding Celery task would never
    autoretry on a transient and would silently desync the index.
    """

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_index_message_propagates_transient_error(
        self, status_code, mock_es_client_index, test_thread
    ):
        """Retryable codes on ``es.index`` propagate, not return ``False``."""
        message = test_thread.messages.first()
        mock_es_client_index.index.side_effect = TransportError(
            status_code, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            index_message(message)

    def test_index_message_swallows_non_transient_error(
        self, mock_es_client_index, test_thread
    ):
        """Non-retryable errors keep the legacy log-and-return-False behavior
        so a single bad doc cannot abort an outer loop.
        """
        message = test_thread.messages.first()
        mock_es_client_index.index.side_effect = TransportError(400, "bad", {})

        with mock.patch("core.services.search.index.logger") as mock_logger:
            assert index_message(message) is False

        mock_logger.error.assert_called_once()

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_update_thread_mailbox_flags_propagates_transient_error(
        self, status_code, mock_es_client_index, test_thread
    ):
        """Same retry contract for the mailbox-flag re-index call."""
        mock_es_client_index.index.side_effect = TransportError(
            status_code, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            update_thread_mailbox_flags(test_thread)

    @pytest.mark.parametrize("status_code", sorted(RETRYABLE_TRANSPORT_STATUS))
    def test_index_thread_propagates_transient_error(
        self, status_code, mock_es_client_index, test_thread
    ):
        """Same retry contract for the thread parent doc."""
        mock_es_client_index.index.side_effect = TransportError(
            status_code, "unavailable", {}
        )

        with pytest.raises(TransientTransportError):
            index_thread(test_thread)

    def test_index_message_propagates_connection_error(
        self, mock_es_client_index, test_thread
    ):
        """Socket-level drops surface as ``OpenSearchConnectionError`` and
        must propagate too — bare-raising ``RETRYABLE_EXCEPTIONS`` covers
        both ``TransientTransportError`` and ``ConnectionError`` in one
        clause.
        """
        message = test_thread.messages.first()
        mock_es_client_index.index.side_effect = OpenSearchConnectionError(
            "N/A", "Connection refused", {}
        )

        with pytest.raises(OpenSearchConnectionError):
            index_message(message)

    def test_update_thread_mailbox_flags_propagates_connection_error(
        self, mock_es_client_index, test_thread
    ):
        """Same retry contract for the mailbox-flag re-index call."""
        mock_es_client_index.index.side_effect = OpenSearchConnectionError(
            "N/A", "Connection refused", {}
        )

        with pytest.raises(OpenSearchConnectionError):
            update_thread_mailbox_flags(test_thread)

    def test_index_thread_propagates_connection_error(
        self, mock_es_client_index, test_thread
    ):
        """Same retry contract for the thread parent doc."""
        mock_es_client_index.index.side_effect = OpenSearchConnectionError(
            "N/A", "Connection refused", {}
        )

        with pytest.raises(OpenSearchConnectionError):
            index_thread(test_thread)


class TestHotPathProbesIndexOnceThenCaches:
    """Per-task tasks HEAD the cluster manager once per worker, then cache.

    Threads the needle between two earlier mistakes: probing on every task
    (overhead + no retry safety net) and probing on no task at all (no
    lazy-bootstrap safety net for fresh deploys). The cache flag is reset
    between tests by the ``mock_es_client_index`` fixture so test ordering
    does not matter.
    """

    @pytest.mark.django_db
    def test_first_hot_path_call_probes_then_subsequent_calls_skip(
        self, mock_es_client_index
    ):
        """One HEAD on first task, zero on the next, regardless of which
        hot-path task is hit second.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import (
            bulk_reindex_threads_task,
            index_message_task,
        )

        thread = ThreadFactory()
        MessageFactory(thread=thread)
        mock_es_client_index.indices.exists.return_value = True

        with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
            bulk_reindex_threads_task.run([str(thread.id)])

        assert mock_es_client_index.indices.exists.call_count == 1

        index_message_task.run(str(thread.messages.first().id))

        # Still 1 — the second task short-circuited via the cached flag.
        assert mock_es_client_index.indices.exists.call_count == 1

    @pytest.mark.django_db
    def test_first_call_creates_index_when_missing(self, mock_es_client_index):
        """If the index is missing on first call, the create is issued — so a
        fresh deploy that forgot ``search_index_create`` still bootstraps
        with the right parent-child mapping instead of letting OS
        auto-create with its default mapping.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_reindex_threads_task

        thread = ThreadFactory()
        MessageFactory(thread=thread)
        mock_es_client_index.indices.exists.return_value = False

        with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
            bulk_reindex_threads_task.run([str(thread.id)])

        mock_es_client_index.indices.exists.assert_called_once()
        mock_es_client_index.indices.create.assert_called_once()

    @pytest.mark.django_db
    def test_failure_on_first_probe_does_not_set_cache_flag(self, mock_es_client_index):
        """End-to-end check of the same retry-on-failure contract as
        ``TestEnsureIndexExists.test_failure_does_not_set_cache``, but
        through a real Celery task entry point.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_reindex_threads_task

        thread = ThreadFactory()
        MessageFactory(thread=thread)
        mock_es_client_index.indices.exists.side_effect = TransportError(
            503, "unavailable", {}
        )

        with (
            mock.patch("core.services.search.index.bulk", return_value=(0, [])),
            pytest.raises(TransientTransportError),
        ):
            bulk_reindex_threads_task.run([str(thread.id)])

        mock_es_client_index.indices.exists.side_effect = None
        mock_es_client_index.indices.exists.return_value = True

        with mock.patch("core.services.search.index.bulk", return_value=(0, [])):
            bulk_reindex_threads_task.run([str(thread.id)])

        assert mock_es_client_index.indices.exists.call_count == 2

    @pytest.mark.django_db
    def test_disabled_setting_short_circuits_before_probe(
        self, mock_es_client_index, test_thread
    ):
        """``OPENSEARCH_INDEX_THREADS=False`` must skip the probe too —
        otherwise we'd HEAD a cluster the deployment intentionally opted out
        of.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_reindex_threads_task

        with override_settings(OPENSEARCH_INDEX_THREADS=False):
            bulk_reindex_threads_task.run([str(test_thread.id)])

        mock_es_client_index.indices.exists.assert_not_called()
