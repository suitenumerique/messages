"""
Declare and configure the models for the messages core application
"""
# pylint: disable=too-many-lines

import uuid
from datetime import timedelta
from logging import getLogger

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.contrib.auth.base_user import AbstractBaseUser
from django.core import validators
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from timezone_field import TimeZoneField

from core.enums import MailboxPermissionChoices, MessageRecipientTypeChoices

logger = getLogger(__name__)


def get_trashbin_cutoff():
    """
    Calculate the cutoff datetime for soft-deleted items based on the retention policy.

    The function returns the current datetime minus the number of days specified in
    the TRASHBIN_CUTOFF_DAYS setting, indicating the oldest date for items that can
    remain in the trash bin.

    Returns:
        datetime: The cutoff datetime for soft-deleted items.
    """
    return timezone.now() - timedelta(days=settings.TRASHBIN_CUTOFF_DAYS)


class LinkRoleChoices(models.TextChoices):
    """Defines the possible roles a link can offer on a item."""

    READER = "reader", _("Reader")  # Can read
    EDITOR = "editor", _("Editor")  # Can read and edit


class RoleChoices(models.TextChoices):
    """Defines the possible roles a user can have in a resource."""

    READER = "reader", _("Reader")  # Can read
    EDITOR = "editor", _("Editor")  # Can read and edit
    ADMIN = "administrator", _("Administrator")  # Can read, edit, delete and share
    OWNER = "owner", _("Owner")


PRIVILEGED_ROLES = [RoleChoices.ADMIN, RoleChoices.OWNER]


class DuplicateEmailError(Exception):
    """Raised when an email is already associated with a pre-existing user."""

    def __init__(self, message=None, email=None):
        """Set message and email to describe the exception."""
        self.message = message
        self.email = email
        super().__init__(self.message)


class BaseModel(models.Model):
    """
    Serves as an abstract base model for other models, ensuring that records are validated
    before saving as Django doesn't do it by default.

    Includes fields common to all models: a UUID primary key and creation/update timestamps.
    """

    id = models.UUIDField(
        verbose_name=_("id"),
        help_text=_("primary key for the record as UUID"),
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(
        verbose_name=_("created on"),
        help_text=_("date and time at which a record was created"),
        auto_now_add=True,
        editable=False,
    )
    updated_at = models.DateTimeField(
        verbose_name=_("updated on"),
        help_text=_("date and time at which a record was last updated"),
        auto_now=True,
        editable=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Call `full_clean` before saving."""
        self.full_clean()
        super().save(*args, **kwargs)


class UserManager(auth_models.UserManager):
    """Custom manager for User model with additional methods."""

    def get_user_by_sub_or_email(self, sub, email):
        """Fetch existing user by sub or email."""
        try:
            return self.get(sub=sub)
        except self.model.DoesNotExist as err:
            if not email:
                return None

            if settings.OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION:
                try:
                    return self.get(email=email)
                except self.model.DoesNotExist:
                    pass
            elif (
                self.filter(email=email).exists()
                and not settings.OIDC_ALLOW_DUPLICATE_EMAILS
            ):
                raise DuplicateEmailError(
                    _(
                        "We couldn't find a user with this sub but the email is already "
                        "associated with a registered user."
                    )
                ) from err
        return None


class User(AbstractBaseUser, BaseModel, auth_models.PermissionsMixin):
    """User model to work with OIDC only authentication."""

    sub_validator = validators.RegexValidator(
        regex=r"^[\w.@+-:]+\Z",
        message=_(
            "Enter a valid sub. This value may contain only letters, "
            "numbers, and @/./+/-/_/: characters."
        ),
    )

    sub = models.CharField(
        _("sub"),
        help_text=_(
            "Required. 255 characters or fewer. Letters, numbers, and @/./+/-/_/: characters only."
        ),
        max_length=255,
        unique=True,
        validators=[sub_validator],
        blank=True,
        null=True,
    )

    full_name = models.CharField(_("full name"), max_length=100, null=True, blank=True)
    short_name = models.CharField(_("short name"), max_length=20, null=True, blank=True)

    email = models.EmailField(_("identity email address"), blank=True, null=True)

    # Unlike the "email" field which stores the email coming from the OIDC token, this field
    # stores the email used by staff users to login to the admin site
    admin_email = models.EmailField(
        _("admin email address"), unique=True, blank=True, null=True
    )

    language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        verbose_name=_("language"),
        help_text=_("The language in which the user wants to see the interface."),
    )
    timezone = TimeZoneField(
        choices_display="WITH_GMT_OFFSET",
        use_pytz=False,
        default=settings.TIME_ZONE,
        help_text=_("The timezone in which the user wants to see times."),
    )
    is_device = models.BooleanField(
        _("device"),
        default=False,
        help_text=_("Whether the user is a device or a real user."),
    )
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Whether the user can log into this admin site."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_(
            "Whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )

    objects = UserManager()

    USERNAME_FIELD = "admin_email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "messages_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.email or self.admin_email or str(self.id)


class MailDomain(BaseModel):
    """Mail domain model to store mail domain information."""

    name = models.CharField(_("name"), max_length=255)

    class Meta:
        db_table = "messages_maildomain"
        verbose_name = _("mail domain")
        verbose_name_plural = _("mail domains")

    def __str__(self):
        return self.name


class Mailbox(BaseModel):
    """Mailbox model to store mailbox information."""

    local_part = models.CharField(_("local part"), max_length=255)
    domain = models.ForeignKey("MailDomain", on_delete=models.CASCADE)

    class Meta:
        db_table = "messages_mailbox"
        verbose_name = _("mailbox")
        verbose_name_plural = _("mailboxes")
        unique_together = ("local_part", "domain")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.local_part}@{self.domain.name}"


class MailboxAccess(BaseModel):
    """Mailbox access model to store mailbox access information."""

    mailbox = models.ForeignKey(
        "Mailbox", on_delete=models.CASCADE, related_name="accesses"
    )
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="mailbox_accesses"
    )
    permission = models.CharField(
        _("permission"),
        max_length=20,
        choices=MailboxPermissionChoices.choices,
        default=MailboxPermissionChoices.READ,
    )

    class Meta:
        db_table = "messages_mailboxaccess"
        verbose_name = _("mailbox access")
        verbose_name_plural = _("mailbox accesses")

    def __str__(self):
        return f"Access to {self.mailbox} for {self.user}"


class Thread(BaseModel):
    """Thread model to group messages."""

    subject = models.CharField(_("subject"), max_length=255)
    snippet = models.TextField(_("snippet"), blank=True)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE)

    class Meta:
        db_table = "messages_thread"
        verbose_name = _("thread")
        verbose_name_plural = _("threads")

    def __str__(self):
        return self.subject


class Contact(BaseModel):
    """Contact model to store contact information."""

    name = models.CharField(_("name"), max_length=255)
    email = models.EmailField(_("email"), unique=True)
    user = models.ForeignKey("User", on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        db_table = "messages_contact"
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")

    def __str__(self):
        return self.name


class MessageRecipient(BaseModel):
    """Message recipient model to store message recipient information."""

    message = models.ForeignKey(
        "Message", on_delete=models.CASCADE, related_name="recipients"
    )
    contact = models.ForeignKey(
        "Contact", on_delete=models.CASCADE, related_name="messages"
    )
    type = models.CharField(
        _("type"),
        max_length=20,
        choices=MessageRecipientTypeChoices.choices,
        default=MessageRecipientTypeChoices.TO,
    )

    class Meta:
        db_table = "messages_messagerecipient"
        verbose_name = _("message recipient")
        verbose_name_plural = _("message recipients")

    def __str__(self):
        return f"{self.message} - {self.contact} - {self.type}"


class Message(BaseModel):
    """Message model to store received and sent messages."""

    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="messages"
    )
    subject = models.CharField(_("subject"), max_length=255)
    # header = models.ForeignKey(EmailHeader, on_delete=models.CASCADE)
    # body = models.ForeignKey(EmailBody, on_delete=models.CASCADE)
    body_html = models.TextField(_("body html"), blank=True)
    body_text = models.TextField(_("body text"), blank=True)
    sender = models.ForeignKey("Contact", on_delete=models.CASCADE)
    received_at = models.DateTimeField(_("received at"), auto_now_add=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    sent_at = models.DateTimeField(_("sent at"))
    read_at = models.DateTimeField(_("read at"))
    mta_sent = models.BooleanField(_("mta sent"), default=False)
    is_read = models.BooleanField(_("is read"), default=False)

    class Meta:
        db_table = "messages_message"
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ["-received_at"]

    def __str__(self):
        return self.subject
