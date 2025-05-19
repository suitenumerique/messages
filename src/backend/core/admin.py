"""Admin classes and registrations for core app."""

from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _

from core.mda.inbound import deliver_inbound_message
from core.mda.rfc5322 import parse_email_message

from . import models
from .forms import EmlImportForm


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
    list_display = ("id", "subject", "snippet", "created_at", "updated_at")


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
    list_display = ("id", "subject", "sender", "created_at")
    change_list_template = "admin/core/message/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import-eml/",
                self.admin_site.admin_view(self.import_eml_view),
                name="core_message_import_eml",
            ),
        ]
        return custom_urls + urls

    def import_eml_view(self, request):
        """View for importing EML files."""
        if request.method == "POST":
            form = EmlImportForm(request.POST, request.FILES)
            if form.is_valid():
                eml_file = request.FILES["eml_file"]
                recipient = form.cleaned_data["recipient"]
                try:
                    # Import the message from the EML file contents
                    eml_content = eml_file.read()
                    parsed_email = parse_email_message(eml_content)
                    deliver_inbound_message(str(recipient), parsed_email, eml_content)
                    # For now, just show a success message
                    messages.success(
                        request,
                        f"Successfully processed EML file: {eml_file.name} for recipient {recipient}",
                    )
                    return redirect("..")
                except Exception as e:  # noqa: BLE001 pylint: disable=broad-except
                    messages.error(request, f"Error processing EML file: {str(e)}")
        else:
            form = EmlImportForm()

        context = dict(
            self.admin_site.each_context(request),
            title=_("Import Messages from EML"),
            form=form,
            opts=self.model._meta,  # noqa: SLF001
        )
        return TemplateResponse(request, "admin/core/message/import_eml.html", context)

    def changelist_view(self, request, extra_context=None):
        """Add import permission to the changelist context."""
        extra_context = extra_context or {}
        extra_context["has_import_eml_permission"] = self.has_add_permission(request)
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
