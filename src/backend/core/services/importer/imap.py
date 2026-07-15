"""IMAP utilities for message import.

Broad exception handling (W0718) is intentional: IMAP servers can raise many
different exception types (socket errors, encoding errors, protocol errors)
and the import must continue processing remaining messages on failure.
"""

# pylint: disable=broad-exception-caught

import base64
import codecs
import imaplib
import re
import socket
import ssl
import time
from typing import Any

from django.conf import settings

from celery.utils.log import get_task_logger
from jmap_email import first_address_email, parse_email

from core import models
from core.services.ssrf import SSRFValidationError, validate_hostname
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

from .channel import mark_started, record_progress
from .utils import FLUSH_EVERY, TransientImportError, beat, deliver, error_text

logger = get_task_logger(__name__)

# In-line retries for a single ``UID FETCH`` that hits a socket timeout (same
# connection, exponential backoff). An implementation detail of the fetch, not
# an operational knob: a persistent failure is handled a layer up (the run is
# parked resumable, then re-dispatched), so this only smooths a momentary blip.
UID_FETCH_MAX_RETRIES = 3


class IMAPSecurityError(RuntimeError):
    """
    Raised when an IMAP connection violates required security constraints.

    This exception is raised when:
    - Encrypted connection is required but cannot be established
    - STARTTLS is required but not supported by the server
    - STARTTLS negotiation fails
    - Any security downgrade is detected or attempted

    Failing fast and explicitly prevents credentials leakage
    and protects against STARTTLS stripping attacks.
    """


class IMAPAuthError(RuntimeError):
    """Raised when the IMAP server rejects the login — carries a human-readable,
    actionable message (the raw imaplib error is a bytestring like
    ``b'[AUTHENTICATIONFAILED] Invalid credentials'``, which is useless to a
    user)."""


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

    try:
        return re.sub(r"&([^-]*)-", decode_match, s)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # A malformed UTF-7 folder name must not abort the whole import (this
        # runs while building the folder map, outside any per-folder guard) —
        # fall back to the raw name.
        logger.warning("Failed to decode IMAP-UTF7 folder name %r: %s", s, e)
        return s


def _validate_imap_host(server: str) -> str:
    """Validate the IMAP server hostname and return the vetted IP to pin to.

    Wraps the shared SSRF validator (allowing public IP literals, which are
    legitimate for customer-supplied IMAP servers) and returns the first
    validated IP address. The caller connects to *exactly* that address so the
    address we vetted is the address we dial — closing the DNS-rebinding
    (TOCTOU) window where stock imaplib would re-resolve the hostname and could
    land on an internal IP.

    Raises:
        ValueError: If the hostname resolves to a blocked / non-public address.
    """
    try:
        valid_ips = validate_hostname(server, allow_ip_literal=True)
    except SSRFValidationError as exc:
        raise ValueError(f"IMAP server {server} is not allowed: {exc}") from exc
    if not valid_ips:
        raise ValueError(f"IMAP server {server} did not resolve to a usable address")
    return valid_ips[0]


class _IPPinnedIMAP4(imaplib.IMAP4):
    """``imaplib.IMAP4`` that dials a pre-validated IP instead of re-resolving.

    SSRF hardening: ``validate_hostname`` vets the server name, but stock
    imaplib re-resolves the hostname when it opens the socket — a DNS-rebinding
    window in which the second lookup can return an internal address. We pin the
    connection to the already-validated IP. The original hostname is kept as
    ``self.host`` (used for STARTTLS SNI/cert verification upstream).
    """

    def __init__(self, host, port, *, connect_ip, timeout=None):
        self._connect_ip = connect_ip
        super().__init__(host, port, timeout)

    def _create_socket(self, timeout):
        return socket.create_connection((self._connect_ip, self.port), timeout)


class _IPPinnedIMAP4SSL(imaplib.IMAP4_SSL):
    """SSL variant of :class:`_IPPinnedIMAP4`.

    Connects to the pinned IP but verifies the TLS certificate against the
    original hostname (``server_hostname`` SNI), so pinning never weakens
    certificate validation.
    """

    def __init__(self, host, port, *, connect_ip, timeout=None):
        self._connect_ip = connect_ip
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self._connect_ip, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


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
        # Validate the server hostname AND pin the vetted IP to prevent SSRF
        # (including DNS-rebinding TOCTOU): we connect to exactly the address
        # that passed validation, never a freshly re-resolved one.
        connect_ip = _validate_imap_host(self.server)

        # Port 143 typically uses STARTTLS, port 993 uses SSL direct
        # If use_ssl=True and port is 143, use STARTTLS instead of SSL direct
        use_starttls = self.use_ssl and self.port == 143
        success = False

        try:
            if self.use_ssl and not use_starttls:
                # SSL direct (typically port 993)
                try:
                    self.connection = _IPPinnedIMAP4SSL(
                        self.server,
                        self.port,
                        connect_ip=connect_ip,
                        timeout=settings.MESSAGES_IMPORT_IMAP_TIMEOUT,
                    )
                except ssl.SSLError as e:
                    # SSL handshake failed - likely wrong port or server doesn't support SSL
                    error_msg = (
                        f"SSL handshake failed for {self.server}:{self.port}: {e}. "
                        f"If using port {self.port}, the server may not support SSL direct. "
                        "Try port 143 with STARTTLS instead."
                    )
                    logger.error(error_msg)
                    raise IMAPSecurityError(error_msg) from e
            else:
                # Non-encrypted connection initially (will upgrade to TLS if use_ssl=True)
                self.connection = _IPPinnedIMAP4(
                    self.server,
                    self.port,
                    connect_ip=connect_ip,
                    timeout=settings.MESSAGES_IMPORT_IMAP_TIMEOUT,
                )

                if use_starttls:
                    # use_ssl=True on port 143: must upgrade to TLS via STARTTLS
                    # Check if server supports STARTTLS
                    typ, data = self.connection.capability()
                    capabilities = data[0].decode().upper() if data and data[0] else ""
                    if typ != "OK" or "STARTTLS" not in capabilities:
                        error_msg = (
                            f"Server {self.server}:{self.port} does not support STARTTLS. "
                            "Encrypted connection required."
                        )
                        logger.error(error_msg)
                        raise IMAPSecurityError(error_msg)

                    # Attempt STARTTLS
                    status, response = self.connection.starttls()
                    if status != "OK":
                        error_msg = (
                            f"STARTTLS failed for {self.server}:{self.port}: {response}. "
                            "Encrypted connection required."
                        )
                        logger.error(error_msg)
                        raise IMAPSecurityError(error_msg)
                # else: use_ssl=False, connection remains unencrypted (explicit user choice)

            # Set UTF-8 encoding for the IMAP connection
            self.connection._encoding = "utf-8"  # noqa: SLF001  # pylint: disable=attribute-defined-outside-init

            # Login
            try:
                self.connection.login(self.username, self.password)
            except imaplib.IMAP4.error as e:
                detail = error_text(e)
                logger.warning(
                    "IMAP login rejected for %s@%s: %s",
                    self.username,
                    self.server,
                    detail,
                )
                raise IMAPAuthError(
                    f"Login to {self.server} was rejected ({detail}). Check the "
                    "username and password."
                ) from e

            success = True
            return self.connection
        except (IMAPAuthError, IMAPSecurityError):
            # Expected, already logged where they were raised (warning for a
            # rejected login, error for the security checks) — re-logging here
            # would double-report them at ERROR level.
            raise
        except Exception as e:
            logger.error(
                "Failed to connect to IMAP server %s:%d: %s", self.server, self.port, e
            )
            raise
        finally:
            if not success and self.connection:
                try:
                    self.connection.logout()
                except Exception as logout_err:
                    logger.debug("Error during cleanup logout: %s", logout_err)

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


def _parse_imap_folder_info(folder_info: str) -> str | None:
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
    imap_connection, _username: str, _imap_server: str
) -> list[str]:
    """Get list of selectable folders from IMAP server."""
    status, folder_list = imap_connection.list()
    if status != "OK":
        raise RuntimeError(f"Failed to list folders: {folder_list}")

    selectable_folders = []
    for folder_info in folder_list:
        if folder_info is None:
            continue
        # ``errors="replace"``: a non-UTF-8 LIST line for one folder must not
        # raise and abort discovery of every other folder.
        raw = (
            folder_info if isinstance(folder_info, bytes) else str(folder_info).encode()
        )
        folder_name = _parse_imap_folder_info(raw.decode("utf-8", errors="replace"))
        if folder_name:
            selectable_folders.append(folder_name)

    return selectable_folders


def create_folder_mapping(
    folders: list[str], username: str, imap_server: str
) -> dict[str, str]:
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
                    logger.debug("Successfully selected folder: %s", folder_variant)
                    return True
            except UnicodeEncodeError:
                # If UTF-8 fails, try with UTF-7 encoding (IMAP standard)
                try:
                    utf7_folder = codecs.encode(
                        folder_variant.encode("utf-8"), "utf-7"
                    ).decode("ascii")
                    status, _ = imap_connection.select(utf7_folder)
                    if status == "OK":
                        logger.debug(
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


def _extract_flags_from_metadata(metadata: bytes) -> list[str]:
    """Extract flags from metadata bytes."""
    flags = []
    metadata_str = metadata.decode(errors="ignore")
    if "FLAGS" in metadata_str:
        flags_match = re.search(r"FLAGS\s*\(([^)]*)\)", metadata_str)
        if flags_match:
            flags_str = flags_match.group(1)
            flags = re.findall(r"\\\w+", flags_str)
    return flags


def _extract_imap_flags_and_content(msg_data) -> tuple[list[str], bytes | None]:
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


def get_folder_uidvalidity(imap_connection, folder: str) -> int | None:
    """Read a folder's UIDVALIDITY without changing the selected folder.

    UIDVALIDITY is the contract that makes UIDs durable: if the server changes
    it, every previously-collected UID for that folder is meaningless. The
    resumable import stores it alongside the last-seen UID so a resume can tell
    whether its watermark is still valid. Returns ``None`` when unreadable.
    """
    try:
        status, data = imap_connection.status(f'"{folder}"', "(UIDVALIDITY)")
        if status != "OK" or not data or not data[0]:
            return None
        raw = data[0] if isinstance(data[0], bytes) else str(data[0]).encode()
        match = re.search(rb"UIDVALIDITY\s+(\d+)", raw)
        return int(match.group(1)) if match else None
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("Failed to read UIDVALIDITY for folder %s: %s", folder, e)
        return None


def _folder_uidnext(imap_connection, folder: str) -> int | None:
    """Read a folder's UIDNEXT (the next UID the server would assign) via
    STATUS, or ``None`` when denied/unparsable."""
    try:
        status, data = imap_connection.status(f'"{folder}"', "(UIDNEXT)")
        if status != "OK" or not data or not data[0]:
            return None
        raw = data[0] if isinstance(data[0], bytes) else str(data[0]).encode()
        match = re.search(rb"UIDNEXT\s+(\d+)", raw)
        return int(match.group(1)) if match else None
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("Failed to read UIDNEXT for folder %s: %s", folder, e)
        return None


def _uid_search_fallback(imap_connection, folder: str) -> list[int]:
    """Union of alternative SEARCH criteria, for servers whose ``UID SEARCH``
    answers empty on a non-empty folder."""
    found: set[int] = set()
    for criteria in ("RECENT", "UNSEEN", "SEEN", "NEW", "OLD"):
        try:
            status, data = imap_connection.uid("SEARCH", None, criteria)
        except Exception as e:
            logger.debug("UID SEARCH %s failed for %s: %s", criteria, folder, e)
            continue
        if status == "OK" and data and data[0]:
            found.update(int(tok) for tok in data[0].split())
    return sorted(found)


def uid_search_all(imap_connection, folder: str, since_uid: int = 0) -> list[int]:
    """Select ``folder`` and return its message UIDs above ``since_uid``, ascending.

    Uses ``UID SEARCH`` (not sequence numbers): UIDs are stable for the life of
    a UIDVALIDITY, so a resumed run can skip everything at or below its stored
    high-water UID and fetch only what is new. When ``since_uid`` is given the
    range is pushed to the server (``UID SEARCH UID <since+1>:*``) so a
    continuous poll of a large mailbox doesn't drag back every UID each time.

    Raises ``TransientImportError`` when the folder cannot be selected:
    treating it as empty would let a oneshot run end COMPLETED while silently
    never importing this folder's mail. Raising keeps the watermark untouched
    and the run resumable; a persistent select failure becomes a *visible*
    FAILED through the cross-run stall budget.
    """
    if not select_imap_folder(imap_connection, folder):
        raise TransientImportError(f"Cannot select IMAP folder {folder}")
    if since_uid and since_uid > 0:
        status, data = imap_connection.uid("SEARCH", None, f"UID {since_uid + 1}:*")
        if status == "OK" and data and data[0]:
            return sorted(int(tok) for tok in data[0].split())
        # An empty incremental result is normally just "no new mail" — but the
        # same buggy servers that answer a full ``UID SEARCH ALL`` with a bogus
        # empty result (see below) do it for ranges too, which here would
        # silently end a resume early or freeze a continuous poller forever.
        # Cross-check with UIDNEXT (one cheap STATUS): only when the server
        # itself claims UIDs above the watermark exist do we pay for the full
        # search path. An unreadable STATUS means the empty answer can't be
        # trusted either — fall through too.
        uidnext = _folder_uidnext(imap_connection, folder)
        if uidnext is not None and uidnext <= since_uid + 1:
            return []
        # Fall through to the full scan; the caller filters ``> since_uid``,
        # so returning already-imported low UIDs is harmless.
    status, data = imap_connection.uid("SEARCH", None, "ALL")
    if status == "OK" and data and data[0]:
        return sorted(int(tok) for tok in data[0].split())
    # Some servers answer a full ``UID SEARCH ALL`` with an empty result on a
    # non-empty folder. Before concluding the folder is empty (and silently
    # importing nothing), retry with alternative criteria and take the union.
    return _uid_search_fallback(imap_connection, folder)


def _uid_fetch_separate_flags(imap_connection, uid: int) -> list[str]:
    """FLAGS-only fallback for servers that return FLAGS in a separate
    untagged response instead of inline with the ``BODY.PEEK[]`` fetch —
    without it every message from such a server imports flagless (all
    unread). Best-effort: a failure just means no flags."""
    try:
        status, flags_data = imap_connection.uid("FETCH", str(uid), "(FLAGS)")
        if status == "OK" and flags_data:
            for part in flags_data:
                raw = part[0] if isinstance(part, tuple) else part
                if isinstance(raw, bytes):
                    flags = _extract_flags_from_metadata(raw)
                    if flags:
                        return flags
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Separate FLAGS fetch failed for uid %s: %s", uid, exc)
    return []


def uid_fetch_message(imap_connection, uid: int) -> tuple[list[str], bytes | None]:
    """Fetch one message by UID, returning ``(flags, raw_email)``.

    Assumes the owning folder is already selected and its UIDVALIDITY verified.
    Retries transient socket timeouts with exponential backoff — a single
    blip must not make the caller skip (and permanently lose) the message.
    """
    max_retries = UID_FETCH_MAX_RETRIES
    for attempt in range(max_retries):
        try:
            status, msg_data = imap_connection.uid(
                "FETCH", str(uid), "(FLAGS BODY.PEEK[])"
            )
            if status != "OK":
                raise RuntimeError(f"UID FETCH {uid} failed: {msg_data}")
            flags, raw_email = _extract_imap_flags_and_content(msg_data)
            if raw_email is None:
                raise RuntimeError(f"No raw email found for UID {uid}")
            if not flags:
                flags = _uid_fetch_separate_flags(imap_connection, uid)
            return flags, raw_email
        except socket.timeout:
            if attempt >= max_retries - 1:
                logger.error(
                    "UID FETCH %s timed out after %d attempts", uid, max_retries
                )
                raise
            logger.warning(
                "UID FETCH %s timed out (attempt %d/%d), retrying",
                uid,
                attempt + 1,
                max_retries,
            )
            time.sleep(2**attempt)
    raise RuntimeError(f"UID FETCH {uid} failed after {max_retries} retries")


def _imap_credentials(channel: models.Channel) -> dict[str, Any]:
    creds = (channel.encrypted_settings or {}).get("imap")
    if not creds:
        raise ValueError(f"IMAP import channel {channel.id} has no stored credentials")
    # EncryptedJSONField (TextField-backed) round-trips every value as a STRING,
    # so ``imap_port`` comes back as "993" and ``use_ssl`` as "False" (which is
    # truthy!). Coerce them back to int/bool or the connection manager forces an
    # SSL handshake on a plaintext port / mis-detects STARTTLS.
    creds = dict(creds)
    creds["imap_port"] = int(creds["imap_port"])
    use_ssl = creds.get("use_ssl")
    if isinstance(use_ssl, str):
        creds["use_ssl"] = use_ssl.strip().lower() in ("true", "1", "yes", "on")
    else:
        creds["use_ssl"] = bool(use_ssl)
    return creds


def run_imap(channel, state) -> tuple[int, int, int]:
    """Transient-network guard around :func:`_run_imap`.

    Connect-time failures (DNS, socket timeouts, dropped connections — raised
    before or between fetches, where ``uid_fetch_message``'s own retry budget
    doesn't apply) are exactly as transient as a mid-run fetch failure: without
    this translation they'd land in ``run_import_task``'s generic handler and
    terminally FAIL the run — permanently disabling a continuous poller over
    one network blip. ``OSError`` covers the socket/ssl/connection family;
    ``imaplib.IMAP4.abort`` is the protocol-level "connection dropped".
    Auth/security errors (``IMAPAuthError``/``IMAPSecurityError``) are
    RuntimeErrors and deliberately stay permanent.
    """
    try:
        return _run_imap(channel, state)
    except (OSError, imaplib.IMAP4.abort) as exc:
        # A TransientImportError raised inside ``_run_imap`` is not an ``OSError``,
        # so it propagates unchanged past this handler (stays transient).
        raise TransientImportError(f"IMAP connection error: {error_text(exc)}") from exc


def _run_imap(channel, state) -> tuple[int, int, int]:
    """Resumable IMAP pass with a per-folder UID watermark.

    Fetches only ``uid > last_uid`` per folder (guarded by ``uidvalidity``), so a
    resume/continuous poll pulls just new mail. A fetch that fails even after
    retries is transient: the watermark is not advanced past it and the run is
    left resumable (``TransientImportError``) rather than losing the message.

    The watermark lives in Redis ONLY — deliberately never mirrored into
    ``Channel.settings``: folder names come from the remote server, so the
    watermark's size/cardinality is attacker-controlled and must not bloat the
    channel row (Postgres is the component to protect). Losing it to a Redis
    eviction just costs a full re-scan; dedup keeps re-delivery idempotent.

    This is an *importer*, not a full syncer: it only ever pulls NEW UIDs. It
    does NOT use IDLE (so no push; new mail arrives within one poll interval),
    nor CONDSTORE/QRESYNC/MODSEQ — so flag changes and expunges on messages
    already imported are never reflected. That is by design for one-way
    archival import; see ``docs/imports.md`` ("importer, not a syncer").
    """
    recipient = channel.mailbox
    creds = _imap_credentials(channel)
    username = creds["username"]
    # Resume watermark for IMAP: {folder: {"uidvalidity": v, "last_uid": u}}.
    # Per folder, "everything up to and including UID last_uid is delivered", so
    # the pass fetches only uid > last_uid — but only while ``uidvalidity`` still
    # matches (a mismatch means the server renumbered UIDs, voiding the bookmark
    # for that folder and forcing last_uid=0, i.e. a full re-scan of it).
    folders_wm: dict[str, Any] = dict(state.get("folders") or {})
    success, failure = state.get("success", 0), state.get("failure", 0)
    mark_started(channel.id)
    processed_since_flush = 0

    with (
        ThreadReindexDeferrer.defer(),
        ThreadStatsUpdateDeferrer.defer(),
        IMAPConnectionManager(
            creds["imap_server"],
            creds["imap_port"],
            username,
            creds["password"],
            creds["use_ssl"],
        ) as conn,
    ):
        folders = sorted(get_selectable_folders(conn, username, creds["imap_server"]))
        mapping = create_folder_mapping(folders, username, creds["imap_server"])
        # Plan every folder up front (UIDVALIDITY check + UID SEARCH above the
        # watermark) so the run knows its TOTAL before delivering the first
        # message — a total that merely trails success+failure would pin the
        # UI's progress at 100% for the whole run.
        plan: list[tuple[str, Any, list[int]]] = []
        for folder in folders:
            beat(channel)
            uidvalidity = get_folder_uidvalidity(conn, folder)
            wm = folders_wm.get(folder) or {}
            if uidvalidity is None:
                # STATUS can be denied (or non-standard) on some shared/virtual
                # folders. Without UIDVALIDITY the stored watermark can't be
                # trusted across runs, but skipping would silently lose the
                # folder's mail — import it with a full re-scan every pass
                # instead (dedup keeps re-delivery idempotent).
                logger.warning(
                    "import: no UIDVALIDITY for IMAP folder %s; re-scanning fully",
                    folder,
                )
                last_uid = 0
            else:
                # A changed UIDVALIDITY invalidates the stored high-water UID;
                # reset to 0 and re-scan (dedup keeps re-delivery duplicate-free).
                last_uid = (
                    wm.get("last_uid", 0) if wm.get("uidvalidity") == uidvalidity else 0
                )
            # Ask the server for only UIDs above the watermark (cheap for a
            # continuous poll of a large mailbox); keep the client-side ``>``
            # guard because ``UID SEARCH n:*`` is inclusive of the boundary.
            uids = [
                u
                for u in uid_search_all(conn, folder, since_uid=last_uid)
                if u > last_uid
            ]
            plan.append((folder, uidvalidity, uids))

        # Already-processed counts carry across resumes/polls; this run adds
        # the pending UIDs it just discovered.
        total = success + failure + sum(len(uids) for _, _, uids in plan)
        record_progress(
            channel.id,
            success=success,
            failure=failure,
            folders=folders_wm,
            total=total,
        )

        for folder, uidvalidity, uids in plan:
            if not uids:
                continue
            # The planning pass left the last-searched folder selected;
            # re-select this one (``uid_fetch_message`` assumes it). Skipping a
            # folder that fails to re-select would let a oneshot run end
            # COMPLETED with the folder's mail silently missing — raise as
            # transient instead (watermark untouched, run resumable, and a
            # persistent failure surfaces via the cross-run stall budget).
            if not select_imap_folder(conn, folder):
                record_progress(
                    channel.id,
                    success=success,
                    failure=failure,
                    folders=folders_wm,
                    total=total,
                )
                raise TransientImportError(f"Cannot re-select IMAP folder {folder}")
            display_name = mapping.get(folder, folder)
            for uid in uids:
                beat(channel)
                try:
                    flags, raw = uid_fetch_message(conn, uid)
                except Exception as exc:
                    # A fetch that fails even after ``uid_fetch_message``'s retry
                    # budget is treated as transient: do NOT advance the watermark
                    # past this UID (so a resume/next poll retries it — never a
                    # silent skip), persist progress so far, and leave the run
                    # resumable instead of terminally failed.
                    logger.warning(
                        "import: fetch failed for uid %s in %s after retries (%s); will resume",
                        uid,
                        folder,
                        exc,
                    )
                    record_progress(
                        channel.id,
                        success=success,
                        failure=failure,
                        folders=folders_wm,
                        total=total,
                    )
                    raise TransientImportError(
                        f"IMAP fetch failed for uid {uid} in {folder}"
                    ) from exc
                try:
                    is_sender = False
                    parsed_from = parse_email(raw)
                    if parsed_from is not None:
                        sender = first_address_email(parsed_from.get("from")) or ""
                        is_sender = sender.lower() == username.lower()
                    if deliver(
                        raw,
                        recipient,
                        channel,
                        imap_labels=[display_name],
                        imap_flags=flags,
                        is_sender=is_sender,
                    ):
                        success += 1
                    else:
                        failure += 1
                except Exception:
                    # A delivery/parse failure is permanent for these bytes, so
                    # it's safe to advance past it (counted as a failure).
                    logger.exception(
                        "import: error delivering IMAP uid %s in %s", uid, folder
                    )
                    failure += 1
                # Only checkpoint a UID we actually handled (fetched + delivered
                # or permanently rejected). A raised *fetch* broke out above
                # without touching the watermark.
                folders_wm[folder] = {"uidvalidity": uidvalidity, "last_uid": uid}
                processed_since_flush += 1
                if processed_since_flush >= FLUSH_EVERY:
                    record_progress(
                        channel.id,
                        success=success,
                        failure=failure,
                        folders=folders_wm,
                        total=total,
                    )
                    processed_since_flush = 0
    record_progress(
        channel.id, success=success, failure=failure, folders=folders_wm, total=total
    )
    return success, failure, total
