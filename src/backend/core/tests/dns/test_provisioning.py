"""
Tests for DNS provisioning functionality.
"""

from unittest.mock import MagicMock, patch

from django.test.utils import override_settings

import pytest

from core.models import MailDomain
from core.services.dns.provisioning import (
    detect_dns_provider,
    get_dns_provider,
    provision_domain_dns,
)
from core.services.dns.resolver import (
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
)
from core.tests.dns_answers import ns_answer


@pytest.mark.django_db
class TestDNSProvisioning:
    """Test DNS provisioning functionality."""

    def test_detect_dns_provider_scaleway(self):
        """Test detection of Scaleway DNS provider."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.return_value = ns_answer(
                "ns0.dom.scw.cloud.", "ns1.dom.scw.cloud."
            )

            provider = detect_dns_provider("example.com")
            assert provider == "scaleway"

    def test_detect_dns_provider_unknown(self):
        """Test detection of unknown DNS provider."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.return_value = ns_answer(
                "ns1.unknown.com.", "ns2.unknown.com."
            )

            provider = detect_dns_provider("example.com")
            assert provider is None

    def test_detect_dns_provider_exception(self):
        """Test DNS provider detection with exception."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = Exception("DNS error")

            provider = detect_dns_provider("example.com")
            assert provider is None

    def test_detect_dns_provider_nxdomain(self):
        """Test DNS provider detection when domain doesn't exist."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = NXDOMAINError("example.com", "NS")

            provider = detect_dns_provider("example.com")
            assert provider is None

    def test_detect_dns_provider_no_nameservers(self):
        """Test DNS provider detection when no nameservers are found."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = ServfailError("example.com", "NS")

            provider = detect_dns_provider("example.com")
            assert provider is None

    def test_detect_dns_provider_no_answer(self):
        """Test DNS provider detection when the domain has no NS records."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = NoAnswerError("example.com", "NS")

            provider = detect_dns_provider("example.com")
            assert provider is None

    def test_detect_dns_provider_timeout(self):
        """Test DNS provider detection when query times out."""
        with patch("core.services.dns.provisioning.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = ResolutionTimeoutError("example.com", "NS")

            provider = detect_dns_provider("example.com")
            assert provider is None

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_get_dns_provider_scaleway(self):
        """Test getting Scaleway DNS provider."""
        provider = get_dns_provider("scaleway")
        assert provider

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_get_dns_provider_scaleway_not_configured(self):
        """Test getting Scaleway DNS provider when not configured."""
        provider = get_dns_provider("scaleway")
        assert provider is None

    def test_get_dns_provider_unsupported(self):
        """Test getting unsupported DNS provider."""
        provider = get_dns_provider("unsupported-provider")
        assert provider is None

    def test_provision_domain_dns_auto_detect(self, maildomain_factory):
        """Test DNS provisioning with auto-detection."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.provisioning.detect_dns_provider") as mock_detect:
            with patch(
                "core.services.dns.provisioning.get_dns_provider"
            ) as mock_get_provider:
                mock_detect.return_value = "scaleway"

                mock_provider = MagicMock()
                mock_provider.provision_domain_records.return_value = []
                mock_get_provider.return_value = mock_provider

                results = provision_domain_dns(maildomain)

                assert results["success"] is True
                assert results["provider"] == "scaleway"
                # Verify the provider was called with domain and expected_records
                mock_provider.provision_domain_records.assert_called_once()
                call_args = mock_provider.provision_domain_records.call_args
                assert call_args[0][0] == "example.com"  # domain
                assert isinstance(call_args[0][1], list)  # expected_records

    def test_provision_domain_dns_specific_provider(self, maildomain_factory):
        """Test DNS provisioning with specific provider."""
        maildomain = maildomain_factory(name="example.com")

        with patch(
            "core.services.dns.provisioning.get_dns_provider"
        ) as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.provision_domain_records.return_value = [
                {"type": "MX", "name": "@", "value": "10 mx1.example.com"}
            ]
            mock_get_provider.return_value = mock_provider

            results = provision_domain_dns(maildomain, provider_name="scaleway")

            assert results["success"] is True
            assert results["provider"] == "scaleway"
            assert len(results["changes"]) == 1

    def test_provision_domain_dns_no_provider_detected(self, maildomain_factory):
        """Test DNS provisioning when no provider is detected."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.provisioning.detect_dns_provider") as mock_detect:
            mock_detect.return_value = None

            results = provision_domain_dns(maildomain)

            assert results["success"] is False
            assert "Could not detect DNS provider" in results["error"]

    def test_provision_domain_dns_unsupported_provider(self, maildomain_factory):
        """Test DNS provisioning with unsupported provider."""
        maildomain = maildomain_factory(name="example.com")

        with patch(
            "core.services.dns.provisioning.get_dns_provider"
        ) as mock_get_provider:
            mock_get_provider.return_value = None

            results = provision_domain_dns(maildomain, provider_name="unsupported")

            assert results["success"] is False
            assert "not supported" in results["error"]

    def test_provision_domain_dns_provider_error(self, maildomain_factory):
        """Test DNS provisioning when provider raises an error."""
        maildomain = maildomain_factory(name="example.com")

        with patch(
            "core.services.dns.provisioning.get_dns_provider"
        ) as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.provision_domain_records.side_effect = Exception(
                "Provider error"
            )
            mock_get_provider.return_value = mock_provider

            results = provision_domain_dns(maildomain, provider_name="scaleway")

            assert results["success"] is False
            assert "Failed to provision DNS records" in results["error"]

    @override_settings(DNS_DEFAULT_PROVIDER="scaleway")
    def test_provision_domain_dns_with_default_provider(self, maildomain_factory):
        """Test DNS provisioning using default provider from environment."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.provisioning.detect_dns_provider") as mock_detect:
            with patch(
                "core.services.dns.provisioning.get_dns_provider"
            ) as mock_get_provider:
                # No provider detected
                mock_detect.return_value = None

                mock_provider = MagicMock()
                mock_provider.provision_domain_records.return_value = {
                    "success": True,
                    "changes": [],
                }
                mock_get_provider.return_value = mock_provider

                results = provision_domain_dns(maildomain)

                assert results["success"] is True
                assert results["provider"] == "scaleway"
                # Verify the provider was called with default provider
                mock_get_provider.assert_called_once_with("scaleway")

    @override_settings(DNS_DEFAULT_PROVIDER=None)
    def test_provision_domain_dns_no_provider_and_no_default(self, maildomain_factory):
        """Test DNS provisioning when no provider is detected and no default is configured."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.provisioning.detect_dns_provider") as mock_detect:
            # No provider detected
            mock_detect.return_value = None

            results = provision_domain_dns(maildomain)

            assert results["success"] is False
            assert (
                "Could not detect DNS provider for domain example.com and no default provider configured"
                in results["error"]
            )

    def test_provision_domain_dns_pretend_mode(self, maildomain_factory):
        """Test DNS provisioning in pretend mode."""
        maildomain = maildomain_factory(name="example.com")

        with patch(
            "core.services.dns.provisioning.get_dns_provider"
        ) as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.provision_domain_records.return_value = [
                {"type": "MX", "name": "@", "value": "10 mx1.example.com"}
            ]

            mock_get_provider.return_value = mock_provider

            results = provision_domain_dns(
                maildomain, provider_name="scaleway", pretend=True
            )

            assert results["success"] is True
            assert results["pretend"] is True
            assert results["provider"] == "scaleway"
            # Verify the provider was called with pretend=True
            mock_provider.provision_domain_records.assert_called_once()
            call_args = mock_provider.provision_domain_records.call_args
            assert call_args[1]["pretend"] is True


@pytest.fixture(name="maildomain_factory")
def fixture_maildomain_factory():
    """Create a maildomain factory for testing."""

    def _create_maildomain(name="test.com"):
        return MailDomain.objects.create(name=name)

    return _create_maildomain
