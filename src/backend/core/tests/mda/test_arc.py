"""Tests for ARC chain verification (core.mda.arc)."""

# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=protected-access,unused-argument

import base64
from unittest.mock import patch

import dkim
import pytest

from core.mda import arc
from core.services.dns.resolver import (
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
)


class TestSealerTrusted:
    def test_exact_match(self):
        assert arc._sealer_trusted("relay.example", {"relay.example"})

    def test_subdomain_match(self):
        assert arc._sealer_trusted("mx.relay.example", {"relay.example"})

    def test_lookalike_not_matched(self):
        assert not arc._sealer_trusted("evil-relay.example", {"relay.example"})

    def test_none(self):
        assert not arc._sealer_trusted(None, {"relay.example"})


class TestArcResult:
    """Decision logic — parser and crypto both mocked."""

    @staticmethod
    def _outer(sealer, max_i=1):
        return patch("core.mda.arc._outermost_sealer", return_value=(sealer, max_i))

    @staticmethod
    def _verify(cv, results):
        return patch("core.mda.arc.arc_verify", return_value=(cv, results, "ok"))

    def test_empty_allowlist_trusts_nothing(self):
        # Fail closed: no allowlist -> nothing trusted, and no parse/verify.
        with (
            patch("core.mda.arc._outermost_sealer") as mock_outer,
            patch("core.mda.arc.arc_verify") as mock_verify,
        ):
            out = arc.arc_result(b"raw", set())
        assert out == {"trusted": False, "sealer": None, "aar": None, "dnsfail": False}
        mock_outer.assert_not_called()
        mock_verify.assert_not_called()

    def test_verify_exception_untrusted(self):
        with (
            self._outer("relay.example"),
            patch("core.mda.arc.arc_verify", side_effect=Exception("boom")),
        ):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["dnsfail"] is False
        assert out["trusted"] is False

    def test_trusted_seal_exposes_aar(self):
        results = [{"ams-domain": b"relay.example", "aar-value": b"i=2; mx; dkim=pass"}]
        with self._outer("relay.example"), self._verify(arc.CV_Pass, results):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is True
        assert out["sealer"] == "relay.example"
        assert out["aar"] == "i=2; mx; dkim=pass"

    def test_cv_fail_untrusted(self):
        # Outermost sealer IS listed, so we verify — but the chain fails.
        results = [{"ams-domain": b"relay.example", "aar-value": b"i=2; mx; dkim=pass"}]
        with self._outer("relay.example"), self._verify(b"fail", results):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is False
        assert out["aar"] is None

    def test_no_arc_set(self):
        # Real b"raw" has no chain: the cheap gate short-circuits before verify.
        with patch("core.mda.arc.arc_verify") as mock_verify:
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out == {"trusted": False, "sealer": None, "aar": None, "dnsfail": False}
        mock_verify.assert_not_called()


class TestArcFastPath:
    """Q1: full crypto/DNS verification runs ONLY for a claimed-trusted sealer."""

    def test_unlisted_sealer_skips_verification(self):
        with (
            patch("core.mda.arc._outermost_sealer", return_value=("evil.net", 1)),
            patch("core.mda.arc.arc_verify") as mock_verify,
        ):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is False
        assert out["sealer"] == "evil.net"
        assert out["dnsfail"] is False
        mock_verify.assert_not_called()

    def test_no_chain_skips_verification(self):
        with (
            patch("core.mda.arc._outermost_sealer", return_value=(None, 0)),
            patch("core.mda.arc.arc_verify") as mock_verify,
        ):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is False
        assert out["sealer"] is None
        mock_verify.assert_not_called()

    def test_overlong_chain_skips_verification(self):
        # One past the cap (_MAX_ARC_INSTANCES = 20) is refused without verifying.
        with (
            patch("core.mda.arc._outermost_sealer", return_value=("relay.example", 21)),
            patch("core.mda.arc.arc_verify") as mock_verify,
        ):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is False
        mock_verify.assert_not_called()

    def test_at_cap_still_verifies(self):
        # Exactly at the cap is still verified.
        results = [{"ams-domain": b"relay.example", "aar-value": b"x"}]
        with (
            patch("core.mda.arc._outermost_sealer", return_value=("relay.example", 20)),
            patch(
                "core.mda.arc.arc_verify", return_value=(arc.CV_Pass, results, "ok")
            ) as mock_verify,
        ):
            out = arc.arc_result(b"raw", {"relay.example"})
        assert out["trusted"] is True
        mock_verify.assert_called_once()

    def test_empty_allowlist_never_verifies(self):
        # Fail closed: an empty allowlist trusts nothing and does no crypto/DNS.
        with patch("core.mda.arc.arc_verify") as mock_verify:
            out = arc.arc_result(b"raw", set())
        assert out["trusted"] is False
        mock_verify.assert_not_called()


# A message ARC-sealed once with a throwaway 2048-bit RSA key (domain
# relay.example, selector arcsel). Verified offline via a stub dnsfunc that
# returns the matching public key below — exercises the real dkimpy crypto
# without network or PII.
_PUBKEY_TXT = (
    "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAz950COD08H"
    "iYPJpql+PUqMY7oH7Z3jR/7KPX0b6hZFaw8wQCE3xB4cWB3Bwarw27oA7f4IfMOH6ifKqf3h"
    "wSo0/zcH6s2Tz2UP8hrC9lq5OoiMdzmzfQ1wx6M5Z0stCCe/FFkh4GihANLtiOslxK+R1Gee"
    "Q+fP2eQWbXcYdElVoWKwTQxpfALfshyxLmLjJq4Ji86wRpii9mSUne1GDbHAMvEU5tgZI+Hr"
    "vluxwR3v6hEe+mhy0la8sXLHK9wEu0D3h3G26te/nat24ZMukI6+jjvhVIRp6LM2rWU2rZgq"
    "6iEmuWl0jURCWD+JrxTuLMU7VgnMYuUOgKI6yuuma1IwIDAQAB"
)

_SEALED_B64 = (
    "QVJDLVNlYWw6IGk9MTsgY3Y9bm9uZTsgYT1yc2Etc2hhMjU2OyBkPXJlbGF5LmV4YW1wbGU7"
    "IHM9YXJjc2VsOyB0PTEyMzQ1Ow0KIGI9WGFvVlAvam1OcVNvNWJwK3ZYM25INS9jRmJZK1Y4"
    "TVdvN2cvQkoyZUd6ZXo0TENIS0U1N1UzU0g1U0RBT0xuc0MzQ3BvDQogbm9VTktyTm9UQ1hk"
    "THBJR1dqNWM0d1NpMExTVjB2NUF1dWphQkN2eEdOZ3F6aGJRT3JiUnV3bUMzSlVHeU5OSExF"
    "RmF6d3gNCiBFd09rSHlRUGx4QXcrVDRTczhiMU50dDRNK2JoZlk2UW5kb1ZIK1JHUXByQXBp"
    "eFhpdzV4TXlmR05XNGxCUXJ0blF4RHllNg0KIDl1aXFRYWlFaktMZlRBU2ZjdjB5ZUJqd2pv"
    "cllnL0x4OHIxZUFqdlFjaythUGpZR1BmTjdLck5LU1pFVkxiOVczK3NENG5mDQogZWdhQjBF"
    "QWdXTnlzaXdXUW5xR1hXN0p6N3RvNGd1d1Y2blRTcWVTb0hMdC9hUlJLc3VSUFBkaVo1Z1RR"
    "PT0NCkFSQy1NZXNzYWdlLVNpZ25hdHVyZTogaT0xOyBhPXJzYS1zaGEyNTY7IGM9cmVsYXhl"
    "ZC9yZWxheGVkOw0KIGQ9cmVsYXkuZXhhbXBsZTsgcz1hcmNzZWw7IHQ9MTIzNDU7IGg9ZnJv"
    "bSA6IHRvIDogc3ViamVjdCA6IG1lc3NhZ2UtaWQNCiA6IGZyb207IGJoPUNrNVNvUk5XVXBT"
    "UjRYMENPdjdSNXViMnBVVHRsNnh6NGRURnorK2ppNE09Ow0KIGI9R0RqSHR0QjBUWVJSM21s"
    "UHQzbEJ0azA2ejlQcHNXZWhjcXUzWCtFRVRveXZ3S0ZOQzFtVHg2eDFBRkZOemJtRkhIRHNh"
    "DQogZG1BdzlyVURTaTBEMEhyQ3Q2L2RBSzZ4QWIxTGRrb0Q5SzZ5MmhFYTExcm01SWF2U1Rk"
    "NDkzcXd4TytBNkpLNUJIVHJqZlINCiA3VlNWN2FWM1FtV1FFOVdwVU56WHZ4RkJxSVU2L2lQ"
    "TzgrUEprbmJGdi9ibW91U2RzRzVFdjZWMGV1RnU4Wk4zL2dFSDRSdQ0KIDNJTnVucVg4M0VD"
    "RzczOWhSV0JoYVZsdkRrdlZRN293Q2gxZW5Iejl2eWwrZWdhUkdyWnhKY1BRSWE0d1UrZTNG"
    "V3VGanh5DQogNmJXcDhvbzdDQnF2NDNqSGVWSGtBSjNtNGFzSDJhNVpzMEdHRHE3aGN4b0pz"
    "YjlvMkFNbFBaRW1tM0dBPT0NCkFSQy1BdXRoZW50aWNhdGlvbi1SZXN1bHRzOiBpPTE7IHJl"
    "bGF5LmV4YW1wbGU7DQogZGtpbT1wYXNzIGhlYWRlci5kPXNlbmRlci5leGFtcGxlOw0KIGRt"
    "YXJjPXBhc3MNCkF1dGhlbnRpY2F0aW9uLVJlc3VsdHM6IHJlbGF5LmV4YW1wbGU7IGRraW09"
    "cGFzcyBoZWFkZXIuZD1zZW5kZXIuZXhhbXBsZTsgZG1hcmM9cGFzcw0KRnJvbTogYUBzZW5k"
    "ZXIuZXhhbXBsZQ0KVG86IGJAcmNwdC5leGFtcGxlDQpTdWJqZWN0OiBoaQ0KTWVzc2FnZS1J"
    "RDogPHhAc2VuZGVyLmV4YW1wbGU+DQoNCmJvZHkNCg=="
)


# A *different* throwaway 2048-bit RSA public key — used to prove that a seal
# which fails to validate against a well-resolved key is a definite failure
# (``dnsfail=False``), not confused with an unreachable resolver.
_MISMATCHED_PUBKEY_P = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsOMjeA+6E9jY54/PEF1q4gDbR1ai"
    "ZSEbyrPvb1zDyDulEzaACYVxxQHK3iJICMK8IW+ewZH/59/WSfAoxKGwv/ua5Ad7rWhW7INk"
    "L7v98eLYGDE4B6PYjtiw+xIquKoL2PUVTXXkkUDsPny5TjPk8pfpRG94Wz1dE7E1CMglEW/R2"
    "MV3E6UQVBg0sTtBA/OF+PPWiKL5+5YcuSN/fuCEwwdzZ9O3x3UnLrz5GGLxwrWJsg75K4UCVj"
    "OLO138VYRt9fN8qtLFs3NZ6gsphMGkZXVPy9FjC+G76PtBRfbN9m2lEMLQCeDuIBDlZ5/Mzsi"
    "RKbkzdfAUrNfj1TfTqA6iuQIDAQAB"
)


def _stub_dns(name, timeout=5):
    return _PUBKEY_TXT


def _verify_with_stub_dns(raw, dnsfunc=None):
    # arc_result now passes its own DNS-tracking dnsfunc; ignore it and use the
    # offline stub so these tests exercise the real crypto without a network.
    return dkim.arc_verify(raw, dnsfunc=_stub_dns)


class TestOutermostSealer:
    """The cheap (no crypto/DNS) outermost-sealer parse."""

    def test_real_sealed_message(self):
        sealer, max_i = arc._outermost_sealer(base64.b64decode(_SEALED_B64))
        assert sealer == "relay.example"
        assert max_i == 1

    def test_no_chain(self):
        assert arc._outermost_sealer(b"From: a@b\r\nSubject: x\r\n\r\nbody") == (
            None,
            0,
        )

    def test_garbage_is_no_chain(self):
        sealer, max_i = arc._outermost_sealer(b"\x00\xff not a real message")
        assert max_i == 0


class TestArcResultRealCrypto:
    """Exercise the real dkimpy chain verification (stub DNS, no network)."""

    SEALED = base64.b64decode(_SEALED_B64)

    def test_trusted_sealer_real_verify(self):
        with patch("core.mda.arc.arc_verify", _verify_with_stub_dns):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is True
        assert out["sealer"] == "relay.example"
        assert "dkim=pass" in str(out["aar"])

    def test_untrusted_sealer_real_verify(self):
        with patch("core.mda.arc.arc_verify", _verify_with_stub_dns):
            out = arc.arc_result(self.SEALED, {"other.example"})
        assert out["trusted"] is False
        assert out["sealer"] == "relay.example"
        assert out["aar"] is None

    def test_dns_lookup_raises_is_dnsfail(self):
        # Real arc_verify runs, but the key lookup did not complete (a timeout
        # or SERVFAIL reaches arc_dns_txt's caller as a raise): indeterminate,
        # not forged -> dnsfail. NXDOMAIN does NOT land here; arc_dns_txt
        # catches it and returns None, which the test below covers.
        def _boom(name, timeout=5):
            raise Exception("dns down")

        with patch("core.mda.arc.arc_dns_txt", _boom):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is True

    def test_settled_absent_key_is_not_dnsfail(self):
        # A sealer that publishes no key is a settled answer: the seal can
        # never validate, so this is a definite untrusted verdict now, not a
        # hold that reaches the same place after the whole deferral window.
        def _empty(name, timeout=5):
            return None

        with patch("core.mda.arc.arc_dns_txt", _empty):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is False

    def test_bad_signature_with_dns_ok_is_not_dnsfail(self):
        # DNS resolves fine but the seal doesn't validate against a DIFFERENT
        # key: a definite failure, must NOT be masked as dnsfail.
        #
        # Returns bytes, as arc_dns_txt does. A str stub never reaches the
        # crypto: dkimpy's parse_tag_value does `tag_list.split(b';')` and
        # raises TypeError, which arc_result's broad except swallows into the
        # same trusted=False/dnsfail=False asserted here — green, while
        # exercising the error path instead of verification.
        def _wrong_key(name, timeout=5):
            return b"v=DKIM1; k=rsa; p=" + _MISMATCHED_PUBKEY_P.encode()

        with patch("core.mda.arc.arc_dns_txt", _wrong_key):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is False


class TestArcDnsLookupBudget:
    """The cap that actually bounds a forged chain's DNS cost.

    ``_MAX_ARC_INSTANCES`` bounds instances, but ``ARC.verify`` looks up both
    the AMS and the AS key per instance, and only the outermost ``d=`` is
    checked against the allowlist — so the inner ones are attacker-chosen
    names that may point at deliberately slow authoritative servers.
    """

    SEALED = base64.b64decode(_SEALED_B64)

    def test_lookups_are_capped_per_message(self):
        """The cap bites before the chain is done with it.

        Pinned to 1 rather than asserting against the real cap: this fixture
        is a single-instance chain that wants two lookups (the AMS key and the
        AS key), so it never approaches 8 and the assertion would hold with
        the cap removed entirely.
        """
        calls = []

        def _counting(name, timeout=5):
            calls.append(name)
            return _PUBKEY_TXT.encode()

        with (
            patch("core.mda.arc._MAX_ARC_DNS_LOOKUPS", 1),
            patch("core.mda.arc.arc_dns_txt", _counting),
        ):
            out = arc.arc_result(self.SEALED, {"relay.example"})

        assert len(calls) == 1
        assert out["trusted"] is False
        assert out["dnsfail"] is False

    def test_exhausted_budget_settles_untrusted_rather_than_holding(self):
        """A refused lookup must not read as "we could not reach DNS".

        dnsfail would hold the message and re-run the same lookups on every
        retry, making the cap amplify the very cost it exists to bound.
        """

        def _never_called(name, timeout=5):
            raise AssertionError("budget should have refused this lookup")

        with (
            patch("core.mda.arc._MAX_ARC_DNS_LOOKUPS", 0),
            patch("core.mda.arc.arc_dns_txt", _never_called),
        ):
            out = arc.arc_result(self.SEALED, {"relay.example"})

        assert out["trusted"] is False
        assert out["dnsfail"] is False


class TestArcDnsTxt:
    """The dnsfunc handed to dkimpy: which TXT value is the key, and which
    failures are settled rather than indeterminate."""

    KEY = _PUBKEY_TXT
    TOKEN = "google-site-verification=Ab1Cd2Ef3"

    @staticmethod
    def _values(*values):
        return patch("core.mda.arc.resolve_txt_values", return_value=list(values))

    def test_picks_the_key_past_an_unrelated_token(self):
        # The token sorts first, as an unspecified TXT ordering may well put
        # it. Taking values[0] returned the token and failed a working sealer.
        with self._values(self.TOKEN, self.KEY):
            assert arc.arc_dns_txt("s._domainkey.relay.example") == self.KEY.encode()

    def test_no_key_record_among_the_values_is_settled(self):
        with self._values(self.TOKEN, "v=spf1 -all"):
            assert arc.arc_dns_txt("s._domainkey.relay.example") is None

    def test_record_without_p_is_not_a_key(self):
        # parse_dkim_tags accepts any tag=value string, so "p" is what
        # separates a key record from a bystander.
        with self._values("v=DKIM1; k=rsa"):
            assert arc.arc_dns_txt("s._domainkey.relay.example") is None

    @pytest.mark.parametrize("settled", [NXDOMAINError, NoAnswerError])
    def test_settled_negative_returns_none(self, settled):
        error = settled("arcsel._domainkey.relay.example", "TXT")
        with patch("core.mda.arc.resolve_txt_values", side_effect=error):
            assert arc.arc_dns_txt("arcsel._domainkey.relay.example") is None

    def test_unformable_name_is_settled_not_indeterminate(self):
        # The name is built from the signature's own s= and d=, so a forged
        # chain can make it unformable. That can never resolve, so raising
        # here would hold the message for the whole deferral window.
        error = InvalidNameError("." * 300, "TXT")
        with patch("core.mda.arc.resolve_txt_values", side_effect=error):
            assert arc.arc_dns_txt("." * 300) is None

    @pytest.mark.parametrize("transient", [ResolutionTimeoutError, ServfailError])
    def test_transient_failure_propagates(self, transient):
        # Must NOT be swallowed into None: arc_result reads the raise as the
        # only signal that the answer is indeterminate rather than absent.
        error = transient("arcsel._domainkey.relay.example", "TXT")
        with patch("core.mda.arc.resolve_txt_values", side_effect=error):
            with pytest.raises(transient):
                arc.arc_dns_txt("arcsel._domainkey.relay.example")
