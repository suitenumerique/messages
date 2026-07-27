"""Unit tests for the per-mailbox storage entitlements of each backend."""
# pylint: disable=redefined-outer-name

import json

from django.core.cache import cache
from django.test import override_settings

import pytest
import responses

from core import factories
from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend
from core.entitlements.backends.local import LocalEntitlementsBackend

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestLocalMailboxEntitlements:
    """Tests for LocalEntitlementsBackend.get_mailbox_entitlements."""

    def test_account_limit_and_usage(self):
        """Returns the configured limit and DB-computed usage; no org level."""
        mailbox = factories.MailboxFactory()
        backend = LocalEntitlementsBackend(mailbox_storage_limit=1000)

        result = backend.get_mailbox_entitlements(mailbox)
        assert result == {
            "account": {"storage_used": 0, "max_storage": 1000},
            "organization": None,
        }

    def test_unlimited_when_limit_none(self):
        """A None mailbox limit surfaces as max_storage=None (gauge hidden)."""
        mailbox = factories.MailboxFactory()
        backend = LocalEntitlementsBackend(mailbox_storage_limit=None)

        result = backend.get_mailbox_entitlements(mailbox)
        assert result["account"]["max_storage"] is None

    def test_organization_level_when_domain_has_claim(self):
        """When the domain carries the org claim, an organization level appears."""
        domain = factories.MailDomainFactory(
            custom_attributes={"siret": "12345678900001"}
        )
        mailbox = factories.MailboxFactory(domain=domain)
        backend = LocalEntitlementsBackend(
            mailbox_storage_limit=1000, organization_storage_limit=5000
        )

        result = backend.get_mailbox_entitlements(mailbox)
        assert result["organization"] == {"storage_used": 0, "max_storage": 5000}

    def test_storage_used_counts_message_overhead(self):
        """storage_used aggregates message overhead across the mailbox threads."""
        overhead = 1024
        mailbox = factories.MailboxFactory()
        contact = factories.ContactFactory(mailbox=mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        factories.MessageFactory(thread=thread, sender=contact)

        backend = LocalEntitlementsBackend(mailbox_storage_limit=10_000)
        with override_settings(METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE=overhead):
            result = backend.get_mailbox_entitlements(mailbox)
        assert result["account"]["storage_used"] == overhead


BASE_URL = "https://deploycenter.example.com/api/v1.0/entitlements"


class TestDeployCenterMailboxEntitlements:
    """Tests for DeployCenterEntitlementsBackend.get_mailbox_entitlements."""

    def _get_backend(self, **kwargs):
        defaults = {
            "base_url": BASE_URL,
            "service_id": "test-service",
            "api_key": "test-api-key",
            "timeout": 5,
        }
        defaults.update(kwargs)
        return DeployCenterEntitlementsBackend(**defaults)

    @responses.activate
    def test_posts_usage_metrics_and_parses_limits(self):
        """POSTs usage_metrics for mailbox + org and reads back the limits."""
        responses.add(
            responses.POST,
            BASE_URL,
            json={
                "entitlements": {
                    "max_storage_account": 1000,
                    "max_storage_organization": 5000,
                },
                "metrics": {
                    "account": {"storage_used": 42},
                    "organization": {"storage_used": 99},
                },
            },
            status=200,
        )

        domain = factories.MailDomainFactory(
            name="example.com", custom_attributes={"siret": "12345678900001"}
        )
        mailbox = factories.MailboxFactory(domain=domain, local_part="alice")

        result = self._get_backend().get_mailbox_entitlements(mailbox)

        assert result == {
            "account": {"storage_used": 42, "max_storage": 1000},
            "organization": {"storage_used": 99, "max_storage": 5000},
        }

        # A single POST carrying the usage metrics and the mailbox scope.
        assert len(responses.calls) == 1
        request = responses.calls[0].request
        assert responses.calls[0].request.method == "POST"
        assert "account_type=mailbox" in request.url
        assert "account_email=alice%40example.com" in request.url
        assert "siret=12345678900001" in request.url
        assert request.headers["X-Service-Auth"] == "Bearer test-api-key"

        body = json.loads(request.body)
        types = {item["account"]["type"] for item in body["usage_metrics"]}
        assert types == {"mailbox", "organization"}

    @responses.activate
    def test_no_organization_when_domain_has_no_claim(self):
        """Without an org claim on the domain, only the mailbox scope is pushed."""
        responses.add(
            responses.POST,
            BASE_URL,
            json={
                "entitlements": {"max_storage_account": 1000},
                "metrics": {"account": {"storage_used": 10}},
            },
            status=200,
        )

        mailbox = factories.MailboxFactory()
        result = self._get_backend().get_mailbox_entitlements(mailbox)

        assert result["organization"] is None
        body = json.loads(responses.calls[0].request.body)
        assert [item["account"]["type"] for item in body["usage_metrics"]] == [
            "mailbox"
        ]

    @responses.activate
    @override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
    def test_cache_hit_avoids_second_post(self):
        """A cached mailbox result is reused without a second POST."""
        responses.add(
            responses.POST,
            BASE_URL,
            json={
                "entitlements": {"max_storage_account": 1000},
                "metrics": {"account": {"storage_used": 10}},
            },
            status=200,
        )
        mailbox = factories.MailboxFactory()
        backend = self._get_backend()

        result1 = backend.get_mailbox_entitlements(mailbox)
        result2 = backend.get_mailbox_entitlements(mailbox)

        assert result1 == result2
        assert len(responses.calls) == 1

    @responses.activate
    def test_failure_without_cache_raises(self):
        """A server error with no cache raises EntitlementsUnavailableError."""
        responses.add(responses.POST, BASE_URL, status=500)
        mailbox = factories.MailboxFactory()

        with pytest.raises(EntitlementsUnavailableError):
            self._get_backend().get_mailbox_entitlements(mailbox)
