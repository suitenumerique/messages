"""Views for placeholder field structure information."""

from django.conf import settings
from django.utils import translation

from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView


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
                    "full_name": "Full name",
                    "job_title": "Job title",
                    "is_elected": "Is elected",
                },
            },
        },
    )
    def get(self, request):
        """Get the structure of available fields."""
        current_language = translation.get_language().split("-")[0]
        fields = {"full_name": translation.gettext_lazy("Full name")}
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
