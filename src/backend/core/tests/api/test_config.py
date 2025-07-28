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
    POSTHOG_KEY="132456",
    POSTHOG_HOST="https://test.i.posthog-test.com",
    POSTHOG_SURVEY_ID="7890",
    LANGUAGES=[["en-us", "English"], ["fr-fr", "French"], ["de-de", "German"]],
    LANGUAGE_CODE="en-us",
    AI_API_KEY=None,
    AI_BASE_URL=None,
    AI_MODEL=None,
    AI_FEATURE_SUMMARY_ENABLED=False,
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
        "POSTHOG_KEY": "132456",
        "POSTHOG_HOST": "https://test.i.posthog-test.com",
        "POSTHOG_SURVEY_ID": "7890",
        "AI_ENABLED": False,
        "AI_FEATURE_SUMMARY_ENABLED": False,
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
