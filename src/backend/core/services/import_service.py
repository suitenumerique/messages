"""Service layer for importing messages via EML, MBOX, or IMAP."""

import logging
from typing import Any, Dict, Optional, Tuple

from django.contrib import messages
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message
from core.models import Mailbox
from core.tasks import import_imap_messages_task, process_mbox_file_task

logger = logging.getLogger(__name__)


class ImportService:
    """Service for handling message imports."""

    @staticmethod
    def import_file(
        file: UploadedFile,
        recipient: Mailbox,
        user: Any,
        request: Optional[HttpRequest] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Import messages from an EML or MBOX file.

        Args:
            file: The uploaded file (EML or MBOX)
            recipient: The recipient mailbox
            user: The user performing the import
            request: Optional HTTP request for admin messages

        Returns:
            Tuple of (success, response_data)
        """
        # Check user has access to mailbox in case of non superuser
        if not user.is_superuser and not recipient.accesses.filter(user=user).exists():
            return False, {"detail": "You do not have access to this mailbox."}

        try:
            file_content = file.read()

            if file.name.endswith(".mbox"):
                # Process MBOX file asynchronously
                task = process_mbox_file_task.delay(file_content, str(recipient.id))
                response_data = {"task_id": task.id, "type": "mbox"}
                if request:
                    messages.info(
                        request,
                        f"Started processing MBOX file: {file.name} for recipient {recipient}. "
                        "This may take a while. You can check the status in the Celery task monitor.",
                    )
                return True, response_data
            else:
                # Process EML file synchronously
                parsed_email = parse_email_message(file_content)
                success = deliver_inbound_message(
                    str(recipient), parsed_email, file_content, is_import=True
                )
                response_data = {"success": success, "type": "eml"}
                if request:
                    if success:
                        messages.success(
                            request,
                            f"Successfully processed EML file: {file.name} for recipient {recipient}",
                        )
                    else:
                        messages.error(
                            request,
                            f"Failed to process EML file: {file.name} for recipient {recipient}",
                        )
                return success, response_data

        except Exception as e:
            logger.exception("Error processing file: %s", e)
            if request:
                messages.error(request, f"Error processing file: {str(e)}")
            return False, {"detail": str(e)}

    @staticmethod
    def import_imap(
        imap_server: str,
        imap_port: int,
        username: str,
        password: str,
        recipient: Mailbox,
        user: Any,
        use_ssl: bool = True,
        folder: str = "INBOX",
        max_messages: int = 0,
        request: Optional[HttpRequest] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Import messages from an IMAP server.

        Args:
            imap_server: IMAP server hostname
            imap_port: IMAP server port
            username: Email address for login
            password: Password for login
            recipient: The recipient mailbox
            user: The user performing the import
            use_ssl: Whether to use SSL
            folder: IMAP folder to import from
            max_messages: Maximum number of messages to import (0 for all)
            request: Optional HTTP request for admin messages

        Returns:
            Tuple of (success, response_data)
        """
        # Check user has access to mailbox in case of non superuser
        if not user.is_superuser and not recipient.accesses.filter(user=user).exists():
            return False, {"detail": "You do not have access to this mailbox."}

        try:
            # Start the import task
            task = import_imap_messages_task.delay(
                imap_server=imap_server,
                imap_port=imap_port,
                username=username,
                password=password,
                use_ssl=use_ssl,
                folder=folder,
                max_messages=max_messages,
                recipient_id=str(recipient.id),
            )
            response_data = {"task_id": task.id, "type": "imap"}
            if request:
                messages.info(
                    request,
                    f"Started importing messages from IMAP server for recipient {recipient}. "
                    "This may take a while. You can check the status in the Celery task monitor.",
                )
            return True, response_data

        except Exception as e:
            logger.exception("Error starting IMAP import: %s", e)
            if request:
                messages.error(request, f"Error starting IMAP import: {str(e)}")
            return False, {"detail": str(e)}
