"""
Tests for Scaleway DNS provider functionality.
"""

from unittest.mock import patch

from django.test.utils import override_settings

import pytest

from core.dns.providers.scaleway import ScalewayDNSProvider


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
    def test_scaleway_provider_resolve_zone_components_root_domain(self):
        """Test that _resolve_zone_components handles root domains correctly."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            mock_get_zones.return_value = []

            parent_domain, subdomain = provider._resolve_zone_components("example.com")
            assert parent_domain == "example.com"
            assert subdomain == ""

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_subdomain_no_parent(self):
        """Test that _resolve_zone_components handles subdomains when no parent exists."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            mock_get_zones.return_value = []

            parent_domain, subdomain = provider._resolve_zone_components(
                "mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "mail"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_subdomain_with_parent(self):
        """Test that _resolve_zone_components finds existing parent zone."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            # Mock existing zones - example.com exists as a root domain
            mock_get_zones.return_value = [
                {"domain": "example.com", "subdomain": ""},
            ]

            parent_domain, subdomain = provider._resolve_zone_components(
                "mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "mail"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_nested_subdomain_with_parent(
        self,
    ):
        """Test that _resolve_zone_components finds existing parent zone for nested subdomain."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            # Mock existing zones - mail.example.com exists as a subdomain
            mock_get_zones.return_value = [
                {"domain": "example.com", "subdomain": "mail"},
            ]

            parent_domain, subdomain = provider._resolve_zone_components(
                "smtp.mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "smtp"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_deep_nested_subdomain(self):
        """Test that _resolve_zone_components handles deeply nested subdomains."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            # Mock existing zones - example.com exists as a root domain
            mock_get_zones.return_value = [
                {"domain": "example.com", "subdomain": ""},
            ]

            parent_domain, subdomain = provider._resolve_zone_components(
                "smtp.mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "smtp.mail"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_multiple_potential_parents(self):
        """Test that _resolve_zone_components finds the closest existing parent."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            # Mock existing zones - both example.com and mail.example.com exist
            mock_get_zones.return_value = [
                {"domain": "example.com", "subdomain": ""},
                {"domain": "example.com", "subdomain": "mail"},
            ]

            # Should find mail.example.com as the closest parent
            parent_domain, subdomain = provider._resolve_zone_components(
                "smtp.mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "smtp"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_resolve_zone_components_no_parent_found(self):
        """Test that _resolve_zone_components creates new zone when no parent exists."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zones") as mock_get_zones:
            # Mock existing zones - no relevant parent exists
            mock_get_zones.return_value = [
                {"domain": "other.com", "subdomain": ""},
            ]

            parent_domain, subdomain = provider._resolve_zone_components(
                "mail.example.com"
            )
            assert parent_domain == "example.com"
            assert subdomain == "mail"

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
            patch.object(provider, "get_zones") as mock_get_zones,
        ):
            mock_request.return_value = {"dns_zone": {"domain": "example.com"}}
            mock_get_zones.return_value = []

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
            patch.object(provider, "get_zones") as mock_get_zones,
        ):
            mock_request.return_value = {"dns_zone": {"domain": "example.com"}}
            mock_get_zones.return_value = []

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
    def test_scaleway_provider_create_zone_subdomain_with_existing_parent(self):
        """Test that create_zone creates sub-zone when parent exists."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "get_zones") as mock_get_zones,
        ):
            mock_request.return_value = {"dns_zone": {"domain": "example.com"}}
            # Mock existing parent zone
            mock_get_zones.return_value = [
                {"domain": "example.com", "subdomain": ""},
            ]

            provider.create_zone("mail.example.com")

            # Should still create sub-zone under existing parent
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
    def test_scaleway_provider_get_zone_name(self):
        """Test that _get_zone_name returns the correct zone name for API calls."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zone") as mock_get_zone:
            mock_get_zone.return_value = None

            # Should return the full domain name for API calls
            assert provider._get_zone_name("example.com") == "example.com"
            assert provider._get_zone_name("mail.example.com") == "mail.example.com"
            assert (
                provider._get_zone_name("smtp.mail.example.com")
                == "smtp.mail.example.com"
            )

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_format_record_name(self):
        """Test that _format_record_name formats record names correctly."""
        provider = ScalewayDNSProvider()

        # Test root domain record
        assert provider._format_record_name("example.com", "example.com") == ""

        # Test subdomain record
        assert provider._format_record_name("test.example.com", "example.com") == "test"

        # Test short name
        assert provider._format_record_name("test", "example.com") == "test"

        # Test empty name
        assert provider._format_record_name("", "example.com") == ""
        assert provider._format_record_name(None, "example.com") == ""

        # Test edge cases
        assert provider._format_record_name("www.example.com", "example.com") == "www"
        assert provider._format_record_name("mail.example.com", "example.com") == "mail"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_record_with_default_ttl(self):
        """Test that create_record uses default TTL when not specified."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "_validate_zone_exists") as mock_validate,
        ):
            mock_request.return_value = {"records": [{"id": "test-record"}]}
            mock_validate.return_value = True

            provider.create_record(
                "example.com", "test.example.com", "A", "192.168.1.1"
            )

            # Verify TTL was passed correctly and record name is formatted
            call_args = mock_request.call_args
            assert call_args[0][0] == "PATCH"  # HTTP method
            assert call_args[0][1] == "dns-zones/example.com/records"  # endpoint
            assert call_args[0][2]["return_all_records"] is False
            assert call_args[0][2]["changes"][0]["add"]["records"][0]["name"] == "test"
            assert call_args[0][2]["changes"][0]["add"]["records"][0]["ttl"] == 600

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_record_with_custom_ttl(self):
        """Test that create_record uses custom TTL when specified."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "_validate_zone_exists") as mock_validate,
        ):
            mock_request.return_value = {"records": [{"id": "test-record"}]}
            mock_validate.return_value = True

            provider.create_record(
                "example.com", "test.example.com", "A", "192.168.1.1", ttl=300
            )

            # Verify custom TTL was passed correctly
            call_args = mock_request.call_args
            assert call_args[0][0] == "PATCH"  # HTTP method
            assert call_args[0][1] == "dns-zones/example.com/records"  # endpoint
            assert call_args[0][2]["return_all_records"] is False
            assert call_args[0][2]["changes"][0]["add"]["records"][0]["name"] == "test"
            assert call_args[0][2]["changes"][0]["add"]["records"][0]["ttl"] == 300

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_record_root_domain(self):
        """Test that create_record handles root domain records correctly."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "_validate_zone_exists") as mock_validate,
        ):
            mock_request.return_value = {"records": [{"id": "test-record"}]}
            mock_validate.return_value = True

            provider.create_record("example.com", "example.com", "A", "192.168.1.1")

            # Verify root domain record has empty name
            call_args = mock_request.call_args
            assert call_args[0][2]["changes"][0]["add"]["records"][0]["name"] == ""

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_update_record(self):
        """Test that update_record uses the correct API structure."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "_validate_zone_exists") as mock_validate,
        ):
            mock_request.return_value = {"records": [{"id": "updated-record"}]}
            mock_validate.return_value = True

            provider.update_record(
                "example.com",
                "record-id",
                "test.example.com",
                "A",
                "192.168.1.2",
                ttl=300,
            )

            # Verify the correct API structure with formatted record name
            call_args = mock_request.call_args
            assert call_args[0][0] == "PATCH"  # HTTP method
            assert call_args[0][1] == "dns-zones/example.com/records"  # endpoint
            assert call_args[0][2]["return_all_records"] is False
            assert call_args[0][2]["changes"][0]["set"]["id_fields"]["name"] == "test"
            assert call_args[0][2]["changes"][0]["set"]["id_fields"]["type"] == "A"
            assert call_args[0][2]["changes"][0]["set"]["records"][0]["name"] == "test"
            assert call_args[0][2]["changes"][0]["set"]["records"][0]["ttl"] == 300

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_delete_record_by_name_type(self):
        """Test that delete_record_by_name_type uses the correct API structure."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_make_request") as mock_request,
            patch.object(provider, "_validate_zone_exists") as mock_validate,
        ):
            mock_request.return_value = {}
            mock_validate.return_value = True

            provider.delete_record_by_name_type("example.com", "test.example.com", "A")

            # Verify the correct API structure with formatted record name
            call_args = mock_request.call_args
            assert call_args[0][0] == "PATCH"  # HTTP method
            assert call_args[0][1] == "dns-zones/example.com/records"  # endpoint
            assert call_args[0][2]["return_all_records"] is False
            assert (
                call_args[0][2]["changes"][0]["delete"]["id_fields"]["name"] == "test"
            )
            assert call_args[0][2]["changes"][0]["delete"]["id_fields"]["type"] == "A"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_find_records(self):
        """Test that find_records uses formatted record names."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_records") as mock_get_records:
            mock_get_records.return_value = [
                {"name": "test", "type": "A", "data": "192.168.1.1"},
                {"name": "other", "type": "A", "data": "192.168.1.2"},
            ]

            records = provider.find_records("example.com", "test.example.com", "A")

            # Should find the record with formatted name
            assert len(records) == 1
            assert records[0]["name"] == "test"
            assert records[0]["type"] == "A"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_record_exists(self):
        """Test that record_exists uses formatted record names."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "find_records") as mock_find_records:
            mock_find_records.return_value = [
                {"name": "test", "type": "A", "data": "192.168.1.1"},
            ]

            exists = provider.record_exists(
                "example.com", "test.example.com", "A", "192.168.1.1"
            )

            assert exists is True
            # Verify that find_records was called with formatted name
            mock_find_records.assert_called_once_with(
                "example.com", "test.example.com", "A"
            )

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
    def test_scaleway_provider_get_zone_name_with_existing_zone(self):
        """Test that _get_zone_name returns correct zone name when zone exists."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zone") as mock_get_zone:
            # Mock that the zone exists
            mock_get_zone.return_value = {"domain": "example.com", "subdomain": ""}

            zone_name = provider._get_zone_name("example.com")
            assert zone_name == "example.com"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_get_zone_name_with_parent_zone(self):
        """Test that _get_zone_name finds parent zone when exact zone doesn't exist."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zone") as mock_get_zone:
            # Mock that exact zone doesn't exist but parent does
            mock_get_zone.side_effect = (
                lambda domain: {"domain": "example.com", "subdomain": ""}
                if domain == "example.com"
                else None
            )

            zone_name = provider._get_zone_name("mail.example.com")
            assert zone_name == "example.com"

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_validate_zone_exists(self):
        """Test that _validate_zone_exists works correctly."""
        provider = ScalewayDNSProvider()

        with patch.object(provider, "get_zone") as mock_get_zone:
            # Test existing zone
            mock_get_zone.return_value = {"domain": "example.com", "subdomain": ""}
            assert provider._validate_zone_exists("example.com") is True

            # Test non-existing zone
            mock_get_zone.return_value = None
            assert provider._validate_zone_exists("nonexistent.com") is False

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_create_record_zone_not_found(self):
        """Test that create_record raises error when zone doesn't exist."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_validate_zone_exists") as mock_validate,
            patch.object(provider, "_get_zone_name") as mock_get_zone_name,
        ):
            mock_validate.return_value = False
            mock_get_zone_name.return_value = "nonexistent.com"

            with pytest.raises(Exception, match="Zone not found"):
                provider.create_record("nonexistent.com", "test", "A", "192.168.1.1")

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_update_record_zone_not_found(self):
        """Test that update_record raises error when zone doesn't exist."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_validate_zone_exists") as mock_validate,
            patch.object(provider, "_get_zone_name") as mock_get_zone_name,
        ):
            mock_validate.return_value = False
            mock_get_zone_name.return_value = "nonexistent.com"

            with pytest.raises(Exception, match="Zone not found"):
                provider.update_record(
                    "nonexistent.com", "id", "test", "A", "192.168.1.1"
                )

    @override_settings(
        DNS_SCALEWAY_API_TOKEN="test-token",
        DNS_SCALEWAY_PROJECT_ID="test-project",
        DNS_SCALEWAY_TTL=600,
    )
    def test_scaleway_provider_delete_record_zone_not_found(self):
        """Test that delete_record_by_name_type raises error when zone doesn't exist."""
        provider = ScalewayDNSProvider()

        with (
            patch.object(provider, "_validate_zone_exists") as mock_validate,
            patch.object(provider, "_get_zone_name") as mock_get_zone_name,
        ):
            mock_validate.return_value = False
            mock_get_zone_name.return_value = "nonexistent.com"

            with pytest.raises(Exception, match="Zone not found"):
                provider.delete_record_by_name_type("nonexistent.com", "test", "A")

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

        with pytest.raises(Exception, match="Zone not found"):
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
                    "code": "conflict",
                },
                "raise_for_status": lambda self: None,
            },
        )()

        with pytest.raises(Exception, match="Zone already exists"):
            provider._handle_api_error(mock_response)
