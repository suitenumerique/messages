# pylint: disable=too-many-lines
"""Client serializers for the messages core app."""
# pylint: disable=too-many-lines

import json

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from core import enums, models


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
    count_unread_messages = serializers.SerializerMethodField(read_only=True)
    count_messages = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "role", "count_unread_messages", "count_messages"]

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

    def get_count_unread_messages(self, instance):
        """Return the number of unread messages in the mailbox."""
        return instance.thread_accesses.aggregate(
            total=Count(
                "thread__messages", filter=Q(thread__messages__read_at__isnull=True)
            )
        )["total"]

    def get_count_messages(self, instance):
        """Return the number of messages in the mailbox."""
        return instance.thread_accesses.aggregate(total=Count("thread__messages"))[
            "total"
        ]

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


class ReadOnlyMessageTemplateSerializer(serializers.ModelSerializer):
    """Serialize message templates for read-only operations."""

    type = IntegerChoicesField(choices_class=enums.MessageTemplateTypeChoices)
    html_body = serializers.SerializerMethodField()
    text_body = serializers.SerializerMethodField()
    raw_body = serializers.SerializerMethodField()

    def get_html_body(self, obj) -> str:
        """Get HTML body from blob."""
        return obj.html_body

    def get_text_body(self, obj) -> str:
        """Get text body from content blob."""
        return obj.text_body

    def get_raw_body(self, obj) -> str | None:
        """Get raw blob from content blob."""
        return obj.raw_body

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


class LabelSerializer(serializers.ModelSerializer):
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
        fields = ["id", "mailbox", "role"]
        read_only_fields = fields


class ThreadSerializer(serializers.ModelSerializer):
    """Serialize threads."""

    messages = serializers.SerializerMethodField(read_only=True)
    sender_names = serializers.ListField(child=serializers.CharField(), read_only=True)
    user_role = serializers.SerializerMethodField(read_only=True)
    accesses = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    summary = serializers.CharField(read_only=True)

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
        """Get current user's role for this thread."""
        request = self.context.get("request")
        mailbox_id = request.query_params.get("mailbox_id")
        if mailbox_id:
            try:
                mailbox = models.Mailbox.objects.get(id=mailbox_id)
            except models.Mailbox.DoesNotExist:
                return None
            if request and hasattr(request, "user") and request.user.is_authenticated:
                try:
                    role_value = instance.accesses.get(mailbox=mailbox).role
                    role_enum = models.ThreadAccessRoleChoices(role_value)
                    return role_enum.label
                except models.ThreadAccess.DoesNotExist:
                    return None
        return None

    @extend_schema_field(ThreadLabelSerializer(many=True))
    def get_labels(self, instance):
        """Get labels for the thread, filtered by user's mailbox access."""
        request = self.context.get("request")
        if not request or not hasattr(request, "user"):
            return []

        labels = instance.labels.filter(
            Exists(
                models.MailboxAccess.objects.filter(
                    mailbox=OuterRef("mailbox"),
                    user=request.user,
                )
            )
        ).distinct()
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
            "is_spam",
            "has_active",
            "messaged_at",
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

    # UUID of the parent message
    parent_id = serializers.UUIDField(
        source="parent.id", allow_null=True, read_only=True
    )

    # UUID of the thread
    thread_id = serializers.UUIDField(
        source="thread.id", allow_null=True, read_only=True
    )

    signature = ReadOnlyMessageTemplateSerializer(read_only=True, allow_null=True)
    stmsg_headers = serializers.SerializerMethodField(read_only=True)

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
            "to",
            "cc",
            "bcc",
            "read_at",
            "sent_at",
            "is_sender",
            "is_draft",
            "is_unread",
            "is_starred",
            "is_trashed",
            "is_archived",
            "has_attachments",
            "signature",
            "stmsg_headers",
        ]
        read_only_fields = fields  # Mark all as read-only


class ThreadAccessSerializer(serializers.ModelSerializer):
    """Serialize thread access information."""

    role = IntegerChoicesField(choices_class=models.ThreadAccessRoleChoices)

    class Meta:
        model = models.ThreadAccess
        fields = ["id", "thread", "mailbox", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


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
        fields = ["id", "name", "created_at", "updated_at", "expected_dns_records"]
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
        """Validate the domain of the mailbox."""
        if not self.context.get("domain"):
            raise serializers.ValidationError(
                "Domain is required in serializer context."
            )

        return super().validate(attrs)

    def validate_local_part(self, value):
        """Validate the local part of the mailbox."""
        if models.Mailbox.objects.filter(
            domain=self.context.get("domain"), local_part=value
        ).exists():
            raise serializers.ValidationError(
                _("A mailbox with this local part already exists in this domain.")
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
            raise serializers.ValidationError("Only EML and MBOX files are supported.")
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


class ChannelSerializer(AbilitiesModelSerializer):
    """Serialize Channel model."""

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
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        """Validate channel data."""
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

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

        return super().validate(attrs)

    def create(self, validated_data):
        """Create template with relationships and ensure atomic content creation."""
        html_body = validated_data.pop("html_body", "")
        text_body = validated_data.pop("text_body", "")
        raw_body = validated_data.pop("raw_body", "")
        validated_data["maildomain"] = self.context.get("domain")
        validated_data["mailbox"] = self.context.get("mailbox")

        # Use atomic transaction to ensure all content fields are created together
        with transaction.atomic():
            # Create content blob with all content
            # Parse raw_body if it's a JSON string
            try:
                raw_body = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError as err:
                raise serializers.ValidationError(
                    {"raw_body": f"Invalid JSON: {err.msg}"}
                ) from err

            content = json.dumps(
                {"html": html_body, "text": text_body, "raw": raw_body},
                separators=(",", ":"),
            )
            # content is changed to bytes
            blob = models.Blob.objects.create_blob(
                content=content.encode("utf-8"),
                content_type="application/json",
                maildomain=self.context.get("domain"),
                mailbox=self.context.get("mailbox"),
            )
            validated_data["blob"] = blob
            template = super().create(validated_data)
            return template

    def update(self, instance, validated_data):
        """Update template with relationships. Not allowed to change mailbox or maildomain."""
        html_body = validated_data.pop("html_body", None)
        text_body = validated_data.pop("text_body", None)
        raw_body = validated_data.pop("raw_body", None)

        # Use atomic transaction to ensure all content fields are updated together
        with transaction.atomic():
            # Update content blob if any content field is provided
            if any(field is not None for field in [html_body, text_body, raw_body]):
                # Delete old blob
                if instance.blob:
                    instance.blob.delete()
                # Create content for new blob
                content = {
                    "html": html_body,
                    "text": text_body,
                    "raw": json.loads(raw_body) if raw_body else None,
                }
                # Create new blob
                blob = models.Blob.objects.create_blob(
                    content=json.dumps(content, separators=(",", ":")).encode("utf-8"),
                    content_type="application/json",
                    maildomain=self.instance.maildomain
                    if self.instance.maildomain
                    else None,
                    mailbox=self.instance.mailbox if self.instance.mailbox else None,
                )
                validated_data["blob"] = blob

            # Update all fields atomically
            template = super().update(instance, validated_data)
            return template


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
