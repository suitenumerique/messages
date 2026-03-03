"""Tests for the provisioning maildomains endpoint."""
# pylint: disable=redefined-outer-name

from django.urls import reverse

import pytest

from core.factories import MailDomainFactory
from core.models import MailDomain


@pytest.fixture
def url():
    """Returns the URL for the provisioning maildomains endpoint."""
    return reverse("provisioning-maildomains")


@pytest.fixture
def auth_header(settings):
    """Returns the authentication header for the provisioning endpoint."""
    settings.PROVISIONING_API_KEY = "test-provisioning-key"
    return {"HTTP_AUTHORIZATION": "Bearer test-provisioning-key"}


# -- Authentication tests --


@pytest.mark.django_db
def test_provisioning_no_auth_returns_403(client, url):
    """Request without Authorization header returns 403."""
    response = client.post(
        url, data={"domains": ["test.fr"]}, content_type="application/json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_provisioning_wrong_token_returns_403(client, url, settings):
    """Request with wrong token returns 403."""
    settings.PROVISIONING_API_KEY = "correct-key"
    response = client.post(
        url,
        data={"domains": ["test.fr"]},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer wrong-key",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_provisioning_no_key_configured_returns_403(client, url, settings):
    """When PROVISIONING_API_KEY is not configured, returns 403."""
    settings.PROVISIONING_API_KEY = None
    response = client.post(
        url,
        data={"domains": ["test.fr"]},
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer some-key",
    )
    assert response.status_code == 403


# -- Create tests --


@pytest.mark.django_db
def test_provisioning_creates_domains(client, url, auth_header):
    """New domains are created with correct custom_attributes."""
    response = client.post(
        url,
        data={
            "domains": ["domaine.fr", "autre.fr"],
            "custom_attributes": {"siret": "12345678901234"},
        },
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert sorted(data["created"]) == ["autre.fr", "domaine.fr"]
    assert data["existing"] == []
    assert data["errors"] == []

    for name in ["domaine.fr", "autre.fr"]:
        domain = MailDomain.objects.get(name=name)
        assert domain.custom_attributes == {"siret": "12345678901234"}


@pytest.mark.django_db
def test_provisioning_creates_domains_without_custom_attributes(
    client, url, auth_header
):
    """Domains can be created without custom_attributes."""
    response = client.post(
        url,
        data={"domains": ["simple.fr"]},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == ["simple.fr"]

    domain = MailDomain.objects.get(name="simple.fr")
    assert domain.custom_attributes == {}


# -- Idempotency tests --


@pytest.mark.django_db
def test_provisioning_idempotent_existing_domains(client, url, auth_header):
    """Existing domains are not duplicated."""
    MailDomainFactory(name="existing.fr", custom_attributes={"siret": "111"})

    response = client.post(
        url,
        data={
            "domains": ["existing.fr", "new.fr"],
            "custom_attributes": {"siret": "111"},
        },
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == ["new.fr"]
    assert data["existing"] == ["existing.fr"]
    assert data["errors"] == []
    assert MailDomain.objects.filter(name="existing.fr").count() == 1


@pytest.mark.django_db
def test_provisioning_updates_custom_attributes_on_existing(client, url, auth_header):
    """custom_attributes are updated on existing domains when they differ."""
    MailDomainFactory(name="existing.fr", custom_attributes={"siret": "old"})

    response = client.post(
        url,
        data={
            "domains": ["existing.fr"],
            "custom_attributes": {"siret": "new"},
        },
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["existing"] == ["existing.fr"]

    domain = MailDomain.objects.get(name="existing.fr")
    assert domain.custom_attributes == {"siret": "new"}


# -- Comma-separated string format --


@pytest.mark.django_db
def test_provisioning_accepts_comma_separated_string(client, url, auth_header):
    """The endpoint accepts a comma-separated string of domains."""
    response = client.post(
        url,
        data={
            "domains": "alpha.fr,beta.fr",
            "custom_attributes": {"siret": "123"},
        },
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert sorted(data["created"]) == ["alpha.fr", "beta.fr"]


# -- Validation tests --


@pytest.mark.django_db
def test_provisioning_invalid_domain_returns_error(client, url, auth_header):
    """Invalid domain names return structured errors, not 500."""
    response = client.post(
        url,
        data={
            "domains": ["valid.fr", "INVALID DOMAIN!"],
            "custom_attributes": {"siret": "123"},
        },
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == ["valid.fr"]
    assert len(data["errors"]) == 1
    assert data["errors"][0]["domain"] == "INVALID DOMAIN!"


@pytest.mark.django_db
def test_provisioning_empty_domains_returns_400(client, url, auth_header):
    """Empty domains list returns 400."""
    response = client.post(
        url,
        data={"domains": [], "custom_attributes": {"siret": "123"}},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_provisioning_missing_domains_returns_400(client, url, auth_header):
    """Missing domains field returns 400."""
    response = client.post(
        url,
        data={"custom_attributes": {"siret": "123"}},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 400
