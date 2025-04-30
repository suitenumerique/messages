"""Client serializers for the messages core app."""

from django.db.models import Count, Q

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
    sender_names = serializers.ListField(child=serializers.CharField(), read_only=True)

    def get_messages(self, instance):
        """Return the messages in the thread."""
        return [str(message.id) for message in instance.messages.order_by("created_at")]

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
        ]


class MessageSerializer(serializers.ModelSerializer):
    """
    Serialize messages, getting parsed details from the Message model.
    Aligns field names with JMAP where appropriate (textBody, htmlBody, to, cc, bcc).
    """

    # JMAP-style body fields (from model's parsed data)
    textBody = serializers.SerializerMethodField(read_only=True)
    htmlBody = serializers.SerializerMethodField(read_only=True)
    draftBody = serializers.SerializerMethodField(read_only=True)

    # JMAP-style recipient fields (from model's parsed data)
    to = serializers.SerializerMethodField(read_only=True)
    cc = serializers.SerializerMethodField(read_only=True)
    bcc = serializers.SerializerMethodField(read_only=True)

    sender = ContactSerializer(read_only=True)

    # UUID of the parent message
    parent_id = serializers.UUIDField(
        source="parent.id", read_only=True, allow_null=True
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
        if (
            request
            and isinstance(self.instance, models.Message)
            and self.instance.thread.mailbox.accesses.filter(user=request.user).exists()
            and self.instance.is_sender
        ):
            contacts = models.Contact.objects.filter(
                id__in=instance.recipients.filter(
                    type=models.MessageRecipientTypeChoices.BCC
                ).values_list("contact", flat=True)
            )
            return ContactSerializer(contacts, many=True).data
        return []

    class Meta:
        model = models.Message
        fields = [
            "id",
            "thread",
            "parent_id",
            "subject",
            "created_at",
            "updated_at",
            "htmlBody",
            "textBody",
            "draftBody",
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
