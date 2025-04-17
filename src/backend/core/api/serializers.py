"""Client serializers for the messages core app."""

from django.db.models import Q

from drf_spectacular.utils import extend_schema_field
from rest_framework import exceptions, serializers

from core import models


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

    class Meta:
        model = models.Mailbox
        fields = ["id", "email", "perms"]


class ContactSerializer(serializers.ModelSerializer):
    """Serialize contacts."""

    class Meta:
        model = models.Contact
        fields = ["id", "name", "email"]


class ThreadSerializer(serializers.ModelSerializer):
    """Serialize threads."""

    messages = serializers.SerializerMethodField(read_only=True)
    is_read = serializers.SerializerMethodField(read_only=True)
    recipients = serializers.SerializerMethodField(read_only=True)

    def get_messages(self, instance):
        """Return the messages in the thread."""
        return [str(message.id) for message in instance.messages.all()]

    def get_is_read(self, instance) -> bool:
        """Return the read status of the thread."""
        return instance.messages.filter(read_at__isnull=False).exists()

    @extend_schema_field(ContactSerializer(many=True))
    def get_recipients(self, instance):
        """Return the recipients of the thread."""
        contacts = models.Contact.objects.filter(
            id__in=instance.messages.values_list("recipients__contact__id", flat=True)
        )
        return ContactSerializer(contacts, many=True).data

    class Meta:
        model = models.Thread
        fields = [
            "id",
            "subject",
            "snippet",
            "recipients",
            "messages",
            "is_read",
            "updated_at",
        ]


class MessageRecipientSerializer(serializers.ModelSerializer):
    """Serialize message recipients."""

    contact = ContactSerializer(read_only=True)

    class Meta:
        model = models.MessageRecipient
        fields = ["id", "contact", "type"]


class MessageSerializer(serializers.ModelSerializer):
    """Serialize messages."""

    raw_html_body = serializers.SerializerMethodField(read_only=True)
    raw_text_body = serializers.SerializerMethodField(read_only=True)
    sender = serializers.SerializerMethodField(read_only=True)
    recipients = serializers.SerializerMethodField(read_only=True)
    is_read = serializers.SerializerMethodField(read_only=True)

    def get_raw_html_body(self, instance):
        """Return the raw HTML body of the message."""
        return instance.body_html

    def get_raw_text_body(self, instance):
        """Return the raw text body of the message."""
        return instance.body_text

    @extend_schema_field(ContactSerializer)
    def get_sender(self, instance):
        """Return the sender of the message."""
        return ContactSerializer(instance.sender).data

    @extend_schema_field(MessageRecipientSerializer(many=True))
    def get_recipients(self, instance):
        """Return the recipients of the message."""
        return MessageRecipientSerializer(instance.recipients, many=True).data

    def get_is_read(self, instance) -> bool:
        """Return the read status of the message."""
        return instance.read_at is not None

    class Meta:
        model = models.Message
        fields = [
            "id",
            "subject",
            "received_at",
            "created_at",
            "updated_at",
            "raw_html_body",
            "raw_text_body",
            "sender",
            "recipients",
            "is_read",
        ]
