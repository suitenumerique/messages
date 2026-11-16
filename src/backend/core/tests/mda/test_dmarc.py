"""DMARC policy discovery and evaluation (RFC 7489)."""

from unittest.mock import patch

import pytest

from core.mda.dmarc import (
    DMARC_FAIL,
    DMARC_NONE,
    DMARC_PASS,
    DMARC_TEMPERROR,
    DMARCLookupIncomplete,
    DMARCPolicy,
    _policy_domains,
    discover_policy,
    evaluate_dmarc,
    identifiers_aligned,
)
from core.mda.signing import DKIMSignatureResult
from core.mda.spf import SPFCheck, SPFResult
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


def _lookup(answers, queried=None):
    """Stand in for ``dmarc_txt_values``, driven by a {qname: result} map.

    A list value is returned; an exception instance is raised. A name absent
    from the map raises ``NXDOMAINError``.
    """

    def _resolve(qname):
        if queried is not None:
            queried.append(qname)
        result = answers.get(qname, NXDOMAINError(qname, "TXT"))
        if isinstance(result, Exception):
            raise result
        return result

    return _resolve


def _discover(answers, domain="example.com", queried=None):
    with patch("core.mda.dmarc.dmarc_txt_values", _lookup(answers, queried)):
        return discover_policy(domain)


def _policy(record, from_domain="example.com", record_domain="example.com"):
    with patch(
        "core.mda.dmarc.dmarc_txt_values",
        _lookup({f"_dmarc.{record_domain}": [record]}),
    ):
        policy = discover_policy(from_domain)
    assert policy is not None
    return policy


class TestPolicyDomains:
    """Which names get queried, before any of them are."""

    def test_a_registrable_domain_is_queried_once(self):
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


class TestDiscovery:
    """RFC 7489 6.6.3."""

    def test_no_record_anywhere(self):
        assert _discover({}, "mail.example.com") is None

    def test_a_name_that_answers_with_no_txt_at_all(self):
        error = NoAnswerError("_dmarc.example.com", "TXT")
        assert _discover({"_dmarc.example.com": error}) is None

    def test_non_dmarc_txt_records_are_ignored(self):
        """``_dmarc`` names carry unrelated TXT as often as any other name."""
        policy = _discover(
            {"_dmarc.example.com": ["v=spf1 -all", "some-verification=abc", STRICT]}
        )
        assert policy.adkim == "s"

    def test_only_non_dmarc_txt_is_the_same_as_no_record(self):
        assert _discover({"_dmarc.example.com": ["some-verification=abc"]}) is None

    def test_a_subdomain_with_no_record_uses_its_parents(self):
        policy = _discover({"_dmarc.example.com": [STRICT]}, "mail.example.com")
        assert policy.record_domain == "example.com"
        assert policy.is_subdomain_policy

    def test_the_subdomains_own_record_wins(self):
        policy = _discover(
            {"_dmarc.mail.example.com": [RELAXED], "_dmarc.example.com": [STRICT]},
            "mail.example.com",
        )
        assert policy.record_domain == "mail.example.com"
        assert policy.adkim == "r"
        assert not policy.is_subdomain_policy

    def test_a_stranger_under_the_same_public_suffix_is_not_consulted(self):
        """``attacker.co.uk`` must not answer for ``victim.co.uk``."""
        answers = {"_dmarc.co.uk": [STRICT], "_dmarc.attacker.co.uk": [STRICT]}
        assert _discover(answers, "victim.co.uk") is None

    @pytest.mark.parametrize(
        "error",
        [
            ResolutionTimeoutError("_dmarc.example.com", "TXT"),
            ServfailError("_dmarc.example.com", "TXT"),
            DNSSECValidationError("_dmarc.example.com", "TXT", "signature expired"),
        ],
    )
    def test_an_incomplete_lookup_raises(self, error):
        with pytest.raises(DMARCLookupIncomplete):
            _discover({"_dmarc.example.com": error})

    def test_an_incomplete_lookup_does_not_fall_back_to_the_parent(self):
        """A timeout is not "no record here", so the parent must not answer."""
        queried = []
        answers = {
            "_dmarc.mail.example.com": ServfailError("_dmarc.mail.example.com", "TXT"),
            "_dmarc.example.com": [STRICT],
        }
        with pytest.raises(DMARCLookupIncomplete):
            _discover(answers, "mail.example.com", queried)
        assert queried == ["_dmarc.mail.example.com"]

    def test_a_malformed_name_is_settled_and_keeps_walking(self):
        answers = {
            "_dmarc.mail.example.com": InvalidNameError("." * 300, "TXT"),
            "_dmarc.example.com": [STRICT],
        }
        assert _discover(answers, "mail.example.com").adkim == "s"

    def test_two_dmarc_records_disable_dmarc(self):
        """6.6.3 step 5: not exactly one record means DMARC does not apply."""
        assert _discover({"_dmarc.example.com": [STRICT, RELAXED]}) is None

    def test_two_identical_dmarc_records_are_still_ambiguous(self):
        assert _discover({"_dmarc.example.com": [STRICT, STRICT]}) is None

    def test_ambiguity_does_not_fall_back_to_the_parent(self):
        queried = []
        answers = {
            "_dmarc.mail.example.com": [STRICT, RELAXED],
            "_dmarc.example.com": [STRICT],
        }
        assert _discover(answers, "mail.example.com", queried) is None
        assert queried == ["_dmarc.mail.example.com"]


class TestPolicyTags:
    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            (STRICT, "s"),
            (RELAXED, "r"),
            (NO_ADKIM, "r"),
            ("v=DMARC1; p=none; adkim=S", "s"),
            ("v=DMARC1; p=none; adkim=wat", "r"),
        ],
    )
    def test_adkim(self, record, expected):
        assert _policy(record).adkim == expected

    @pytest.mark.parametrize(
        ("record", "expected"),
        [("v=DMARC1; p=reject; aspf=s", "s"), (NO_ADKIM, "r")],
    )
    def test_aspf(self, record, expected):
        assert _policy(record).aspf == expected

    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            ("v=DMARC1; p=reject", "reject"),
            ("v=DMARC1; p=Quarantine", "quarantine"),
            ("v=DMARC1; p=none", "none"),
            ("v=DMARC1; p=bogus", "none"),
            ("v=DMARC1", "none"),
            ("v=DMARC1; p=reject; pct=100", "reject"),
            ("v=DMARC1; p=reject; pct=99", "quarantine"),
            ("v=DMARC1; p=reject; pct=0", "quarantine"),
            ("v=DMARC1; p=quarantine; pct=50", "none"),
            ("v=DMARC1; p=reject; pct=abc", "reject"),
            ("v=DMARC1; p=reject; t=y", "quarantine"),
            ("v=DMARC1; p=reject; t=n", "reject"),
            ("v=DMARC1; p=reject; sp=bogus", "none"),
        ],
    )
    def test_disposition_for_the_domain_itself(self, record, expected):
        assert _policy(record).disposition == expected

    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            ("v=DMARC1; p=reject", "reject"),
            ("v=DMARC1; p=reject; sp=none", "none"),
            ("v=DMARC1; p=none; sp=reject", "reject"),
            # RFC 9989 4.10: an invalid p, sp or np voids the record.
            ("v=DMARC1; p=reject; sp=bogus", "none"),
            ("v=DMARC1; p=reject; np=bogus", "none"),
            ("v=DMARC1; sp=reject", "none"),
            ("v=DMARC1; p=bogus; sp=reject", "none"),
            ("v=DMARC1; p=reject; np=reject", "reject"),
        ],
    )
    def test_disposition_for_a_subdomain(self, record, expected):
        policy = _policy(record, from_domain="mail.example.com")
        assert policy.disposition == expected


class TestAlignment:
    @pytest.mark.parametrize(
        ("identifier", "from_domain", "mode", "expected"),
        [
            ("example.com", "example.com", "s", True),
            ("Example.COM.", "example.com", "s", True),
            ("mail.example.com", "example.com", "s", False),
            ("mail.example.com", "example.com", "r", True),
            ("example.com", "mail.example.com", "r", True),
            ("attacker.com", "example.com", "r", False),
            ("attacker.co.uk", "example.co.uk", "r", False),
            ("attacker.github.io", "victim.github.io", "r", False),
        ],
    )
    def test_identifiers_aligned(self, identifier, from_domain, mode, expected):
        assert identifiers_aligned(identifier, from_domain, mode) is expected


class TestEvaluate:
    """RFC 7489 4.2 and 6.6.2."""

    SPF_FAIL = SPFResult("fail", "example.com", "mailfrom")

    def _evaluate(self, record, dkim=(), spf=SPF_FAIL, from_domain="example.com"):
        answers = {"_dmarc.example.com": [record]} if record else {}
        check = SPFCheck(spf.domain, lambda: spf) if spf else None
        with patch("core.mda.dmarc.dmarc_txt_values", _lookup(answers)):
            return evaluate_dmarc(from_domain, list(dkim), check)

    def test_spf_not_evaluated_is_undecided(self):
        """It could have passed DMARC on its own: never a decided fail."""
        assert self._evaluate(NO_ADKIM, spf=None).result == DMARC_TEMPERROR

    def test_aligned_spf_temperror_is_undecided(self):
        spf = SPFResult("temperror", "example.com", "mailfrom")
        assert self._evaluate(NO_ADKIM, spf=spf).result == DMARC_TEMPERROR

    def test_aligned_spf_permerror_is_a_failure(self):
        """RFC 7489 6.6.2: only a temporary error leaves DMARC undecided."""
        spf = SPFResult("permerror", "example.com", "mailfrom")
        assert self._evaluate(NO_ADKIM, spf=spf).result == DMARC_FAIL

    def test_unaligned_spf_error_does_not_save_a_failure(self):
        spf = SPFResult("temperror", "esp.example.net", "mailfrom")
        assert self._evaluate(NO_ADKIM, spf=spf).result == DMARC_FAIL

    def test_no_policy(self):
        assert self._evaluate(None).result == DMARC_NONE

    def test_policy_lookup_incomplete(self):
        error = ServfailError("_dmarc.example.com", "TXT")
        with patch(
            "core.mda.dmarc.dmarc_txt_values",
            _lookup({"_dmarc.example.com": error}),
        ):
            assert evaluate_dmarc("example.com", [], None).result == DMARC_TEMPERROR

    def test_aligned_dkim_passes(self):
        result = self._evaluate(
            NO_ADKIM, dkim=[DKIMSignatureResult("mail.example.com", "pass")]
        )
        assert result.result == DMARC_PASS
        assert result.dkim_domain == "mail.example.com"

    def test_aligned_spf_passes(self):
        spf = SPFResult("pass", "bounce.example.com", "mailfrom")
        result = self._evaluate(NO_ADKIM, spf=spf)
        assert result.result == DMARC_PASS
        assert result.spf_aligned

    def test_spf_without_domain_is_ignored(self):
        spf = SPFResult("pass", None, "helo")
        assert self._evaluate(NO_ADKIM, spf=spf).result == DMARC_FAIL

    def test_fail_carries_the_disposition(self):
        result = self._evaluate("v=DMARC1; p=quarantine")
        assert result.result == DMARC_FAIL
        assert result.disposition == "quarantine"

    def test_pass_has_no_disposition(self):
        result = self._evaluate(
            NO_ADKIM, dkim=[DKIMSignatureResult("example.com", "pass")]
        )
        assert result.disposition is None

    def test_aligned_temperror_is_temperror(self):
        result = self._evaluate(
            NO_ADKIM, dkim=[DKIMSignatureResult("example.com", "temperror")]
        )
        assert result.result == DMARC_TEMPERROR

    def test_temperror_does_not_hide_a_later_pass(self):
        result = self._evaluate(
            NO_ADKIM,
            dkim=[
                DKIMSignatureResult("example.com", "temperror"),
                DKIMSignatureResult("example.com", "pass"),
            ],
        )
        assert result.result == DMARC_PASS

    def test_signature_without_domain_is_ignored(self):
        result = self._evaluate(NO_ADKIM, dkim=[DKIMSignatureResult(None, "pass")])
        assert result.result == DMARC_FAIL


def test_policy_is_a_plain_value():
    """Frozen: shared across the pipeline without defensive copies."""
    policy = DMARCPolicy("example.com", "example.com", {"p": "reject"})
    with pytest.raises(AttributeError):
        policy.tags = {}
