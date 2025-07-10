"""
Core application enums declaration
"""

from django.conf import global_settings
from django.db import models
from django.utils.translation import gettext_lazy as _

# In Django's code base, `LANGUAGES` is set by default with all supported languages.
# We can use it for the choice of languages which should not be limited to the few languages
# active in the app.
# pylint: disable=no-member
ALL_LANGUAGES = {language: _(name) for language, name in global_settings.LANGUAGES}


class MailboxRoleChoices(models.IntegerChoices):
    """Defines the unique roles a user can have to access a mailbox."""

    VIEWER = 1, _("Viewer")
    EDITOR = 2, _("Editor")
    SENDER = 3, _("Sender")
    ADMIN = 4, _("Admin")


class ThreadAccessRoleChoices(models.IntegerChoices):
    """Defines the possible roles a mailbox can have to access to a thread."""

    VIEWER = 1, _("Viewer")
    EDITOR = 2, _("Editor")


class MessageRecipientTypeChoices(models.IntegerChoices):
    """Defines the possible types of message recipients."""

    TO = 1, _("To")
    CC = 2, _("Cc")
    BCC = 3, _("Bcc")


class MessageDeliveryStatusChoices(models.IntegerChoices):
    """Defines the possible statuses of a message delivery."""

    INTERNAL = 1, _("Internal")
    SENT = 2, _("Sent")
    FAILED = 3, _("Failed")
    RETRY = 4, _("Retry")


class MailDomainAccessRoleChoices(models.IntegerChoices):
    """Defines the unique roles a user can have to access a mail domain."""

    ADMIN = 1, _("Admin")


class CompressionTypeChoices(models.IntegerChoices):
    """Defines the possible compression types."""

    NONE = 0, "None"
    ZSTD = 1, "Zstd"


class DKIMAlgorithmChoices(models.IntegerChoices):
    """Defines the possible DKIM signing algorithms."""

    RSA = 1, _("RSA")
    ED25519 = 2, _("Ed25519")

    @property
    def dns_value(self) -> str:
        """Get the DNS record algorithm value for this choice."""
        mapping = {
            self.RSA: "rsa",
            self.ED25519: "ed25519",
        }
        return mapping[self]


THREAD_STATS_FIELDS_MAP = {
    "all": "all",
    "all_unread": "all_unread",
}
