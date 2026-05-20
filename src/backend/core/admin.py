"""Admin classes and registrations for core app."""
# pylint: disable=too-many-lines

import json
import logging
import mimetypes

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Exists, JSONField, OuterRef, Q
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import escape, format_html
from django.utils.text import slugify

from sentry_sdk import capture_exception

from core.api.utils import get_file_key
from core.api.viewsets.task import register_task_owner
from core.mda.outbound_tasks import retry_messages_task
from core.services import thread_events as thread_events_service
from core.services.dns.provisioning import provision_domain_dns
from core.services.exporter.tasks import export_mailbox_task
from core.services.importer.service import ImportService
from core.services.throttle import get_throttle_status

from . import enums, models
from .enums import MessageDeliveryStatusChoices
from .forms import IMAPImportForm, MessageImportForm


# Lock the entire Django admin to superusers. ``AdminSite.has_permission``
# is the single gate every admin URL passes through (model views, custom
# ``admin_view``-wrapped endpoints, the login redirect). The default
# (``is_active and is_staff``) is too loose given that the admin can
# download raw mail blobs, inject EML into arbitrary inboxes, and
# capture third-party IMAP credentials.
def _admin_superuser_only(self, request):  # pylint: disable=unused-argument
    # ``self`` is required by Django's AdminSite.has_permission signature.
    user = getattr(request, "user", None)
    return bool(user and user.is_active and user.is_superuser)


admin.AdminSite.has_permission = _admin_superuser_only


class PrettyJSONWidget(forms.Textarea):
    """A textarea widget that pretty-prints JSON content."""

    def __init__(self, attrs=None):
        default_attrs = {"cols": 80, "rows": 20, "style": "font-family: monospace;"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    def format_value(self, value):
        if isinstance(value, str):
            try:
                value = json.dumps(json.loads(value), indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
        return value


# Apply pretty JSON widget globally to every ModelAdmin (in-house and
# third-party). EncryptedJSONField inherits from TextField, not JSONField,
# so encrypted columns are unaffected.
admin.ModelAdmin.formfield_overrides = {
    **admin.ModelAdmin.formfield_overrides,
    JSONField: {"widget": PrettyJSONWidget},
}


class RecipientDeliveryStatusFilter(admin.SimpleListFilter):
    """Filter messages by their recipients' delivery status."""

    title = "delivery status"
    parameter_name = "recipient_delivery_status"

    def lookups(self, request, model_admin):
        """Return a list of delivery status choices."""
        return MessageDeliveryStatusChoices.choices

    def queryset(self, request, queryset):
        """Filter queryset by recipient delivery status."""
        if self.value():
            return queryset.filter(
                Exists(
                    models.MessageRecipient.objects.filter(
                        message_id=OuterRef("pk"),
                        delivery_status=int(self.value()),
                    )
                )
            )
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
        f"{len(message_ids)} messages - "
        f"Retry send message task queued (id: {task.id}).",
    )


retry_send_messages_action.short_description = (
    "Retry to send selected messages to pending recipients"
)


class PasswordlessUserForm(forms.Form):
    """Minimal form to create a passwordless (sub-less) User from the admin."""

    email = forms.EmailField(label="Email", required=True)

    def clean_email(self):
        """Reject emails that are already in use."""
        email = self.cleaned_data["email"]
        if models.User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


@admin.register(models.User)
class UserAdmin(auth_admin.UserAdmin):
    """Admin class for the User model"""

    change_list_template = "admin/core/user/change_list.html"

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
            "Personal info",
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
            "Permissions",
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
        ("Important dates", {"fields": ("created_at", "updated_at")}),
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "add-passwordless/",
                self.admin_site.admin_view(self.add_passwordless_view),
                name="core_user_add_passwordless",
            ),
        ]
        return custom_urls + urls

    def add_passwordless_view(self, request):
        """Create a passwordless (sub-less) user from a single email field.

        These users cannot authenticate locally (unusable password, no
        ``admin_email``) and will be claimed on first OIDC login by
        ``UserManager.get_user_by_sub_or_email``.
        """
        if request.method == "POST":
            form = PasswordlessUserForm(request.POST)
            if form.is_valid():
                email = form.cleaned_data["email"]
                user = models.User.objects.filter(email=email).first()
                if user is None:
                    user = models.User(email=email)
                    user.set_unusable_password()
                    user.save()
                    messages.success(
                        request,
                        f"Passwordless user created: {user.email}",
                    )
                else:
                    messages.info(
                        request,
                        f"User already exists: {user.email}",
                    )
                return redirect("admin:core_user_changelist")
        else:
            form = PasswordlessUserForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Add passwordless user",
            "form": form,
            "opts": self.model._meta,  # noqa: SLF001
        }
        return TemplateResponse(
            request, "admin/core/user/add_passwordless.html", context
        )


class MailDomainAccessInline(admin.TabularInline):
    """Inline class for the MailDomainAccess model"""

    model = models.MailDomainAccess
    autocomplete_fields = ("user",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")


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
    readonly_fields = ("throttle_status_display",)
    change_form_template = "admin/core/maildomain/change_form.html"

    @admin.display(description="Throttle Status (External Recipients)")
    def throttle_status_display(self, obj):
        """Display current throttle usage for this maildomain."""
        status = get_throttle_status(maildomain=obj)
        if "maildomain" not in status:
            return "No throttle configured"

        info = status["maildomain"]
        return format_html(
            "<strong>{}/{}</strong> this {} (resets in {})",
            info["current"],
            info["limit"],
            info["period"],
            info["reset_in_human"],
        )

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
            messages.error(request, "Mail domain not found.")
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
                    f"DNS provisioning successful via {provider_used}: {changes_text}",
                )
            else:
                messages.success(
                    request,
                    f"DNS provisioning successful via {provider_used} (no changes needed).",
                )
        else:
            error_msg = results.get("error", "Unknown error")
            messages.error(
                request,
                f"DNS provisioning failed: {error_msg}",
            )

        return redirect("..")


class MailboxAccessInline(admin.TabularInline):
    """Inline class for the MailboxAccess model"""

    model = models.MailboxAccess
    autocomplete_fields = ("user",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")


@admin.register(models.Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    """Admin class for the Mailbox model"""

    inlines = [MailboxAccessInline]
    list_display = ("__str__", "is_identity", "contact", "alias_of", "updated_at")
    list_filter = ("is_identity", "created_at", "updated_at")
    search_fields = ("local_part", "domain__name", "contact__name", "contact__email")
    actions = [reset_keycloak_password_action]
    autocomplete_fields = ("domain", "contact", "alias_of")
    change_form_template = "admin/core/mailbox/change_form.html"
    readonly_fields = ("throttle_status_display",)

    def save_formset(self, request, form, formset, change):
        """Route MailboxAccess inline edits through the cleanup service."""
        if formset.model is models.MailboxAccess:
            _cleanup_mailbox_access_formset(formset)
            return
        super().save_formset(request, form, formset, change)

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance"""
        return (
            super()
            .get_queryset(request)
            .select_related("domain", "contact", "alias_of")
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/export/",
                self.admin_site.admin_view(self.export_messages_view),
                name="core_mailbox_export",
            ),
        ]
        return custom_urls + urls

    def export_messages_view(self, request, object_id):
        """View for exporting all messages from a mailbox."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        mailbox_obj = self.get_object(request, object_id)

        if mailbox_obj is None:
            messages.error(request, "Mailbox not found.")
            return redirect("..")

        # Start the export task
        try:
            task = export_mailbox_task.delay(str(mailbox_obj.id), str(request.user.id))
            register_task_owner(task.id, request.user.id)
        except Exception:  # pylint: disable=broad-exception-caught
            logging.exception(
                "Failed to queue export task for mailbox %s", mailbox_obj.id
            )
            capture_exception()
            messages.error(
                request, "Failed to queue export task. Please try again later."
            )
            return redirect("..")

        messages.success(
            request,
            f"Export task has been queued for mailbox {mailbox_obj}. "
            f"You will receive a message with the download link when the export "
            f"is complete (task id: {task.id}).",
        )

        return redirect("..")

    @admin.display(description="Throttle Status (External Recipients)")
    def throttle_status_display(self, obj):
        """Display current throttle usage for this mailbox and its domain."""
        status = get_throttle_status(mailbox=obj, maildomain=obj.domain)

        parts = []
        if "mailbox" in status:
            info = status["mailbox"]
            parts.append(
                format_html(
                    "Mailbox: <strong>{}/{}</strong> this {} (resets in {})",
                    info["current"],
                    info["limit"],
                    info["period"],
                    info["reset_in_human"],
                )
            )
        if "maildomain" in status:
            info = status["maildomain"]
            parts.append(
                format_html(
                    "Domain: <strong>{}/{}</strong> this {} (resets in {})",
                    info["current"],
                    info["limit"],
                    info["period"],
                    info["reset_in_human"],
                )
            )

        return format_html("<br>".join(parts)) if parts else "No throttle configured"


@admin.register(models.Channel)
class ChannelAdmin(admin.ModelAdmin):
    """Admin class for the Channel model"""

    list_display = (
        "name",
        "type",
        "scope_level",
        "mailbox",
        "maildomain",
        "user",
        "created_at",
    )
    list_filter = ("type", "scope_level", "created_at")
    list_select_related = ("mailbox", "maildomain", "user")
    search_fields = ("name", "type")
    readonly_fields = ("created_at", "updated_at", "last_used_at")
    autocomplete_fields = ("mailbox", "maildomain", "user")
    change_form_template = "admin/core/channel/change_form.html"

    fieldsets = (
        (None, {"fields": ("name", "type", "scope_level", "settings")}),
        (
            "Target",
            {
                "fields": ("mailbox", "maildomain", "user"),
                "description": (
                    "Bind the channel to exactly the target required by its "
                    "scope_level: 'global' → none; 'maildomain' → maildomain; "
                    "'mailbox' → mailbox; 'user' → user. On non-user scopes "
                    "the user FK is an optional creator audit."
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at", "last_used_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Constrain ``type`` to known ChannelTypes in the admin form.

        The model field is intentionally a free-form CharField (see
        ``ChannelTypes`` docstring — adding a new type must not require a
        migration). The choice constraint is therefore admin-only.
        """
        if db_field.name == "type":
            # pylint: disable=import-outside-toplevel
            from core.enums import ChannelTypes

            kwargs["widget"] = forms.Select(
                choices=[(t.value, t.value) for t in ChannelTypes]
            )
            return db_field.formfield(**kwargs)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/regenerate-api-key/",
                self.admin_site.admin_view(self.regenerate_api_key_view),
                name="core_channel_regenerate_api_key",
            ),
        ]
        return custom_urls + urls

    def regenerate_api_key_view(self, request, object_id):
        """Regenerate the api_key secret on an api_key channel.

        Delegates the actual rotation to ``Channel.rotate_api_key`` (single
        source of truth, shared with the DRF create + regenerate flows). The
        plaintext is rendered ONCE in the response body and never stored in
        cookies, session, or the messages framework — closing the window
        where a credential could leak through signed-cookie message storage.
        """
        # pylint: disable=import-outside-toplevel
        from core.enums import ChannelTypes

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        channel = self.get_object(request, object_id)
        if channel is None:
            messages.error(request, "Channel not found.")
            return redirect("..")
        if channel.type != ChannelTypes.API_KEY:
            messages.error(
                request, "Only api_key channels can have their secret regenerated."
            )
            return redirect("..")

        plaintext = channel.rotate_api_key()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,  # noqa: SLF001
            "original": channel,
            "title": "New api_key generated",
            "api_key": plaintext,
        }
        return TemplateResponse(
            request, "admin/core/channel/regenerated_api_key.html", context
        )


@admin.register(models.MailboxAccess)
class MailboxAccessAdmin(admin.ModelAdmin):
    """Admin class for the MailboxAccess model.

    Routes all writes through ``thread_events_service`` so that role
    downgrades and deletions clean up assignments/mentions exactly the
    same way the API viewsets do. Without these overrides, an operator
    editing a row directly from the admin would leave stale ``UserEvent``
    rows behind.
    """

    list_display = ("id", "mailbox", "user", "role")
    list_select_related = ("mailbox", "user")
    search_fields = ("mailbox__local_part", "mailbox__domain__name", "user__email")
    autocomplete_fields = ("mailbox", "user")

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        """Detect role downgrades or principal changes and trigger cleanup.

        When ``mailbox`` or ``user`` is reassigned, the row the previous
        principal had is gone after save, so we must revoke against the
        pre-save snapshot rather than the new ``obj``. Wrapped in
        ``transaction.atomic`` so the save and the cleanup commit together
        — the service layer requires this contract.
        """
        previous = None
        if change and obj.pk:
            previous = models.MailboxAccess.objects.filter(pk=obj.pk).first()
        super().save_model(request, obj, form, change)
        if previous is None:
            return
        if (previous.mailbox_id, previous.user_id) != (
            obj.mailbox_id,
            obj.user_id,
        ):
            thread_events_service.revoke_mailbox_access(mailbox_access=previous)
            return
        was_editor = previous.role in enums.MAILBOX_ROLES_CAN_EDIT
        is_editor = obj.role in enums.MAILBOX_ROLES_CAN_EDIT
        if was_editor and not is_editor:
            thread_events_service.downgrade_mailbox_access(mailbox_access=obj)

    @transaction.atomic
    def delete_model(self, request, obj):
        """Delete the row, then cleanup using the in-memory instance.

        Order matters: ``revoke_mailbox_access`` re-runs the editor /
        viewer-rights queries to decide who lost their effective access,
        and those queries must observe a state where ``obj`` is gone.
        Wrapped in ``transaction.atomic`` so a failing cleanup rolls the
        delete back instead of leaving stale ``UserEvent`` rows.
        """
        super().delete_model(request, obj)
        thread_events_service.revoke_mailbox_access(mailbox_access=obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        """Bulk-delete then cleanup per row.

        ``queryset`` is consumed before ``super().delete_queryset()``
        deletes the rows, so each ``MailboxAccess`` is still readable in
        memory afterwards for the cleanup pass. Wrapped in
        ``transaction.atomic`` so a partial cleanup failure does not
        leave half the rows deleted without their ``UserEvent`` cleanup.
        """
        rows = list(queryset)
        super().delete_queryset(request, queryset)
        for obj in rows:
            thread_events_service.revoke_mailbox_access(mailbox_access=obj)


@admin.register(models.ThreadAccess)
class ThreadAccessAdmin(admin.ModelAdmin):
    """Admin class for the ThreadAccess model.

    Registered explicitly so that direct admin writes flow through the
    cleanup service. ``ThreadAccess`` is also surfaced as an inline on
    ``ThreadAdmin``; inline edits go through ``ThreadAdmin.save_formset``
    rather than this class.
    """

    list_display = ("id", "thread", "mailbox", "role")
    list_select_related = ("thread", "mailbox")
    search_fields = (
        "thread__subject",
        "mailbox__local_part",
        "mailbox__domain__name",
    )
    autocomplete_fields = ("thread", "mailbox")

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        """Detect EDITOR → other transitions or principal changes and cleanup.

        When ``thread`` or ``mailbox`` is reassigned, the row the previous
        principal had is gone after save, so we must revoke against the
        pre-save snapshot rather than the new ``obj``. Wrapped in
        ``transaction.atomic`` so the save and the cleanup commit together
        — the service layer requires this contract.
        """
        previous = None
        if change and obj.pk:
            previous = models.ThreadAccess.objects.filter(pk=obj.pk).first()
        super().save_model(request, obj, form, change)
        if previous is None:
            return
        if (previous.thread_id, previous.mailbox_id) != (
            obj.thread_id,
            obj.mailbox_id,
        ):
            thread_events_service.revoke_thread_access(thread_access=previous)
            return
        if (
            previous.role == enums.ThreadAccessRoleChoices.EDITOR
            and obj.role != enums.ThreadAccessRoleChoices.EDITOR
        ):
            thread_events_service.downgrade_thread_access(thread_access=obj)

    @transaction.atomic
    def delete_model(self, request, obj):
        """Delete the row, then cleanup using the in-memory instance.

        Wrapped in ``transaction.atomic`` so a failing cleanup rolls the
        delete back instead of leaving stale ``UserEvent`` rows.
        """
        super().delete_model(request, obj)
        thread_events_service.revoke_thread_access(thread_access=obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        """Bulk-delete then cleanup per row.

        Wrapped in ``transaction.atomic`` so a partial cleanup failure
        does not leave half the rows deleted without their ``UserEvent``
        cleanup.
        """
        rows = list(queryset)
        super().delete_queryset(request, queryset)
        for obj in rows:
            thread_events_service.revoke_thread_access(thread_access=obj)


class ThreadAccessInline(admin.TabularInline):
    """Inline class for the ThreadAccess model.

    Inline writes are routed through ``ThreadAdmin.save_formset`` so that
    role downgrades and deletions trigger the same assignment cleanup as
    the standalone ``ThreadAccessAdmin`` and the API viewset.
    """

    model = models.ThreadAccess
    autocomplete_fields = ("mailbox",)
    readonly_fields = ("read_at", "starred_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("mailbox")


class ThreadEventInline(admin.TabularInline):
    """Inline class for the ThreadEvent model.

    Read-only on purpose: ASSIGN/UNASSIGN/IM events have invariants tied
    to ``UserEvent`` rows that the service layer enforces. Authoring them
    by hand from the admin would leave the user-facing notifications
    inconsistent with the timeline. Operators inspecting a thread can
    still see every event and its data — the lock is on creation/edit
    only.
    """

    model = models.ThreadEvent
    raw_id_fields = ("message",)
    readonly_fields = (
        "type",
        "author",
        "channel",
        "message",
        "data",
        "created_at",
    )
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("author", "channel", "message")
        )


class UserEventInline(admin.TabularInline):
    """Inline class for the UserEvent model.

    UserEvent entries are created exclusively by business logic (mentions,
    assignments) and must never be edited via the admin: editing ``thread_event``
    would desynchronize ``user_event.thread`` from
    ``user_event.thread_event.thread``, breaking mention filters and unread flags.
    """

    model = models.UserEvent
    readonly_fields = (
        "user",
        "thread",
        "thread_event",
        "type",
        "read_at",
        "created_at",
    )
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "thread", "thread_event")
        )


@transaction.atomic
def _cleanup_thread_access_formset(formset):
    """Run the assignment cleanup service for a ThreadAccess inline formset.

    Captures pre-mutation state (full snapshots, instances scheduled for
    deletion) before ``formset.save()`` actually mutates the database,
    then runs the cleanup service afterwards so the editor-rights
    queries observe the post-mutation state. Snapshots the full row
    (not just role) so reassigning ``thread`` or ``mailbox`` in place
    revokes the old grant rather than leaving it behind. Wrapped in
    ``transaction.atomic`` so ``formset.save()`` and the cleanup commit
    together — partial cleanup would leave orphaned ``UserEvent`` rows.
    """
    previous_snapshots = {}
    instances_to_revoke = []
    tracked_fields = ("role", "thread", "mailbox")
    for sub_form in formset.forms:
        if not sub_form.instance.pk:
            continue
        if sub_form.cleaned_data.get("DELETE"):
            snapshot = models.ThreadAccess.objects.filter(
                pk=sub_form.instance.pk
            ).first()
            if snapshot is not None:
                instances_to_revoke.append(snapshot)
            continue
        if any(field in (sub_form.changed_data or []) for field in tracked_fields):
            previous_snapshots[sub_form.instance.pk] = (
                models.ThreadAccess.objects.filter(pk=sub_form.instance.pk).first()
            )

    formset.save()

    for instance in instances_to_revoke:
        thread_events_service.revoke_thread_access(thread_access=instance)

    for sub_form in formset.forms:
        if not sub_form.instance.pk or sub_form.cleaned_data.get("DELETE"):
            continue
        previous = previous_snapshots.get(sub_form.instance.pk)
        if previous is None:
            continue
        new = sub_form.instance
        if (previous.thread_id, previous.mailbox_id) != (
            new.thread_id,
            new.mailbox_id,
        ):
            thread_events_service.revoke_thread_access(thread_access=previous)
            continue
        if (
            previous.role == enums.ThreadAccessRoleChoices.EDITOR
            and new.role != enums.ThreadAccessRoleChoices.EDITOR
        ):
            thread_events_service.downgrade_thread_access(thread_access=new)


@transaction.atomic
def _cleanup_mailbox_access_formset(formset):
    """Run the assignment cleanup service for a MailboxAccess inline formset.

    Snapshots the full row (not just role) so that reassigning ``mailbox``
    or ``user`` in place revokes the old principal's grant rather than
    leaving stale assignment/user-event state behind. Wrapped in
    ``transaction.atomic`` so ``formset.save()`` and the cleanup commit
    together — partial cleanup would leave orphaned ``UserEvent`` rows.
    """
    previous_snapshots = {}
    instances_to_revoke = []
    tracked_fields = ("role", "mailbox", "user")
    for sub_form in formset.forms:
        if not sub_form.instance.pk:
            continue
        if sub_form.cleaned_data.get("DELETE"):
            snapshot = models.MailboxAccess.objects.filter(
                pk=sub_form.instance.pk
            ).first()
            if snapshot is not None:
                instances_to_revoke.append(snapshot)
            continue
        if any(field in (sub_form.changed_data or []) for field in tracked_fields):
            previous_snapshots[sub_form.instance.pk] = (
                models.MailboxAccess.objects.filter(pk=sub_form.instance.pk).first()
            )

    formset.save()

    for instance in instances_to_revoke:
        thread_events_service.revoke_mailbox_access(mailbox_access=instance)

    for sub_form in formset.forms:
        if not sub_form.instance.pk or sub_form.cleaned_data.get("DELETE"):
            continue
        previous = previous_snapshots.get(sub_form.instance.pk)
        if previous is None:
            continue
        new = sub_form.instance
        if (previous.mailbox_id, previous.user_id) != (new.mailbox_id, new.user_id):
            thread_events_service.revoke_mailbox_access(mailbox_access=previous)
            continue
        was_editor = previous.role in enums.MAILBOX_ROLES_CAN_EDIT
        is_editor = new.role in enums.MAILBOX_ROLES_CAN_EDIT
        if was_editor and not is_editor:
            thread_events_service.downgrade_mailbox_access(mailbox_access=new)


@admin.register(models.Thread)
class ThreadAdmin(admin.ModelAdmin):
    """Admin class for the Thread model"""

    inlines = [ThreadAccessInline, ThreadEventInline, UserEventInline]

    def save_formset(self, request, form, formset, change):
        """Route ThreadAccess inline edits through the cleanup service."""
        if formset.model is models.ThreadAccess:
            _cleanup_thread_access_formset(formset)
            return
        super().save_formset(request, form, formset, change)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("labels")

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
        "has_trashed",
        "has_archived",
        "has_draft",
        "has_sender",
        "has_attachments",
        "has_delivery_pending",
        "has_delivery_failed",
        "is_spam",
        "created_at",
    )
    fieldsets = (
        (None, {"fields": ("subject", "snippet", "display_labels", "summary")}),
        (
            "Statistics",
            {
                "fields": (
                    "has_trashed",
                    "has_archived",
                    "has_draft",
                    "has_sender",
                    "has_messages",
                    "has_attachments",
                    "is_spam",
                    "has_active",
                    "has_delivery_pending",
                    "has_delivery_failed",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "sender_names",
                    "created_at",
                    "updated_at",
                    "messaged_at",
                    "active_messaged_at",
                    "trashed_messaged_at",
                    "draft_messaged_at",
                    "sender_messaged_at",
                    "archived_messaged_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "display_labels",
        "has_trashed",
        "has_archived",
        "has_draft",
        "has_attachments",
        "has_sender",
        "has_messages",
        "has_delivery_pending",
        "has_delivery_failed",
        "is_spam",
        "has_active",
        "messaged_at",
        "active_messaged_at",
        "trashed_messaged_at",
        "draft_messaged_at",
        "sender_messaged_at",
        "archived_messaged_at",
        "sender_names",
        "created_at",
        "updated_at",
    )

    def get_labels(self, obj):
        """Return a comma-separated list of labels for the thread."""
        return ", ".join(label.name for label in obj.labels.all())

    get_labels.short_description = "Labels"
    get_labels.admin_order_field = "labels__name"

    def display_labels(self, obj):
        """Display labels with their colors in the detail view."""
        if not obj.labels.exists():
            return "No labels"

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

    display_labels.short_description = "Labels"


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

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contact")


@admin.register(models.Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Admin class for the Attachment model"""

    list_display = ("id", "name", "mailbox", "message", "created_at")
    list_select_related = ("mailbox", "message")
    search_fields = ("name", "mailbox__local_part", "mailbox__domain__name")
    autocomplete_fields = ("mailbox",)
    raw_id_fields = ("blob", "message")
    exclude = ("_deprecated_messages",)


class AttachmentInline(admin.TabularInline):
    """Inline class showing a Message's attachments via the FK."""

    model = models.Attachment
    fk_name = "message"
    raw_id_fields = ("blob",)
    autocomplete_fields = ("mailbox",)
    exclude = ("_deprecated_messages",)
    extra = 0


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
        "has_attachments",
        "created_at",
        "sent_at",
    )
    list_filter = (
        "is_sender",
        "is_draft",
        "is_trashed",
        "is_spam",
        "is_archived",
        "has_attachments",
        RecipientDeliveryStatusFilter,
        "created_at",
        "sent_at",
        "archived_at",
        "trashed_at",
    )
    search_fields = ("subject", "sender__name", "sender__email", "mime_id")
    change_list_template = "admin/core/message/change_list.html"
    change_form_template = "admin/core/message/change_form.html"
    raw_id_fields = ("thread", "blob", "draft_blob", "parent", "channel")
    autocomplete_fields = ("sender", "sender_user", "signature")
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
                    filename=import_file.name,
                )
                if success:
                    return redirect("..")
        else:
            form = MessageImportForm()

        context = dict(
            self.admin_site.each_context(request),
            title="Import Messages",
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
            title="Import Messages from IMAP",
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
            messages.error(request, "Message not found.")
            return redirect("..")

        # Check if message has recipients with retry status
        retryable_recipients_count = message.recipients.filter(
            Q(delivery_status=MessageDeliveryStatusChoices.RETRY)
            | Q(delivery_status__isnull=True)
        ).count()

        if retryable_recipients_count == 0:
            messages.warning(
                request,
                "No pending recipients found for this message.",
            )
            return redirect("..")

        # Trigger the retry task
        task = retry_messages_task.delay(message_ids=[str(message.id)])

        messages.success(
            request,
            f"Retry task has been queued for "
            f"{retryable_recipients_count} pending recipient(s) (id: {task.id}).",
        )
        return redirect("..")


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    """Admin class for the Contact model"""

    list_display = ("id", "name", "email", "mailbox")
    list_select_related = ("mailbox",)
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
    list_select_related = ("mailbox",)
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
    """Admin class for the Blob model."""

    list_display = (
        "id",
        "content_type",
        "size",
        "size_compressed",
        "compression",
        "storage_location",
        "encryption_key_id",
        "created_at",
    )
    search_fields = ("id", "content_type")
    list_filter = (
        "content_type",
        "compression",
        "storage_location",
        "encryption_key_id",
        "created_at",
        "updated_at",
    )
    exclude = ("_deprecated_mailbox", "_deprecated_maildomain")
    change_form_template = "admin/core/blob/change_form.html"

    def get_queryset(self, request):
        """Exclude large binary content from list view."""
        return super().get_queryset(request).defer("raw_content")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/download/",
                self.admin_site.admin_view(self.download_view),
                name="core_blob_download",
            ),
        ]
        return custom_urls + urls

    def download_view(self, request, object_id):
        """Download the decompressed (and decrypted) blob content."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        blob = self.get_object(request, object_id)
        if blob is None:
            messages.error(request, "Blob not found.")
            return redirect("..")

        try:
            content = blob.get_content()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.exception("Failed to fetch blob %s for admin download", blob.id)
            capture_exception()
            messages.error(request, f"Failed to download blob: {exc}")
            # Bounce back to the change view; "." would re-target the
            # POST-only download endpoint and 405.
            return redirect("..")

        extension = mimetypes.guess_extension(blob.content_type or "") or ""
        filename = f"blob-{blob.id}{extension}"
        response = HttpResponse(
            content, content_type=blob.content_type or "application/octet-stream"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(content))
        return response


@admin.register(models.MailDomainAccess)
class MailDomainAccessAdmin(admin.ModelAdmin):
    """Admin class for the MailDomainAccess model"""

    list_display = ("id", "maildomain", "user", "role")
    list_select_related = ("maildomain", "user")
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
    list_select_related = ("domain",)
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
            "Keys",
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
    has_error.short_description = "Error"

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
    list_select_related = ("mailbox", "maildomain")
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
