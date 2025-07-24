"""Core tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines
import base64
import codecs
import imaplib
import re
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from celery.utils.log import get_task_logger

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.mda.inbound import deliver_inbound_message
from core.mda.outbound import send_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox
from core.search import (
    create_index_if_not_exists,
    delete_index,
    index_message,
    index_thread,
)

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


def decode_imap_utf7(s):
    """Decode IMAP UTF-7 encoded string to UTF-8.

    Args:
        s: UTF-7 encoded string

    Returns:
        Decoded UTF-8 string
    """

    def decode_match(match):
        b64_text = match.group(1)
        if not b64_text:
            return "&"
        b64_text = b64_text.replace(",", "/")
        decoded_bytes = base64.b64decode(b64_text + "===")
        return decoded_bytes.decode("utf-16-be")

    return re.sub(r"&([^-]*)-", decode_match, s)


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
def retry_messages_task(self, message_id=None, force_mta_out=False, batch_size=100):
    """Retry sending messages with retryable recipients (respects retry timing).

    Args:
        message_id: Optional specific message ID to retry
        force_mta_out: Whether to force sending via MTA
        batch_size: Number of messages to process in each batch

    Returns:
        dict: A dictionary with task status and results
    """
    # Get messages to process
    if message_id:
        # Single message mode
        try:
            message = models.Message.objects.get(id=message_id)
        except models.Message.DoesNotExist:
            error_msg = f"Message with ID '{message_id}' does not exist"
            return {"success": False, "error": error_msg}

        if message.is_draft:
            error_msg = f"Message '{message_id}' is still a draft and cannot be sent"
            return {"success": False, "error": error_msg}

        messages_to_process = [message]
        total_messages = 1
    else:
        # Bulk mode - find all messages with retryable recipients that are ready for retry
        message_filter_q = Q(
            is_draft=False,
            recipients__delivery_status=MessageDeliveryStatusChoices.RETRY,
        ) & (
            Q(recipients__retry_at__isnull=True)
            | Q(recipients__retry_at__lte=timezone.now())
        )

        messages_to_process = list(
            models.Message.objects.filter(message_filter_q).distinct()
        )
        total_messages = len(messages_to_process)

    if total_messages == 0:
        return {
            "success": True,
            "total_messages": 0,
            "processed_messages": 0,
            "success_count": 0,
            "error_count": 0,
            "message": "No messages ready for retry",
        }

    # Process messages in batches
    processed_count = 0
    success_count = 0
    error_count = 0

    for batch_start in range(0, total_messages, batch_size):
        batch_messages = messages_to_process[batch_start : batch_start + batch_size]

        # Update progress for bulk operations
        if not message_id:
            self.update_state(
                state="PROGRESS",
                meta={
                    "current_batch": batch_start // batch_size + 1,
                    "total_batches": (total_messages + batch_size - 1) // batch_size,
                    "processed_messages": processed_count,
                    "total_messages": total_messages,
                    "success_count": success_count,
                    "error_count": error_count,
                },
            )

        for message in batch_messages:
            try:
                # Get recipients with retry status that are ready for retry
                retry_filter_q = Q(
                    delivery_status=MessageDeliveryStatusChoices.RETRY
                ) & (Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now()))

                retry_recipients = message.recipients.filter(retry_filter_q)

                if retry_recipients.exists():
                    # Process this message
                    send_message(message, force_mta_out=force_mta_out)
                    success_count += 1
                    logger.info(
                        "Successfully retried message %s (%d recipients)",
                        message.id,
                        retry_recipients.count(),
                    )

                processed_count += 1

            except Exception as e:
                error_count += 1
                logger.exception("Failed to retry message %s: %s", message.id, e)

    # Return appropriate result format
    if message_id:
        return {
            "success": True,
            "message_id": str(message_id),
            "recipients_processed": success_count,
            "processed_messages": processed_count,
            "success_count": success_count,
            "error_count": error_count,
        }

    return {
        "success": True,
        "total_messages": total_messages,
        "processed_messages": processed_count,
        "success_count": success_count,
        "error_count": error_count,
    }


def _reindex_all_base(update_progress=None):
    """Base function for reindexing all threads and messages.

    Args:
        update_progress: Optional callback function to update progress
    """
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

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

        # Update progress if callback provided
        if update_progress and i % 100 == 0:
            update_progress(i, total, success_count, failure_count)

    return {
        "success": True,
        "total": total,
        "success_count": success_count,
        "failure_count": failure_count,
    }


@celery_app.task(bind=True)
def reindex_all(self):
    """Celery task wrapper for reindexing all threads and messages."""

    def update_progress(current, total, success_count, failure_count):
        """Update task progress."""
        self.update_state(
            state="PROGRESS",
            meta={
                "current": current,
                "total": total,
                "success_count": success_count,
                "failure_count": failure_count,
            },
        )

    return _reindex_all_base(update_progress)


@celery_app.task(bind=True)
def reindex_thread_task(self, thread_id):
    """Reindex a specific thread and all its messages."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
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
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch thread indexing is disabled.")
        return {"success": False, "reason": "disabled"}

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


@celery_app.task(bind=True)
def index_message_task(self, message_id):
    """Index a single message."""
    if not settings.OPENSEARCH_INDEX_THREADS:
        logger.info("OpenSearch message indexing is disabled.")
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
def reset_search_index(self):
    """Delete and recreate the OpenSearch index."""

    delete_index()
    create_index_if_not_exists()
    return {"success": True}


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
) -> Dict[str, Any]:
    """
    Process a MBOX file asynchronously.

    Args:
        file_content: The content of the MBOX file
        recipient_id: The UUID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    success_count = 0
    failure_count = 0
    total_messages = 0
    current_message = 0

    try:
        recipient = Mailbox.objects.get(id=recipient_id)
    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process messages",
            "total_messages": 0,
            "success_count": 0,
            "failure_count": 0,
            "type": "mbox",
            "current_message": 0,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    # Split the mbox file into individual messages
    messages = split_mbox_file(file_content)
    total_messages = len(messages)

    for i, message_content in enumerate(messages, 1):
        current_message = i
        try:
            # Update task state with progress
            result = {
                "message_status": f"Processing message {i} of {total_messages}",
                "total_messages": total_messages,
                "success_count": success_count,
                "failure_count": failure_count,
                "type": "mbox",
                "current_message": i,
            }
            self.update_state(
                state="PROGRESS",
                meta={
                    "result": result,
                    "error": None,
                },
            )

            # Parse the email message
            parsed_email = parse_email_message(message_content)
            # Deliver the message
            if deliver_inbound_message(
                str(recipient), parsed_email, message_content, is_import=True
            ):
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

    result = {
        "message_status": "Completed processing messages",
        "total_messages": total_messages,
        "success_count": success_count,
        "failure_count": failure_count,
        "type": "mbox",
        "current_message": current_message,
    }

    self.update_state(
        state="SUCCESS",
        meta={
            "result": result,
            "error": None,
        },
    )

    return {
        "status": "SUCCESS",
        "result": result,
        "error": None,
    }


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


class IMAPConnectionManager:
    """Context manager for IMAP connections with proper cleanup."""

    def __init__(
        self, server: str, port: int, username: str, password: str, use_ssl: bool
    ):
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.connection = None

    def __enter__(self):
        try:
            if self.use_ssl:
                self.connection = imaplib.IMAP4_SSL(self.server, self.port, timeout=30)
            else:
                self.connection = imaplib.IMAP4(self.server, self.port, timeout=30)

            # Set UTF-8 encoding for the IMAP connection
            self.connection._encoding = "utf-8"  # noqa: SLF001

            # Login
            self.connection.login(self.username, self.password)
            return self.connection
        except Exception as e:
            logger.error(
                "Failed to connect to IMAP server %s:%d: %s", self.server, self.port, e
            )
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connection:
            try:
                # Only close if we're in SELECTED state
                if (
                    hasattr(self.connection, "_state")
                    and getattr(self.connection, "_state", None) == "SELECTED"
                ):
                    self.connection.close()
            except Exception as e:
                logger.debug("Error closing IMAP folder: %s", e)
            try:
                self.connection.logout()
            except Exception as e:
                logger.debug("Error during IMAP logout: %s", e)


def _parse_imap_folder_info(folder_info: str) -> Optional[str]:
    """Parse IMAP folder info and return the folder name."""
    try:
        # Skip non-selectable folders
        if "\\Noselect" in folder_info:
            return None

        # Parse IMAP folder info format: (flags) "delimiter" "folder_name"
        parts = folder_info.split('"')
        if len(parts) < 3:
            return None

        if parts[-1] == "":
            folder_name = parts[-2]  # Last quoted string
        else:
            folder_name = parts[-1]  # Last quoted string

        if not folder_name or folder_name == "/":
            return None
        return folder_name
    except Exception as e:
        logger.error("Error parsing folder info '%s': %s", folder_info, e)

    return None


def _get_selectable_folders(
    imap_connection, username: str, imap_server: str
) -> List[str]:
    """Get list of selectable folders from IMAP server."""
    status, folder_list = imap_connection.list()
    if status != "OK":
        raise Exception(f"Failed to list folders: {folder_list}")

    selectable_folders = []
    for folder_info in folder_list:
        folder_name = _parse_imap_folder_info(folder_info.decode())
        if folder_name:
            selectable_folders.append(folder_name)

    return selectable_folders


def _create_folder_mapping(
    folders: List[str], username: str, imap_server: str
) -> Dict[str, str]:
    """Create mapping between technical folder names and display names
    for our internal labels and flags."""
    folder_mapping = {}

    for folder in folders:
        display_name = folder
        technical_name = folder

        # Clean folder names for Orange (remove INBOX/ prefix for display only)
        if "orange.fr" in username.lower() or "orange.fr" in imap_server.lower():
            display_name = folder.strip()
            if display_name.startswith("INBOX/"):
                # Remove "INBOX/" for display
                display_name = display_name.split("/")[-1].strip()

        # Decode the folder name
        display_name = decode_imap_utf7(display_name)

        folder_mapping[technical_name] = display_name

    return folder_mapping


def _select_imap_folder(imap_connection, folder: str) -> bool:
    """Select an IMAP folder with proper encoding handling."""
    try:
        # Try different folder name variations for compatibility
        folder_variations = [
            folder,  # Original folder name
            f'"{folder}"',  # Quoted folder name
        ]

        # For folders that might need INBOX/ prefix
        if not folder.startswith("INBOX/"):
            folder_variations.extend(
                [
                    f"INBOX/{folder}",
                    f'"{folder}"',
                    f'"INBOX/{folder}"',
                ]
            )

        for folder_variant in folder_variations:
            try:
                status, _ = imap_connection.select(folder_variant)
                if status == "OK":
                    logger.info("Successfully selected folder: %s", folder_variant)
                    return True
            except UnicodeEncodeError:
                # If UTF-8 fails, try with UTF-7 encoding (IMAP standard)
                try:
                    utf7_folder = codecs.encode(
                        folder_variant.encode("utf-8"), "utf-7"
                    ).decode("ascii")
                    status, _ = imap_connection.select(utf7_folder)
                    if status == "OK":
                        logger.info(
                            "Successfully selected folder with UTF-7: %s",
                            folder_variant,
                        )
                        return True
                except Exception as e:
                    logger.debug("Failed to select folder with UTF-7 encoding: %s", e)
                    continue
            except Exception as e:
                logger.debug(
                    "Failed to select folder variant %s: %s", folder_variant, e
                )
                continue

        logger.error("Failed to select folder %s with any variation", folder)
        return False

    except Exception as e:
        logger.exception("Error selecting folder %s: %s", folder, e)
        return False


def _get_message_numbers(
    imap_connection, folder: str, username: str, imap_server: str
) -> List[bytes]:
    """Get message numbers from the selected folder."""
    # Search for all messages
    status, message_numbers = imap_connection.search(None, "ALL")

    if status != "OK":
        logger.error(
            "Failed to search messages in folder %s: %s", folder, message_numbers
        )
        return []

    message_list = message_numbers[0].split()

    # If no messages found with ALL, try alternative search criteria
    if not message_list:
        logger.warning(
            "No messages found with ALL search in folder %s, trying alternatives",
            folder,
        )

        search_criteria_list = [
            ("RECENT", "Recent messages"),
            ("UNSEEN", "Unseen messages"),
            ("SEEN", "Seen messages"),
            ("NEW", "New messages"),
            ("OLD", "Old messages"),
        ]

        for criteria, description in search_criteria_list:
            try:
                status, alt_message_numbers = imap_connection.search(None, criteria)
                if status == "OK" and alt_message_numbers[0]:
                    alt_message_list = alt_message_numbers[0].split()
                    if alt_message_list:
                        logger.info(
                            "Found %d messages with %s search in folder %s",
                            len(alt_message_list),
                            description,
                            folder,
                        )
                        message_list = alt_message_list
                        break
            except Exception as e:
                logger.debug("Search criteria %s failed: %s", criteria, e)
                continue

        if not message_list:
            logger.debug(
                "No messages found with any search criteria in folder %s", folder
            )
            return []
    return message_list


def _extract_flags_from_metadata(metadata: bytes) -> List[str]:
    """Extract flags from metadata bytes."""
    flags = []
    metadata_str = metadata.decode(errors="ignore")
    if "FLAGS" in metadata_str:
        flags_match = re.search(r"FLAGS\s*\(([^)]*)\)", metadata_str)
        if flags_match:
            flags_str = flags_match.group(1)
            flags = re.findall(r"\\\w+", flags_str)
    return flags


def _fetch_separate_flags(imap_connection, msg_num: bytes) -> List[str]:
    """Fetch flags separately if not found in main fetch."""
    try:
        status, flags_data = imap_connection.fetch(msg_num, "FLAGS")
        if status == "OK" and flags_data:
            for flags_response in flags_data:
                if isinstance(flags_response, bytes):
                    flags_str = flags_response.decode(errors="ignore")
                    flags_match = re.search(r"FLAGS\s*\(([^)]*)\)", flags_str)
                    if flags_match:
                        flags_str_content = flags_match.group(1)
                        return re.findall(r"\\\w+", flags_str_content)
    except Exception as e:
        logger.debug("Separate flags fetch failed: %s", e)
    return []


def _extract_imap_flags_and_content(msg_data) -> Tuple[List[str], Optional[bytes]]:
    """Extract IMAP flags and raw email content from fetch response."""
    flags = []
    raw_email = None

    # Extract flags and content from the message
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            # response_part[0] contains metadata (flags, etc.)
            # response_part[1] contains message content
            if len(response_part) >= 2:
                metadata = response_part[0]
                content = response_part[1]

                # Extract flags from metadata
                if isinstance(metadata, bytes):
                    flags = _extract_flags_from_metadata(metadata)

                # Extract message content
                if content and isinstance(content, bytes):
                    raw_email = content
        elif isinstance(response_part, bytes):
            # Sometimes content can be directly in response_part
            response_str = response_part.decode(errors="ignore")
            if "FLAGS" in response_str:
                flags_match = re.search(r"FLAGS\s*\(([^)]*)\)", response_str)
                if flags_match:
                    flags_str = flags_match.group(1)
                    flags = re.findall(r"\\\w+", flags_str)
            elif raw_email is None and len(response_part) > 100:
                # If it's not flags, it might be content
                raw_email = response_part

    return flags, raw_email


def _fetch_message_with_flags(
    imap_connection, msg_num: bytes
) -> Tuple[List[str], Optional[bytes]]:
    """Fetch a message with its flags from IMAP server."""
    # Fetch message with flags
    status, msg_data = imap_connection.fetch(msg_num, "(FLAGS BODY.PEEK[])")
    if status != "OK":
        raise Exception(f"Failed to fetch message {msg_num}: {msg_data}")

    flags, raw_email = _extract_imap_flags_and_content(msg_data)

    # If flags not found, try separate FLAGS fetch
    if not flags:
        flags = _fetch_separate_flags(imap_connection, msg_num)

    if raw_email is None:
        raise Exception(f"No raw email found for message {msg_num}")

    return flags, raw_email


def _process_folder_messages(  # pylint: disable=too-many-arguments
    imap_connection: Any,
    folder: str,
    display_name: str,
    message_list: List[bytes],
    recipient: models.Mailbox,
    username: str,
    task_instance: Any,
    success_count: int,
    failure_count: int,
    current_message: int,
    total_messages: int,
) -> Tuple[int, int, int]:
    """Process messages in a specific folder."""
    folder_message_count = len(message_list)
    logger.info("Processing %s messages from folder %s", folder_message_count, folder)

    # Process each message in this folder
    for msg_num in message_list:
        current_message += 1
        try:
            # Fetch message with flags
            flags, raw_email = _fetch_message_with_flags(imap_connection, msg_num)

            # Parse message
            parsed_email = parse_email_message(raw_email)
            if parsed_email["from"]["email"] == username:
                flags.append("is_sender")

            # Deliver message
            if deliver_inbound_message(
                str(recipient),
                parsed_email,
                raw_email,
                is_import=True,
                imap_labels=[display_name],
                imap_flags=flags,
            ):
                success_count += 1
            else:
                failure_count += 1

        except Exception as e:
            logger.exception(
                "Error processing message %s from folder %s: %s",
                msg_num,
                folder,
                e,
            )
            failure_count += 1

        # Update task state after processing the message
        message_status = f"Processing message {current_message} of {total_messages}"
        result = {
            "message_status": message_status,
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }
        task_instance.update_state(
            state="PROGRESS",
            meta={"result": result, "error": None},
        )

    return success_count, failure_count, current_message


@celery_app.task(bind=True)
def import_imap_messages_task(
    self,
    imap_server: str,
    imap_port: int,
    username: str,
    password: str,
    use_ssl: bool,
    recipient_id: str,
) -> Dict[str, Any]:
    """Import messages from an IMAP server.

    Args:
        imap_server: IMAP server hostname
        imap_port: IMAP server port
        username: Email address for login
        password: Password for login
        use_ssl: Whether to use SSL
        recipient_id: ID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    success_count = 0
    failure_count = 0
    total_messages = 0
    current_message = 0

    try:
        # Get recipient mailbox
        recipient = Mailbox.objects.get(id=recipient_id)

        # Connect to IMAP server using context manager
        with IMAPConnectionManager(
            imap_server, imap_port, username, password, use_ssl
        ) as imap:
            # Get selectable folders
            selectable_folders = _get_selectable_folders(imap, username, imap_server)

            # Process all folders
            folders_to_process = selectable_folders

            # Create folder mapping
            folder_mapping = _create_folder_mapping(
                selectable_folders, username, imap_server
            )

            # Calculate total messages across all folders
            for folder_name in folders_to_process:
                if _select_imap_folder(imap, folder_name):
                    message_list = _get_message_numbers(
                        imap, folder_name, username, imap_server
                    )
                    total_messages += len(message_list)

            # Process each folder

            for folder_to_process in folders_to_process:
                display_name = folder_mapping.get(folder_to_process, folder_to_process)

                # Select folder
                if not _select_imap_folder(imap, folder_to_process):
                    logger.warning(
                        "Skipping folder %s - could not select it", folder_to_process
                    )
                    continue

                # Get message numbers
                message_list = _get_message_numbers(
                    imap, folder_to_process, username, imap_server
                )
                if not message_list:
                    logger.info("No messages found in folder %s", folder_to_process)
                    continue

                # Process messages in this folder
                success_count, failure_count, current_message = (
                    _process_folder_messages(
                        imap_connection=imap,
                        folder=folder_to_process,
                        display_name=display_name,
                        message_list=message_list,
                        recipient=recipient,
                        username=username,
                        task_instance=self,
                        success_count=success_count,
                        failure_count=failure_count,
                        current_message=current_message,
                        total_messages=total_messages,
                    )
                )

        # Determine appropriate message status
        if len(folders_to_process) == 1:
            # If only one folder was processed, show which folder it was
            actual_folder = folders_to_process[0]
            message_status = (
                f"Completed processing messages from folder '{actual_folder}'"
            )
        else:
            message_status = "Completed processing messages from all folders"

        result = {
            "message_status": message_status,
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }

        self.update_state(
            state="SUCCESS",
            meta={"status": "SUCCESS", "result": result, "error": None},
        )

        return {"status": "SUCCESS", "result": result, "error": None}

    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process messages",
            "total_messages": 0,
            "success_count": 0,
            "failure_count": 0,
            "type": "imap",
            "current_message": 0,
        }
        self.update_state(state="FAILURE", meta={"result": result, "error": error_msg})
        return {"status": "FAILURE", "result": result, "error": error_msg}

    except Exception as e:
        logger.exception("Error in import_imap_messages_task: %s", e)

        error_msg = str(e)
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }
        self.update_state(state="FAILURE", meta={"result": result, "error": error_msg})
        return {"status": "FAILURE", "result": result, "error": error_msg}


@celery_app.task(bind=True)
def process_eml_file_task(
    self, file_content: bytes, recipient_id: str
) -> Dict[str, Any]:
    """
    Process an EML file asynchronously.

    Args:
        file_content: The content of the EML file
        recipient_id: The UUID of the recipient mailbox

    Returns:
        Dict with task status and result
    """
    try:
        recipient = Mailbox.objects.get(id=recipient_id)
    except Mailbox.DoesNotExist:
        error_msg = f"Recipient mailbox {recipient_id} not found"
        result = {
            "message_status": "Failed to process message",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 0,
            "type": "eml",
            "current_message": 0,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "result": result,
            "error": error_msg,
        }

    try:
        # Update progress state
        progress_result = {
            "message_status": "Processing message 1 of 1",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 0,
            "type": "eml",
            "current_message": 1,
        }
        self.update_state(
            state="PROGRESS",
            meta={
                "result": progress_result,
                "error": None,
            },
        )

        # Parse the email message
        parsed_email = parse_email_message(file_content)
        # Deliver the message
        success = deliver_inbound_message(
            str(recipient), parsed_email, file_content, is_import=True
        )

        result = {
            "message_status": "Completed processing message",
            "total_messages": 1,
            "success_count": 1 if success else 0,
            "failure_count": 0 if success else 1,
            "type": "eml",
            "current_message": 1,
        }

        if success:
            self.update_state(
                state="SUCCESS",
                meta={
                    "result": result,
                    "error": None,
                },
            )
            return {
                "status": "SUCCESS",
                "result": result,
                "error": None,
            }

        error_msg = "Failed to deliver message"
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }

    except Exception as e:
        logger.exception(
            "Error processing EML file for recipient %s: %s",
            recipient_id,
            e,
        )
        error_msg = str(e)
        result = {
            "message_status": "Failed to process message",
            "total_messages": 1,
            "success_count": 0,
            "failure_count": 1,
            "type": "eml",
            "current_message": 1,
        }
        self.update_state(
            state="FAILURE",
            meta={
                "result": result,
                "error": error_msg,
            },
        )
        return {
            "status": "FAILURE",
            "result": result,
            "error": error_msg,
        }
