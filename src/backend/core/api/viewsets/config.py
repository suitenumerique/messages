"""API ViewSet for sharing some public settings."""

from dataclasses import dataclass, field
from typing import Any, Callable

from django.conf import settings

import rest_framework as drf
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny

from core.ai.utils import is_ai_enabled, is_ai_summary_enabled, is_auto_labels_enabled
from core.services.identity.keycloak import is_mandatory_totp_enabled


def _get_drive_config():
    """Build the Drive external service URLs, or None when not configured."""
    base_url = settings.DRIVE_CONFIG.get("base_url")
    if not base_url:
        return None
    return {
        "sdk_url": f"{base_url}{settings.DRIVE_CONFIG.get('sdk_url')}",
        "api_url": f"{base_url}{settings.DRIVE_CONFIG.get('api_url')}",
        "file_url": f"{base_url}{settings.DRIVE_CONFIG.get('file_url')}",
        "preview_url": f"{base_url}{settings.DRIVE_CONFIG.get('preview_url')}",
        "app_name": settings.DRIVE_CONFIG.get("app_name"),
    }


@dataclass(frozen=True)
class ConfigEntry:
    """A public setting exposed to the frontend through the config endpoint."""

    key: str
    schema: dict = field(default_factory=lambda: {"type": "string"})
    # Defaults to reading the setting named `key`; entries whose value is
    # computed (feature flags, composed URLs) provide their own getter.
    getter: Callable[[], Any] | None = None
    # Non-required entries are omitted from the response when their value
    # is None, and generated as optional fields in the API client.
    required: bool = True

    def resolve(self):
        """Return the value to expose for this entry."""
        if self.getter is not None:
            return self.getter()
        return getattr(settings, self.key, None)


CONFIG_ENTRIES = (
    ConfigEntry("ENVIRONMENT"),
    ConfigEntry(
        "RELEASE",
        {"type": "string", "description": "Version of the application"},
    ),
    ConfigEntry(
        "LANGUAGES",
        {
            "type": "array",
            "description": "Available languages, as (code, label) pairs",
            "items": {"type": "array", "items": {"type": "string"}},
        },
    ),
    ConfigEntry("LANGUAGE_CODE"),
    ConfigEntry("AI_ENABLED", {"type": "boolean"}, getter=is_ai_enabled),
    ConfigEntry(
        "FEATURE_AI_SUMMARY", {"type": "boolean"}, getter=is_ai_summary_enabled
    ),
    ConfigEntry(
        "FEATURE_AI_AUTOLABELS", {"type": "boolean"}, getter=is_auto_labels_enabled
    ),
    ConfigEntry(
        "FEATURE_MAILBOX_ADMIN_CHANNELS", {"type": "array", "items": {"type": "string"}}
    ),
    ConfigEntry(
        "DRIVE",
        {
            "type": "object",
            "description": "The URLs of the Drive external service.",
            "properties": {
                "sdk_url": {"type": "string"},
                "api_url": {"type": "string"},
                "file_url": {"type": "string"},
                "preview_url": {"type": "string"},
                "app_name": {"type": "string"},
            },
            "required": ["sdk_url", "api_url", "file_url", "preview_url", "app_name"],
        },
        getter=_get_drive_config,
        required=False,
    ),
    ConfigEntry("SCHEMA_CUSTOM_ATTRIBUTES_USER", {"type": "object"}),
    ConfigEntry("SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN", {"type": "object"}),
    ConfigEntry(
        "MAX_OUTGOING_ATTACHMENT_SIZE",
        {
            "type": "integer",
            "description": "Maximum size in bytes for outgoing email attachments",
        },
    ),
    ConfigEntry(
        "MAX_RECIPIENTS_PER_MESSAGE",
        {
            "type": "integer",
            "description": "Maximum number of recipients per message (to + cc + bcc)",
        },
    ),
    ConfigEntry(
        "MAX_TEMPLATE_IMAGE_SIZE",
        {
            "type": "integer",
            "description": (
                "Maximum size in bytes for images embedded in templates and signatures"
            ),
        },
    ),
    ConfigEntry(
        "IMAGE_PROXY_ENABLED",
        {
            "type": "boolean",
            "description": "Whether external images should be proxied",
        },
    ),
    ConfigEntry(
        "MESSAGE_TRUSTED_LINK_DOMAINS",
        {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Hostnames whose external links skip the redirect confirmation "
                "modal (a leading *. wildcard also matches subdomains)"
            ),
        },
    ),
    ConfigEntry("FEATURE_MAILDOMAIN_CREATE", {"type": "boolean"}),
    ConfigEntry("FEATURE_MAILDOMAIN_MANAGE_ACCESSES", {"type": "boolean"}),
    ConfigEntry("FEATURE_THREAD_SPLIT", {"type": "boolean"}),
    # Expose the *effective* mandatory-TOTP capability rather than the raw
    # flag: the feature also requires IDENTITY_PROVIDER == "keycloak" and a
    # populated KEYCLOAK_TOTP_ROLE_ID. Surfacing the raw flag would let the
    # frontend render TOTP affordances that the backend silently refuses.
    ConfigEntry(
        "FEATURE_MAILDOMAIN_MANAGE_TOTP",
        {"type": "boolean"},
        getter=is_mandatory_totp_enabled,
    ),
    ConfigEntry(
        "MESSAGES_MANUAL_RETRY_MAX_AGE",
        {
            "type": "integer",
            "description": (
                "Maximum age in seconds for a message to be eligible "
                "for manual retry of failed deliveries"
            ),
        },
    ),
    ConfigEntry(
        "FRONTEND_SILENT_LOGIN_ENABLED",
        {
            "type": "boolean",
            "description": "Whether silent OIDC login is enabled",
        },
    ),
    ConfigEntry(
        "SENTRY_DSN",
        {"type": "string", "description": "Sentry DSN shared with the frontend"},
        required=False,
    ),
    # The FRONTEND_* entries below must stay optional (omitted when the
    # backend setting is unset) as long as the frontend keeps its deprecated
    # NEXT_PUBLIC_* fallbacks: sending a backend default here would silently
    # override those build-time variables.
    ConfigEntry(
        "FRONTEND_THEME_CONFIG",
        {
            "type": "object",
            "description": (
                "Theme configuration for the frontend "
                "(theme, terms_of_service_url, footer)"
            ),
        },
        required=False,
    ),
    ConfigEntry(
        "FRONTEND_FORCED_DEFAULT_LANGUAGE",
        {
            "type": "boolean",
            "description": (
                "Whether the frontend should fall back to LANGUAGE_CODE "
                "instead of the browser language"
            ),
        },
        required=False,
    ),
    ConfigEntry(
        "FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB",
        {
            "type": "integer",
            "description": "Chunk size in MB for frontend multipart uploads",
        },
        required=False,
    ),
    ConfigEntry(
        "FRONTEND_HELP_CENTER_URL",
        {"type": "string", "description": "Help center URL"},
        required=False,
    ),
    ConfigEntry(
        "FRONTEND_FEEDBACK_WIDGET_CONFIG",
        {
            "type": "object",
            "description": "Configuration of the feedback widget",
            "properties": {
                "api_url": {"type": "string"},
                "path": {"type": "string"},
                "channel": {"type": "string"},
                "home_channel": {"type": "string"},
            },
        },
        required=False,
    ),
    ConfigEntry(
        "FRONTEND_LAGAUFRE_WIDGET_CONFIG",
        {
            "type": "object",
            "description": "Configuration of the Lagaufre widget",
            "properties": {
                "api_url": {"type": "string"},
                "path": {"type": "string"},
            },
        },
        required=False,
    ),
)


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
                        entry.key: {**entry.schema, "readOnly": True}
                        for entry in CONFIG_ENTRIES
                    },
                    "required": [
                        entry.key for entry in CONFIG_ENTRIES if entry.required
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
        return drf.response.Response(
            {
                entry.key: value
                for entry in CONFIG_ENTRIES
                if (value := entry.resolve()) is not None or entry.required
            }
        )
