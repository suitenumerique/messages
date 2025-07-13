"""
Tests for DNS checking functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
from dns.resolver import NXDOMAIN, YXDOMAIN, NoAnswer, NoNameservers, Timeout

from core.dns.check import check_dns_records, check_single_record
from core.models import MailDomain


@pytest.mark.django_db
class TestDNSChecking:
    """Test DNS checking functionality."""

    def test_check_single_record_mx_correct(self, maildomain_factory):
        """Test checking a correct MX record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock correct MX record
            mock_answer = MagicMock()
            mock_answer.preference = 10
            mock_answer.exchange = "mx1.example.com"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["10 mx1.example.com"]

    def test_check_single_record_mx_incorrect(self, maildomain_factory):
        """Test checking an incorrect MX record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock incorrect MX record
            mock_answer = MagicMock()
            mock_answer.preference = 20
            mock_answer.exchange = "mx2.example.com"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
            assert result["found"] == ["20 mx2.example.com"]

    def test_check_single_record_txt_correct(self, maildomain_factory):
        """Test checking a correct TXT record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "@",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock correct TXT record
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=spf1 include:_spf.example.com -all"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["v=spf1 include:_spf.example.com -all"]

    def test_check_single_record_missing(self, maildomain_factory):
        """Test checking a missing record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock missing record
            mock_resolve.side_effect = Exception("No records found")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "No records found" in result["error"]

    def test_check_single_record_nxdomain(self, maildomain_factory):
        """Test checking a record when domain doesn't exist."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NXDOMAIN
            mock_resolve.side_effect = NXDOMAIN()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "Domain not found"

    def test_check_single_record_no_answer(self, maildomain_factory):
        """Test checking a record when no answer is returned."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NoAnswer
            mock_resolve.side_effect = NoAnswer()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "No records found"

    def test_check_single_record_no_nameservers(self, maildomain_factory):
        """Test checking a record when no nameservers are found."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NoNameservers
            mock_resolve.side_effect = NoNameservers()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "No nameservers found"

    def test_check_single_record_timeout(self, maildomain_factory):
        """Test checking a record when DNS query times out."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock Timeout
            mock_resolve.side_effect = Timeout()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "DNS query timeout"

    def test_check_single_record_yxdomain(self, maildomain_factory):
        """Test checking a record when domain name is too long."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock YXDOMAIN
            mock_resolve.side_effect = YXDOMAIN()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "Domain name too long"

    def test_check_single_record_generic_exception(self, maildomain_factory):
        """Test checking a record when a generic exception occurs."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock generic exception
            mock_resolve.side_effect = Exception("Network error")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "DNS query failed: Network error" in result["error"]

    def test_check_single_record_mx_correct_format(self, maildomain_factory):
        """Test that MX records are formatted correctly in results."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock correct MX record
            mock_answer = MagicMock()
            mock_answer.preference = 10
            mock_answer.exchange = "mx1.example.com"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["10 mx1.example.com"]

    def test_check_single_record_mx_incorrect_format(self, maildomain_factory):
        """Test that MX records with wrong format are detected as incorrect."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock MX record with different preference
            mock_answer = MagicMock()
            mock_answer.preference = 20
            mock_answer.exchange = "mx1.example.com"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
            assert result["found"] == ["20 mx1.example.com"]

    def test_check_dns_records_multiple_records(self, maildomain_factory):
        """Test checking multiple DNS records."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
                {
                    "type": "TXT",
                    "target": "@",
                    "value": "v=spf1 include:_spf.example.com -all",
                },
            ]

            with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
                # Mock responses for both records
                mock_mx_answer = MagicMock()
                mock_mx_answer.preference = 10
                mock_mx_answer.exchange = "mx1.example.com"

                mock_txt_answer = MagicMock()
                mock_txt_answer.to_text.return_value = (
                    '"v=spf1 include:_spf.example.com -all"'
                )

                mock_resolve.side_effect = [
                    [mock_mx_answer],  # MX record response
                    [mock_txt_answer],  # TXT record response
                ]

                results = check_dns_records(maildomain)

                assert len(results) == 2
                assert results[0]["type"] == "MX"
                assert results[0]["_check"]["status"] == "correct"
                assert results[1]["type"] == "TXT"
                assert results[1]["_check"]["status"] == "correct"

    def test_check_dns_records_mixed_status(self, maildomain_factory):
        """Test checking DNS records with mixed status (correct, incorrect, missing)."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
                {
                    "type": "TXT",
                    "target": "@",
                    "value": "v=spf1 include:_spf.example.com -all",
                },
                {"type": "A", "target": "@", "value": "192.168.1.1"},
            ]

            with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
                # Mock responses: correct MX, incorrect TXT, missing A
                mock_mx_answer = MagicMock()
                mock_mx_answer.preference = 10
                mock_mx_answer.exchange = "mx1.example.com"

                mock_resolve.side_effect = [
                    [mock_mx_answer],  # Correct MX
                    [],  # Incorrect TXT (empty response)
                    NoAnswer(),  # Missing A record
                ]

                results = check_dns_records(maildomain)

                assert len(results) == 3
                assert results[0]["_check"]["status"] == "correct"
                assert results[1]["_check"]["status"] == "incorrect"
                assert results[2]["_check"]["status"] == "missing"

    def test_check_single_record_with_subdomain(self, maildomain_factory):
        """Test checking a record for a subdomain."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "A", "target": "www", "value": "192.168.1.1"}

        with patch("core.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock correct A record for subdomain
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = "192.168.1.1"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["192.168.1.1"]
            # Verify the query was made for the subdomain
            mock_resolve.assert_called_once_with("www.example.com", "A")


@pytest.fixture(name="maildomain_factory")
@pytest.mark.django_db
def fixture_maildomain_factory():
    """Factory for creating test mail domains."""

    def _create_maildomain(name="test.com"):
        return MailDomain.objects.create(name=name)

    return _create_maildomain
