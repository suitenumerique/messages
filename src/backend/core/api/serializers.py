"""Client serializers for the messages core app."""

from django.db.models import Count, Exists, OuterRef, Q

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core import models


class UserSerializer(serializers.ModelSerializer):
    """Serialize users."""

    class Meta:
        model = models.User
        fields = ["id", "email", "full_name", "short_name"]
        read_only_fields = ["id", "email", "full_name", "short_name"]


class MailboxSerializer(serializers.ModelSerializer):
    """Serialize mailboxes."""

    email = serializers.SerializerMethodField(read_only=True)
    role = serializers.SerializerMethodField(read_only=True)
    count_unread_messages = serializers.SerializerMethodField(read_only=True)
    count_messages = serializers.SerializerMethodField(read_only=True)

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    def get_role(self, instance):
        """Return the allowed actions of the logged-in user on the instance."""
        request = self.context.get("request")
        if request:
            return instance.accesses.get(user=request.user).role
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


class ThreadAccessDetailSerializer(serializers.ModelSerializer):
    """Serializer for thread access details."""

    mailbox = MailboxLightSerializer()
    role = serializers.ChoiceField(choices=models.ThreadAccessRoleChoices.choices)

    class Meta:
        model = models.ThreadAccess
        fields = ["id", "mailbox", "role"]
        read_only_fields = fields


class ThreadSerializer(serializers.ModelSerializer):
    """Serialize threads."""

    messages = serializers.SerializerMethodField(read_only=True)
    sender_names = serializers.ListField(child=serializers.CharField(), read_only=True)
    user_role = serializers.SerializerMethodField()
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
                    return instance.accesses.get(mailbox=mailbox).role
                except models.ThreadAccess.DoesNotExist:
                    return None
        return None

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
        return LabelSerializer(labels, many=True).data

    class Meta:
        model = models.Thread
        fields = [
            "id",
            "subject",
            "snippet",
            "messages",
            "count_unread",
            "count_trashed",
            "count_draft",
            "count_starred",
            "count_sender",
            "count_messages",
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
        return instance.draft_body

    @extend_schema_field(AttachmentSerializer(many=True))
    def get_attachments(self, instance):
        """Return the parsed email attachments or linked attachments for drafts."""
        # First check for directly linked attachments (for drafts)
        if instance.attachments.exists():
            return AttachmentSerializer(instance.attachments.all(), many=True).data

        # Then get any parsed attachments from the email if available
        parsed_attachments = instance.get_parsed_field("attachments") or []

        # Convert parsed attachments to a format similar to AttachmentSerializer
        if parsed_attachments:
            return parsed_attachments

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
        ]
        read_only_fields = fields  # Mark all as read-only


class ThreadAccessSerializer(serializers.ModelSerializer):
    """Serialize thread access information."""

    class Meta:
        model = models.ThreadAccess
        fields = ["id", "thread", "mailbox", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class MailboxAccessReadSerializer(serializers.ModelSerializer):
    """Serialize mailbox access information for read operations with nested user details.
    Mailbox context is implied by the URL, so mailbox details are not included here.
    """

    user_details = UserSerializer(source="user", read_only=True)

    class Meta:
        model = models.MailboxAccess
        fields = ["id", "user_details", "role", "created_at", "updated_at"]
        read_only_fields = fields  # All fields are effectively read-only from this serializer's perspective


class MailboxAccessWriteSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating mailbox access records.
    Mailbox is set from the view based on URL parameters.
    """

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


class MailDomainAdminSerializer(serializers.ModelSerializer):
    """Serialize MailDomain basic information for admin listing."""

    class Meta:
        model = models.MailDomain
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = fields


class MailboxAccessNestedUserSerializer(serializers.ModelSerializer):
    """
    Serialize MailboxAccess for nesting within MailboxAdminSerializer.
    Shows user details and their role on the mailbox.
    """

    user = UserSerializer(read_only=True)

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
            "alias_of",  # show if it's an alias
            "accesses",  # List of users and their roles
            "created_at",
            "updated_at",
        ]
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
    """Serializer for importing EML or MBOX files via API."""

    import_file = serializers.FileField(
        help_text="Select an EML or MBOX file to import",
        required=True,
    )
    recipient = serializers.UUIDField(
        help_text="UUID of the recipient mailbox",
        required=True,
    )

    def validate_import_file(self, file):
        """Validate the import file."""
        if not file.name.endswith((".eml", ".mbox")):
            raise serializers.ValidationError(
                "File must be either an EML (.eml) or MBOX (.mbox) file"
            )
        return file


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


class LabelSerializer(serializers.ModelSerializer):
    """Serializer for Label model."""

    class Meta:
        model = models.Label
        fields = ["id", "name", "slug", "color", "mailbox", "threads"]
        read_only_fields = ["id", "slug"]
        extra_kwargs = {
            "mailbox": {"required": True},
            "name": {"required": True},
            "color": {"required": False, "default": "#000000"},
            "threads": {"required": False, "write_only": True},
        }

    def validate_name(self, value):
        """Validate label name format."""
        if not value:
            raise serializers.ValidationError("Label name is required")
        return value

    def validate_mailbox(self, value):
        """Validate that user has access to the mailbox."""
        user = self.context["request"].user
        if not value.accesses.filter(user=user).exists():
            raise serializers.ValidationError("You don't have access to this mailbox")
        return value
