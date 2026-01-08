"""API ViewSet for sharing some public settings."""

from django.conf import settings

import rest_framework as drf
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny

from core.ai.utils import is_ai_enabled, is_ai_summary_enabled, is_auto_labels_enabled


class ConfigView(drf.views.APIView):
    """API ViewSet for sharing some public settings."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["config"],
        responses={
            200: OpenApiResponse(
                description="A dictionary of public configuration settings.",
                response={
                    "type": "object",
                    "properties": {
                        "ENVIRONMENT": {"type": "string", "readOnly": True},
                        "LANGUAGES": {
                            "type": "array",
                            "items": {"type": "string"},
                            "readOnly": True,
                        },
                        "LANGUAGE_CODE": {"type": "string", "readOnly": True},
                        "AI_ENABLED": {"type": "boolean", "readOnly": True},
                        "FEATURE_AI_SUMMARY": {
                            "type": "boolean",
                            "readOnly": True,
                        },
                        "FEATURE_AI_AUTOLABELS": {
                            "type": "boolean",
                            "readOnly": True,
                        },
                        "DRIVE": {
                            "type": "object",
                            "description": "The URLs of the Drive external service.",
                            "properties": {
                                "sdk_url": {
                                    "type": "string",
                                    "readOnly": True,
                                },
                                "api_url": {
                                    "type": "string",
                                    "readOnly": True,
                                },
                                "file_url": {
                                    "type": "string",
                                    "readOnly": True,
                                },
                                "app_name": {
                                    "type": "string",
                                    "readOnly": True,
                                },
                            },
                            "readOnly": True,
                            "required": ["sdk_url", "api_url", "file_url", "app_name"],
                        },
                        "SCHEMA_CUSTOM_ATTRIBUTES_USER": {
                            "type": "object",
                            "readOnly": True,
                        },
                        "SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN": {
                            "type": "object",
                            "readOnly": True,
                        },
                        "MAX_OUTGOING_ATTACHMENT_SIZE": {
                            "type": "integer",
                            "description": "Maximum size in bytes for outgoing email attachments",
                            "readOnly": True,
                        },
                        "MAX_OUTGOING_BODY_SIZE": {
                            "type": "integer",
                            "description": "Maximum size in bytes for outgoing email body (text + HTML)",
                            "readOnly": True,
                        },
                        "MAX_INCOMING_EMAIL_SIZE": {
                            "type": "integer",
                            "description": (
                                "Maximum size in bytes for incoming email "
                                "(including attachments and body)"
                            ),
                            "readOnly": True,
                        },
                        "MAX_RECIPIENTS_PER_MESSAGE": {
                            "type": "integer",
                            "description": (
                                "Maximum number of recipients per message "
                                "(to + cc + bcc)"
                            ),
                            "readOnly": True,
                        },
                        "IMAGE_PROXY_ENABLED": {
                            "type": "boolean",
                            "description": "Whether external images should be proxied",
                            "readOnly": True,
                        },
                    },
                    "required": [
                        "ENVIRONMENT",
                        "LANGUAGES",
                        "LANGUAGE_CODE",
                        "AI_ENABLED",
                        "FEATURE_AI_SUMMARY",
                        "FEATURE_AI_AUTOLABELS",
                        "SCHEMA_CUSTOM_ATTRIBUTES_USER",
                        "SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN",
                        "MAX_OUTGOING_ATTACHMENT_SIZE",
                        "MAX_OUTGOING_BODY_SIZE",
                        "MAX_INCOMING_EMAIL_SIZE",
                        "MAX_RECIPIENTS_PER_MESSAGE",
                        "IMAGE_PROXY_ENABLED",
                    ],
                },
            )
        },
        description="Return a dictionary of public settings for the frontend to consume.",
    )
    def get(self, request):
        """
        GET /api/v1.0/config/
            Return a dictionary of public settings.
        """
        array_settings = [
            "ENVIRONMENT",
            "LANGUAGES",
            "LANGUAGE_CODE",
            "SCHEMA_CUSTOM_ATTRIBUTES_USER",
            "SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN",
            "IMAGE_PROXY_ENABLED",
        ]
        dict_settings = {}
        for setting in array_settings:
            if hasattr(settings, setting):
                dict_settings[setting] = getattr(settings, setting)

        # AI Features
        dict_settings["AI_ENABLED"] = is_ai_enabled()
        dict_settings["FEATURE_AI_SUMMARY"] = is_ai_summary_enabled()
        dict_settings["FEATURE_AI_AUTOLABELS"] = is_auto_labels_enabled()

        # Email size limits
        dict_settings["MAX_OUTGOING_ATTACHMENT_SIZE"] = (
            settings.MAX_OUTGOING_ATTACHMENT_SIZE
        )
        dict_settings["MAX_OUTGOING_BODY_SIZE"] = settings.MAX_OUTGOING_BODY_SIZE
        dict_settings["MAX_INCOMING_EMAIL_SIZE"] = settings.MAX_INCOMING_EMAIL_SIZE
        dict_settings["MAX_RECIPIENTS_PER_MESSAGE"] = (
            settings.MAX_RECIPIENTS_PER_MESSAGE
        )

        # Drive service
        if base_url := settings.DRIVE_CONFIG.get("base_url"):
            dict_settings.update(
                {
                    "DRIVE": {
                        "sdk_url": f"{base_url}{settings.DRIVE_CONFIG.get('sdk_url')}",
                        "api_url": f"{base_url}{settings.DRIVE_CONFIG.get('api_url')}",
                        "file_url": f"{base_url}{settings.DRIVE_CONFIG.get('file_url')}",
                        "app_name": settings.DRIVE_CONFIG.get("app_name"),
                    }
                }
            )

        return drf.response.Response(dict_settings)
