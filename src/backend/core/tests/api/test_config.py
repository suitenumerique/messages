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
    }
