"""Client serializers for the messages core app."""

from django.db.models import Count, Exists, OuterRef, Q

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from core import models


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
        """Exclude fields after class instanciation."""
        self.exclude_abilities = kwargs.pop("exclude_abilities", False)
        super().__init__(*args, **kwargs)

    def to_representation(self, instance):
        """Add abilities except when the serializer is nested."""
        representation = super().to_representation(instance)
        request = self.context.get("request")
        if request and not self.exclude_abilities:
            if isinstance(instance, models.User):
                representation["abilities"] = instance.get_abilities()
            else:
                representation["abilities"] = instance.get_abilities(request.user)
        return representation


class UserSerializer(AbilitiesModelSerializer):
    """Serialize users."""

    class Meta:
        model = models.User
        fields = ["id", "email", "full_name"]
        read_only_fields = ["id", "email", "full_name"]


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


class MailboxSerializer(serializers.ModelSerializer):
    """Serialize mailboxes."""

    email = serializers.SerializerMethodField(read_only=True)
    role = serializers.SerializerMethodField(read_only=True)
    count_unread_messages = serializers.SerializerMethodField(read_only=True)
    count_messages = serializers.SerializerMethodField(read_only=True)

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    @extend_schema_field(IntegerChoicesField(choices_class=models.MailboxRoleChoices))
    def get_role(self, instance):
        """Return the allowed actions of the logged-in user on the instance."""
        request = self.context.get("request")
        if request:
            role_enum = models.MailboxRoleChoices(
                instance.accesses.get(user=request.user).role
            )
            return role_enum.label
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

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "role", "count_unread_messages", "count_messages"]


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

    def get_sha256(self, obj):
        """Convert binary SHA256 to hex string."""
        return obj.sha256.hex() if obj.sha256 else None

    class Meta:
        model = models.Attachment
        fields = [
            "id",
            "blobId",
            "name",
            "size",
            "type",
            "sha256",
            "created_at",
        ]
        read_only_fields = fields


class ThreadLabelSerializer(serializers.ModelSerializer):
    """Serializer to get labels details for a thread."""

    display_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Label
        fields = ["id", "name", "slug", "color", "display_name"]
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

    class Meta:
        model = models.Label
        fields = ["id", "name", "slug", "color", "display_name", "children"]
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
        fields = ["id", "name", "slug", "color", "mailbox", "threads"]
        read_only_fields = ["id", "slug"]

    def validate_mailbox(self, value):
        """Validate that user has access to the mailbox."""
        user = self.context["request"].user
        if not value.accesses.filter(
            user=user,
            role__in=[
                models.MailboxRoleChoices.ADMIN,
                models.MailboxRoleChoices.EDITOR,
                models.MailboxRoleChoices.SENDER,
            ],
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
        ]
        read_only_fields = fields  # Mark all as read-only for safety


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

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_textBody(self, instance):  # pylint: disable=invalid-name
        """Return the list of text body parts (JMAP style)."""
        return instance.get_parsed_field("textBody") or []

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_htmlBody(self, instance):  # pylint: disable=invalid-name
        """Return the list of HTML body parts (JMAP style)."""
        return instance.get_parsed_field("htmlBody") or []

    @extend_schema_field(serializers.CharField())
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
                    }
                )
            return stripped_attachments

        return []

    @extend_schema_field(ContactSerializer(many=True))
    def get_to(self, instance):
        """Return the 'To' recipients."""
        contacts = models.Contact.objects.filter(
            id__in=instance.recipients.filter(
                type=models.MessageRecipientTypeChoices.TO
            ).values_list("contact", flat=True)
        )
        return ContactSerializer(contacts, many=True).data

    @extend_schema_field(ContactSerializer(many=True))
    def get_cc(self, instance):
        """Return the 'Cc' recipients."""
        contacts = models.Contact.objects.filter(
            id__in=instance.recipients.filter(
                type=models.MessageRecipientTypeChoices.CC
            ).values_list("contact", flat=True)
        )
        return ContactSerializer(contacts, many=True).data

    @extend_schema_field(ContactSerializer(many=True))
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
                role=models.ThreadAccessRoleChoices.EDITOR,
            ).exists()
        ):
            contacts = models.Contact.objects.filter(
                id__in=instance.recipients.filter(
                    type=models.MessageRecipientTypeChoices.BCC
                ).values_list("contact", flat=True)
            )
            return ContactSerializer(contacts, many=True).data
        return []  # Hide Bcc by default

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
            "has_attachments",
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

    user_details = UserSerializer(source="user", read_only=True, exclude_abilities=True)
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


class MailboxAccessNestedUserSerializer(serializers.ModelSerializer):
    """
    Serialize MailboxAccess for nesting within MailboxAdminSerializer.
    Shows user details and their role on the mailbox.
    """

    user = UserSerializer(read_only=True, exclude_abilities=True)
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
        ]
        read_only_fields = fields


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

    blob = serializers.UUIDField(
        help_text="UUID of the blob",
        required=True,
    )

    recipient = serializers.UUIDField(
        help_text="UUID of the recipient mailbox",
        required=True,
    )


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
    folder = serializers.CharField(
        help_text="IMAP folder to import from", required=False, default="INBOX"
    )
    max_messages = serializers.IntegerField(
        help_text="Maximum number of messages to import (0 for all)",
        required=False,
        default=0,
        min_value=0,
    )
