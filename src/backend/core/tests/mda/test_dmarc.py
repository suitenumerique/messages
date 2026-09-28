"""DMARC record discovery (RFC 7489 §6.6.3).

Only ``adkim`` is read today, and only to decide how strictly a DKIM ``d=``
must match the From domain, so every case here is about which record we end
up reading — not about enforcing a policy.
"""

from unittest.mock import patch

import pytest

from core.mda.dmarc import _policy_domains, dkim_alignment_mode
from core.services.dns.resolver import (
    DNSSECValidationError,
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
)

STRICT = "v=DMARC1; p=reject; adkim=s"
RELAXED = "v=DMARC1; p=reject; adkim=r"
NO_ADKIM = "v=DMARC1; p=reject"


def _lookup(answers):
    """Stand in for ``resolve_txt_values``, driven by a {qname: result} map.

    A list value is returned; an exception instance is raised. A name absent
    from the map raises ``NXDOMAINError``, so a test only has to spell out
    the names it cares about — and a query it did not expect reads as "no
    record" rather than silently succeeding.
    """

    def _resolve(qname):
        result = answers.get(qname, NXDOMAINError(qname, "TXT"))
        if isinstance(result, Exception):
            raise result
        return result

    return _resolve


class TestPolicyDomains:
    """Which names get queried, before any of them are."""

    def test_a_registrable_domain_is_queried_once(self):
        """``example.com`` is its own organizational domain: no second query."""
        assert _policy_domains("example.com") == ["example.com"]

    def test_a_subdomain_falls_back_to_its_organizational_domain(self):
        assert _policy_domains("mail.example.com") == [
            "mail.example.com",
            "example.com",
        ]

    def test_a_multi_label_suffix_is_not_stripped_one_label_at_a_time(self):
        """``co.uk`` is a public suffix, so the org domain is ``example.co.uk``."""
        assert _policy_domains("mail.example.co.uk") == [
            "mail.example.co.uk",
            "example.co.uk",
        ]

    def test_an_unusable_organizational_domain_is_dropped(self):
        """Never query ``_dmarc.``: a bare public suffix has no registrable part."""
        assert _policy_domains("co.uk") == ["co.uk"]


class TestDkimAlignmentMode:
    """The ``adkim`` tag, or the relaxed default."""

    @pytest.mark.parametrize(
        "record,expected",
        [
            (STRICT, "s"),
            (RELAXED, "r"),
            (NO_ADKIM, "r"),
            ("v=DMARC1; p=none; adkim=S", "s"),  # tag values fold case
            ("v=DMARC1; p=none; adkim=wat", "r"),  # unrecognised -> default
        ],
    )
    def test_reads_the_adkim_tag(self, record, expected):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": [record]}),
        ):
            assert dkim_alignment_mode("example.com") == expected

    def test_no_record_anywhere_is_relaxed(self):
        with patch("core.mda.dmarc.resolve_txt_values", _lookup({})):
            assert dkim_alignment_mode("mail.example.com") == "r"

    def test_a_name_that_answers_with_no_txt_at_all_is_relaxed(self):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": NoAnswerError("_dmarc.example.com", "TXT")}),
        ):
            assert dkim_alignment_mode("example.com") == "r"

    def test_non_dmarc_txt_records_are_ignored(self):
        """``_dmarc`` names carry unrelated TXT as often as any other name."""
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup(
                {
                    "_dmarc.example.com": [
                        "v=spf1 -all",
                        "some-verification=abc123",
                        STRICT,
                    ]
                }
            ),
        ):
            assert dkim_alignment_mode("example.com") == "s"

    def test_only_non_dmarc_txt_is_the_same_as_no_record(self):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": ["some-verification=abc123"]}),
        ):
            assert dkim_alignment_mode("example.com") == "r"


class TestOrganizationalDomainFallback:
    """RFC 7489 §6.6.3: a record at the org domain covers its subdomains."""

    def test_a_subdomain_with_no_record_uses_its_parents(self):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": [STRICT]}),
        ):
            assert dkim_alignment_mode("mail.example.com") == "s"

    def test_the_subdomains_own_record_wins(self):
        """The walk stops at the first name that publishes one."""
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup(
                {
                    "_dmarc.mail.example.com": [RELAXED],
                    "_dmarc.example.com": [STRICT],
                }
            ),
        ):
            assert dkim_alignment_mode("mail.example.com") == "r"

    def test_a_stranger_under_the_same_public_suffix_is_not_consulted(self):
        """``attacker.co.uk`` must not answer for ``victim.co.uk``."""
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.co.uk": [STRICT], "_dmarc.attacker.co.uk": [STRICT]}),
        ):
            assert dkim_alignment_mode("victim.co.uk") == "r"


class TestFailuresFallBackToRelaxed:
    """Nothing short of a clear answer may narrow what counts as aligned."""

    @pytest.mark.parametrize(
        "error",
        [
            ResolutionTimeoutError("_dmarc.example.com", "TXT"),
            ServfailError("_dmarc.example.com", "TXT"),
            DNSSECValidationError("_dmarc.example.com", "TXT", "signature expired"),
        ],
    )
    def test_an_incomplete_lookup_is_relaxed(self, error):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": error}),
        ):
            assert dkim_alignment_mode("example.com") == "r"

    @pytest.mark.parametrize(
        "error",
        [
            ResolutionTimeoutError("_dmarc.mail.example.com", "TXT"),
            ServfailError("_dmarc.mail.example.com", "TXT"),
        ],
    )
    def test_an_incomplete_lookup_does_not_fall_back_to_the_parent(self, error):
        """A timeout is not "no record here", so the parent must not answer.

        Falling through would apply the parent's policy to a subdomain that
        may well publish its own — a record we simply failed to read.
        """
        queried = []

        def _resolve(qname):
            queried.append(qname)
            if qname == "_dmarc.mail.example.com":
                raise error
            return [STRICT]

        with patch("core.mda.dmarc.resolve_txt_values", _resolve):
            assert dkim_alignment_mode("mail.example.com") == "r"
        assert queried == ["_dmarc.mail.example.com"]

    def test_a_malformed_name_is_settled_and_keeps_walking(self):
        """An unusable label is "nothing here", not "we could not look"."""
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup(
                {
                    "_dmarc.mail.example.com": InvalidNameError("." * 300, "TXT"),
                    "_dmarc.example.com": [STRICT],
                }
            ),
        ):
            assert dkim_alignment_mode("mail.example.com") == "s"


class TestAmbiguousRecords:
    """RFC 7489 §6.6.3 step 5: not exactly one record means DMARC does not apply."""

    def test_two_dmarc_records_are_relaxed(self):
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": [STRICT, RELAXED]}),
        ):
            assert dkim_alignment_mode("example.com") == "r"

    def test_two_identical_dmarc_records_are_still_ambiguous(self):
        """Deduplicating would be guessing which one the owner meant to keep."""
        with patch(
            "core.mda.dmarc.resolve_txt_values",
            _lookup({"_dmarc.example.com": [STRICT, STRICT]}),
        ):
            assert dkim_alignment_mode("example.com") == "r"

    def test_ambiguity_does_not_fall_back_to_the_parent(self):
        """The name answered; it just answered unusably."""
        queried = []

        def _resolve(qname):
            queried.append(qname)
            if qname == "_dmarc.mail.example.com":
                return [STRICT, RELAXED]
            return [STRICT]

        with patch("core.mda.dmarc.resolve_txt_values", _resolve):
            assert dkim_alignment_mode("mail.example.com") == "r"
        assert queried == ["_dmarc.mail.example.com"]
