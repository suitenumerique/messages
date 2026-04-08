"""Tests for the provisioning maildomains endpoint."""
# pylint: disable=redefined-outer-name

import uuid

from django.urls import reverse

import pytest

from core.enums import ChannelApiKeyScope, ChannelScopeLevel
from core.factories import MailDomainFactory, make_api_key_channel
from core.models import MailDomain


def _make_api_key_channel(
    scopes=(ChannelApiKeyScope.MAILDOMAINS_CREATE.value,), **kwargs
):
    """Wrapper around the shared factory pre-loaded with the
    maildomains-write scope used by every test in this module."""
    kwargs.setdefault("name", "provisioning-test")
    return make_api_key_channel(scopes=scopes, **kwargs)


@pytest.fixture
def url():
    """Returns the URL for the provisioning maildomains endpoint."""
    return reverse("provisioning-maildomains")


@pytest.fixture
def auth_header():
    """Global-scope api_key with ChannelApiKeyScope.MAILDOMAINS_CREATE."""
    channel, plaintext = _make_api_key_channel()
    return {
        "HTTP_X_CHANNEL_ID": str(channel.id),
        "HTTP_X_API_KEY": plaintext,
    }


# -- Authentication tests --


@pytest.mark.django_db
def test_provisioning_no_auth_returns_401(client, url):
    """Request without X-Channel-Id/X-API-Key returns 401 (not authenticated)."""
    response = client.post(
        url, data={"domains": ["test.fr"]}, content_type="application/json"
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_provisioning_wrong_token_returns_401(client, url):
    """Request with wrong token returns 401 (invalid credentials)."""
    channel, _plaintext = _make_api_key_channel()
    response = client.post(
        url,
        data={"domains": ["test.fr"]},
        content_type="application/json",
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY="not-the-real-key",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_provisioning_unknown_channel_returns_401(client, url):
    """Unknown channel id returns 401 (invalid credentials)."""
    response = client.post(
        url,
        data={"domains": ["test.fr"]},
        content_type="application/json",
        HTTP_X_CHANNEL_ID=str(uuid.uuid4()),
        HTTP_X_API_KEY="anything",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_provisioning_non_global_scope_returns_403(client, url):
    """A maildomain-scope key cannot call maildomain provisioning."""
    domain = MailDomainFactory(name="test.fr")
    channel, plaintext = _make_api_key_channel(
        scope_level=ChannelScopeLevel.MAILDOMAIN,
        maildomain=domain,
    )
    response = client.post(
        url,
        data={"domains": ["new.fr"]},
        content_type="application/json",
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=plaintext,
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


# -- oidc_autojoin and identity_sync tests --


@pytest.mark.django_db
def test_provisioning_default_oidc_autojoin_true(client, url, auth_header):
    """oidc_autojoin defaults to True when not provided."""
    response = client.post(
        url,
        data={"domains": ["autojoin.fr"]},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="autojoin.fr")
    assert domain.oidc_autojoin is True


@pytest.mark.django_db
def test_provisioning_default_identity_sync_false(client, url, auth_header):
    """identity_sync defaults to False when not provided."""
    response = client.post(
        url,
        data={"domains": ["sync.fr"]},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="sync.fr")
    assert domain.identity_sync is False


@pytest.mark.django_db
def test_provisioning_explicit_oidc_autojoin_false(client, url, auth_header):
    """oidc_autojoin can be explicitly set to False."""
    response = client.post(
        url,
        data={"domains": ["nojoin.fr"], "oidc_autojoin": False},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="nojoin.fr")
    assert domain.oidc_autojoin is False


@pytest.mark.django_db
def test_provisioning_explicit_identity_sync_true(client, url, auth_header):
    """identity_sync can be explicitly set to True."""
    response = client.post(
        url,
        data={"domains": ["synced.fr"], "identity_sync": True},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="synced.fr")
    assert domain.identity_sync is True


@pytest.mark.django_db
def test_provisioning_updates_oidc_autojoin_on_existing(client, url, auth_header):
    """oidc_autojoin is updated on existing domains when it differs."""
    MailDomainFactory(name="existing.fr", oidc_autojoin=True)

    response = client.post(
        url,
        data={"domains": ["existing.fr"], "oidc_autojoin": False},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="existing.fr")
    assert domain.oidc_autojoin is False


@pytest.mark.django_db
def test_provisioning_updates_identity_sync_on_existing(client, url, auth_header):
    """identity_sync is updated on existing domains when it differs."""
    MailDomainFactory(name="existing.fr", identity_sync=False)

    response = client.post(
        url,
        data={"domains": ["existing.fr"], "identity_sync": True},
        content_type="application/json",
        **auth_header,
    )
    assert response.status_code == 200
    domain = MailDomain.objects.get(name="existing.fr")
    assert domain.identity_sync is True
