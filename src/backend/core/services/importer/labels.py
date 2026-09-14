"""Label and flag processing for imported messages."""

import logging
import re

from django.utils import timezone

from jmap_email.types import JmapEmail

from core import models
from core.mda.utils import gmail_labels, header_value

logger = logging.getLogger(__name__)

# Thunderbird message flags, from mailnews/base/public/nsMsgMessageFlags.idl.
# Thunderbird keeps flags in its .msf index and only writes them into the mbox
# on compaction, so X-Mozilla-Status is the only place a Thunderbird export
# carries read and starred state: it writes no X-Keywords at all.
MOZILLA_STATUS_READ = 0x0001
MOZILLA_STATUS_MARKED = 0x0004
MOZILLA_STATUS_PATTERN = re.compile(r"[0-9A-Fa-f]{4}")

IMAP_LABEL_TO_MESSAGE_FLAG = {
    "Drafts": "is_draft",
    "Brouillons": "is_draft",
    "[Gmail]/Drafts": "is_draft",
    "[Gmail]/Brouillons": "is_draft",
    "DRAFT": "is_draft",
    "Draft": "is_draft",
    "INBOX.INBOX.Drafts": "is_draft",
    "Sent": "is_sender",
    "Messages envoyés": "is_sender",
    "[Gmail]/Sent Mail": "is_sender",
    "[Gmail]/Mails envoyés": "is_sender",
    "[Gmail]/Messages envoyés": "is_sender",
    "Sent Mail": "is_sender",
    "Mails envoyés": "is_sender",
    "INBOX.INBOX.Sent": "is_sender",
    "Archived": "is_archived",
    "Messages archivés": "is_archived",
    "Starred": "_starred",
    "[Gmail]/Starred": "_starred",
    "[Gmail]/Suivis": "_starred",
    "Favoris": "_starred",
    "Trash": "is_trashed",
    "TRASH": "is_trashed",
    "[Gmail]/Corbeille": "is_trashed",
    "Corbeille": "is_trashed",
    "INBOX.INBOX.Trash": "is_trashed",
    # TODO: '[Gmail]/Important'
    "OUTBOX": "is_sender",
    "Spam": "is_spam",
    "QUARANTAINE": "is_spam",
    "INBOX.INBOX.Junk": "is_spam",
}

IMAP_READ_UNREAD_LABELS = {
    "Ouvert": "read",
    "Non lus": "unread",
    "Opened": "read",
    "Unread": "unread",
}

IMAP_LABELS_TO_IGNORE = [
    "Promotions",
    "Social",
    "Boîte de réception",
    "Inbox",
    "INBOX",
    "[Gmail]/Important",
    "[Gmail]/All Mail",
    "[Gmail]/Tous les messages",
]


def _clean_label(label: str) -> str:
    """Strip whitespace and the ``INBOX/`` / ``INBOX.`` folder prefix."""
    cleaned_label = label.strip()
    if cleaned_label.startswith("INBOX/"):
        cleaned_label = "/".join(cleaned_label.split("/")[1:]).strip()
    if cleaned_label.startswith("INBOX."):
        cleaned_label = ".".join(cleaned_label.split(".")[1:]).strip()
    return cleaned_label


def is_plain_label(label: str) -> bool:
    """Whether a folder/label name comes back from X-Gmail-Labels as itself:
    not consumed as message state or ignored, and not altered by cleaning."""
    cleaned_label = _clean_label(label)
    return cleaned_label == label and not (
        cleaned_label in IMAP_READ_UNREAD_LABELS
        or cleaned_label in IMAP_LABEL_TO_MESSAGE_FLAG
        or cleaned_label in IMAP_LABELS_TO_IGNORE
    )


def _mozilla_status(parsed_email: JmapEmail) -> int | None:
    """The X-Mozilla-Status flag word, or None when absent, empty or unparseable.

    The first occurrence, wherever it sits: Thunderbird writes its own above
    the message's headers, but after X-Account-Key and X-UIDL for POP3 mail
    (nsPop3Sink.cpp), and keeps one the message already carried, updating it
    in place (nsLocalMailFolder.cpp).

    Only X-Mozilla-Status is read: X-Mozilla-Status2 carries no state we
    model (Attachment, Template, MDN), and X-Mozilla-Keys would need the
    tag key of every label from the writer's profile to mean anything.
    """
    raw = header_value(parsed_email, "X-Mozilla-Status")
    if not raw:
        return None
    # Exactly 4 hex digits, as Thunderbird writes and reads it (nsParseMailbox)
    if not MOZILLA_STATUS_PATTERN.fullmatch(raw):
        logger.warning("Ignoring unparseable X-Mozilla-Status header")
        return None
    return int(raw, 16)


def compute_labels_and_flags(
    parsed_email: JmapEmail,
    imap_labels: list[str] | None,
    imap_flags: list[str] | None,
    *,
    is_sender: bool = False,
) -> tuple[set[str], dict[str, bool]]:
    """Compute labels and flags for a parsed email.

    ``imap_labels`` and ``imap_flags`` are None for file sources (mbox, eml),
    and lists (possibly empty) for sources that report flags themselves.
    ``is_sender`` is True when the message was sent from the mailbox it is
    imported into.
    """
    # Folder-like names: from the source, and from X-Gmail-Labels
    from_file = imap_labels is None and imap_flags is None
    header_labels = gmail_labels(parsed_email, ("x-gmail-labels",))
    all_labels = list(imap_labels or []) + header_labels
    imap_flags = imap_flags or []

    message_flags = {}
    labels_to_add = set()
    read_state_from_labels = False
    for original_label in all_labels:
        cleaned_label = _clean_label(original_label)
        # Handle read/unread status
        if cleaned_label in IMAP_READ_UNREAD_LABELS:
            read_state_from_labels = True
            if IMAP_READ_UNREAD_LABELS[cleaned_label] == "read":
                message_flags["is_unread"] = False
            elif IMAP_READ_UNREAD_LABELS[cleaned_label] == "unread":
                message_flags["is_unread"] = True
            continue  # Skip further processing for this label
        message_flag = IMAP_LABEL_TO_MESSAGE_FLAG.get(cleaned_label)
        if message_flag:
            message_flags[message_flag] = True
        elif cleaned_label not in IMAP_LABELS_TO_IGNORE:
            labels_to_add.add(cleaned_label)

    # X-Keywords holds IMAP keywords, i.e. tags, never folders: always plain
    # labels. This is also how a user label spelled like a system one
    # ("Trash", "Unread") survives a roundtrip through our own export.
    labels_to_add.update(gmail_labels(parsed_email, ("x-keywords",)))

    # Read/starred state from a Thunderbird mbox. It is not read for sources
    # that report flags themselves (IMAP, PST), even with no flag set at all,
    # nor next to X-Gmail-Labels (Takeout, our own exports): an
    # X-Mozilla-Status there came with the message from its sender (Mozilla
    # bug 196749). Presence is what counts: an empty header still describes
    # the state.
    has_label_header = header_value(parsed_email, "X-Gmail-Labels") is not None
    mozilla_status = (
        _mozilla_status(parsed_email) if from_file and not has_label_header else None
    )
    if mozilla_status is not None:
        message_flags["is_unread"] = not mozilla_status & MOZILLA_STATUS_READ
        if mozilla_status & MOZILLA_STATUS_MARKED:
            message_flags["_starred"] = True

    # Handle read/unread status via IMAP flags
    if imap_flags:
        # If the \\Seen flag is present, the message is read
        is_seen = "\\Seen" in imap_flags
        message_flags["is_unread"] = not is_seen

        # Handle \\Draft flag
        if "\\Draft" in imap_flags:
            message_flags["is_draft"] = True

        # Handle \\Flagged flag (follow-up / starred)
        if "\\Flagged" in imap_flags:
            message_flags["_starred"] = True

    # Sent mail and drafts are read, unless a file says otherwise with an
    # explicit Opened/Unread label (our own exports always carry one). Sources
    # reporting flags themselves keep the rule unconditionally.
    if not (from_file and read_state_from_labels) and (
        is_sender or message_flags.get("is_sender") or message_flags.get("is_draft")
    ):
        message_flags["is_unread"] = False

    return labels_to_add, message_flags


def handle_duplicate_message(
    existing_message: models.Message,
    parsed_email: JmapEmail,
    imap_labels: list[str],
    imap_flags: list[str],
    mailbox: models.Mailbox,
) -> None:
    """Handle duplicate message by updating labels and flags."""
    # get labels from parsed_email
    labels, message_flags = compute_labels_and_flags(
        parsed_email, imap_labels, imap_flags
    )

    # Extract flags handled via ThreadAccess (not Message fields)
    import_is_unread = message_flags.pop("is_unread", True)
    import_is_starred = message_flags.pop("_starred", False)

    for flag, value in message_flags.items():
        if hasattr(existing_message, flag):
            setattr(existing_message, flag, value)
    if message_flags:
        update_fields = list(message_flags.keys())
        # Timestamps stay in lockstep with the booleans, as the flag endpoint
        # and the create path set them.
        if existing_message.is_trashed and existing_message.trashed_at is None:
            existing_message.trashed_at = timezone.now()
            update_fields.append("trashed_at")
        if existing_message.is_archived and existing_message.archived_at is None:
            existing_message.archived_at = timezone.now()
            update_fields.append("archived_at")
        existing_message.save(update_fields=update_fields)

    # Update ThreadAccess.starred_at if the duplicate is starred
    if not import_is_unread or import_is_starred:
        access = models.ThreadAccess.objects.filter(
            thread=existing_message.thread, mailbox=mailbox
        ).first()
        if access:
            update_fields = []
            if not import_is_unread and (
                access.read_at is None or existing_message.created_at > access.read_at
            ):
                access.read_at = existing_message.created_at
                update_fields.append("read_at")
            if import_is_starred and (
                access.starred_at is None
                or existing_message.created_at > access.starred_at
            ):
                access.starred_at = existing_message.created_at
                update_fields.append("starred_at")
            if update_fields:
                access.save(update_fields=update_fields)

    for label in labels:
        try:
            label_obj, _ = models.Label.objects.get_or_create(
                name=label, mailbox=mailbox
            )
            existing_message.thread.labels.add(label_obj)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Error creating label %s: %s", label, e)
            continue
