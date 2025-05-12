"""Elasticsearch client and indexing functionality."""
# pylint: disable=unexpected-keyword-arg

import logging

from django.conf import settings

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import NotFoundError

from core import enums, models
from core.mda.rfc5322 import parse_email_message
from core.search.mapping import MESSAGE_INDEX, MESSAGE_MAPPING

logger = logging.getLogger(__name__)


# Elasticsearch client instantiation
def get_es_client():
    """Get Elasticsearch client instance."""
    if not hasattr(get_es_client, "cached_client"):
        get_es_client.cached_client = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
    return get_es_client.cached_client


def create_index_if_not_exists():
    """Create ES indices if they don't exist."""
    es = get_es_client()

    # Check if the index exists
    if not es.indices.exists(index=MESSAGE_INDEX):
        # Create the index with our mapping
        es.indices.create(index=MESSAGE_INDEX, **MESSAGE_MAPPING)
        logger.info("Created Elasticsearch index: %s", MESSAGE_INDEX)
    return True


def delete_index():
    """Delete the messages index."""
    es = get_es_client()
    try:
        es.indices.delete(index=MESSAGE_INDEX)
        logger.info("Deleted Elasticsearch index: %s", MESSAGE_INDEX)
        return True
    except NotFoundError:
        logger.warning("Index %s not found, nothing to delete", MESSAGE_INDEX)
        return False


def index_message(message: models.Message) -> bool:
    """Index a single message."""
    es = get_es_client()

    # Parse message content if it has raw MIME
    parsed_data = {}
    if message.raw_mime:
        try:
            parsed_data = parse_email_message(message.raw_mime)
        # pylint: disable=broad-exception-caught
        except Exception as e:  # noqa: BLE001
            logger.error("Error parsing raw MIME for message %s: %s", message.id, e)
            return False

    # Extract text content from parsed data
    text_body = ""
    html_body = ""

    if parsed_data.get("textBody"):
        text_body = " ".join(
            [item.get("content", "") for item in parsed_data.get("textBody", [])]
        )

    if parsed_data.get("htmlBody"):
        html_body = " ".join(
            [item.get("content", "") for item in parsed_data.get("htmlBody", [])]
        )

    # Get recipient details
    recipients = message.recipients.select_related("contact").all()

    # Get mailbox information for this thread
    mailbox_ids = list(message.thread.accesses.values_list("mailbox__id", flat=True))

    # Build document
    doc = {
        "relation": {"name": "message", "parent": str(message.thread_id)},
        "message_id": str(message.id),
        "thread_id": str(message.thread_id),
        "mailbox_ids": [str(mailbox_id) for mailbox_id in mailbox_ids],
        "mime_id": message.mime_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "subject": message.subject,
        "sender_name": message.sender.email + " " + message.sender.name,
        "sender_email": message.sender.email,
        "to_name": [
            r.contact.email + " " + r.contact.name
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.TO
        ],
        "to_email": [
            r.contact.email
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.TO
        ],
        "cc_name": [
            r.contact.email + " " + r.contact.name
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.CC
        ],
        "cc_email": [
            r.contact.email
            for r in recipients
            if r.type == enums.MessageRecipientTypeChoices.CC
        ],
        "bcc_name": [
            r.contact.email + " " + r.contact.name
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
        "is_starred": message.is_starred,
        "is_unread": message.is_unread,
        "is_sender": message.is_sender,
    }

    try:
        # pylint: disable=no-value-for-parameter
        es.index(
            index=MESSAGE_INDEX,
            id=str(message.id),
            routing=str(message.thread_id),  # Ensure parent-child routing
            document=doc,
        )
        logger.debug("Indexed message %s", message.id)
        return True
    # pylint: disable=broad-exception-caught
    except Exception as e:  # noqa: BLE001
        logger.error("Error indexing message %s: %s", message.id, e)
        return False


def index_thread(thread: models.Thread) -> bool:
    """Index a thread and all its messages."""
    es = get_es_client()

    # Get mailbox IDs that have access to this thread
    mailbox_ids = list(thread.accesses.values_list("mailbox__id", flat=True))

    # First, index the thread document
    thread_doc = {
        "relation": "thread",
        "thread_id": str(thread.id),
        "subject": thread.subject,
        "mailbox_ids": [str(mailbox_id) for mailbox_id in mailbox_ids],
    }

    try:
        # Index thread as parent document
        # pylint: disable=no-value-for-parameter
        es.index(index=MESSAGE_INDEX, id=str(thread.id), document=thread_doc)

        # Index all messages in the thread
        messages = thread.messages.all()
        success = True
        for message in messages:
            if not index_message(message):
                success = False

        return success
    # pylint: disable=broad-exception-caught
    except Exception as e:  # noqa: BLE001
        logger.error("Error indexing thread %s: %s", thread.id, e)
        return False


def reindex_all():
    """Reindex all messages and threads."""

    # Delete and recreate the index
    delete_index()
    create_index_if_not_exists()

    # Count indexed items
    indexed_threads = 0
    indexed_messages = 0

    # Index all threads
    for thread in models.Thread.objects.all():
        if index_thread(thread):
            indexed_threads += 1
            indexed_messages += thread.messages.count()

    return {
        "status": "success",
        "indexed_threads": indexed_threads,
        "indexed_messages": indexed_messages,
    }


def reindex_mailbox(mailbox_id: str):
    """Reindex all messages and threads for a specific mailbox."""

    # Count indexed items
    indexed_threads = 0
    indexed_messages = 0

    try:
        # Get the mailbox
        mailbox = models.Mailbox.objects.get(id=mailbox_id)

        # Index all threads the mailbox has access to
        for thread in mailbox.threads_viewer:
            if index_thread(thread):
                indexed_threads += 1
                indexed_messages += thread.messages.count()

        return {
            "status": "success",
            "mailbox": mailbox_id,
            "indexed_threads": indexed_threads,
            "indexed_messages": indexed_messages,
        }
    except models.Mailbox.DoesNotExist:
        return {"status": "error", "mailbox": mailbox_id, "error": "Mailbox not found"}

    # pylint: disable=broad-exception-caught
    except Exception as e:  # noqa: BLE001
        logger.error("Error reindexing mailbox %s: %s", mailbox_id, e)
        return {"status": "error", "mailbox": mailbox_id, "error": str(e)}


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
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "thread": thread_id, "error": str(e)}
