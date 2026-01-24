"""Service layer for importing messages via EML, MBOX, or IMAP."""

import logging
from typing import Any, Dict, Optional, Tuple

from django.contrib import messages
from django.core.files.storage import storages
from django.http import HttpRequest

from core import enums
from core.api.viewsets.task import register_task_owner
from core.models import Mailbox

from .tasks import (
    import_imap_messages_task,
    process_eml_file_task,
    process_mbox_file_task,
)

logger = logging.getLogger(__name__)


class ImportService:
    """Service for handling message imports."""

    @staticmethod
    def import_file(
        file_key: str,
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
        # Check user has edit access to mailbox in case of non superuser
        if (
            not user.is_superuser
            and not recipient.accesses.filter(
                user=user, role__in=enums.MAILBOX_ROLES_CAN_EDIT
            ).exists()
        ):
            return False, {"detail": "You do not have access to this mailbox."}

        message_imports_storage = storages["message-imports"]

        if not message_imports_storage.exists(file_key):
            return False, {"detail": "File not found."}

        # We retrieve the content type from the file metadata as we need to make a quick check
        # but this is not guaranteed to be correct so we have to check the file content again in the task
        s3_client = message_imports_storage.connection.meta.client
        unsafe_content_type = s3_client.head_object(
            Bucket=message_imports_storage.bucket_name, Key=file_key
        ).get("ContentType")

        if unsafe_content_type not in enums.ARCHIVE_SUPPORTED_MIME_TYPES:
            return False, {
                "detail": (
                    "Invalid file format. Only EML (message/rfc822) and MBOX "
                    "(application/octet-stream, application/mbox, or text/plain) files are supported. "
                    "Detected content type: {content_type}"
                ).format(content_type=unsafe_content_type)
            }

        try:
            # Check MIME type for MBOX
            if unsafe_content_type in enums.MBOX_SUPPORTED_MIME_TYPES:
                # Process MBOX file asynchronously
                task = process_mbox_file_task.delay(file_key, str(recipient.id))
                register_task_owner(task.id, user.id)
                response_data = {"task_id": task.id, "type": "mbox"}
                if request:
                    messages.info(
                        request,
                        f"Started processing MBOX file for recipient {recipient}. "
                        "This may take a while. You can check the status in the Celery task monitor.",
                    )
                return True, response_data
            # Check MIME type for EML
            elif unsafe_content_type in enums.EML_SUPPORTED_MIME_TYPES:
                # Process EML file asynchronously
                task = process_eml_file_task.delay(file_key, str(recipient.id))
                register_task_owner(task.id, user.id)
                response_data = {"task_id": task.id, "type": "eml"}
                if request:
                    messages.info(
                        request,
                        f"Started processing EML file for recipient {recipient}. "
                        "This may take a while. You can check the status in the Celery task monitor.",
                    )
                return True, response_data
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
            request: Optional HTTP request for admin messages

        Returns:
            Tuple of (success, response_data)
        """
        # Check user has edit access to mailbox in case of non superuser
        if (
            not user.is_superuser
            and not recipient.accesses.filter(
                user=user, role__in=enums.MAILBOX_ROLES_CAN_EDIT
            ).exists()
        ):
            return False, {"detail": "You do not have access to this mailbox."}

        try:
            # Start the import task
            task = import_imap_messages_task.delay(
                imap_server=imap_server,
                imap_port=imap_port,
                username=username,
                password=password,
                use_ssl=use_ssl,
                recipient_id=str(recipient.id),
            )
            register_task_owner(task.id, user.id)
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
