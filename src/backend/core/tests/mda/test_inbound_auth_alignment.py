"""Tests for the From-alignment check of inbound sender authentication."""

# pylint: disable=missing-function-docstring

import pytest
from jmap_email import parse_email

from core.mda.inbound_auth import (
    VERDICT_FORGED,
    VERDICT_UNVERIFIED,
    check_inbound_authentication,
    from_is_authenticated,
)

RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: rcpt@example.com\r\n"
    b"Subject: Test\r\n"
    b"Message-ID: <abc@example.com>\r\n"
    b"\r\n"
    b"Body\r\n"
)


class TestFromIsAuthenticated:
    """A verified verdict proves the From domain only with alignment."""

    PARSED = parse_email(RAW_EMAIL)  # From: sender@example.com

    @pytest.mark.parametrize("config", [{}, {"inbound_auth": ""}])
    def test_disabled_is_not_authenticated(self, config):
        assert from_is_authenticated(self.PARSED, config, None) is False

    def test_unknown_mode_is_not_authenticated(self):
        # check_inbound_authentication returns None for a typo. That is not
        # a proof.
        config = {"inbound_auth": "nativ"}
        assert from_is_authenticated(self.PARSED, config, None) is False

    @pytest.mark.parametrize("verdict", [VERDICT_UNVERIFIED, VERDICT_FORGED])
    def test_unverified_or_forged_is_not_authenticated(self, verdict):
        config = {"inbound_auth": "native"}
        assert from_is_authenticated(self.PARSED, config, verdict) is False

    def test_native_verified_is_authenticated(self):
        config = {"inbound_auth": "native"}
        assert from_is_authenticated(self.PARSED, config, None) is True

    def test_rspamd_dkim_of_another_domain_without_dmarc(self):
        # The From domain has no DMARC policy and another domain signed the
        # message. The verdict is "verified", but nothing proves the From.
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"options": ["attacker.example:s=mail"]},
                "DMARC_NA": {"score": 0},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, self.PARSED, config, rspamd) is None
        )
        assert from_is_authenticated(self.PARSED, config, None, rspamd) is False

    def test_rspamd_dkim_without_options(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"R_DKIM_ALLOW": {"score": 0.1}}}
        assert from_is_authenticated(self.PARSED, config, None, rspamd) is False

    def test_rspamd_dmarc_pass(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"options": ["esp.example:s=mail"]},
                "DMARC_POLICY_ALLOW": {"score": -0.5},
            }
        }
        assert from_is_authenticated(self.PARSED, config, None, rspamd) is True

    def test_rspamd_dkim_of_the_from_domain(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {
                    "options": ["esp.example:s=mail", "Example.COM:s=2026"]
                },
                "DMARC_NA": {"score": 0},
            }
        }
        assert from_is_authenticated(self.PARSED, config, None, rspamd) is True

    def test_rspamd_dkim_of_a_subdomain_is_not_aligned(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"R_DKIM_ALLOW": {"options": ["mail.example.com:s=a"]}}}
        assert from_is_authenticated(self.PARSED, config, None, rspamd) is False

    def test_authentication_results_needs_dmarc_pass(self):
        config = {"inbound_auth": "authentication-results"}
        dkim_only = {
            "headers": [
                {"name": "Authentication-Results", "value": "mx; dkim=pass"},
                {"name": "Received", "value": "from hop0"},
            ]
        }
        with_dmarc = {
            "headers": [
                {
                    "name": "Authentication-Results",
                    "value": "mx; dkim=pass; dmarc=pass",
                },
                {"name": "Received", "value": "from hop0"},
            ]
        }
        assert from_is_authenticated(dkim_only, config, None) is False
        assert from_is_authenticated(with_dmarc, config, None) is True

    def test_arc_needs_dmarc_pass_from_a_trusted_sealer(self):
        config = {"inbound_auth": "arc", "trusted_arc_sealers": ["relay.example"]}
        arc = {
            "trusted": True,
            "sealer": "relay.example",
            "aar": "i=2; mx; dkim=pass; dmarc=pass",
            "dnsfail": False,
        }
        assert from_is_authenticated({}, config, None, arc=arc) is True
        assert (
            from_is_authenticated(
                {}, config, None, arc={**arc, "aar": "i=2; mx; dkim=pass"}
            )
            is False
        )
        assert from_is_authenticated({}, config, None, arc=None) is False
