"""IMAP utilities for message import."""

import base64
import codecs
import imaplib
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from celery.utils.log import get_task_logger

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message

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
                self.connection = imaplib.IMAP4_SSL(
                    self.server, self.port, timeout=settings.IMAP_TIMEOUT
                )
            else:
                self.connection = imaplib.IMAP4(
                    self.server, self.port, timeout=settings.IMAP_TIMEOUT
                )

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


def get_selectable_folders(
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


def create_folder_mapping(
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


def select_imap_folder(imap_connection, folder: str) -> bool:
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


def get_message_numbers(
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


def _fetch_message_with_flags_retry(
    imap_connection, msg_num: bytes
) -> Tuple[List[str], Optional[bytes]]:
    """Fetch a message with retry logic for timeout errors."""
    max_retries = settings.IMAP_MAX_RETRIES
    for attempt in range(max_retries):
        try:
            return _fetch_message_with_flags(imap_connection, msg_num)
        except socket.timeout:
            if attempt < max_retries - 1:
                logger.warning(
                    "Timeout fetching message %s (attempt %d/%d), retrying...",
                    msg_num,
                    attempt + 1,
                    max_retries,
                )
                # Exponential backoff
                time.sleep(2**attempt)
                continue
            else:
                logger.error(
                    "Failed to fetch message %s after %d attempts",
                    msg_num,
                    max_retries,
                )
                raise
        except Exception as e:
            logger.error("Unexpected error fetching message %s: %s", msg_num, e)
            raise


def process_folder_messages(  # pylint: disable=too-many-arguments
    imap_connection: Any,
    folder: str,
    display_name: str,
    message_list: List[bytes],
    recipient: Any,
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
            # Fetch message with flags using retry logic
            flags, raw_email = _fetch_message_with_flags_retry(imap_connection, msg_num)

            # Parse message
            parsed_email = parse_email_message(raw_email)

            # TODO: better heuristic to determine if the message is from the sender
            is_sender = parsed_email["from"]["email"].lower() == username.lower()

            # Deliver message
            if deliver_inbound_message(
                str(recipient),
                parsed_email,
                raw_email,
                is_import=True,
                is_import_sender=is_sender,
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
