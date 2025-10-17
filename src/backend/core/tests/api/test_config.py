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
    DRIVE_CONFIG={"base_url": None},
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
        "LANGUAGES": [["en-us", "English"], ["fr-fr", "French"], ["de-de", "German"]],
        "LANGUAGE_CODE": "en-us",
        "AI_ENABLED": False,
        "FEATURE_AI_SUMMARY": False,
        "FEATURE_AI_AUTOLABELS": False,
        "SCHEMA_CUSTOM_ATTRIBUTES_USER": {},
        "SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN": {},
    }


@override_settings(
    DRIVE_CONFIG={
        "base_url": "http://localhost:8902",
        "sdk_url": "/sdk",
        "api_url": "/api/v1.0",
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
    }
