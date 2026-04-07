"""
Core application enums declaration
"""

from enum import StrEnum

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


# Mailbox role groups for permission checks
MAILBOX_ROLES_CAN_EDIT = [
    MailboxRoleChoices.EDITOR,
    MailboxRoleChoices.SENDER,
    MailboxRoleChoices.ADMIN,
]
MAILBOX_ROLES_CAN_SEND = [
    MailboxRoleChoices.SENDER,
    MailboxRoleChoices.ADMIN,
]


class ThreadAccessRoleChoices(models.IntegerChoices):
    """Defines the possible roles a mailbox can have to access to a thread."""

    VIEWER = 1, "viewer"
    EDITOR = 2, "editor"


# Thread role groups for permission checks
THREAD_ROLES_CAN_EDIT = [
    ThreadAccessRoleChoices.EDITOR,
]


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
    CANCELLED = 5, "cancelled"


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
    "has_delivery_pending": "has_delivery_pending",
    "has_delivery_failed": "has_delivery_failed",
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


class ThreadEventTypeChoices(models.TextChoices):
    """Defines the possible types of thread events."""

    IM = "im", "Instant message"


class ChannelScopeLevel(models.TextChoices):
    """Scope level for a Channel: which resource the channel is bound to.

    - GLOBAL: instance-wide, no target. Creatable only via Django admin or CLI.
    - MAILDOMAIN: bound to one MailDomain; actions limited to that domain.
    - MAILBOX: bound to one Mailbox; actions limited to that mailbox.
    - USER: personal channel bound to a User; actions limited to mailboxes
      the user has MailboxAccess to.
    """

    GLOBAL = "global", "Global"
    MAILDOMAIN = "maildomain", "Maildomain"
    MAILBOX = "mailbox", "Mailbox"
    USER = "user", "User"


class ChannelTypes(StrEnum):
    """Known Channel.type values.

    ``StrEnum`` (not a Django ``TextChoices``): Channel.type is intentionally
    a free-form CharField so adding a new type never requires a migration.
    Members ARE strings (``ChannelTypes.MTA == "mta"``) so comparisons,
    dict keys and ORM filters work transparently.
    """

    MTA = "mta"
    WIDGET = "widget"
    API_KEY = "api_key"
    WEBHOOK = "webhook"


class WebhookEvents(StrEnum):
    """Known webhook event identifiers.

    Stored as strings in ``Channel.settings["events"]``; validated by the
    serializer at write time. Adding a new event is a Python-only change.
    """

    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"


class ChannelApiKeyScope(models.TextChoices):
    """Capability scopes granted to an api_key Channel.

    Stored as a list of string values in Channel.settings["scopes"] and
    enforced by the serializer + HasChannelScope permission at the API layer.
    Adding a new scope is a Python-only change (no DB choices, no migration).

    A credential's blast radius for any scope is automatically bounded by its
    channel's scope_level + target FK: a scope_level=mailbox api_key can only
    act on that mailbox, regardless of which scopes it holds.

    WRITE vs CREATE distinction: ``*_WRITE`` scopes modify an object the
    channel already has resource-scope access to (e.g. archiving a thread in
    a mailbox-scope channel's mailbox). ``*_CREATE`` scopes mint a brand-new
    top-level resource, which is an escalation — these are global-only and
    listed in ``CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY``. Most resources only
    need WRITE because their "create" never escalates; only mailboxes and
    maildomains have a meaningful _CREATE counterpart.

    **Only scopes wired to a real endpoint live in this enum.** Adding a new
    scope is a one-line change here once the endpoint exists. Forward-looking
    scopes are sketched in the comment block below for design reference but
    intentionally not enabled — having them in the enum without an enforcing
    endpoint is dead surface area an attacker could probe.
    """

    METRICS_READ = "metrics:read", "Read usage metrics"
    MAILBOXES_READ = "mailboxes:read", "Read mailboxes (and their users/roles)"
    MESSAGES_SEND = "messages:send", "Send outbound messages"
    MAILDOMAINS_CREATE = "maildomains:create", "Create new maildomains"

    # Forward-looking scopes — DO NOT uncomment without a real endpoint
    # enforcing them. Listed here so the planned vocabulary is visible at a
    # glance and so the WRITE/CREATE convention is documented.
    #
    # Reads:
    #   MAILDOMAINS_READ   = "maildomains:read",       "Read maildomains"
    #   USERS_READ         = "users:read",             "Read users"
    #   LABELS_READ        = "labels:read",            "Read labels"
    #   CONTACTS_READ      = "contacts:read",          "Read contacts"
    #   THREADS_READ       = "threads:read",           "Read thread metadata"
    #   MESSAGES_READ      = "messages:read",          "Read message metadata"
    #   MESSAGES_READ_BODY = "messages:read.body",     "Read message bodies"
    #   ATTACHMENTS_READ   = "attachments:read",       "Read attachments"
    #   BLOBS_READ         = "blobs:read",             "Read raw MIME blobs"
    #
    # Writes (update an object the channel already has access to):
    #   MESSAGES_WRITE     = "messages:write",         "Create/modify drafts"
    #   THREADS_WRITE      = "threads:write",          "Archive/star/label"
    #   LABELS_WRITE       = "labels:write",           "Create/modify labels"
    #   CONTACTS_WRITE     = "contacts:write",         "Create/modify contacts"
    #   MAILBOXES_WRITE    = "mailboxes:write",        "Update existing mailboxes"
    #   MAILDOMAINS_WRITE  = "maildomains:write",      "Update existing maildomains"
    #
    # Creates (mint a brand-new top-level object — global-only):
    #   MAILBOXES_CREATE   = "mailboxes:create",       "Create new mailboxes"


# Scopes that can only be granted to / used by a scope_level=global Channel.
# Two enforcement points use this set:
#  - the serializer (write time) rejects non-global channels asking for these
#  - HasChannelScope (request time) rejects requests where the calling
#    channel is not global but a global-only scope is required
CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY = frozenset(
    {
        ChannelApiKeyScope.METRICS_READ.value,
        ChannelApiKeyScope.MAILDOMAINS_CREATE.value,
    }
)


class MessageTemplateTypeChoices(models.IntegerChoices):
    """Defines the possible types of message templates."""

    MESSAGE = 1, "message"
    SIGNATURE = 2, "signature"
    AUTOREPLY = 3, "autoreply"


EML_SUPPORTED_MIME_TYPES = ["message/rfc822", "application/eml", "text/plain"]
MBOX_SUPPORTED_MIME_TYPES = [
    "application/octet-stream",
    "text/plain",
    "application/mbox",
]
PST_SUPPORTED_MIME_TYPES = ["application/vnd.ms-outlook"]
ARCHIVE_SUPPORTED_MIME_TYPES = (
    EML_SUPPORTED_MIME_TYPES + MBOX_SUPPORTED_MIME_TYPES + PST_SUPPORTED_MIME_TYPES
)

BLACKLISTED_PROXY_IMAGE_MIME_TYPES = [
    "image/svg+xml",  # Can contain JavaScript and external references
    "image/x-wmf",  # Windows Metafile - can contain executable code
    "image/wmf",
    "image/x-emf",  # Enhanced Metafile - same risks as WMF
    "image/emf",
    "image/x-icon",  # Icon files - can contain executable code
    "image/vnd.microsoft.icon",
    "image/x-icns",  # Apple Icon Image format - can contain executable code
    "image/cgm",
    "image/x-cut",
]
