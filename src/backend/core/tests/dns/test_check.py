"""
Tests for DNS checking functionality.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import override_settings

import pytest
from dns.resolver import NXDOMAIN, YXDOMAIN, NoAnswer, NoNameservers, Timeout

from core.models import MailDomain
from core.services.dns.check import check_dns_records, check_single_record


@pytest.mark.django_db
class TestDNSChecking:  # pylint: disable=too-many-public-methods
    """Test DNS checking functionality."""

    def test_check_single_record_mx_correct(self, maildomain_factory):
        """Test checking a correct MX record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock missing record
            mock_resolve.side_effect = Exception("No records found")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "No records found" in result["error"]

    def test_check_single_record_nxdomain(self, maildomain_factory):
        """Test checking a record when domain doesn't exist."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NXDOMAIN
            mock_resolve.side_effect = NXDOMAIN()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "Domain not found"

    def test_check_single_record_no_answer(self, maildomain_factory):
        """Test checking a record when no answer is returned."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NoAnswer
            mock_resolve.side_effect = NoAnswer()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "No records found"

    def test_check_single_record_no_nameservers(self, maildomain_factory):
        """Test checking a record when no nameservers are found."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock NoNameservers
            mock_resolve.side_effect = NoNameservers()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "No nameservers found"

    def test_check_single_record_timeout(self, maildomain_factory):
        """Test checking a record when DNS query times out."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock Timeout
            mock_resolve.side_effect = Timeout()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "DNS query timeout"

    def test_check_single_record_yxdomain(self, maildomain_factory):
        """Test checking a record when domain name is too long."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock YXDOMAIN
            mock_resolve.side_effect = YXDOMAIN()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "Domain name too long"

    def test_check_single_record_generic_exception(self, maildomain_factory):
        """Test checking a record when a generic exception occurs."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock generic exception
            mock_resolve.side_effect = Exception("Network error")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "DNS query failed: Network error" in result["error"]

    def test_check_single_record_mx_correct_format(self, maildomain_factory):
        """Test that MX records are formatted correctly in results."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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
                {
                    "type": "TXT",
                    "target": "_dmarc",
                    "value": "v=DMARC1; p=reject; adkim=s; aspf=s;",
                },
                {
                    "type": "TXT",
                    "target": "_dmarc_stripped",
                    "value": "v=DMARC1;p=reject;adkim=s;aspf=s; ",
                },
                {
                    "type": "TXT",
                    "target": "_dmarc_missing",
                    "value": "v=DMARC1;p=reject;adkim=s;aspf=s; ",
                },
            ]

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

                def resolve_side_effect(name, record_type):
                    if name == "_dmarc_missing.example.com":
                        raise NoAnswer()

                    if record_type == "MX":
                        mock_mx_answer = MagicMock()
                        mock_mx_answer.preference = 10
                        mock_mx_answer.exchange = "mx1.example.com"
                        return [mock_mx_answer]

                    if record_type == "TXT" and name == "@.example.com":
                        mock_txt_answer = MagicMock()
                        mock_txt_answer.to_text.return_value = (
                            '"v=spf1 include:_spf.example.com -all"'
                        )
                        garbage = MagicMock()
                        garbage.to_text.return_value = "some-garbage"
                        return [garbage, mock_txt_answer, garbage]

                    if (
                        record_type == "TXT"
                        and name == "_dmarc.example.com"
                        or name == "_dmarc_stripped.example.com"
                    ):
                        mock_txt_dmarc_answer = MagicMock()
                        mock_txt_dmarc_answer.to_text.return_value = (
                            '"v=DMARC1; p=reject; adkim=s; aspf=s;"'
                        )
                        return [mock_txt_dmarc_answer]

                    return []

                mock_resolve.side_effect = resolve_side_effect

                results = check_dns_records(maildomain)

                assert len(results) == 5
                assert results[0]["type"] == "MX"
                assert results[0]["_check"]["status"] == "correct", results[0]
                assert results[1]["type"] == "TXT"
                assert results[1]["_check"]["status"] == "correct", results[1]
                assert results[2]["type"] == "TXT"
                assert results[2]["_check"]["status"] == "correct", results[2]
                assert results[3]["type"] == "TXT"
                assert results[3]["_check"]["status"] == "correct", results[3]
                assert results[4]["type"] == "TXT"
                assert results[4]["_check"]["status"] == "missing"

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

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
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

    def test_check_single_record_spf_duplicate(self, maildomain_factory):
        """Test that duplicate SPF records are detected.

        Per RFC 7208, a domain must not have multiple SPF records.
        Example in the wild: saint-sozy.fr has both
          "v=spf1 include:_spf.mail.suite.anct.gouv.fr -all"
          "v=spf1 include:_spf.legacy-provider.com ~all"
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock two SPF TXT records (invalid per RFC 7208)
            mock_answer1 = MagicMock()
            mock_answer1.to_text.return_value = '"v=spf1 include:_spf.example.com -all"'
            mock_answer2 = MagicMock()
            mock_answer2.to_text.return_value = (
                '"v=spf1 include:_spf.legacy-provider.com ~all"'
            )
            mock_resolve.return_value = [mock_answer1, mock_answer2]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "duplicate"
            assert len(result["found"]) == 2
            assert "v=spf1 include:_spf.example.com -all" in result["found"]
            assert "v=spf1 include:_spf.legacy-provider.com ~all" in result["found"]

    def test_check_single_record_spf_duplicate_even_if_correct_present(
        self, maildomain_factory
    ):
        """Test that duplicate SPF is reported even when the correct value is present."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_correct = MagicMock()
            mock_correct.to_text.return_value = '"v=spf1 include:_spf.example.com -all"'
            mock_legacy = MagicMock()
            mock_legacy.to_text.return_value = (
                '"v=spf1 include:_spf.legacy-provider.com ~all"'
            )
            mock_resolve.return_value = [mock_correct, mock_legacy]

            result = check_single_record(maildomain, expected_record)

            # Should be duplicate, NOT correct
            assert result["status"] == "duplicate"

    def test_check_single_record_spf_single_is_not_duplicate(self, maildomain_factory):
        """Test that a single SPF record is not flagged as duplicate."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_spf = MagicMock()
            mock_spf.to_text.return_value = '"v=spf1 include:_spf.example.com -all"'
            # Also has a non-SPF TXT record
            mock_other = MagicMock()
            mock_other.to_text.return_value = '"google-site-verification=abc123"'
            mock_resolve.return_value = [mock_spf, mock_other]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_single_record_dmarc_not_affected_by_spf_duplicate_check(
        self, maildomain_factory
    ):
        """Test that duplicate detection only applies to SPF, not other TXT records."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1; p=reject; adkim=s; aspf=s;",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=DMARC1; p=reject; adkim=s; aspf=s;"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_single_record_spf_insecure_plus_all(self, maildomain_factory):
        """Test that SPF with +all is detected as insecure when -all is expected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=spf1 include:_spf.example.com +all"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"
            assert "v=spf1 include:_spf.example.com +all" in result["found"]

    def test_check_single_record_spf_insecure_question_all(self, maildomain_factory):
        """Test that SPF with ?all is detected as insecure when -all is expected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=spf1 include:_spf.example.com ?all"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"

    def test_check_single_record_spf_tilde_all_accepted_as_correct(
        self, maildomain_factory
    ):
        """Test that SPF with ~all is accepted as correct when -all is expected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=spf1 include:_spf.example.com ~all"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            # ~all is accepted as correct when -all is expected
            assert result["status"] == "correct"

    def test_check_single_record_spf_insecure_not_triggered_when_expected_not_dash_all(
        self, maildomain_factory
    ):
        """Test that insecure check is skipped when expected SPF doesn't end with -all."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com ~all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=spf1 include:_spf.example.com +all"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            # Expected uses ~all, so insecure check doesn't apply
            assert result["status"] == "incorrect"

    def test_check_single_record_dmarc_duplicate(self, maildomain_factory):
        """Test that duplicate DMARC records are detected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer1 = MagicMock()
            mock_answer1.to_text.return_value = '"v=DMARC1;p=reject;adkim=s;aspf=s"'
            mock_answer2 = MagicMock()
            mock_answer2.to_text.return_value = '"v=DMARC1;p=none"'
            mock_resolve.return_value = [mock_answer1, mock_answer2]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "duplicate"
            assert len(result["found"]) == 2

    def test_check_single_record_dmarc_insecure_p_none(self, maildomain_factory):
        """Test that DMARC with p=none is detected as insecure when p=reject expected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=DMARC1;p=none"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"
            assert "v=DMARC1;p=none" in result["found"]

    def test_check_single_record_dmarc_insecure_not_triggered_when_expected_p_none(
        self, maildomain_factory
    ):
        """Test that insecure check is skipped when expected DMARC uses p=none."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=none",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = '"v=DMARC1;p=none"'
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_dns_records_conflicting_mx(self, maildomain_factory):
        """Test that extra MX records from other providers are detected as conflicting."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
            ]

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
                # Return our expected MX plus an extra one from another provider
                mock_mx1 = MagicMock()
                mock_mx1.preference = 10
                mock_mx1.exchange = "mx1.example.com"
                mock_mx2 = MagicMock()
                mock_mx2.preference = 20
                mock_mx2.exchange = "mx.otherprovider.com"
                mock_resolve.return_value = [mock_mx1, mock_mx2]

                results = check_dns_records(maildomain)

                assert len(results) == 1
                assert results[0]["_check"]["status"] == "conflicting"
                assert "10 mx1.example.com" in results[0]["_check"]["found"]
                assert "20 mx.otherprovider.com" in results[0]["_check"]["found"]

    def test_check_dns_records_mx_correct_no_extra(self, maildomain_factory):
        """Test that MX records without extra entries stay correct."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
            ]

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
                mock_mx1 = MagicMock()
                mock_mx1.preference = 10
                mock_mx1.exchange = "mx1.example.com"
                mock_resolve.return_value = [mock_mx1]

                results = check_dns_records(maildomain)

                assert len(results) == 1
                assert results[0]["_check"]["status"] == "correct"

    def test_check_dns_records_conflicting_mx_multiple_expected(
        self, maildomain_factory
    ):
        """Test conflicting detection with multiple expected MX records."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
                {"type": "MX", "target": "@", "value": "20 mx2.example.com"},
            ]

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
                # Both expected MX records present plus an extra one
                mock_mx1 = MagicMock()
                mock_mx1.preference = 10
                mock_mx1.exchange = "mx1.example.com"
                mock_mx2 = MagicMock()
                mock_mx2.preference = 20
                mock_mx2.exchange = "mx2.example.com"
                mock_mx3 = MagicMock()
                mock_mx3.preference = 30
                mock_mx3.exchange = "mx.legacy.com"
                mock_resolve.return_value = [mock_mx1, mock_mx2, mock_mx3]

                results = check_dns_records(maildomain)

                assert len(results) == 2
                # Both should be conflicting since extra MX is present
                assert results[0]["_check"]["status"] == "conflicting"
                assert results[1]["_check"]["status"] == "conflicting"

    def test_check_dns_records_mx_incorrect_not_conflicting(self, maildomain_factory):
        """Test that incorrect MX records are not marked as conflicting."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com"},
            ]

            with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
                # Only a foreign MX, our expected one is absent
                mock_mx = MagicMock()
                mock_mx.preference = 20
                mock_mx.exchange = "mx.otherprovider.com"
                mock_resolve.return_value = [mock_mx]

                results = check_dns_records(maildomain)

                assert len(results) == 1
                # Should be incorrect, not conflicting (our MX is not present)
                assert results[0]["_check"]["status"] == "incorrect"

    def test_check_single_record_with_subdomain(self, maildomain_factory):
        """Test checking a record for a subdomain."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "A", "target": "www", "value": "192.168.1.1"}

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Mock correct A record for subdomain
            mock_answer = MagicMock()
            mock_answer.to_text.return_value = "192.168.1.1"
            mock_resolve.return_value = [mock_answer]

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["192.168.1.1"]
            # Verify the query was made for the subdomain
            mock_resolve.assert_called_once_with("www.example.com", "A")

    @override_settings(MESSAGES_TECHNICAL_DOMAIN="example.com")
    def test_get_expected_dns_records_default(self, maildomain_factory):
        """Test that default MESSAGES_DNS_RECORDS produces the standard 4 records."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_active_dkim_key", return_value=None):
            records = maildomain.get_expected_dns_records()

        assert len(records) == 4
        assert records[0] == {
            "target": "",
            "type": "mx",
            "value": "10 mx1.example.com.",
        }
        assert records[1] == {
            "target": "",
            "type": "mx",
            "value": "20 mx2.example.com.",
        }
        assert records[2] == {
            "target": "",
            "type": "txt",
            "value": "v=spf1 include:_spf.example.com -all",
        }
        assert records[3] == {
            "target": "_dmarc",
            "type": "txt",
            "value": "v=DMARC1; p=reject; adkim=s; aspf=s;",
        }

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="example.com",
        MESSAGES_DNS_RECORDS=json.dumps(
            [
                {
                    "target": "",
                    "type": "mx",
                    "value": "10 custom-mx.{technical_domain}.",
                },
                {
                    "target": "",
                    "type": "txt",
                    "value": "v=spf1 include:custom.{technical_domain} -all",
                },
            ]
        ),
    )
    def test_get_expected_dns_records_custom_override(self, maildomain_factory):
        """Test that MESSAGES_DNS_RECORDS env override replaces the default records."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_active_dkim_key", return_value=None):
            records = maildomain.get_expected_dns_records()

        assert len(records) == 2
        assert records[0] == {
            "target": "",
            "type": "mx",
            "value": "10 custom-mx.example.com.",
        }
        assert records[1] == {
            "target": "",
            "type": "txt",
            "value": "v=spf1 include:custom.example.com -all",
        }

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="example.com",
        MESSAGES_DNS_RECORDS=json.dumps(
            [{"target": "", "type": "mx", "value": "10 custom-mx.{technical_domain}."}]
        ),
    )
    def test_get_expected_dns_records_custom_override_with_dkim(
        self, maildomain_factory
    ):
        """Test that DKIM is still appended when using a custom DNS records override."""
        maildomain = maildomain_factory(name="example.com")

        mock_dkim_key = MagicMock()
        mock_dkim_key.selector = "selector1"
        mock_dkim_key.get_dns_record_value.return_value = "v=DKIM1; k=rsa; p=MIGf..."

        with patch.object(
            maildomain, "get_active_dkim_key", return_value=mock_dkim_key
        ):
            records = maildomain.get_expected_dns_records()

        assert len(records) == 2
        assert records[0] == {
            "target": "",
            "type": "mx",
            "value": "10 custom-mx.example.com.",
        }
        assert records[1] == {
            "target": "selector1._domainkey",
            "type": "txt",
            "value": "v=DKIM1; k=rsa; p=MIGf...",
        }


@pytest.fixture(name="maildomain_factory")
@pytest.mark.django_db
def fixture_maildomain_factory():
    """Factory for creating test mail domains."""

    def _create_maildomain(name="test.com"):
        return MailDomain.objects.create(name=name)

    return _create_maildomain
