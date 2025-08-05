"""Tests for the MailDomain permissions system based on get_abilities."""
# pylint: disable=redefined-outer-name,unused-argument

from django.core.exceptions import ValidationError
from django.test import override_settings

import pytest

from core import models
from core.factories import MailDomainFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def maildomain():
    """Create a test mail domain."""
    return MailDomainFactory()


class TestMailDomainModel:
    """Test the MailDomain model."""

    def test_models_maildomain_name_validator(self):
        """Test the MailDomain name validator."""

        for name in [
            "?",
            "/",
            "x",
            "-invalid",
            "invalid-",
            "invalid.example.com/",
            "",
            "invalid.example.com ",
            " ",
        ]:
            with pytest.raises(ValidationError):
                MailDomainFactory(name=name)

        domain = MailDomainFactory(name="va-lid.example.com")
        assert domain.name == "va-lid.example.com"

    def test_models_maildomain_auto_generates_dkim_key(self):
        """Test that DKIM key is automatically generated when creating a new domain."""
        # Create a new domain - should automatically generate DKIM key
        domain = MailDomainFactory(name="test.example.com")

        # Verify a DKIM key was created
        dkim_key = domain.get_active_dkim_key()
        assert dkim_key is not None
        assert dkim_key.domain == domain
        assert dkim_key.is_active is True
        assert dkim_key.selector == "stmessages"  # Default selector
        assert dkim_key.private_key is not None
        assert dkim_key.public_key is not None

    def test_models_maildomain_no_duplicate_dkim_keys(self):
        """Test that no duplicate DKIM keys are generated."""
        # Create a domain with a DKIM key manually
        domain = MailDomainFactory(name="test.example.com")
        original_dkim_key = domain.get_active_dkim_key()

        # Save the domain again (should not create another DKIM key)
        domain.save()

        # Verify we still have only one DKIM key
        dkim_keys = models.DKIMKey.objects.filter(domain=domain)
        assert dkim_keys.count() == 1
        assert dkim_keys.first() == original_dkim_key

    @override_settings(
        SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/maildomain",
            "type": "object",
            "title": "Maildomain custom fields",
            "additionalProperties": False,
            "properties": {
                "siret": {
                    "type": "string",
                    "title": "Siret",
                    "default": "",
                    "minLength": 14,
                    "maxLength": 14,
                    "pattern": "^[0-9]{14}$",
                },
            },
            "required": [],
        }
    )
    def test_models_maildomain_custom_attributes_validation(self):
        """The custom attributes should be validated on save."""
        custom_attributes = {"siret": "0123456789abcd"}

        with pytest.raises(ValidationError) as exception_info:
            MailDomainFactory(custom_attributes=custom_attributes)
        assert (
            str(exception_info.value)
            == "{'custom_attributes': [\"'0123456789abcd' does not match '^[0-9]{14}$'\"]}"
        )

        # Fix the job title error
        custom_attributes = {"siret": "01234567890000"}
        user = MailDomainFactory(custom_attributes=custom_attributes)
        assert user.custom_attributes == custom_attributes

        # Try to save with an additional property should fail
        custom_attributes = {
            "siret": "01234567890000",
            "additional_property": "should fail",
        }
        with pytest.raises(ValidationError) as exception_info:
            MailDomainFactory(custom_attributes=custom_attributes)
        assert (
            str(exception_info.value)
            == "{'custom_attributes': [\"Additional properties are not allowed ('additional_property' was unexpected)\"]}"
        )


class TestMailDomainModelAbilities:
    """Test the get_abilities methods on MailDomain models."""

    def test_models_maildomain_get_abilities_no_access(self, user, maildomain):
        """Test MailDomain.get_abilities when user has no access."""
        abilities = maildomain.get_abilities(user)

        assert abilities["get"] is False
        assert abilities["patch"] is False
        assert abilities["put"] is False
        assert abilities["post"] is False
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["manage_mailboxes"] is False

    def test_models_maildomain_get_abilities_admin(self, user, maildomain):
        """Test MailDomain.get_abilities when user has admin access."""
        models.MailDomainAccess.objects.create(
            maildomain=maildomain,
            user=user,
            role=models.MailDomainAccessRoleChoices.ADMIN,
        )

        abilities = maildomain.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
        assert abilities["manage_accesses"] is True
        assert abilities["manage_mailboxes"] is True
