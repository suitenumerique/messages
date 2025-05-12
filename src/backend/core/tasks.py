"""Core tasks."""

# pylint: disable=unused-argument
from django.conf import settings

from celery.utils.log import get_task_logger

from core import models
from core.mda.outbound import send_message
from core.search import (
    create_index_if_not_exists,
    delete_index,
    index_message,
    index_thread,
)

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def send_message_task(self, message_id, force_mta_out=False):
    """Send a message asynchronously.

    Args:
        message_id: The ID of the message to send
        mime_data: The MIME data dictionary
        force_mta_out: Whether to force sending via MTA

    Returns:
        dict: A dictionary with success status and info
    """
    try:
        message = (
            models.Message.objects.select_related("thread", "sender")
            .prefetch_related("recipients__contact")
            .get(id=message_id)
        )

        send_message(message, force_mta_out)

        # Update task state with progress information
        self.update_state(
            state="SUCCESS",
            meta={
                "status": "completed",  # TODO fetch recipients statuses
                "message_id": str(message_id),
                "success": True,
            },
        )

        return {
            "message_id": str(message_id),
            "success": True,
        }
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in send_message_task for message %s: %s", message_id, e)
        self.update_state(
            state="FAILURE",
            meta={"status": "failed", "message_id": str(message_id), "error": str(e)},
        )
        raise


@celery_app.task(bind=True)
def reindex_all(self):
    """Reindex all threads and messages."""
    if not settings.ELASTICSEARCH_INDEX_THREADS:
        logger.info("Elasticsearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get all threads and index them
        threads = models.Thread.objects.all()
        total = threads.count()
        success_count = 0
        failure_count = 0

        for i, thread in enumerate(threads):
            try:
                if index_thread(thread):
                    success_count += 1
                else:
                    failure_count += 1
            # pylint: disable=broad-exception-caught
            except Exception as e:
                failure_count += 1
                logger.exception("Error indexing thread %s: %s", thread.id, e)

            # Update progress every 100 threads
            if i % 100 == 0:
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": i,
                        "total": total,
                        "success_count": success_count,
                        "failure_count": failure_count,
                    },
                )

        return {
            "success": True,
            "total": total,
            "success_count": success_count,
            "failure_count": failure_count,
        }
    # pylint: disable=broad-exception-caught
    except Exception as e:
        logger.exception("Error in reindex_all task: %s", e)
        raise


@celery_app.task(bind=True)
def reindex_thread_task(self, thread_id):
    """Reindex a specific thread and all its messages."""
    if not settings.ELASTICSEARCH_INDEX_THREADS:
        logger.info("Elasticsearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get the thread
        thread = models.Thread.objects.get(id=thread_id)

        # Index the thread
        success = index_thread(thread)

        return {
            "thread_id": str(thread_id),
            "success": success,
        }
    except models.Thread.DoesNotExist:
        logger.error("Thread %s does not exist", thread_id)
        return {
            "thread_id": str(thread_id),
            "success": False,
            "error": f"Thread {thread_id} does not exist",
        }
    except Exception as e:
        logger.exception("Error in reindex_thread_task for thread %s: %s", thread_id, e)
        raise


@celery_app.task(bind=True)
def reindex_mailbox_task(self, mailbox_id):
    """Reindex all threads and messages in a specific mailbox."""
    if not settings.ELASTICSEARCH_INDEX_THREADS:
        logger.info("Elasticsearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get all threads in the mailbox
        threads = models.Mailbox.objects.get(id=mailbox_id).threads_viewer
        total = threads.count()
        success_count = 0
        failure_count = 0

        for i, thread in enumerate(threads):
            try:
                if index_thread(thread):
                    success_count += 1
                else:
                    failure_count += 1
            # pylint: disable=broad-exception-caught
            except Exception as e:
                failure_count += 1
                logger.exception("Error indexing thread %s: %s", thread.id, e)

            # Update progress every 50 threads
            if i % 50 == 0:
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": i,
                        "total": total,
                        "success_count": success_count,
                        "failure_count": failure_count,
                    },
                )

        return {
            "mailbox_id": str(mailbox_id),
            "success": True,
            "total": total,
            "success_count": success_count,
            "failure_count": failure_count,
        }
    except Exception as e:
        logger.exception(
            "Error in reindex_mailbox_task for mailbox %s: %s", mailbox_id, e
        )
        raise


@celery_app.task(bind=True)
def index_message_task(self, message_id):
    """Index a single message."""
    if not settings.ELASTICSEARCH_INDEX_THREADS:
        logger.info("Elasticsearch message indexing is disabled.")
        return {"success": False, "reason": "disabled"}

    try:
        # Ensure index exists first
        create_index_if_not_exists()

        # Get the message
        message = (
            models.Message.objects.select_related("thread", "sender")
            .prefetch_related("recipients__contact")
            .get(id=message_id)
        )

        # Index the message
        success = index_message(message)

        return {
            "message_id": str(message_id),
            "thread_id": str(message.thread_id),
            "success": success,
        }
    except models.Message.DoesNotExist:
        logger.error("Message %s does not exist", message_id)
        return {
            "message_id": str(message_id),
            "success": False,
            "error": f"Message {message_id} does not exist",
        }
    except Exception as e:
        logger.exception(
            "Error in index_message_task for message %s: %s", message_id, e
        )
        raise


@celery_app.task(bind=True)
def reset_elasticsearch_index(self):
    """Delete and recreate the Elasticsearch index."""
    try:
        delete_index()
        create_index_if_not_exists()
        return {"success": True}
    except Exception as e:
        logger.exception("Error resetting Elasticsearch index: %s", e)
        raise


# @celery_app.task(bind=True)
# def check_maildomain_dns(self, maildomain_id):
#     """Check if the DNS records for a mail domain are correct."""

#     maildomain = models.MailDomain.objects.get(id=maildomain_id)
#     expected_records = maildomain.get_expected_dns_records()
#     for record in expected_records:
#         res = dns.resolver.resolve(
#             record["target"], record["type"], raise_on_no_answer=False, lifetime=10
#         )
#         print(res)
#         print(record)
#     return {"success": True}
