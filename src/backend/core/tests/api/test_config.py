"""
Test config API endpoints in the messages core app.
"""

from django.test import override_settings

import pytest
from rest_framework.status import (
    HTTP_200_OK,
)
from rest_framework.test import APIClient

from core import factories

pytestmark = pytest.mark.django_db


@override_settings(
    LANGUAGES=[["en-us", "English"], ["fr-fr", "French"], ["de-de", "German"]],
    LANGUAGE_CODE="en-us",
    AI_API_KEY=None,
    AI_BASE_URL=None,
    AI_MODEL=None,
    FEATURE_AI_SUMMARY=False,
    FEATURE_AI_AUTOLABELS=False,
    FEATURE_MAILBOX_ADMIN_CHANNELS=[],
    FEATURE_MAILDOMAIN_CREATE=True,
    FEATURE_MAILDOMAIN_MANAGE_ACCESSES=True,
    FEATURE_THREAD_SPLIT=True,
    FEATURE_MAILDOMAIN_MANAGE_TOTP=False,
    DRIVE_CONFIG={"base_url": None, "app_name": "Drive"},
    MAX_OUTGOING_ATTACHMENT_SIZE=20971520,  # 20MB
    MAX_OUTGOING_BODY_SIZE=5242880,  # 5MB
    MAX_RECIPIENTS_PER_MESSAGE=42,
    MAX_TEMPLATE_IMAGE_SIZE=2097152,  # 2MB
    IMAGE_PROXY_ENABLED=False,
    MESSAGES_MANUAL_RETRY_MAX_AGE=86400,  # 1 day in seconds
    FRONTEND_SILENT_LOGIN_ENABLED=True,
    RELEASE="1.2.3",
)
@pytest.mark.parametrize("is_authenticated", [False, True])
def test_api_config(is_authenticated):
    """Anonymous users should be allowed to get the configuration."""
    client = APIClient()

    if is_authenticated:
        user = factories.UserFactory()
        client.force_login(user)

    response = client.get("/api/v1.0/config/")
    assert response.status_code == HTTP_200_OK
    assert response.json() == {
        "ENVIRONMENT": "test",
        "RELEASE": "1.2.3",
        "LANGUAGES": [["en-us", "English"], ["fr-fr", "French"], ["de-de", "German"]],
        "LANGUAGE_CODE": "en-us",
        "AI_ENABLED": False,
        "FEATURE_AI_SUMMARY": False,
        "FEATURE_AI_AUTOLABELS": False,
        "FEATURE_MAILBOX_ADMIN_CHANNELS": [],
        "FEATURE_MAILDOMAIN_CREATE": True,
        "FEATURE_MAILDOMAIN_MANAGE_ACCESSES": True,
        "FEATURE_THREAD_SPLIT": True,
        "FEATURE_MAILDOMAIN_MANAGE_TOTP": False,
        "SCHEMA_CUSTOM_ATTRIBUTES_USER": {},
        "SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN": {},
        "MAX_OUTGOING_ATTACHMENT_SIZE": 20971520,
        "MAX_RECIPIENTS_PER_MESSAGE": 42,
        "MAX_TEMPLATE_IMAGE_SIZE": 2097152,
        "IMAGE_PROXY_ENABLED": False,
        "MESSAGES_MANUAL_RETRY_MAX_AGE": 86400,
        "FRONTEND_SILENT_LOGIN_ENABLED": True,
    }
    # Optional settings left unconfigured must be omitted, not null nor
    # defaulted: the frontend falls back on its deprecated NEXT_PUBLIC_*
    # variables when a key is absent, so sending a backend default here
    # would silently override them.
    assert "SENTRY_DSN" not in response.json()
    assert "DRIVE" not in response.json()
    assert "FRONTEND_THEME_CONFIG" not in response.json()
    assert "FRONTEND_FORCED_DEFAULT_LANGUAGE" not in response.json()
    assert "FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB" not in response.json()
    assert "FRONTEND_HELP_CENTER_URL" not in response.json()
    assert "FRONTEND_FEEDBACK_WIDGET_CONFIG" not in response.json()
    assert "FRONTEND_LAGAUFRE_WIDGET_CONFIG" not in response.json()


@override_settings(
    DRIVE_CONFIG={
        "base_url": "http://localhost:8902",
        "sdk_url": "/sdk",
        "api_url": "/api/v1.0",
        "file_url": "/explorer/items/files",
        "preview_url": "/media/preview/item",
        "app_name": "Drive App",
    }
)
def test_api_config_with_external_services():
    """If Drive external service is configured, it should be included in the configuration."""
    client = APIClient()

    response = client.get("/api/v1.0/config/")
    assert response.status_code == HTTP_200_OK
    assert response.json().get("DRIVE") == {
        "sdk_url": "http://localhost:8902/sdk",
        "api_url": "http://localhost:8902/api/v1.0",
        "file_url": "http://localhost:8902/explorer/items/files",
        "preview_url": "http://localhost:8902/media/preview/item",
        "app_name": "Drive App",
    }


@override_settings(
    SENTRY_DSN="https://public@sentry.example.com/1",
    FRONTEND_THEME_CONFIG={"theme": "dsfr", "terms_of_service_url": "https://tos"},
    FRONTEND_FORCED_DEFAULT_LANGUAGE=False,
    FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB=50,
    FRONTEND_HELP_CENTER_URL="https://help.example.com",
    FRONTEND_FEEDBACK_WIDGET_CONFIG={
        "api_url": "https://feedback.example.com",
        "path": "https://feedback.example.com/static/",
        "channel": "support",
        "home_channel": "home",
    },
    FRONTEND_LAGAUFRE_WIDGET_CONFIG={
        "api_url": "https://lagaufre.example.com",
        "path": "https://lagaufre.example.com/static/",
    },
)
def test_api_config_frontend_settings():
    """Frontend settings configured on the backend should be exposed as-is."""
    response = APIClient().get("/api/v1.0/config/")
    assert response.status_code == HTTP_200_OK
    config = response.json()
    assert config["SENTRY_DSN"] == "https://public@sentry.example.com/1"
    assert config["FRONTEND_THEME_CONFIG"] == {
        "theme": "dsfr",
        "terms_of_service_url": "https://tos",
    }
    # An explicit False must be sent: the backend value takes precedence
    # over the frontend fallbacks, even when it equals their default.
    assert config["FRONTEND_FORCED_DEFAULT_LANGUAGE"] is False
    assert config["FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB"] == 50
    assert config["FRONTEND_HELP_CENTER_URL"] == "https://help.example.com"
    assert config["FRONTEND_FEEDBACK_WIDGET_CONFIG"]["channel"] == "support"
    assert config["FRONTEND_LAGAUFRE_WIDGET_CONFIG"]["api_url"] == (
        "https://lagaufre.example.com"
    )


@override_settings(
    FEATURE_MAILDOMAIN_MANAGE_TOTP=True,
    KEYCLOAK_TOTP_ROLE_ID=None,
    IDENTITY_PROVIDER="keycloak",
)
def test_api_config_totp_flag_is_effective_not_raw():
    """Frontend must not see TOTP enabled when the backend can't enforce it.

    The raw ``FEATURE_MAILDOMAIN_MANAGE_TOTP`` flag is True here, but a
    missing role id means the backend cannot actually carry out toggles —
    so the config endpoint must report False.
    """
    response = APIClient().get("/api/v1.0/config/")
    assert response.status_code == HTTP_200_OK
    assert response.json()["FEATURE_MAILDOMAIN_MANAGE_TOTP"] is False
