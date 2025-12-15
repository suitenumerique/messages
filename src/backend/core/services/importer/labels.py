"""Label and flag processing for imported messages."""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from core import models

logger = logging.getLogger(__name__)

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
    "Starred": "is_starred",
    "[Gmail]/Starred": "is_starred",
    "[Gmail]/Suivis": "is_starred",
    "Favoris": "is_starred",
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


def compute_labels_and_flags(
    parsed_email: Dict[str, Any],
    imap_labels: Optional[List[str]],
    imap_flags: Optional[List[str]],
) -> Tuple[Set[str], Dict[str, bool]]:
    """Compute labels and flags for a parsed email."""

    # Combine both imap_labels and gmail_labels from parsed email
    gmail_labels = parsed_email.get("gmail_labels", [])
    imap_labels = imap_labels or []
    imap_flags = imap_flags or []
    all_labels = list(imap_labels) + list(gmail_labels)

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

    # Handle read/unread status via IMAP flags
    if imap_flags:
        # If the \\Seen flag is present, the message is read
        is_seen = "\\Seen" in imap_flags
        message_flags["is_unread"] = not is_seen

        # Handle \\Draft flag
        if "\\Draft" in imap_flags:
            message_flags["is_draft"] = True

    # Special case: if message is sender or draft, it should not be unread
    if message_flags.get("is_sender") or message_flags.get("is_draft"):
        message_flags["is_unread"] = False

    return labels_to_add, message_flags


def handle_duplicate_message(
    existing_message: models.Message,
    parsed_email: Dict[str, Any],
    imap_labels: List[str],
    imap_flags: List[str],
    mailbox: models.Mailbox,
) -> None:
    """Handle duplicate message by updating labels and flags."""
    # get labels from parsed_email
    labels, message_flags = compute_labels_and_flags(
        parsed_email, imap_labels, imap_flags
    )

    for flag, value in message_flags.items():
        if hasattr(existing_message, flag):
            setattr(existing_message, flag, value)
    existing_message.save(update_fields=message_flags.keys())

    for label in labels:
        try:
            label_obj, _ = models.Label.objects.get_or_create(
                name=label, mailbox=mailbox
            )
            existing_message.thread.labels.add(label_obj)

        except Exception as e:
            logger.exception("Error creating label %s: %s", label, e)
            continue
