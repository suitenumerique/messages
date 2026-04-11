"""
Tests for DNS checking functionality.
"""
# pylint: disable=too-many-lines

import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import override_settings

import pytest
from dns.resolver import NXDOMAIN, YXDOMAIN, NoAnswer, NoNameservers, Timeout

from core.models import MailDomain
from core.services.dns.check import (
    check_dns_records,
    check_single_record,
    check_spf_status,
    invalidate_spf_check_cache,
    parse_dkim_tags,
    parse_spf_terms,
)


def _txt_rr(value):
    """Create a mock TXT resource record with .strings for dnspython rrset."""
    rr = MagicMock()
    rr.strings = (value.encode(),)
    return rr


def _txt_answer(*values):
    """Create a mock dns.resolver answer for TXT records."""
    rrs = [_txt_rr(v) for v in values]
    answer = MagicMock()
    answer.rrset = rrs
    return answer


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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com -all"
            )

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
                        return _txt_answer(
                            "some-garbage",
                            "v=spf1 include:_spf.example.com -all",
                            "some-garbage",
                        )

                    if record_type == "TXT" and name == "_spf.example.com":
                        return _txt_answer("v=spf1 ip4:1.2.3.4 -all")

                    if record_type == "TXT" and name in (
                        "_dmarc.example.com",
                        "_dmarc_stripped.example.com",
                    ):
                        return _txt_answer("v=DMARC1; p=reject; adkim=s; aspf=s;")

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
        """Test checking DNS records with mixed status (correct, missing SPF, missing A)."""
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
                # Mock responses: correct MX, no SPF found, missing A
                mock_mx_answer = MagicMock()
                mock_mx_answer.preference = 10
                mock_mx_answer.exchange = "mx1.example.com"

                mock_resolve.side_effect = [
                    [mock_mx_answer],  # Correct MX
                    _txt_answer("some-unrelated-record"),  # No SPF record
                    NoAnswer(),  # Missing A record
                ]

                results = check_dns_records(maildomain)

                assert len(results) == 3
                assert results[0]["_check"]["status"] == "correct"
                assert results[1]["_check"]["status"] == "missing"
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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com -all",
                "v=spf1 include:_spf.legacy-provider.com ~all",
            )

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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com -all",
                "v=spf1 include:_spf.legacy-provider.com ~all",
            )

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
            # Also has a non-SPF TXT record
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com -all",
                "google-site-verification=abc123",
            )

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_single_record_spf_found_when_resolver_merges_txt_records(
        self, maildomain_factory
    ):
        """Regression: some local resolvers (e.g. systemd-resolved) merge
        separate TXT records into a single RR with multiple strings. SPF must
        still be found by iterating individual strings."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Single RR with two strings (merged by local resolver)
            merged_rr = MagicMock()
            merged_rr.strings = (
                b"google-site-verification=abc123",
                b"v=spf1 include:_spf.example.com -all",
            )
            answer = MagicMock()
            answer.rrset = [merged_rr]
            mock_resolve.return_value = answer

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
            mock_resolve.return_value = _txt_answer(
                "v=DMARC1; p=reject; adkim=s; aspf=s;"
            )

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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com +all"
            )

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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com ?all"
            )

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
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com ~all"
            )

            result = check_single_record(maildomain, expected_record)

            # ~all is accepted as correct when -all is expected
            assert result["status"] == "correct"

    def test_check_single_record_spf_insecure_when_all_weaker(self, maildomain_factory):
        """Test that weaker 'all' mechanism is reported as insecure when includes resolve."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com ~all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com +all"
            )

            result = check_single_record(maildomain, expected_record)

            # Includes resolve but +all is weaker than ~all
            assert result["status"] == "insecure"

    def test_check_single_record_dmarc_duplicate(self, maildomain_factory):
        """Test that duplicate DMARC records are detected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=DMARC1;p=reject;adkim=s;aspf=s",
                "v=DMARC1;p=none",
            )

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
            mock_resolve.return_value = _txt_answer("v=DMARC1;p=none")

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
            mock_resolve.return_value = _txt_answer("v=DMARC1;p=none")

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


class TestParseDkimTags:
    """Test DKIM tag parsing."""

    def test_basic_dkim_record(self):
        """Test parsing a standard DKIM record."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_reordered_tags(self):
        """Test parsing DKIM with reordered tags."""
        result = parse_dkim_tags("v=DKIM1; p=MIGfMA0; k=rsa")
        assert result == {"v": "DKIM1", "p": "MIGfMA0", "k": "rsa"}

    def test_with_t_s_flag(self):
        """Test parsing DKIM with t=s (strict) flag."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=s")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "s"}

    def test_with_t_y_flag(self):
        """Test parsing DKIM with t=y (testing) flag."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=y")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "y"}

    def test_with_t_y_s_flags(self):
        """Test parsing DKIM with t=y:s (testing+strict) flags."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=y:s")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "y:s"}

    def test_v_not_first_returns_none(self):
        """Test that v= not being first tag returns None."""
        assert parse_dkim_tags("k=rsa; v=DKIM1; p=MIGfMA0") is None

    def test_wrong_version_returns_none(self):
        """Test that wrong DKIM version returns None."""
        assert parse_dkim_tags("v=DKIM2; k=rsa; p=MIGfMA0") is None

    def test_empty_string_returns_none(self):
        """Test that empty string returns None."""
        assert parse_dkim_tags("") is None


class TestParseSpfTerms:
    """Test SPF term parsing."""

    def test_basic_spf(self):
        """Test parsing a basic SPF record."""
        all_mech, terms = parse_spf_terms("v=spf1 include:_spf.example.com -all")
        assert all_mech == "-all"
        assert terms == {"include:_spf.example.com"}

    def test_multiple_includes(self):
        """Test parsing SPF with multiple includes."""
        all_mech, terms = parse_spf_terms(
            "v=spf1 include:_spf.example.com include:other.com -all"
        )
        assert all_mech == "-all"
        assert terms == {"include:_spf.example.com", "include:other.com"}

    def test_tilde_all(self):
        """Test parsing SPF with ~all mechanism."""
        all_mech, _terms = parse_spf_terms("v=spf1 include:_spf.example.com ~all")
        assert all_mech == "~all"

    def test_not_spf_returns_none(self):
        """Test that non-SPF record returns None."""
        assert parse_spf_terms("not-an-spf-record") is None

    def test_no_all_mechanism(self):
        """Test parsing SPF without an all mechanism."""
        all_mech, terms = parse_spf_terms("v=spf1 include:_spf.example.com")
        assert all_mech is None
        assert terms == {"include:_spf.example.com"}


@pytest.mark.django_db
class TestDKIMSemanticComparison:
    """Test DKIM semantic comparison in check_single_record."""

    def test_dkim_with_extra_t_s_flag_is_correct(self, maildomain_factory):
        """DKIM record with t=s appended should still be valid."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa; p=MIGfMA0; t=s")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_with_t_y_flag_is_insecure(self, maildomain_factory):
        """DKIM record with t=y (testing mode) should be marked insecure."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa; p=MIGfMA0; t=y")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "insecure"

    def test_dkim_with_t_y_s_flags_is_insecure(self, maildomain_factory):
        """DKIM record with t=y:s (testing + strict) should be marked insecure."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa; p=MIGfMA0; t=y:s")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "insecure"

    def test_dkim_reordered_tags_is_correct(self, maildomain_factory):
        """DKIM record with reordered tags (v= still first) should be valid."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; p=MIGfMA0; k=rsa")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_wrong_key_is_incorrect(self, maildomain_factory):
        """DKIM record with wrong public key should be incorrect."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa; p=WRONG_KEY")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_multiline_txt_record_with_t_s(self, maildomain_factory):
        """Multiline DKIM TXT record (split across quoted strings) with t=s."""
        maildomain = maildomain_factory(name="example.com")
        long_key = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC"
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": f"v=DKIM1; k=rsa; p={long_key}",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # Simulate DNS returning a split TXT record with extra t=s tag
            rr = MagicMock()
            rr.strings = (
                b"v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBA",
                b"QUAA4GNADCBiQKBgQC; t=s",
            )
            answer = MagicMock()
            answer.rrset = [rr]
            mock_resolve.return_value = answer

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_multiline_txt_record_reordered_with_t_y(self, maildomain_factory):
        """Multiline DKIM TXT record with reordered tags and t=y is insecure."""
        maildomain = maildomain_factory(name="example.com")
        long_key = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC"
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": f"v=DKIM1; k=rsa; p={long_key}",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            rr = MagicMock()
            rr.strings = (
                b"v=DKIM1; t=y; p=MIGfMA0GCSqGSIb3DQEBA",
                b"QUAA4GNADCBiQKBgQC; k=rsa",
            )
            answer = MagicMock()
            answer.rrset = [rr]
            mock_resolve.return_value = answer

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "insecure"


@pytest.mark.django_db
class TestSPFSemanticComparison:
    """Test SPF semantic comparison in check_single_record."""

    def test_spf_reordered_terms_is_correct(self, maildomain_factory):
        """SPF record with reordered mechanisms should be valid."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com include:other.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:other.com include:_spf.example.com -all"
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_spf_reordered_with_tilde_all_accepted(self, maildomain_factory):
        """SPF with reordered terms and ~all accepted when -all expected."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com ~all"
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_spf_with_extra_includes_is_correct(self, maildomain_factory):
        """SPF with extra includes (superset) should be valid."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com include:extra.com -all"
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_spf_missing_expected_include_is_incorrect(self, maildomain_factory):
        """SPF missing an expected include should be incorrect."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=spf1 include:other.com -all")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"


@pytest.fixture(name="maildomain_factory")
def fixture_maildomain_factory():
    """Factory for creating test mail domains."""

    def _create_maildomain(name="test.com"):
        return MailDomain.objects.create(name=name)

    return _create_maildomain


@pytest.mark.django_db
class TestSPFRecursiveCheck:
    """Test recursive SPF include checking."""

    def test_spf_include_single_level_found(self, maildomain_factory, settings):
        """Include target under technical_domain resolves to valid SPF."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                return []

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_spf_include_not_found_on_incorrect_record(
        self, maildomain_factory, settings
    ):
        """When the found SPF doesn't match, recursive check still runs."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.other.com -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_no_include_no_recursive_check(self, maildomain_factory):
        """SPF without include: terms gets no recursive check."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 ip4:1.2.3.4 -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=spf1 ip4:1.2.3.4 -all")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_spf_real_recursion_two_levels(self, maildomain_factory, settings):
        """BFS follows found chain to reach expected technical include 2 levels deep."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        # Expected: we want _spf2.messages.org to be reachable
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf2.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    # Found: includes _spf.messages.org (not _spf2 directly)
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                # Level 1: _spf.messages.org includes _spf2.messages.org
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 include:_spf2.messages.org -all")
                # Level 2: _spf2.messages.org has actual IPs
                if name == "_spf2.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_spf_bfs_breadth_first_ordering(self, maildomain_factory, settings):
        """BFS processes siblings before children to reach nested target."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        # We expect child-a.messages.org — only reachable through a.messages.org
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:child-a.messages.org -all",
        }
        resolved_order = []

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:a.messages.org include:b.messages.org -all"
                    )
                resolved_order.append(name)
                if name == "a.messages.org":
                    return _txt_answer("v=spf1 include:child-a.messages.org -all")
                if name == "b.messages.org":
                    return _txt_answer("v=spf1 ip4:2.3.4.5 -all")
                if name == "child-a.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            # BFS: a, b processed before child-a
            assert resolved_order == [
                "a.messages.org",
                "b.messages.org",
                "child-a.messages.org",
            ]

    def test_spf_10_lookup_limit(self, maildomain_factory, settings):
        """RFC 7208: max 10 DNS lookups for mechanisms. Chain of exactly 10
        includes succeeds, 11th triggers the limit."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")

        # Chain of 10: _spf1 -> _spf2 -> ... -> _spf10 (= target)
        # Should succeed: exactly 10 lookups.
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf10.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf1.messages.org -all")
                for i in range(1, 10):
                    if name == f"_spf{i}.messages.org":
                        return _txt_answer(
                            f"v=spf1 include:_spf{i + 1}.messages.org -all"
                        )
                if name == "_spf10.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

        # Chain of 11: needs 11 lookups, should hit the limit.
        expected_record_11 = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf11.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect_11(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf1.messages.org -all")
                for i in range(1, 11):
                    if name == f"_spf{i}.messages.org":
                        return _txt_answer(
                            f"v=spf1 include:_spf{i + 1}.messages.org -all"
                        )
                if name == "_spf11.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect_11
            result = check_single_record(maildomain, expected_record_11)
            assert result["status"] == "incorrect"

    def test_spf_dns_error_means_not_found(self, maildomain_factory, settings):
        """DNS resolution failure on an include target = include_found: False."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                # Include target fails to resolve
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_duplicate_record_in_include_chain(self, maildomain_factory, settings):
        """Duplicate SPF records on an include target = duplicate status."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    # Two SPF records — customer duplicated the record
                    return _txt_answer(
                        "v=spf1 ip4:1.2.3.4 -all",
                        "v=spf1 ip4:5.6.7.8 -all",
                    )
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "duplicate"


@pytest.mark.django_db
class TestCheckSPFStatus:
    """Test check_spf_status with caching."""

    def setup_method(self):
        """Clear cache before each test."""
        cache.clear()

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_returns_true_when_spf_correct(self, maildomain_factory):
        """Correct SPF with valid include returns True."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            assert check_spf_status(maildomain) is True

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_returns_false_when_spf_missing(self, maildomain_factory):
        """Missing SPF record returns False."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = NXDOMAIN()
            assert check_spf_status(maildomain) is False

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_returns_false_when_include_not_found(self, maildomain_factory):
        """SPF exists but include target doesn't resolve returns False."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            assert check_spf_status(maildomain) is False

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_result_is_cached(self, maildomain_factory):
        """Second call uses cache, no DNS queries."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect

            # First call does DNS
            assert check_spf_status(maildomain) is True
            first_call_count = mock_resolve.call_count

            # Second call uses cache — no additional DNS queries
            assert check_spf_status(maildomain) is True
            assert mock_resolve.call_count == first_call_count

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_invalidate_clears_cache(self, maildomain_factory):
        """After invalidation, next call does fresh DNS queries."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect

            # Populate cache
            assert check_spf_status(maildomain) is True
            first_call_count = mock_resolve.call_count

            # Invalidate
            invalidate_spf_check_cache(maildomain)

            # Next call does DNS again
            assert check_spf_status(maildomain) is True
            assert mock_resolve.call_count > first_call_count

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_transient_dns_error_not_cached(self, maildomain_factory):
        """DNS timeout should return False but NOT cache the result."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # First call: DNS timeout (transient error)
            mock_resolve.side_effect = Timeout()
            assert check_spf_status(maildomain) is False

            # Second call: DNS works now — should NOT use cache
            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            assert check_spf_status(maildomain) is True

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_definitive_failure_is_cached(self, maildomain_factory):
        """A definitive SPF misconfiguration (missing record) should be cached."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            # NXDOMAIN is a definitive failure (status=missing, not error)
            mock_resolve.side_effect = NXDOMAIN()
            assert check_spf_status(maildomain) is False
            first_call_count = mock_resolve.call_count

            # Second call should use cache — no additional DNS queries
            assert check_spf_status(maildomain) is False
            assert mock_resolve.call_count == first_call_count

    @override_settings(
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 ip4:1.2.3.4 -all"}]',
    )
    def test_returns_true_when_no_spf_expected(self, maildomain_factory):
        """No includes in expected SPF = always True."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=spf1 ip4:1.2.3.4 -all")
            assert check_spf_status(maildomain) is True

    def test_spf_non_technical_domain_includes_still_traversed(
        self, maildomain_factory, settings
    ):
        """Non-technical-domain includes are resolved (BFS traversal) but
        DNS errors on them don't cause include_found=False."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.other.com include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            resolved_names = []

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:_spf.other.com include:_spf.messages.org -all"
                    )
                resolved_names.append(name)
                if name == "_spf.other.com":
                    # Non-technical include resolves fine, has no children
                    return _txt_answer("v=spf1 ip4:9.9.9.9 -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            # Both includes were resolved (BFS follows everything)
            assert "_spf.other.com" in resolved_names
            assert "_spf.messages.org" in resolved_names

    def test_spf_no_recursive_check_in_exception_handler(
        self, maildomain_factory, settings
    ):
        """When the initial DNS query fails, no recursive check should run."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = Exception("Connection refused")
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"

    def test_spf_include_target_no_spf_record(self, maildomain_factory, settings):
        """Include target exists but has no SPF record = include_found: False."""
        settings.MESSAGES_TECHNICAL_DOMAIN = "messages.org"
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.messages.org -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    # TXT record exists but is not SPF
                    return _txt_answer("not an spf record")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
