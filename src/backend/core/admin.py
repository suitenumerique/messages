"""Admin classes and registrations for core app."""

from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.utils.translation import gettext_lazy as _

from . import models


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


@admin.register(models.Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin class for the Message model"""

    inlines = [MessageRecipientInline]
    list_display = ("id", "subject", "sender", "created_at")


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    """Admin class for the Contact model"""

    list_display = ("id", "name", "email", "mailbox")


@admin.register(models.MessageRecipient)
class MessageRecipientAdmin(admin.ModelAdmin):
    """Admin class for the MessageRecipient model"""

    list_display = ("id", "message", "contact", "type")
    search_fields = ("message__subject", "contact__name", "contact__email")
