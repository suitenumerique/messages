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
    spf_syntax_is_valid,
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

        Per RFC 7208, a domain must not have multiple SPF records. Domains
        that kept a previous provider's record alongside ours are the common
        case in the wild.
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

    def test_check_single_record_spf_found_among_other_txt_records(
        self, maildomain_factory
    ):
        """SPF must be found when the name carries other TXT records too.

        Separate TXT records arrive as separate resource records, each with
        its own strings: the RDATA boundary is part of the record framing, so
        no resolver can merge two records into one. Several strings on one
        record therefore always belong together (RFC 7208 3.3).
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "google-site-verification=abc123",
                "v=spf1 include:_spf.example.com -all",
            )

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

    def test_whitespace_around_equals(self):
        """RFC 6376 3.2 allows folding whitespace on both sides of the '='."""
        result = parse_dkim_tags("v = DKIM1; k = rsa; p = MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_missing_v_defaults_to_dkim1(self):
        """RFC 6376 3.6.1: v= is optional in a key record and defaults to DKIM1."""
        result = parse_dkim_tags("k=rsa; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_missing_k_defaults_to_rsa(self):
        """RFC 6376 3.6.1: k= is optional in a key record and defaults to rsa."""
        result = parse_dkim_tags("v=DKIM1; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_record_without_any_tag_returns_none(self):
        """A TXT record with no tag=value pair is not a DKIM record."""
        assert parse_dkim_tags("not a dkim record") is None

    def test_segment_without_equals_returns_none(self):
        """RFC 6376 3.2: every segment must be a tag=value pair."""
        assert parse_dkim_tags("v; v=DKIM1; k=rsa; p=MIGfMA0") is None
        assert parse_dkim_tags("k=rsa; p=MIGfMA0; trailing") is None

    def test_empty_tag_name_returns_none(self):
        """A segment with no tag name before the '=' is malformed."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; =MIGfMA0") is None

    def test_trailing_semicolon_is_valid(self):
        """A trailing semicolon is explicitly allowed by RFC 6376 3.2."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0;")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_leading_semicolon_returns_none(self):
        """Only a trailing semicolon is optional; a leading one is malformed."""
        assert parse_dkim_tags("; v=DKIM1; k=rsa; p=MIGfMA0") is None

    def test_interior_empty_tag_spec_returns_none(self):
        """An empty tag-spec between two others is malformed."""
        assert parse_dkim_tags("v=DKIM1; k=rsa;; p=MIGfMA0") is None

    def test_second_trailing_semicolon_returns_none(self):
        """RFC 6376 3.2 allows one optional trailing semicolon, not two."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0;;") is None

    def test_tag_name_not_starting_with_alpha_returns_none(self):
        """RFC 6376 3.2: a tag name must start with an ALPHA."""
        assert parse_dkim_tags("v=DKIM1; 2x=junk; k=rsa; p=MIGfMA0") is None
        assert parse_dkim_tags("v=DKIM1; _x=junk; k=rsa; p=MIGfMA0") is None

    def test_vendor_tag_name_is_accepted(self):
        """Tags like "x-foo" are used in the wild and verify, so keep them."""
        result = parse_dkim_tags("v=DKIM1; x-vendor=junk; k=rsa; p=MIGfMA0")
        assert result == {
            "v": "DKIM1",
            "x-vendor": "junk",
            "k": "rsa",
            "p": "MIGfMA0",
        }

    def test_duplicate_p_tag_returns_none(self):
        """RFC 6376 3.2: a duplicate tag name invalidates the whole tag-list."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=AAAA; p=MIGfMA0") is None

    def test_duplicate_v_tag_returns_none(self):
        """A repeated v= tag invalidates the record even if both values match."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; v=DKIM1") is None

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
        assert terms == {"+include:_spf.example.com"}

    def test_multiple_includes(self):
        """Test parsing SPF with multiple includes."""
        all_mech, terms = parse_spf_terms(
            "v=spf1 include:_spf.example.com include:other.com -all"
        )
        assert all_mech == "-all"
        assert terms == {"+include:_spf.example.com", "+include:other.com"}

    def test_tilde_all(self):
        """Test parsing SPF with ~all mechanism."""
        all_mech, _terms = parse_spf_terms("v=spf1 include:_spf.example.com ~all")
        assert all_mech == "~all"

    def test_not_spf_returns_none(self):
        """Test that non-SPF record returns None."""
        assert parse_spf_terms("not-an-spf-record") is None

    def test_version_is_case_insensitive(self):
        """RFC 7208 12: ABNF literals are case-insensitive."""
        all_mech, terms = parse_spf_terms("V=sPf1 MX -ALL")
        assert all_mech == "-all"
        assert terms == {"+mx"}

    def test_version_must_be_terminated(self):
        """RFC 7208 4.5: "v=spf10" is not an SPF record."""
        assert parse_spf_terms("v=spf10 include:_spf.example.com -all") is None

    def test_version_must_be_ascii(self):
        """RFC 7208 3.1 encodes records in US-ASCII and receivers compare
        bytes, but Unicode case folding maps U+017F onto "s" and U+212A onto
        "k". A record spelled with either is no record at all."""
        assert parse_spf_terms("v=ſpf1 include:_spf.example.com -all") is None
        assert parse_spf_terms("v=spf1 -all".replace("k", "K")) is not None

    def test_version_must_be_terminated_by_a_space(self):
        """RFC 7208 4.6.1 separates terms with SP alone. Receivers read a
        record broken by another control character as no record at all, so we
        must not report it as one either."""
        assert parse_spf_terms("v=spf1\tinclude:_spf.example.com -all") is None

    def test_empty_record_is_valid(self):
        """RFC 7208 4.5: a bare "v=spf1" is a record, with no terms."""
        all_mech, terms = parse_spf_terms("v=spf1")
        assert all_mech is None
        assert terms == set()

    def test_qualifiers_are_made_explicit(self):
        """An omitted qualifier means "+", so both spellings are one term."""
        _all_mech, terms = parse_spf_terms("v=spf1 +MX ip4:1.2.3.4")
        assert terms == {"+mx", "+ip4:1.2.3.4"}

    def test_bare_all_means_pass(self):
        """A bare "all" carries the implicit "+" qualifier."""
        all_mech, _terms = parse_spf_terms("v=spf1 mx all")
        assert all_mech == "+all"

    def test_first_all_wins(self):
        """RFC 7208 5.1: "all" always matches, so anything after it is never
        tested — including a second, stricter "all"."""
        all_mech, _terms = parse_spf_terms("v=spf1 +all -all")
        assert all_mech == "+all"

    def test_mechanisms_after_all_are_dropped(self):
        """Mechanisms listed after "all" MUST be ignored (RFC 7208 5.1)."""
        all_mech, terms = parse_spf_terms("v=spf1 mx -all ip4:1.2.3.4")
        assert all_mech == "-all"
        assert terms == {"+mx"}

    def test_modifiers_after_all_are_kept(self):
        """Modifiers are not mechanisms, so an "all" does not skip them."""
        _all_mech, terms = parse_spf_terms("v=spf1 -all exp=why.example.com")
        assert terms == {"exp=why.example.com"}


class TestSpfSyntaxIsValid:
    """Test the SPF record syntax check."""

    def test_known_mechanisms_and_modifiers(self):
        """Every mechanism of RFC 7208 5, plus modifiers, are accepted."""
        assert spf_syntax_is_valid(
            "v=spf1 a mx ptr ip4:1.2.3.4 ip6:2001:db8::1 exists:%{i}.e.com"
            " include:x.example.com redirect=y.example.com exp=z.example.com -all"
        )

    def test_unknown_mechanism(self):
        """An unknown bare term is a syntax error (RFC 7208 4.6)."""
        assert not spf_syntax_is_valid("v=spf1 include:x.example.com gibberish -all")

    def test_unknown_modifier_is_ignored(self):
        """RFC 7208 6: unrecognized modifiers MUST be ignored, not rejected."""
        assert spf_syntax_is_valid(
            "v=spf1 moo.cow-far_out=man:dog/cat ip4:1.2.3.4 -all"
        )

    def test_case_insensitive(self):
        """Mechanism names are case-insensitive (RFC 7208 12)."""
        assert spf_syntax_is_valid("v=spf1 MX Include:x.example.com -ALL")

    def test_well_formed_ip_literals(self):
        """ip4 and ip6 take an address with an optional CIDR length."""
        assert spf_syntax_is_valid(
            "v=spf1 ip4:1.2.3.4 ip4:192.0.2.0/24 ip6:2001:db8::1 ip6:2001:db8::/32 -all"
        )

    def test_malformed_ip4_literal(self):
        """RFC 7208 12 spells ip4-network as a dotted quad of 0-255 values."""
        assert not spf_syntax_is_valid("v=spf1 ip4:999.1.1.1 -all")

    def test_truncated_ip4_literal(self):
        """RFC 7208 5.6: parts may not be omitted in place of a CIDR."""
        assert not spf_syntax_is_valid("v=spf1 ip4:192.0.2 -all")

    def test_malformed_ip6_literal(self):
        """ip6-network is an address per RFC 4291 2.2."""
        assert not spf_syntax_is_valid("v=spf1 ip6:gggg::1 -all")

    def test_cidr_length_out_of_range(self):
        """RFC 7208 12 bounds the lengths at 32 for ip4 and 128 for ip6."""
        assert not spf_syntax_is_valid("v=spf1 ip4:1.2.3.4/33 -all")
        assert not spf_syntax_is_valid("v=spf1 ip6:2001:db8::1/129 -all")

    def test_dual_cidr_not_allowed_on_ip4(self):
        """The dual "//" form belongs to a and mx, not to ip4 and ip6."""
        assert not spf_syntax_is_valid("v=spf1 ip4:1.2.3.4//24 -all")

    def test_a_and_mx_bare_dual_cidr_is_checked(self):
        """With no domain-spec the whole argument is a dual-cidr-length, so
        there is nothing it could be confused with (RFC 7208 12)."""
        assert spf_syntax_is_valid("v=spf1 a/24//64 mx/32 a//128 -all")
        assert not spf_syntax_is_valid("v=spf1 mx/99 -all")
        assert not spf_syntax_is_valid("v=spf1 a//129 -all")
        assert not spf_syntax_is_valid("v=spf1 a/ -all")

    def test_a_and_mx_domain_spec_is_not_checked(self):
        """Known limitation: once a domain-spec is present it may hold a macro
        carrying a "/" of its own, so the argument is left alone."""
        assert spf_syntax_is_valid("v=spf1 a:foo.example.com/24//64 -all")
        assert spf_syntax_is_valid("v=spf1 mx:%{d}/99 -all")

    def test_defined_modifiers_need_a_domain_spec(self):
        """RFC 7208 6.1 and 6.2 spell redirect and exp with a domain-spec,
        which is never empty."""
        assert not spf_syntax_is_valid("v=spf1 redirect=")
        assert not spf_syntax_is_valid("v=spf1 exp= -all")

    def test_unknown_modifier_may_be_empty(self):
        """RFC 7208 12: unknown-modifier takes a macro-string, and that one
        is allowed to be empty."""
        assert spf_syntax_is_valid("v=spf1 zzz= -all")

    def test_non_ascii_lookalike_is_not_a_mechanism_name(self):
        """Case-insensitive matching must stay ASCII: U+017F case-folds to
        "s" and U+212A to "k", but receivers compare bytes."""
        assert not spf_syntax_is_valid("v=spf1 ſoo=x -all")

    def test_qualified_modifier(self):
        """RFC 7208 4.6.1: a qualifier belongs to a directive. A modifier is a
        bare "name=value", so a qualified one is neither."""
        assert not spf_syntax_is_valid("v=spf1 +redirect=a.example.com")
        assert not spf_syntax_is_valid("v=spf1 -zzz=one -all")

    def test_modifier_name_must_follow_the_grammar(self):
        """RFC 7208 12: name = ALPHA *( ALPHA / DIGIT / "-" / "_" / "." )."""
        assert not spf_syntax_is_valid("v=spf1 1bad=x -all")
        assert not spf_syntax_is_valid("v=spf1 =x -all")
        assert spf_syntax_is_valid("v=spf1 moo.cow-far_out=man:dog/cat -all")

    def test_repeated_redirect_modifier(self):
        """RFC 7208 6: redirect= MUST NOT appear more than once."""
        assert not spf_syntax_is_valid(
            "v=spf1 redirect=a.example.com redirect=b.example.com"
        )

    def test_repeated_exp_modifier(self):
        """RFC 7208 6: exp= MUST NOT appear more than once."""
        assert not spf_syntax_is_valid(
            "v=spf1 exp=a.example.com exp=b.example.com -all"
        )

    def test_repeated_unknown_modifier_is_allowed(self):
        """Only redirect= and exp= are capped; others MUST just be ignored."""
        assert spf_syntax_is_valid("v=spf1 zzz=one zzz=two -all")

    def test_mechanism_missing_its_required_argument(self):
        """include, exists, ip4 and ip6 all spell a mandatory ":" argument."""
        assert not spf_syntax_is_valid("v=spf1 include -all")
        assert not spf_syntax_is_valid("v=spf1 include: -all")
        assert not spf_syntax_is_valid("v=spf1 ip4 -all")

    def test_all_takes_no_argument(self):
        """RFC 7208 12 spells all as the bare word."""
        assert not spf_syntax_is_valid("v=spf1 -all:example.com")

    def test_optional_argument_may_be_omitted(self):
        """a, mx and ptr default to the current domain."""
        assert spf_syntax_is_valid("v=spf1 a mx ptr -all")
        assert not spf_syntax_is_valid("v=spf1 a: -all")

    def test_non_spf_value(self):
        """A value that is not a record at all cannot be a valid one."""
        assert not spf_syntax_is_valid("google-site-verification=abc123")

    def test_modifiers_keep_no_qualifier(self):
        """Modifiers are name=value pairs and take no qualifier."""
        _all_mech, terms = parse_spf_terms("v=spf1 redirect=_spf.example.com")
        assert terms == {"redirect=_spf.example.com"}

    def test_macro_argument_keeps_its_case(self):
        """ "%{s}" and "%{S}" do not expand the same way (RFC 7208 7.3)."""
        _all_mech, terms = parse_spf_terms("v=spf1 exists:%{S}.example.com")
        assert terms == {"+exists:%{S}.example.com"}

    def test_no_all_mechanism(self):
        """Test parsing SPF without an all mechanism."""
        all_mech, terms = parse_spf_terms("v=spf1 include:_spf.example.com")
        assert all_mech is None
        assert terms == {"+include:_spf.example.com"}


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

    def test_dkim_key_with_internal_whitespace_is_correct(self, maildomain_factory):
        """Whitespace inside the base64 key is not significant (RFC 6376)."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=DKIM1; k=rsa; p=MIGfMA0 GCSqGSIb3"
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_whitespace_in_other_tags_is_significant(self, maildomain_factory):
        """Internal whitespace outside base64 tags still marks a mismatch."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=r sa; p=MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_whitespace_before_equals_is_correct(self, maildomain_factory):
        """Folding whitespace before the '=' is legal, including on v= (RFC 6376)."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v = DKIM1; k=rsa; p = MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_without_v_tag_is_correct(self, maildomain_factory):
        """A key record omitting the optional v= tag is still valid (RFC 6376)."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("k=rsa; p=MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_without_k_tag_is_correct(self, maildomain_factory):
        """A record omitting the optional k= tag signs as rsa and is valid."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; p=MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_dkim_missing_k_does_not_match_ed25519(self, maildomain_factory):
        """The rsa default must not satisfy an expected ed25519 key."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=ed25519; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; p=MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_double_trailing_semicolon_is_incorrect(self, maildomain_factory):
        """Normalization must not turn a repeated trailing ';' into a valid one."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa; p=MIGfMA0;;")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_malformed_segment_is_incorrect(self, maildomain_factory):
        """A bare segment must not let a record through the semantic check.

        The leading "v" segment would otherwise satisfy the "v= must be first"
        rule while the record itself is malformed and fails verification.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v; v=DKIM1; k=rsa; p=MIGfMA0")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_trailing_junk_segment_is_incorrect(self, maildomain_factory):
        """A trailing segment without '=' invalidates the whole tag-list."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("k=rsa; p=MIGfMA0; trailing")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "incorrect"

    def test_dkim_missing_expected_tag_is_incorrect(self, maildomain_factory):
        """A tag present in the expected record but absent in DNS is a mismatch."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "selector._domainkey",
            "value": "v=DKIM1; k=rsa; p=MIGfMA0",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DKIM1; k=rsa")

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


@pytest.mark.django_db
class TestSPFValidRecordsAreNotFlagged:
    """Valid SPF records that must not be reported as broken.

    A false "incorrect" here blocks outgoing mail (MESSAGES_SPF_CHECK_OUTGOING),
    so every shape RFC 7208 allows has to be accepted.
    """

    @staticmethod
    def _resolver(records):
        """Side effect resolving TXT queries from a {name: answer} mapping."""

        def resolve_side_effect(name, _record_type):
            if name not in records:
                raise NXDOMAIN()
            return records[name]

        return resolve_side_effect

    def test_explicit_plus_qualifier_on_include(self, maildomain_factory):
        """RFC 7208 4.6.1: every mechanism takes an optional qualifier, so
        "+include:" delegates exactly like "include:".

        Records of this shape are published in the wild and used to be
        flagged as incorrect, which stopped us from sending for them.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer(
                        "v=spf1 +mx +a +include:_spf.example.com"
                        " +include:spf.example.net -all",
                        "google-site-verification=abc123",
                    ),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                    "spf.example.net": _txt_answer("v=spf1 ip4:5.6.7.8 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_uppercase_version_and_mechanism_names(self, maildomain_factory):
        """RFC 7208 12: ABNF literals are case-insensitive, so "V=SPF1" and
        "INCLUDE:" are a valid record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer(
                        "V=SPF1 MX INCLUDE:_SPF.Example.COM -ALL"
                    ),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_record_split_across_several_strings(self, maildomain_factory):
        """RFC 7208 3.3: the strings of one TXT record are concatenated with no
        separator, which is how a value over the 255-octet character-string
        limit is published. Publishers split where they like, not only at
        exactly 255 octets, so the split point carries no meaning."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        split_rr = MagicMock()
        split_rr.strings = (
            b"v=spf1 include:a.example.net include:b.example.net ",
            b"include:_spf.example.com -all",
        )
        answer = MagicMock()
        answer.rrset = [split_rr]

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": answer,
                    "a.example.net": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                    "b.example.net": _txt_answer("v=spf1 ip4:5.6.7.8 -all"),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:9.10.11.12 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_delegation_through_redirect_modifier(self, maildomain_factory):
        """RFC 7208 6.1: "redirect=" hands the whole decision to another
        domain's record, so it reaches our include just like an include does."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer("v=spf1 redirect=policy.example.net"),
                    "policy.example.net": _txt_answer(
                        "v=spf1 include:_spf.example.com -all"
                    ),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            # The delegation is in place; only the "all" is missing locally.
            assert result["status"] == "insecure"

    def test_v_spf10_is_not_a_second_record(self, maildomain_factory):
        """RFC 7208 4.5: the version section is terminated by a space or the end
        of the record, so "v=spf10" is not an SPF record and cannot make the
        domain look like it publishes two."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer(
                        "v=spf1 include:_spf.example.com -all",
                        "v=spf10 include:legacy.example.net ~all",
                    ),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_broken_third_party_include_does_not_hide_ours(self, maildomain_factory):
        """A third party duplicating its own SPF record is not our customer's
        problem as long as the include we asked for is in place."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer(
                        "v=spf1 include:_spf.example.com include:broken.example.net -all"
                    ),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                    "broken.example.net": _txt_answer(
                        "v=spf1 ip4:5.6.7.8 -all",
                        "v=spf1 ip4:9.10.11.12 ~all",
                    ),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_long_chain_after_our_include_does_not_hide_it(self, maildomain_factory):
        """Hitting the 10-lookup limit further along the record must not mask an
        include that already resolved."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }
        # Our include first, then a long tail of other providers.
        records = {
            "example.com": _txt_answer(
                "v=spf1 include:_spf.example.com "
                + " ".join(f"include:p{i}.example.net" for i in range(12))
                + " -all"
            ),
            "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
        }
        for i in range(12):
            records[f"p{i}.example.net"] = _txt_answer(f"v=spf1 ip4:10.0.0.{i} -all")

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(records)

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_expected_value_without_an_all_mechanism(self, maildomain_factory):
        """A configured expected value carrying no "all" ends in the implicit
        "?all" (RFC 7208 4.7); a stricter published one satisfies it. Reading
        it as "no acceptable all exists" would make the check unsatisfiable."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer("v=spf1 include:_spf.example.com -all"),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_expected_value_without_an_all_still_rejects_plus_all(
        self, maildomain_factory
    ):
        """The implicit "?all" is still stricter than a published "+all"."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer("v=spf1 include:_spf.example.com +all"),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "insecure"

    def test_unrelated_non_ascii_txt_record(self, maildomain_factory):
        """SPF records are US-ASCII (RFC 7208 3.1), but another TXT record at
        the same name may hold anything, and decoding it must not fail the
        whole check."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        binary_rr = MagicMock()
        binary_rr.strings = (b"\xff\xfe some vendor blob",)
        spf_rr = MagicMock()
        spf_rr.strings = (b"v=spf1 include:_spf.example.com -all",)
        answer = MagicMock()
        answer.rrset = [binary_rr, spf_rr]

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": answer,
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_stricter_all_than_expected_is_correct(self, maildomain_factory):
        """A domain hardening ~all into -all is stricter, not insecure."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com ~all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer("v=spf1 include:_spf.example.com -all"),
                    "_spf.example.com": _txt_answer("v=spf1 ip4:1.2.3.4 -all"),
                }
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_qualifiers_in_terms_only_comparison(self, maildomain_factory):
        """With no include to follow, terms are compared directly: an explicit
        "+" qualifier and upper case must not make them differ."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 ip4:1.2.3.4 mx -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=spf1 +MX +ip4:1.2.3.4 -all")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_transient_dns_failure_in_chain_is_reported_as_an_error(
        self, maildomain_factory
    ):
        """A timeout while walking the chain says nothing about the record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.example.com -all")
                raise Timeout()

            mock_resolve.side_effect = resolve_side_effect

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "error"

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_transient_dns_failure_in_chain_is_not_cached(self, maildomain_factory):
        """That error must not be cached as a definitive failure for 10 minutes."""
        cache.clear()
        maildomain = maildomain_factory(name="example.com")

        with (
            patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve,
            patch("core.services.dns.check.cache.set") as mock_cache_set,
        ):

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                raise Timeout()

            mock_resolve.side_effect = resolve_side_effect

            assert check_spf_status(maildomain) is False
            mock_cache_set.assert_not_called()

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_unrelated_transient_failure_stays_definitive(self, maildomain_factory):
        """A timeout on someone else's include, when ours was looked up and
        settled, is not what hid it: the answer is definitive and cacheable."""
        cache.clear()
        maildomain = maildomain_factory(name="example.com")

        with (
            patch("core.services.dns.check.dns.resolver.resolve") as mock_resolve,
            patch("core.services.dns.check.cache.set") as mock_cache_set,
        ):

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org"
                        " include:other.example.net -all"
                    )
                if name == "_spf.messages.org":
                    raise NXDOMAIN()
                raise Timeout()

            mock_resolve.side_effect = resolve_side_effect

            assert check_spf_status(maildomain) is False
            mock_cache_set.assert_called_once()


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

    def test_spf_weak_all_does_not_excuse_a_missing_include(
        self, maildomain_factory, settings
    ):
        """A weak "all" says the policy is lax; it says nothing about whether
        the domain delegates to us. Reporting "insecure" here would let
        check_spf_status send for a domain that never included us."""
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
                    return _txt_answer("v=spf1 ?all include:other.example.net")
                if name == "other.example.net":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_include_after_all_is_never_reached(self, maildomain_factory, settings):
        """RFC 7208 5.1: "all" always matches, so mechanisms listed after it
        MUST be ignored. An include placed after it delegates nothing, and the
        record hard-fails every sender."""
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
                    return _txt_answer("v=spf1 -all include:_spf.messages.org")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_unknown_mechanism_is_a_syntax_error(
        self, maildomain_factory, settings
    ):
        """RFC 7208 4.6: a syntax error anywhere in the record makes receivers
        return permerror, so the delegation never takes effect."""
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
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org gibberish -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_invalid_record_at_the_include_target(
        self, maildomain_factory, settings
    ):
        """RFC 7208 5.2: a recursive check returning permerror makes the
        include return permerror, so a malformed record at the target breaks
        the chain rather than completing it."""
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
                    return _txt_answer("v=spf1 ip4:999.1.1.1 gibberish -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"

    def test_spf_void_lookups_are_capped(self, maildomain_factory, settings):
        """RFC 7208 4.6.4 and 11.1: a record listing names that do not exist
        turns a verifier into a DNS amplifier aimed at them, so the walk stops
        after two void lookups instead of spending the full budget of ten."""
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
                    return _txt_answer(
                        "v=spf1 "
                        + " ".join(f"include:v{i}.victim.example" for i in range(9))
                        + " include:_spf.messages.org -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
            # One lookup for the domain itself, then three void ones: the
            # third trips the cap. Without it this would have been ten.
            assert mock_resolve.call_count == 4

    def test_spf_void_lookups_do_not_mask_an_include_reached_first(
        self, maildomain_factory, settings
    ):
        """The cap must not hide a delegation that already resolved."""
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
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org "
                        + " ".join(f"include:v{i}.example.net" for i in range(9))
                        + " -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_spf_name_answering_without_a_record_is_not_a_void_lookup(
        self, maildomain_factory, settings
    ):
        """A void lookup is one that came back empty. A name that answers with
        TXT records but no SPF among them did answer."""
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
                    return _txt_answer(
                        "v=spf1 "
                        + " ".join(f"include:n{i}.example.net" for i in range(5))
                        + " include:_spf.messages.org -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                return _txt_answer("google-site-verification=abc123")

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_spf_redirect_is_ignored_when_all_is_present(
        self, maildomain_factory, settings
    ):
        """RFC 7208 5.1 and 6.1: a "redirect=" modifier MUST be ignored when the
        record has an "all" mechanism anywhere, so it delegates nothing and our
        include is not actually in place."""
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
                    return _txt_answer("v=spf1 redirect=policy.example.net -all")
                if name == "policy.example.net":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise NXDOMAIN()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"


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
