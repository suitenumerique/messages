"""Admin classes and registrations for core app."""

from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.services.import_service import ImportService

from . import models
from .forms import IMAPImportForm, MessageImportForm


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
                    "short_name",
                    "language",
                    "timezone",
                )
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_device",
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
        "is_device",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_staff", "is_superuser", "is_device", "is_active")
    ordering = (
        "is_active",
        "-is_superuser",
        "-is_staff",
        "-is_device",
        "-updated_at",
        "full_name",
    )
    readonly_fields = (
        "id",
        "sub",
        "email",
        "full_name",
        "short_name",
        "created_at",
        "updated_at",
    )
    search_fields = ("id", "sub", "admin_email", "email", "full_name")


@admin.register(models.MailDomain)
class MailDomainAdmin(admin.ModelAdmin):
    """Admin class for the MailDomain model"""

    list_display = (
        "name",
        "created_at",
        "updated_at",
    )
    search_fields = ("name",)


class MailboxAccessInline(admin.TabularInline):
    """Inline class for the MailboxAccess model"""

    model = models.MailboxAccess


@admin.register(models.Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    """Admin class for the Mailbox model"""

    inlines = [MailboxAccessInline]
    list_display = ("__str__", "domain", "updated_at")
    search_fields = ("local_part", "domain__name")


@admin.register(models.MailboxAccess)
class MailboxAccessAdmin(admin.ModelAdmin):
    """Admin class for the MailboxAccess model"""

    list_display = ("id", "mailbox", "user", "role")
    search_fields = ("mailbox__local_part", "mailbox__domain__name", "user__email")


class ThreadAccessInline(admin.TabularInline):
    """Inline class for the ThreadAccess model"""

    model = models.ThreadAccess


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
    list_filter = ("labels",)
    fieldsets = (
        (None, {"fields": ("subject", "snippet", "display_labels")}),
        (
            _("Statistics"),
            {
                "fields": (
                    "count_unread",
                    "count_trashed",
                    "count_draft",
                    "count_starred",
                    "count_sender",
                    "count_messages",
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
        "count_unread",
        "count_trashed",
        "count_draft",
        "count_starred",
        "count_sender",
        "count_messages",
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

        labels_html = []
        for label in obj.labels.all():
            # Create a colored label display
            label_html = f'<span style="display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 3px; background-color: {label.color}; color: white;">{label.name}</span>'
            labels_html.append(label_html)

        return mark_safe(" ".join(labels_html))

    display_labels.short_description = _("Labels")


class MessageRecipientInline(admin.TabularInline):
    """Inline class for the MessageRecipient model"""

    model = models.MessageRecipient


@admin.register(models.Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Admin class for the Attachment model"""

    list_display = ("id", "name", "mailbox", "created_at")
    search_fields = ("name", "mailbox__local_part", "mailbox__domain__name")


class AttachmentInline(admin.TabularInline):
    """Inline class for the Attachment model"""

    model = models.Attachment.messages.through


@admin.register(models.Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin class for the Message model"""

    inlines = [MessageRecipientInline, AttachmentInline]
    list_display = ("id", "subject", "sender", "created_at", "sent_at")
    change_list_template = "admin/core/message/change_list.html"

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
        ]
        return custom_urls + urls

    def import_messages_view(self, request):
        """View for importing EML or MBOX files."""
        if request.method == "POST":
            form = MessageImportForm(request.POST, request.FILES)
            if form.is_valid():
                import_file = request.FILES["import_file"]
                recipient = form.cleaned_data["recipient"]
                success, _response_data = ImportService.import_file(
                    file=import_file,
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
                    folder=form.cleaned_data["folder"],
                    max_messages=form.cleaned_data["max_messages"],
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


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    """Admin class for the Contact model"""

    list_display = ("id", "name", "email", "mailbox")
    ordering = ("-created_at", "email")


@admin.register(models.MessageRecipient)
class MessageRecipientAdmin(admin.ModelAdmin):
    """Admin class for the MessageRecipient model"""

    list_display = ("id", "message", "contact", "type")
    search_fields = ("message__subject", "contact__name", "contact__email")


@admin.register(models.Label)
class LabelAdmin(admin.ModelAdmin):
    """Admin class for the Label model"""

    list_display = ("id", "name", "slug", "mailbox", "color")
    search_fields = ("name", "mailbox__local_part", "mailbox__domain__name")
    filter_horizontal = ("threads",)
    list_filter = ("mailbox",)
    readonly_fields = ("slug",)

    def save_model(self, request, obj, form, change):
        """Generate slug from name before saving."""
        if not obj.slug or (change and "name" in form.changed_data):
            obj.slug = slugify(obj.name.replace("/", "-"))
        super().save_model(request, obj, form, change)
