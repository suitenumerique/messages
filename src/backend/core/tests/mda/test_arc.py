"""Tests for ARC chain verification (core.mda.arc)."""

# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=protected-access,unused-argument

import base64
from unittest.mock import patch

import dkim

from core.mda import arc


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
        # Real arc_verify runs, but the key lookup blows up (timeout/SERVFAIL/
        # NXDOMAIN all land here): indeterminate, not forged -> dnsfail.
        def _boom(name, timeout=5):
            raise Exception("dns down")

        with patch("core.mda.arc.get_txt", _boom):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is True

    def test_dns_lookup_empty_is_dnsfail(self):
        # An empty/absent key record is treated the same way — we can't tell a
        # genuinely-retired selector from a fresh-publish negative cache.
        def _empty(name, timeout=5):
            return ""

        with patch("core.mda.arc.get_txt", _empty):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is True

    def test_bad_signature_with_dns_ok_is_not_dnsfail(self):
        # DNS resolves fine but the seal doesn't validate against a DIFFERENT
        # key: a definite failure, must NOT be masked as dnsfail.
        def _wrong_key(name, timeout=5):
            return "v=DKIM1; k=rsa; p=" + _MISMATCHED_PUBKEY_P

        with patch("core.mda.arc.get_txt", _wrong_key):
            out = arc.arc_result(self.SEALED, {"relay.example"})
        assert out["trusted"] is False
        assert out["dnsfail"] is False
