"""Tests for the PlaceholderView."""

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core.factories import UserFactory


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
        """Test that the endpoint returns field structure with slugs and labels."""
        url = reverse("placeholders")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "Name"
        assert data["job_title"] == "Job title"
        assert data["is_elected"] == "Is elected"

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
    )
    def test_internationalization_support(self, api_client):
        """Test that internationalization is properly handled."""
        url = reverse("placeholders")
        response = api_client.get(url, HTTP_ACCEPT_LANGUAGE="fr-fr")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_title"] == "Fonction"
        assert data["is_elected"] == "Est élu"
        assert data["name"] == "Nom"

        # Test that fallback language handling works correctly.
        response = api_client.get(url, HTTP_ACCEPT_LANGUAGE="de-de")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_title"] == "Job title"
        assert data["is_elected"] == "Is elected"
        assert data["name"] == "Name"
