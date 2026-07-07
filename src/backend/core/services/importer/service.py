"""Start an import run: validate/detect the source, create its channel,
dispatch the task.

Deliberately does NOT authorize: the only caller is the imports API, which
gates on the ``IsMailboxAdmin`` permission for the URL mailbox (see
``docs/permissions.md``). Failure dicts carry an HTTP-ish ``status`` so the
viewset can map "your upload is wrong" (400) vs "missing" (404) vs "broke"
(500).
"""

import logging
from typing import Any

from django.conf import settings
from django.core.files.storage import storages

import magic

from core import enums
from core.models import Mailbox

from .channel import create_import_channel
from .tasks import run_import_task

logger = logging.getLogger(__name__)


def start_file_import(
    file_key: str,
    recipient: Mailbox,
    user: Any,
    filename: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Start an import from an uploaded EML, MBOX, or PST file.

    Args:
        file_key: The storage key of the uploaded file
        recipient: The recipient mailbox
        user: The user performing the import
        filename: Original filename for MIME type disambiguation

    Returns:
        Tuple of (success, response_data)
    """
    message_imports_storage = storages["message-imports"]

    if not message_imports_storage.exists(file_key):
        return False, {"detail": "File not found.", "status": 404}

    s3_client = message_imports_storage.connection.meta.client

    # Reject an archive larger than the cap before spending a worker on it.
    max_size = settings.MESSAGES_IMPORT_MAX_FILE_SIZE
    if max_size:
        size = s3_client.head_object(
            Bucket=message_imports_storage.bucket_name, Key=file_key
        ).get("ContentLength", 0)
        if size > max_size:
            return False, {
                "detail": (
                    f"File too large ({size} bytes); the maximum import size "
                    f"is {max_size} bytes."
                )
            }

    # Detect content type from actual file bytes using python-magic
    head = s3_client.get_object(
        Bucket=message_imports_storage.bucket_name,
        Key=file_key,
        Range="bytes=0-2047",
    )["Body"].read()

    # RFC 4155: an mbox file starts with a "From " envelope line at offset 0.
    # Trust that signature first — libmagic can otherwise misclassify mbox
    # files whose first message body contains HTML as text/html.
    if head.startswith(b"From "):
        content_type = "application/mbox"
    else:
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

    # Map the detected MIME family to its import source type + label.
    if content_type in enums.PST_SUPPORTED_MIME_TYPES:
        source, label = enums.ImportSource.PST, "PST"
    elif content_type in enums.MBOX_SUPPORTED_MIME_TYPES:
        source, label = enums.ImportSource.MBOX, "MBOX"
    elif content_type in enums.EML_SUPPORTED_MIME_TYPES:
        source, label = enums.ImportSource.EML, "EML"
    else:
        return False, {"detail": f"Unsupported file format: {content_type}"}

    try:
        # Group every message of this import under a Channel so the run is
        # trackable, resumable and cancellable via /imports/{id}/. The
        # single ``run_import_task`` reads its config back off the channel.
        channel = create_import_channel(
            recipient=recipient,
            user=user,
            source_type=source.value,
            file_key=file_key,
            name=f"Import {filename}" if filename else f"Import {label}",
        )
        run_import_task.delay(str(channel.id))
        return True, {
            "type": source.value,
            "import_id": str(channel.id),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error processing file: %s", e)
        return False, {
            "detail": "An error occurred while processing the file.",
            "status": 500,
        }


def start_imap_import(
    imap_server: str,
    imap_port: int,
    username: str,
    password: str,
    recipient: Mailbox,
    user: Any,
    use_ssl: bool = True,
    mode: str = enums.ImportMode.ONESHOT.value,
) -> tuple[bool, dict[str, Any]]:
    """Start an import from a live IMAP account.

    Args:
        imap_server: IMAP server hostname
        imap_port: IMAP server port
        username: Email address for login
        password: Password for login
        recipient: The recipient mailbox
        user: The user performing the import
        use_ssl: Whether to use SSL
        mode: ``oneshot`` (default) or ``continuous`` (re-poll on the global
            interval); continuous is IMAP-only.

    Returns:
        Tuple of (success, response_data)
    """
    try:
        # Group the run under a Channel; IMAP credentials are stored
        # encrypted on it so the single ``run_import_task`` can read them
        # back and resume (and, once continuous, poll) with only the
        # channel id.
        channel = create_import_channel(
            recipient=recipient,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            name=f"Import IMAP {imap_server}",
            mode=mode,
            imap_credentials={
                "imap_server": imap_server,
                "imap_port": imap_port,
                "username": username,
                "password": password,
                "use_ssl": use_ssl,
            },
        )
        run_import_task.delay(str(channel.id))
        return True, {
            "type": "imap",
            "import_id": str(channel.id),
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error starting IMAP import: %s", e)
        return False, {
            "detail": "An error occurred while starting the IMAP import.",
            "status": 500,
        }
