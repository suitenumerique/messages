"""Service layer for importing messages via EML, MBOX, PST, or IMAP."""

import logging
from typing import Any, Dict, Optional, Tuple

from django.contrib import messages
from django.core.files.storage import storages
from django.http import HttpRequest

import magic
from sentry_sdk import capture_exception

from core import enums
from core.api.viewsets.task import register_task_owner
from core.models import Mailbox

from .eml_tasks import process_eml_file_task
from .imap_tasks import import_imap_messages_task
from .mbox_tasks import process_mbox_file_task
from .pst_tasks import process_pst_file_task

logger = logging.getLogger(__name__)


class ImportService:
    """Service for handling message imports."""

    @staticmethod
    def import_file(
        file_key: str,
        recipient: Mailbox,
        user: Any,
        request: Optional[HttpRequest] = None,
        filename: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Import messages from an EML, MBOX, or PST file.

        Args:
            file_key: The storage key of the uploaded file
            recipient: The recipient mailbox
            user: The user performing the import
            request: Optional HTTP request for admin messages
            filename: Original filename for MIME type disambiguation

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

        # Detect content type from actual file bytes using python-magic
        s3_client = message_imports_storage.connection.meta.client
        head = s3_client.get_object(
            Bucket=message_imports_storage.bucket_name,
            Key=file_key,
            Range="bytes=0-2047",
        )["Body"].read()
        content_type = magic.from_buffer(head, mime=True)

        # Disambiguate ambiguous MIME types using filename extension
        if content_type in ("text/plain", "application/octet-stream") and filename:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            extension_map = {
                "eml": "message/rfc822",
                "mbox": "application/mbox",
                "pst": "application/vnd.ms-outlook",
            }
            content_type = extension_map.get(ext, content_type)

        if content_type not in enums.ARCHIVE_SUPPORTED_MIME_TYPES:
            return False, {
                "detail": (
                    f"Invalid file format. Only EML, MBOX, "
                    f"and PST files are supported. "
                    f"Detected content type: {content_type}"
                )
            }

        try:
            # Check MIME type for PST
            if content_type in enums.PST_SUPPORTED_MIME_TYPES:
                task = process_pst_file_task.delay(file_key, str(recipient.id))
                register_task_owner(task.id, user.id)
                response_data = {"task_id": task.id, "type": "pst"}
                if request:
                    messages.info(
                        request,
                        f"Started processing PST file for recipient {recipient}. "
                        "This may take a while. You can check the status in the Celery task monitor.",
                    )
                return True, response_data
            # Check MIME type for MBOX
            if content_type in enums.MBOX_SUPPORTED_MIME_TYPES:
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
            if content_type in enums.EML_SUPPORTED_MIME_TYPES:
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
            return False, {"detail": f"Unsupported file format: {content_type}"}
        except Exception as e:  # pylint: disable=broad-exception-caught
            capture_exception(e)
            logger.exception("Error processing file: %s", e)
            if request:
                messages.error(request, "Error processing file.")

            return False, {"detail": "An error occurred while processing the file."}

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

        except Exception as e:  # pylint: disable=broad-exception-caught
            capture_exception(e)
            logger.exception("Error starting IMAP import: %s", e)
            if request:
                messages.error(request, "Error starting IMAP import.")
            return False, {
                "detail": "An error occurred while starting the IMAP import."
            }
