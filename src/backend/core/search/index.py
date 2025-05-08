"""Elasticsearch client and indexing functionality."""

import logging

from django.conf import settings

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import NotFoundError

from core import models
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
        es.indices.create(index=MESSAGE_INDEX, body=MESSAGE_MAPPING)
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
    recipients = []
    for recipient in message.recipients.select_related("contact").all():
        recipients.append(
            {
                "type": recipient.type,
                "name": recipient.contact.name,
                "email": recipient.contact.email,
            }
        )

    # Build document
    doc = {
        "relation": {"name": "message", "parent": str(message.thread_id)},
        "message_id": str(message.id),
        "thread_id": str(message.thread_id),
        "mailbox_id": str(message.thread.mailbox_id),
        "mime_id": message.mime_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "subject": message.subject,
        "sender": {"name": message.sender.name, "email": message.sender.email},
        "recipients": recipients,
        "text_body": text_body,
        "html_body": html_body,
        "is_draft": message.is_draft,
        "is_trashed": message.is_trashed,
        "is_starred": message.is_starred,
        "is_unread": message.is_unread,
    }

    try:
        es.index(
            index=MESSAGE_INDEX,
            id=str(message.id),
            routing=str(message.thread_id),  # Ensure parent-child routing
            body=doc,
        )
        logger.debug("Indexed message %s", message.id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Error indexing message %s: %s", message.id, e)
        return False


def index_thread(thread: models.Thread) -> bool:
    """Index a thread and all its messages."""
    es = get_es_client()

    # First, index the thread document
    thread_doc = {
        "relation": "thread",
        "thread_id": str(thread.id),
        "mailbox_id": str(thread.mailbox_id),
        "subject": thread.subject,
    }

    try:
        # Index thread as parent document
        es.index(index=MESSAGE_INDEX, id=str(thread.id), body=thread_doc)

        # Index all messages in the thread
        messages = thread.messages.all()
        success = True
        for message in messages:
            if not index_message(message):
                success = False

        return success
    except Exception as e:  # noqa: BLE001
        logger.error("Error indexing thread %s: %s", thread.id, e)
        return False


def reindex_all():
    """Reindex all messages and threads."""
    from core.models import Thread

    # Delete and recreate the index
    delete_index()
    create_index_if_not_exists()

    # Count indexed items
    indexed_threads = 0
    indexed_messages = 0

    # Index all threads
    for thread in Thread.objects.all():
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
    from core.models import Thread

    # Count indexed items
    indexed_threads = 0
    indexed_messages = 0

    # Index all threads in the mailbox
    for thread in Thread.objects.filter(mailbox_id=mailbox_id):
        if index_thread(thread):
            indexed_threads += 1
            indexed_messages += thread.messages.count()

    return {
        "status": "success",
        "mailbox": mailbox_id,
        "indexed_threads": indexed_threads,
        "indexed_messages": indexed_messages,
    }


def reindex_thread(thread_id: str):
    """Reindex a specific thread."""
    from core.models import Thread

    try:
        thread = Thread.objects.get(id=thread_id)
        success = index_thread(thread)

        return {
            "status": "success" if success else "error",
            "thread": thread_id,
            "indexed_messages": thread.messages.count() if success else 0,
        }
    except Thread.DoesNotExist:
        return {"status": "error", "thread": thread_id, "error": "Thread not found"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "thread": thread_id, "error": str(e)}
