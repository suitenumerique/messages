"""Client serializers for the messages core app."""
# pylint: disable=too-many-lines

import hashlib
import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from core import enums, models
from core.mda.rfc5322 import extract_base64_images_from_html


class CreateOnlyFieldsMixin:
    """Mixin that makes specified fields read-only on update (when instance exists).

    Usage:
        class MySerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
            class Meta:
                create_only_fields = ["mailbox", "thread"]
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            for field_name in getattr(self.Meta, "create_only_fields", []):
                if field_name in self.fields:
                    self.fields[field_name].read_only = True


@extend_schema_field({"type": "object", "additionalProperties": True})
class ObjectJSONField(serializers.JSONField):
    """JSONField annotated as ``type: object`` for OpenAPI schema generation."""


def _build_thread_event_data_schema():
    """Build the OpenAPI schema for ThreadEvent.data from model DATA_SCHEMAS.

    Returns a `oneOf` composition when multiple types are defined.
    """
    schemas = models.ThreadEvent.DATA_SCHEMAS
    return {"oneOf": list(schemas.values())}


@extend_schema_field(_build_thread_event_data_schema())
class ThreadEventDataField(serializers.JSONField):
    """JSONField for ThreadEvent.data, OpenAPI-annotated from model DATA_SCHEMAS."""


class IntegerChoicesField(serializers.ChoiceField):
    """
    Custom field to handle IntegerChoices that accepts string labels for input
    and returns string labels for output.

    Example usage:
        role = IntegerChoicesField(choices=MailboxRoleChoices)

    This field will:
    - Accept strings like "viewer", "editor", "admin" for input
    - Store them as integers (1, 2, 4) in the database
    - Return strings like "viewer", "editor", "admin" for output
    - Provide helpful error messages for invalid choices
    - Support backward compatibility with integer input
    """

    def __init__(self, choices_class, **kwargs):
        super().__init__(choices=choices_class.choices, **kwargs)
        self._override_spectacular_annotation(choices_class)

    def _override_spectacular_annotation(self, choices_class):
        """
        Override the OpenAPI annotation for the field.
        This method has the same effect than `extend_schema_field` decorator.
        We do that only to be able to use class attributes as choices that is not possible with the decorator.
        https://drf-spectacular.readthedocs.io/en/latest/drf_spectacular.html#drf_spectacular.utils.extend_schema_field
        """
        self._spectacular_annotation = {
            "field": {
                "type": "string",
                "enum": [label for _value, label in choices_class.choices],
            },
            "field_component_name": choices_class.__name__,
        }

    @extend_schema_field(
        {
            "type": "string",
            "enum": None,  # This will be set dynamically
            "description": "Choice field that accepts string labels and returns string labels",
        }
    )
    def to_representation(self, value):
        """Convert integer value to string label for output."""
        if value is None:
            return None
        enum_instance = self.choices[value]
        return enum_instance

    def to_internal_value(self, data):
        """Convert string label to integer value for storage."""
        if data is None:
            return None

        # If it's already an integer (for backward compatibility), validate and return it
        if isinstance(data, int):
            try:
                # Validate it's a valid choice
                self.choices[data]  # pylint: disable=pointless-statement
                return data
            except KeyError:
                self.fail("invalid_choice", input=data)

        # Convert string label to integer value
        if isinstance(data, str):
            for choice_value, choice_label in self.choices.items():
                if choice_label == data:
                    return choice_value
            self.fail("invalid_choice", input=data)

        self.fail("invalid_choice", input=data)

        return None

    default_error_messages = {
        "invalid_choice": "Invalid choice: {input}. Valid choices are: {choices}."
    }

    def fail(self, key, **kwargs):
        """Override to provide better error messages."""
        if key == "invalid_choice":
            valid_choices = [label for value, label in self.choices.items()]
            kwargs["choices"] = ", ".join(valid_choices)
        super().fail(key, **kwargs)


class AbilitiesModelSerializer(serializers.ModelSerializer):
    """
    A ModelSerializer that takes an additional `exclude` argument that
    dynamically controls which fields should be excluded from the serializer.
    """

    def __init__(self, *args, **kwargs):
        """Add abilities field unless exclude_abilities is True."""
        if not hasattr(self, "exclude_abilities"):
            self.exclude_abilities = kwargs.pop("exclude_abilities", False)
        super().__init__(*args, **kwargs)

        # Add abilities field unless exclude_abilities is True
        if not self.exclude_abilities:
            abilities_field = serializers.SerializerMethodField(read_only=True)
            self.fields["abilities"] = abilities_field

    # This decorator is generic, override the `get_abilities` method
    # in the child serializer to provide the specific implementation if needed.
    @extend_schema_field(
        {
            "type": "object",
            "description": "Instance permissions and capabilities",
            "additionalProperties": {"type": "boolean"},
            "nullable": True,
        }
    )
    def get_abilities(self, instance):
        """Get abilities for the instance."""
        request = self.context.get("request")
        if not request:
            return {}

        if isinstance(instance, models.User):
            return instance.get_abilities()

        return instance.get_abilities(request.user)


class UserSerializer(AbilitiesModelSerializer):
    """Serialize users."""

    custom_attributes = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.User
        fields = ["id", "email", "full_name", "custom_attributes"]
        read_only_fields = fields

    @extend_schema_field(
        {
            "type": "object",
            "description": "Instance permissions and capabilities",
            "properties": {
                choice.value: {"type": "boolean", "description": choice.label}
                for choice in models.UserAbilities
            },
            "required": [choice.value for choice in models.UserAbilities],
        }
    )
    def get_abilities(self, instance):
        """Get abilities for the instance."""
        return super().get_abilities(instance)

    def get_custom_attributes(self, instance) -> dict:
        """Get custom attributes for the instance."""
        return instance.custom_attributes


class UserWithAbilitiesSerializer(UserSerializer):
    """
    Serialize users with abilities.
    Allow to have separated OpenAPI definition for users with and without abilities.
    """

    exclude_abilities = False


class UserWithoutAbilitiesSerializer(UserSerializer):
    """
    Serialize users without abilities.
    Allow to have separated OpenAPI definition for users with and without abilities.
    """

    exclude_abilities = True


class MailboxAvailableSerializer(serializers.ModelSerializer):
    """Serialize mailboxes."""

    contact = serializers.SerializerMethodField(read_only=True)
    email = serializers.SerializerMethodField(read_only=True)

    def get_contact(self, instance):
        """Return the contact of the mailbox."""
        if instance.contact:
            return instance.contact.name
        return None

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "contact"]


class MailboxSerializer(AbilitiesModelSerializer):
    """Serialize mailboxes."""

    email = serializers.SerializerMethodField(read_only=True)
    role = serializers.SerializerMethodField(read_only=True)
    count_unread_threads = serializers.SerializerMethodField(read_only=True)
    count_threads = serializers.SerializerMethodField(read_only=True)
    count_delivering = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Mailbox
        fields = [
            "id",
            "email",
            "is_identity",
            "role",
            "count_unread_threads",
            "count_threads",
            "count_delivering",
        ]
        read_only_fields = fields

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    @extend_schema_field(IntegerChoicesField(choices_class=models.MailboxRoleChoices))
    def get_role(self, instance):
        """Return the allowed actions of the logged-in user on the instance."""
        # Use the annotated user_role field
        if hasattr(instance, "user_role") and instance.user_role is not None:
            try:
                role_enum = models.MailboxRoleChoices(instance.user_role)
                return role_enum.label
            except ValueError:
                return None

        # Fallback for backward compatibility
        request = self.context.get("request")
        if request:
            try:
                role_enum = models.MailboxRoleChoices(
                    instance.accesses.get(user=request.user).role
                )
                return role_enum.label
            except models.MailboxAccess.DoesNotExist:
                return None
        return None

    def _get_cached_counts(self, instance):
        """Get or compute cached counts for the instance in a single query."""
        cache_key = f"_counts_{instance.pk}"
        if not hasattr(self, cache_key):
            counts = instance.thread_accesses.aggregate(
                count_unread_threads=Count(
                    "id",
                    filter=models.ThreadAccess.unread_filter(),
                ),
                count_threads=Count("thread"),
                count_delivering=Count(
                    "thread",
                    filter=Q(thread__has_delivery_pending=True),
                    distinct=True,
                ),
            )
            setattr(self, cache_key, counts)
        return getattr(self, cache_key)

    def get_count_unread_threads(self, instance):
        """Return the number of threads with unread messages in the mailbox."""
        return self._get_cached_counts(instance)["count_unread_threads"]

    def get_count_threads(self, instance):
        """Return the number of threads in the mailbox."""
        return self._get_cached_counts(instance)["count_threads"]

    def get_count_delivering(self, instance):
        """Return the number of threads with messages being delivered."""
        return self._get_cached_counts(instance)["count_delivering"]

    @extend_schema_field(
        {
            "type": "object",
            "description": "Instance permissions and capabilities",
            "properties": {
                choice.value: {"type": "boolean", "description": choice.label}
                for choice in [*models.CRUDAbilities, *models.MailboxAbilities]
            },
            "required": [
                choice.value
                for choice in [*models.CRUDAbilities, *models.MailboxAbilities]
            ],
        }
    )
    def get_abilities(self, instance):
        """Get abilities for the instance."""
        return super().get_abilities(instance)


class MailboxLightSerializer(serializers.ModelSerializer):
    """Serializer for mailbox details in thread access."""

    email = serializers.SerializerMethodField(read_only=True)
    name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "name"]
        read_only_fields = fields

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    def get_name(self, instance):
        """Return the contact of the mailbox."""
        if instance.contact:
            return instance.contact.name
        return None


class ReadMessageTemplateSerializer(serializers.ModelSerializer):
    """Serialize message templates with dynamic body field inclusion.

    Body fields (html_body, text_body, raw_body) are only included when
    explicitly requested via the ``?bodies=`` query parameter or the
    ``body_fields`` keyword argument (for nested usage).

    Allowed values: ``raw``, ``html``, ``text`` (comma-separated).
    Mapping: ``raw`` → ``raw_body``, ``html`` → ``html_body``, ``text`` → ``text_body``.

    When neither query param nor kwarg is provided, no body field is returned.
    """

    BODY_FIELD_MAP = {
        "raw": "raw_body",
        "html": "html_body",
        "text": "text_body",
    }

    type = IntegerChoicesField(choices_class=enums.MessageTemplateTypeChoices)
    # Not marked read_only so that drf-spectacular (with COMPONENT_SPLIT_REQUEST)
    # correctly treats them as optional in the response schema. This serializer
    # is never used for write operations.
    html_body = serializers.CharField(required=False, allow_null=True, default=None)
    text_body = serializers.CharField(required=False, allow_null=True, default=None)
    raw_body = serializers.CharField(required=False, allow_null=True, default=None)
    metadata = ObjectJSONField(required=False, default=dict)
    is_active_autoreply = serializers.SerializerMethodField()
    signature = serializers.PrimaryKeyRelatedField(read_only=True)

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_is_active_autoreply(self, obj):
        """Return whether the autoreply is currently active based on schedule."""
        if obj.type != enums.MessageTemplateTypeChoices.AUTOREPLY:
            return None
        return obj.is_active_autoreply()

    def __init__(self, *args, **kwargs):
        body_fields = kwargs.pop("body_fields", None)
        super().__init__(*args, **kwargs)

        # Determine which body fields (html_body, text_body, raw_body) to keep.
        # When there is no request context and no explicit body_fields kwarg,
        # keep all fields (this is the case for OpenAPI schema introspection).
        request = self.context.get("request")
        if body_fields is None and request is None:
            return

        requested = set()
        if body_fields is not None:
            requested = set(body_fields)
        elif request:
            for value in request.query_params.getlist("bodies"):
                for part in value.split(","):
                    field_name = part.strip()
                    if field_name in self.BODY_FIELD_MAP:
                        requested.add(field_name)

        # Remove unrequested body fields
        all_body_keys = set(self.BODY_FIELD_MAP.keys())
        for key in all_body_keys - requested:
            self.fields.pop(self.BODY_FIELD_MAP[key], None)

    class Meta:
        model = models.MessageTemplate
        fields = [
            "id",
            "name",
            "html_body",
            "text_body",
            "raw_body",
            "type",
            "is_active",
            "is_forced",
            "is_default",
            "metadata",
            "signature",
            "is_active_autoreply",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ContactSerializer(serializers.ModelSerializer):
    """Serialize contacts."""

    class Meta:
        model = models.Contact
        fields = ["id", "name", "email"]


class BlobSerializer(serializers.ModelSerializer):
    """Serialize blobs."""

    blobId = serializers.UUIDField(source="id", read_only=True)
    type = serializers.CharField(source="content_type", read_only=True)
    sha256 = serializers.SerializerMethodField()

    def get_sha256(self, obj):
        """Convert binary SHA256 to hex string."""
        return obj.sha256.hex() if obj.sha256 else None

    class Meta:
        model = models.Blob
        fields = [
            "blobId",
            "size",
            "type",
            "sha256",
            "created_at",
        ]
        read_only_fields = fields


class AttachmentSerializer(serializers.ModelSerializer):
    """Serialize attachments."""

    blobId = serializers.UUIDField(source="blob.id", read_only=True)
    type = serializers.CharField(source="content_type", read_only=True)
    sha256 = serializers.SerializerMethodField()
    cid = serializers.CharField(
        read_only=True, allow_null=True, help_text="Content-ID for inline images"
    )

    def get_sha256(self, obj):
        """Convert binary SHA256 to hex string."""
        return obj.sha256.hex() if obj.sha256 else None

    class Meta:
        model = models.Attachment
        fields = [
            "blobId",
            "name",
            "size",
            "type",
            "sha256",
            "created_at",
            "cid",
        ]
        read_only_fields = fields


class ThreadLabelSerializer(serializers.ModelSerializer):
    """Serializer to get labels details for a thread."""

    display_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Label
        fields = [
            "id",
            "name",
            "slug",
            "color",
            "display_name",
            "description",
            "is_auto",
        ]
        read_only_fields = ["id", "slug", "display_name"]

    def get_display_name(self, instance):
        """Return the display name of the label."""
        return instance.name.split("/")[-1]


class TreeLabelSerializer(serializers.ModelSerializer):
    """Serializer for tree label response structure (OpenAPI purpose only...)."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.CharField(read_only=True)
    color = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    children = serializers.SerializerMethodField(read_only=True)
    description = serializers.CharField(read_only=True)
    is_auto = serializers.BooleanField(read_only=True)

    class Meta:
        model = models.Label
        fields = [
            "id",
            "name",
            "slug",
            "color",
            "display_name",
            "children",
            "description",
            "is_auto",
        ]
        read_only_fields = fields

    @extend_schema_field(
        {"type": "array", "items": {"$ref": "#/components/schemas/TreeLabel"}}
    )
    def get_children(self, instance):
        """
        Fake method just to make the OpenAPI schema valid and work well with
        the recursive nature of the tree label structure.
        """


class LabelSerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    """Serializer for Label model."""

    class Meta:
        model = models.Label
        fields = [
            "id",
            "name",
            "slug",
            "color",
            "mailbox",
            "threads",
            "description",
            "is_auto",
        ]
        read_only_fields = ["id", "slug"]
        create_only_fields = ["mailbox"]

    def validate_mailbox(self, value):
        """Validate that user has access to the mailbox."""
        user = self.context["request"].user
        if not value.accesses.filter(
            user=user,
            role__in=enums.MAILBOX_ROLES_CAN_EDIT,
        ).exists():
            raise PermissionDenied("You don't have access to this mailbox")
        return value


class ThreadAccessDetailSerializer(serializers.ModelSerializer):
    """Serializer for thread access details."""

    mailbox = MailboxLightSerializer()
    role = IntegerChoicesField(
        choices_class=models.ThreadAccessRoleChoices, read_only=True
    )

    class Meta:
        model = models.ThreadAccess
        fields = ["id", "mailbox", "role", "read_at", "starred_at"]
        read_only_fields = fields


class ThreadSerializer(serializers.ModelSerializer):
    """Serialize threads."""

    messages = serializers.SerializerMethodField(read_only=True)
    sender_names = serializers.ListField(child=serializers.CharField(), read_only=True)
    user_role = serializers.SerializerMethodField(read_only=True)
    has_unread = serializers.SerializerMethodField(read_only=True)
    has_starred = serializers.SerializerMethodField(read_only=True)
    accesses = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    summary = serializers.CharField(read_only=True)

    @extend_schema_field(serializers.BooleanField())
    def get_has_unread(self, instance):
        """Return whether the thread has unread messages for the current mailbox.

        Requires the _has_unread annotation (set by ThreadViewSet when mailbox_id is provided).
        Returns False when the annotation is absent (no mailbox context).
        """
        return getattr(instance, "_has_unread", False)

    @extend_schema_field(serializers.BooleanField())
    def get_has_starred(self, instance):
        """Return whether the thread is starred for the current mailbox.

        Requires the _has_starred annotation (set by ThreadViewSet when mailbox_id is provided).
        Returns False when the annotation is absent (no mailbox context).
        """
        return getattr(instance, "_has_starred", False)

    @extend_schema_field(ThreadAccessDetailSerializer(many=True))
    def get_accesses(self, instance):
        """Return the accesses for the thread."""
        accesses = instance.accesses.select_related("mailbox", "mailbox__contact")

        return ThreadAccessDetailSerializer(accesses, many=True).data

    def get_messages(self, instance):
        """Return the messages in the thread."""
        # Consider performance for large threads; pagination might be needed here?
        return [str(message.id) for message in instance.messages.order_by("created_at")]

    @extend_schema_field(
        IntegerChoicesField(choices_class=models.ThreadAccessRoleChoices)
    )
    def get_user_role(self, instance):
        """Get current user's role for this thread, scoped to the context mailbox."""
        mailbox_id = self.context.get("mailbox_id")
        if not mailbox_id:
            return None

        try:
            role_value = instance.accesses.get(mailbox_id=mailbox_id).role
            return models.ThreadAccessRoleChoices(role_value).label
        except models.ThreadAccess.DoesNotExist:
            return None

    @extend_schema_field(ThreadLabelSerializer(many=True))
    def get_labels(self, instance):
        """Get labels for the thread, scoped to the context mailbox."""
        mailbox_id = self.context.get("mailbox_id")
        if not mailbox_id:
            return []

        labels = instance.labels.filter(mailbox_id=mailbox_id)
        return ThreadLabelSerializer(labels, many=True).data

    class Meta:
        model = models.Thread
        fields = [
            "id",
            "subject",
            "snippet",
            "messages",
            "has_unread",
            "has_trashed",
            "is_trashed",
            "has_archived",
            "has_draft",
            "has_starred",
            "has_attachments",
            "has_sender",
            "has_messages",
            "has_delivery_failed",
            "has_delivery_pending",
            "is_spam",
            "has_active",
            "messaged_at",
            "active_messaged_at",
            "draft_messaged_at",
            "sender_messaged_at",
            "archived_messaged_at",
            "trashed_messaged_at",
            "sender_names",
            "updated_at",
            "user_role",
            "accesses",
            "labels",
            "summary",
        ]
        read_only_fields = fields  # Mark all as read-only for safety


class MessageRecipientSerializer(serializers.ModelSerializer):
    """Serialize message recipients."""

    contact = ContactSerializer(read_only=True)
    delivery_status = IntegerChoicesField(
        choices_class=models.MessageDeliveryStatusChoices,
        read_only=True,
        allow_null=True,
    )
    delivery_message = serializers.CharField(read_only=True, allow_null=True)
    retry_at = serializers.DateTimeField(read_only=True, allow_null=True)
    delivered_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = models.MessageRecipient
        fields = [
            "id",
            "contact",
            "delivery_status",
            "delivery_message",
            "retry_at",
            "delivered_at",
        ]


class MessageBodyItemSerializer(serializers.Serializer):
    """Message body item serializer."""

    partId = serializers.CharField()
    type = serializers.CharField()
    content = serializers.CharField(allow_blank=True)

    def create(self, validated_data):
        """Do not allow creating instances from this serializer."""
        raise RuntimeError(f"{self.__class__.__name__} does not support create method")

    def update(self, instance, validated_data):
        """Do not allow updating instances from this serializer."""
        raise RuntimeError(f"{self.__class__.__name__} does not support update method")


class MessageSenderUserSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the user who sent a message."""

    class Meta:
        model = models.User
        fields = ["id", "full_name", "email"]
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    """
    Serialize messages, getting parsed details from the Message model.
    Aligns field names with JMAP where appropriate (textBody, htmlBody, to, cc, bcc).
    """

    # JMAP-style body fields (from model's parsed data)
    textBody = serializers.SerializerMethodField(read_only=True)
    htmlBody = serializers.SerializerMethodField(read_only=True)
    draftBody = serializers.SerializerMethodField(read_only=True)
    attachments = serializers.SerializerMethodField(read_only=True)

    # JMAP-style recipient fields (from model's parsed data)
    to = serializers.SerializerMethodField(read_only=True)
    cc = serializers.SerializerMethodField(read_only=True)
    bcc = serializers.SerializerMethodField(read_only=True)

    sender = ContactSerializer(read_only=True)  # Sender contact info
    sender_user = MessageSenderUserSerializer(read_only=True, allow_null=True)

    # UUID of the parent message
    parent_id = serializers.UUIDField(
        source="parent.id", allow_null=True, read_only=True
    )

    # UUID of the thread
    thread_id = serializers.UUIDField(
        source="thread.id", allow_null=True, read_only=True
    )

    is_unread = serializers.SerializerMethodField(read_only=True)
    signature = serializers.SerializerMethodField()
    stmsg_headers = serializers.SerializerMethodField(read_only=True)

    @extend_schema_field(ReadMessageTemplateSerializer(allow_null=True))
    def get_signature(self, instance):
        """Return the signature template with only html_body included."""
        if instance.signature is None:
            return None
        return ReadMessageTemplateSerializer(
            instance.signature, body_fields=["html"]
        ).data

    @extend_schema_field(serializers.BooleanField())
    def get_is_unread(self, instance):
        """Return the ``_is_unread`` annotation set by ``MessageQuerySet.with_read_state()``.

        Falls back to ``False`` when the queryset was not annotated (e.g.
        internal usage without a mailbox context).
        """
        return getattr(instance, "_is_unread", False)

    def get_stmsg_headers(self, instance) -> dict:
        """Return the STMSG headers of the message."""
        return instance.get_stmsg_headers()

    @extend_schema_field(MessageBodyItemSerializer(many=True))
    def get_textBody(self, instance):  # pylint: disable=invalid-name
        """Return the list of text body parts (JMAP style)."""
        return instance.get_parsed_field("textBody") or []

    @extend_schema_field(MessageBodyItemSerializer(many=True))
    def get_htmlBody(self, instance):  # pylint: disable=invalid-name
        """Return the list of HTML body parts (JMAP style)."""
        return instance.get_parsed_field("htmlBody") or []

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_draftBody(self, instance):  # pylint: disable=invalid-name
        """Return an arbitrary JSON object representing the draft body."""
        return (
            instance.draft_blob.get_content().decode("utf-8")
            if instance.draft_blob
            else None
        )

    @extend_schema_field(AttachmentSerializer(many=True))
    def get_attachments(self, instance):
        """Return the parsed email attachments or linked attachments for drafts."""

        # If the message has no attachments, return an empty list
        if not instance.has_attachments:
            return []

        # First check for directly linked attachments (for drafts)
        if instance.is_draft:
            return AttachmentSerializer(instance.attachments.all(), many=True).data

        # Then get any parsed attachments from the email if available
        parsed_attachments = instance.get_parsed_field("attachments") or []

        # Convert parsed attachments to a format similar to AttachmentSerializer
        # Remove the content field from the parsed attachments and create a
        # reference to a virtual blob msg_[message_id]_[attachment_number]
        # This is needed to map our storage schema with the JMAP spec.
        if parsed_attachments:
            stripped_attachments = []
            for index, attachment in enumerate(parsed_attachments):
                stripped_attachments.append(
                    {
                        "blobId": f"msg_{instance.id}_{index}",
                        "name": attachment["name"],
                        "size": attachment["size"],
                        "type": attachment["type"],
                        "cid": attachment.get("cid"),
                    }
                )
            return stripped_attachments

        return []

    @extend_schema_field(MessageRecipientSerializer(many=True))
    def get_to(self, instance):
        """Return the 'To' recipients."""
        recipients = models.MessageRecipient.objects.filter(
            message_id=instance.id, type=models.MessageRecipientTypeChoices.TO
        ).select_related("contact")
        return MessageRecipientSerializer(recipients, many=True).data

    @extend_schema_field(MessageRecipientSerializer(many=True))
    def get_cc(self, instance):
        """Return the 'Cc' recipients."""
        recipients = models.MessageRecipient.objects.filter(
            message_id=instance.id, type=models.MessageRecipientTypeChoices.CC
        ).select_related("contact")
        return MessageRecipientSerializer(recipients, many=True).data

    @extend_schema_field(MessageRecipientSerializer(many=True))
    def get_bcc(self, instance):
        """
        Return the 'Bcc' recipients, only if the requesting user is allowed to see them.
        """
        request = self.context.get("request")
        # Only show Bcc if it's a mailbox the user has access to and it's a sent message.
        # TODO: add some tests for this

        if (
            request
            and hasattr(request, "user")
            and request.user.is_authenticated
            and instance.is_sender
            and instance.thread.accesses.filter(
                mailbox__accesses__user=request.user,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            ).exists()
        ):
            recipients = models.MessageRecipient.objects.filter(
                message_id=instance.id, type=models.MessageRecipientTypeChoices.BCC
            ).select_related("contact")
            return MessageRecipientSerializer(recipients, many=True).data

        return []

    class Meta:
        model = models.Message
        fields = [
            "id",
            "parent_id",
            "thread_id",
            "subject",
            "created_at",
            "updated_at",
            "htmlBody",
            "textBody",
            "draftBody",
            "attachments",
            "sender",
            "sender_user",
            "to",
            "cc",
            "bcc",
            "sent_at",
            "is_sender",
            "is_draft",
            "is_unread",
            "is_trashed",
            "is_archived",
            "has_attachments",
            "signature",
            "stmsg_headers",
        ]
        read_only_fields = fields  # Mark all as read-only


class ThreadAccessSerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    """Serialize thread access information."""

    role = IntegerChoicesField(choices_class=models.ThreadAccessRoleChoices)

    class Meta:
        model = models.ThreadAccess
        fields = ["id", "thread", "mailbox", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
        create_only_fields = ["thread", "mailbox"]


class ThreadEventSerializer(CreateOnlyFieldsMixin, serializers.ModelSerializer):
    """Serialize thread event information."""

    author = UserWithoutAbilitiesSerializer(read_only=True)
    data = ThreadEventDataField()

    class Meta:
        model = models.ThreadEvent
        fields = [
            "id",
            "thread",
            "type",
            "channel",
            "message",
            "author",
            "data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "thread",
            "channel",
            "author",
            "created_at",
            "updated_at",
        ]
        create_only_fields = ["type", "message"]


class MailboxAccessReadSerializer(serializers.ModelSerializer):
    """Serialize mailbox access information for read operations with nested user details.
    Mailbox context is implied by the URL, so mailbox details are not included here.
    """

    user_details = UserWithoutAbilitiesSerializer(source="user", read_only=True)
    role = IntegerChoicesField(choices_class=models.MailboxRoleChoices, read_only=True)

    class Meta:
        model = models.MailboxAccess
        fields = ["id", "user_details", "role", "created_at", "updated_at"]
        read_only_fields = fields  # All fields are effectively read-only from this serializer's perspective


class UserField(serializers.PrimaryKeyRelatedField):
    """Custom field that accepts either UUID or email address for user lookup."""

    def to_internal_value(self, data):
        """Convert UUID string or email to User instance."""
        if isinstance(data, str):
            if "@" in data:
                # It's an email address, look up the user
                try:
                    return models.User.objects.get(email=data)
                except models.User.DoesNotExist as e:
                    raise serializers.ValidationError(
                        f"No user found with email: {data}"
                    ) from e
            else:
                # It's a UUID, use the parent method
                return super().to_internal_value(data)
        return super().to_internal_value(data)


class MailboxAccessWriteSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating mailbox access records.
    Mailbox is set from the view based on URL parameters.
    """

    role = IntegerChoicesField(choices_class=models.MailboxRoleChoices)
    user = UserField(
        queryset=models.User.objects.all(), help_text="User ID (UUID) or email address"
    )

    class Meta:
        model = models.MailboxAccess
        fields = ["id", "user", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        """Additional validation that applies to the whole object."""
        if self.instance and "user" in attrs and attrs["user"] != self.instance.user:
            raise serializers.ValidationError(
                {
                    "user": [
                        "Cannot change the user of an existing mailbox access record. Delete and create a new one."
                    ]
                }
            )
        return attrs


class MailDomainAdminSerializer(AbilitiesModelSerializer):
    """Serialize mail domains for admin view."""

    expected_dns_records = serializers.SerializerMethodField(read_only=True)

    def get_expected_dns_records(self, instance):
        """Return the expected DNS records for the mail domain, only in detail views."""

        # Only include DNS records in detail views, not in list views
        view = self.context.get("view")
        if view and hasattr(view, "action") and view.action == "retrieve":
            return instance.get_expected_dns_records()

        return None

    class Meta:
        model = models.MailDomain
        fields = [
            "id",
            "name",
            "created_at",
            "updated_at",
            "expected_dns_records",
            "identity_sync",
        ]
        read_only_fields = fields

    @extend_schema_field(
        {
            "type": "object",
            "description": "Instance permissions and capabilities",
            "properties": {
                choice.value: {"type": "boolean", "description": choice.label}
                for choice in [*models.CRUDAbilities, *models.MailDomainAbilities]
            },
            "required": [
                choice.value
                for choice in [*models.CRUDAbilities, *models.MailDomainAbilities]
            ],
        }
    )
    def get_abilities(self, instance):
        """Return the abilities for the mail domain."""
        return super().get_abilities(instance)


class MaildomainAccessReadSerializer(serializers.ModelSerializer):
    """
    Serialize maildomain access information for read operations with nested user details.
    """

    user = UserWithoutAbilitiesSerializer(read_only=True)
    role = IntegerChoicesField(
        choices_class=models.MailDomainAccessRoleChoices, read_only=True
    )

    class Meta:
        model = models.MailDomainAccess
        fields = ["id", "user", "role", "created_at", "updated_at"]
        read_only_fields = fields


class MaildomainAccessWriteSerializer(serializers.ModelSerializer):
    """
    Serializer for creating and updating maildomain access records.
    """

    role = IntegerChoicesField(choices_class=models.MailDomainAccessRoleChoices)
    user = UserField(
        queryset=models.User.objects.all(), help_text="User ID (UUID) or email address"
    )

    class Meta:
        model = models.MailDomainAccess
        fields = ["id", "user", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        """Additional validation that applies to the whole object."""
        if self.instance and "user" in attrs and attrs["user"] != self.instance.user:
            raise serializers.ValidationError(
                {
                    "user": [
                        "Cannot change the user of an existing maildomain access record. Delete and create a new one."
                    ]
                }
            )
        return attrs


class MailDomainAdminWriteSerializer(serializers.ModelSerializer):
    """Serialize mail domains for creating / editing admin view."""

    class Meta:
        model = models.MailDomain
        fields = [
            "id",
            "name",
            "created_at",
            "updated_at",
            "oidc_autojoin",
            "identity_sync",
            "custom_attributes",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MailboxAccessNestedUserSerializer(serializers.ModelSerializer):
    """
    Serialize MailboxAccess for nesting within MailboxAdminSerializer.
    Shows user details and their role on the mailbox.
    """

    user = UserWithoutAbilitiesSerializer(read_only=True)
    role = IntegerChoicesField(choices_class=models.MailboxRoleChoices, read_only=True)

    class Meta:
        model = models.MailboxAccess
        fields = ["id", "user", "role"]  # 'user' will be nested UserSerializer output
        read_only_fields = fields


class MailboxAdminSerializer(serializers.ModelSerializer):
    """
    Serialize Mailbox details for admin view, including users with access.
    """

    domain_name = serializers.CharField(source="domain.name", read_only=True)
    accesses = MailboxAccessNestedUserSerializer(
        many=True, read_only=True
    )  # accesses is the related_name
    can_reset_password = serializers.BooleanField(read_only=True)
    contact = ContactSerializer(read_only=True)
    alias_of = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=models.Mailbox.objects.none()
    )

    class Meta:
        model = models.Mailbox
        fields = [
            "id",
            "local_part",
            "domain_name",
            "is_identity",
            "alias_of",  # show if it's an alias
            "accesses",  # List of users and their roles
            "created_at",
            "updated_at",
            "can_reset_password",
            "contact",
        ]
        read_only_fields = [
            "id",
            "domain_name",
            "is_identity",
            "accesses",  # List of users and their roles
            "created_at",
            "updated_at",
            "can_reset_password",
            "contact",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.context.get("domain"):
            # Lookup in domain mailboxes that are not an alias
            # We must do that here to define a finer grain queryset to lookup
            self.fields["alias_of"].queryset = models.Mailbox.objects.filter(
                domain=self.context.get("domain"), alias_of__isnull=True
            )

    def validate(self, attrs):
        """Validate the domain of the mailbox and denylist rules."""
        if not self.context.get("domain"):
            raise serializers.ValidationError(
                "Domain is required in serializer context."
            )

        domain = self.context.get("domain")
        metadata = self.context.get("metadata", {})
        if metadata.get("type") == "personal" and not domain.identity_sync:
            raise serializers.ValidationError(
                {
                    "identity_sync": (
                        "Personal mailboxes cannot be created when "
                        "identity synchronization is disabled."
                    )
                }
            )

        if metadata.get("type") == "personal":
            local_part = attrs.get("local_part", "")
            denylist = getattr(
                settings, "MESSAGES_MAILBOX_LOCALPART_DENYLIST_PERSONAL", []
            )
            lower_value = local_part.lower()
            if any(lower_value == prefix.lower() for prefix in denylist):
                raise serializers.ValidationError(
                    {
                        "local_part_denied": (
                            "This prefix is not allowed for personal mailboxes."
                        )
                    }
                )

        return super().validate(attrs)

    def validate_local_part(self, value):
        """Validate the local part of the mailbox."""
        if models.Mailbox.objects.filter(
            domain=self.context.get("domain"), local_part=value
        ).exists():
            raise serializers.ValidationError(
                "A mailbox with this local part already exists in this domain."
            )
        return value

    def create(self, validated_data):
        """Perform the create action."""
        domain = self.context.get("domain")
        metadata = self.context.get("metadata", {})
        mailbox_type = metadata.get("type")

        mailbox = models.Mailbox.objects.create(
            domain=domain,
            local_part=validated_data.get("local_part"),
            alias_of=validated_data.get("alias_of"),
            is_identity=mailbox_type == "personal",
        )

        if mailbox_type == "personal":
            email = str(mailbox)
            first_name = metadata.get("first_name")
            last_name = metadata.get("last_name")
            custom_attributes = metadata.get("custom_attributes", {})
            user, created = models.User.objects.get_or_create(
                email=email,
                defaults={
                    "custom_attributes": custom_attributes,
                    "full_name": f"{first_name} {last_name}",
                    "password": "?",
                },
            )

            if not created and custom_attributes:
                user.custom_attributes = custom_attributes
                user.save()

            models.MailboxAccess.objects.create(
                mailbox=mailbox,
                user=user,
                role=models.MailboxRoleChoices.ADMIN,
            )

            contact, _ = models.Contact.objects.get_or_create(
                email=email,
                mailbox=mailbox,
                defaults={"name": f"{first_name} {last_name}"},
            )
            mailbox.contact = contact
            mailbox.save()

        elif mailbox_type == "shared":
            email = str(mailbox)
            name = metadata.get("name")
            contact, _ = models.Contact.objects.get_or_create(
                email=email,
                mailbox=mailbox,
                defaults={"name": name},
            )
            mailbox.contact = contact
            mailbox.save()

        return mailbox

    def update(self, instance, validated_data):
        """Perform the update action."""
        # Do not allow to update some mailbox fields
        validated_data.pop("local_part", None)
        validated_data.pop("alias_of", None)
        validated_data.pop("is_identity", None)

        metadata = self.context.get("metadata", {})
        updated = False

        if instance.is_identity is True:
            user_updated_fields = {}
            contact_updated_fields = {}

            if full_name := metadata.get("full_name"):
                user_updated_fields["full_name"] = full_name
                contact_updated_fields["name"] = full_name
            if custom_attributes := metadata.get("custom_attributes"):
                user_updated_fields["custom_attributes"] = custom_attributes

            if user_updated_fields:
                owner = models.User.objects.filter(
                    email=str(instance), mailbox_accesses__mailbox=instance
                ).first()
                # Use save here to enforce data validation on custom_attributes
                if owner:
                    for key, value in user_updated_fields.items():
                        setattr(owner, key, value)
                    owner.save(update_fields=list(user_updated_fields.keys()))
                    updated = True

            if contact_updated_fields:
                contact = models.Contact.objects.filter(pk=instance.contact_id)
                contact.update(**contact_updated_fields)
                updated = True

        else:
            contact_updated_fields = {}

            if name := metadata.get("name"):
                contact_updated_fields["name"] = name

            if contact_updated_fields:
                contact = models.Contact.objects.filter(pk=instance.contact_id)
                contact.update(**contact_updated_fields)
                updated = True

        if updated:
            instance.refresh_from_db()

        return instance


class MailboxAdminCreateSerializer(MailboxAdminSerializer):
    """
    Serialize Mailbox details for create admin endpoint, including users with access and
    metadata.
    """

    one_time_password = serializers.SerializerMethodField(
        read_only=True, required=False
    )

    def get_one_time_password(self, instance) -> str | None:
        """
        Fake method just to make the OpenAPI schema valid.
        """

    class Meta:
        model = models.Mailbox
        fields = MailboxAdminSerializer.Meta.fields + ["one_time_password"]
        read_only_fields = fields


class ImportBaseSerializer(serializers.Serializer):
    """Base serializer for import actions that disables create and update."""

    def create(self, validated_data):
        """Do not allow creating instances from this serializer."""
        raise RuntimeError(f"{self.__class__.__name__} does not support create method")

    def update(self, instance, validated_data):
        """Do not allow updating instances from this serializer."""
        raise RuntimeError(f"{self.__class__.__name__} does not support update method")


class ImportFileSerializer(ImportBaseSerializer):
    """Serializer for importing email files."""

    filename = serializers.CharField(
        help_text="Filename",
        required=True,
    )

    recipient = serializers.UUIDField(
        help_text="UUID of the recipient mailbox",
        required=True,
    )


class ImportFileUploadSerializer(ImportBaseSerializer):
    """Serializer for uploading files to the message imports bucket."""

    filename = serializers.CharField(
        help_text="Filename",
        required=True,
    )
    content_type = serializers.CharField(
        help_text="Content type",
        required=True,
    )

    class Meta:
        fields = ["filename", "content_type"]

    def validate_content_type(self, value):
        """Validate content type."""
        if value not in enums.ARCHIVE_SUPPORTED_MIME_TYPES:
            raise serializers.ValidationError(
                "Only EML, MBOX, and PST files are supported."
            )
        return value


class ImportFileUploadPartSerializer(ImportBaseSerializer):
    """Serializer for uploading parts of a file to the message imports bucket."""

    filename = serializers.CharField(
        help_text="Filename",
        required=True,
    )
    upload_id = serializers.CharField(
        help_text="Upload ID",
        required=True,
    )
    part_number = serializers.IntegerField(
        help_text="Part number", required=True, min_value=1
    )

    class Meta:
        fields = ["filename", "upload_id", "part_number"]


class UploadPartSerializer(ImportBaseSerializer):
    """Serializer for an upload part."""

    ETag = serializers.CharField(
        help_text="ETag",
        required=True,
    )
    PartNumber = serializers.IntegerField(
        help_text="Part number", required=True, min_value=1
    )

    class Meta:
        fields = ["ETag", "PartNumber"]


class ImportFileUploadCompleteSerializer(ImportBaseSerializer):
    """Serializer for completing a multipart upload of a file to the message imports bucket."""

    filename = serializers.CharField(
        help_text="Filename",
        required=True,
    )
    upload_id = serializers.CharField(
        help_text="Upload ID",
        required=True,
    )
    parts = UploadPartSerializer(required=True, many=True)

    class Meta:
        fields = ["filename", "upload_id", "parts"]


class ImportFileUploadAbortSerializer(ImportBaseSerializer):
    """Serializer for aborting a multipart upload of a file to the message imports bucket."""

    filename = serializers.CharField(
        help_text="Filename",
        required=True,
    )
    upload_id = serializers.CharField(
        help_text="Upload ID",
        required=True,
    )

    class Meta:
        fields = ["filename", "upload_id"]


class ImportIMAPSerializer(ImportBaseSerializer):
    """Serializer for importing messages from IMAP server via API."""

    recipient = serializers.UUIDField(
        help_text="UUID of the recipient mailbox", required=True
    )
    imap_server = serializers.CharField(help_text="IMAP server hostname", required=True)
    imap_port = serializers.IntegerField(
        help_text="IMAP server port", required=True, min_value=0
    )
    username = serializers.EmailField(
        help_text="Email address for IMAP login", required=True
    )
    password = serializers.CharField(
        help_text="IMAP password", required=True, write_only=True
    )
    use_ssl = serializers.BooleanField(
        help_text="Use SSL for IMAP connection", required=False, default=True
    )


class ChannelSerializer(serializers.ModelSerializer):
    """Serialize Channel model."""

    # Explicitly mark nullable fields to fix OpenAPI schema
    mailbox = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    maildomain = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)

    class Meta:
        model = models.Channel
        fields = [
            "id",
            "name",
            "type",
            "settings",
            "mailbox",
            "maildomain",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "mailbox", "maildomain", "created_at", "updated_at"]

    def validate_settings(self, value):
        """Validate settings, including tags if present."""
        if not value:
            return value

        tags = value.get("tags", [])
        if not tags:
            return value

        # Get mailbox from context or instance
        mailbox = self.context.get("mailbox")
        if not mailbox and self.instance:
            mailbox = self.instance.mailbox

        if not mailbox:
            # Tags require a mailbox - can't use tags without one
            raise serializers.ValidationError(
                {"tags": "Tags can only be used when a mailbox is configured."}
            )

        # Validate each tag
        invalid_tags = []
        missing_tags = []

        for tag_id in tags:
            # Validate UUID format
            try:
                tag_uuid = uuid.UUID(str(tag_id))
            except (ValueError, TypeError):
                invalid_tags.append(tag_id)
                continue

            # Check if label exists in the mailbox
            if not models.Label.objects.filter(id=tag_uuid, mailbox=mailbox).exists():
                missing_tags.append(tag_id)

        errors = []
        if invalid_tags:
            errors.append(f"Invalid tag IDs (not valid UUIDs): {invalid_tags}")
        if missing_tags:
            errors.append(f"Tags not found in mailbox: {missing_tags}")

        if errors:
            raise serializers.ValidationError({"tags": errors})

        return value

    def validate(self, attrs):
        """Validate channel data.

        When used in the nested mailbox context (via ChannelViewSet),
        the mailbox is set from context and doesn't need to be validated here.
        """
        # If we have a mailbox in context (from ChannelViewSet), validate channel type
        # and skip mailbox/maildomain validation.
        # This allows Django admin to create any channel type.
        if self.context.get("mailbox"):
            channel_type = attrs.get("type")
            if channel_type:
                allowed_types = settings.FEATURE_MAILBOX_ADMIN_CHANNELS
                if channel_type not in allowed_types:
                    raise serializers.ValidationError(
                        {
                            "type": f"Channel type '{channel_type}' is not authorized. "
                            f"Allowed types: {', '.join(allowed_types)}"
                        }
                    )
            return attrs

        mailbox = attrs.get("mailbox")
        maildomain = attrs.get("maildomain")

        # Validate that either mailbox or maildomain is set, but not both
        if not mailbox and not maildomain:
            raise serializers.ValidationError(
                "Either mailbox or maildomain must be specified."
            )

        if mailbox and maildomain:
            raise serializers.ValidationError(
                "Cannot specify both mailbox and maildomain."
            )

        return attrs


class MessageTemplateSerializer(serializers.ModelSerializer):
    """Serialize message templates for POST/PUT/PATCH operations."""

    type = IntegerChoicesField(choices_class=enums.MessageTemplateTypeChoices)

    is_forced = serializers.BooleanField(
        required=False, default=False, help_text="Set as forced template"
    )
    is_default = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Set as default template (auto-loaded when composing a new message)",
    )
    html_body = serializers.CharField(required=False)
    text_body = serializers.CharField(required=False)
    raw_body = serializers.CharField(required=False)
    metadata = ObjectJSONField(required=False, default=dict)
    signature_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = models.MessageTemplate
        fields = [
            "id",
            "name",
            "html_body",
            "text_body",
            "raw_body",
            "type",
            "is_active",
            "is_forced",
            "is_default",
            "metadata",
            "signature_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _build_content_bytes(self, attrs):
        """Build the JSON blob content bytes from body fields."""
        raw_body = attrs.get("raw_body", "")
        try:
            raw_body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError as err:
            raise serializers.ValidationError(
                {"raw_body": f"Invalid JSON: {err.msg}"}
            ) from err

        return json.dumps(
            {
                "html": attrs.get("html_body", ""),
                "text": attrs.get("text_body", ""),
                "raw": raw_body,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _body_unchanged(self, attrs):
        """Check if body content is identical to the existing blob."""
        instance = self.instance
        if not instance or not instance.blob:
            return False
        content_bytes = self._build_content_bytes(attrs)
        new_hash = hashlib.sha256(content_bytes).digest()
        return new_hash == bytes(instance.blob.sha256)

    def validate(self, attrs):
        """Validate template data."""
        # For creation or update, all content fields must be provided
        # if one of fields html_body, text_body, raw_body is provided, all must be provided
        if any(field in attrs for field in ["html_body", "text_body", "raw_body"]):
            if not all(
                field in attrs for field in ["html_body", "text_body", "raw_body"]
            ):
                raise serializers.ValidationError(
                    "All content fields (html_body, text_body, raw_body) must be provided together."
                )

        if "html_body" in attrs:
            # Skip expensive image validation if body content hasn't changed
            if self._body_unchanged(attrs):
                attrs.pop("html_body")
                attrs.pop("text_body")
                attrs.pop("raw_body")
            else:
                _html, images = extract_base64_images_from_html(attrs["html_body"])
                total_image_size = 0
                for image in images:
                    total_image_size += image["size"]
                    if image["size"] > settings.MAX_TEMPLATE_IMAGE_SIZE:
                        max_mb = settings.MAX_TEMPLATE_IMAGE_SIZE / (1024 * 1024)
                        image_mb = image["size"] / (1024 * 1024)
                        raise serializers.ValidationError(
                            {
                                "html_body": (
                                    'Image "%(name)s" (%(size)s MB) exceeds'
                                    " the %(max)s MB limit."
                                )
                                % {
                                    "name": image["name"],
                                    "size": f"{image_mb:.1f}",
                                    "max": f"{max_mb:.0f}",
                                }
                            }
                        )
                    if total_image_size > settings.MAX_OUTGOING_ATTACHMENT_SIZE:
                        max_mb = settings.MAX_OUTGOING_ATTACHMENT_SIZE / (1024 * 1024)
                        total_mb = total_image_size / (1024 * 1024)
                        raise serializers.ValidationError(
                            {
                                "html_body": (
                                    "Total attachment size (%(total_size)s MB) exceeds the %(max_size)s MB limit. "
                                    "Please remove or reduce attachments."
                                )
                                % {
                                    "total_size": f"{total_mb:.1f}",
                                    "max_size": f"{max_mb:.0f}",
                                }
                            }
                        )

        # Autoreply-specific validation
        template_type = attrs.get("type") or (
            self.instance.type if self.instance else None
        )
        if template_type == enums.MessageTemplateTypeChoices.AUTOREPLY:
            if attrs.get("is_forced"):
                raise serializers.ValidationError(
                    {"is_forced": "Autoreply templates cannot be forced."}
                )
            if attrs.get("is_default"):
                raise serializers.ValidationError(
                    {"is_default": "Autoreply templates cannot be default."}
                )
            metadata = attrs.get("metadata")
            # Skip metadata validation on update
            if not self.instance or metadata is not None:
                try:
                    models.MessageTemplate(
                        type=template_type,
                        metadata=metadata or {},
                    ).validate_autoreply_metadata()
                except DjangoValidationError as exc:
                    raise serializers.ValidationError(exc.message_dict) from exc

        # Validate and resolve signature_id
        signature_id = attrs.pop("signature_id", None)
        if signature_id:
            mailbox = self.context.get("mailbox") or (
                self.instance.mailbox if self.instance else None
            )
            domain = self.context.get("domain") or (
                self.instance.maildomain if self.instance else None
            )

            # Build scope filter: signature must belong to the same mailbox or domain
            scope_filter = Q()
            if mailbox:
                scope_filter |= Q(mailbox=mailbox) | Q(maildomain=mailbox.domain)
            if domain:
                scope_filter |= Q(maildomain=domain)

            if not scope_filter:
                raise serializers.ValidationError(
                    {"signature_id": "Invalid or inaccessible signature."}
                )

            signature = models.MessageTemplate.objects.filter(
                scope_filter,
                id=signature_id,
                type=enums.MessageTemplateTypeChoices.SIGNATURE,
                is_active=True,
            ).first()

            if not signature:
                raise serializers.ValidationError(
                    {"signature_id": "Invalid or inaccessible signature."}
                )
            attrs["signature"] = signature
        elif signature_id is None and "signature_id" in self.initial_data:
            attrs["signature"] = None

        return super().validate(attrs)

    def create(self, validated_data):
        """Create template with relationships and ensure atomic content creation."""
        content_bytes = self._build_content_bytes(validated_data)
        validated_data.pop("html_body", None)
        validated_data.pop("text_body", None)
        validated_data.pop("raw_body", None)
        validated_data["maildomain"] = self.context.get("domain")
        validated_data["mailbox"] = self.context.get("mailbox")

        with transaction.atomic():
            blob = models.Blob.objects.create_blob(
                content=content_bytes,
                content_type="application/json",
                maildomain=self.context.get("domain"),
                mailbox=self.context.get("mailbox"),
            )
            validated_data["blob"] = blob
            return super().create(validated_data)

    def update(self, instance, validated_data):
        """Update template with relationships. Not allowed to change mailbox or maildomain."""
        has_body = any(
            field in validated_data for field in ["html_body", "text_body", "raw_body"]
        )

        with transaction.atomic():
            if has_body:
                content_bytes = self._build_content_bytes(validated_data)
                validated_data.pop("html_body", None)
                validated_data.pop("text_body", None)
                validated_data.pop("raw_body", None)

                if instance.blob:
                    instance.blob.delete()

                blob = models.Blob.objects.create_blob(
                    content=content_bytes,
                    content_type="application/json",
                    maildomain=instance.maildomain or None,
                    mailbox=instance.mailbox or None,
                )
                validated_data["blob"] = blob

            return super().update(instance, validated_data)


class SendMessageSerializer(serializers.Serializer):
    """Serializer for sending messages."""

    messageId = serializers.UUIDField(required=True)
    senderId = serializers.UUIDField(required=True)
    archive = serializers.BooleanField(required=False, default=False)
    textBody = serializers.CharField(required=False, allow_blank=True)
    htmlBody = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        fields = ["messageId", "senderId", "archive", "textBody", "htmlBody"]

    def create(self, validated_data):
        """This serializer is only used to validate the data, not to create or update."""

    def update(self, instance, validated_data):
        """This serializer is only used to validate the data, not to create or update."""


class PartialDriveItemSerializer(serializers.Serializer):
    """
    Serializer for Drive Item resource (OpenAPI purpose only...).
    It supports partially the Drive Item resource response structure.
    We declare only fields that are useful in the Messages context.
    """

    id = serializers.UUIDField(required=True)
    filename = serializers.CharField(required=True)
    mimetype = serializers.CharField(required=True)
    size = serializers.IntegerField(required=True)

    class Meta:
        fields = ["id", "filename", "mimetype", "size"]
        read_only_fields = fields

    def create(self, validated_data):
        """This serializer is only used to validate the data, not to create or update."""

    def update(self, instance, validated_data):
        """This serializer is only used to validate the data, not to create or update."""


class DomainsField(serializers.Field):
    """Accepts either a JSON list of strings or a comma-separated string."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = [d.strip() for d in data.split(",") if d.strip()]
        if not isinstance(data, list):
            raise serializers.ValidationError(
                "Expected a list of domains or a comma-separated string."
            )
        if not data:
            raise serializers.ValidationError("At least one domain is required.")
        return data

    def to_representation(self, value):
        return value


class ProvisioningMailDomainSerializer(serializers.Serializer):
    """Serializer for the provisioning endpoint that creates mail domains."""

    domains = DomainsField()
    custom_attributes = serializers.JSONField(required=False, default=dict)
    oidc_autojoin = serializers.BooleanField(required=False, default=True)
    identity_sync = serializers.BooleanField(required=False, default=False)

    def create(self, validated_data):
        """This serializer is only used to validate the data, not to create or update."""

    def update(self, instance, validated_data):
        """This serializer is only used to validate the data, not to create or update."""
