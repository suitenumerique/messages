"""Tests for the site configuration inline of the Site admin."""

import json

from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse

import pytest

from core import factories, models

pytestmark = pytest.mark.django_db

PREFIX = "configuration"


def _change_form_data(site, **configuration):
    """Build the POST payload of the Site change form with its inline."""
    data = {
        "domain": site.domain,
        "name": site.name,
        f"{PREFIX}-TOTAL_FORMS": "1",
        f"{PREFIX}-INITIAL_FORMS": "0",
        f"{PREFIX}-MIN_NUM_FORMS": "0",
        f"{PREFIX}-MAX_NUM_FORMS": "1",
        f"{PREFIX}-0-site": str(site.pk),
        f"{PREFIX}-0-service_message_variant": "brand",
        f"{PREFIX}-0-service_message": "{}",
        f"{PREFIX}-0-service_message_link_url": "",
        f"{PREFIX}-0-service_message_link_label": "{}",
    }
    data.update({f"{PREFIX}-0-{key}": value for key, value in configuration.items()})
    return data


@pytest.fixture(name="admin_client")
def fixture_admin_client(client):
    """A client logged in as a superuser."""
    client.force_login(factories.UserFactory(is_superuser=True, is_staff=True))
    return client


def test_admin_site_change_page_renders_configuration_inline(admin_client):
    """The Site change page embeds the site configuration form."""
    site = Site.objects.get(pk=settings.SITE_ID)

    response = admin_client.get(reverse("admin:sites_site_change", args=[site.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert f"{PREFIX}-0-service_message_enabled" in content
    # Fields are grouped under a "Service message" fieldset, so their labels
    # drop that prefix.
    assert 'class="fieldset-heading">Service message</h4>' in content
    assert ">Link label:</label>" in content
    assert "Service message enabled" not in content


def test_admin_site_change_saves_service_message(admin_client):
    """Saving the Site change form creates its configuration."""
    site = Site.objects.get(pk=settings.SITE_ID)

    response = admin_client.post(
        reverse("admin:sites_site_change", args=[site.pk]),
        _change_form_data(
            site,
            service_message_enabled="on",
            service_message_variant="warning",
            service_message=json.dumps({"fr-fr": "Maintenance ce soir"}),
        ),
    )

    assert response.status_code == 302
    assert site.configuration.get_service_message() == {
        "variant": "warning",
        "message": {"fr-fr": "Maintenance ce soir"},
    }


def test_admin_site_change_rejects_enabled_message_without_text(admin_client):
    """The admin surfaces the model validation errors."""
    site = Site.objects.get(pk=settings.SITE_ID)

    response = admin_client.post(
        reverse("admin:sites_site_change", args=[site.pk]),
        _change_form_data(site, service_message_enabled="on"),
    )

    assert response.status_code == 200
    assert "A message is required to enable it." in response.content.decode()
    assert not models.SiteConfiguration.objects.exists()
