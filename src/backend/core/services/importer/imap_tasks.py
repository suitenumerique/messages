"""IMAP import task."""

# pylint: disable=broad-exception-caught
from typing import Any, Dict

from celery.utils.log import get_task_logger

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

            # Count total messages and cache message lists per folder
            folder_messages = {}
            for folder_name in folders_to_process:
                if select_imap_folder(imap, folder_name):
                    message_list = get_message_numbers(
                        imap, folder_name, username, imap_server
                    )
                    if message_list:
                        folder_messages[folder_name] = message_list
                        total_messages += len(message_list)

            # Process each folder (reusing cached message lists)
            for folder_to_process in folders_to_process:
                if folder_to_process not in folder_messages:
                    continue

                display_name = folder_mapping.get(folder_to_process, folder_to_process)
                message_list = folder_messages[folder_to_process]

                # Re-select folder for processing
                if not select_imap_folder(imap, folder_to_process):
                    logger.warning(
                        "Skipping folder %s - could not select it", folder_to_process
                    )
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
        return {"status": "FAILURE", "result": result, "error": error_msg}

    except Exception as e:
        logger.exception("Error in import_imap_messages_task: %s", e)

        result = {
            "message_status": "Failed to process messages",
            "total_messages": total_messages,
            "success_count": success_count,
            "failure_count": failure_count,
            "type": "imap",
            "current_message": current_message,
        }
        return {"status": "FAILURE", "result": result, "error": str(e)}
