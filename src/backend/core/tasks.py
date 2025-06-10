"""Core tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught
import imaplib
from typing import Any, Dict, List, Tuple

from django.conf import settings

from celery.utils.log import get_task_logger

from core import models
from core.mda.inbound import deliver_inbound_message
from core.mda.outbound import send_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox, Thread, Label
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


@celery_app.task(bind=True)
def process_mbox_file_task(
    self, file_content: bytes, recipient_id: str
) -> Tuple[int, int]:
    """
    Process a MBOX file asynchronously.

    Args:
        file_content: The content of the MBOX file
        recipient_id: The UUID of the recipient mailbox

    Returns:
        Tuple of (success_count, failure_count)
    """
    success_count = 0
    failure_count = 0

    try:
        recipient = Mailbox.objects.get(id=recipient_id)
    except Mailbox.DoesNotExist:
        logger.error("Recipient mailbox %s not found", recipient_id)
        return success_count, failure_count

    # Split the mbox file into individual messages
    messages = split_mbox_file(file_content)

    for message_content in messages:
        try:
            # Parse the email message
            parsed_email = parse_email_message(message_content)
            
            # Extract labels from X-Gmail-Labels header
            labels = []
            if 'X-Gmail-Labels' in parsed_email.get('headers', {}):
                # Handle both plain and encoded labels
                labels_str = parsed_email['headers']['X-Gmail-Labels']
                if labels_str.startswith('=?'):
                    # Decode quoted-printable encoded labels
                    from email.header import decode_header
                    decoded_labels = decode_header(labels_str)
                    labels_str = ' '.join(
                        label.decode(charset or 'utf-8') if isinstance(label, bytes) else label
                        for label, charset in decoded_labels
                    )
                labels = [label.strip() for label in labels_str.split(',')]

            # Deliver the message
            if deliver_inbound_message(
                str(recipient), parsed_email, message_content, is_import=True
            ):
                # Create labels and associate with the thread
                thread = Thread.objects.filter(
                    messages__mime_id=parsed_email.get('headers', {}).get('Message-ID')
                ).first()
                
                if thread:
                    for label_name in labels:
                        # Create or get label
                        label, _ = Label.objects.get_or_create(
                            name=label_name,
                            mailbox=recipient,
                            defaults={'color': '#000000'}  # Default color
                        )
                        # Add thread to label
                        label.threads.add(thread)
                
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Error processing message from mbox file for recipient %s: %s",
                recipient_id,
                e,
            )
            failure_count += 1

    return success_count, failure_count


def split_mbox_file(content: bytes) -> List[bytes]:
    """
    Split a MBOX file into individual email messages.

    Args:
        content: The content of the MBOX file

    Returns:
        List of individual email messages as bytes
    """
    messages = []
    current_message = []
    in_message = False

    for line in content.splitlines(keepends=True):
        # Check for mbox message separator
        if line.startswith(b"From "):
            if in_message:
                # End of previous message
                messages.append(b"".join(current_message))
                current_message = []
            in_message = True
            # Skip the mbox From line
            continue

        if in_message:
            current_message.append(line)

    # Add the last message if there is one
    if current_message:
        messages.append(b"".join(current_message))

    # Last message is the first one, so we need to reverse the list
    # to treat messages replies correctly
    return messages[::-1]


@celery_app.task(bind=True)
def import_imap_messages_task(
    self,
    imap_server: str,
    imap_port: int,
    username: str,
    password: str,
    use_ssl: bool,
    folder: str,
    max_messages: int,
    recipient_id: str,
) -> Dict[str, Any]:
    """Import messages from an IMAP server.

    Args:
        imap_server: IMAP server hostname
        imap_port: IMAP server port
        username: Email address for login
        password: Password for login
        use_ssl: Whether to use SSL
        folder: IMAP folder to import from
        max_messages: Maximum number of messages to import (0 for all)
        recipient_id: ID of the recipient mailbox

    Returns:
        Dictionary with import statistics
    """
    try:
        # Connect to IMAP server
        if use_ssl:
            imap = imaplib.IMAP4_SSL(imap_server, imap_port)
        else:
            imap = imaplib.IMAP4(imap_server, imap_port)

        # Login

        imap.login(username, password)

        # Select folder
        status, messages = imap.select(folder)
        if status != "OK":
            raise Exception(f"Failed to select folder {folder}: {messages}")

        # Search for all messages
        status, message_numbers = imap.search(None, "ALL")
        if status != "OK":
            raise Exception(f"Failed to search messages: {message_numbers}")

        # Get list of message numbers
        message_list = message_numbers[0].split()

        # Apply max_messages limit if specified
        if max_messages > 0:
            message_list = message_list[-max_messages:]  # Get most recent messages

        total_messages = len(message_list)
        success_count = 0
        failure_count = 0

        # Get recipient mailbox
        recipient = Mailbox.objects.get(id=recipient_id)

        # Process each message
        for i, msg_num in enumerate(message_list, 1):
            try:
                # Update task state
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": i,
                        "total": total_messages,
                        "status": f"Processing message {i} of {total_messages}",
                    },
                )

                # Fetch message
                status, msg_data = imap.fetch(msg_num, "(RFC822)")
                if status != "OK":
                    logger.error("Failed to fetch message %s: %s", msg_num, msg_data)
                    failure_count += 1
                    continue

                # Parse message
                raw_email = msg_data[0][1]
                parsed_email = parse_email_message(raw_email)

                # Deliver message
                if deliver_inbound_message(
                    str(recipient), parsed_email, raw_email, is_import=True
                ):
                    success_count += 1
                else:
                    failure_count += 1

            except Exception as e:
                logger.exception("Error processing message %s: %s", msg_num, e)
                failure_count += 1

        # Logout
        imap.close()
        imap.logout()

        return {
            "status": "completed",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
        }

    except Exception as e:
        logger.exception("Error in import_imap_messages_task: %s", e)
        self.update_state(state="FAILURE", meta={"status": "failed", "error": str(e)})
        raise
