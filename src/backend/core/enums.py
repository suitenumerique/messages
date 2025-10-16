"""
Core application enums declaration
"""

from django.conf import global_settings
from django.db import models

# In Django's code base, `LANGUAGES` is set by default with all supported languages.
# We can use it for the choice of languages which should not be limited to the few languages
# active in the app.
# pylint: disable=no-member
ALL_LANGUAGES = dict(global_settings.LANGUAGES)


class MailboxRoleChoices(models.IntegerChoices):
    """Defines the unique roles a user can have to access a mailbox."""

    VIEWER = 1, "viewer"
    EDITOR = 2, "editor"
    SENDER = 3, "sender"
    ADMIN = 4, "admin"


class ThreadAccessRoleChoices(models.IntegerChoices):
    """Defines the possible roles a mailbox can have to access to a thread."""

    VIEWER = 1, "viewer"
    EDITOR = 2, "editor"


class MessageRecipientTypeChoices(models.IntegerChoices):
    """Defines the possible types of message recipients."""

    TO = 1, "to"
    CC = 2, "cc"
    BCC = 3, "bcc"


class MessageDeliveryStatusChoices(models.IntegerChoices):
    """Defines the possible statuses of a message delivery."""

    INTERNAL = 1, "internal"
    SENT = 2, "sent"
    FAILED = 3, "failed"
    RETRY = 4, "retry"


class MailDomainAccessRoleChoices(models.IntegerChoices):
    """Defines the unique roles a user can have to access a mail domain."""

    ADMIN = 1, "admin"


class CompressionTypeChoices(models.IntegerChoices):
    """Defines the possible compression types."""

    NONE = 0, "None"
    ZSTD = 1, "Zstd"


class DKIMAlgorithmChoices(models.IntegerChoices):
    """Defines the possible DKIM signing algorithms."""

    RSA = 1, "rsa"
    ED25519 = 2, "ed25519"


THREAD_STATS_FIELDS_MAP = {
    "all": "all",
    "all_unread": "all_unread",
}


# Abilities
class UserAbilities(models.TextChoices):
    """Defines the possible abilities a user can have."""

    CAN_VIEW_DOMAIN_ADMIN = "view_maildomains", "Can view domain admin"
    CAN_CREATE_MAILDOMAINS = "create_maildomains", "Can create maildomains"
    CAN_MANAGE_MAILDOMAIN_ACCESSES = (
        "manage_maildomain_accesses",
        "Can manage maildomain accesses",
    )


class CRUDAbilities(models.TextChoices):
    """Mixin that provides standard CRUD abilities."""

    CAN_READ = "get", "Can read"
    CAN_CREATE = "post", "Can create"
    CAN_UPDATE = "put", "Can update"
    CAN_PARTIALLY_UPDATE = "patch", "Can partially update"
    CAN_DELETE = "delete", "Can delete"


class MailDomainAbilities(models.TextChoices):
    """Defines specific abilities a MailDomain can have."""

    CAN_MANAGE_ACCESSES = "manage_accesses", "Can manage accesses"
    CAN_MANAGE_MAILBOXES = "manage_mailboxes", "Can manage mailboxes"


class MailboxAbilities(models.TextChoices):
    """Defines specific abilities a Mailbox can have."""

    CAN_MANAGE_ACCESSES = "manage_accesses", "Can manage accesses"
    CAN_VIEW_MESSAGES = "view_messages", "Can view mailbox messages"
    CAN_SEND_MESSAGES = "send_messages", "Can send messages from mailbox"
    CAN_MANAGE_LABELS = "manage_labels", "Can manage mailbox labels"
    CAN_MANAGE_MESSAGE_TEMPLATES = (
        "manage_message_templates",
        "Can manage mailbox message templates",
    )
    CAN_IMPORT_MESSAGES = "import_messages", "Can import messages"


class MessageTemplateTypeChoices(models.IntegerChoices):
    """Defines the possible types of message templates."""

    MESSAGE = 1, "message"
    SIGNATURE = 2, "signature"


EML_SUPPORTED_MIME_TYPES = ["message/rfc822", "application/eml", "text/plain"]
MBOX_SUPPORTED_MIME_TYPES = ["application/octet-stream", "text/plain", "application/mbox"]
ARCHIVE_SUPPORTED_MIME_TYPES = EML_SUPPORTED_MIME_TYPES + MBOX_SUPPORTED_MIME_TYPES
