"""
Declare and configure the models for the messages core application
"""
# pylint: disable=too-many-lines,too-many-instance-attributes

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime as dt
from datetime import time, timedelta
from functools import cached_property
from logging import getLogger
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.contrib.auth.base_user import AbstractBaseUser
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, Exists, F, Q, Value, When
from django.db.models.fields import BooleanField
from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify

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
    MessageTemplateTypeChoices,
    ThreadAccessRoleChoices,
    ThreadEventTypeChoices,
    UserAbilities,
)
from core.mda.rfc5322 import EmailParseError, parse_email_message
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
        verbose_name="id",
        help_text="primary key for the record as UUID",
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(
        verbose_name="created on",
        help_text="date and time at which a record was created",
        auto_now_add=True,
        editable=False,
    )
    updated_at = models.DateTimeField(
        verbose_name="updated on",
        help_text="date and time at which a record was last updated",
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
                    "We couldn't find a user with this sub but the email is already "
                    "associated with a registered user."
                ) from err
        return None


class User(AbstractBaseUser, BaseModel, auth_models.PermissionsMixin):
    """User model to work with OIDC only authentication."""

    sub_validator = validators.RegexValidator(
        regex=r"^[\w.@+-:]+\Z",
        message=(
            "Enter a valid sub. This value may contain only letters, "
            "numbers, and @/./+/-/_/: characters."
        ),
    )

    sub = models.CharField(
        "sub",
        help_text=(
            "Required. 255 characters or fewer. Letters, numbers, and @/./+/-/_/: characters only."
        ),
        max_length=255,
        unique=True,
        validators=[sub_validator],
        blank=True,
        null=True,
    )

    full_name = models.CharField("full name", max_length=255, null=True, blank=True)

    email = models.EmailField("identity email address", blank=True, null=True)

    # Unlike the "email" field which stores the email coming from the OIDC token, this field
    # stores the email used by staff users to login to the admin site
    admin_email = models.EmailField(
        "admin email address", unique=True, blank=True, null=True
    )

    language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        verbose_name="language",
        help_text="The language in which the user wants to see the interface.",
    )
    timezone = TimeZoneField(
        choices_display="WITH_GMT_OFFSET",
        use_pytz=False,
        default=settings.TIME_ZONE,
        help_text="The timezone in which the user wants to see times.",
    )
    is_staff = models.BooleanField(
        "staff status",
        default=False,
        help_text="Whether the user can log into this admin site.",
    )
    is_active = models.BooleanField(
        "active",
        default=True,
        help_text=(
            "Whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )

    custom_attributes = models.JSONField(
        "Custom attributes",
        default=dict,
        blank=True,
        help_text="Metadata to sync to the user in the identity provider.",
    )

    objects = UserManager()

    USERNAME_FIELD = "admin_email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "messages_user"
        verbose_name = "user"
        verbose_name_plural = "users"

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
            UserAbilities.CAN_MANAGE_MAILDOMAIN_ACCESSES: is_admin,
        }


class MailDomain(BaseModel):
    """Mail domain model to store mail domain information."""

    name_validator = validators.RegexValidator(
        regex=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
        message=(
            "Enter a valid domain name. This value may contain only lowercase "
            "letters, numbers, dots and - characters."
        ),
    )

    name = models.CharField(
        "name", max_length=253, unique=True, validators=[name_validator]
    )

    alias_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True
    )

    oidc_autojoin = models.BooleanField(
        "oidc autojoin",
        default=False,
        help_text="Create mailboxes automatically based on OIDC emails.",
    )

    identity_sync = models.BooleanField(
        "Identity sync",
        default=False,
        help_text="Sync mailboxes to an identity provider.",
    )

    custom_settings = models.JSONField(
        "Custom settings",
        default=dict,
        blank=True,
        help_text="Custom settings for the mail domain.",
    )

    custom_attributes = models.JSONField(
        "Custom attributes",
        default=dict,
        blank=True,
        help_text=(
            "Metadata to sync to the maildomain group in the identity provider."
        ),
    )

    class Meta:
        db_table = "messages_maildomain"
        verbose_name = "mail domain"
        verbose_name_plural = "mail domains"

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

    def get_spam_config(self) -> Dict[str, Any]:
        """Get spam configuration for this mail domain.

        Returns a merged configuration dict that combines global SPAM_CONFIG settings
        with domain-specific overrides from custom_settings. Domain-specific settings
        override global settings on a key-by-key basis.

        Returns:
            Dict containing spam configuration (e.g., {"rspamd_url": "...", "rspamd_auth": "..."})
        """
        spam_config = settings.SPAM_CONFIG.copy()
        if self.custom_settings and "SPAM_CONFIG" in self.custom_settings:
            # Override with maildomain-specific config (key by key)
            domain_spam_config = self.custom_settings.get("SPAM_CONFIG", {})
            spam_config.update(domain_spam_config)
        return spam_config

    def get_expected_dns_records(self) -> List[str]:
        """Get the list of DNS records we expect to be present for this domain."""

        technical_domain = settings.MESSAGES_TECHNICAL_DOMAIN
        raw = settings.MESSAGES_DNS_RECORDS.replace(
            "{technical_domain}", technical_domain
        )
        records = json.loads(raw)

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


class Channel(BaseModel):
    """Channel model to store channel information for receiving messages from various sources."""

    name = models.CharField(
        "name", max_length=255, help_text="Human-readable name for this channel"
    )

    type = models.CharField(
        "type", max_length=255, help_text="Type of channel", default="mta"
    )

    settings = models.JSONField(
        "settings",
        default=dict,
        blank=True,
        help_text="Channel-specific configuration settings",
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="channels",
        help_text="Mailbox that receives messages from this channel",
    )

    maildomain = models.ForeignKey(
        "MailDomain",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="channels",
        help_text="Mail domain that owns this channel",
    )

    class Meta:
        db_table = "messages_channel"
        verbose_name = "channel"
        verbose_name_plural = "channels"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(mailbox__isnull=False) ^ models.Q(maildomain__isnull=False)
                ),
                name="channel_has_target",
            ),
        ]

    def __str__(self):
        return self.name


class Mailbox(BaseModel):
    """Mailbox model to store mailbox information."""

    local_part = models.CharField(
        "local part",
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
        "is identity",
        default=True,
        help_text=(
            "Whether this mailbox identifies a person (i.e. is not an alias or a group)"
        ),
    )

    alias_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        db_table = "messages_mailbox"
        verbose_name = "mailbox"
        verbose_name_plural = "mailboxes"
        unique_together = ("local_part", "domain")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.local_part}@{self.domain.name}"

    @property
    def can_reset_password(self) -> bool:
        """Return True if the mailbox user's password can be reset."""
        return (
            self.is_identity
            and settings.IDENTITY_PROVIDER == "keycloak"
            and self.domain.identity_sync
        )

    def reset_password(self):
        """Reset the mailbox user's password."""
        if self.can_reset_password is False:
            return None

        email = str(self)
        # Local import to avoid circular dependency with identity services
        from core.services.identity.keycloak import (  # pylint: disable=import-outside-toplevel
            reset_keycloak_user_password,
        )

        return reset_keycloak_user_password(email)

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

        return Blob.objects.create_blob(
            content=content,
            content_type=content_type,
            compression=compression,
            mailbox=self,
        )

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
                "manage_message_templates": False,
                "import_messages": False,
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
            MailboxAbilities.CAN_MANAGE_MESSAGE_TEMPLATES: (
                is_admin and settings.FEATURE_MESSAGE_TEMPLATES
            ),
            MailboxAbilities.CAN_IMPORT_MESSAGES: (
                is_admin and settings.FEATURE_IMPORT_MESSAGES
            ),
        }

    def get_validated_signature(self, signature_id: str):
        """Helper method to validate and retrieve a signature template.

        Args:
            signature_id: ID of the signature template

        Returns:
            MessageTemplate if valid and accessible, None otherwise
        """
        # Check for forced signature with domain having priority over mailbox
        forced_signature = (
            MessageTemplate.objects.filter(
                Q(maildomain=self.domain) | Q(mailbox=self),
                type=MessageTemplateTypeChoices.SIGNATURE,
                is_forced=True,
                is_active=True,
            )
            .order_by(
                # Domain signatures first (maildomain_id not null), then mailbox signatures
                Case(
                    When(maildomain__isnull=False, then=0),
                    default=1,
                )
            )
            .first()
        )

        signature = forced_signature if forced_signature else None
        if not signature and not signature_id:
            return None

        if not signature and signature_id:
            try:
                signature = MessageTemplate.objects.get(
                    id=signature_id,
                    type=MessageTemplateTypeChoices.SIGNATURE,
                    is_active=True,
                )
            except MessageTemplate.DoesNotExist:
                logger.error("Signature template not found with id: %s", signature_id)
                return None

            # Verify signature is in sender scope
            in_sender_scope = (
                signature.mailbox_id and signature.mailbox_id == self.id
            ) or (signature.maildomain_id and signature.maildomain_id == self.domain_id)
            if not in_sender_scope:
                logger.warning(
                    "Signature %s cannot be used in mailbox %s",
                    signature.id,
                    str(self),
                )
                return None

        return signature


class MailboxAccess(BaseModel):
    """Mailbox access model to store mailbox access information."""

    mailbox = models.ForeignKey(
        "Mailbox", on_delete=models.CASCADE, related_name="accesses"
    )
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="mailbox_accesses"
    )
    role = models.SmallIntegerField(
        "role",
        choices=MailboxRoleChoices.choices,
        default=MailboxRoleChoices.VIEWER,
    )

    accessed_at = models.DateTimeField(
        "accessed at", null=True, blank=True, db_index=True
    )

    class Meta:
        db_table = "messages_mailboxaccess"
        verbose_name = "mailbox access"
        verbose_name_plural = "mailbox accesses"
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

    subject = models.CharField("subject", max_length=255, null=True, blank=True)
    snippet = models.TextField("snippet", blank=True)
    has_trashed = models.BooleanField("has trashed", default=False)
    is_trashed = models.BooleanField(
        "is trashed",
        default=False,
        help_text="Whether all messages in the thread are trashed",
    )
    has_archived = models.BooleanField("has archived", default=False)
    has_draft = models.BooleanField("has draft", default=False)
    has_sender = models.BooleanField("has sender", default=False)
    has_messages = models.BooleanField("has messages", default=True)
    has_attachments = models.BooleanField("has attachments", default=False)
    is_spam = models.BooleanField("is spam", default=False)
    has_active = models.BooleanField("has active", default=False)
    has_delivery_pending = models.BooleanField(
        "has delivery pending",
        default=False,
        help_text=(
            "True if thread has messages awaiting successful delivery "
            "(sending, retrying, or failed)."
        ),
    )
    has_delivery_failed = models.BooleanField(
        "has delivery failed",
        default=False,
        help_text="True if thread has messages with permanent delivery failure.",
    )
    messaged_at = models.DateTimeField(
        "messaged at", null=True, blank=True, db_index=True
    )
    active_messaged_at = models.DateTimeField(
        "active messaged at",
        null=True,
        blank=True,
        db_index=True,
        help_text="Date of the last active (received, not spam/archived/trashed/draft) message.",
    )
    trashed_messaged_at = models.DateTimeField(
        "trashed messaged at",
        null=True,
        blank=True,
        db_index=True,
        help_text="Date of the last trashed message.",
    )
    draft_messaged_at = models.DateTimeField(
        "draft messaged at",
        null=True,
        blank=True,
        db_index=True,
        help_text="Date of the last draft (non-trashed) message.",
    )
    sender_messaged_at = models.DateTimeField(
        "sender messaged at",
        null=True,
        blank=True,
        db_index=True,
        help_text="Date of the last sent (non-trashed, non-draft) message.",
    )
    archived_messaged_at = models.DateTimeField(
        "archived messaged at",
        null=True,
        blank=True,
        db_index=True,
        help_text="Date of the last archived (non-trashed) message.",
    )
    sender_names = models.JSONField("sender names", null=True, blank=True)
    summary = models.TextField("summary", null=True, blank=True, default=None)

    class Meta:
        db_table = "messages_thread"
        verbose_name = "thread"
        verbose_name_plural = "threads"

    def __str__(self):
        return str(self.subject) if self.subject else "(no subject)"

    def update_stats(self):
        """Update the denormalized stats of the thread."""
        # Fetch all message metadata in a single query to avoid multiple DB hits
        message_data = list(
            self.messages.select_related("sender")
            .values(
                "is_trashed",
                "is_draft",
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
            self.has_trashed = False
            self.is_trashed = False
            self.has_archived = False
            self.has_draft = False
            self.has_sender = False
            self.has_messages = False
            self.has_attachments = False
            self.is_spam = False
            self.has_active = False
            self.messaged_at = None
            self.active_messaged_at = None
            self.trashed_messaged_at = None
            self.draft_messaged_at = None
            self.sender_messaged_at = None
            self.archived_messaged_at = None
            self.sender_names = None
            self.has_delivery_pending = False
            self.has_delivery_failed = False
        else:
            # Compute stats in Python
            self.has_trashed = any(msg["is_trashed"] for msg in message_data)
            self.is_trashed = all(msg["is_trashed"] for msg in message_data)
            self.has_archived = any(msg["is_archived"] for msg in message_data)
            self.has_draft = any(
                msg["is_draft"] and not msg["is_trashed"] for msg in message_data
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

            # Fetch recipient delivery statuses for outbox fields
            recipient_statuses = list(
                MessageRecipient.objects.filter(
                    message__thread=self,
                    message__is_sender=True,
                    message__is_draft=False,
                    message__is_trashed=False,
                ).values_list("delivery_status", flat=True)
            )

            self.has_delivery_failed = any(
                s == MessageDeliveryStatusChoices.FAILED for s in recipient_statuses
            )
            # Consider as pending if sending, retrying OR FAILED
            self.has_delivery_pending = any(
                s
                in [
                    None,
                    MessageDeliveryStatusChoices.RETRY,
                    MessageDeliveryStatusChoices.FAILED,
                ]
                for s in recipient_statuses
            )

            # Set messaged_at to the creation time of the most recent
            # non-draft and non-trashed message.
            visible_messages = [
                msg
                for msg in message_data
                if not msg["is_draft"] and not msg["is_trashed"]
            ]
            if visible_messages:
                self.messaged_at = max(msg["created_at"] for msg in visible_messages)
            else:
                self.messaged_at = None

            # Compute per-view messaged_at timestamps
            active_msgs = [
                msg
                for msg in message_data
                if not msg["is_sender"]
                and not msg["is_spam"]
                and not msg["is_archived"]
                and not msg["is_trashed"]
                and not msg["is_draft"]
            ]
            self.active_messaged_at = (
                max(msg["created_at"] for msg in active_msgs) if active_msgs else None
            )

            trashed_msgs = [msg for msg in message_data if msg["is_trashed"]]
            self.trashed_messaged_at = (
                max(msg["created_at"] for msg in trashed_msgs) if trashed_msgs else None
            )

            draft_msgs = [
                msg for msg in message_data if msg["is_draft"] and not msg["is_trashed"]
            ]
            self.draft_messaged_at = (
                max(msg["created_at"] for msg in draft_msgs) if draft_msgs else None
            )

            sender_msgs = [
                msg
                for msg in message_data
                if msg["is_sender"] and not msg["is_trashed"] and not msg["is_draft"]
            ]
            self.sender_messaged_at = (
                max(msg["created_at"] for msg in sender_msgs) if sender_msgs else None
            )

            archived_msgs = [
                msg
                for msg in message_data
                if msg["is_archived"] and not msg["is_trashed"]
            ]
            self.archived_messaged_at = (
                max(msg["created_at"] for msg in archived_msgs)
                if archived_msgs
                else None
            )

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
                "has_trashed",
                "is_trashed",
                "has_archived",
                "has_draft",
                "has_sender",
                "has_messages",
                "has_attachments",
                "is_spam",
                "has_active",
                "has_delivery_pending",
                "has_delivery_failed",
                "messaged_at",
                "active_messaged_at",
                "trashed_messaged_at",
                "draft_messaged_at",
                "sender_messaged_at",
                "archived_messaged_at",
                "sender_names",
            ]
        )


class Label(BaseModel):
    """Label model to organize threads into folders using slash-based naming."""

    name = models.CharField(
        "name",
        max_length=255,
        help_text=(
            "Name of the label/folder (can use slashes for hierarchy, e.g. 'Work/Projects')"
        ),
    )
    slug = models.SlugField(
        "slug",
        max_length=255,
        help_text="URL-friendly version of the name",
    )
    color = models.CharField(
        "color",
        max_length=7,
        default="#E3E3FD",
        help_text="Color of the label in hex format (e.g. #FF0000)",
    )
    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="labels",
        help_text="Mailbox that owns this label",
    )
    threads = models.ManyToManyField(
        "Thread",
        related_name="labels",
        help_text="Threads that have this label",
        blank=True,
    )
    description = models.CharField(
        "description",
        max_length=255,
        blank=True,
        default="",
        help_text="Description of the label, used by AI to understand its purpose",
    )
    is_auto = models.BooleanField(
        "auto labeling",
        default=False,
        help_text="Whether this label should be automatically applied by AI",
    )

    class Meta:
        db_table = "messages_label"
        verbose_name = "label"
        verbose_name_plural = "labels"
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
        "role",
        choices=ThreadAccessRoleChoices.choices,
        default=ThreadAccessRoleChoices.VIEWER,
    )
    read_at = models.DateTimeField("read at", null=True, blank=True)
    starred_at = models.DateTimeField("starred at", null=True, blank=True)

    class Meta:
        db_table = "messages_threadaccess"
        verbose_name = "thread access"
        verbose_name_plural = "thread accesses"
        unique_together = ("thread", "mailbox")

    def __str__(self):
        return f"{self.thread} - {self.mailbox} - {self.role}"

    @staticmethod
    def thread_unread_filter(user, mailbox_id=None):
        """Return an expression to annotate `_has_unread` on a Thread queryset.

        Intended for `ThreadViewSet.get_queryset()`. Uses `messaged_at`
        (last non-trashed, non-draft message) so the unread filter works
        across all views (inbox, sent, archive, trash).

        When `mailbox_id` is provided, scopes to that single mailbox (Case expression).
        Otherwise, checks across all the user's mailboxes (Exists subquery).
        """
        if mailbox_id:
            return Case(
                When(
                    accesses__mailbox_id=mailbox_id,
                    accesses__read_at__isnull=True,
                    messaged_at__isnull=False,
                    then=Value(True),
                ),
                When(
                    accesses__mailbox_id=mailbox_id,
                    accesses__read_at__isnull=False,
                    messaged_at__gt=F("accesses__read_at"),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            )
        return Exists(
            ThreadAccess.objects.filter(
                thread=models.OuterRef("pk"),
                mailbox__accesses__user=user,
            ).filter(
                Q(read_at__isnull=True, thread__messaged_at__isnull=False)
                | Q(read_at__lt=F("thread__messaged_at"))
            )
        )

    @staticmethod
    def unread_filter():
        """
        Return a `Q` for filtering a `ThreadAccess` queryset to unread entries.
        """
        return Q(read_at__isnull=True, thread__messaged_at__isnull=False) | Q(
            read_at__lt=F("thread__messaged_at")
        )

    @staticmethod
    def thread_starred_filter(user, mailbox_id=None):
        """Return an expression to annotate `_has_starred` on a Thread queryset.

        Intended for `ThreadViewSet.get_queryset()`. A thread is starred when
        the corresponding ThreadAccess has a non-null `starred_at`.

        When `mailbox_id` is provided, scopes to that single mailbox (Case expression).
        Otherwise, checks across all the user's mailboxes (Exists subquery).
        """
        if mailbox_id:
            return Case(
                When(
                    accesses__mailbox_id=mailbox_id,
                    accesses__starred_at__isnull=False,
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            )
        return Exists(
            ThreadAccess.objects.filter(
                thread=models.OuterRef("pk"),
                mailbox__accesses__user=user,
                starred_at__isnull=False,
            )
        )

    @staticmethod
    def starred_filter():
        """Return a `Q` for filtering a `ThreadAccess` queryset to starred entries.

        Used by `_compute_unread_starred_from_accesses()` in the search index.
        """
        return Q(starred_at__isnull=False)


class ThreadEvent(BaseModel):
    """Thread event model to store events in a thread timeline (internal comments, notifications, etc.)."""

    DATA_SCHEMAS = {
        ThreadEventTypeChoices.IM: {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                },
                "mentions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "name": {"type": "string"},
                        },
                        "required": ["id", "name"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    }

    thread = models.ForeignKey(
        "Thread", on_delete=models.CASCADE, related_name="events"
    )
    type = models.CharField(
        "type",
        max_length=36,
        choices=ThreadEventTypeChoices.choices,
    )
    channel = models.ForeignKey(
        "Channel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="thread_events",
    )
    message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="thread_events",
    )
    author = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="thread_events",
    )
    data = models.JSONField("data", default=dict, blank=True)

    class Meta:
        db_table = "messages_threadevent"
        verbose_name = "thread event"
        verbose_name_plural = "thread events"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["thread", "created_at"]),
        ]

    def __str__(self):
        return f"{self.thread} - {self.type} - {self.created_at}"

    def clean(self):
        """Validate the data field against the schema for this event type."""
        schema = self.DATA_SCHEMAS.get(self.type)
        if schema:
            try:
                jsonschema.validate(self.data, schema)
            except jsonschema.ValidationError as exception:
                raise ValidationError({"data": exception.message}) from exception
        super().clean()


class Contact(BaseModel):
    """Contact model to store contact information."""

    name = models.CharField("name", max_length=255, null=True, blank=True)
    email = models.EmailField("email")
    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="contacts",
    )

    class Meta:
        db_table = "messages_contact"
        verbose_name = "contact"
        verbose_name_plural = "contacts"
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
        "type",
        choices=MessageRecipientTypeChoices.choices,
        default=MessageRecipientTypeChoices.TO,
    )

    delivered_at = models.DateTimeField("delivered at", null=True, blank=True)
    delivery_status = models.SmallIntegerField(
        "delivery status",
        null=True,
        blank=True,
        choices=MessageDeliveryStatusChoices.choices,
    )
    delivery_message = models.TextField("delivery message", null=True, blank=True)
    retry_count = models.IntegerField("retry count", default=0)
    retry_at = models.DateTimeField("retry at", null=True, blank=True)

    class Meta:
        db_table = "messages_messagerecipient"
        verbose_name = "message recipient"
        verbose_name_plural = "message recipients"
        unique_together = ("message", "contact", "type")

    def __str__(self):
        return f"{self.message} - {self.contact} - {self.get_type_display()}"


class MessageQuerySet(models.QuerySet):
    """Custom QuerySet for Message with read-state annotation."""

    def with_read_state(self, mailbox_id):
        """Annotate each message with ``_is_unread`` relative to a mailbox.

        The annotation compares the message ``created_at`` to the
        ``ThreadAccess.read_at`` for the given mailbox:
        - ``read_at IS NULL``          → True  (never read)
        - ``created_at > read_at``     → True  (new message since last read)
        - otherwise                    → False
        """
        access_qs = ThreadAccess.objects.filter(
            thread_id=models.OuterRef("thread_id"),
            mailbox_id=mailbox_id,
        )
        return self.annotate(
            _access_read_at=models.Subquery(access_qs.values("read_at")[:1]),
        ).annotate(
            _is_unread=Case(
                When(_access_read_at__isnull=True, then=Value(True)),
                When(created_at__gt=F("_access_read_at"), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )


MessageManager = models.Manager.from_queryset(MessageQuerySet)


class Message(BaseModel):
    """Message model to store received and sent messages."""

    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="messages"
    )
    subject = models.CharField("subject", max_length=255, null=True, blank=True)
    sender = models.ForeignKey("Contact", on_delete=models.CASCADE)
    sender_user = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )
    parent = models.ForeignKey(
        "Message", on_delete=models.SET_NULL, null=True, blank=True
    )

    # Flags
    is_draft = models.BooleanField("is draft", default=False)
    is_sender = models.BooleanField("is sender", default=False)
    is_trashed = models.BooleanField("is trashed", default=False)
    is_spam = models.BooleanField("is spam", default=False)
    is_archived = models.BooleanField("is archived", default=False)
    has_attachments = models.BooleanField("has attachments", default=False)

    trashed_at = models.DateTimeField("trashed at", null=True, blank=True)
    sent_at = models.DateTimeField("sent at", null=True, blank=True)
    archived_at = models.DateTimeField("archived at", null=True, blank=True)

    mime_id = models.CharField("mime id", max_length=998, null=True, blank=True)

    channel = models.ForeignKey(
        "Channel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

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
    signature = models.ForeignKey(
        "MessageTemplate",
        help_text="Signature template for the message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

    objects = MessageManager()

    # Internal cache for parsed data
    _parsed_email_cache: Optional[Dict[str, Any]] = None

    class Meta:
        db_table = "messages_message"
        verbose_name = "message"
        verbose_name_plural = "messages"
        ordering = ["-created_at"]

    def __str__(self):
        return str(self.subject) if self.subject else "(no subject)"

    def get_parsed_data(self) -> Dict[str, Any]:
        """Parse raw mime message using parser and cache the result."""
        if self._parsed_email_cache is not None:
            return self._parsed_email_cache

        if self.blob:
            try:
                self._parsed_email_cache = parse_email_message(self.blob.get_content())
            except EmailParseError:
                logger.warning(
                    "Failed to parse email for message %s, returning empty data",
                    self.id,
                )
                self._parsed_email_cache = {}
        else:
            self._parsed_email_cache = {}
        return self._parsed_email_cache

    def get_parsed_field(self, field_name: str) -> Any:
        """Get a parsed field from the parsed email data."""
        return (self.get_parsed_data() or {}).get(field_name)

    def get_mime_headers(self) -> Dict[str, str]:
        """Get the MIME headers of the message."""
        return self.get_parsed_data().get("headers", {})

    def get_stmsg_headers(self) -> Dict[str, str]:
        """Get the STMSG headers of the message."""
        return {
            k[len("x-stmsg-") :].lower(): v
            for k, v in self.get_parsed_data().get("headers", {}).items()
            if k.startswith("x-stmsg-")
        }

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
        subject = self.subject or "No subject"
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
            f"Message ID: {msg_id}\n"
            f"From: {sender}\n"
            f"To: {', '.join(recipients)}\n"
            f"CC: {', '.join(cc)}\n"
            f"Date: {date_str}\n"
            f"Subject: {subject}\n\n"
            f"Body: {body}"
        )

    def get_tokens_count(self) -> int:
        """Get the number of tokens in the message (subject + body)."""
        # Subject
        subject = self.subject or "No subject"
        # Body: try to get text/plain from parsed data
        body = ""
        parsed_data = self.get_parsed_data()
        for part in parsed_data.get("textBody", []):
            if part.get("type") == "text/plain":
                body = part.get("content", "")
                break
        counted_text = f"{subject} {body}"
        return len(counted_text.split())


class InboundMessage(BaseModel):
    """Temporary queue model for inbound messages waiting to be processed by spam filter."""

    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="inbound_messages",
    )
    raw_data = models.BinaryField("raw data", help_text="Raw email message bytes")
    channel = models.ForeignKey(
        "Channel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_messages",
    )
    error_message = models.TextField(
        "error message",
        blank=True,
        help_text="Error message if processing failed",
    )

    class Meta:
        db_table = "messages_inboundmessage"
        verbose_name = "inbound message"
        verbose_name_plural = "inbound messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"InboundMessage {self.id} - {self.mailbox}"


class BlobManager(models.Manager):
    """Custom Manager for Blob model."""

    def create_blob(
        self,
        content: bytes,
        content_type: str,
        compression: Optional[CompressionTypeChoices] = CompressionTypeChoices.ZSTD,
        **kwargs,
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
            ValidationError: If content is empty or compression is unsupported
        """
        if not content:
            raise ValidationError({"content": "Content cannot be empty"})

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
            raise ValidationError(
                {"compression": f"Unsupported compression type: {compression}"}
            )

        # Create the blob
        blob = Blob.objects.create(
            sha256=sha256_hash,
            size=original_size,
            content_type=content_type,
            compression=compression,
            raw_content=compressed_content,
            **kwargs,
        )

        logger.debug(
            "Created blob %s: %d bytes, %s compression, %s content type",
            blob.id,
            original_size,
            compression.label,
            content_type,
        )

        return blob


class Blob(BaseModel):
    """
    Blob model to store immutable binary data.

    This model follows the JMAP blob design, storing raw content that can
    be referenced by multiple attachments.

    This will be offloaded to object storage in the future.
    """

    sha256 = models.BinaryField(
        "sha256 hash",
        max_length=32,
        db_index=True,
        help_text="SHA-256 hash of the uncompressed blob content",
    )

    size = models.PositiveIntegerField(
        "file size", help_text="Size of the blob in bytes"
    )

    size_compressed = models.PositiveIntegerField(
        "compressed size", help_text="Size of the compressed blob in bytes"
    )

    content_type = models.CharField(
        "content type", max_length=127, help_text="MIME type of the blob"
    )

    compression = models.SmallIntegerField(
        "compression",
        choices=CompressionTypeChoices.choices,
        default=CompressionTypeChoices.NONE,
    )

    raw_content = models.BinaryField(
        "raw content",
        help_text="Compressed binary content of the blob",
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blobs",
        help_text="Mailbox that owns this blob",
    )
    maildomain = models.ForeignKey(
        "MailDomain",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blobs",
        help_text="Mail domain that owns this blob",
    )

    objects = BlobManager()

    class Meta:
        db_table = "messages_blob"
        verbose_name = "blob"
        verbose_name_plural = "blobs"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(mailbox__isnull=False) | models.Q(maildomain__isnull=False)
                ),
                name="blob_has_owner",
            ),
        ]

    def __str__(self):
        return f"Blob {self.id} ({self.size} bytes)"

    def save(self, *args, **kwargs):
        """Compute size_compressed and save the blob."""
        self.size_compressed = len(self.raw_content)
        super().save(*args, **kwargs)

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
        "file name",
        max_length=255,
        help_text="Original filename of the attachment",
    )

    blob = models.ForeignKey(
        "Blob",
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Reference to the blob containing the attachment data",
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Mailbox that owns this attachment",
    )

    messages = models.ManyToManyField(
        "Message",
        related_name="attachments",
        help_text="Messages that use this attachment",
    )

    cid = models.CharField(
        "content ID",
        max_length=255,
        blank=True,
        null=True,
        help_text="Content-ID for inline images",
    )

    class Meta:
        db_table = "messages_attachment"
        verbose_name = "attachment"
        verbose_name_plural = "attachments"
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
        "role",
        choices=MailDomainAccessRoleChoices.choices,
        default=MailDomainAccessRoleChoices.ADMIN,
    )

    class Meta:
        db_table = "messages_maildomainaccess"
        verbose_name = "mail domain access"
        verbose_name_plural = "mail domain accesses"
        unique_together = ("maildomain", "user")

    def __str__(self):
        return f"Access to {self.maildomain} for {self.user} with {self.role} role"


class DKIMKey(BaseModel):
    """DKIM Key model to store DKIM signing keys with encrypted private key storage."""

    selector = models.CharField(
        "selector",
        max_length=255,
        help_text="DKIM selector (e.g., 'default', 'mail')",
    )

    private_key = EncryptedTextField(
        "private key",
        help_text="DKIM private key in PEM format (encrypted)",
    )

    public_key = models.TextField(
        "public key",
        help_text="DKIM public key for DNS record generation",
    )

    algorithm = models.SmallIntegerField(
        "algorithm",
        choices=DKIMAlgorithmChoices.choices,
        default=DKIMAlgorithmChoices.RSA,
        help_text="DKIM signing algorithm",
    )

    key_size = models.PositiveIntegerField(
        "key size",
        help_text="Key size in bits (e.g., 2048, 4096 for RSA)",
    )

    is_active = models.BooleanField(
        "is active",
        default=True,
        help_text="Whether this DKIM key is active and should be used for signing",
    )

    domain = models.ForeignKey(
        "MailDomain",
        on_delete=models.CASCADE,
        related_name="dkim_keys",
        help_text="Domain that owns this DKIM key",
    )

    class Meta:
        db_table = "messages_dkimkey"
        verbose_name = "DKIM key"
        verbose_name_plural = "DKIM keys"
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


class MessageTemplate(BaseModel):
    """Message template model to store reusable message templates and signatures."""

    name = models.CharField(
        "name",
        max_length=255,
        help_text=(
            "Name of the template (e.g., 'Standard Reply', 'Out of Office', 'Work Signature')"
        ),
    )

    blob = models.ForeignKey(
        "Blob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_templates",
        help_text=(
            "Reference to the blob containing template content as JSON: {html: str, text: str, raw: any}"
        ),
    )

    type = models.SmallIntegerField(
        "type",
        choices=MessageTemplateTypeChoices.choices,
        default=MessageTemplateTypeChoices.MESSAGE,
        help_text="Type of template (message, signature, autoreply)",
    )

    is_active = models.BooleanField(
        "is active",
        default=True,
        help_text="Whether this template is available for use",
    )

    maildomain = models.ForeignKey(
        "MailDomain",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="message_templates",
        help_text="Mail domain that can use this template",
    )

    mailbox = models.ForeignKey(
        "Mailbox",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="message_templates",
        help_text="Mailbox that can use this template",
    )

    is_forced = models.BooleanField(
        "is forced",
        default=False,
        help_text=(
            "Whether this template is forced; no other template of the same type can be used in the same scope"
        ),
    )

    is_default = models.BooleanField(
        "is default",
        default=False,
        help_text=(
            "Whether this template is the default; it will be automatically loaded when composing a new message"
        ),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extra configuration (used for autoreply schedule, etc.)",
    )

    signature = models.ForeignKey(
        "MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signed_templates",
        help_text="Signature template to append when sending (only for autoreply type)",
    )

    class Meta:
        db_table = "messages_messagetemplate"
        verbose_name = "message template"
        verbose_name_plural = "message templates"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(mailbox__isnull=False) | models.Q(maildomain__isnull=False)
                ),
                name="messagetemplate_has_owner",
            ),
            models.UniqueConstraint(
                fields=("mailbox", "type"),
                condition=models.Q(is_forced=True),
                name="uniq_forced_template_mailbox_type",
            ),
            models.UniqueConstraint(
                fields=("maildomain", "type"),
                condition=models.Q(is_forced=True),
                name="uniq_forced_template_maildomain_type",
            ),
            models.UniqueConstraint(
                fields=("mailbox", "type"),
                condition=models.Q(is_default=True),
                name="uniq_default_template_mailbox_type",
            ),
            models.UniqueConstraint(
                fields=("maildomain", "type"),
                condition=models.Q(is_default=True),
                name="uniq_default_template_maildomain_type",
            ),
            models.UniqueConstraint(
                fields=("mailbox",),
                condition=models.Q(
                    type=MessageTemplateTypeChoices.AUTOREPLY.value, is_active=True
                ),
                name="uniq_active_autoreply_per_mailbox",
            ),
        ]
        indexes = [
            models.Index(fields=("mailbox", "type", "is_active")),
            models.Index(fields=("maildomain", "type", "is_active")),
            models.Index(fields=("mailbox", "type", "is_default")),
            models.Index(fields=("maildomain", "type", "is_default")),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def save(self, *args, **kwargs):
        """If the template is forced or default, unset other templates of the same type
        in the same scope (mailbox or maildomain).
        Only one forced/default template is allowed per type and scope."""
        # Invalidate cached parsed content so it's re-read from the new blob
        self.__dict__.pop("_parsed_content", None)

        with transaction.atomic():
            # Handle is_forced: only one forced template per type and scope
            if self.is_forced:
                qs = (
                    MessageTemplate.objects.select_for_update()
                    .filter(type=self.type, is_forced=True)
                    .exclude(id=self.id)
                )
                if self.mailbox_id:
                    qs = qs.filter(mailbox_id=self.mailbox_id)
                elif self.maildomain_id:
                    qs = qs.filter(maildomain_id=self.maildomain_id)
                qs.update(is_forced=False)

            # Handle autoreply: only one active autoreply per mailbox
            if (
                self.type == MessageTemplateTypeChoices.AUTOREPLY
                and self.is_active
                and self.mailbox_id
            ):
                MessageTemplate.objects.select_for_update().filter(
                    type=MessageTemplateTypeChoices.AUTOREPLY,
                    is_active=True,
                    mailbox_id=self.mailbox_id,
                ).exclude(id=self.id).update(is_active=False)

            # Handle is_default: only one default template per type and scope
            if self.is_default:
                qs = (
                    MessageTemplate.objects.select_for_update()
                    .filter(type=self.type, is_default=True)
                    .exclude(id=self.id)
                )
                if self.mailbox_id:
                    qs = qs.filter(mailbox_id=self.mailbox_id)
                elif self.maildomain_id:
                    qs = qs.filter(maildomain_id=self.maildomain_id)
                qs.update(is_default=False)

            super().save(*args, **kwargs)

    def clean(self):
        if not self.mailbox and not self.maildomain:
            raise ValidationError(
                {"__all__": "MessageTemplate must have a mailbox or maildomain"}
            )
        # it's not possible to link a template to both a mailbox and a maildomain
        if self.mailbox and self.maildomain:
            raise ValidationError(
                {"__all__": "Mailbox and maildomain cannot be linked together"}
            )
        # if user deactivates a template, it should no longer be forced or default
        if not self.is_active:
            self.is_forced = False
            self.is_default = False
        # Autoreply-specific validations
        if self.type == MessageTemplateTypeChoices.AUTOREPLY:
            if not self.mailbox:
                raise ValidationError(
                    {"__all__": "Autoreply templates must be scoped to a mailbox."}
                )
            if self.is_forced or self.is_default:
                raise ValidationError(
                    {"__all__": "Autoreply templates cannot be forced or default."}
                )
            self.validate_autoreply_metadata()
        super().clean()

    def validate_autoreply_metadata(self):
        """Validate autoreply metadata schema."""
        metadata = self.metadata or {}
        schedule_type = metadata.get("schedule_type")
        if not schedule_type:
            raise ValidationError(
                {"metadata": "Autoreply metadata must include 'schedule_type'."}
            )
        if schedule_type not in ("always", "date_range", "recurring_weekly"):
            raise ValidationError(
                {
                    "metadata": "schedule_type must be 'always', 'date_range', or 'recurring_weekly'."
                }
            )
        if schedule_type == "date_range":
            start_at = metadata.get("start_at")
            end_at = metadata.get("end_at")
            if not start_at or not end_at:
                raise ValidationError(
                    {
                        "metadata": "date_range schedule requires 'start_at' and 'end_at'."
                    }
                )
            try:
                start = dt.fromisoformat(start_at)
                end = dt.fromisoformat(end_at)
            except (ValueError, TypeError) as exc:
                raise ValidationError(
                    {
                        "metadata": "start_at and end_at must be valid ISO datetime strings."
                    }
                ) from exc
            if start >= end:
                raise ValidationError({"metadata": "start_at must be before end_at."})
        if schedule_type == "recurring_weekly":
            intervals = metadata.get("intervals")
            if not intervals or not isinstance(intervals, list):
                raise ValidationError(
                    {
                        "metadata": "recurring_weekly schedule requires 'intervals' as a list."
                    }
                )
            time_re = re.compile(r"^\d{2}:\d{2}$")
            for interval in intervals:
                if not isinstance(interval, dict):
                    raise ValidationError(
                        {"metadata": "Each interval must be an object."}
                    )
                for key in ("start_day", "start_time", "end_day", "end_time"):
                    if key not in interval:
                        raise ValidationError(
                            {"metadata": f"Each interval must have '{key}'."}
                        )
                for day_key in ("start_day", "end_day"):
                    if not isinstance(interval[day_key], int) or not (
                        1 <= interval[day_key] <= 7
                    ):
                        raise ValidationError(
                            {
                                "metadata": f"'{day_key}' must be an integer between 1 and 7."
                            }
                        )
                for time_key in ("start_time", "end_time"):
                    val = interval[time_key]
                    if not isinstance(val, str) or not time_re.match(val):
                        raise ValidationError(
                            {"metadata": f"'{time_key}' must be a valid HH:MM string."}
                        )
                    try:
                        time.fromisoformat(val)
                    except ValueError as error:
                        raise ValidationError(
                            {"metadata": f"'{time_key}' must be a valid HH:MM string."}
                        ) from error
        # Validate timezone if provided
        tz_name = metadata.get("timezone")
        if tz_name:
            try:
                ZoneInfo(tz_name)
            except (KeyError, ValueError) as exc:
                raise ValidationError(
                    {"metadata": f"Invalid timezone: {tz_name}"}
                ) from exc

    def is_active_autoreply(self, now=None):
        """Check if this template is an active autoreply based on schedule.

        Returns True if this template is active, is of type AUTOREPLY,
        and the current time falls within the configured schedule.
        """
        if not self.is_active:
            return False
        if self.type != MessageTemplateTypeChoices.AUTOREPLY:
            return False

        metadata = self.metadata or {}
        schedule_type = metadata.get("schedule_type", "always")

        if schedule_type == "always":
            return True

        tz_name = metadata.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, ValueError):
            tz = ZoneInfo("UTC")

        if now is None:
            now = timezone.now()
        local_now = now.astimezone(tz)

        if schedule_type == "date_range":
            start_at = metadata.get("start_at")
            end_at = metadata.get("end_at")
            if not start_at or not end_at:
                return False
            try:
                start = dt.fromisoformat(start_at)
                end = dt.fromisoformat(end_at)
            except (ValueError, TypeError):
                return False
            # Make timezone-aware if naive
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if end.tzinfo is None:
                end = end.replace(tzinfo=tz)
            return start <= local_now <= end

        if schedule_type == "recurring_weekly":
            intervals = metadata.get("intervals", [])
            current_day = local_now.isoweekday()
            current_time = local_now.time()
            current = (current_day, current_time)

            for interval in intervals:
                try:
                    st = time.fromisoformat(interval["start_time"])
                    et = time.fromisoformat(interval["end_time"])
                    sd = interval["start_day"]
                    ed = interval["end_day"]
                except (KeyError, ValueError, TypeError):
                    continue
                start = (sd, st)
                end = (ed, et)
                if start <= end:
                    # Same-week span (e.g., Mon 09:00 → Fri 17:00)
                    if start <= current <= end:
                        return True
                # Cross-week span (e.g., Fri 18:00 → Mon 08:00)
                elif current >= start or current <= end:
                    return True
            return False

        return False

    @cached_property
    def _parsed_content(self):
        """Decompress and parse the blob content once."""
        if not self.blob:
            return {}
        try:
            return json.loads(self.blob.get_content().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            logger.warning(
                "Failed to parse blob content for MessageTemplate %s: %s",
                self.id,
                exc,
            )
            return {}

    @property
    def html_body(self):
        """Get HTML body from content blob."""
        return self._parsed_content.get("html", "")

    @property
    def text_body(self):
        """Get text body from content blob."""
        return self._parsed_content.get("text", "")

    @property
    def raw_body(self):
        """Get raw body from content blob."""
        raw_body = self._parsed_content.get("raw")
        return json.dumps(raw_body, separators=(",", ":")) if raw_body else None

    @staticmethod
    def resolve_placeholder_values(mailbox=None, user=None, message=None):
        """
        Resolve placeholder values from mailbox, user, and message context.

        Args:
            mailbox: Mailbox object — provides `name` via its contact
            user: User object — fallback for `name` and source of custom attributes
            message: Message object — provides `recipient_name` from TO recipients

        Returns:
            Dictionary mapping placeholder keys to their resolved string values
        """
        context = {}
        context["name"] = (
            mailbox.contact.name
            if mailbox and mailbox.contact
            else (getattr(user, "full_name", None) if user else "")
        )
        schema = settings.SCHEMA_CUSTOM_ATTRIBUTES_USER
        schema_properties = schema.get("properties", {})

        if user:
            for field_key in schema_properties.keys():
                context[field_key] = user.custom_attributes.get(field_key) or ""

        if message:
            to_recipients = message.recipients.filter(
                type=MessageRecipientTypeChoices.TO
            ).select_related("contact")
            context["recipient_name"] = ", ".join(
                recipient.contact.name
                for recipient in to_recipients
                if recipient.contact.name
            )

        return context

    def render_template(
        self,
        mailbox: Mailbox = None,
        user: User = None,
        message: Message = None,
    ) -> Dict[str, str]:
        """
        Render the template with the given context.

        Args:
            mailbox: Mailbox object
            user: User object
            message: Message object

        Returns:
            Dictionary with 'html_body' and 'text_body' keys containing rendered content
        """
        resolved = self.resolve_placeholder_values(
            mailbox=mailbox, user=user, message=message
        )

        rendered_html_body = self.html_body
        rendered_text_body = self.text_body

        # Simple placeholder substitution
        for key, value in resolved.items():
            placeholder = f"{{{key}}}"
            rendered_html_body = rendered_html_body.replace(
                placeholder, escape(str(value))
            )
            rendered_text_body = rendered_text_body.replace(placeholder, str(value))

        return {
            "html_body": rendered_html_body,
            "text_body": rendered_text_body,
        }
