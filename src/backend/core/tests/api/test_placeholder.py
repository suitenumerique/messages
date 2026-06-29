"""Tests for the PlaceholderView."""

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core.factories import UserFactory

# Custom attributes schema providing x-i18n labels, shared by the i18n tests.
SCHEMA_WITH_I18N = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/user",
    "type": "object",
    "title": "User custom fields",
    "additionalProperties": False,
    "properties": {
        "job_title": {
            "type": "string",
            "title": "Job title",
            "default": "",
            "description": "The job name of the user",
            "minLength": 3,
            "x-i18n": {
                "title": {"fr": "Fonction", "en": "Job title"},
                "description": {
                    "fr": "Le nom de la fonction de l'utilisateur",
                    "en": "The job name of the user",
                },
            },
        },
        "is_elected": {
            "type": "boolean",
            "title": "Is elected",
            "default": False,
            "description": "Whether the user is elected",
            "x-i18n": {
                "title": {"fr": "Est élu", "en": "Is elected"},
                "description": {
                    "fr": "Indique si l'utilisateur est élu",
                    "en": "Indicates if the user is elected",
                },
            },
        },
    },
    "required": [],
}


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return UserFactory(
        full_name="John Doe",
        email="john@example.com",
        language="fr-fr",
        custom_attributes={"job_title": "Developer", "is_elected": False},
    )


@pytest.fixture(name="api_client")
def fixture_api_client(user):
    """Create an authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestPlaceholderView:
    """Test the PlaceholderView."""

    def test_authentication_required(self):
        """Test that authentication is required."""
        client = APIClient()
        url = reverse("placeholders")
        response = client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @override_settings(
        SCHEMA_CUSTOM_ATTRIBUTES_USER={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/user",
            "type": "object",
            "title": "User custom fields",
            "additionalProperties": False,
            "properties": {
                "job_title": {
                    "type": "string",
                    "title": "Job title",
                    "default": "",
                    "minLength": 3,
                },
                "is_elected": {
                    "type": "boolean",
                    "title": "Is elected",
                    "default": False,
                },
            },
            "required": [],
        }
    )
    def test_get_fields_structure(self, api_client):
        """Built-in fields are empty; custom fields expose their schema title."""
        url = reverse("placeholders")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Built-in fields carry no label (localized client-side).
        assert data["name"] == {}
        assert data["recipient_name"] == {}
        assert data["user_name"] == {}
        # Custom fields without x-i18n expose their schema title only.
        assert data["job_title"] == {"title": "Job title"}
        # Non-string custom fields (e.g. the boolean "is_elected") are excluded.
        assert "is_elected" not in data

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_WITH_I18N)
    def test_returns_x_i18n_translations_for_custom_fields(self, api_client):
        """Custom fields expose their x-i18n title translations for the frontend."""
        url = reverse("placeholders")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Built-in fields remain unlabeled regardless of the schema.
        assert data["name"] == {}
        assert data["recipient_name"] == {}
        assert data["user_name"] == {}
        # String custom fields ship every available translation; frontend picks one.
        assert data["job_title"] == {
            "title": "Job title",
            "i18n": {"fr": "Fonction", "en": "Job title"},
        }
        # Non-string custom fields (e.g. the boolean "is_elected") are excluded.
        assert "is_elected" not in data
