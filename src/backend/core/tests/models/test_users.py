"""
Unit tests for the User model
"""

from django.core.exceptions import ValidationError
from django.test import override_settings

import pytest

from core import factories

pytestmark = pytest.mark.django_db


class TestUserModel:
    """Test the User model."""

    def test_models_user_str(self):
        """The str representation should be the email."""
        user = factories.UserFactory()
        assert str(user) == user.email

    def test_models_user_id_unique(self):
        """The "id" field should be unique."""
        user = factories.UserFactory()
        with pytest.raises(ValidationError):
            factories.UserFactory(id=user.id)

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
            },
            "required": [],
        }
    )
    def test_models_user_custom_attributes_validation(self):
        """The custom attributes should be validated on save."""
        custom_attributes = {"job_title": "te"}
        with pytest.raises(ValidationError) as exception_info:
            factories.UserFactory(custom_attributes=custom_attributes)
        assert (
            str(exception_info.value)
            == "{'custom_attributes': [\"'te' is too short\"]}"
        )

        # Fix the job title error
        custom_attributes = {"job_title": "test"}
        user = factories.UserFactory(custom_attributes=custom_attributes)
        assert user.custom_attributes == custom_attributes

        # Try to save with an additional property should fail
        custom_attributes = {"job_title": "test", "additional_property": "should fail"}
        with pytest.raises(ValidationError) as exception_info:
            factories.UserFactory(custom_attributes=custom_attributes)
        assert (
            str(exception_info.value)
            == "{'custom_attributes': [\"Additional properties are not allowed ('additional_property' was unexpected)\"]}"
        )
