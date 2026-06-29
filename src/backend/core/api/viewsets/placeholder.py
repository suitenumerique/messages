"""Views for placeholder field structure information."""

from django.conf import settings
from django.db.models import F

from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core import enums, models

# Built-in placeholders, always available. They carry no label: the frontend
# localizes them client-side from its "placeholders" i18next namespace.
BUILTIN_PLACEHOLDER_FIELDS = ("name", "recipient_name", "user_name")


@extend_schema(tags=["placeholders"])
class PlaceholderView(APIView):
    """
    View for placeholder field structure information.

    This view provides endpoints for viewing the structure of available fields
    including User model fields and user custom attributes from schema.

    Available actions:
    - GET: Get the structure of all available fields
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get field structure",
        description="Get the structure of all available fields with their labels",
        responses={
            200: {
                "type": "object",
                "description": (
                    "Field slugs mapped to their label metadata. Built-in "
                    "fields have an empty object and are localized client-side. "
                    "Custom attribute fields expose their schema title and "
                    "optional per-language translations."
                ),
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Default label (custom fields only).",
                        },
                        "i18n": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": (
                                "Label translations by language code, from the "
                                "schema 'x-i18n' entry (custom fields only)."
                            ),
                        },
                    },
                },
                "example": {
                    "name": {},
                    "recipient_name": {},
                    "job_title": {
                        "title": "Job title",
                        "i18n": {"en": "Job title", "fr": "Fonction"},
                    },
                },
            },
        },
    )
    def get(self, request):
        """Get the structure of available fields.

        Built-in fields are returned as empty objects and localized
        client-side. Custom attribute fields carry their schema title and,
        when defined, the ``x-i18n`` title translations so the frontend can
        pick the right language.
        """
        fields = {field_name: {} for field_name in BUILTIN_PLACEHOLDER_FIELDS}

        # Add user custom attributes fields from schema. Only string fields are
        # exposed: a placeholder is substituted as text, so non-string types
        # (e.g. boolean, integer) are not meaningful here.
        schema = settings.SCHEMA_CUSTOM_ATTRIBUTES_USER
        schema_properties = schema.get("properties", {})
        for field_name, field_schema in schema_properties.items():
            if field_schema.get("type") != "string":
                continue
            field = {"title": field_schema.get("title", field_name)}
            x_i18n = field_schema.get("x-i18n")
            if isinstance(x_i18n, dict):
                i18n_titles = x_i18n.get("title")
                if isinstance(i18n_titles, dict) and i18n_titles:
                    field["i18n"] = i18n_titles
            fields[field_name] = field
        return Response(fields)


@extend_schema(tags=["messages"])
class DraftPlaceholderView(APIView):
    """
    Resolve placeholder values in the context of a draft message.

    The authenticated user must have editor-level access to the mailbox
    that owns the draft, and that mailbox must have editor access to the
    draft's thread.

    Returns actual values (not labels) that should be substituted into
    template placeholders: sender name, custom user attributes, and
    recipient_name from the draft's TO recipients.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Resolve placeholder values for a draft",
        description=(
            "Resolve placeholder values for the authenticated user in the "
            "context of a draft message. The mailbox is derived from the "
            "draft's sender. recipient_name is resolved from the draft's "
            "TO recipients."
        ),
        responses={
            200: {
                "type": "object",
                "description": "Placeholder keys mapped to their resolved values",
                "additionalProperties": {"type": "string"},
                "example": {
                    "name": "John Doe",
                    "recipient_name": "Jane Smith",
                    "job_title": "Developer",
                },
            },
            404: {"description": "Draft not found"},
        },
    )
    def get(self, request, message_id):
        """Resolve placeholder values for the given draft context."""
        try:
            message = models.Message.objects.select_related("sender__mailbox").get(
                id=message_id,
                is_draft=True,
                # User has CAN_EDIT role on the sender's mailbox
                sender__mailbox__accesses__user=request.user,
                sender__mailbox__accesses__role__in=enums.MAILBOX_ROLES_CAN_EDIT,
                # The sender's mailbox has EDITOR access to the thread
                thread__accesses__mailbox=F("sender__mailbox"),
                thread__accesses__role=enums.ThreadAccessRoleChoices.EDITOR,
            )
        except models.Message.DoesNotExist as exc:
            raise NotFound(
                "Draft message not found, is not a draft, or access denied."
            ) from exc

        mailbox = message.sender.mailbox

        context = models.MessageTemplate.resolve_placeholder_values(
            mailbox=mailbox, user=request.user, message=message
        )
        return Response(context)
