"""Label and flag processing for imported messages."""

import logging

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


def _mozilla_status(parsed_email: JmapEmail) -> int | None:
    """The X-Mozilla-Status flag word, or None when absent or unparseable.

    Only X-Mozilla-Status is read: X-Mozilla-Status2 carries no state we
    model (Attachment, Template, MDN), and X-Mozilla-Keys would need the
    tag key of every label from the writer's profile to mean anything.
    """
    raw = header_value(parsed_email, "X-Mozilla-Status")
    if raw is None:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        logger.warning("Ignoring unparseable X-Mozilla-Status: %r", raw[:32])
        return None


def compute_labels_and_flags(
    parsed_email: JmapEmail,
    imap_labels: list[str] | None,
    imap_flags: list[str] | None,
) -> tuple[set[str], dict[str, bool]]:
    """Compute labels and flags for a parsed email."""

    # Combine both imap_labels and gmail_labels from parsed email
    imap_labels = imap_labels or []
    imap_flags = imap_flags or []
    all_labels = list(imap_labels) + gmail_labels(parsed_email)

    message_flags = {}
    labels_to_add = set()
    for original_label in all_labels:
        cleaned_label = original_label.strip()
        if cleaned_label.startswith("INBOX/"):
            cleaned_label = "/".join(cleaned_label.split("/")[1:]).strip()
        if cleaned_label.startswith("INBOX."):
            cleaned_label = ".".join(cleaned_label.split(".")[1:]).strip()
        # Handle read/unread status
        if cleaned_label in IMAP_READ_UNREAD_LABELS:
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

    # Read/starred state from a Thunderbird mbox. Applied before the IMAP
    # flags below so a live \\Seen always wins: on an IMAP import the server
    # is authoritative, and a message can carry an X-Mozilla-Status written by
    # whoever sent it (Mozilla bug 196749).
    mozilla_status = _mozilla_status(parsed_email)
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

    # Special case: if message is sender or draft, it should not be unread
    if message_flags.get("is_sender") or message_flags.get("is_draft"):
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
        # Timestamps stay in lockstep with the booleans: a trashed/archived
        # row with a NULL timestamp breaks restore, ordering and auto-purge.
        if existing_message.is_trashed and existing_message.trashed_at is None:
            existing_message.trashed_at = existing_message.created_at
            update_fields.append("trashed_at")
        if existing_message.is_archived and existing_message.archived_at is None:
            existing_message.archived_at = existing_message.created_at
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
