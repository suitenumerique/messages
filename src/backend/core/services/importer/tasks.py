"""Import-related tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines
from typing import Any, Dict, Generator

from django.core.files.storage import storages

import magic
from celery.utils.log import get_task_logger

from core import enums
from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox

from messages.celery_app import app as celery_app

from .imap import (
    IMAPConnectionManager,
    create_folder_mapping,
    get_message_numbers,
    get_selectable_folders,
    process_folder_messages,
    select_imap_folder,
)

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def process_mbox_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process a MBOX file asynchronously.

    Args:
        file_key: The storage key of the MBOX file
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

    try:
        # Get storage and open file
        message_imports_storage = storages["message-imports"]

        with message_imports_storage.open(file_key, "rb") as file:
            self.update_state(
                state="PROGRESS",
                meta={
                    "result": {
                        "message_status": "Initializing import",
                        "type": "mbox",
                    },
                    "error": None,
                },
            )
            # Ensure file is a valid mbox file
            content_type = magic.from_buffer(file.read(2048), mime=True)
            if content_type not in enums.MBOX_SUPPORTED_MIME_TYPES:
                raise Exception(f"Expected MBOX file, got {content_type}")

            # First pass: scan for message positions (also gives us the count)
            file.seek(0)
            message_positions, file_end = scan_mbox_messages(file)
            total_messages = len(message_positions)

            # Second pass: process messages using pre-computed positions
            for i, message_content in enumerate(
                stream_mbox_messages(file, message_positions, file_end), 1
            ):
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

    except Exception as e:
        logger.exception(
            "Error processing MBOX file for recipient %s: %s",
            recipient_id,
            e,
        )
        error_msg = str(e)
        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "mbox",
            "current_message": current_message,
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


def scan_mbox_messages(file) -> tuple[list[int], int]:
    """
    Scan an MBOX file and return message positions without loading content into memory.

    This function performs a single pass through the file to record the byte offset
    where each message starts. The count of messages is simply len(positions).

    Args:
        file: File-like object to read from

    Returns:
        A tuple of (message_positions, file_end) where:
        - message_positions: list of byte offsets where each message starts
        - file_end: byte offset of end of file (needed to compute last message size)
    """
    message_positions = []
    position = 0

    for line in file:
        if line.startswith(b"From "):
            message_positions.append(position)
        position += len(line)

    return message_positions, position


def stream_mbox_messages(
    file, message_positions: list[int], file_end: int | None = None
) -> Generator[bytes, None, None]:
    """
    Stream individual email messages from an MBOX file without loading everything into memory.

    Yields messages in reverse order (oldest first) for proper reply threading,
    since mbox files store messages with the most recent first.

    Args:
        file: File-like object to read from (must support seek)
        message_positions: Pre-computed list of byte offsets where messages start.
        file_end: Byte offset of end of file.

    Yields:
        Individual email messages as bytes
    """
    if message_positions is None or file_end is None:
        logger.warning(
            "Cannot stream MBOX messages: message positions or file end not provided"
        )
        return

    # Read messages in reverse order for chronological processing
    # Process from last message to first (oldest to newest in real time)
    for i in range(len(message_positions) - 1, -1, -1):
        start_pos = message_positions[i]
        # End position is either the next message start or end of file
        end_pos = (
            message_positions[i + 1] if i + 1 < len(message_positions) else file_end
        )

        # Seek to message start
        file.seek(start_pos)

        # Read the message content (excluding the "From " line)
        first_line = file.readline()  # Skip the "From " separator line
        content_start = start_pos + len(first_line)
        content_length = end_pos - content_start

        # Read just this message's content
        message_content = file.read(content_length)
        yield message_content


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
            selectable_folders = get_selectable_folders(imap, username, imap_server)

            # Process all folders
            folders_to_process = selectable_folders

            # Create folder mapping
            folder_mapping = create_folder_mapping(
                selectable_folders, username, imap_server
            )

            # Calculate total messages across all folders
            for folder_name in folders_to_process:
                if select_imap_folder(imap, folder_name):
                    message_list = get_message_numbers(
                        imap, folder_name, username, imap_server
                    )
                    total_messages += len(message_list)

            # Process each folder

            for folder_to_process in folders_to_process:
                display_name = folder_mapping.get(folder_to_process, folder_to_process)

                # Select folder
                if not select_imap_folder(imap, folder_to_process):
                    logger.warning(
                        "Skipping folder %s - could not select it", folder_to_process
                    )
                    continue

                # Get message numbers
                message_list = get_message_numbers(
                    imap, folder_to_process, username, imap_server
                )
                if not message_list:
                    logger.info("No messages found in folder %s", folder_to_process)
                    continue

                # Process messages in this folder
                success_count, failure_count, current_message = process_folder_messages(
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
def process_eml_file_task(self, file_key: str, recipient_id: str) -> Dict[str, Any]:
    """
    Process an EML file asynchronously.

    Args:
        file_key: The storage key of the EML file
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

        # Get storage and read file
        message_imports_storage = storages["message-imports"]
        with message_imports_storage.open(file_key, "rb") as file:
            content_type = magic.from_buffer(file.read(2048), mime=True)
            if content_type not in enums.EML_SUPPORTED_MIME_TYPES:
                raise Exception(f"Expected EML file, got {content_type}")

            file.seek(0)
            file_content = file.read()

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
