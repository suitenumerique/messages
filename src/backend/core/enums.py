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
