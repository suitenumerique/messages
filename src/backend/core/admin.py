"""Admin classes and registrations for core app."""

from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.core.files.storage import storages
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import escape, format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.api.utils import get_file_key
from core.mda.outbound_tasks import retry_messages_task
from core.services.dns.provisioning import provision_domain_dns
from core.services.importer.service import ImportService

from . import models
from .enums import MessageDeliveryStatusChoices
from .forms import IMAPImportForm, MessageImportForm


class RecipientDeliveryStatusFilter(admin.SimpleListFilter):
    """Filter messages by their recipients' delivery status."""

    title = _("delivery status")
    parameter_name = "recipient_delivery_status"

    def lookups(self, request, model_admin):
        """Return a list of delivery status choices."""
        return MessageDeliveryStatusChoices.choices

    def queryset(self, request, queryset):
        """Filter queryset by recipient delivery status."""
        if self.value():
            return queryset.filter(
                recipients__delivery_status=int(self.value())
            ).distinct()
        return queryset


def reset_keycloak_password_action(_, request, queryset):
    """Admin action to reset Keycloak passwords for selected mailboxes."""
    success_count = 0
    error_count = 0

    for mailbox in queryset:
        if not mailbox.domain.identity_sync:
            messages.warning(
                request,
                f"Skipped {mailbox} - identity sync not enabled for domain {mailbox.domain.name}",
            )
            continue

        try:
            new_password = mailbox.reset_password()
            messages.success(
                request,
                f"Password reset for {mailbox}. New temporary password: {new_password}",
            )
            success_count += 1

        # pylint: disable=broad-except
        except Exception as e:
            messages.error(request, f"Failed to reset password for {mailbox}: {str(e)}")
            error_count += 1

    if success_count > 0:
        messages.info(request, f"Successfully reset {success_count} password(s)")
    if error_count > 0:
        messages.warning(request, f"Failed to reset {error_count} password(s)")


reset_keycloak_password_action.short_description = (
    "Reset Keycloak password for selected mailboxes"
)


def retry_send_messages_action(__, request, queryset):
    """Admin action to retry sending selected messages with retryable recipients."""
    message_ids = [
        str(message_id) for message_id in queryset.values_list("id", flat=True)
    ]
    task = retry_messages_task.delay(message_ids=message_ids)

    messages.info(
        request,
        _(
            "%(message_count)d messages - "
            "Retry send message task queued (id: %(task_id)s)."
        )
        % {
            "message_count": len(message_ids),
            "task_id": task.id,
        },
    )


retry_send_messages_action.short_description = _(
    "Retry to send selected messages to pending recipients"
)


@admin.register(models.User)
class UserAdmin(auth_admin.UserAdmin):
    """Admin class for the User model"""

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "admin_email",
                    "password",
                )
            },
        ),
        (
            _("Personal info"),
            {
                "fields": (
                    "sub",
                    "email",
                    "full_name",
                    "language",
                    "timezone",
                    "custom_attributes",
                )
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    list_display = (
        "id",
        "sub",
        "full_name",
        "admin_email",
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")
    ordering = (
        "is_active",
        "-is_superuser",
        "-is_staff",
        "-updated_at",
        "full_name",
    )
    readonly_fields = (
        "id",
        "sub",
        "email",
        "created_at",
        "updated_at",
    )
    search_fields = ("id", "sub", "admin_email", "email", "full_name")


class MailDomainAccessInline(admin.TabularInline):
    """Inline class for the MailDomainAccess model"""

    model = models.MailDomainAccess
    autocomplete_fields = ("user",)


@admin.register(models.MailDomain)
class MailDomainAdmin(admin.ModelAdmin):
    """Admin class for the MailDomain model"""

    inlines = [MailDomainAccessInline]
    list_display = (
        "name",
        "identity_sync",
        "created_at",
        "updated_at",
    )
    list_filter = ("identity_sync",)
    search_fields = ("name",)
    autocomplete_fields = ("alias_of",)
    change_form_template = "admin/core/maildomain/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/dns-provision/",
                self.admin_site.admin_view(self.dns_provision_view),
                name="core_maildomain_dns_provision",
            ),
        ]
        return custom_urls + urls

    def dns_provision_view(self, request, object_id):
        """View for provisioning DNS records for a mail domain."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        maildomain = self.get_object(request, object_id)

        if maildomain is None:
            messages.error(request, _("Mail domain not found."))
            return redirect("..")

        # Run DNS provisioning
        results = provision_domain_dns(maildomain)

        if results["success"]:
            provider_used = results.get("provider", "unknown")
            changes = results.get("changes", [])
            if changes:
                changes_text = ", ".join(changes)
                messages.success(
                    request,
                    _("DNS provisioning successful via %(provider)s: %(changes)s")
                    % {"provider": provider_used, "changes": changes_text},
                )
            else:
                messages.success(
                    request,
                    _(
                        "DNS provisioning successful via %(provider)s (no changes needed)."
                    )
                    % {"provider": provider_used},
                )
        else:
            error_msg = results.get("error", "Unknown error")
            messages.error(
                request,
                _("DNS provisioning failed: %(error)s") % {"error": error_msg},
            )

        return redirect("..")


class MailboxAccessInline(admin.TabularInline):
    """Inline class for the MailboxAccess model"""

    model = models.MailboxAccess
    autocomplete_fields = ("user",)


@admin.register(models.Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    """Admin class for the Mailbox model"""

    inlines = [MailboxAccessInline]
    list_display = ("__str__", "is_identity", "contact", "alias_of", "updated_at")
    list_filter = ("is_identity", "created_at", "updated_at")
    search_fields = ("local_part", "domain__name", "contact__name", "contact__email")
    actions = [reset_keycloak_password_action]
    autocomplete_fields = ("domain", "contact", "alias_of")

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance"""
        return (
            super()
            .get_queryset(request)
            .select_related("domain", "contact", "alias_of")
        )


@admin.register(models.Channel)
class ChannelAdmin(admin.ModelAdmin):
    """Admin class for the Channel model"""

    list_display = ("name", "type", "mailbox", "maildomain", "created_at")
    list_filter = ("type", "created_at")
    search_fields = ("name", "type")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("mailbox", "maildomain")

    fieldsets = (
        (None, {"fields": ("name", "type", "settings")}),
        (
            "Target",
            {
                "fields": ("mailbox", "maildomain"),
                "description": "Specify either a mailbox or maildomain, but not both.",
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(models.MailboxAccess)
class MailboxAccessAdmin(admin.ModelAdmin):
    """Admin class for the MailboxAccess model"""

    list_display = ("id", "mailbox", "user", "role")
    search_fields = ("mailbox__local_part", "mailbox__domain__name", "user__email")
    autocomplete_fields = ("mailbox", "user")


class ThreadAccessInline(admin.TabularInline):
    """Inline class for the ThreadAccess model"""

    model = models.ThreadAccess
    autocomplete_fields = ("mailbox",)


@admin.register(models.Thread)
class ThreadAdmin(admin.ModelAdmin):
    """Admin class for the Thread model"""

    inlines = [ThreadAccessInline]
    list_display = (
        "id",
        "subject",
        "snippet",
        "get_labels",
        "messaged_at",
        "created_at",
        "updated_at",
    )
    search_fields = ("subject", "snippet", "labels__name")
    list_filter = (
        "has_unread",
        "has_trashed",
        "has_archived",
        "has_draft",
        "has_starred",
        "has_sender",
        "has_attachments",
        "is_spam",
        "created_at",
    )
    fieldsets = (
        (None, {"fields": ("subject", "snippet", "display_labels", "summary")}),
        (
            _("Statistics"),
            {
                "fields": (
                    "has_unread",
                    "has_trashed",
                    "has_archived",
                    "has_draft",
                    "has_starred",
                    "has_sender",
                    "has_messages",
                    "has_attachments",
                    "is_spam",
                    "has_active",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("sender_names", "created_at", "updated_at", "messaged_at"),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "display_labels",
        "has_unread",
        "has_trashed",
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
        "created_at",
        "updated_at",
    )

    def get_labels(self, obj):
        """Return a comma-separated list of labels for the thread."""
        return ", ".join(label.name for label in obj.labels.all())

    get_labels.short_description = _("Labels")
    get_labels.admin_order_field = "labels__name"

    def display_labels(self, obj):
        """Display labels with their colors in the detail view."""
        if not obj.labels.exists():
            return _("No labels")

        # Create a list of formatted label spans
        label_spans = []
        for label in obj.labels.all():
            # Create each label span using format_html
            label_span = format_html(
                '<span style="display: inline-block; padding: 2px 8px; margin: 2px; '
                'border-radius: 3px; background-color: {}; color: white;">{}</span>',
                label.color,
                escape(label.name),
            )
            label_spans.append(label_span)

        # Join all spans with a space using format_html
        return format_html(" ".join(label_spans))

    display_labels.short_description = _("Labels")


class MessageRecipientInline(admin.TabularInline):
    """Inline class for the MessageRecipient model"""

    model = models.MessageRecipient
    autocomplete_fields = ("contact",)
    fields = (
        "contact",
        "type",
        "delivery_status",
        "delivery_message",
        "delivered_at",
        "retry_count",
    )


@admin.register(models.Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Admin class for the Attachment model"""

    list_display = ("id", "name", "mailbox", "created_at")
    search_fields = ("name", "mailbox__local_part", "mailbox__domain__name")
    autocomplete_fields = ("mailbox",)
    raw_id_fields = ("blob", "messages")


class AttachmentInline(admin.TabularInline):
    """Inline class for the Attachment model"""

    model = models.Attachment.messages.through
    raw_id_fields = ("attachment",)


@admin.register(models.Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin class for the Message model"""

    inlines = [MessageRecipientInline, AttachmentInline]
    actions = [retry_send_messages_action]
    list_display = (
        "id",
        "subject",
        "sender",
        "is_sender",
        "is_draft",
        "is_unread",
        "has_attachments",
        "created_at",
        "sent_at",
    )
    list_filter = (
        "is_sender",
        "is_draft",
        "is_starred",
        "is_trashed",
        "is_unread",
        "is_spam",
        "is_archived",
        "has_attachments",
        RecipientDeliveryStatusFilter,
        "created_at",
        "sent_at",
        "read_at",
        "archived_at",
        "trashed_at",
    )
    search_fields = ("subject", "sender__name", "sender__email", "mime_id")
    change_list_template = "admin/core/message/change_list.html"
    change_form_template = "admin/core/message/change_form.html"
    raw_id_fields = ("thread", "blob", "draft_blob", "parent", "channel")
    autocomplete_fields = ("sender", "signature")
    readonly_fields = ("mime_id", "created_at", "updated_at")

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance"""
        return super().get_queryset(request).select_related("sender", "thread")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import-messages/",
                self.admin_site.admin_view(self.import_messages_view),
                name="core_message_import_messages",
            ),
            path(
                "import-imap/",
                self.admin_site.admin_view(self.import_imap_view),
                name="core_message_import_imap",
            ),
            path(
                "<path:object_id>/retry/",
                self.admin_site.admin_view(self.retry_message_view),
                name="core_message_retry",
            ),
        ]
        return custom_urls + urls

    def import_messages_view(self, request):
        """View for importing EML or MBOX files."""
        if request.method == "POST":
            form = MessageImportForm(request.POST, request.FILES)
            if form.is_valid():
                import_file = request.FILES["import_file"]
                recipient = form.cleaned_data["recipient"]

                # Create a Blob from the uploaded file
                file_content = import_file.read()
                storage = storages["message-imports"]
                s3_client = storage.connection.meta.client
                file_key = get_file_key(recipient.id, import_file.name)
                s3_client.put_object(
                    Bucket=storage.bucket_name,
                    Key=file_key,
                    Body=file_content,
                    ContentType=import_file.content_type,
                )

                success, _response_data = ImportService.import_file(
                    file_key=file_key,
                    recipient=recipient,
                    user=request.user,
                    request=request,
                )
                if success:
                    return redirect("..")
        else:
            form = MessageImportForm()

        context = dict(
            self.admin_site.each_context(request),
            title=_("Import Messages"),
            form=form,
            opts=self.model._meta,  # noqa: SLF001
        )
        return TemplateResponse(
            request, "admin/core/message/import_messages.html", context
        )

    def import_imap_view(self, request):
        """View for importing messages from IMAP server."""
        if request.method == "POST":
            form = IMAPImportForm(request.POST)
            if form.is_valid():
                success, _response_data = ImportService.import_imap(
                    imap_server=form.cleaned_data["imap_server"],
                    imap_port=form.cleaned_data["imap_port"],
                    username=form.cleaned_data["username"],
                    password=form.cleaned_data["password"],
                    recipient=form.cleaned_data["recipient"],
                    user=request.user,
                    use_ssl=form.cleaned_data["use_ssl"],
                    request=request,
                )
                if success:
                    return redirect("..")
        else:
            form = IMAPImportForm()

        context = dict(
            self.admin_site.each_context(request),
            title=_("Import Messages from IMAP"),
            form=form,
            opts=self.model._meta,  # noqa: SLF001
        )
        return TemplateResponse(
            request,
            "admin/core/message/import_imap.html",
            context,
        )

    def changelist_view(self, request, extra_context=None):
        """Add import permission to the changelist context."""
        extra_context = extra_context or {}
        extra_context["has_import_permission"] = self.has_add_permission(request)
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Add retry availability context to the change form."""
        context = extra_context.copy() if extra_context else {}

        try:
            message = self.get_object(request, object_id)
            if message.is_draft is False and message.is_sender is True:
                # Check if message has recipients with retry status
                has_retryable_recipients = message.recipients.filter(
                    Q(delivery_status=MessageDeliveryStatusChoices.RETRY)
                    | Q(delivery_status__isnull=True)
                ).exists()
                context["has_retryable_recipients"] = has_retryable_recipients
        except Exception:  # pylint: disable=broad-except
            context["has_retryable_recipients"] = False

        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context=context,
        )

    def retry_message_view(self, request, object_id):
        """View for retrying to send a message to recipients with retry status."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        message = self.get_object(request, object_id)

        if message is None:
            messages.error(request, _("Message not found."))
            return redirect("..")

        # Check if message has recipients with retry status
        retryable_recipients_count = message.recipients.filter(
            Q(delivery_status=MessageDeliveryStatusChoices.RETRY)
            | Q(delivery_status__isnull=True)
        ).count()

        if retryable_recipients_count == 0:
            messages.warning(
                request,
                _("No pending recipients found for this message."),
            )
            return redirect("..")

        # Trigger the retry task
        task = retry_messages_task.delay(message_ids=[str(message.id)])

        messages.success(
            request,
            _(
                "Retry task has been queued for "
                "%(retryable_recipients_count)d pending recipient(s) (id: %(task_id)s)."
            )
            % {
                "retryable_recipients_count": retryable_recipients_count,
                "task_id": task.id,
            },
        )
        return redirect("..")


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    """Admin class for the Contact model"""

    list_display = ("id", "name", "email", "mailbox")
    ordering = ("-created_at", "email")
    search_fields = ("name", "email")
    autocomplete_fields = ("mailbox",)


@admin.register(models.MessageRecipient)
class MessageRecipientAdmin(admin.ModelAdmin):
    """Admin class for the MessageRecipient model"""

    list_display = (
        "id",
        "message",
        "contact",
        "type",
        "delivery_status",
        "delivered_at",
        "retry_count",
        "delivery_message",
    )
    list_filter = ("delivery_status", "type", "delivered_at", "created_at")
    search_fields = (
        "message__subject",
        "contact__name",
        "contact__email",
        "delivery_message",
    )
    autocomplete_fields = ("contact",)
    raw_id_fields = ("message",)

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance"""
        return super().get_queryset(request).select_related("message", "contact")


@admin.register(models.Label)
class LabelAdmin(admin.ModelAdmin):
    """Admin class for the Label model"""

    list_display = (
        "id",
        "name",
        "slug",
        "mailbox",
        "color",
        "depth",
        "basename",
        "parent_name",
    )
    search_fields = ("name", "mailbox__local_part", "mailbox__domain__name")
    readonly_fields = ("slug",)
    autocomplete_fields = ("mailbox",)
    raw_id_fields = ("threads",)

    def get_basename(self, obj):
        """Return the display name of the label."""
        return obj.basename

    def get_parent_name(self, obj):
        """Return the display name of the label."""
        return obj.parent_name

    def get_depth(self, obj):
        """Return the display name of the label."""
        return obj.depth

    def save_model(self, request, obj, form, change):
        """Generate slug from name before saving."""
        if not obj.slug or (change and "name" in form.changed_data):
            obj.slug = slugify(obj.name.replace("/", "-"))
        super().save_model(request, obj, form, change)


@admin.register(models.Blob)
class BlobAdmin(admin.ModelAdmin):
    """Admin class for the Blob model"""

    list_display = (
        "id",
        "mailbox",
        "content_type",
        "size",
        "size_compressed",
        "compression",
        "created_at",
    )
    search_fields = ("mailbox__local_part", "mailbox__domain__name", "content_type")
    list_filter = ("content_type", "compression", "created_at", "updated_at")
    autocomplete_fields = ("mailbox",)

    def get_queryset(self, request):
        """Optimize queryset with select_related and exclude large binary content"""
        return (
            super()
            .get_queryset(request)
            .select_related("mailbox", "mailbox__domain")
            .defer("raw_content")  # Exclude large binary content from list view
        )


@admin.register(models.MailDomainAccess)
class MailDomainAccessAdmin(admin.ModelAdmin):
    """Admin class for the MailDomainAccess model"""

    list_display = ("id", "maildomain", "user", "role")
    search_fields = ("maildomain__name", "user__email")
    list_filter = ("role",)
    autocomplete_fields = ("maildomain", "user")


@admin.register(models.DKIMKey)
class DKIMKeyAdmin(admin.ModelAdmin):
    """Admin class for the DKIMKey model"""

    list_display = (
        "id",
        "selector",
        "domain",
        "algorithm",
        "key_size",
        "is_active",
        "created_at",
    )
    search_fields = ("selector", "domain__name")
    list_filter = ("algorithm", "is_active")
    readonly_fields = ("public_key", "created_at", "updated_at")
    autocomplete_fields = ("domain",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "selector",
                    "domain",
                    "algorithm",
                    "key_size",
                    "is_active",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Keys"),
            {
                "fields": ("public_key",),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(models.InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    """Admin class for the InboundMessage model (spam filter queue)."""

    list_display = (
        "id",
        "mailbox",
        "channel",
        "has_error",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = (
        "mailbox__local_part",
        "mailbox__domain__name",
        "error_message",
    )
    autocomplete_fields = ("mailbox", "channel")
    readonly_fields = ("created_at", "updated_at")
    fields = ("mailbox", "channel", "error_message", "created_at", "updated_at")

    def has_error(self, obj):
        """Return whether the message has an error."""
        return bool(obj.error_message)

    has_error.boolean = True
    has_error.short_description = _("Error")

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance."""
        return (
            super()
            .get_queryset(request)
            .select_related("mailbox", "mailbox__domain", "channel")
            .defer("raw_data")  # Exclude large binary content from list view
        )


@admin.register(models.MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    """Admin class for the MessageTemplate model"""

    list_display = (
        "name",
        "type",
        "is_forced",
        "is_default",
        "is_active",
        "mailbox",
        "maildomain",
        "created_at",
    )
    list_filter = (
        "type",
        "is_forced",
        "is_default",
        "created_at",
    )
    autocomplete_fields = ("mailbox", "maildomain")
    search_fields = ("name",)
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "raw_body",
        "text_body",
        "html_body",
    )

    def get_raw_body(self, obj):
        """Return the raw body of the template."""
        return obj.raw_body

    get_raw_body.short_description = "Raw Body"

    def get_text_body(self, obj):
        """Return the text body of the template."""
        return obj.text_body

    get_text_body.short_description = "Text Body"

    def get_html_body(self, obj):
        """Return the html body of the template."""
        return obj.html_body

    get_html_body.short_description = "HTML Body"
