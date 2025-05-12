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


class MailboxPermissionChoices(models.TextChoices):
    """Defines the possible permissions a user can have to access to a mailbox."""

    READ = "read", _("Read")
    EDIT = "edit", _("Edit")
    SEND = "send", _("Send")
    DELETE = "delete", _("Delete")
    ADMIN = "admin", _("Admin")


class MessageRecipientTypeChoices(models.TextChoices):
    """Defines the possible types of message recipients."""

    TO = "to", _("To")
    CC = "cc", _("Cc")
    BCC = "bcc", _("Bcc")


THREAD_STATS_FIELDS_MAP = {
    "unread": "count_unread",
    "trashed": "count_trashed",
    "draft": "count_draft",
    "starred": "count_starred",
    "sender": "count_sender",
    "messages": "count_messages",
}
