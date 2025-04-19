"""Client serializers for the messages core app."""

import logging

from django.db.models import Count, Q

from drf_spectacular.utils import extend_schema_field
from rest_framework import exceptions, serializers

from core import models
from core.formats.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)


class UserSerializer(serializers.ModelSerializer):
    """Serialize users."""

    class Meta:
        model = models.User
        fields = ["id", "email", "full_name", "short_name"]
        read_only_fields = ["id", "email", "full_name", "short_name"]


class BaseAccessSerializer(serializers.ModelSerializer):
    """Serialize template accesses."""

    abilities = serializers.SerializerMethodField(read_only=True)

    def update(self, instance, validated_data):
        """Make "user" field is readonly but only on update."""
        validated_data.pop("user", None)
        return super().update(instance, validated_data)

    def get_abilities(self, access) -> dict:
        """Return abilities of the logged-in user on the instance."""
        request = self.context.get("request")
        if request:
            return access.get_abilities(request.user)
        return {}

    def validate(self, attrs):
        """
        Check access rights specific to writing (create/update)
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        role = attrs.get("role")

        # Update
        if self.instance:
            can_set_role_to = self.instance.get_abilities(user)["set_role_to"]

            if role and role not in can_set_role_to:
                message = (
                    f"You are only allowed to set role to {', '.join(can_set_role_to)}"
                    if can_set_role_to
                    else "You are not allowed to set this role for this template."
                )
                raise exceptions.PermissionDenied(message)

        # Create
        else:
            try:
                resource_id = self.context["resource_id"]
            except KeyError as exc:
                raise exceptions.ValidationError(
                    "You must set a resource ID in kwargs to create a new access."
                ) from exc

            if not self.Meta.model.objects.filter(  # pylint: disable=no-member
                Q(user=user) | Q(team__in=user.teams),
                role__in=[models.RoleChoices.OWNER, models.RoleChoices.ADMIN],
                **{self.Meta.resource_field_name: resource_id},  # pylint: disable=no-member
            ).exists():
                raise exceptions.PermissionDenied(
                    "You are not allowed to manage accesses for this resource."
                )

            if (
                role == models.RoleChoices.OWNER
                and not self.Meta.model.objects.filter(  # pylint: disable=no-member
                    Q(user=user) | Q(team__in=user.teams),
                    role=models.RoleChoices.OWNER,
                    **{self.Meta.resource_field_name: resource_id},  # pylint: disable=no-member
                ).exists()
            ):
                raise exceptions.PermissionDenied(
                    "Only owners of a resource can assign other users as owners."
                )

        # pylint: disable=no-member
        attrs[f"{self.Meta.resource_field_name}_id"] = self.context["resource_id"]
        return attrs


class MailboxSerializer(serializers.ModelSerializer):
    """Serialize mailboxes."""

    email = serializers.SerializerMethodField(read_only=True)
    perms = serializers.SerializerMethodField(read_only=True)
    count_unread_messages = serializers.SerializerMethodField(read_only=True)
    count_messages = serializers.SerializerMethodField(read_only=True)

    def get_email(self, instance):
        """Return the email of the mailbox."""
        return str(instance)

    def get_perms(self, instance):
        """Return the allowed actions of the logged-in user on the instance."""
        request = self.context.get("request")
        if request:
            return list(
                instance.accesses.filter(user=request.user).values_list(
                    "permission", flat=True
                )
            )
        return []

    def get_count_unread_messages(self, instance):
        """Return the number of unread messages in the mailbox."""
        return instance.threads.aggregate(
            total=Count("messages", filter=Q(messages__read_at__isnull=True))
        )["total"]

    def get_count_messages(self, instance):
        """Return the number of messages in the mailbox."""
        return instance.threads.aggregate(total=Count("messages"))["total"]

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "perms", "count_unread_messages", "count_messages"]


class ContactSerializer(serializers.ModelSerializer):
    """Serialize contacts."""

    class Meta:
        model = models.Contact
        fields = ["id", "name", "email"]


class ThreadSerializer(serializers.ModelSerializer):
    """Serialize threads."""

    messages = serializers.SerializerMethodField(read_only=True)
    is_read = serializers.SerializerMethodField(read_only=True)

    def get_messages(self, instance):
        """Return the messages in the thread."""
        return [str(message.id) for message in instance.messages.all()]

    def get_is_read(self, instance) -> bool:
        """Return the read status of the thread."""
        return instance.messages.filter(read_at__isnull=False).exists()

    class Meta:
        model = models.Thread
        fields = [
            "id",
            "subject",
            "snippet",
            "messages",
            "is_read",
            "updated_at",
        ]


class MessageSerializer(serializers.ModelSerializer):
    """
    Serialize messages, parsing details from raw_mime on demand.
    Aligns field names with JMAP where appropriate (textBody, htmlBody, to, cc, bcc).
    """

    # JMAP-style body fields (parsed from raw_mime)
    textBody = serializers.SerializerMethodField(read_only=True)
    htmlBody = serializers.SerializerMethodField(read_only=True)

    # JMAP-style recipient fields (parsed from raw_mime)
    to = serializers.SerializerMethodField(read_only=True)
    cc = serializers.SerializerMethodField(read_only=True)
    bcc = serializers.SerializerMethodField(read_only=True)

    # Keep sender (denormalized in model)
    sender = ContactSerializer(read_only=True)
    # Keep is_read (calculated or from model field)
    is_read = serializers.SerializerMethodField(read_only=True)
    # Keep messageId (denormalized - though could also be parsed)
    # Consider adding messageId if needed, parsed from raw_mime or a dedicated model field
    # messageId = serializers.SerializerMethodField(read_only=True)

    def _get_parsed_email(self, instance):
        """
        Helper to parse raw_mime once per instance for the current serialization context.
        Caches the result directly on the model instance using a temporary attribute.
        """
        # Check if already parsed and cached on the instance for this context
        if hasattr(instance, "_parsed_email_cache"):
            return instance._parsed_email_cache

        parsed_email = None
        if instance.raw_mime:  # raw_mime is BinaryField, should be bytes
            try:
                # Directly parse bytes from BinaryField
                parsed_email = parse_email_message(instance.raw_mime)
            except EmailParseError as e:
                logger.error(
                    f"Failed to parse raw_mime for Message {instance.id} during serialization: {e}"
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error parsing raw_mime for Message {instance.id}: {e}",
                    exc_info=True,
                )

        # Store the parsed result (or None) on the instance using a non-persistent attribute
        # This cache only lives as long as this instance object within the current request/process
        instance._parsed_email_cache = parsed_email
        return parsed_email

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_textBody(self, instance):
        """Return the list of text body parts (JMAP style)."""
        parsed = self._get_parsed_email(instance)
        return parsed.get("textBody", []) if parsed else []

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_htmlBody(self, instance):
        """Return the list of HTML body parts (JMAP style)."""
        parsed = self._get_parsed_email(instance)
        return parsed.get("htmlBody", []) if parsed else []

    @extend_schema_field(ContactSerializer(many=True))
    def get_to(self, instance):
        """Return the 'To' recipients."""
        parsed = self._get_parsed_email(instance)
        return parsed.get("to", []) if parsed else []

    @extend_schema_field(ContactSerializer(many=True))
    def get_cc(self, instance):
        """Return the 'Cc' recipients."""
        parsed = self._get_parsed_email(instance)
        return parsed.get("cc", []) if parsed else []

    @extend_schema_field(ContactSerializer(many=True))
    def get_bcc(self, instance):
        """Return the 'Bcc' recipients."""
        parsed = self._get_parsed_email(instance)
        return parsed.get("bcc", []) if parsed else []

    def get_is_read(self, instance) -> bool:
        """Return the read status of the message."""
        # Uses the denormalized is_read field from the model
        return instance.is_read

    class Meta:
        model = models.Message
        fields = [
            "id",
            "subject",  # Denormalized from model
            "received_at",  # Denormalized from model
            "created_at",  # From model
            "updated_at",  # From model
            "textBody",  # Parsed from raw_mime
            "htmlBody",  # Parsed from raw_mime
            "sender",  # Denormalized from model (Contact relation)
            "to",  # Parsed from raw_mime
            "cc",  # Parsed from raw_mime
            "bcc",  # Parsed from raw_mime
            "is_read",  # Denormalized from model
            # Add other desired fields from models.Message here if needed
        ]
        # Ensure read_only_fields are correctly specified if needed,
        # although SerializerMethodFields are read-only by default.
        # read_only_fields = ["id", "created_at", "updated_at", "received_at", "sender", "is_read", ...]
