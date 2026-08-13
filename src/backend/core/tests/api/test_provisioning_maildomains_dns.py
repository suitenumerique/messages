"""Tests for the maildomain DNS provisioning endpoint."""
# pylint: disable=redefined-outer-name, unused-argument

import uuid

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

import pytest

from core.enums import ChannelApiKeyScope, ChannelScopeLevel
from core.factories import MailDomainFactory, make_api_key_channel

pytestmark = pytest.mark.django_db


@pytest.fixture
def url():
    """Returns the URL for the maildomain DNS provisioning endpoint."""
    return reverse("provisioning-maildomains-dns")


@pytest.fixture
def correctly_configured_header(db):
    """
    Returns the authentication headers for the endpoint via a
    scope_level=global api_key Channel with maildomains:read.
    """
    channel, plaintext = make_api_key_channel(
        scopes=(ChannelApiKeyScope.MAILDOMAINS_READ.value,),
        name="provisioning-dns-test",
    )
    return {
        "HTTP_X_CHANNEL_ID": str(channel.id),
        "HTTP_X_API_KEY": plaintext,
    }


class TestProvisioningMailDomainsDNS:
    """Tests for the maildomain DNS provisioning endpoint."""

    def test_requires_auth(self, api_client, url, correctly_configured_header):
        """Requires a valid api_key Channel with maildomains:read to access."""

        assert api_client.get(url).status_code == 401

        response = api_client.get(
            url,
            HTTP_X_CHANNEL_ID=str(uuid.uuid4()),
            HTTP_X_API_KEY="invalid_token",
        )
        assert response.status_code == 401

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

    def test_requires_maildomains_read_scope(self, api_client, url):
        """An api_key Channel without maildomains:read is rejected."""

        channel, plaintext = make_api_key_channel(
            scopes=(ChannelApiKeyScope.MAILBOXES_READ.value,),
            name="no-maildomains-read-scope",
        )
        response = api_client.get(
            url,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 403

    def test_requires_global_channel(self, api_client, url):
        """A non-global api_key Channel is rejected even with the right scope."""

        domain = MailDomainFactory()
        channel, plaintext = make_api_key_channel(
            scope_level=ChannelScopeLevel.MAILDOMAIN,
            scopes=(ChannelApiKeyScope.MAILDOMAINS_READ.value,),
            maildomain=domain,
            name="maildomain-scope",
        )
        response = api_client.get(
            url,
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
        )
        assert response.status_code == 403

    def test_no_maildomains(self, api_client, url, correctly_configured_header):
        """Returns an empty list when no maildomain exists."""

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200
        assert response.json() == {
            "count": 0,
            "next": None,
            "previous": None,
            "results": [],
        }

    def test_lists_all_maildomains_in_creation_order(
        self, api_client, url, correctly_configured_header
    ):
        """All maildomains are returned, oldest first."""

        MailDomainFactory(name="zeta.example.com")
        MailDomainFactory(name="alpha.example.com")

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        content = response.json()
        assert content["count"] == 2
        assert [result["name"] for result in content["results"]] == [
            "zeta.example.com",
            "alpha.example.com",
        ]

    def test_expected_dns_records(self, api_client, url, correctly_configured_header):
        """Each maildomain is returned with its expected DNS records."""

        domain = MailDomainFactory(name="example-dns.com")

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        content = response.json()
        assert content["count"] == 1
        result = content["results"][0]
        assert result["id"] == str(domain.id)
        assert result["name"] == "example-dns.com"
        assert result["expected_dns_records"] == domain.get_expected_dns_records()

        records = result["expected_dns_records"]
        assert len([r for r in records if r["type"] == "mx"]) == 2
        assert any("v=spf1" in r["value"] for r in records)
        assert any(r["target"] == "_dmarc" for r in records)

        # A DKIM key is generated on domain creation
        dkim_records = [r for r in records if "DKIM1" in r["value"]]
        assert len(dkim_records) == 1
        assert dkim_records[0]["target"].endswith("._domainkey")

    def test_pagination(self, api_client, url, correctly_configured_header):
        """Results are paginated with page/page_size, oldest first."""

        for index in range(5):
            MailDomainFactory(name=f"domain{index}.example.com")

        response = api_client.get(f"{url}?page_size=2", **correctly_configured_header)
        assert response.status_code == 200
        content = response.json()
        assert content["count"] == 5
        assert content["previous"] is None
        assert content["next"] is not None
        assert [result["name"] for result in content["results"]] == [
            "domain0.example.com",
            "domain1.example.com",
        ]

        response = api_client.get(
            f"{url}?page_size=2&page=3", **correctly_configured_header
        )
        assert response.status_code == 200
        content = response.json()
        assert content["next"] is None
        assert [result["name"] for result in content["results"]] == [
            "domain4.example.com"
        ]

        # Out-of-range pages are rejected by the paginator
        response = api_client.get(
            f"{url}?page_size=2&page=99", **correctly_configured_header
        )
        assert response.status_code == 404

    def test_page_size_is_capped_at_1000(
        self, api_client, url, correctly_configured_header
    ):
        """page_size is honored up to 1000, above which it is clamped."""

        MailDomainFactory.create_batch(3)

        response = api_client.get(
            f"{url}?page_size=1000", **correctly_configured_header
        )
        assert response.status_code == 200
        assert response.json()["next"] is None

        # Asking for more than the max falls back to the max, not to an error
        response = api_client.get(
            f"{url}?page_size=5000", **correctly_configured_header
        )
        assert response.status_code == 200
        assert len(response.json()["results"]) == 3

    def test_domain_created_mid_walk_does_not_shift_pages(
        self, api_client, url, correctly_configured_header
    ):
        """A domain created between two page requests lands after the pages already read."""

        for index in range(4):
            MailDomainFactory(name=f"domain{index}.example.com")

        response = api_client.get(f"{url}?page_size=2", **correctly_configured_header)
        first_page = [result["name"] for result in response.json()["results"]]
        assert first_page == ["domain0.example.com", "domain1.example.com"]

        MailDomainFactory(name="created-mid-walk.example.com")

        response = api_client.get(
            f"{url}?page_size=2&page=2", **correctly_configured_header
        )
        second_page = [result["name"] for result in response.json()["results"]]

        # No row from page 1 reappears, and none is skipped
        assert second_page == ["domain2.example.com", "domain3.example.com"]

        response = api_client.get(
            f"{url}?page_size=2&page=3", **correctly_configured_header
        )
        assert [result["name"] for result in response.json()["results"]] == [
            "created-mid-walk.example.com"
        ]

    def test_dkim_keys_are_prefetched(
        self, api_client, url, correctly_configured_header
    ):
        """The query count does not grow with the number of domains."""

        MailDomainFactory()
        with CaptureQueriesContext(connection) as baseline:
            assert api_client.get(url, **correctly_configured_header).status_code == 200

        MailDomainFactory.create_batch(3)
        with CaptureQueriesContext(connection) as queries:
            response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200
        assert response.json()["count"] == 4

        assert len(queries) == len(baseline)
