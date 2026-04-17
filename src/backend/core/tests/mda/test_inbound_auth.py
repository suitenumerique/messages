"""Tests for inbound sender authentication checks (DKIM / DMARC)."""

from unittest.mock import Mock, patch

from django.test import override_settings

import pytest

from core import factories, models
from core.mda.inbound_auth import (
    VERDICT_FORGED,
    VERDICT_UNVERIFIED,
    check_inbound_authentication,
)
from core.mda.inbound_tasks import process_inbound_message_task
from core.mda.rfc5322 import parse_email_message


RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: rcpt@example.com\r\n"
    b"Subject: Test\r\n"
    b"Message-ID: <abc@example.com>\r\n"
    b"\r\n"
    b"Body\r\n"
)


class TestCheckInboundAuthenticationDisabled:
    """When inbound_auth is absent or empty the check is a no-op."""

    def test_missing_key_returns_none(self):
        assert check_inbound_authentication(RAW_EMAIL, {}, {}) is None

    def test_none_value_returns_none(self):
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": None})
            is None
        )

    def test_empty_string_returns_none(self):
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": ""})
            is None
        )

    def test_unknown_mode_returns_none(self):
        """An unrecognised mode is treated as disabled (with a log warning)."""
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": "wat"})
            is None
        )


class TestCheckInboundAuthenticationNative:
    """Native mode verifies DKIM locally and ignores DMARC."""

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dkim_pass(self, mock_verify):
        mock_verify.return_value = True
        config = {"inbound_auth": "native"}
        assert check_inbound_authentication(RAW_EMAIL, {}, config) is None

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dkim_fail(self, mock_verify):
        mock_verify.return_value = False
        config = {"inbound_auth": "native"}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config)
            == VERDICT_UNVERIFIED
        )

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dkim_error_unverified(self, mock_verify):
        """Transient errors -> cannot verify -> "none" (not forgery)."""
        mock_verify.side_effect = RuntimeError("dns broken")
        config = {"inbound_auth": "native"}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config)
            == VERDICT_UNVERIFIED
        )

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dmarc_header_ignored(self, mock_verify):
        """Native doesn't look at DMARC; passing DKIM alone is enough."""
        mock_verify.return_value = True
        parsed = {
            "headers_blocks": [
                {"authentication-results": ["mx; dmarc=fail"]}
            ]
        }
        config = {"inbound_auth": "native"}
        assert check_inbound_authentication(RAW_EMAIL, parsed, config) is None

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dmarc_rspamd_ignored(self, mock_verify):
        """Native ignores any rspamd_result that was passed in."""
        mock_verify.return_value = True
        rspamd = {"symbols": {"DMARC_POLICY_REJECT": {"score": 5}}}
        config = {"inbound_auth": "native"}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None
        )


class TestCheckInboundAuthenticationRspamd:
    """Rspamd mode reads DKIM/DMARC symbols from the /checkv2 response."""

    def test_dkim_pass_no_dmarc(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {"symbols": {"R_DKIM_ALLOW": {"score": 0.1}}}
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None
        )

    def test_dkim_pass_dmarc_pass(self):
        config = {"inbound_auth": "rspamd"}
        rspamd = {
            "symbols": {
                "R_DKIM_ALLOW": {"score": 0.1},
                "DMARC_POLICY_ALLOW": {"score": 0.1},
            }
        }
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None
        )

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
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None
        )

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
        blocks = []
        for i in range(trust_blocks):
            block = {}
            if i < len(ar_values) and ar_values[i] is not None:
                block["authentication-results"] = ar_values[i]
            blocks.append(block)
        return {"headers_blocks": blocks}

    def test_dkim_pass_no_dmarc(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=pass"]])
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_pass_dmarc_pass(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed(
            [["mx.example.net; dkim=pass; dmarc=pass"]]
        )
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_pass_dmarc_fail_forged(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed(
            [["mx.example.net; dkim=pass; dmarc=fail"]]
        )
        assert (
            check_inbound_authentication(b"", parsed, config) == VERDICT_FORGED
        )

    def test_dkim_pass_dmarc_none(self):
        """dmarc=none -> no policy -> don't require DMARC pass."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed(
            [["mx.example.net; dkim=pass; dmarc=none"]]
        )
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_fail(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx.example.net; dkim=fail"]])
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_dkim_softfail_is_fail(self):
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; dkim=softfail"]])
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_dkim_none_is_unverified(self):
        """dkim=none (no signature) is not a pass -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; dkim=none"]])
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_header_absent_unverified(self):
        """No AR header anywhere -> can't verify -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {"headers_blocks": [{}]}
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_no_dkim_entry_unverified(self):
        """AR present but no dkim= entry -> unknown -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed([["mx; spf=pass"]])
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_untrusted_block_ignored(self):
        """trusted_relays=0 -> only block 0 (our MTA) is trusted."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 0}
        parsed = {
            "headers_blocks": [
                {},  # block 0: no AR from us
                {"authentication-results": ["mx; dkim=pass"]},  # untrusted
            ]
        }
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_trusted_block_used(self):
        """Default trusted_relays=1 -> block 1 is trusted."""
        config = {"inbound_auth": "authentication-results"}
        parsed = {
            "headers_blocks": [
                {},
                {"authentication-results": ["mx; dkim=pass"]},
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) is None

    def test_dkim_fail_dominates_pass_across_values(self):
        """Multiple AR values in one block: fail wins -> 'none'."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = self._parsed(
            [["mx1; dkim=pass", "mx2; dkim=fail"]]
        )
        assert (
            check_inbound_authentication(b"", parsed, config)
            == VERDICT_UNVERIFIED
        )

    def test_single_string_ar_value(self):
        """AR header may be a bare string (single occurrence) rather than list."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {
            "headers_blocks": [
                {"authentication-results": "mx; dkim=pass"}
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) is None


@pytest.mark.django_db
class TestProcessInboundMessageAuthIntegration:
    """End-to-end: a verdict prepends X-StMsg-Sender-Auth with its value."""

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_tasks.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_unverified_verdict_injects_none_header(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )
        mock_auth_check.return_value = VERDICT_UNVERIFIED
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        assert mock_create_message.called
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth: none\r\n")
        parsed = call_kwargs["parsed_email"]
        assert parsed["headers"].get("x-stmsg-sender-auth") == "none"

    @override_settings(SPAM_CONFIG={"inbound_auth": "rspamd"})
    @patch("core.mda.inbound_tasks.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_forged_verdict_injects_fail_header(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )
        mock_auth_check.return_value = VERDICT_FORGED
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth: fail\r\n")
        parsed = call_kwargs["parsed_email"]
        assert parsed["headers"].get("x-stmsg-sender-auth") == "fail"

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_tasks.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_verified_does_not_inject_header(
        self, mock_create_message, mock_auth_check
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )
        mock_auth_check.return_value = None
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        assert not call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth")

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
        }
    )
    @patch("core.mda.inbound_tasks.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_rspamd_response_reused_by_auth_check(
        self, mock_create_message, mock_post
    ):
        """Single rspamd call feeds both spam and auth."""
        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )
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
        assert call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth: none\r\n")

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
        }
    )
    @patch("core.mda.inbound_tasks.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_dmarc_fail_injects_fail_header_end_to_end(
        self, mock_create_message, mock_post
    ):
        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )
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
        assert call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth: fail\r\n")

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "inbound_auth": "rspamd",
            "rules": [{"header_match": "X-Spam:yes", "action": "ham"}],
        }
    )
    @patch("core.mda.inbound_tasks.requests.post")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_rspamd_fetched_on_demand_when_spam_skipped_rspamd(
        self, mock_create_message, mock_post
    ):
        """Hardcoded spam rule short-circuits spam; rspamd still fetched for auth."""
        mailbox = factories.MailboxFactory()
        raw = b"X-Spam: yes\r\n" + RAW_EMAIL
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=raw,
        )
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
        assert not call_kwargs["raw_data"].startswith(b"X-StMsg-Sender-Auth")

    def test_maildomain_override(self):
        """custom_settings.SPAM_CONFIG overrides the global default."""
        maildomain = factories.MailDomainFactory(
            custom_settings={
                "SPAM_CONFIG": {"inbound_auth": "rspamd"},
            }
        )
        config = maildomain.get_spam_config()
        assert config.get("inbound_auth") == "rspamd"

    def test_header_injection_propagates_to_stmsg(self):
        """After prepending, the parser exposes the header via x-stmsg-*."""
        tagged = b"X-StMsg-Sender-Auth: fail\r\n" + RAW_EMAIL
        parsed = parse_email_message(tagged)
        assert parsed["headers"].get("x-stmsg-sender-auth") == "fail"
