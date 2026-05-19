"""PST file parsing utilities for message import.

Broad exception handling (W0718/C0302) is intentional: PST parsing relies on
the pypff C library which can raise arbitrary exceptions on malformed data.
Each field extraction must be individually guarded to maximise data recovery.
"""

# pylint: disable=broad-exception-caught,too-many-lines

import base64
import hashlib
import logging
import re
import struct
import uuid
from email import message_from_string
from typing import Generator, Optional, Tuple

from core.mda.rfc5322 import compose_email, parse_email_address, parse_email_addresses

logger = logging.getLogger(__name__)

# MAPI property tags — message store
PR_CONTAINER_CLASS = 0x3613
PR_MESSAGE_FLAGS = 0x0E07
PR_IPM_SUBTREE_ENTRYID = 0x35E0
PR_IPM_SENTMAIL_ENTRYID = 0x35E4
PR_IPM_WASTEBASKET_ENTRYID = 0x35E3
PR_IPM_OUTBOX_ENTRYID = 0x35E2

# MAPI property tags — sender
PR_SENDER_EMAIL_ADDRESS = 0x0C1F
PR_SENDER_SMTP_ADDRESS = 0x5D01
PR_SENDER_ADDRTYPE = 0x0C1E

# MAPI property tags — sent-representing (delegation / shared mailboxes).
# For internal Exchange messages, PR_SENDER_* may hold the X.500 DN while
# PR_SENT_REPRESENTING_SMTP_ADDRESS carries the resolvable SMTP.
PR_SENT_REPRESENTING_NAME = 0x0042
PR_SENT_REPRESENTING_ADDRTYPE = 0x0064
PR_SENT_REPRESENTING_EMAIL_ADDRESS = 0x0065
PR_SENT_REPRESENTING_SMTP_ADDRESS = 0x5D02

# MAPI property tags — creator / last modifier.
# Last-resort SMTP fallback for messages where every PR_SENDER_* and
# PR_SENT_REPRESENTING_* slot only holds an X.500 DN. For sent items
# composed by a delegate of a shared mailbox, these typically carry the
# delegate's real SMTP — semantically imperfect (the human-facing sender
# is the shared mailbox) but at least a resolvable contact.
PR_CREATOR_SMTP_ADDRESS = 0x5D0A
PR_LAST_MODIFIER_SMTP_ADDRESS = 0x5D0B

# MAPI property tags — recipients
PR_DISPLAY_NAME = 0x3001
PR_ADDRTYPE = 0x3002
PR_EMAIL_ADDRESS = 0x3003
PR_RECIPIENT_TYPE = 0x0C15
PR_SMTP_ADDRESS = 0x39FE

# MAPI property tags — pre-formatted "display" recipient strings.
# Outlook keeps a header-style copy of the To/Cc/Bcc fields as
# semicolon-separated strings, used as a fallback when pypff exposes an
# empty recipient table.
PR_DISPLAY_BCC = 0x0E02
PR_DISPLAY_CC = 0x0E03
PR_DISPLAY_TO = 0x0E04

# MAPI property tags — attachments
PR_ATTACH_LONG_FILENAME = 0x3707
PR_ATTACH_FILENAME = 0x3704
PR_ATTACH_MIME_TAG = 0x370E
PR_ATTACH_CONTENT_ID = 0x3712
PR_ATTACH_METHOD = 0x3705

# MAPI property tags — flags
PR_FLAG_STATUS = 0x1090

# MAPI property tag — Internet Message-ID stored natively on the PST message,
# independent of transport_headers. Outlook populates it for received items
# and often for drafts; reading it lets us dedupe messages whose
# transport_headers were lost or never existed.
PR_INTERNET_MESSAGE_ID = 0x1035

# Attachment method values
ATTACH_BY_VALUE = 1
ATTACH_EMBEDDED_MSG = 5

# Message flag bits
MSGFLAG_READ = 0x1
MSGFLAG_UNSENT = 0x8

# Flag status values
FLAG_STATUS_FOLLOWUP = 2  # Flagged for follow-up

# Container class prefix for email folders (MAPI standard)
EMAIL_CONTAINER_CLASS_PREFIX = "IPF.Note"

# Maximum recursion depth for PST folder traversal
MAX_FOLDER_DEPTH = 50

# Folder types
FOLDER_TYPE_NORMAL = "normal"
FOLDER_TYPE_INBOX = "inbox"
FOLDER_TYPE_SENT = "sent"
FOLDER_TYPE_DELETED = "deleted"
FOLDER_TYPE_OUTBOX = "outbox"
FOLDER_TYPE_DRAFTS = "drafts"

# Regex to extract charset from HTML meta tags
_CHARSET_RE = re.compile(rb'<meta[^>]+charset=["\']?([^"\';>\s]+)', re.IGNORECASE)


class PSTFileUnreadableError(RuntimeError):
    """The PST file cannot be parsed.

    Raised when the archive is corrupt or its MAPI tree is missing the
    structures we need (root folder or message store). The wider importer
    pipeline catches this to surface a user-facing message distinct from
    the generic processing-error fallback.
    """


def assert_pst_readable(pst_file) -> None:
    """Probe the PST for the structures the walk relies on.

    Some corrupt archives open without raising, then later fail deep in
    the traversal with an AttributeError on a NoneType. Touching the root
    folder and message store up-front turns those late, opaque failures
    into a single explicit error at task boot.
    """
    try:
        root = pst_file.get_root_folder()
        _ = root.number_of_sub_folders
        store = pst_file.get_message_store()
        _ = store.number_of_record_sets
    except Exception as exc:
        raise PSTFileUnreadableError(
            "PST archive is unreadable: missing root folder or message store."
        ) from exc


def get_mapi_property(item, property_tag):
    """Get a MAPI property entry from an item's record sets by tag."""
    for record_set_idx in range(item.number_of_record_sets):
        record_set = item.get_record_set(record_set_idx)
        for entry_idx in range(record_set.get_number_of_entries()):
            entry = record_set.get_entry(entry_idx)
            if entry.entry_type == property_tag:
                return entry
    return None


def get_mapi_property_data(item, property_tag) -> Optional[bytes]:
    """Get raw data bytes for a MAPI property."""
    entry = get_mapi_property(item, property_tag)
    if entry is not None:
        try:
            return entry.data
        except Exception:
            return None
    return None


def get_mapi_property_integer(item, property_tag) -> Optional[int]:
    """Get an integer value for a MAPI property."""
    entry = get_mapi_property(item, property_tag)
    if entry is not None:
        try:
            return entry.data_as_integer
        except Exception:
            return None
    return None


def get_mapi_property_string(item, property_tag) -> Optional[str]:
    """Get a string value for a MAPI property."""
    entry = get_mapi_property(item, property_tag)
    if entry is not None:
        try:
            return entry.data_as_string
        except Exception:
            # Fall back to raw data decoded as utf-8
            try:
                raw = entry.data
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", errors="replace").rstrip("\x00")
            except Exception:
                logger.debug("Failed to decode MAPI property 0x%04X", property_tag)
    return None


def _folder_id_from_entry_id(entry_id: Optional[bytes]) -> Optional[int]:
    """Extract the folder identifier from a MAPI entry ID.

    PST entry IDs are 24 bytes: 4 flags + 16 UID + 4 folder_id (LE uint32).
    The last 4 bytes match folder.get_identifier().
    """
    if entry_id and len(entry_id) >= 4:
        return struct.unpack_from("<I", entry_id, len(entry_id) - 4)[0]
    return None


def build_special_folder_map(pst_file) -> dict:
    """Build a mapping from folder identifiers to folder types.

    Reads PR_IPM_SENTMAIL_ENTRYID, PR_IPM_WASTEBASKET_ENTRYID, and
    PR_IPM_OUTBOX_ENTRYID from the message store, extracts the folder
    identifier from each entry ID, and maps it to a folder type.
    """
    special_map = {}
    try:
        store = pst_file.get_message_store()
        for tag, folder_type in [
            (PR_IPM_SENTMAIL_ENTRYID, FOLDER_TYPE_SENT),
            (PR_IPM_WASTEBASKET_ENTRYID, FOLDER_TYPE_DELETED),
            (PR_IPM_OUTBOX_ENTRYID, FOLDER_TYPE_OUTBOX),
        ]:
            entry_id = get_mapi_property_data(store, tag)
            folder_id = _folder_id_from_entry_id(entry_id)
            if folder_id is not None:
                special_map[folder_id] = folder_type
    except Exception:
        logger.debug("Could not read message store properties for special folder map")
    return special_map


def get_store_owner_email(pst_file) -> Optional[str]:
    """Get the mailbox owner's email from the message store PR_DISPLAY_NAME."""
    try:
        store = pst_file.get_message_store()
    except Exception:
        return None
    display_name = get_mapi_property_string(store, PR_DISPLAY_NAME)
    if display_name and "@" in display_name:
        return display_name
    return None


# SourceWellKnownFolderType — named property from Microsoft's migration tools.
# GUID {9137a2fd-2fa5-4409-91aa-2c3ee697350a}, string name "SourceWellKnownFolderType".
# Present on PSTs exported via Exchange/O365 migration; absent from local Outlook PSTs.
_SOURCE_WKFT_GUID = "9137a2fd-2fa5-4409-91aa-2c3ee697350a"
_SOURCE_WKFT_NAME = "SourceWellKnownFolderType"
_SOURCE_WKFT_VALUES = {
    10: FOLDER_TYPE_INBOX,
    11: FOLDER_TYPE_SENT,
    12: FOLDER_TYPE_OUTBOX,
    14: FOLDER_TYPE_DELETED,
    17: FOLDER_TYPE_DRAFTS,
}


def _resolve_named_property_tag(pst_file, target_guid: str, target_name: str):
    """Resolve a string-named property to its NPID tag.

    Reads the Name-to-ID Map to find the property tag for a given
    (GUID, string_name) pair.  Returns the NPID (e.g. 0x8022) or None.
    """
    try:
        name_map = pst_file.get_name_to_id_map()
        rs = name_map.get_record_set(0)

        entry_stream = guid_stream = string_stream = None
        for e_idx in range(rs.get_number_of_entries()):
            entry = rs.get_entry(e_idx)
            tag = entry.entry_type
            if tag == 0x0002:
                guid_stream = entry.data
            elif tag == 0x0003:
                entry_stream = entry.data
            elif tag == 0x0004:
                string_stream = entry.data

        if not entry_stream or not guid_stream or not string_stream:
            return None

        target_name_lower = target_name.lower()

        for i in range(len(entry_stream) // 8):
            record = entry_stream[i * 8 : i * 8 + 8]
            dw_prop_id = struct.unpack_from("<I", record, 0)[0]
            word1 = struct.unpack_from("<H", record, 4)[0]
            w_prop_idx = struct.unpack_from("<H", record, 6)[0]

            n_bit = word1 & 0x01
            w_guid = (word1 >> 1) & 0x7FFF

            if n_bit != 1:
                continue  # Not a string-named property

            # Resolve GUID
            if w_guid < 3:
                continue  # PS_MAPI or PS_PUBLIC_STRINGS — not what we want
            guid_offset = (w_guid - 3) * 16
            if guid_offset + 16 > len(guid_stream):
                continue
            guid_str = str(
                uuid.UUID(bytes_le=guid_stream[guid_offset : guid_offset + 16])
            )
            if guid_str != target_guid:
                continue

            # Read string name (4-byte LE length prefix + UTF-16LE)
            if dw_prop_id + 4 > len(string_stream):
                continue
            str_len = struct.unpack_from("<I", string_stream, dw_prop_id)[0]
            if dw_prop_id + 4 + str_len > len(string_stream):
                continue
            prop_name = string_stream[dw_prop_id + 4 : dw_prop_id + 4 + str_len].decode(
                "utf-16-le", errors="replace"
            )

            if prop_name.lower() == target_name_lower:
                return 0x8000 + w_prop_idx

    except Exception:
        logger.debug("Failed to resolve named property %s", target_name)
    return None


def build_well_known_folder_map(pst_file, ipm_subtree) -> dict:
    """Build folder map from SourceWellKnownFolderType named property.

    This named property is set by Microsoft migration tools on Exchange/O365
    PSTs.  Returns a dict mapping folder identifier → folder type, or empty
    dict if the property is absent.
    """
    tag = _resolve_named_property_tag(pst_file, _SOURCE_WKFT_GUID, _SOURCE_WKFT_NAME)
    if tag is None:
        return {}

    wkft_map = {}
    try:
        for i in range(ipm_subtree.number_of_sub_folders):
            folder = ipm_subtree.get_sub_folder(i)
            val = get_mapi_property_integer(folder, tag)
            if val is not None and val in _SOURCE_WKFT_VALUES:
                wkft_map[folder.get_identifier()] = _SOURCE_WKFT_VALUES[val]
    except Exception:
        logger.debug("Failed to read SourceWellKnownFolderType from folders")
    return wkft_map


def _is_email_folder(folder) -> bool:
    """Check if a folder contains email items based on PR_CONTAINER_CLASS."""
    entry = get_mapi_property(folder, PR_CONTAINER_CLASS)
    if entry is None:
        # No container class set — treat as email folder (safe default)
        return True
    try:
        container_class = entry.data_as_string
        return container_class.startswith(EMAIL_CONTAINER_CLASS_PREFIX)
    except Exception:
        logger.debug("Failed to read container class for folder")
    return True


def _get_folder_type(folder, special_folder_map: dict) -> str:
    """Determine the folder type by matching its identifier against the special folder map."""
    try:
        folder_id = folder.get_identifier()
        if folder_id in special_folder_map:
            return special_folder_map[folder_id]
    except Exception:
        logger.debug("Failed to get folder identifier")
    return FOLDER_TYPE_NORMAL


# Mapping of known Outlook default folder names (lowercased) to folder types.
# Used as fallback when MAPI entry ID properties are absent from the message store
# (PR_VALID_FOLDER_MASK bits not set). Only applied to direct children of the
# IPM subtree — these are always the standard Outlook special folders.
# fmt: off
_KNOWN_FOLDER_NAMES = {
    # Inbox
    "inbox": FOLDER_TYPE_INBOX,
    "boîte de réception": FOLDER_TYPE_INBOX,
    "posteingang": FOLDER_TYPE_INBOX,
    "bandeja de entrada": FOLDER_TYPE_INBOX,
    "posta in arrivo": FOLDER_TYPE_INBOX,
    "postvak in": FOLDER_TYPE_INBOX,
    "caixa de entrada": FOLDER_TYPE_INBOX,
    "a]receber": FOLDER_TYPE_INBOX,
    "входящие": FOLDER_TYPE_INBOX,
    "skrzynka odbiorcza": FOLDER_TYPE_INBOX,
    "doručená pošta": FOLDER_TYPE_INBOX,
    "beérkezett üzenetek": FOLDER_TYPE_INBOX,
    "indbakke": FOLDER_TYPE_INBOX,
    "innboks": FOLDER_TYPE_INBOX,
    "inkorg": FOLDER_TYPE_INBOX,
    "saapuneet": FOLDER_TYPE_INBOX,
    "gelen kutusu": FOLDER_TYPE_INBOX,
    "受信トレイ": FOLDER_TYPE_INBOX,
    "收件箱": FOLDER_TYPE_INBOX,
    "收件匣": FOLDER_TYPE_INBOX,
    "받은 편지함": FOLDER_TYPE_INBOX,
    "علبة الوارد": FOLDER_TYPE_INBOX,
    "דואר נכנס": FOLDER_TYPE_INBOX,
    "вхідні": FOLDER_TYPE_INBOX,
    "primite": FOLDER_TYPE_INBOX,
    # Sent Items
    "sent items": FOLDER_TYPE_SENT,
    "sent": FOLDER_TYPE_SENT,
    "éléments envoyés": FOLDER_TYPE_SENT,
    "gesendete elemente": FOLDER_TYPE_SENT,
    "gesendete objekte": FOLDER_TYPE_SENT,
    "elementos enviados": FOLDER_TYPE_SENT,
    "posta inviata": FOLDER_TYPE_SENT,
    "verzonden items": FOLDER_TYPE_SENT,
    "itens enviados": FOLDER_TYPE_SENT,
    "отправленные": FOLDER_TYPE_SENT,
    "отправленные элементы": FOLDER_TYPE_SENT,
    "elementy wysłane": FOLDER_TYPE_SENT,
    "odeslaná pošta": FOLDER_TYPE_SENT,
    "elküldött elemek": FOLDER_TYPE_SENT,
    "sendte elementer": FOLDER_TYPE_SENT,
    "skickade objekt": FOLDER_TYPE_SENT,
    "lähetetyt": FOLDER_TYPE_SENT,
    "gönderilmiş öğeler": FOLDER_TYPE_SENT,
    "送信済みアイテム": FOLDER_TYPE_SENT,
    "已发送邮件": FOLDER_TYPE_SENT,
    "寄件備份": FOLDER_TYPE_SENT,
    "보낸 편지함": FOLDER_TYPE_SENT,
    "العناصر المرسلة": FOLDER_TYPE_SENT,
    "פריטים שנשלחו": FOLDER_TYPE_SENT,
    "надіслані": FOLDER_TYPE_SENT,
    "trimise": FOLDER_TYPE_SENT,
    # Drafts
    "drafts": FOLDER_TYPE_DRAFTS,
    "brouillons": FOLDER_TYPE_DRAFTS,
    "entwürfe": FOLDER_TYPE_DRAFTS,
    "borradores": FOLDER_TYPE_DRAFTS,
    "bozze": FOLDER_TYPE_DRAFTS,
    "concepten": FOLDER_TYPE_DRAFTS,
    "rascunhos": FOLDER_TYPE_DRAFTS,
    "черновики": FOLDER_TYPE_DRAFTS,
    "wersje robocze": FOLDER_TYPE_DRAFTS,
    "koncepty": FOLDER_TYPE_DRAFTS,
    "piszkozatok": FOLDER_TYPE_DRAFTS,
    "kladder": FOLDER_TYPE_DRAFTS,
    "utkast": FOLDER_TYPE_DRAFTS,
    "luonnokset": FOLDER_TYPE_DRAFTS,
    "taslaklar": FOLDER_TYPE_DRAFTS,
    "下書き": FOLDER_TYPE_DRAFTS,
    "草稿": FOLDER_TYPE_DRAFTS,
    "임시 보관함": FOLDER_TYPE_DRAFTS,
    "مسودات": FOLDER_TYPE_DRAFTS,
    "טיוטות": FOLDER_TYPE_DRAFTS,
    "чернетки": FOLDER_TYPE_DRAFTS,
    "ciorne": FOLDER_TYPE_DRAFTS,
    # Outbox
    "outbox": FOLDER_TYPE_OUTBOX,
    "boîte d'envoi": FOLDER_TYPE_OUTBOX,
    "postausgang": FOLDER_TYPE_OUTBOX,
    "bandeja de salida": FOLDER_TYPE_OUTBOX,
    "posta in uscita": FOLDER_TYPE_OUTBOX,
    "postvak uit": FOLDER_TYPE_OUTBOX,
    "caixa de saída": FOLDER_TYPE_OUTBOX,
    "a]enviar": FOLDER_TYPE_OUTBOX,
    "исходящие": FOLDER_TYPE_OUTBOX,
    "skrzynka nadawcza": FOLDER_TYPE_OUTBOX,
    "pošta k odeslání": FOLDER_TYPE_OUTBOX,
    "postázandó üzenetek": FOLDER_TYPE_OUTBOX,
    "udbakke": FOLDER_TYPE_OUTBOX,
    "utboks": FOLDER_TYPE_OUTBOX,
    "utkorgen": FOLDER_TYPE_OUTBOX,
    "lähtevät": FOLDER_TYPE_OUTBOX,
    "giden kutusu": FOLDER_TYPE_OUTBOX,
    "送信トレイ": FOLDER_TYPE_OUTBOX,
    "发件箱": FOLDER_TYPE_OUTBOX,
    "寄件匣": FOLDER_TYPE_OUTBOX,
    "보낼 편지함": FOLDER_TYPE_OUTBOX,
    "علبة الصادر": FOLDER_TYPE_OUTBOX,
    "דואר יוצא": FOLDER_TYPE_OUTBOX,
    "вихідні": FOLDER_TYPE_OUTBOX,
    # Deleted Items
    "deleted items": FOLDER_TYPE_DELETED,
    "trash": FOLDER_TYPE_DELETED,
    "éléments supprimés": FOLDER_TYPE_DELETED,
    "gelöschte elemente": FOLDER_TYPE_DELETED,
    "gelöschte objekte": FOLDER_TYPE_DELETED,
    "elementos eliminados": FOLDER_TYPE_DELETED,
    "posta eliminata": FOLDER_TYPE_DELETED,
    "verwijderde items": FOLDER_TYPE_DELETED,
    "itens excluídos": FOLDER_TYPE_DELETED,
    "удалённые": FOLDER_TYPE_DELETED,
    "удаленные": FOLDER_TYPE_DELETED,
    "elementy usunięte": FOLDER_TYPE_DELETED,
    "odstraněná pošta": FOLDER_TYPE_DELETED,
    "törölt elemek": FOLDER_TYPE_DELETED,
    "slettet post": FOLDER_TYPE_DELETED,
    "slettede elementer": FOLDER_TYPE_DELETED,
    "borttagna objekt": FOLDER_TYPE_DELETED,
    "poistetut": FOLDER_TYPE_DELETED,
    "silinmiş öğeler": FOLDER_TYPE_DELETED,
    "削除済みアイテム": FOLDER_TYPE_DELETED,
    "已删除邮件": FOLDER_TYPE_DELETED,
    "刪除的郵件": FOLDER_TYPE_DELETED,
    "지운 편지함": FOLDER_TYPE_DELETED,
    "العناصر المحذوفة": FOLDER_TYPE_DELETED,
    "פריטים שנמחקו": FOLDER_TYPE_DELETED,
    "видалені": FOLDER_TYPE_DELETED,
}
# fmt: on


def _detect_folder_type_by_name(folder_name: str) -> str:
    """Detect folder type by matching folder name against known Outlook defaults.

    Only used as a fallback when MAPI entry ID properties are absent.
    Should only be applied to direct children of the IPM subtree.
    """
    return _KNOWN_FOLDER_NAMES.get(folder_name.lower().strip(), FOLDER_TYPE_NORMAL)


def _get_message_timestamp(message):
    """Get a sortable timestamp from a message for chronological ordering."""
    try:
        if message.delivery_time:
            return message.delivery_time
    except Exception:
        logger.debug("Failed to read delivery_time")
    try:
        if message.client_submit_time:
            return message.client_submit_time
    except Exception:
        logger.debug("Failed to read client_submit_time")
    return None


def _get_message_flags(message) -> int:
    """Get PR_MESSAGE_FLAGS from a message."""
    flags = get_mapi_property_integer(message, PR_MESSAGE_FLAGS)
    return flags if flags is not None else 0


def _get_flag_status(message) -> Optional[int]:
    """Get PR_FLAG_STATUS from a message (follow-up flag)."""
    return get_mapi_property_integer(message, PR_FLAG_STATUS)


def _addr_tuple_to_dict(name: str, addr: str) -> dict:
    """Convert a (name, email) tuple to a JMAP address dict."""
    return {"name": name, "email": addr}


def _resolve_smtp_address(item) -> Optional[str]:
    """Resolve an SMTP email address from a MAPI item (recipient or message).

    Handles Exchange "EX" address types by checking PR_SMTP_ADDRESS first.
    """
    addr_type = get_mapi_property_string(item, PR_ADDRTYPE)
    if addr_type and addr_type.upper() == "EX":
        # Exchange address — try PR_SMTP_ADDRESS first
        smtp = get_mapi_property_string(item, PR_SMTP_ADDRESS)
        if smtp and "@" in smtp:
            return smtp
        # Fall back to PR_EMAIL_ADDRESS (may be X.500 DN, but try anyway)
        email_addr = get_mapi_property_string(item, PR_EMAIL_ADDRESS)
        if email_addr and "@" in email_addr:
            return email_addr
        return None

    # SMTP or unknown — use PR_EMAIL_ADDRESS directly
    email_addr = get_mapi_property_string(item, PR_EMAIL_ADDRESS)
    if email_addr and "@" in email_addr:
        return email_addr
    # Also try PR_SMTP_ADDRESS as fallback
    smtp = get_mapi_property_string(item, PR_SMTP_ADDRESS)
    if smtp and "@" in smtp:
        return smtp
    return None


def _safe_sender_name(message) -> str:
    """Safely get sender_name from a message, returning empty string on error."""
    try:
        return message.sender_name or ""
    except Exception:
        return ""


def _extract_sender_from_mapi(
    message,
    store_email: Optional[str] = None,
    preferred_name: Optional[str] = None,
) -> Optional[dict]:
    """Extract sender address from MAPI properties on the message itself.

    Order of attempts (first hit wins):
        1. PR_SENDER_SMTP_ADDRESS (direct, Exchange-resolved)
        2. PR_SENDER_EMAIL_ADDRESS if it looks like SMTP
        3. PR_SENT_REPRESENTING_SMTP_ADDRESS (shared mailboxes / delegation)
        4. PR_SENT_REPRESENTING_EMAIL_ADDRESS if SMTP
        5. PR_CREATOR_SMTP_ADDRESS / PR_LAST_MODIFIER_SMTP_ADDRESS — usually
           the actual delegate's SMTP for shared-mailbox sent items
        6. Parse sender_name as an email address
        7. store_email (common for EX sent items where only the store owner
           identifies the sender)

    ``preferred_name`` overrides any name resolved here — used when an upstream
    header (e.g. transport_headers' From) carried a valid display name but an
    X.500 DN as the address, so the human-readable name is preserved while the
    SMTP comes from MAPI.
    """

    def _build(
        fallback_name: Optional[str],
        smtp: str,
        *,
        sender_name_fallback: bool = True,
    ) -> dict:
        # ``sender_name`` is only a valid display-name fallback when the SMTP
        # came from a *different* source (PR_SENDER_*, store_email…). When the
        # SMTP was itself extracted from ``sender_name``, reusing it as a name
        # produces a redundant ``"addr" <addr>``.
        name = preferred_name or fallback_name
        if not name and sender_name_fallback:
            name = _safe_sender_name(message)
        return _addr_tuple_to_dict(name or "", smtp)

    # 1. Direct sender SMTP (most reliable when present)
    smtp = get_mapi_property_string(message, PR_SENDER_SMTP_ADDRESS)
    if smtp and "@" in smtp:
        return _build(None, smtp)

    # 2. Direct sender email — only trusted if it looks like SMTP (excludes
    # Exchange X.500 DNs that lack '@')
    email_addr = get_mapi_property_string(message, PR_SENDER_EMAIL_ADDRESS)
    if email_addr and "@" in email_addr:
        return _build(None, email_addr)

    # 3. Sent-representing SMTP — shared mailboxes ("au nom de") often carry
    # the resolvable SMTP only here while PR_SENDER_* holds the X.500 DN
    sr_smtp = get_mapi_property_string(message, PR_SENT_REPRESENTING_SMTP_ADDRESS)
    if sr_smtp and "@" in sr_smtp:
        sr_name = get_mapi_property_string(message, PR_SENT_REPRESENTING_NAME)
        return _build(sr_name, sr_smtp)

    # 4. Sent-representing email as SMTP
    sr_email = get_mapi_property_string(message, PR_SENT_REPRESENTING_EMAIL_ADDRESS)
    if sr_email and "@" in sr_email:
        sr_name = get_mapi_property_string(message, PR_SENT_REPRESENTING_NAME)
        return _build(sr_name, sr_email)

    # 5. Creator / last-modifier SMTP — last resort before heuristics. For
    # delegate sends from shared mailboxes this is typically the only
    # resolvable SMTP available (the shared mailbox's own SMTP lives only
    # in the GAL, which a PST does not export).
    for tag in (PR_CREATOR_SMTP_ADDRESS, PR_LAST_MODIFIER_SMTP_ADDRESS):
        creator_smtp = get_mapi_property_string(message, tag)
        if creator_smtp and "@" in creator_smtp:
            return _build(None, creator_smtp)

    # 6. Try to parse sender_name as an email address.
    try:
        if message.sender_name:
            parsed_name, addr = parse_email_address(message.sender_name)
            if addr and "@" in addr:
                return _build(parsed_name, addr, sender_name_fallback=False)
    except Exception:
        logger.debug("Failed to parse sender_name as email address")

    # 7. Fall back to message store owner email (common for EX sent items)
    if store_email:
        return _build(None, store_email)

    return None


def _extract_recipients_from_mapi(message) -> dict:
    """Extract To/Cc/Bcc recipients from MAPI recipient table.

    Returns dict with 'to', 'cc', 'bcc' keys mapping to lists of address dicts.
    """
    result = {"to": [], "cc": [], "bcc": []}

    # MAPI recipient types
    MAPI_TO = 1  # pylint: disable=invalid-name
    MAPI_CC = 2  # pylint: disable=invalid-name
    MAPI_BCC = 3  # pylint: disable=invalid-name

    try:
        num_recipients = int(message.number_of_recipients)
    except (AttributeError, TypeError, ValueError, Exception):
        return result

    for i in range(num_recipients):
        try:
            recipient = message.get_recipient(i)
        except Exception:
            logger.debug("Failed to get recipient %d", i)
            continue

        # Get recipient type
        recip_type = get_mapi_property_integer(recipient, PR_RECIPIENT_TYPE)
        if recip_type is None:
            recip_type = MAPI_TO  # Default to To

        # Get display name
        display_name = get_mapi_property_string(recipient, PR_DISPLAY_NAME) or ""

        # Resolve email address (handles EX vs SMTP)
        email_addr = _resolve_smtp_address(recipient)

        if not email_addr:
            # Try display name as email
            if "@" in display_name:
                email_addr = display_name
            else:
                continue  # Skip recipients without resolvable email

        addr_dict = _addr_tuple_to_dict(display_name, email_addr)

        if recip_type == MAPI_TO:
            result["to"].append(addr_dict)
        elif recip_type == MAPI_CC:
            result["cc"].append(addr_dict)
        elif recip_type == MAPI_BCC:
            result["bcc"].append(addr_dict)
        else:
            result["to"].append(addr_dict)

    return result


def _parse_display_recipients(display_string: Optional[str]) -> list:
    """Parse Outlook's semicolon-separated To/Cc/Bcc display string.

    Returns a list of JMAP address dicts for entries containing an email
    address. Name-only entries (no '@') are dropped on purpose — a Contact
    without an email cannot be created downstream, and silently inventing
    one would corrupt the address book.
    """
    if not display_string:
        return []

    addresses = []
    for raw in display_string.split(";"):
        token = raw.strip()
        if not token:
            continue
        try:
            name, addr = parse_email_address(token)
        except Exception:
            logger.debug("Failed to parse display recipient token")
            continue
        if addr and "@" in addr:
            addresses.append(_addr_tuple_to_dict(name or "", addr))
        elif "@" in token:
            # parse_email_address sometimes hands back the address as the
            # name field when the token is a bare email — recover it.
            addresses.append(_addr_tuple_to_dict("", token))
        else:
            logger.debug("Dropping display recipient with no email")
    return addresses


def _extract_display_recipients_from_mapi(message) -> dict:
    """Fall back to PR_DISPLAY_TO/CC/BCC when the recipient table is empty.

    Some PSTs exported from Exchange Online expose ``number_of_recipients=0``
    even for messages that clearly went to recipients. The header-style
    strings still carry whichever addresses Exchange formatted at compose
    time, so they're our last source of truth before giving up.
    """
    return {
        "to": _parse_display_recipients(
            get_mapi_property_string(message, PR_DISPLAY_TO)
        ),
        "cc": _parse_display_recipients(
            get_mapi_property_string(message, PR_DISPLAY_CC)
        ),
        "bcc": _parse_display_recipients(
            get_mapi_property_string(message, PR_DISPLAY_BCC)
        ),
    }


def sanitize_folder_name(name: str, max_length: int = 255) -> str:
    """Sanitize a PST folder name for use as an IMAP label."""
    name = name.strip()
    # Remove control characters
    name = "".join(c for c in name if c.isprintable())
    return name[:max_length] if name else "Unknown"


def _decode_html_bytes(raw_html: bytes) -> str:
    """Decode HTML bytes, detecting encoding from meta charset tag.

    Falls back through common Outlook encodings before using utf-8 with replacement.
    """
    # Try to detect charset from HTML meta tag
    match = _CHARSET_RE.search(raw_html[:4096])
    if match:
        charset = match.group(1).decode("ascii", errors="ignore")
        try:
            return raw_html.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass

    # Try UTF-8 first (most modern)
    try:
        return raw_html.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # Try Windows-1252 (most common for Outlook)
    try:
        return raw_html.decode("cp1252")
    except UnicodeDecodeError:
        pass

    # Final fallback
    return raw_html.decode("utf-8", errors="replace")


def _apply_recipient_fallback_chain(message, jmap_data: dict) -> None:
    """Fill missing To/Cc/Bcc via MAPI recipient table, then PR_DISPLAY_TO/CC/BCC.

    The recipient table is authoritative. Exchange Online exports often leave
    it empty even on real messages — PR_DISPLAY_* carries the addresses that
    Exchange formatted at compose time and is the last source of truth before
    giving up. Common to both transport-headers and MAPI-only branches.
    """
    if all(jmap_data.get(key) for key in ("to", "cc", "bcc")):
        return

    recipients = _extract_recipients_from_mapi(message)
    display_recipients = None

    for key in ("to", "cc", "bcc"):
        if jmap_data.get(key):
            continue
        if recipients[key]:
            jmap_data[key] = recipients[key]
            continue
        if display_recipients is None:
            display_recipients = _extract_display_recipients_from_mapi(message)
        if display_recipients[key]:
            jmap_data[key] = display_recipients[key]


# Strict shape mirror of compose_email's _MSG_ID_RE, applied to the
# bracket-stripped value. PST archives routinely carry Message-IDs that
# would crash strict composition (empty, missing '@', embedded whitespace,
# nested brackets) — pre-validating here lets us fall back to MAPI/synth
# instead of failing the entire message reconstruction.
_VALID_MSG_ID_INNER_RE = re.compile(r"^[^\s<>@]+@[^\s<>@]+$")


def _sanitize_message_id(raw: Optional[str]) -> Optional[str]:
    """Return ``raw`` stripped of brackets/whitespace if it's a valid msg-id.

    Returns None for anything compose_email would reject (empty, no '@',
    multiple '@', whitespace, nested brackets…). Keeps the importer
    "lenient parse, strict compose" contract from collapsing on malformed
    archives.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1].strip()
    if not candidate or not _VALID_MSG_ID_INNER_RE.match(candidate):
        return None
    return candidate


def _extract_message_id_from_mapi(message) -> Optional[str]:
    """Read the native Internet Message-ID stored on the PST message.

    Returns the value of PR_INTERNET_MESSAGE_ID stripped of surrounding
    angle brackets when it parses to a shape compose_email accepts, or
    None when the property is absent, empty, or malformed.
    """
    return _sanitize_message_id(
        get_mapi_property_string(message, PR_INTERNET_MESSAGE_ID)
    )


def _synthesize_message_id(message, recipient_email: Optional[str]) -> str:
    """Build a deterministic Message-ID from stable MAPI properties.

    Used as last resort when no native Message-ID is available (typical of
    drafts and locally-composed items). Two re-imports of the same PST will
    produce the same value, allowing the inbound dedup check to skip them.

    The hash inputs are picked for stability across libpff reads: delivery
    and submit timestamps, sender, subject, body prefix, and attachment
    fingerprint. The recipient mailbox's domain is used as the host part so
    the synthesized ID stays scoped to this import.
    """
    parts: list[str] = []

    for attr in ("delivery_time", "client_submit_time"):
        try:
            value = getattr(message, attr, None)
            parts.append(value.isoformat() if value else "")
        except Exception:
            parts.append("")

    try:
        parts.append(message.subject or "")
    except Exception:
        parts.append("")

    try:
        parts.append(message.sender_name or "")
    except Exception:
        parts.append("")

    parts.append(get_mapi_property_string(message, PR_SENDER_SMTP_ADDRESS) or "")
    parts.append(get_mapi_property_string(message, PR_SENDER_EMAIL_ADDRESS) or "")

    # Body prefix — 1 KB is enough to disambiguate without making the hash
    # sensitive to small late-message edits (signature insertion, etc.).
    try:
        raw_text = message.plain_text_body
        if raw_text:
            if isinstance(raw_text, bytes):
                raw_text = raw_text.decode("utf-8", errors="replace")
            parts.append(raw_text[:1024])
    except Exception:
        logger.debug("Failed to read plain_text_body for Message-ID synthesis")

    # Attachment fingerprint — count + total declared size; reading the full
    # buffers would defeat the point of an O(1) Message-ID synthesis.
    try:
        num_attachments = int(message.number_of_attachments)
        total_size = 0
        for i in range(num_attachments):
            try:
                total_size += int(message.get_attachment(i).get_size())
            except Exception:
                logger.debug(
                    "Failed to read attachment %d size for Message-ID synthesis", i
                )
        parts.append(f"{num_attachments}:{total_size}")
    except Exception:
        parts.append("")

    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]

    domain = "localhost"
    if recipient_email and "@" in recipient_email:
        domain = recipient_email.rsplit("@", 1)[1]
    return f"pst-synth-{digest}@{domain}"


def reconstruct_eml(
    message,
    store_email: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> bytes:  # pylint: disable=too-many-branches
    """Convert a pypff message to RFC5322 bytes.

    If transport_headers is available, uses those for threading headers.
    Otherwise, constructs headers from MAPI properties.
    Uses the core/mda/rfc5322 compose_email API for MIME construction.

    ``recipient_email`` is the import target mailbox; its domain is used to
    synthesize ``unknown-sender@<domain>`` when no sender can be extracted,
    so the message still composes (``compose_email`` rejects empty ``from``).
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    jmap_data = {}
    extra_headers = {}

    # Try to get original transport headers for threading-critical fields
    transport_headers = None
    try:
        transport_headers = message.transport_headers
    except Exception:
        logger.debug("Failed to read transport_headers")

    if transport_headers:
        parsed_headers = message_from_string(transport_headers)

        # From — Exchange rewrites internal/shared-mailbox senders as X.500
        # DNs ("/O=EXCHANGELABS/.../CN=..."). Trust the header only if it
        # parses to a real SMTP address; otherwise fall back to MAPI below,
        # preserving the human-readable name from the header.
        from_str = parsed_headers.get("From", "")
        from_name_hint: Optional[str] = None
        if from_str:
            name, addr = parse_email_address(from_str)
            if addr and "@" in addr:
                jmap_data["from"] = _addr_tuple_to_dict(name, addr)
            else:
                from_name_hint = name or None

        if "from" not in jmap_data:
            sender_dict = _extract_sender_from_mapi(
                message,
                store_email=store_email,
                preferred_name=from_name_hint,
            )
            if sender_dict:
                jmap_data["from"] = sender_dict

        # To / Cc / Bcc — header values are authoritative when present.
        to_str = parsed_headers.get("To", "")
        if to_str:
            jmap_data["to"] = [
                _addr_tuple_to_dict(n, a) for n, a in parse_email_addresses(to_str)
            ]

        cc_str = parsed_headers.get("Cc", "")
        if cc_str:
            jmap_data["cc"] = [
                _addr_tuple_to_dict(n, a) for n, a in parse_email_addresses(cc_str)
            ]

        bcc_str = parsed_headers.get("Bcc", "")
        if bcc_str:
            jmap_data["bcc"] = [
                _addr_tuple_to_dict(n, a) for n, a in parse_email_addresses(bcc_str)
            ]

        # Subject
        subject = parsed_headers.get("Subject")
        if subject:
            jmap_data["subject"] = subject

        # Date
        date_str = parsed_headers.get("Date")
        if date_str:
            jmap_data["date"] = date_str

        # Message-ID — header is preferred, but Exchange/O365 exports
        # sometimes strip it or carry a malformed value (empty, missing
        # '@', embedded whitespace) that compose_email's strict validator
        # would reject; fall back to MAPI and synthesis below in those
        # cases.
        message_id = _sanitize_message_id(parsed_headers.get("Message-ID"))
        if message_id:
            jmap_data["messageId"] = message_id

        # In-Reply-To and References — pass as custom headers to preserve
        # exact original values (the in_reply_to parameter on compose_email
        # would append to References, which we don't want for imports)
        in_reply_to_val = parsed_headers.get("In-Reply-To")
        if in_reply_to_val:
            extra_headers["In-Reply-To"] = in_reply_to_val

        references = parsed_headers.get("References")
        if references:
            extra_headers["References"] = references

    else:
        # Build from MAPI properties — sender
        sender_dict = _extract_sender_from_mapi(message, store_email=store_email)
        if sender_dict:
            jmap_data["from"] = sender_dict

        # Date
        try:
            if message.delivery_time:
                jmap_data["date"] = message.delivery_time.isoformat()
            elif message.client_submit_time:
                jmap_data["date"] = message.client_submit_time.isoformat()
        except Exception:
            logger.debug("Failed to read message date")

    # Recipients fallback chain (MAPI recipient table → PR_DISPLAY_*).
    # Exchange Online sometimes emits transport headers without To/Cc/Bcc
    # lines, and PST exports often leave the recipient table empty even on
    # real messages — this guarantees we capture every available source.
    _apply_recipient_fallback_chain(message, jmap_data)

    # Subject — ensure set even if transport_headers didn't include it
    if "subject" not in jmap_data:
        try:
            if message.subject:
                jmap_data["subject"] = message.subject
        except Exception:
            logger.debug("Failed to read message subject")

    # Message-ID fallback chain — drafts and locally-composed items have no
    # transport_headers, and Exchange exports sometimes drop the header even
    # for received items. Without a Message-ID the inbound dedup check is
    # skipped and the same message gets imported again on every re-run.
    if "messageId" not in jmap_data:
        native_id = _extract_message_id_from_mapi(message)
        if native_id:
            jmap_data["messageId"] = native_id
        else:
            jmap_data["messageId"] = _synthesize_message_id(message, recipient_email)

    # No sender resolvable: synthesize one using the recipient's domain so
    # compose_email accepts the message. inbound_create.py keeps this value
    # as-is (it only substitutes when the email is empty).
    if "from" not in jmap_data:
        fallback_domain = None
        if recipient_email and "@" in recipient_email:
            fallback_domain = recipient_email.rsplit("@", 1)[1]
        fallback_email = (
            f"unknown-sender@{fallback_domain}"
            if fallback_domain
            else "unknown-sender@localhost"
        )
        logger.warning(
            "PST message has no resolvable sender; using synthesized sender address"
        )
        jmap_data["from"] = {"name": "Unknown Sender", "email": fallback_email}

    # Body parts
    try:
        raw_text = message.plain_text_body
        if raw_text:
            if isinstance(raw_text, bytes):
                raw_text = raw_text.decode("utf-8", errors="replace")
            jmap_data["textBody"] = [{"content": raw_text}]
    except Exception:
        logger.debug("Failed to read plain_text_body")
    try:
        raw_html = message.html_body
        if raw_html:
            if isinstance(raw_html, str):
                html_str = raw_html
            else:
                html_str = _decode_html_bytes(raw_html)
            jmap_data["htmlBody"] = [{"content": html_str}]
    except Exception:
        logger.debug("Failed to read html_body")

    # Ensure at least an empty text body
    if "textBody" not in jmap_data and "htmlBody" not in jmap_data:
        jmap_data["textBody"] = [{"content": ""}]

    # Attachments
    attachments = []
    try:
        num_attachments = message.number_of_attachments
        for i in range(num_attachments):
            try:
                attachment = message.get_attachment(i)
                att_size = attachment.get_size()
                att_data = attachment.read_buffer(att_size)

                # Filename from MAPI properties
                filename = (
                    get_mapi_property_string(attachment, PR_ATTACH_LONG_FILENAME)
                    or get_mapi_property_string(attachment, PR_ATTACH_FILENAME)
                    or get_mapi_property_string(attachment, PR_DISPLAY_NAME)
                    or f"attachment_{i}"
                )

                # MIME type from MAPI property
                mime_type = get_mapi_property_string(attachment, PR_ATTACH_MIME_TAG)
                if not mime_type:
                    mime_type = "application/octet-stream"

                # Content-ID for inline images
                content_id = get_mapi_property_string(attachment, PR_ATTACH_CONTENT_ID)

                # Attachment method
                attach_method = get_mapi_property_integer(attachment, PR_ATTACH_METHOD)

                # Determine disposition
                disposition = "attachment"
                if content_id and attach_method == ATTACH_BY_VALUE:
                    disposition = "inline"

                att_dict = {
                    "content": base64.b64encode(att_data).decode("ascii"),
                    "type": mime_type,
                    "name": filename,
                    "disposition": disposition,
                }
                if disposition == "inline" and content_id:
                    att_dict["cid"] = content_id

                attachments.append(att_dict)
            except Exception:
                logger.debug("Failed to process attachment %d", i)
    except Exception:
        logger.debug("Failed to read attachments")

    if attachments:
        jmap_data["attachments"] = attachments

    if extra_headers:
        jmap_data["headers"] = extra_headers

    # PST is an archive: the Bcc list was in the original source file and we
    # are reconstructing the .eml for storage in the user's own mailbox, not
    # retransmitting. Preserve it.
    return compose_email(jmap_data, keep_bcc=True)


def _find_ipm_subtree(pst_file):
    """Find the IPM subtree folder (the real top-level mail folder).

    Uses PR_IPM_SUBTREE_ENTRYID from the message store to locate the
    subtree. Falls back to the root folder if not found.
    """
    root = pst_file.get_root_folder()
    try:
        store = pst_file.get_message_store()
    except Exception:
        return root
    subtree_entry_id = get_mapi_property_data(store, PR_IPM_SUBTREE_ENTRYID)
    subtree_id = _folder_id_from_entry_id(subtree_entry_id)
    if subtree_id is not None:
        try:
            for i in range(root.number_of_sub_folders):
                child = root.get_sub_folder(i)
                if child.get_identifier() == subtree_id:
                    return child
        except Exception:
            logger.debug("Failed to locate IPM subtree folder")
    return root


def count_pst_messages(pst_file, special_folder_map: Optional[dict] = None) -> int:
    """Recursively count email messages across all email folders in a PST file."""
    if special_folder_map is None:
        special_folder_map = build_special_folder_map(pst_file)

    count = 0
    start_folder = _find_ipm_subtree(pst_file)

    def _count_folder(folder, depth=0):
        nonlocal count
        if depth > MAX_FOLDER_DEPTH:
            logger.warning("Maximum folder depth exceeded in PST file")
            return
        if not _is_email_folder(folder):
            return
        try:
            count += folder.number_of_sub_messages
        except Exception:
            logger.debug("Failed to count sub_messages in folder")
        try:
            for i in range(folder.number_of_sub_folders):
                _count_folder(folder.get_sub_folder(i), depth + 1)
        except Exception:
            logger.debug("Failed to iterate sub_folders for counting")

    try:
        for i in range(start_folder.number_of_sub_folders):
            _count_folder(start_folder.get_sub_folder(i))
    except Exception:
        logger.debug("Failed to iterate root sub_folders for counting")

    return count


def walk_pst_messages(
    pst_file,
    special_folder_map: dict,
    store_email: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> Generator[Tuple[str, str, int, Optional[int], Optional[bytes]], None, None]:
    """Walk all email messages in a PST file, yielding them in chronological order.

    First pass: collect lightweight metadata (folder ref + message index).
    Sort by delivery_time (oldest first) for proper threading.
    Second pass: reconstruct EML one at a time to limit memory usage.

    Args:
        pst_file: An opened pypff file object.
        special_folder_map: Mapping from folder identifiers to folder types.
        store_email: Mailbox owner's email for sender fallback.
        recipient_email: Import target mailbox email, used as ultimate sender
            fallback domain when no sender can be extracted.

    Yields:
        (folder_type, folder_path, message_flags, flag_status, eml_bytes) tuples
        sorted chronologically. ``eml_bytes`` is ``None`` when the message
        could not be read from libpff or when EML reconstruction raised; the
        caller is expected to count those as failures rather than skip them.
    """
    start_folder = _find_ipm_subtree(pst_file)

    # Build well-known folder type map from SourceWellKnownFolderType named
    # property (Exchange/O365 migration PSTs). Merges with entry-ID-based map,
    # with entry-ID taking priority.
    wkft_map = build_well_known_folder_map(pst_file, start_folder)
    merged_map = {**wkft_map, **special_folder_map}

    # (timestamp, folder_ref, msg_index, folder_type, folder_path, flags, flag_status)
    collected = []
    # Keep folder references alive to prevent GC from releasing pypff resources
    _folder_refs = []

    def _collect_folder(folder, folder_type, folder_path, depth=0):
        if depth > MAX_FOLDER_DEPTH:
            logger.warning("Maximum folder depth exceeded in PST file")
            return
        if not _is_email_folder(folder):
            return
        _folder_refs.append(folder)

        ft = _get_folder_type(folder, merged_map)
        if ft != FOLDER_TYPE_NORMAL:
            folder_type = ft

        try:
            for i in range(folder.number_of_sub_messages):
                try:
                    message = folder.get_sub_message(i)
                    flags = _get_message_flags(message)
                    flag_status = _get_flag_status(message)
                    timestamp = _get_message_timestamp(message)
                    collected.append(
                        (
                            timestamp,
                            folder,
                            i,
                            folder_type,
                            folder_path,
                            flags,
                            flag_status,
                        )
                    )
                except Exception:
                    logger.debug(
                        "Failed to read message %d in folder %s", i, folder_path
                    )
        except Exception:
            logger.debug("Failed to iterate sub_messages in folder %s", folder_path)

        # Children inherit the parent's folder_type (so they keep the special
        # treatment, e.g. is_import_sender for Sent subfolders) and build
        # hierarchical paths. Special folders are called with folder_path=""
        # so their children start from just their own name.
        try:
            for i in range(folder.number_of_sub_folders):
                child = folder.get_sub_folder(i)
                child_name = None
                try:
                    if child.name:
                        child_name = sanitize_folder_name(child.name)
                except Exception:
                    logger.debug("Failed to read subfolder name")
                if folder_path and child_name:
                    child_path = f"{folder_path}/{child_name}"
                else:
                    child_path = child_name or folder_path
                _collect_folder(child, folder_type, child_path, depth + 1)
        except Exception:
            logger.debug("Failed to iterate sub_folders in folder %s", folder_path)

    try:
        for i in range(start_folder.number_of_sub_folders):
            subfolder = start_folder.get_sub_folder(i)
            name = "Inbox"
            try:
                if subfolder.name:
                    name = sanitize_folder_name(subfolder.name)
            except Exception:
                logger.debug("Failed to read root subfolder name")
            folder_type = _get_folder_type(subfolder, merged_map)
            # Fall back to name-based detection for direct children of
            # the IPM subtree (standard Outlook special folders).
            if folder_type == FOLDER_TYPE_NORMAL:
                folder_type = _detect_folder_type_by_name(name)
            # Special folders get empty path — their label comes from
            # folder_type, not folder_path. Their children build paths
            # starting from just their own name.
            folder_path = "" if folder_type != FOLDER_TYPE_NORMAL else name
            _collect_folder(subfolder, folder_type, folder_path)
    except Exception:
        logger.debug("Failed to iterate root sub_folders")

    # Sort by timestamp (oldest first), None timestamps go last
    collected.sort(key=lambda x: (x[0] is None, x[0] or 0))

    # Second pass: reconstruct EML one at a time
    for (
        _timestamp,
        folder_ref,
        msg_idx,
        folder_type,
        folder_path,
        flags,
        flag_status,
    ) in collected:
        try:
            message = folder_ref.get_sub_message(msg_idx)
        except Exception:
            # Cannot even read the message back from libpff — non-recoverable.
            logger.warning(
                "Failed to read message %d in folder %s", msg_idx, folder_path
            )
            yield folder_type, folder_path, flags, flag_status, None
            continue
        try:
            eml_bytes = reconstruct_eml(
                message, store_email=store_email, recipient_email=recipient_email
            )
        except Exception:
            # Yield None so pst_tasks.py counts this as a failure instead of
            # silently dropping it (was hidden at debug level previously).
            logger.exception(
                "Failed to reconstruct EML for message %d in folder %s",
                msg_idx,
                folder_path,
            )
            yield folder_type, folder_path, flags, flag_status, None
            continue
        yield folder_type, folder_path, flags, flag_status, eml_bytes
