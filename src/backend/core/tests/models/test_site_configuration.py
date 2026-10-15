"""Tests for the SiteConfiguration model."""

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError

import pytest

from core import models

pytestmark = pytest.mark.django_db


def build_configuration(**kwargs):
    """Build a site configuration bound to the current site."""
    return models.SiteConfiguration(
        site=Site.objects.get(pk=settings.SITE_ID), **kwargs
    )


def test_models_site_configuration_defaults():
    """A fresh configuration has no service message."""
    configuration = build_configuration()
    configuration.save()

    assert configuration.service_message_enabled is False
    assert configuration.service_message_variant == "brand"
    assert configuration.get_service_message() is None


def test_models_site_configuration_one_per_site():
    """A site holds a single configuration."""
    build_configuration().save()

    with pytest.raises(ValidationError, match="site"):
        build_configuration().save()


@pytest.mark.parametrize(
    "service_message",
    [
        {"xx-xx": "Unknown language"},
        {"en-us": 42},
        ["en-us", "Not an object"],
    ],
)
def test_models_site_configuration_invalid_translations(service_message):
    """Translations must map configured language codes to text."""
    with pytest.raises(ValidationError) as excinfo:
        build_configuration(service_message=service_message).save()

    assert "service_message" in excinfo.value.message_dict


@pytest.mark.parametrize("service_message", [{}, {"en-us": "  "}])
def test_models_site_configuration_enabled_requires_message(service_message):
    """An enabled service message must have some text to display."""
    with pytest.raises(ValidationError) as excinfo:
        build_configuration(
            service_message_enabled=True, service_message=service_message
        ).save()

    assert excinfo.value.message_dict == {
        "service_message": ["A message is required to enable it."]
    }


def test_models_site_configuration_link_requires_label():
    """A link needs a label as the banner renders it as a button."""
    with pytest.raises(ValidationError) as excinfo:
        build_configuration(
            service_message_link_url="https://status.example.com"
        ).save()

    assert excinfo.value.message_dict == {
        "service_message_link_label": ["A label is required for the link."]
    }


def test_models_site_configuration_get_service_message():
    """The service message keeps non-blank translations only."""
    configuration = build_configuration(
        service_message_enabled=True,
        service_message_variant="warning",
        service_message={"en-us": " Maintenance tonight ", "fr-fr": ""},
        service_message_link_url="https://status.example.com",
        service_message_link_label={"en-us": "Status", "fr-fr": "Statut"},
    )
    configuration.save()

    assert configuration.get_service_message() == {
        "variant": "warning",
        "message": {"en-us": "Maintenance tonight"},
        "link": {
            "url": "https://status.example.com",
            "label": {"en-us": "Status", "fr-fr": "Statut"},
        },
    }


def test_models_site_configuration_get_service_message_without_link():
    """The link is omitted when no URL is configured."""
    configuration = build_configuration(
        service_message_enabled=True,
        service_message={"fr-fr": "Maintenance ce soir"},
    )
    configuration.save()

    assert configuration.get_service_message() == {
        "variant": "brand",
        "message": {"fr-fr": "Maintenance ce soir"},
    }
