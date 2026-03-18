"""OpenSearch client and indexing functionality."""

# pylint: disable=unexpected-keyword-arg

import logging

from django.conf import settings
from django.db.models import Prefetch, prefetch_related_objects

from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError
from opensearchpy.helpers import bulk

from core import enums, models
from core.mda.rfc5322 import parse_email_message
from core.services.search.mapping import MESSAGE_INDEX, MESSAGE_MAPPING

logger = logging.getLogger(__name__)

BULK_CHUNK_SIZE = 100


# OpenSearch client instantiation
def get_opensearch_client():
    """Get OpenSearch client instance."""
    if not hasattr(get_opensearch_client, "cached_client"):
        kwargs = {"hosts": settings.OPENSEARCH_HOSTS}
        if settings.OPENSEARCH_CA_CERTS:
            kwargs["ca_certs"] = settings.OPENSEARCH_CA_CERTS
        get_opensearch_client.cached_client = OpenSearch(**kwargs)
    return get_opensearch_client.cached_client


def create_index_if_not_exists():
    """Create ES indices if they don't exist."""
    es = get_opensearch_client()

    # Check if the index exists
    if not es.indices.exists(index=MESSAGE_INDEX):
        # Create the index with our mapping
        es.indices.create(index=MESSAGE_INDEX, body=MESSAGE_MAPPING)
        logger.info("Created OpenSearch index: %s", MESSAGE_INDEX)
    return True


def delete_index():
    """Delete the messages index."""
    es = get_opensearch_client()
    try:
        es.indices.delete(index=MESSAGE_INDEX)
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
    parsed_data = {}
    try:
        if message.blob:
            parsed_data = parse_email_message(message.blob.get_content())
    except models.Blob.DoesNotExist:
        pass
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.error("Error parsing blob content for message %s: %s", message.id, e)
        return None

    if recipients is None:
        recipients = list(message.recipients.select_related("contact").all())

    text_body = ""
    html_body = ""

    if parsed_data.get("textBody"):
        text_body = " ".join(
            item.get("content", "") for item in parsed_data["textBody"]
        )

    if parsed_data.get("htmlBody"):
        html_body = " ".join(
            item.get("content", "") for item in parsed_data["htmlBody"]
        )

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
        es.index(
            index=MESSAGE_INDEX,
            id=str(message.id),
            routing=str(message.thread_id),  # Ensure parent-child routing
            body=doc,
        )
        logger.debug("Indexed message %s", message.id)
        return True
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
        es.index(index=MESSAGE_INDEX, id=str(thread.id), body=thread_doc)
        return True
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
        es.index(index=MESSAGE_INDEX, id=str(thread.id), body=thread_doc)

        # Index all messages in the thread
        success = True
        for message in thread.messages.all():
            if not index_message(message, mailbox_ids=mailbox_ids):
                success = False

        return success
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.error("Error indexing thread %s: %s", thread.id, e)
        return False


def _bulk_reindex_threads(threads_qs, progress_callback=None):
    """Reindex a queryset of threads using the bulk API for performance.

    Uses chunked prefetching and ``opensearchpy.helpers.bulk`` to minimize
    both DB queries and HTTP calls to OpenSearch.

    Args:
        threads_qs: A ``Thread`` queryset (unordered is fine).
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.

    Returns:
        dict with ``total``, ``indexed_threads``, ``indexed_messages`` and
        ``failure_count``.
    """
    es = get_opensearch_client()

    create_index_if_not_exists()

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

    for thread in threads_qs.iterator(chunk_size=BULK_CHUNK_SIZE):
        mailbox_ids = [str(access.mailbox_id) for access in thread.accesses.all()]
        unread_ids, starred_ids = _compute_unread_starred_from_accesses(thread)

        # Thread action
        thread_doc = _build_thread_doc(thread, mailbox_ids, unread_ids, starred_ids)
        actions.append(
            {
                "_index": MESSAGE_INDEX,
                "_id": str(thread.id),
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
                        "_routing": str(thread.id),
                        "_source": doc,
                    }
                )
                indexed_messages += 1

        indexed_threads += 1

        if len(actions) >= BULK_CHUNK_SIZE:
            _, errors = bulk(es, actions, raise_on_error=False)
            if errors:
                failure_count += len(errors)
                for error in errors:  # pylint: disable=not-an-iterable
                    logger.error("Bulk indexing error: %s", error)
            actions = []

        if progress_callback and indexed_threads % BULK_CHUNK_SIZE == 0:
            progress_callback(indexed_threads, total, indexed_threads, failure_count)

    # Flush remaining actions
    if actions:
        _, errors = bulk(es, actions, raise_on_error=False)
        if errors:
            failure_count += len(errors)
            for error in errors:  # pylint: disable=not-an-iterable
                logger.error("Bulk indexing error: %s", error)

    return {
        "status": "success",
        "total": total,
        "indexed_threads": indexed_threads,
        "indexed_messages": indexed_messages,
        "failure_count": failure_count,
    }


def reindex_all(progress_callback=None):
    """Reindex all threads using the bulk API for performance.

    Args:
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.
    """
    return _bulk_reindex_threads(models.Thread.objects.all(), progress_callback)


def reindex_mailbox(mailbox_id: str, progress_callback=None):
    """Reindex all messages and threads for a specific mailbox.

    Args:
        mailbox_id: The mailbox UUID as a string.
        progress_callback: optional callable(current, total, success_count,
            failure_count) called after each chunk.
    """
    try:
        mailbox = models.Mailbox.objects.get(id=mailbox_id)
    except models.Mailbox.DoesNotExist:
        return {"status": "error", "mailbox": mailbox_id, "error": "Mailbox not found"}

    result = _bulk_reindex_threads(mailbox.threads_viewer, progress_callback)
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
