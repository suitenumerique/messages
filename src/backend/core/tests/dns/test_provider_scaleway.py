"""
Tests for Scaleway DNS provider functionality.
"""

from unittest.mock import MagicMock, patch

from django.test.utils import override_settings

import pytest

from core.services.dns.providers.scaleway import ScalewayDNSProvider


@pytest.mark.django_db
# pylint: disable=protected-access,too-many-public-methods
class TestScalewayDNSProvider:
    """Test Scaleway DNS provider functionality."""

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_is_configured(self):
        """Test that is_configured returns True when properly configured."""
        provider = ScalewayDNSProvider()
        assert provider.is_configured() is True

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_is_not_configured_missing_token(self):
        """Test that is_configured returns False when API token is missing."""
        provider = ScalewayDNSProvider()
        assert provider.is_configured() is False

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_is_not_configured_missing_project(self):
        """Test that is_configured returns False when project ID is missing."""
        provider = ScalewayDNSProvider()
        assert provider.is_configured() is False

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_ttl_setting(self):
        """Test that Scaleway provider uses the TTL setting correctly."""
        provider = ScalewayDNSProvider()
        assert provider.ttl == 600

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_zone_root_domain(self):
        """Test that create_zone handles root domains correctly."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "zone_exists") as mock_zone_exists,
        ):
            mock_request.return_value = {"domain": "example.com"}
            mock_zone_exists.return_value = False

            provider.create_zone("example.com")

            # Verify correct parameters for root domain
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"  # HTTP method
            assert call_args[0][1] == "dns-zones"  # endpoint
            assert call_args[0][2]["domain"] == "example.com"
            assert call_args[0][2]["subdomain"] == ""
            assert call_args[0][2]["project_id"] == "test-project"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_zone_subdomain(self):
        """Test that create_zone handles subdomains correctly."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "zone_exists") as mock_zone_exists,
        ):
            mock_request.return_value = {"domain": "example.com"}
            # Parent zone exists, subdomain doesn't
            mock_zone_exists.side_effect = lambda domain: domain == "example.com"

            provider.create_zone("mail.example.com")

            # Verify correct parameters for subdomain
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"  # HTTP method
            assert call_args[0][1] == "dns-zones"  # endpoint
            assert call_args[0][2]["domain"] == "example.com"
            assert call_args[0][2]["subdomain"] == "mail"
            assert call_args[0][2]["project_id"] == "test-project"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_zone_existing_zone(self):
        """Test that create_zone does nothing when zone already exists."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "zone_exists") as mock_zone_exists,
        ):
            mock_zone_exists.return_value = True

            provider.create_zone("example.com")

            # Verify no request was made
            mock_request.assert_not_called()

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_get_records(self):
        """Test that get_records uses correct zone name."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "_make_request") as mock_request:
            mock_request.return_value = {"records": [{"name": "test", "type": "A"}]}

            provider.get_records("example.com")

            # Verify correct zone name is used
            call_args = mock_request.call_args
            assert call_args[0][0] == "GET"  # HTTP method
            assert call_args[0][1] == "dns-zones/example.com/records"  # endpoint

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_get_records_subdomain(self):
        """Test that get_records works with subdomains."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "_make_request") as mock_request:
            mock_request.return_value = {"records": [{"name": "test", "type": "A"}]}

            provider.get_records("mail.example.com")

            # Verify correct zone name is used for subdomain
            call_args = mock_request.call_args
            assert call_args[0][0] == "GET"  # HTTP method
            assert call_args[0][1] == "dns-zones/mail.example.com/records"  # endpoint

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_zone_exists(self):
        """Test that zone_exists works correctly."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_records") as mock_get_records:
            # Test existing zone
            mock_get_records.return_value = [{"name": "test", "type": "A"}]
            assert provider.zone_exists("example.com") is True

            # Test non-existing zone
            mock_get_records.return_value = []
            assert provider.zone_exists("nonexistent.com") is False

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_get_zones(self):
        """Test that get_zones works correctly."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "_make_request") as mock_request:
            mock_request.return_value = {"dns_zones": [{"domain": "example.com"}]}

            zones = provider.get_zones()

            # Verify correct request
            call_args = mock_request.call_args
            assert call_args[0][0] == "GET"  # HTTP method
            assert call_args[0][1] == "dns-zones"  # endpoint
            assert zones == [{"domain": "example.com"}]

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_handle_api_error_404(self):
        """Test that _handle_api_error handles 404 errors correctly."""
        provider = ScalewayDNSProvider()

        # Create a mock response
        mock_response = type(
            "MockResponse",
            (),
            {
                "status_code": 404,
                "json": lambda self: {"message": "Zone not found", "code": "not_found"},
                "raise_for_status": lambda self: None,
            },
        )()

        with pytest.raises(ValueError, match="Zone not found"):
            provider._handle_api_error(mock_response)

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_handle_api_error_409(self):
        """Test that _handle_api_error handles 409 errors correctly."""
        provider = ScalewayDNSProvider()

        # Create a mock response
        mock_response = type(
            "MockResponse",
            (),
            {
                "status_code": 409,
                "json": lambda self: {
                    "message": "Zone already exists",
                    "code": "already_exists",
                },
                "raise_for_status": lambda self: None,
            },
        )()

        with pytest.raises(ValueError, match="Zone already exists"):
            provider._handle_api_error(mock_response)

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_make_request_with_pagination(self):
        """Test that _make_request handles pagination correctly."""
        provider = ScalewayDNSProvider()

        with patch(
            "core.services.dns.providers.scaleway.requests.request"
        ) as mock_request:
            # Mock first page response
            mock_response1 = MagicMock()
            mock_response1.ok = True
            mock_response1.json.return_value = {
                "dns_zones": [{"domain": "example.com"}],
                "total_count": 1,
            }

            mock_request.return_value = mock_response1

            response = provider._make_request("GET", "dns-zones", paginate=True)

            # Verify that only one request was made since total_count equals results
            assert mock_request.call_count == 1

            # Verify response contains results
            assert len(response["dns_zones"]) == 1
            assert response["dns_zones"][0]["domain"] == "example.com"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_make_request_without_pagination(self):
        """Test that _make_request works normally without pagination."""
        provider = ScalewayDNSProvider()

        with patch(
            "core.services.dns.providers.scaleway.requests.request"
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.json.return_value = {"dns_zones": [{"domain": "example.com"}]}
            mock_request.return_value = mock_response

            response = provider._make_request("GET", "dns-zones", paginate=False)

            # Verify that only one request was made without pagination parameters
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert (
                call_args[1]["url"]
                == "https://api.scaleway.com/domain/v2beta1/dns-zones"
            )

            # Verify response
            assert response["dns_zones"] == [{"domain": "example.com"}]

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_make_request_pagination_single_page(self):
        """Test that _make_request handles single page pagination correctly."""
        provider = ScalewayDNSProvider()

        with patch(
            "core.services.dns.providers.scaleway.requests.request"
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.json.return_value = {
                "dns_zones": [{"domain": "example.com"}],
                "total_count": 1,
            }
            mock_request.return_value = mock_response

            response = provider._make_request("GET", "dns-zones", paginate=True)

            # Verify that only one request was made
            mock_request.assert_called_once()

            # Verify response
            assert response["dns_zones"] == [{"domain": "example.com"}]
            assert response["total_count"] == 1

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_make_request_pagination_multiple_pages(self):
        """Test that _make_request handles multiple page pagination correctly."""
        provider = ScalewayDNSProvider()

        with patch(
            "core.services.dns.providers.scaleway.requests.request"
        ) as mock_request:
            # Mock first page response
            mock_response1 = MagicMock()
            mock_response1.ok = True
            mock_response1.json.return_value = {
                "dns_zones": [{"domain": "example.com"}] * 100,
                "total_count": 101,
            }

            # Mock second page response
            mock_response2 = MagicMock()
            mock_response2.ok = True
            mock_response2.json.return_value = {
                "dns_zones": [{"domain": "test.com"}],
                "total_count": 101,
            }

            mock_request.side_effect = [mock_response1, mock_response2]

            response = provider._make_request("GET", "dns-zones", paginate=True)

            # Verify that two requests were made
            assert mock_request.call_count == 2

            # Verify response contains combined results
            assert len(response["dns_zones"]) == 101
            assert response["dns_zones"][0]["domain"] == "example.com"
            assert response["dns_zones"][1]["domain"] == "example.com"
            assert response["dns_zones"][100]["domain"] == "test.com"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_provision_domain_records(self):
        """Test that provision_domain_records works correctly."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "create_zone") as mock_create_zone,
            patch.object(provider, "get_records") as mock_get_records,
            patch.object(provider, "_sync_records") as mock_sync_records,
        ):
            mock_get_records.return_value = []
            mock_sync_records.return_value = [{"type": "add", "record": "test"}]

            expected_records = [
                {"type": "MX", "target": "", "value": "10 mx1.example.com"},
                {"type": "TXT", "target": "_dmarc", "value": "v=DMARC1; p=reject;"},
            ]

            results = provider.provision_domain_records("example.com", expected_records)

            # Verify zone creation was called
            mock_create_zone.assert_called_once_with("example.com", False)

            # Verify records were fetched
            mock_get_records.assert_called_once_with("example.com", paginate=True)

            # Verify sync was called
            assert mock_sync_records.call_count > 0

            # Verify results
            assert len(results) > 0

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_provision_domain_records_pretend(self):
        """Test that provision_domain_records works in pretend mode."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "create_zone") as mock_create_zone,
            patch.object(provider, "get_records") as mock_get_records,
            patch.object(provider, "_sync_records") as mock_sync_records,
        ):
            mock_get_records.return_value = []
            mock_sync_records.return_value = [{"type": "add", "record": "test"}]

            expected_records = [
                {"type": "MX", "target": "", "value": "10 mx1.example.com"},
            ]

            results = provider.provision_domain_records(
                "example.com", expected_records, pretend=True
            )

            # Verify zone creation was called with pretend=True
            mock_create_zone.assert_called_once_with("example.com", True)

            # Verify results
            assert len(results) > 0
