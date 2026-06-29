"""OpenSearch client and indexing functionality."""

# pylint: disable=unexpected-keyword-arg

import logging

from django.conf import settings
from django.db.models import Prefetch, prefetch_related_objects

from jmap_email import body_text_joined, parse_email
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError, TransportError
from opensearchpy.helpers import bulk

from core import enums, models
from core.services.search.exceptions import (
    RETRYABLE_EXCEPTIONS,
    RETRYABLE_TRANSPORT_STATUS,
    TransientTransportError,
)
from core.services.search.mapping import MESSAGE_INDEX, MESSAGE_MAPPING

logger = logging.getLogger(__name__)


def _run_request(fn, *args, **kwargs):
    """Wrap a single OpenSearch call with the shared retry contract.

    Translates retryable HTTP statuses to ``TransientTransportError`` so the
    Celery ``autoretry_for`` list catches them. ``ConnectionError`` falls
    through (its ``status_code`` is ``"N/A"``) and is matched directly by
    ``RETRYABLE_EXCEPTIONS`` upstream. Everything else propagates so caller
    bugs surface in Sentry and bare ``NotFoundError`` catches in
    ``delete_index`` / ``_build_message_doc`` keep working.
    """
    try:
        return fn(*args, **kwargs)
    except TransportError as exc:
        if getattr(exc, "status_code", None) in RETRYABLE_TRANSPORT_STATUS:
            raise TransientTransportError(
                exc.status_code,
                f"OpenSearch returned {exc.status_code} for "
                f"{getattr(fn, '__qualname__', fn)}",
                getattr(exc, "info", None),
            ) from exc
        raise


def _run_bulk(es, actions, *, swallow_4xx_as_failure, ignored_statuses=()):
    """Run an opensearchpy bulk request with our standard knobs.

    Same retry contract as ``_run_request``. Transient transport errors
    (502/503/504) are retried at the transport layer by the OpenSearch
    client (see ``get_opensearch_client``); whatever still bubbles up
    here is re-raised as ``TransientTransportError`` so Celery
    autoretry takes over with its own exponential backoff. Stacking a
    third local retry would just block the worker on ``time.sleep``
    without adding resilience.

    ``ignored_statuses`` drops matching per-document errors before
    logging — delete callers pass ``(404,)`` because hitting an
    already-removed document is benign and would otherwise flood
    Sentry once per cascaded message delete. When
    ``swallow_4xx_as_failure`` is True the reindex loop turns
    request-level 4xx into a failure count so it can keep draining the
    coalescer buffer; delete callers leave it False so caller bugs
    surface in Sentry instead of silently disappearing.
    """
    try:
        _, errors = bulk(
            es,
            actions,
            raise_on_error=False,
            request_timeout=settings.OPENSEARCH_BULK_TIMEOUT,
            max_chunk_bytes=settings.OPENSEARCH_BULK_MAX_BYTES,
        )
    except TransportError as exc:
        if getattr(exc, "status_code", None) in RETRYABLE_TRANSPORT_STATUS:
            raise TransientTransportError(
                exc.status_code,
                f"OpenSearch returned {exc.status_code} for bulk request "
                f"({len(actions)} actions)",
                getattr(exc, "info", None),
            ) from exc
        if swallow_4xx_as_failure:
            logger.exception("Bulk indexing request failed (%d actions)", len(actions))
            return len(actions)
        raise

    real_errors = [
        error
        for error in errors  # pylint: disable=not-an-iterable
        if next(iter(error.values())).get("status") not in ignored_statuses
    ]
    if real_errors:
        for error in real_errors:
            logger.error("Bulk indexing error: %s", error)
        return len(real_errors)

    return 0


def _flush_bulk_actions(es, actions):
    """Send reindex bulk actions to OpenSearch and return the failure count.

    Per-document errors (4xx) are logged and counted so the outer reindex
    loop can keep draining the coalescer buffer rather than aborting on the
    first malformed doc.
    """
    return _run_bulk(es, actions, swallow_4xx_as_failure=True)


def bulk_delete_documents(actions):
    """Send bulk delete actions to OpenSearch.

    ``ignored_statuses=(404,)`` drops per-doc "already gone" errors that
    routinely happen for cascaded message deletes whose parent thread doc
    was never indexed or has already been purged — logging them flooded
    Sentry. Caller bugs (other 4xx) propagate so they remain visible.
    """
    _run_bulk(
        get_opensearch_client(),
        actions,
        swallow_4xx_as_failure=False,
        ignored_statuses=(404,),
    )


# OpenSearch client instantiation
def get_opensearch_client():
    """Get OpenSearch client instance.

    ``max_retries`` is forwarded to the transport layer, which already
    retries on its ``DEFAULT_RETRY_ON_STATUS`` set (502/503/504). This
    is the single source of truth for transport-level retries — Celery
    autoretry handles the longer outage with exponential backoff if
    the transport budget is exhausted.
    """
    if not hasattr(get_opensearch_client, "cached_client"):
        kwargs = {
            "hosts": settings.OPENSEARCH_HOSTS,
            "timeout": settings.OPENSEARCH_TIMEOUT,
            "retry_on_timeout": True,
            "max_retries": settings.OPENSEARCH_MAX_RETRIES,
        }
        if settings.OPENSEARCH_CA_CERTS:
            kwargs["ca_certs"] = settings.OPENSEARCH_CA_CERTS
        get_opensearch_client.cached_client = OpenSearch(**kwargs)
    return get_opensearch_client.cached_client


def create_index_if_not_exists():
    """Create the messages index if it does not yet exist.

    Called from setup paths (``reindex_all`` / ``reindex_mailbox`` /
    ``reset_search_index`` / the ``search_reindex`` / ``search_index_create``
    management commands) and once per worker process from
    ``ensure_index_exists``. The wrapping ``_run_request`` makes the existence
    check participate in the shared retry contract.
    """
    es = get_opensearch_client()

    if not _run_request(es.indices.exists, index=MESSAGE_INDEX):
        _run_request(es.indices.create, index=MESSAGE_INDEX, body=MESSAGE_MAPPING)
        logger.info("Created OpenSearch index: %s", MESSAGE_INDEX)
    return True


def ensure_index_exists():
    """One-shot wrapper around ``create_index_if_not_exists`` for hot paths.

    Pays one HEAD per worker process on the first task; subsequent tasks
    short-circuit. Restores the safety net of the previous "create on first
    call" behavior so a fresh deploy where the operator forgot to run
    ``search_index_create`` still bootstraps the index with our parent-child
    mapping instead of letting OpenSearch auto-create with its default
    mapping (which would silently break search). The flag is set *after* a
    successful call — if the check raises, the attribute stays unset and
    the next task retries.
    """
    if getattr(ensure_index_exists, "done", False):
        return
    create_index_if_not_exists()
    ensure_index_exists.done = True


def delete_index():
    """Delete the messages index."""
    es = get_opensearch_client()
    try:
        _run_request(es.indices.delete, index=MESSAGE_INDEX)
        logger.info("Deleted OpenSearch index: %s", MESSAGE_INDEX)
        return True
    except NotFoundError:
        logger.warning("Index %s not found, nothing to delete", MESSAGE_INDEX)
        return False


def _build_message_doc(message, mailbox_ids, recipients=None):
    """Build an OpenSearch document dict for a message.

    Args:
        message: Message instance (with sender already loaded).
        mailbox_ids: list of string mailbox IDs.
        recipients: pre-fetched recipients with contact loaded.
            If None, they will be fetched from the database.

    Returns:
        dict or None if the message blob cannot be parsed.
    """
    parsed_data: dict = {}
    if message.blob:
        try:
            raw = message.blob.get_content()
        except models.Blob.DoesNotExist:
            raw = None
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error reading blob content for message %s: %s", message.id, e)
            return None
        if raw is not None:
            parsed_data = parse_email(raw)
            if parsed_data is None:
                logger.error("parse_email returned None for message %s", message.id)
                return None

    if recipients is None:
        recipients = list(message.recipients.select_related("contact").all())

    text_body = body_text_joined(parsed_data, "textBody")
    html_body = body_text_joined(parsed_data, "htmlBody")

    return {
        "relation": {"name": "message", "parent": str(message.thread_id)},
        "message_id": str(message.id),
        "thread_id": str(message.thread_id),
        "mailbox_ids": mailbox_ids,
        "mime_id": message.mime_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "subject": message.subject,
        "sender_name": message.sender.name,
        "sender_email": message.sender.email,
        "to_name": [
            r.contact.name
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.TO
        ],
        "to_email": [
            r.contact.email
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.TO
        ],
        "cc_name": [
            r.contact.name
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.CC
        ],
        "cc_email": [
            r.contact.email
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.CC
        ],
        "bcc_name": [
            r.contact.name
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.BCC
        ],
        "bcc_email": [
            r.contact.email
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.BCC
        ],
        "text_body": text_body,
        "html_body": html_body,
        "is_draft": message.is_draft,
        "is_trashed": message.is_trashed,
        "is_archived": message.is_archived,
        "is_spam": message.is_spam,
        "is_sender": message.is_sender,
    }


def _build_thread_doc(thread, mailbox_ids, unread_mailbox_ids, starred_mailbox_ids):
    """Build an OpenSearch document dict for a thread."""
    return {
        "relation": "thread",
        "thread_id": str(thread.id),
        "subject": thread.subject,
        "mailbox_ids": mailbox_ids,
        "unread_mailboxes": unread_mailbox_ids,
        "starred_mailboxes": starred_mailbox_ids,
    }


def _compute_unread_starred_from_accesses(thread):
    """Compute unread and starred mailbox IDs from prefetched accesses.

    Reproduces the logic of `ThreadAccess.unread_filter()` and
    `ThreadAccess.starred_filter()` in Python, avoiding additional DB
    queries when accesses are already prefetched.
    """
    unread_ids = []
    starred_ids = []
    messaged_at = thread.messaged_at

    for access in thread.accesses.all():
        # Reproduces: Q(read_at__isnull=True, thread__messaged_at__isnull=False)
        #           | Q(read_at__lt=F("thread__messaged_at"))
        if messaged_at is not None and (
            access.read_at is None or access.read_at < messaged_at
        ):
            unread_ids.append(str(access.mailbox_id))

        # Reproduces: Q(starred_at__isnull=False)
        if access.starred_at is not None:
            starred_ids.append(str(access.mailbox_id))

    return unread_ids, starred_ids


def index_message(message: models.Message, mailbox_ids=None) -> bool:
    """Index a single message."""
    es = get_opensearch_client()

    if mailbox_ids is None:
        mailbox_ids = [
            str(mid)
            for mid in message.thread.accesses.values_list("mailbox__id", flat=True)
        ]

    doc = _build_message_doc(message, mailbox_ids)
    if doc is None:
        return False

    try:
        # pylint: disable=no-value-for-parameter
        _run_request(
            es.index,
            index=MESSAGE_INDEX,
            id=str(message.id),
            routing=str(message.thread_id),  # Ensure parent-child routing
            body=doc,
        )
        logger.debug("Indexed message %s", message.id)
        return True
    except RETRYABLE_EXCEPTIONS:
        # Bare-raise so Celery autoretry fires; the broad catch below would
        # otherwise swallow the transient and silently desync the index.
        raise
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.error("Error indexing message %s: %s", message.id, e)
        return False


def update_thread_mailbox_flags(thread: models.Thread) -> bool:
    """Re-index the thread parent document to update mailbox-scoped flags.

    Updates both `unread_mailboxes` and `starred_mailboxes`.
    Uses full document replacement (es.index) instead of partial update
    (es.update) because partial updates don't work reliably with join field
    documents in OpenSearch.
    """
    es = get_opensearch_client()
    prefetch_related_objects([thread], "accesses")
    mailbox_ids = [str(access.mailbox_id) for access in thread.accesses.all()]
    unread_ids, starred_ids = _compute_unread_starred_from_accesses(thread)
    thread_doc = _build_thread_doc(thread, mailbox_ids, unread_ids, starred_ids)
    try:
        # pylint: disable=no-value-for-parameter
        _run_request(es.index, index=MESSAGE_INDEX, id=str(thread.id), body=thread_doc)
        return True
    except RETRYABLE_EXCEPTIONS:
        raise
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.error("Error updating mailbox flags for thread %s: %s", thread.id, e)
        return False


def index_thread(thread: models.Thread) -> bool:
    """Index a thread and all its messages."""
    es = get_opensearch_client()

    # Prefetch accesses once for mailbox IDs and unread/starred computation
    prefetch_related_objects([thread], "accesses")
    mailbox_ids = [str(access.mailbox_id) for access in thread.accesses.all()]
    unread_ids, starred_ids = _compute_unread_starred_from_accesses(thread)

    # First, index the thread document
    thread_doc = _build_thread_doc(thread, mailbox_ids, unread_ids, starred_ids)

    try:
        # Index thread as parent document
        # pylint: disable=no-value-for-parameter
        _run_request(es.index, index=MESSAGE_INDEX, id=str(thread.id), body=thread_doc)

        # Index all messages in the thread
        success = True
        for message in thread.messages.all():
            if not index_message(message, mailbox_ids=mailbox_ids):
                success = False

        return success
    except RETRYABLE_EXCEPTIONS:
        raise
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.error("Error indexing thread %s: %s", thread.id, e)
        return False


def reindex_bulk_threads(threads_qs, progress_callback=None):
    """Reindex a queryset of threads using the bulk API for performance.

    Pure upsert: every document still in the DB is rewritten in place via
    ``opensearchpy.helpers.bulk``. Orphan documents (messages or threads
    that no longer exist in the DB) are *not* swept here — that work is
    handled by the dedicated ``bulk_delete_threads_task`` /
    ``bulk_delete_messages_task`` queues fed by ``post_delete`` signals.
    Splitting the two paths avoids the costly per-chunk ``delete_by_query``
    that this loop used to issue, which under load triggered 503s on the
    OpenSearch cluster. Residual orphans (rows removed without firing
    ``post_delete`` — raw SQL, ``_raw_delete``…) are not swept anywhere
    yet; a manual sweeper would need to be wired up if drift becomes a
    concern.

    Uses chunked prefetching to minimize both DB queries and HTTP calls
    to OpenSearch.

    Args:
        threads_qs: A ``Thread`` queryset (unordered is fine).
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.

    Returns:
        dict with ``total``, ``indexed_threads``, ``indexed_messages`` and
        ``failure_count``.
    """
    es = get_opensearch_client()

    indexed_threads = 0
    indexed_messages = 0
    failure_count = 0
    total = threads_qs.count()

    # Prefetch the full tree needed to build index documents without N+1:
    # - accesses: to compute mailbox_ids, unread and starred flags
    # - messages → sender: for sender name/email
    # - messages → recipients → contact: for to/cc/bcc name/email
    threads_qs = threads_qs.prefetch_related(
        "accesses",
        Prefetch(
            "messages",
            queryset=models.Message.objects.select_related("sender").prefetch_related(
                Prefetch(
                    "recipients",
                    queryset=models.MessageRecipient.objects.select_related("contact"),
                )
            ),
        ),
    )

    actions = []
    chunk_size = settings.OPENSEARCH_BULK_CHUNK_SIZE

    for thread in threads_qs.iterator(chunk_size=chunk_size):
        mailbox_ids = [str(access.mailbox_id) for access in thread.accesses.all()]
        unread_ids, starred_ids = _compute_unread_starred_from_accesses(thread)

        # Thread action
        thread_id_str = str(thread.id)
        thread_doc = _build_thread_doc(thread, mailbox_ids, unread_ids, starred_ids)
        actions.append(
            {
                "_index": MESSAGE_INDEX,
                "_id": thread_id_str,
                "_source": thread_doc,
            }
        )

        # Message actions
        for message in thread.messages.all():
            recipients = list(message.recipients.all())
            doc = _build_message_doc(message, mailbox_ids, recipients=recipients)
            if doc is not None:
                actions.append(
                    {
                        "_index": MESSAGE_INDEX,
                        "_id": str(message.id),
                        "_routing": thread_id_str,
                        "_source": doc,
                    }
                )
                indexed_messages += 1

        indexed_threads += 1

        if len(actions) >= chunk_size:
            failure_count += _flush_bulk_actions(es, actions)
            actions = []

        if progress_callback and indexed_threads % chunk_size == 0:
            progress_callback(indexed_threads, total, indexed_threads, failure_count)

    # Flush remaining actions
    if actions:
        failure_count += _flush_bulk_actions(es, actions)

    return {
        "status": "success",
        "total": total,
        "indexed_threads": indexed_threads,
        "indexed_messages": indexed_messages,
        "failure_count": failure_count,
    }


def reindex_all(progress_callback=None, from_date=None):
    """Reindex all threads using the bulk API for performance.

    Args:
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.
        from_date: optional ``datetime`` — when set, only threads with
            ``updated_at >= from_date`` are reindexed. Filtering on
            ``updated_at`` (rather than ``created_at``) covers both newly
            created threads and pre-existing threads that have changed
            since the cutoff.
    """
    create_index_if_not_exists()
    queryset = models.Thread.objects.all()
    if from_date is not None:
        queryset = queryset.filter(updated_at__gte=from_date)
    return reindex_bulk_threads(queryset, progress_callback)


def reindex_mailbox(mailbox_id: str, progress_callback=None, from_date=None):
    """Reindex all messages and threads for a specific mailbox.

    Args:
        mailbox_id: The mailbox UUID as a string.
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.
        from_date: optional ``datetime`` — when set, only threads with
            ``updated_at >= from_date`` are reindexed.
    """
    try:
        mailbox = models.Mailbox.objects.get(id=mailbox_id)
    except models.Mailbox.DoesNotExist:
        return {"status": "error", "mailbox": mailbox_id, "error": "Mailbox not found"}

    queryset = mailbox.threads_viewer
    if from_date is not None:
        queryset = queryset.filter(updated_at__gte=from_date)

    create_index_if_not_exists()
    result = reindex_bulk_threads(queryset, progress_callback)
    result["mailbox"] = mailbox_id
    return result


def reindex_thread(thread_id: str):
    """Reindex a specific thread."""

    try:
        thread = models.Thread.objects.get(id=thread_id)
        success = index_thread(thread)

        return {
            "status": "success" if success else "error",
            "thread": thread_id,
            "indexed_messages": thread.messages.count() if success else 0,
        }
    except models.Thread.DoesNotExist:
        return {"status": "error", "thread": thread_id, "error": "Thread not found"}
    # pylint: disable=broad-exception-caught
    except Exception as e:
        return {"status": "error", "thread": thread_id, "error": str(e)}
