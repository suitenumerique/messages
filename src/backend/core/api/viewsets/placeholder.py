"""Views for placeholder field structure information."""

from django.conf import settings
from django.db.models import F

from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core import enums, models


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
                "description": "Field slugs mapped to their verbose labels",
                "additionalProperties": {
                    "type": "string",
                    "description": "Verbose label for the field",
                },
                "example": {
                    "name": "Name",
                    "job_title": "Job title",
                    "is_elected": "Is elected",
                },
            },
        },
    )
    def get(self, request):
        """Get the structure of available fields."""
        current_language = settings.LANGUAGE_CODE.split("-")[0]
        fields = {
            "name": "Name",
            "recipient_name": "Recipient name",
        }
        # Add user custom attributes fields from schema
        schema = settings.SCHEMA_CUSTOM_ATTRIBUTES_USER
        schema_properties = schema.get("properties", {})
        for field_name, field_schema in schema_properties.items():
            # Check if there's internationalization
            i18n_data = field_schema.get("x-i18n", {})
            if "title" in i18n_data:
                label = i18n_data["title"].get(
                    current_language, i18n_data["title"].get("en", field_name)
                )
            else:
                # No internationalization, use schema title
                label = field_schema.get("title", field_name)
            fields[field_name] = label
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
