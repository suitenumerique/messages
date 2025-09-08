"""
Declare and configure the models for the messages core application
"""
# pylint: disable=too-many-lines,too-many-instance-attributes

import base64
import hashlib
import uuid
from datetime import timedelta
from logging import getLogger
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.contrib.auth.base_user import AbstractBaseUser
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

import jsonschema
import pyzstd
from encrypted_fields.fields import EncryptedTextField
from timezone_field import TimeZoneField

from core.enums import (
    CompressionTypeChoices,
    CRUDAbilities,
    DKIMAlgorithmChoices,
    MailboxAbilities,
    MailboxRoleChoices,
    MailDomainAbilities,
    MailDomainAccessRoleChoices,
    MessageDeliveryStatusChoices,
    MessageRecipientTypeChoices,
    ThreadAccessRoleChoices,
    UserAbilities,
)
from core.mda.rfc5322 import parse_email_message
from core.mda.signing import generate_dkim_key as _generate_dkim_key

logger = getLogger(__name__)


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

    full_name = models.CharField(_("full name"), max_length=255, null=True, blank=True)

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

    custom_attributes = models.JSONField(
        _("Custom attributes"),
        default=dict,
        blank=True,
        help_text=_("Metadata to sync to the user in the identity provider."),
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

    def save(self, *args, **kwargs):
        """Enforce validation before saving."""
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        """Validate fields values."""
        try:
            jsonschema.validate(
                self.custom_attributes, settings.SCHEMA_CUSTOM_ATTRIBUTES_USER
            )
        except jsonschema.ValidationError as exception:
            raise ValidationError(
                {"custom_attributes": exception.message}
            ) from exception

        super().clean()

    def get_abilities(self):
        """
        Return abilities of the logged-in user.

        - Superuser and maildomain admin can view maildomains
        - Only superuser can create maildomains!
        """
        # if user as access to any maildomain or is superuser, he can view them
        has_access = self.maildomain_accesses.exists()
        is_admin = self.is_superuser
        return {
            UserAbilities.CAN_VIEW_DOMAIN_ADMIN: has_access or is_admin,
            UserAbilities.CAN_CREATE_MAILDOMAINS: is_admin,
        }


class MailDomain(BaseModel):
    """Mail domain model to store mail domain information."""

    name_validator = validators.RegexValidator(
        regex=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
        message=_(
            "Enter a valid domain name. This value may contain only lowercase "
            "letters, numbers, dots and - characters."
        ),
    )

    name = models.CharField(
        _("name"), max_length=253, unique=True, validators=[name_validator]
    )

    alias_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True
    )

    oidc_autojoin = models.BooleanField(
        _("oidc autojoin"),
        default=False,
        help_text=_("Create mailboxes automatically based on OIDC emails."),
    )

    identity_sync = models.BooleanField(
        _("Identity sync"),
        default=False,
        help_text=_("Sync mailboxes to an identity provider."),
    )

    custom_attributes = models.JSONField(
        _("Custom attributes"),
        default=dict,
        blank=True,
        help_text=_(
            "Metadata to sync to the maildomain group in the identity provider."
        ),
    )

    class Meta:
        db_table = "messages_maildomain"
        verbose_name = _("mail domain")
        verbose_name_plural = _("mail domains")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Enforce validation before saving."""
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        """Validate custom attributes."""
        try:
            jsonschema.validate(
                self.custom_attributes, settings.SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN
            )
        except jsonschema.ValidationError as exception:
            raise ValidationError(
                {"custom_attributes": exception.message}
            ) from exception

        super().clean()

    def get_expected_dns_records(self) -> List[str]:
        """Get the list of DNS records we expect to be present for this domain."""

        technical_domain = settings.MESSAGES_TECHNICAL_DOMAIN

        records = [
            {"target": "", "type": "mx", "value": f"10 mx1.{technical_domain}."},
            {"target": "", "type": "mx", "value": f"20 mx2.{technical_domain}."},
            {
                "target": "",
                "type": "txt",
                "value": f"v=spf1 include:_spf.{technical_domain} -all",
            },
            {
                "target": "_dmarc",
                "type": "txt",
                "value": "v=DMARC1; p=reject; adkim=s; aspf=s;",
            },
        ]

        # Add DKIM record if we have an active DKIM key
        dkim_key = self.get_active_dkim_key()
        if dkim_key:
            records.append(
                {
                    "target": f"{dkim_key.selector}._domainkey",
                    "type": "txt",
                    "value": dkim_key.get_dns_record_value(),
                }
            )

        return records

    def get_abilities(self, user):
        """
        Compute and return abilities for a given user on the mail domain.
        """
        role = None

        if user.is_authenticated:
            try:
                role = self.user_role
            except AttributeError:
                # Use prefetched accesses if available to avoid additional queries
                if (
                    hasattr(self, "_prefetched_objects_cache")
                    and "accesses" in self._prefetched_objects_cache
                ):
                    # Find the user's access in the prefetched accesses
                    for access in self.accesses.all():
                        if access.user_id == user.id:
                            role = access.role
                            break
                else:
                    try:
                        role = self.accesses.filter(user=user).values("role")[0]["role"]
                    except (MailDomainAccess.DoesNotExist, IndexError):
                        role = None

        is_admin = role == MailDomainAccessRoleChoices.ADMIN or user.is_superuser

        return {
            CRUDAbilities.CAN_READ: bool(role),
            CRUDAbilities.CAN_CREATE: is_admin,
            CRUDAbilities.CAN_UPDATE: is_admin,
            CRUDAbilities.CAN_PARTIALLY_UPDATE: is_admin,
            CRUDAbilities.CAN_DELETE: is_admin,
            MailDomainAbilities.CAN_MANAGE_ACCESSES: is_admin,
            MailDomainAbilities.CAN_MANAGE_MAILBOXES: is_admin,
        }

    def generate_dkim_key(
        self,
        selector: str = settings.MESSAGES_DKIM_DEFAULT_SELECTOR,
        algorithm: DKIMAlgorithmChoices = DKIMAlgorithmChoices.RSA,
        key_size: int = 2048,
    ) -> "DKIMKey":
        """
        Generate and create a new DKIM key for this domain.

        Args:
            selector: The DKIM selector (e.g., 'default', 'mail')
            algorithm: The signing algorithm
            key_size: The key size in bits (e.g., 2048, 4096 for RSA)

        Returns:
            The created DKIMKey instance
        """
        # Generate private and public keys
        private_key, public_key = _generate_dkim_key(algorithm, key_size=key_size)
        return DKIMKey.objects.create(
            selector=selector,
            private_key=private_key,
            public_key=public_key,
            algorithm=algorithm,
            key_size=key_size,
            is_active=True,
            domain=self,
        )

    def get_active_dkim_key(self):
        """Get the most recent active DKIM key for this domain."""
        return (
            DKIMKey.objects.filter(
                domain=self, is_active=True
            ).first()  # Most recent due to ordering in model
        )


class Mailbox(BaseModel):
    """Mailbox model to store mailbox information."""

    local_part = models.CharField(
        _("local part"),
        max_length=64,
        validators=[validators.RegexValidator(regex=r"^[a-zA-Z0-9_.-]+$")],
    )
    domain = models.ForeignKey("MailDomain", on_delete=models.CASCADE)
    contact = models.ForeignKey(
        "Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mailboxes",
    )

    is_identity = models.BooleanField(
        _("is identity"),
        default=True,
        help_text=_(
            "Whether this mailbox identifies a person (i.e. is not an alias or a group)"
        ),
    )

    alias_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        db_table = "messages_mailbox"
        verbose_name = _("mailbox")
        verbose_name_plural = _("mailboxes")
        unique_together = ("local_part", "domain")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.local_part}@{self.domain.name}"

    @property
    def threads_viewer(self):
        """Return queryset of threads where the mailbox has at least viewer access."""
        return Thread.objects.filter(
            accesses__mailbox=self,
        )

    @property
    def threads_editor(self):
        """Return queryset of threads where the mailbox has editor access."""
        return Thread.objects.filter(
            accesses__mailbox=self,
            accesses__role=ThreadAccessRoleChoices.EDITOR,
        )

    def create_blob(
        self,
        content: bytes,
        content_type: str,
        compression: Optional[CompressionTypeChoices] = CompressionTypeChoices.ZSTD,
    ) -> "Blob":
        """
        Create a new blob with automatic SHA256 calculation and compression.

        Args:
            content: Raw binary content to store
            content_type: MIME type of the content
            compression: Compression type to use (defaults to ZSTD)

        Returns:
            The created Blob instance

        Raises:
            ValueError: If content is empty
        """
        if not content:
            raise ValueError("Content cannot be empty")

        # Calculate SHA256 hash of the original content
        sha256_hash = hashlib.sha256(content).digest()

        # Store the original size
        original_size = len(content)

        # Apply compression if requested
        compressed_content = content
        if compression == CompressionTypeChoices.ZSTD:
            compressed_content = pyzstd.compress(
                content, level_or_option=settings.MESSAGES_BLOB_ZSTD_LEVEL
            )
            logger.debug(
                "Compressed blob from %d bytes to %d bytes (%.1f%% reduction)",
                original_size,
                len(compressed_content),
                (1 - len(compressed_content) / original_size) * 100,
            )
        elif compression == CompressionTypeChoices.NONE:
            compressed_content = content
        else:
            raise ValueError(f"Unsupported compression type: {compression}")

        # Create the blob
        blob = Blob.objects.create(
            sha256=sha256_hash,
            size=original_size,
            content_type=content_type,
            compression=compression,
            raw_content=compressed_content,
            mailbox=self,
        )

        logger.info(
            "Created blob %s: %d bytes, %s compression, %s content type",
            blob.id,
            original_size,
            compression.label,
            content_type,
        )

        return blob

    def get_abilities(self, user):
        """
        Compute and return abilities for a given user on the mailbox.
        """
        role = None

        if user.is_authenticated:
            # Use the annotated user_role field
            try:
                role = self.user_role
            # Fallback to query if not pre-calculated (should not happen with optimized ViewSet)
            except AttributeError:
                if (
                    hasattr(self, "_prefetched_objects_cache")
                    and "accesses" in self._prefetched_objects_cache
                ):
                    # Find the user's access in the prefetched accesses
                    for access in self.accesses.all():
                        if access.user_id == user.id:
                            role = access.role
                            break
                else:
                    try:
                        role = self.accesses.filter(user=user).values("role")[0]["role"]
                    except (MailboxAccess.DoesNotExist, IndexError):
                        role = None

        if role is None:
            return {
                "get": False,
                "patch": False,
                "put": False,
                "post": False,
                "delete": False,
                "manage_accesses": False,
                "view_messages": False,
                "send_messages": False,
                "manage_labels": False,
            }

        is_admin = role == MailboxRoleChoices.ADMIN
        can_modify = role >= MailboxRoleChoices.EDITOR
        can_delete = role == MailboxRoleChoices.ADMIN
        can_send = role >= MailboxRoleChoices.SENDER
        has_access = bool(role)

        return {
            CRUDAbilities.CAN_READ: has_access,
            CRUDAbilities.CAN_CREATE: can_modify,
            CRUDAbilities.CAN_PARTIALLY_UPDATE: can_modify,
            CRUDAbilities.CAN_UPDATE: can_modify,
            CRUDAbilities.CAN_DELETE: can_delete,
            MailboxAbilities.CAN_MANAGE_ACCESSES: is_admin,
            MailboxAbilities.CAN_VIEW_MESSAGES: has_access,
            MailboxAbilities.CAN_SEND_MESSAGES: can_send,
            MailboxAbilities.CAN_MANAGE_LABELS: can_modify,
        }


class MailboxAccess(BaseModel):
    """Mailbox access model to store mailbox access information."""

    mailbox = models.ForeignKey(
        "Mailbox", on_delete=models.CASCADE, related_name="accesses"
    )
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="mailbox_accesses"
    )
    role = models.SmallIntegerField(
        _("role"),
        choices=MailboxRoleChoices.choices,
        default=MailboxRoleChoices.VIEWER,
    )

    accessed_at = models.DateTimeField(
        _("accessed at"), null=True, blank=True, db_index=True
    )

    class Meta:
        db_table = "messages_mailboxaccess"
        verbose_name = _("mailbox access")
        verbose_name_plural = _("mailbox accesses")
        unique_together = ("mailbox", "user")

    def __str__(self):
        return f"Access to {self.mailbox} for {self.user} with {self.role} role"

    def mark_accessed(self, only_if_older_than_minutes: int = 60):
        """
        Update the accessed_at timestamp to now if older than the specified minutes.

        Args:
            only_if_older_than_minutes: Only update if the last access was older than this many minutes.
                Defaults to 60 minutes to avoid excessive updates.
        """
        if self.accessed_at is None or self.accessed_at < timezone.now() - timedelta(
            minutes=only_if_older_than_minutes
        ):
            self.accessed_at = timezone.now()
            self.save(update_fields=["accessed_at"])


class Thread(BaseModel):
    """Thread model to group messages."""

    subject = models.CharField(_("subject"), max_length=255, null=True, blank=True)
    snippet = models.TextField(_("snippet"), blank=True)
    has_unread = models.BooleanField(_("has unread"), default=False)
    has_trashed = models.BooleanField(_("has trashed"), default=False)
    has_draft = models.BooleanField(_("has draft"), default=False)
    has_starred = models.BooleanField(_("has starred"), default=False)
    has_sender = models.BooleanField(_("has sender"), default=False)
    has_messages = models.BooleanField(_("has messages"), default=True)
    has_attachments = models.BooleanField(_("has attachments"), default=False)
    is_spam = models.BooleanField(_("is spam"), default=False)
    has_active = models.BooleanField(_("has active"), default=False)
    messaged_at = models.DateTimeField(_("messaged at"), null=True, blank=True)
    sender_names = models.JSONField(_("sender names"), null=True, blank=True)
    summary = models.TextField(_("summary"), null=True, blank=True, default=None)

    class Meta:
        db_table = "messages_thread"
        verbose_name = _("thread")
        verbose_name_plural = _("threads")

    def __str__(self):
        return str(self.subject) if self.subject else "(no subject)"

    def update_stats(self):
        """Update the denormalized stats of the thread."""
        # Fetch all message metadata in a single query to avoid multiple DB hits
        message_data = list(
            self.messages.select_related("sender")
            .values(
                "is_unread",
                "is_trashed",
                "is_draft",
                "is_starred",
                "is_sender",
                "is_spam",
                "is_archived",
                "has_attachments",
                "created_at",
                "sender__name",
            )
            .order_by("created_at")
        )

        if not message_data:
            # No messages in thread
            self.has_unread = False
            self.has_trashed = False
            self.has_draft = False
            self.has_starred = False
            self.has_sender = False
            self.has_messages = False
            self.has_attachments = False
            self.is_spam = False
            self.has_active = False
            self.messaged_at = None
            self.sender_names = None
        else:
            # Compute stats in Python
            self.has_unread = any(
                msg["is_unread"] and not msg["is_trashed"] for msg in message_data
            )
            self.has_trashed = any(msg["is_trashed"] for msg in message_data)
            self.has_draft = any(
                msg["is_draft"] and not msg["is_trashed"] for msg in message_data
            )
            self.has_starred = any(
                msg["is_starred"] and not msg["is_trashed"] for msg in message_data
            )
            self.has_sender = any(
                msg["is_sender"] and not msg["is_trashed"] and not msg["is_draft"]
                for msg in message_data
            )
            self.has_attachments = any(
                msg["has_attachments"] and not msg["is_trashed"] for msg in message_data
            )

            # Check if we have any non-trashed, non-spam messages
            active_messages = [
                msg
                for msg in message_data
                if not msg["is_trashed"] and not msg["is_spam"]
            ]
            self.has_messages = len(active_messages) > 0

            # Set is_spam based on first message
            self.is_spam = message_data[0]["is_spam"]

            # Check if thread has active messages (!is_sender && !is_spam && !is_archived && !is_trashed && !is_draft)
            self.has_active = any(
                not msg["is_sender"]
                and not msg["is_spam"]
                and not msg["is_archived"]
                and not msg["is_trashed"]
                and not msg["is_draft"]
                for msg in message_data
            )

            # Set messaged_at to the creation time of the most recent non-trashed message
            non_trashed_messages = [
                msg for msg in message_data if not msg["is_trashed"]
            ]
            if non_trashed_messages:
                self.messaged_at = max(
                    msg["created_at"] for msg in non_trashed_messages
                )
            elif len(message_data) > 0:
                self.messaged_at = max(msg["created_at"] for msg in message_data)
            else:
                self.messaged_at = None

            # Set sender names (first and last sender names)
            sender_names = None
            if len(active_messages) > 0:
                sender_names = [
                    active_messages[0]["sender__name"],
                    active_messages[-1]["sender__name"],
                ]
            elif len(message_data) > 0:
                sender_names = [
                    message_data[0]["sender__name"],
                    message_data[-1]["sender__name"],
                ]

            if sender_names:
                # Get unique sender names from first and last messages
                first_sender = sender_names[0]
                last_sender = sender_names[-1]
                if last_sender is not None and first_sender != last_sender:
                    self.sender_names = [first_sender, last_sender]
                else:
                    self.sender_names = [first_sender]

        self.save(
            update_fields=[
                "has_unread",
                "has_trashed",
                "has_draft",
                "has_starred",
                "has_sender",
                "has_messages",
                "has_attachments",
                "is_spam",
                "has_active",
                "messaged_at",
                "sender_names",
            ]
        )


class Label(BaseModel):
    """Label model to organize threads into folders using slash-based naming."""

    name = models.CharField(
        _("name"),
        max_length=255,
        help_text=_(
            "Name of the label/folder (can use slashes for hierarchy, e.g. 'Work/Projects')"
        ),
    )
    slug = models.SlugField(
        _("slug"),
        max_length=255,
        help_text=_("URL-friendly version of the name"),
    )
    color = models.CharField(
        _("color"),
        max_length=7,
        default="#E3E3FD",
        help_text=_("Color of the label in hex format (e.g. #FF0000)"),
    )
    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="labels",
        help_text=_("Mailbox that owns this label"),
    )
    threads = models.ManyToManyField(
        "Thread",
        related_name="labels",
        help_text=_("Threads that have this label"),
        blank=True,
    )
    description = models.CharField(
        _("description"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Description of the label, used by AI to understand its purpose"),
    )
    is_auto = models.BooleanField(
        _("auto labeling"),
        default=False,
        help_text=_("Whether this label should be automatically applied by AI"),
    )

    class Meta:
        db_table = "messages_label"
        verbose_name = _("label")
        verbose_name_plural = _("labels")
        unique_together = ("slug", "mailbox")
        ordering = ["slug"]

    def __str__(self):
        return f"{self.name} ({self.mailbox})"

    def save(self, *args, **kwargs):
        """
        Ensure all parent labels exist before saving this label.
        Also handle renaming of parent labels by updating all children.
        """
        # Check if this is an update and the name is changing
        if self.pk and hasattr(self, "_state") and not self._state.adding:
            try:
                old_instance = Label.objects.get(pk=self.pk)
                old_name = old_instance.name
                new_name = self.name

                # If the name is changing
                if old_name != new_name:
                    # Find all child labels that start with the old name
                    child_labels = Label.objects.filter(
                        mailbox=self.mailbox, name__startswith=f"{old_name}/"
                    )

                    # Update all child labels to use the new parent name
                    for child in child_labels:
                        child.name = child.name.replace(
                            f"{old_name}/", f"{new_name}/", 1
                        )
                        child.slug = slugify(child.name.replace("/", "-"))
                        # Use update to avoid triggering save method again
                        Label.objects.filter(pk=child.pk).update(
                            name=child.name, slug=child.slug
                        )

                    # Clean up orphaned parent labels that are no longer referenced
                    self._cleanup_orphaned_parents(old_name)

            except Label.DoesNotExist:
                # This is a new instance, not an update
                pass

        # Create parent labels if they don't exist
        if self.name and self.mailbox:
            parts = self.name.split("/")
            current_path = []
            for part in parts[:-1]:  # Exclude the last part (the actual label)
                current_path.append(part)
                parent_name = "/".join(current_path)
                Label.objects.get_or_create(
                    name=parent_name,
                    mailbox=self.mailbox,
                    defaults={"color": self.color},
                )
        # Generate slug from name before saving
        if not self.slug:
            self.slug = slugify(self.name.replace("/", "-"))
        super().save(*args, **kwargs)

    def _cleanup_orphaned_parents(self, old_name):
        """Remove parent labels that are no longer referenced by any children."""
        # Get all parts of the old name
        old_parts = old_name.split("/")

        # Check each potential parent level
        for i in range(len(old_parts)):
            potential_parent = "/".join(old_parts[: i + 1])

            # Check if this parent is still referenced by any children
            has_children = Label.objects.filter(
                mailbox=self.mailbox, name__startswith=f"{potential_parent}/"
            ).exists()

            # If no children reference this parent, and it's not the current label being updated
            if not has_children and potential_parent != self.name:
                # Check if this parent label exists
                try:
                    orphaned_parent = Label.objects.get(
                        mailbox=self.mailbox, name=potential_parent
                    )
                    # Only delete if it's not the current label being updated
                    if orphaned_parent.pk != self.pk:
                        orphaned_parent.delete()
                except Label.DoesNotExist:
                    pass

    @property
    def parent_name(self):
        """Get the parent label name if this is a subfolder."""
        if "/" not in self.name:
            return None
        return self.name.rsplit("/", 1)[0]

    @property
    def basename(self):
        """Get the base name of the label without parent path."""
        return self.name.rsplit("/", maxsplit=1)[-1]

    @property
    def depth(self):
        """Get the depth of the label in the hierarchy."""
        return self.name.count("/")

    @classmethod
    def get_children(cls, mailbox, parent_name):
        """Get all direct children of a parent label."""
        if parent_name:
            prefix = f"{parent_name}/"
            # Get all labels that start with the parent prefix
            labels = cls.objects.filter(
                mailbox=mailbox,
                name__startswith=prefix,
            )
            # Filter to only get direct children (one level deeper)
            return [
                label for label in labels if label.depth == parent_name.count("/") + 1
            ]

        # Get root level labels (no slashes)
        return cls.objects.filter(
            mailbox=mailbox,
        ).exclude(name__contains="/")

    def get_display_name(self):
        """Return the display name of the label."""
        if "/" not in self.name:
            return self.name
        return self.name.rsplit("/", maxsplit=1)[-1]

    def delete(self, *args, **kwargs):
        """Delete this label and all its child labels (cascading deletion)."""
        # Find all child labels that start with this label's name followed by a slash
        child_labels = Label.objects.filter(
            mailbox=self.mailbox, name__startswith=f"{self.name}/"
        )

        # Delete all child labels first (to maintain referential integrity)
        child_count = child_labels.count()
        if child_count > 0:
            logger.info(
                "Deleting %d child labels for parent label '%s' (mailbox: %s)",
                child_count,
                self.name,
                self.mailbox,
            )
            child_labels.delete()

        # Delete the parent label
        logger.info("Deleting parent label '%s' (mailbox: %s)", self.name, self.mailbox)
        super().delete(*args, **kwargs)


class ThreadAccess(BaseModel):
    """Thread access model to store thread access information for a mailbox."""

    thread = models.ForeignKey(
        "Thread", on_delete=models.CASCADE, related_name="accesses"
    )
    mailbox = models.ForeignKey(
        "Mailbox", on_delete=models.CASCADE, related_name="thread_accesses"
    )
    role = models.SmallIntegerField(
        _("role"),
        choices=ThreadAccessRoleChoices.choices,
        default=ThreadAccessRoleChoices.VIEWER,
    )

    class Meta:
        db_table = "messages_threadaccess"
        verbose_name = _("thread access")
        verbose_name_plural = _("thread accesses")
        unique_together = ("thread", "mailbox")

    def __str__(self):
        return f"{self.thread} - {self.mailbox} - {self.role}"


class Contact(BaseModel):
    """Contact model to store contact information."""

    name = models.CharField(_("name"), max_length=255, null=True, blank=True)
    email = models.EmailField(_("email"))
    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="contacts",
    )

    class Meta:
        db_table = "messages_contact"
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")
        unique_together = ("email", "mailbox")

    def __str__(self):
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email

    def __repr__(self):
        return str(self)


class MessageRecipient(BaseModel):
    """Message recipient model to store message recipient information."""

    message = models.ForeignKey(
        "Message", on_delete=models.CASCADE, related_name="recipients"
    )
    contact = models.ForeignKey(
        "Contact", on_delete=models.CASCADE, related_name="messages"
    )
    type = models.SmallIntegerField(
        _("type"),
        choices=MessageRecipientTypeChoices.choices,
        default=MessageRecipientTypeChoices.TO,
    )

    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    delivery_status = models.SmallIntegerField(
        _("delivery status"),
        null=True,
        blank=True,
        choices=MessageDeliveryStatusChoices.choices,
    )
    delivery_message = models.TextField(_("delivery message"), null=True, blank=True)
    retry_count = models.IntegerField(_("retry count"), default=0)
    retry_at = models.DateTimeField(_("retry at"), null=True, blank=True)

    class Meta:
        db_table = "messages_messagerecipient"
        verbose_name = _("message recipient")
        verbose_name_plural = _("message recipients")
        unique_together = ("message", "contact", "type")

    def __str__(self):
        return f"{self.message} - {self.contact} - {self.get_type_display()}"


class Message(BaseModel):
    """Message model to store received and sent messages."""

    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="messages"
    )
    subject = models.CharField(_("subject"), max_length=255, null=True, blank=True)
    sender = models.ForeignKey("Contact", on_delete=models.CASCADE)
    parent = models.ForeignKey(
        "Message", on_delete=models.SET_NULL, null=True, blank=True
    )

    # Flags
    is_draft = models.BooleanField(_("is draft"), default=False)
    is_sender = models.BooleanField(_("is sender"), default=False)
    is_starred = models.BooleanField(_("is starred"), default=False)
    is_trashed = models.BooleanField(_("is trashed"), default=False)
    is_unread = models.BooleanField(_("is unread"), default=False)
    is_spam = models.BooleanField(_("is spam"), default=False)
    is_archived = models.BooleanField(_("is archived"), default=False)
    has_attachments = models.BooleanField(_("has attachments"), default=False)

    trashed_at = models.DateTimeField(_("trashed at"), null=True, blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)

    mime_id = models.CharField(_("mime id"), max_length=998, null=True, blank=True)

    # Stores the raw MIME message.
    blob = models.ForeignKey(
        "Blob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

    draft_blob = models.OneToOneField(
        "Blob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="draft",
    )

    # Internal cache for parsed data
    _parsed_email_cache: Optional[Dict[str, Any]] = None

    class Meta:
        db_table = "messages_message"
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ["-created_at"]

    def __str__(self):
        return str(self.subject) if self.subject else "(no subject)"

    def get_parsed_data(self) -> Dict[str, Any]:
        """Parse raw mime message using parser and cache the result."""
        if self._parsed_email_cache is not None:
            return self._parsed_email_cache

        if self.blob:
            self._parsed_email_cache = parse_email_message(self.blob.get_content())
        else:
            self._parsed_email_cache = {}
        return self._parsed_email_cache

    def get_parsed_field(self, field_name: str) -> Any:
        """Get a parsed field from the parsed email data."""
        return (self.get_parsed_data() or {}).get(field_name)

    def generate_mime_id(self) -> str:
        """Get the RFC5322 Message-ID of the message."""
        _id = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")
        return f"{_id}@_lst.{self.sender.email.split('@')[1]}"

    def get_all_recipient_contacts(self) -> Dict[str, List[Contact]]:
        """Get all recipients of the message."""
        recipients_by_type = {
            kind: [] for kind, _ in MessageRecipientTypeChoices.choices
        }
        for mr in self.recipients.select_related("contact").all():
            recipients_by_type[mr.type].append(mr.contact)
        return recipients_by_type

    def get_as_text(self) -> str:
        """Get the message as text, similar to the Message dataclass __str__ in utils.py."""
        # Date
        date_str = self.sent_at.isoformat() if self.sent_at else ""
        # Sender: "Name <email>" or just email
        sender = str(self.sender)
        # Recipients: list of "Name <email>" or just email
        to_contacts = self.recipients.filter(
            type=MessageRecipientTypeChoices.TO
        ).select_related("contact")
        recipients = [str(mr.contact) for mr in to_contacts]
        # CC
        cc_contacts = self.recipients.filter(
            type=MessageRecipientTypeChoices.CC
        ).select_related("contact")
        cc = [str(mr.contact) for mr in cc_contacts]
        # Subject
        subject = self.subject or _("No subject")
        # Body: try to get text/plain from parsed data
        body = ""
        parsed_data = self.get_parsed_data()
        for part in parsed_data.get("textBody", []):
            if part.get("type") == "text/plain":
                body = part.get("content", "")
                break
        # Message ID
        msg_id = str(self.id)
        return (
            f"{_('Message ID')}: {msg_id}\n"
            f"{_('From')}: {sender}\n"
            f"{_('To')}: {', '.join(recipients)}\n"
            f"{_('CC')}: {', '.join(cc)}\n"
            f"{_('Date')}: {date_str}\n"
            f"{_('Subject')}: {subject}\n\n"
            f"{_('Body')}: {body}"
        )

    def get_tokens_count(self) -> int:
        """Get the number of tokens in the message (subject + body)."""
        # Subject
        subject = self.subject or _("No subject")
        # Body: try to get text/plain from parsed data
        body = ""
        parsed_data = self.get_parsed_data()
        for part in parsed_data.get("textBody", []):
            if part.get("type") == "text/plain":
                body = part.get("content", "")
                break
        counted_text = f"{subject} {body}"
        return len(counted_text.split())


class Blob(BaseModel):
    """
    Blob model to store immutable binary data.

    This model follows the JMAP blob design, storing raw content that can
    be referenced by multiple attachments.

    This will be offloaded to object storage in the future.
    """

    sha256 = models.BinaryField(
        _("sha256 hash"),
        max_length=32,
        db_index=True,
        help_text=_("SHA-256 hash of the uncompressed blob content"),
    )

    size = models.PositiveIntegerField(
        _("file size"), help_text=_("Size of the blob in bytes")
    )

    content_type = models.CharField(
        _("content type"), max_length=127, help_text=_("MIME type of the blob")
    )

    compression = models.SmallIntegerField(
        _("compression"),
        choices=CompressionTypeChoices.choices,
        default=CompressionTypeChoices.NONE,
    )

    raw_content = models.BinaryField(
        _("raw content"),
        help_text=_("Compressed binary content of the blob"),
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="blobs",
        help_text=_("Mailbox that owns this blob"),
    )

    class Meta:
        db_table = "messages_blob"
        verbose_name = _("blob")
        verbose_name_plural = _("blobs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Blob {self.id} ({self.size} bytes)"

    def get_content(self) -> bytes:
        """
        Get the decompressed content of this blob.

        Returns:
            The decompressed content

        Raises:
            ValueError: If the blob compression type is not supported
        """
        if self.compression == CompressionTypeChoices.NONE:
            return self.raw_content
        if self.compression == CompressionTypeChoices.ZSTD:
            return pyzstd.decompress(self.raw_content)
        raise ValueError(f"Unsupported compression type: {self.compression}")


class Attachment(BaseModel):
    """Attachment model to link messages with blobs."""

    name = models.CharField(
        _("file name"),
        max_length=255,
        help_text=_("Original filename of the attachment"),
    )

    blob = models.ForeignKey(
        "Blob",
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text=_("Reference to the blob containing the attachment data"),
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text=_("Mailbox that owns this attachment"),
    )

    messages = models.ManyToManyField(
        "Message",
        related_name="attachments",
        help_text=_("Messages that use this attachment"),
    )

    class Meta:
        db_table = "messages_attachment"
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.blob.size} bytes)"

    @property
    def content_type(self):
        """Return the content type of the associated blob."""
        return self.blob.content_type

    @property
    def size(self) -> int:
        """Return the size of the associated blob."""
        return self.blob.size

    @property
    def sha256(self):
        """Return the SHA-256 hash of the associated blob."""
        return self.blob.sha256


class MailDomainAccess(BaseModel):
    """Mail domain access model to store mail domain access information for a user."""

    maildomain = models.ForeignKey(
        "MailDomain", on_delete=models.CASCADE, related_name="accesses"
    )
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="maildomain_accesses"
    )
    role = models.SmallIntegerField(
        _("role"),
        choices=MailDomainAccessRoleChoices.choices,
        default=MailDomainAccessRoleChoices.ADMIN,
    )

    class Meta:
        db_table = "messages_maildomainaccess"
        verbose_name = _("mail domain access")
        verbose_name_plural = _("mail domain accesses")
        unique_together = ("maildomain", "user")

    def __str__(self):
        return f"Access to {self.maildomain} for {self.user} with {self.role} role"


class DKIMKey(BaseModel):
    """DKIM Key model to store DKIM signing keys with encrypted private key storage."""

    selector = models.CharField(
        _("selector"),
        max_length=255,
        help_text=_("DKIM selector (e.g., 'default', 'mail')"),
    )

    private_key = EncryptedTextField(
        _("private key"),
        help_text=_("DKIM private key in PEM format (encrypted)"),
    )

    public_key = models.TextField(
        _("public key"),
        help_text=_("DKIM public key for DNS record generation"),
    )

    algorithm = models.SmallIntegerField(
        _("algorithm"),
        choices=DKIMAlgorithmChoices.choices,
        default=DKIMAlgorithmChoices.RSA,
        help_text=_("DKIM signing algorithm"),
    )

    key_size = models.PositiveIntegerField(
        _("key size"),
        help_text=_("Key size in bits (e.g., 2048, 4096 for RSA)"),
    )

    is_active = models.BooleanField(
        _("is active"),
        default=True,
        help_text=_("Whether this DKIM key is active and should be used for signing"),
    )

    domain = models.ForeignKey(
        "MailDomain",
        on_delete=models.CASCADE,
        related_name="dkim_keys",
        help_text=_("Domain that owns this DKIM key"),
    )

    class Meta:
        db_table = "messages_dkimkey"
        verbose_name = _("DKIM key")
        verbose_name_plural = _("DKIM keys")
        ordering = ["-created_at"]  # Most recent first for picking latest active key

    def __str__(self):
        return f"DKIM Key {self.selector} ({self.algorithm}) - {self.domain}"

    def get_private_key_bytes(self) -> bytes:
        """Get the private key as bytes."""
        return self.private_key.encode("utf-8")

    def get_dns_record_value(self) -> str:
        """Get the DNS TXT record value for this DKIM key."""
        algorithm_enum = DKIMAlgorithmChoices(self.algorithm)
        return f"v=DKIM1; k={algorithm_enum.label}; p={self.public_key}"
