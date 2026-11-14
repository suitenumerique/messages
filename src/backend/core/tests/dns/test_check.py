"""
Tests for DNS checking functionality.
"""
# pylint: disable=too-many-lines

import json
import time
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import override_settings

import pytest

from core.models import MailDomain
from core.services.dns.check import (
    _resolve_spf_includes,
    check_dns_records,
    check_single_record,
    check_spf_status,
    invalidate_spf_check_cache,
)
from core.services.dns.resolver import (
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
)
from core.tests.dns_answers import answer as _record_answer
from core.tests.dns_answers import mx_answer, txt_answer_chunked, txt_answer_raw
from core.tests.dns_answers import txt_answer as _txt_answer


def _nxdomain(name="example.com", rdtype="TXT"):
    return NXDOMAINError(name, rdtype)


def _timeout(name="example.com", rdtype="TXT"):
    return ResolutionTimeoutError(name, rdtype)


@pytest.mark.django_db
class TestDNSChecking:  # pylint: disable=too-many-public-methods
    """Test DNS checking functionality."""

    def test_check_single_record_mx_correct(self, maildomain_factory):
        """Test checking a correct MX record."""
        maildomain = maildomain_factory(name="example.com")
        # Fully qualified, with the trailing dot, exactly as the shipped
        # MESSAGES_DNS_RECORDS default writes it — an MX exchange renders
        # absolute, so a dotless expected value never matches.
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com."}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = mx_answer((10, "mx1.example.com."))

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["10 mx1.example.com."]

    def test_check_single_record_mx_incorrect(self, maildomain_factory):
        """Test checking an incorrect MX record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com."}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = mx_answer((20, "mx2.example.com."))

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
            assert result["found"] == ["20 mx2.example.com."]

    def test_check_single_record_txt_correct(self, maildomain_factory):
        """Test checking a correct TXT record."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "@",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock missing record
            mock_resolve.side_effect = Exception("No records found")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "No records found" in result["error"]

    def test_check_single_record_nxdomain(self, maildomain_factory):
        """Test checking a record when domain doesn't exist."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock NXDOMAIN
            mock_resolve.side_effect = _nxdomain()

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "Domain not found"

    def test_check_single_record_no_answer(self, maildomain_factory):
        """Test checking a record when no answer is returned."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock NoAnswer
            mock_resolve.side_effect = NoAnswerError("example.com", "TXT")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "missing"
            assert result["error"] == "No records found"

    def test_check_single_record_servfail_is_transient(self, maildomain_factory):
        """A SERVFAIL is a failed lookup, not an absent record.

        "missing" would be cached by ``check_spf_status`` as a definitive
        negative for 10 minutes; only "error" is treated as transient.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = ServfailError("example.com", "TXT")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"

    def test_check_single_record_timeout(self, maildomain_factory):
        """Test checking a record when DNS query times out."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock Timeout
            mock_resolve.side_effect = ResolutionTimeoutError("example.com", "TXT")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "DNS query timeout"

    def test_check_single_record_invalid_name(self, maildomain_factory):
        """Test checking a record the resolver refuses to query at all."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = InvalidNameError("example.com", "name too long")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert result["error"] == "Invalid domain name"

    def test_check_single_record_generic_exception(self, maildomain_factory):
        """Test checking a record when a generic exception occurs."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock generic exception
            mock_resolve.side_effect = Exception("Network error")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "error"
            assert "DNS query failed: Network error" in result["error"]

    def test_check_single_record_mx_correct_format(self, maildomain_factory):
        """Test that MX records are formatted correctly in results."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com."}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = mx_answer((10, "mx1.example.com."))

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"
            assert result["found"] == ["10 mx1.example.com."]

    def test_check_single_record_mx_incorrect_format(self, maildomain_factory):
        """Test that MX records with wrong format are detected as incorrect."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "MX", "target": "@", "value": "10 mx1.example.com."}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = mx_answer((20, "mx1.example.com."))

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
            assert result["found"] == ["20 mx1.example.com."]

    def test_check_dns_records_multiple_records(self, maildomain_factory):
        """Test checking multiple DNS records."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
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

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:

                def resolve_side_effect(name, record_type):
                    if name == "_dmarc_missing.example.com":
                        raise NoAnswerError("example.com", "TXT")

                    if record_type == "MX":
                        return mx_answer((10, "mx1.example.com."))

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

                    raise NoAnswerError(name, record_type)

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
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
                {
                    "type": "TXT",
                    "target": "@",
                    "value": "v=spf1 include:_spf.example.com -all",
                },
                {"type": "A", "target": "@", "value": "192.168.1.1"},
            ]

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:
                # Mock responses: correct MX, no SPF found, missing A
                mock_resolve.side_effect = [
                    mx_answer((10, "mx1.example.com.")),  # Correct MX
                    _txt_answer("some-unrelated-record"),  # No SPF record
                    NoAnswerError("example.com", "A"),  # Missing A record
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Also has a non-SPF TXT record
            mock_resolve.return_value = _txt_answer(
                "v=spf1 include:_spf.example.com -all",
                "google-site-verification=abc123",
            )

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_single_record_spf_split_across_character_strings(
        self, maildomain_factory
    ):
        """One record split into several character-strings is one value.

        RFC 7208 §3.3 requires the chunks be joined with no separator.
        Emitting each string as its own value only makes sense against a local
        stub resolver merging distinct TXT records into one RR; querying
        authoritative servers directly there is no such merging.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = txt_answer_chunked(
                ["v=spf1 include:_spf.exa", "mple.com -all"]
            )

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_check_single_record_separate_txt_records_stay_separate(
        self, maildomain_factory
    ):
        """Distinct TXT records are distinct values, never concatenated."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = txt_answer_chunked(
                ["google-site-verification=abc123"],
                ["v=spf1 include:_spf.example.com -all"],
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DMARC1;p=none")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    def test_check_single_record_dmarc_sp_none_leaves_subdomains_open(
        self, maildomain_factory
    ):
        """ "sp=none" is weaker: subdomains otherwise inherit "p" (RFC 7489 6.3).

        Reported as insecure for the *subdomain* policy, not because "sp=none"
        was read as the domain's own "p=none" — ``dmarc_policy`` returning
        "reject" for this record is covered in ``test_records``.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=DMARC1;p=reject;adkim=s;aspf=s;sp=none"
            )

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"

    def test_check_single_record_dmarc_expected_sp_none_still_checks_p(
        self, maildomain_factory
    ):
        """An "sp=none" in the expected record must not switch the check off.

        A substring guard on ``"p=none"`` is satisfied by an
        operator-configured ``sp=none``, which silently accepts any published
        policy.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;sp=none",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DMARC1;p=none")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"

    def test_check_single_record_dmarc_stronger_policy_is_correct(
        self, maildomain_factory
    ):
        """Publishing a stronger policy than expected is not a mismatch."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=quarantine",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=DMARC1;p=reject")

            result = check_single_record(maildomain, expected_record)

            assert result["status"] != "insecure"

    def test_check_single_record_dmarc_lookalike_is_not_a_dmarc_record(
        self, maildomain_factory
    ):
        """ "v=DMARC1000" is not a DMARC record (RFC 7489 6.4 needs a ";").

        ``startswith("v=DMARC1")`` counts it, making an unrelated TXT record
        at _dmarc read as the domain publishing duplicates.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer(
                "v=DMARC1;p=reject", "v=DMARC1000;x=1"
            )

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "correct"

    @pytest.mark.parametrize("found", ["V=DMARC1;p=none", "v = DMARC1;p=none"])
    def test_check_single_record_dmarc_version_case_and_wsp(
        self, maildomain_factory, found
    ):
        """RFC 7489 6.4 spells the version as "v" *WSP "=" *WSP %x44...

        The "v" is an ABNF quoted literal and so case-insensitive, and *WSP is
        allowed around the "=". Both of these are DMARC records that a
        ``startswith("v=DMARC1")`` test skipped entirely, hiding a weak policy.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer(found)

            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "insecure"

    @pytest.mark.parametrize(
        "published",
        [
            # Adding a reporting address is what every deployment guide tells
            # an operator to do.
            "v=DMARC1;p=reject;adkim=s;aspf=s;rua=mailto:d@example.com",
            # Tag order carries no meaning past the version (RFC 7489 6.4).
            "v=DMARC1;aspf=s;adkim=s;p=reject",
            # Strictly stronger than what we asked for.
            "v=DMARC1;p=reject;adkim=s;aspf=s;sp=reject",
            # A trailing ";" is idiomatic and every parser accepts it.
            "v=DMARC1;p=reject;adkim=s;aspf=s;",
        ],
    )
    def test_dmarc_is_judged_semantically_not_by_spelling(
        self, maildomain_factory, published
    ):
        """DKIM and SPF are compared semantically; DMARC has to be too.

        Compared as an exact string, each of these reads as "incorrect" while
        being correct or better, sending the operator to fix a working record.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer(published)

            assert (
                check_single_record(maildomain, expected_record)["status"] == "correct"
            )

    @pytest.mark.parametrize(
        "published,status",
        [
            # Relaxed alignment is the default, so omitting the tags asks for
            # less than the "adkim=s;aspf=s" we publish: weaker, not broken.
            ("v=DMARC1;p=reject", "insecure"),
            ("v=DMARC1;p=none;adkim=s;aspf=s", "insecure"),
            # Unparseable: receivers ignore the record (RFC 7489 6.6.3), so it
            # enforces nothing at all — "incorrect", not merely weaker.
            ("v=DMARC1;p=reject;adkim=s;aspf=s;!!!", "incorrect"),
            ("v=DMARC1;p=banish;adkim=s;aspf=s", "incorrect"),
            ("v=DMARC1;p=reject;adkim=x;aspf=s", "incorrect"),
        ],
    )
    def test_dmarc_weaker_is_insecure_and_malformed_is_incorrect(
        self, maildomain_factory, published, status
    ):
        """The two failures are different and must not be reported alike."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "_dmarc",
            "value": "v=DMARC1;p=reject;adkim=s;aspf=s",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer(published)

            assert check_single_record(maildomain, expected_record)["status"] == status

    def test_check_dns_records_conflicting_mx(self, maildomain_factory):
        """Test that extra MX records from other providers are detected as conflicting."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
            ]

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:
                # Return our expected MX plus an extra one from another provider
                mock_resolve.return_value = mx_answer(
                    (10, "mx1.example.com."), (20, "mx.otherprovider.com.")
                )

                results = check_dns_records(maildomain)

                assert len(results) == 1
                assert results[0]["_check"]["status"] == "conflicting"
                assert "10 mx1.example.com." in results[0]["_check"]["found"]
                assert "20 mx.otherprovider.com." in results[0]["_check"]["found"]

    def test_check_dns_records_mx_correct_no_extra(self, maildomain_factory):
        """Test that MX records without extra entries stay correct."""
        maildomain = maildomain_factory(name="example.com")

        with patch.object(maildomain, "get_expected_dns_records") as mock_get_records:
            mock_get_records.return_value = [
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
            ]

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:
                mock_resolve.return_value = mx_answer((10, "mx1.example.com."))

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
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
                {"type": "MX", "target": "@", "value": "20 mx2.example.com."},
            ]

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:
                # Both expected MX records present plus an extra one
                mock_resolve.return_value = mx_answer(
                    (10, "mx1.example.com."),
                    (20, "mx2.example.com."),
                    (30, "mx.legacy.com."),
                )

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
                {"type": "MX", "target": "@", "value": "10 mx1.example.com."},
            ]

            with patch("core.services.dns.check.resolve_answer") as mock_resolve:
                # Only a foreign MX, our expected one is absent
                mock_resolve.return_value = mx_answer((20, "mx.otherprovider.com."))

                results = check_dns_records(maildomain)

                assert len(results) == 1
                # Should be incorrect, not conflicting (our MX is not present)
                assert results[0]["_check"]["status"] == "incorrect"

    def test_check_single_record_with_subdomain(self, maildomain_factory):
        """Test checking a record for a subdomain."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {"type": "A", "target": "www", "value": "192.168.1.1"}

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Mock correct A record for subdomain
            mock_resolve.return_value = _record_answer(
                "www.example.com", "A", "192.168.1.1"
            )

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # Simulate DNS returning a split TXT record with extra t=s tag
            mock_resolve.return_value = txt_answer_chunked(
                [
                    "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBA",
                    "QUAA4GNADCBiQKBgQC; t=s",
                ]
            )

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = txt_answer_chunked(
                [
                    "v=DKIM1; t=y; p=MIGfMA0GCSqGSIb3DQEBA",
                    "QUAA4GNADCBiQKBgQC; k=rsa",
                ]
            )

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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
                raise _nxdomain(name)
            return records[name]

        return resolve_side_effect

    def test_explicit_plus_qualifier_on_include(self, maildomain_factory):
        """RFC 7208 4.6.1: every mechanism takes an optional qualifier, so
        "+include:" delegates exactly like "include:".

        Records of this shape are published in the wild, and flagging them
        as incorrect stops us sending for those domains.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        split_answer = txt_answer_chunked(
            [
                "v=spf1 include:a.example.net include:b.example.net ",
                "include:_spf.example.com -all",
            ]
        )

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": split_answer,
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

    def test_broken_third_party_include_listed_first_does_not_hide_ours(
        self, maildomain_factory
    ):
        """Same record, with the broken third party listed before our include.

        Where a third party sits in the record must not decide whether we find
        the include we asked for. The walk goes past its duplicate the way it
        goes past a malformed record, instead of stopping on it.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": _txt_answer(
                        "v=spf1 include:broken.example.net include:_spf.example.com -all"
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        binary_answer = txt_answer_raw(
            [b"\xff\xfe some vendor blob"],
            [b"v=spf1 include:_spf.example.com -all"],
        )

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = self._resolver(
                {
                    "example.com": binary_answer,
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.return_value = _txt_answer("v=spf1 +MX +ip4:1.2.3.4 -all")

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

    def test_unformable_include_name_is_settled_not_transient(self, maildomain_factory):
        """An include no query can be sent for is a dead end, not a blip.

        Grouping it with the transient failures makes the result permanently
        non-definitive: "error" is never cached, so every outbound message
        re-walks the whole chain, and the operator is told DNS failed instead
        of being pointed at the malformed include.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:..broken.example -all")
                raise InvalidNameError(name, "TXT")

            mock_resolve.side_effect = resolve_side_effect

            result = check_single_record(maildomain, expected_record)
            # Definitive: the published record does not carry our include.
            assert result["status"] == "incorrect"

    def test_our_own_bug_while_walking_is_not_blamed_on_the_customer(
        self, maildomain_factory
    ):
        """An unexpected exception must not read as "no SPF record there".

        Left out of both resolved and transient the domain reads as settled,
        so the check returns a definitive "incorrect" that is cached for ten
        minutes and puts every external recipient in the retry ladder.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.example.com -all")
                raise RuntimeError("bug in our own code")

            mock_resolve.side_effect = resolve_side_effect

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "error"

    def test_chain_duplicate_names_the_domain_that_has_them(self, maildomain_factory):
        """The customer's apex holds one correct record; say whose is doubled."""
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:third-party.example -all")
                return _txt_answer("v=spf1 -all", "v=spf1 ip4:1.2.3.4 -all")

            mock_resolve.side_effect = resolve_side_effect

            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "duplicate"
            assert "third-party.example" in result["error"]
            # The apex itself is fine, and is what "found" holds.
            assert result["found"] == ["v=spf1 include:third-party.example -all"]

    def test_whole_check_budget_stops_the_walk(self, maildomain_factory):
        """A chain of slow includes must not outlive the request timeout.

        Each resolution is bounded on its own; their sum is not, and this runs
        inside a synchronous endpoint whose worker is killed at 90s.
        """
        maildomain = maildomain_factory(name="example.com")
        expected_record = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf.example.com -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:a.example -all")
                # Every hop delegates onwards, so the walk only ends on a cap.
                return _txt_answer(f"v=spf1 include:next-{name} -all")

            mock_resolve.side_effect = resolve_side_effect

            # A deadline already in the past: no include is ever walked.
            result = check_single_record(
                maildomain, expected_record, deadline=time.monotonic() - 1
            )

            assert result["status"] == "error"
            assert "budget" in result["error"]

    def test_budget_exhausted_mid_walk_is_not_definitive(self, maildomain_factory):
        """Domains left unwalked are unexplored, not absent."""
        resolved, visited, transient, error = _resolve_spf_includes(
            ["v=spf1 include:a.example include:b.example -all"],
            deadline=time.monotonic() - 1,
        )

        assert error == "deadline_exceeded"
        assert resolved == set()
        # Both queued domains are marked unexplored, so _check_spf reports an
        # error rather than blaming the customer's record.
        assert transient == {"a.example", "b.example"}
        assert visited == set()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.example.com -all")
                raise _timeout(name)

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
            patch("core.services.dns.check.resolve_answer") as mock_resolve,
            patch("core.services.dns.check.cache.set") as mock_cache_set,
        ):

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                raise _timeout(name)

            mock_resolve.side_effect = resolve_side_effect

            assert check_spf_status(maildomain) is False
            mock_cache_set.assert_not_called()

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_servfail_on_the_domain_itself_is_not_cached(self, maildomain_factory):
        """A SERVFAIL looking up the customer's own record is not definitive.

        Cached, it would keep every external recipient in the retry ladder for
        the full 10 minutes on one bad answer from an authoritative server.
        """
        cache.clear()
        maildomain = maildomain_factory(name="example.com")

        with (
            patch("core.services.dns.check.resolve_answer") as mock_resolve,
            patch("core.services.dns.check.cache.set") as mock_cache_set,
        ):
            mock_resolve.side_effect = ServfailError("example.com", "TXT")

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
            patch("core.services.dns.check.resolve_answer") as mock_resolve,
            patch("core.services.dns.check.cache.set") as mock_cache_set,
        ):

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org"
                        " include:other.example.net -all"
                    )
                if name == "_spf.messages.org":
                    raise _nxdomain(name)
                raise _timeout(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.other.com -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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
                raise _nxdomain()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)
            assert result["status"] == "correct"

        # Chain of 11: needs 11 lookups, should hit the limit.
        expected_record_11 = {
            "type": "TXT",
            "target": "",
            "value": "v=spf1 include:_spf11.messages.org -all",
        }

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                # Include target fails to resolve
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    # Two SPF records — customer duplicated the record
                    return _txt_answer(
                        "v=spf1 ip4:1.2.3.4 -all",
                        "v=spf1 ip4:5.6.7.8 -all",
                    )
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 ?all include:other.example.net")
                if name == "other.example.net":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 -all include:_spf.messages.org")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org gibberish -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:999.1.1.1 gibberish -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 "
                        + " ".join(f"include:v{i}.victim.example" for i in range(9))
                        + " include:_spf.messages.org -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer(
                        "v=spf1 include:_spf.messages.org "
                        + " ".join(f"include:v{i}.example.net" for i in range(9))
                        + " -all"
                    )
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 redirect=policy.example.net -all")
                if name == "policy.example.net":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain(name)

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            mock_resolve.side_effect = _nxdomain()
            assert check_spf_status(maildomain) is False

    @override_settings(
        MESSAGES_TECHNICAL_DOMAIN="messages.org",
        MESSAGES_DNS_RECORDS='[{"target":"","type":"txt",'
        '"value":"v=spf1 include:_spf.messages.org -all"}]',
    )
    def test_returns_false_when_include_not_found(self, maildomain_factory):
        """SPF exists but include target doesn't resolve returns False."""
        maildomain = maildomain_factory(name="example.com")

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # First call: DNS timeout (transient error)
            mock_resolve.side_effect = ResolutionTimeoutError("example.com", "TXT")
            assert check_spf_status(maildomain) is False

            # Second call: DNS works now — should NOT use cache
            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    return _txt_answer("v=spf1 ip4:1.2.3.4 -all")
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
            # NXDOMAIN is a definitive failure (status=missing, not error)
            mock_resolve.side_effect = _nxdomain()
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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
                raise _nxdomain()

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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:
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

        with patch("core.services.dns.check.resolve_answer") as mock_resolve:

            def resolve_side_effect(name, _record_type):
                if name == "example.com":
                    return _txt_answer("v=spf1 include:_spf.messages.org -all")
                if name == "_spf.messages.org":
                    # TXT record exists but is not SPF
                    return _txt_answer("not an spf record")
                raise _nxdomain()

            mock_resolve.side_effect = resolve_side_effect
            result = check_single_record(maildomain, expected_record)

            assert result["status"] == "incorrect"
