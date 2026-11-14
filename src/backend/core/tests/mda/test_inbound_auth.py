"""Tests for inbound sender authentication checks (DKIM / DMARC)."""

# pylint: disable=missing-function-docstring,too-many-public-methods

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

import pytest
from jmap_email import parse_email

from core import enums, factories, models
from core.mda.inbound_auth import (
    VERDICT_FORGED,
    VERDICT_UNVERIFIED,
    check_inbound_authentication,
)
from core.mda.inbound_pipeline import DEFERRAL_MAX_AGE, Decision, _make_arc_step
from core.mda.inbound_tasks import process_inbound_message_task
from core.mda.signing import DKIMSignatureResult
from core.mda.spf import SPFResult
from core.services.dns.resolver import NXDOMAINError, ServfailError
from core.tests.dns_answers import txt_answer

RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: rcpt@example.com\r\n"
    b"Subject: Test\r\n"
    b"Message-ID: <abc@example.com>\r\n"
    b"\r\n"
    b"Body\r\n"
)


MTA_ENVELOPE = {
    "origin": "mta",
    "mail_from": "bounce@example.com",
    "ip": "192.0.2.1",
    "helo": "mx.example.com",
    "hostname": "mx.example.com",
    "rcpt_to": "rcpt@example.com",
}


def _queue_inbound(mailbox, content=RAW_EMAIL):
    """Create a queued InboundMessage backed by a blob (blob-at-ingest)."""
    blob = models.Blob.objects.create_blob(
        content=content, content_type="message/rfc822"
    )
    return models.InboundMessage.objects.create(mailbox=mailbox, blob=blob)


class TestCheckInboundAuthenticationDisabled:
    """When inbound_auth is absent or empty the check is a no-op."""

    def test_missing_key_returns_none(self):
        assert check_inbound_authentication(RAW_EMAIL, {}, {}) is None

    def test_none_value_returns_none(self):
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": None}) is None
        )

    def test_empty_string_returns_none(self):
        assert check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": ""}) is None

    def test_unknown_mode_returns_none(self):
        """An unrecognised mode is treated as disabled (with a log warning)."""
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": "wat"}) is None
        )


def _sig(domain, result="pass"):
    return DKIMSignatureResult(domain, result)


class _NativeBase:
    """Native mode, with DKIM, SPF and the DMARC record injected per test.

    ``dkim`` is what ``verify_dkim_signatures`` returns, ``spf`` what
    ``evaluate_spf`` returns, ``dmarc`` maps ``_dmarc`` names to records
    (anything else is NXDOMAIN). ``queried`` records the names asked.
    """

    CONFIG = {"inbound_auth": "native"}
    PARSED_FROM = {"from": [{"email": "sender@example.com"}]}
    MTA_ENVELOPE = {
        "origin": "mta",
        "ip": "192.0.2.1",
        "mail_from": "bounce@example.com",
        "helo": "mx.example.com",
    }

    @pytest.fixture(autouse=True)
    def _seams(self):
        self.dkim = []
        # Baseline: SPF evaluated and failed, so DKIM and the policy decide.
        self.spf = SPFResult("fail", "example.com", "mailfrom")
        self.dmarc = {}
        self.queried = []

        def _dmarc_txt(qname):
            self.queried.append(qname)
            if qname in self.dmarc:
                record = self.dmarc[qname]
                if isinstance(record, Exception):
                    raise record
                return [record]
            raise NXDOMAINError(qname, "TXT")

        with (
            patch(
                "core.mda.inbound_auth.verify_dkim_signatures",
                side_effect=lambda raw, **kwargs: self.dkim,
            ),
            patch(
                "core.mda.inbound_auth.evaluate_spf",
                side_effect=lambda *args: self.spf,
            ) as self.mock_spf,
            patch("core.mda.dmarc.dmarc_txt_values", side_effect=_dmarc_txt),
        ):
            yield

    _UNSET = object()

    def verdict(self, parsed=None, config=None, envelope=_UNSET, arc=None):
        return check_inbound_authentication(
            RAW_EMAIL,
            self.PARSED_FROM if parsed is None else parsed,
            self.CONFIG if config is None else config,
            arc=arc,
            envelope=self.MTA_ENVELOPE if envelope is self._UNSET else envelope,
        )


class TestNativeDkimAlignment(_NativeBase):
    """Without a DMARC record, an aligned passing signature decides."""

    def test_dkim_pass_aligned(self):
        self.dkim = [_sig("example.com")]
        assert self.verdict() is None

    def test_dkim_pass_unaligned_unverified(self):
        """From: example.com signed d=attacker.com: the spoofing signature."""
        self.dkim = [_sig("attacker.com")]
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_subdomain_signing_for_its_parent_is_aligned(self):
        """Relaxed alignment, DMARC's default (RFC 7489 3.1.1)."""
        self.dkim = [_sig("mail.example.com")]
        assert self.verdict() is None

    def test_parent_signing_for_its_subdomain_is_aligned(self):
        self.dkim = [_sig("example.com")]
        assert self.verdict({"from": [{"email": "sender@mail.example.com"}]}) is None

    def test_multi_label_public_suffix_does_not_align_strangers(self):
        """``co.uk`` is a public suffix: stripping one label would merge orgs."""
        self.dkim = [_sig("attacker.co.uk")]
        parsed = {"from": [{"email": "sender@example.co.uk"}]}
        assert self.verdict(parsed) == VERDICT_UNVERIFIED

    def test_private_suffix_does_not_align_strangers(self):
        """The PSL's private section counts too (``github.io``)."""
        self.dkim = [_sig("attacker.github.io")]
        parsed = {"from": [{"email": "sender@victim.github.io"}]}
        assert self.verdict(parsed) == VERDICT_UNVERIFIED

    def test_alignment_is_case_insensitive(self):
        self.dkim = [_sig("example.com")]
        assert self.verdict({"from": [{"email": "Sender@Example.COM"}]}) is None

    def test_several_from_domains_unverified(self):
        """RFC 7489 6.6.1: one aligned domain says nothing about the other."""
        self.dkim = [_sig("evil.test")]
        parsed = {
            "from": [{"email": "attacker@evil.test"}, {"email": "ceo@example.com"}]
        }
        assert self.verdict(parsed) == VERDICT_UNVERIFIED

    def test_several_from_addresses_on_one_domain_still_verify(self):
        self.dkim = [_sig("example.com")]
        parsed = {"from": [{"email": "a@example.com"}, {"email": "b@Example.com"}]}
        assert self.verdict(parsed) is None

    def test_no_from_header_unverified(self):
        self.dkim = [_sig("example.com")]
        assert self.verdict({}) == VERDICT_UNVERIFIED

    @pytest.mark.parametrize("result", ["fail", "permerror", "temperror"])
    def test_non_passing_signature_unverified(self, result):
        self.dkim = [_sig("example.com", result)]
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_unsigned_unverified(self):
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_any_aligned_signature_counts(self):
        """An ESP signature first must not hide the author domain's own."""
        self.dkim = [_sig("esp.example.net"), _sig("example.com")]
        assert self.verdict() is None

    def test_plaintext_dmarc_header_ignored(self):
        """Native never reads a sender-supplied Authentication-Results."""
        self.dkim = [_sig("example.com")]
        parsed = {
            "from": [{"email": "sender@example.com"}],
            "ext": {"headersBlocks": [{"authentication-results": ["mx; dmarc=fail"]}]},
        }
        assert self.verdict(parsed) is None

    def test_rspamd_result_ignored(self):
        self.dkim = [_sig("example.com")]
        rspamd = {"symbols": {"DMARC_POLICY_REJECT": {"score": 5}}}
        assert (
            check_inbound_authentication(
                RAW_EMAIL, self.PARSED_FROM, self.CONFIG, rspamd
            )
            is None
        )


class TestNativeDmarc(_NativeBase):
    """DMARC policy evaluation drives the forgery verdict."""

    def test_reject_without_aligned_identifier_is_forged(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict() == VERDICT_FORGED

    def test_quarantine_without_aligned_identifier_is_forged(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=quarantine"}
        assert self.verdict() == VERDICT_FORGED

    def test_unaligned_signature_under_reject_is_forged(self):
        self.dkim = [_sig("attacker.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict() == VERDICT_FORGED

    def test_aligned_signature_passes(self):
        self.dkim = [_sig("example.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict() is None

    def test_policy_none_is_only_unverified(self):
        """``p=none`` asks receivers not to act: never a forgery claim."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=none"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_missing_policy_tag_is_only_unverified(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; rua=mailto:x@example.com"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_strict_adkim_rejects_a_subdomain_signature(self):
        self.dkim = [_sig("mail.example.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; adkim=s"}
        assert self.verdict() == VERDICT_FORGED

    def test_relaxed_adkim_accepts_the_same_signature(self):
        """The same message under ``adkim=r``, so the test above is not vacuous."""
        self.dkim = [_sig("mail.example.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; adkim=r"}
        assert self.verdict() is None

    def test_strict_adkim_unaligned_signature_is_not_dkim_pass(self):
        """With ``adkim=s`` and ``p=none`` the subdomain signature is unverified."""
        self.dkim = [_sig("mail.example.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=none; adkim=s"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_subdomain_policy_from_the_parent(self):
        """A subdomain with no record of its own uses the parent's ``sp``."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; sp=none"}
        parsed = {"from": [{"email": "sender@mail.example.com"}]}
        assert self.verdict(parsed) == VERDICT_UNVERIFIED
        assert self.queried == ["_dmarc.mail.example.com", "_dmarc.example.com"]

    def test_subdomain_inherits_p_without_sp(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        parsed = {"from": [{"email": "sender@mail.example.com"}]}
        assert self.verdict(parsed) == VERDICT_FORGED

    def test_sp_does_not_apply_to_the_domain_itself(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; sp=none"}
        assert self.verdict() == VERDICT_FORGED

    def test_partial_quarantine_is_not_enforced(self):
        """``pct`` below 100 reads as "not selected": the next weaker policy."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=quarantine; pct=50"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_partial_reject_still_quarantines(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; pct=10"}
        assert self.verdict() == VERDICT_FORGED

    def test_testing_flag_downgrades(self):
        """RFC 9989 ``t=y``."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=quarantine; t=y"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_dmarc_lookup_failure_is_never_forged(self):
        self.dmarc = {"_dmarc.example.com": ServfailError("_dmarc.example.com", "TXT")}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_dmarc_lookup_failure_keeps_an_aligned_signature_verified(self):
        """A resolver blip must not downgrade every subdomain-signed sender."""
        self.dkim = [_sig("mail.example.com")]
        self.dmarc = {"_dmarc.example.com": ServfailError("_dmarc.example.com", "TXT")}
        assert self.verdict() is None

    def test_aligned_key_lookup_failure_is_never_forged(self):
        self.dkim = [_sig("example.com", "temperror")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict() == VERDICT_UNVERIFIED

    def test_unaligned_key_lookup_failure_does_not_save_a_forgery(self):
        """Only an aligned signature could have passed DMARC."""
        self.dkim = [_sig("attacker.com", "temperror")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict() == VERDICT_FORGED

    def test_two_dmarc_records_disable_dmarc(self):
        def _txt(qname):
            return ["v=DMARC1; p=reject", "v=DMARC1; p=none"]

        with patch("core.mda.dmarc.dmarc_txt_values", side_effect=_txt):
            assert self.verdict() == VERDICT_UNVERIFIED


class TestNativeRobustness(_NativeBase):
    @override_settings(DNS_RESOLVER_INBOUND_AUTH_BUDGET=7.5)
    def test_evaluation_runs_under_the_dns_budget(self):
        with patch("core.mda.inbound_auth.dns_budget") as budget:
            self.verdict()
        budget.assert_called_once_with(7.5)

    def test_unexpected_error_is_unverified_not_raised(self):
        """A bug in evaluation must neither block delivery nor claim forgery."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        with patch(
            "core.mda.inbound_auth.evaluate_dmarc", side_effect=RuntimeError("bug")
        ):
            assert self.verdict() == VERDICT_UNVERIFIED


class TestNativeSpf(_NativeBase):
    """SPF from the SMTP envelope feeds DMARC."""

    def test_spf_is_evaluated_from_the_envelope(self):
        self.verdict(envelope=self.MTA_ENVELOPE)
        self.mock_spf.assert_called_once_with(
            "192.0.2.1", "bounce@example.com", "mx.example.com"
        )

    @pytest.mark.parametrize("origin", ["widget", "internal", "import", None])
    def test_spf_is_skipped_without_an_smtp_peer(self, origin):
        self.verdict(envelope={**self.MTA_ENVELOPE, "origin": origin})
        self.mock_spf.assert_not_called()

    def test_spf_is_skipped_without_a_client_ip(self):
        self.verdict(envelope={**self.MTA_ENVELOPE, "ip": ""})
        self.mock_spf.assert_not_called()

    def test_spf_is_skipped_behind_a_trusted_relay(self):
        """The peer is then the gateway, not the sender."""
        config = {**self.CONFIG, "trusted_relays": 1}
        self.verdict(config=config, envelope=self.MTA_ENVELOPE)
        self.mock_spf.assert_not_called()

    def test_aligned_spf_pass_satisfies_reject(self):
        """DMARC passes on SPF alone: no forgery, but DKIM still decides.

        The same final rule as the other backends: without an aligned DKIM
        signature the message stays "unverified".
        """
        self.spf = SPFResult("pass", "bounces.example.com", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_UNVERIFIED

    def test_strict_aspf_rejects_a_subdomain_identity(self):
        self.spf = SPFResult("pass", "bounces.example.com", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject; aspf=s"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_FORGED

    def test_unaligned_spf_pass_does_not_count(self):
        self.spf = SPFResult("pass", "esp.example.net", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_FORGED

    @pytest.mark.parametrize("result", ["fail", "softfail", "neutral", "none"])
    def test_non_passing_spf_does_not_count(self, result):
        self.spf = SPFResult(result, "example.com", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_FORGED

    def test_aligned_spf_temperror_is_never_forged(self):
        self.spf = SPFResult("temperror", "example.com", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_UNVERIFIED

    def test_aligned_spf_permerror_is_never_forged(self):
        """RFC limits are stricter than many real records: undecided, not forged."""
        self.spf = SPFResult("permerror", "example.com", "mailfrom")
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(envelope=self.MTA_ENVELOPE) == VERDICT_UNVERIFIED

    @pytest.mark.parametrize(
        "case",
        ["no_envelope", "widget", "trusted_relay"],
    )
    def test_spf_not_evaluated_is_never_forged(self, case):
        """An SPF-only sender would otherwise be called forged for our blind spot."""
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        config, envelope = self.CONFIG, self.MTA_ENVELOPE
        if case == "no_envelope":
            envelope = None
        elif case == "widget":
            envelope = {**self.MTA_ENVELOPE, "origin": "widget"}
        else:
            config = {**self.CONFIG, "trusted_relays": 1}
        assert self.verdict(config=config, envelope=envelope) == VERDICT_UNVERIFIED


class TestNativeArcOverride(_NativeBase):
    """A trusted sealer's ``dmarc=pass`` cancels a forgery verdict."""

    SEALED_PASS = {
        "trusted": True,
        "aar": "i=1; relay.example; dkim=pass; dmarc=pass header.from=example.com",
    }

    def test_trusted_seal_cancels_forgery(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        config = {**self.CONFIG, "trusted_arc_sealers": ["relay.example"]}
        assert self.verdict(config=config, arc=self.SEALED_PASS) == VERDICT_UNVERIFIED

    @pytest.mark.parametrize(
        "aar",
        [
            # The seal vouches for another From.
            "i=1; relay.example; dmarc=pass header.from=other.test",
            # No header.from at all: nothing ties it to this message.
            "i=1; relay.example; dmarc=pass",
            # A second result could be an injected value.
            "i=1; relay.example; dmarc=none header.from=example.com; "
            "dmarc=pass header.from=example.com",
        ],
    )
    def test_seal_must_be_unambiguous_and_for_this_from(self, aar):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        config = {**self.CONFIG, "trusted_arc_sealers": ["relay.example"]}
        arc = {"trusted": True, "aar": aar}
        assert self.verdict(config=config, arc=arc) == VERDICT_FORGED

    def test_no_allowlist_no_override(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(arc=self.SEALED_PASS) == VERDICT_FORGED

    def test_untrusted_seal_no_override(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        config = {**self.CONFIG, "trusted_arc_sealers": ["relay.example"]}
        arc = {**self.SEALED_PASS, "trusted": False}
        assert self.verdict(config=config, arc=arc) == VERDICT_FORGED

    def test_sealed_dmarc_fail_no_override(self):
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        config = {**self.CONFIG, "trusted_arc_sealers": ["relay.example"]}
        arc = {**self.SEALED_PASS, "aar": "i=1; relay.example; dmarc=fail"}
        assert self.verdict(config=config, arc=arc) == VERDICT_FORGED

    @patch("core.mda.inbound_auth.arc_result")
    def test_arc_is_computed_only_for_a_forgery(self, mock_arc):
        mock_arc.return_value = self.SEALED_PASS
        config = {**self.CONFIG, "trusted_arc_sealers": ["relay.example"]}
        self.dkim = [_sig("example.com")]
        self.dmarc = {"_dmarc.example.com": "v=DMARC1; p=reject"}
        assert self.verdict(config=config) is None
        mock_arc.assert_not_called()

        self.dkim = []
        assert self.verdict(config=config) == VERDICT_UNVERIFIED
        mock_arc.assert_called_once()


class TestCheckInboundAuthenticationRspamd:
    """Rspamd mode reads DKIM/DMARC symbols from the /checkv2 response."""

    def test_dkim_pass_no_dmarc(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"R_DKIM_ALLOW": {"score": 0.1}}}
        assert check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None

    def test_dkim_pass_dmarc_pass(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_POLICY_ALLOW": {"score": 0.1},
            }
        }
        assert check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None

    def test_dkim_pass_dmarc_fail_forged(self):
        """DMARC fail on a message that otherwise has DKIM -> 'fail' (forgery)."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_POLICY_REJECT": {"score": 5},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_FORGED
        )

    def test_dkim_pass_dmarc_quarantine_forged(self):
        """DMARC quarantine is a fail-equivalent signal -> 'fail'."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_POLICY_QUARANTINE": {"score": 2},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_FORGED
        )

    def test_dkim_pass_dmarc_na(self):
        """DMARC_NA = no published policy -> no DMARC requirement -> accept."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_NA": {"score": 0},
            }
        }
        assert check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None

    def test_dkim_fail(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"R_DKIM_REJECT": {"score": 5}}}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_UNVERIFIED
        )

    def test_dkim_missing(self):
        """DKIM_NA means no DKIM-Signature header -> 'none'."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"DKIM_NA": {"score": 0}}}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_UNVERIFIED
        )

    def test_dkim_fail_dominates_pass(self):
        """Two DKIM symbols in one response: fail wins -> 'none'."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "R_DKIM_REJECT": {"score": 5},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_UNVERIFIED
        )

    def test_dkim_fail_and_dmarc_fail_is_forged(self):
        """DMARC fail is stronger than DKIM fail -> 'fail'."""
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_REJECT": {"score": 5},
                "DMARC_POLICY_REJECT": {"score": 5},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd)
            == VERDICT_FORGED
        )

    def test_no_rspamd_result_unverified(self):
        """Backend unavailable -> can't verify -> 'none' (not 'fail')."""
        config = {"inbound_auth": "rspamd"}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, None)
            == VERDICT_UNVERIFIED
        )

    def test_rspamd_result_without_symbols_unverified(self):
        """Response missing `symbols` key -> no evidence -> 'none'."""
        config = {"inbound_auth": "rspamd"}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, {})
            == VERDICT_UNVERIFIED
        )


class TestCheckInboundAuthenticationResults:
    """`authentication-results` mode parses an upstream relay's AR header."""

    @staticmethod
    def _parsed(ar_values, trust_blocks=1):
        """Build a ``parsed_email`` with a ``headers`` list shaped so the
        ``headers_blocks`` helper recovers exactly the blocks described
        by ``ar_values``.

        Each block ends with a synthetic ``Received`` header. AR entries
        for the block appear before its closing ``Received``; ``None``
        in ``ar_values`` produces a block whose Received is the only
        header (no Authentication-Results).
        """
        headers: list[dict] = []
        for i in range(trust_blocks):
            if i < len(ar_values) and ar_values[i] is not None:
                for value in ar_values[i]:
                    headers.append({"name": "Authentication-Results", "value": value})
            headers.append({"name": "Received", "value": f"from hop{i}"})
        return {"headers": headers}

    def test_dkim_pass_no_dmarc(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=pass"]])
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_pass_dmarc_pass(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=pass; dmarc=pass"]])
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_pass_dmarc_fail_forged(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=pass; dmarc=fail"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_FORGED

    def test_dkim_pass_dmarc_none(self):
        """dmarc=none -> no policy -> don't require DMARC pass."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=pass; dmarc=none"]])
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_fail(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=fail"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_dkim_softfail_is_fail(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; dkim=softfail"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_dkim_none_is_unverified(self):
        """dkim=none (no signature) is not a pass -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; dkim=none"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_header_absent_unverified(self):
        """No AR header anywhere -> can't verify -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {"ext": {"headersBlocks": [{}]}}
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_no_dkim_entry_unverified(self):
        """AR present but no dkim= entry -> unknown -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; spf=pass"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_untrusted_block_ignored(self):
        """trusted_relays=0 -> only block 0 (our MTA) is trusted."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 0}
        parsed = {
            "ext": {
                "headersBlocks": [
                    {},  # block 0: no AR from us
                    {"authentication-results": ["mx; dkim=pass"]},  # untrusted
                ]
            }
        }
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_trusted_block_used(self):
        """trusted_relays=1 -> block 1 (one configured relay hop) is trusted.

        Block 0 (our own prepend) has no AR; block 1 (the first
        upstream relay) carries ``dkim=pass``. ``headers_blocks`` groups
        them by walking the ``headers`` list in order, closing each
        block at the next ``Received``.
        """
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {
            "headers": [
                {"name": "Received", "value": "from our-mta"},
                {"name": "Authentication-Results", "value": "mx; dkim=pass"},
                {"name": "Received", "value": "from upstream"},
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_default_trusts_only_our_block(self):
        """Default (no trusted_relays) -> only block 0 is trusted.

        The same payload as ``test_trusted_block_used`` but without an
        explicit ``trusted_relays`` must NOT honour block 1's ``dkim=pass``:
        a sender can forge that block, so the secure default of 0 ignores it
        and the verdict collapses to "unverified".
        """
        config = {"inbound_auth": "authentication-results"}
        parsed = {
            "headers": [
                {"name": "Received", "value": "from our-mta"},
                {"name": "Authentication-Results", "value": "mx; dkim=pass"},
                {"name": "Received", "value": "from upstream"},
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_dkim_fail_dominates_pass_across_values(self):
        """Multiple AR values in one block: fail wins -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx1; dkim=pass", "mx2; dkim=fail"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_single_string_ar_value(self):
        """AR header may be a bare string (single occurrence) rather than list."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {
            "headers": [
                {"name": "Authentication-Results", "value": "mx; dkim=pass"},
                {"name": "Received", "value": "from hop0"},
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) is None


class TestCheckInboundAuthArcMode:
    """`arc` mode: verdict from the trusted sealer's sealed AAR only; the
    plaintext header is never read."""

    CONFIG = {"inbound_auth": "arc", "trusted_arc_sealers": ["relay.example"]}

    # A forged top-level header must be ignored entirely in arc mode.
    PARSED_FORGED_HEADER = {
        "headers": [{"name": "Authentication-Results", "value": "mx; dkim=pass"}]
    }

    def test_trusted_seal_verified(self):
        arc = {
            "trusted": True,
            "sealer": "relay.example",
            "aar": "i=2; mx; dkim=pass; dmarc=pass",
            "dnsfail": False,
        }
        assert check_inbound_authentication(b"raw", {}, self.CONFIG, arc=arc) is None

    def test_trusted_seal_dmarc_fail_forged(self):
        arc = {
            "trusted": True,
            "sealer": "relay.example",
            "aar": "i=2; mx; dkim=pass; dmarc=fail",
            "dnsfail": False,
        }
        assert (
            check_inbound_authentication(b"raw", {}, self.CONFIG, arc=arc)
            == VERDICT_FORGED
        )

    def test_untrusted_ignores_forged_header(self):
        arc = {"trusted": False, "sealer": None, "aar": None, "dnsfail": False}
        assert (
            check_inbound_authentication(
                b"raw", self.PARSED_FORGED_HEADER, self.CONFIG, arc=arc
            )
            == VERDICT_UNVERIFIED
        )

    def test_dnsfail_unverified(self):
        arc = {"trusted": False, "sealer": None, "aar": None, "dnsfail": True}
        assert (
            check_inbound_authentication(b"raw", {}, self.CONFIG, arc=arc)
            == VERDICT_UNVERIFIED
        )

    @patch("core.mda.inbound_auth.arc_result")
    def test_computes_arc_when_not_passed(self, mock_arc):
        mock_arc.return_value = {
            "trusted": True,
            "sealer": "relay.example",
            "aar": "i=1; mx; dkim=pass",
            "dnsfail": False,
        }
        assert check_inbound_authentication(b"raw", {}, self.CONFIG) is None
        mock_arc.assert_called_once()


class TestCheckInboundAuthenticationResultsScrubbing:
    """`dkim=`/`dmarc=` literals inside CFWS comments or quoted strings must
    NOT be honoured — they're attacker-controlled free text."""

    CONFIG = {"inbound_auth": "authentication-results", "trusted_relays": 1}

    @staticmethod
    def _parsed(ar_value):
        return {
            "headers": [
                {"name": "Authentication-Results", "value": ar_value},
                {"name": "Received", "value": "from hop0"},
            ]
        }

    # --- Comments (parens) ---------------------------------------------

    def test_comment_dkim_pass_does_not_satisfy(self):
        """`(dkim=pass)` inside a comment is text, not a token."""
        parsed = self._parsed("mx; spf=fail (dkim=pass attack)")
        # No real dkim= → unverified.
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_comment_after_real_token_does_not_override(self):
        """Real `dkim=fail` outside a comment still wins."""
        parsed = self._parsed("mx; dkim=fail (reason: dkim=pass spoof)")
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_comment_dmarc_fail_does_not_force_forged(self):
        """`(dmarc=fail)` inside a comment shouldn't escalate to 'fail'."""
        parsed = self._parsed("mx; dkim=pass (note: dmarc=fail in some other domain)")
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    def test_nested_comments_scrubbed(self):
        """RFC 5322 comments may nest; the whole nested span is scrubbed."""
        parsed = self._parsed("mx; spf=pass (outer (dkim=pass) trailing) ")
        # No real dkim= token outside the comment.
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_real_token_after_comment_still_matched(self):
        """A genuine token following a comment must still be parsed."""
        parsed = self._parsed("mx (received from upstream); dkim=pass")
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    # --- Quoted strings ------------------------------------------------

    def test_quoted_dkim_pass_does_not_satisfy(self):
        """`dkim=pass` inside a quoted string is just opaque text."""
        parsed = self._parsed('mx; spf=pass smtp.mailfrom="bob (dkim=pass)"')
        # spf is not in our rule set; no real dkim= → unverified.
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_quoted_string_with_escapes_scrubbed(self):
        """Backslash-escaped quotes inside a quoted string don't end it."""
        parsed = self._parsed('mx; spf=pass smtp.mailfrom="a\\"dkim=pass\\"b"')
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_real_token_after_quoted_string_still_matched(self):
        """A real token after a quoted reason is still seen."""
        parsed = self._parsed('mx; spf=fail smtp.mailfrom="weird value"; dkim=pass')
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    # --- Mixed ---------------------------------------------------------

    def test_quoted_inside_comment_scrubbed(self):
        parsed = self._parsed('mx (a "dkim=pass" b); dkim=fail')
        # Real token is dkim=fail.
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_unterminated_comment_does_not_explode(self):
        """Malformed (unterminated) CFWS shouldn't crash the parser.

        Real `dkim=pass` appears before the unterminated comment, so it's
        still seen as a verified token; the comment swallows the rest of the
        string but that has no other tokens to lose.
        """
        parsed = self._parsed("mx; dkim=pass (unterminated comment")
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    def test_unterminated_comment_swallows_real_token(self):
        """If the real token is INSIDE an unterminated comment, it's lost."""
        parsed = self._parsed("mx; spf=pass (comment that never closes dkim=pass")
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_unterminated_quote_does_not_explode(self):
        parsed = self._parsed('mx; dkim=pass "unterminated')
        # Real dkim=pass token comes BEFORE the unterminated quote.
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    # --- Token boundary --------------------------------------------------

    def test_prefixed_label_not_matched(self):
        """`x-dkim=fail` is its own label, not a bare `dkim=` token."""
        parsed = self._parsed("mx; x-dkim=fail")
        # No real dkim token → unverified.
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_dotted_label_not_matched(self):
        """`arc.dkim=pass` is also a different label."""
        parsed = self._parsed("mx; arc.dkim=pass")
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_underscore_label_not_matched(self):
        parsed = self._parsed("mx; foo_dkim=pass")
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_label_with_real_token_after_separator(self):
        """A spurious label first, then a real bare `dkim=`."""
        parsed = self._parsed("mx; x-dkim=fail; dkim=pass")
        # x-dkim=fail must not pollute; real dkim=pass wins → verified.
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    def test_real_token_at_start_of_value(self):
        """No leading separator — start-of-string still matches."""
        parsed = self._parsed("dkim=pass")
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    def test_realworld_multiline_ar_header_all_pass(self):
        """A real Authentication-Results header (anonymized): dkim+spf+dmarc all pass.

        Includes folded continuation lines, parenthesized comments containing
        `domain of "..."` quoted strings with `=` signs, `header.b="..."`
        quoted DKIM signatures, and `(policy=quarantine)` comments — all
        prone to spoofing the simple regex if scrubbing isn't applied.
        """
        ar = (
            "mx.example-relay.net;\r\n"
            '    dkim=pass header.d=newsletter.example.com header.s=s1 header.b="aB/cDeFg";\r\n'
            "    spf=pass (mx.example-relay.net: domain of "
            '"bounces+12345-abcd-recipient=example.org@bounce.newsletter.example.com" '
            "designates 192.0.2.10 as permitted sender) "
            'smtp.mailfrom="bounces+12345-abcd-recipient=example.org@bounce.newsletter.example.com";\r\n'
            "    dmarc=pass (policy=quarantine) header.from=example.com"
        )
        parsed = self._parsed(ar)
        # dkim=pass real, dmarc=pass real → verified.
        assert check_inbound_authentication(b"", parsed, self.CONFIG) is None

    def test_realworld_ar_header_dmarc_fail_still_detected(self):
        """Same shape as above but dmarc fails — must surface as forgery."""
        ar = (
            "mx.example-relay.net;\r\n"
            '    dkim=pass header.d=newsletter.example.com header.s=s1 header.b="aB/cDeFg";\r\n'
            "    spf=pass (mx.example-relay.net: domain of "
            '"bounces+12345@bounce.example.com" designates 192.0.2.10 as permitted sender) '
            'smtp.mailfrom="bounces+12345@bounce.example.com";\r\n'
            "    dmarc=fail (policy=reject) header.from=example.com"
        )
        parsed = self._parsed(ar)
        assert check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_FORGED

    def test_realworld_ar_header_dkim_fail(self):
        """Real-world shape with dkim=fail → unverified."""
        ar = (
            "mx.example-relay.net;\r\n"
            '    dkim=fail header.d=newsletter.example.com header.s=s1 header.b="aB/cDeFg";\r\n'
            "    spf=pass (mx.example-relay.net: domain of "
            '"bounces+12345@bounce.example.com" designates 192.0.2.10 as permitted sender) '
            'smtp.mailfrom="bounces+12345@bounce.example.com";\r\n'
            "    dmarc=pass (policy=quarantine) header.from=example.com"
        )
        parsed = self._parsed(ar)
        assert (
            check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_UNVERIFIED
        )

    def test_dkim_pass_inside_comment_then_dmarc_fail_real(self):
        """Combination: comment fakes dkim=pass; real dmarc=fail wins as 'fail'."""
        parsed = self._parsed("mx; spf=pass (dkim=pass spoof); dmarc=fail")
        # No real dkim= token; dmarc=fail real → 'fail' verdict.
        # But our rules say: dkim must pass for verified; dmarc=fail forces
        # 'fail' regardless. Since dkim isn't pass, but dmarc=fail dominates
        # → return VERDICT_FORGED.
        assert check_inbound_authentication(b"", parsed, self.CONFIG) == VERDICT_FORGED


@pytest.mark.django_db
class TestNativeEndToEnd:
    """The real native stack through the inbound task, DNS faked at the resolver.

    The message (From: sender@example.com, unsigned) arrives from 192.0.2.1
    with MAIL FROM bounce@example.com. Only DNS changes between tests.
    """

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        cache.clear()
        yield
        cache.clear()

    def _deliver(self, zone):
        def _resolve(name, rdtype="A"):
            key = (name.rstrip(".").lower(), rdtype)
            if key not in zone:
                raise NXDOMAINError(name, rdtype)
            return txt_answer(*zone[key], name=key[0])

        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        inbound_message.envelope = dict(MTA_ENVELOPE)
        inbound_message.save(update_fields=["envelope"])
        with (
            override_settings(SPAM_CONFIG={"inbound_auth": "native"}),
            patch("core.services.dns.resolver.resolve_answer", side_effect=_resolve),
            patch(
                "core.mda.inbound_tasks._create_message_from_inbound",
                return_value=True,
            ) as mock_create,
            patch.object(process_inbound_message_task, "update_state", Mock()),
        ):
            process_inbound_message_task.run(str(inbound_message.id))
        return (mock_create.call_args[1].get("postmark") or {}).get("auth")

    def test_spoof_of_a_reject_domain_is_forged(self):
        zone = {
            ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject"],
            ("example.com", "TXT"): ["v=spf1 ip4:198.51.100.0/24 -all"],
        }
        assert self._deliver(zone) == VERDICT_FORGED

    def test_aligned_spf_pass_is_not_forged(self):
        zone = {
            ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject"],
            ("example.com", "TXT"): ["v=spf1 ip4:192.0.2.0/24 -all"],
        }
        assert self._deliver(zone) == VERDICT_UNVERIFIED

    def test_no_dmarc_is_unverified(self):
        zone = {("example.com", "TXT"): ["v=spf1 -all"]}
        assert self._deliver(zone) == VERDICT_UNVERIFIED


@pytest.mark.django_db
class TestProcessInboundMessageAuthIntegration:
    """End-to-end: a verdict is recorded in ``postmark["auth"]``, off the bytes."""

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_unverified_verdict_recorded_in_postmark(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_auth_check.return_value = VERDICT_UNVERIFIED
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_create_message.called
        call_kwargs = mock_create_message.call_args[1]
        # Verdict is structured, not baked into the bytes.
        assert call_kwargs["postmark"]["auth"] == "none"
        assert not call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth")

    @override_settings(SPAM_CONFIG={"inbound_auth": "rspamd"})
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_forged_verdict_recorded_in_postmark(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_auth_check.return_value = VERDICT_FORGED
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["postmark"]["auth"] == "fail"
        assert not call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth")

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_verified_records_no_auth(self, mock_create_message, mock_auth_check):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_auth_check.return_value = None
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        assert "auth" not in (call_kwargs.get("postmark") or {})
        assert not call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth")

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_envelope_reaches_the_auth_check(
        self, mock_create_message, mock_auth_check
    ):
        """Native SPF needs the SMTP envelope the MTA recorded."""
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        inbound_message.envelope = dict(MTA_ENVELOPE)
        inbound_message.save(update_fields=["envelope"])
        mock_auth_check.return_value = None
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        kwargs = mock_auth_check.call_args.kwargs
        assert kwargs["envelope"] == MTA_ENVELOPE
        assert kwargs["mode"] == "native"

    @override_settings(
        SPAM_CONFIG={
            "inbound_auth": "authentication-results",
            "inbound_auth_shadow": "native",
        }
    )
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_shadow_mode_is_logged_not_surfaced(
        self, mock_create_message, mock_auth_check, caplog
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        verdicts = {"authentication-results": None, "native": VERDICT_FORGED}
        mock_auth_check.side_effect = lambda *a, mode, **kw: verdicts[mode]
        mock_create_message.return_value = True

        with (
            caplog.at_level("INFO", logger="core.mda.inbound_pipeline"),
            patch.object(process_inbound_message_task, "update_state", Mock()),
        ):
            process_inbound_message_task.run(str(inbound_message.id))

        assert "auth" not in (mock_create_message.call_args[1].get("postmark") or {})
        assert [c.kwargs["mode"] for c in mock_auth_check.call_args_list] == [
            "authentication-results",
            "native",
        ]
        assert "authentication-results=verified native=fail MISMATCH" in caplog.text

    @override_settings(
        SPAM_CONFIG={"inbound_auth": "native", "inbound_auth_shadow": "native"}
    )
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_shadow_identical_to_primary_is_not_run_twice(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_auth_check.return_value = None
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_auth_check.call_count == 1

    @override_settings(
        SPAM_CONFIG={"inbound_auth": "native", "inbound_auth_shadow": "rspamd"}
    )
    @patch("core.mda.inbound_pipeline.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_shadow_error_does_not_break_delivery(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)

        def _check(*args, mode, **kwargs):
            if mode == "rspamd":
                raise RuntimeError("shadow broke")
            return VERDICT_UNVERIFIED

        mock_auth_check.side_effect = _check
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_create_message.call_args[1]["postmark"]["auth"] == "none"

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
        }
    )
    @patch("core.mda.spam.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_rspamd_response_reused_by_auth_check(self, mock_create_message, mock_post):
        """Single rspamd call feeds both spam and auth."""
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 1.0,
            "required_score": 15.0,
            "symbols": {"R_DKIM_REJECT": {"score": 5}},
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_post.call_count == 1
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["postmark"]["auth"] == "none"

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
        }
    )
    @patch("core.mda.spam.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_dmarc_fail_injects_fail_header_end_to_end(
        self, mock_create_message, mock_post
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(mailbox)
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 1.0,
            "required_score": 15.0,
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_POLICY_REJECT": {"score": 5},
            },
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["postmark"]["auth"] == "fail"

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
            "rules": [{"header_match": "X-Spam:yes", "action": "ham"}],
        }
    )
    @patch("core.mda.spam.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_rspamd_fetched_on_demand_when_spam_skipped_rspamd(
        self, mock_create_message, mock_post
    ):
        """Hardcoded spam rule short-circuits spam; rspamd still fetched for auth."""
        mailbox = factories.MailboxFactory()
        raw = b"X-Spam: yes\r\n" + RAW_EMAIL
        inbound_message = _queue_inbound(mailbox, raw)
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 1.0,
            "required_score": 15.0,
            "symbols": {"R_DKIM_ALLOW": {"score": 0.1}},
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_post.call_count == 1
        call_kwargs = mock_create_message.call_args[1]
        # DKIM_ALLOW → verified → no auth verdict recorded.
        assert "auth" not in (call_kwargs.get("postmark") or {})

    def test_maildomain_override(self):
        """custom_settings.SPAM_CONFIG overrides the global default."""
        maildomain = factories.MailDomainFactory(
            custom_settings={
                "SPAM_CONFIG": {"inbound_auth": "rspamd"},
            }
        )
        config = maildomain.get_spam_config()
        assert config.get("inbound_auth") == "rspamd"

    def test_legacy_stmsg_header_still_parsed(self):
        """The parser still exposes legacy baked ``X-StMsg-*`` headers, which
        ``get_stmsg_headers`` reads for pre-``postmark`` messages."""
        tagged = b"X-StMsg-Sender-Auth: fail\r\n" + RAW_EMAIL
        parsed = parse_email(tagged)
        values = [
            h["value"]
            for h in parsed["headers"]
            if h["name"].lower() == "x-stmsg-sender-auth"
        ]
        assert values == ["fail"]


class TestArcStep:
    """The ``arc`` pipeline step holds (RETRY) a message whose *trusted* seal
    couldn't be verified due to a DNS failure. Untrusted-message gating now
    lives in the ``arc`` spam rules (see TestArcRules), not this step."""

    @staticmethod
    def _ctx(created_at=None, origin=None):
        return SimpleNamespace(
            raw_data=b"raw",
            is_spam=None,
            arc=None,
            inbound_message=SimpleNamespace(
                id="msg1",
                created_at=created_at or timezone.now(),
                envelope={"origin": origin} if origin else None,
            ),
        )

    @staticmethod
    def _arc(trusted=False, dnsfail=False, sealer=None):
        return {"trusted": trusted, "dnsfail": dnsfail, "sealer": sealer, "aar": None}

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_no_allowlist_is_noop(self, mock_arc):
        # Empty allowlist = not an enforcement posture: skip, and don't even
        # pay the ARC verification cost.
        ctx = self._ctx()
        assert _make_arc_step({})(ctx) == Decision.CONTINUE
        mock_arc.assert_not_called()
        assert ctx.arc is None

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_trusted_continues(self, mock_arc):
        mock_arc.return_value = self._arc(trusted=True, sealer="relay.example")
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        assert _make_arc_step(cfg)(self._ctx()) == Decision.CONTINUE

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_dnsfail_for_claimed_trusted_sealer_retries(self, mock_arc):
        mock_arc.return_value = self._arc(dnsfail=True, sealer="relay.example")
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        ctx = self._ctx()
        assert _make_arc_step(cfg)(ctx) == Decision.RETRY
        # Cached for downstream reuse (rules / inbound_auth).
        assert ctx.arc is mock_arc.return_value

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_dnsfail_for_untrusted_sealer_does_not_retry(self, mock_arc):
        # A DNS blip against a sealer we don't list is not our problem to hold —
        # it's a definite untrusted verdict handled by the arc rules.
        mock_arc.return_value = self._arc(dnsfail=True, sealer="evil.net")
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        assert _make_arc_step(cfg)(self._ctx()) == Decision.CONTINUE

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_definite_untrusted_does_not_retry(self, mock_arc):
        # The step never gates untrusted messages (that's the rules' job); it
        # only holds on dnsfail.
        mock_arc.return_value = self._arc(trusted=False, sealer="evil.net")
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        ctx = self._ctx()
        assert _make_arc_step(cfg)(ctx) == Decision.CONTINUE
        assert ctx.is_spam is None

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_dnsfail_past_deferral_window_reclassified_untrusted(self, mock_arc):
        # After the retry window, an unresolvable claimed-trusted seal stops
        # being held: dnsfail is cleared so the arc_verdict rules see "untrusted"
        # (and an untrusted->drop rule can fire) instead of force-delivery.
        mock_arc.return_value = self._arc(dnsfail=True, sealer="relay.example")
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        old = timezone.now() - DEFERRAL_MAX_AGE - timedelta(hours=1)
        ctx = self._ctx(created_at=old)
        assert _make_arc_step(cfg)(ctx) == Decision.CONTINUE
        assert ctx.arc["dnsfail"] is False

    @patch("core.mda.inbound_pipeline.arc_result")
    def test_widget_origin_skips_arc(self, mock_arc):
        # Widget submissions carry no seal — ARC gating must not touch them.
        cfg = {"trusted_arc_sealers": ["relay.example"]}
        ctx = self._ctx(origin=enums.InboundOrigin.WIDGET)
        assert _make_arc_step(cfg)(ctx) == Decision.CONTINUE
        mock_arc.assert_not_called()
        assert ctx.arc is None
