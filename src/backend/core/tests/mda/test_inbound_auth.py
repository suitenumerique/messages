"""Tests for inbound sender authentication checks (DKIM / DMARC)."""

# pylint: disable=missing-function-docstring,too-many-public-methods

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
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": None}) is None
        )

    def test_empty_string_returns_none(self):
        assert check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": ""}) is None

    def test_unknown_mode_returns_none(self):
        """An unrecognised mode is treated as disabled (with a log warning)."""
        assert (
            check_inbound_authentication(RAW_EMAIL, {}, {"inbound_auth": "wat"}) is None
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
        assert check_inbound_authentication(RAW_EMAIL, {}, config) == VERDICT_UNVERIFIED

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dkim_error_unverified(self, mock_verify):
        """Transient errors -> cannot verify -> "none" (not forgery)."""
        mock_verify.side_effect = RuntimeError("dns broken")
        config = {"inbound_auth": "native"}
        assert check_inbound_authentication(RAW_EMAIL, {}, config) == VERDICT_UNVERIFIED

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dmarc_header_ignored(self, mock_verify):
        """Native doesn't look at DMARC; passing DKIM alone is enough."""
        mock_verify.return_value = True
        parsed = {"headers_blocks": [{"authentication-results": ["mx; dmarc=fail"]}]}
        config = {"inbound_auth": "native"}
        assert check_inbound_authentication(RAW_EMAIL, parsed, config) is None

    @patch("core.mda.inbound_auth.verify_message_dkim")
    def test_dmarc_rspamd_ignored(self, mock_verify):
        """Native ignores any rspamd_result that was passed in."""
        mock_verify.return_value = True
        rspamd = {"symbols": {"DMARC_POLICY_REJECT": {"score": 5}}}
        config = {"inbound_auth": "native"}
        assert check_inbound_authentication(RAW_EMAIL, {}, config, rspamd) is None


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
        parsed = {"headers_blocks": [{}]}
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
            "headers_blocks": [
                {},  # block 0: no AR from us
                {"authentication-results": ["mx; dkim=pass"]},  # untrusted
            ]
        }
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

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
        parsed = self._parsed([["mx1; dkim=pass", "mx2; dkim=fail"]])
        assert check_inbound_authentication(b"", parsed, config) == VERDICT_UNVERIFIED

    def test_single_string_ar_value(self):
        """AR header may be a bare string (single occurrence) rather than list."""
        config = {"inbound_auth": "authentication-results", "trusted_relays": 1}
        parsed = {"headers_blocks": [{"authentication-results": "mx; dkim=pass"}]}
        assert check_inbound_authentication(b"", parsed, config) is None


class TestCheckInboundAuthenticationResultsScrubbing:
    """`dkim=`/`dmarc=` literals inside CFWS comments or quoted strings must
    NOT be honoured — they're attacker-controlled free text."""

    CONFIG = {"inbound_auth": "authentication-results", "trusted_relays": 1}

    @staticmethod
    def _parsed(ar_value):
        return {"headers_blocks": [{"authentication-results": [ar_value]}]}

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
    def test_rspamd_response_reused_by_auth_check(self, mock_create_message, mock_post):
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

    @override_settings(SPAM_CONFIG={"inbound_auth": "native"})
    @patch("core.mda.inbound_tasks.parse_email_message")
    @patch("core.mda.inbound_tasks.check_inbound_authentication")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_reparse_failure_after_prepend_keeps_views_in_sync(
        self, mock_create_message, mock_auth_check, mock_parse
    ):
        """If re-parsing the prepended bytes blows up, the prepend is dropped.

        Otherwise raw_data (with the new header) and parsed_email (without it)
        diverge, and `Message.get_parsed_data()` later returns {} for the
        whole message because the same bytes fail to parse at display time.
        """
        # First call: initial parse succeeds. Second: re-parse after prepend fails.
        original_parsed = {
            "headers": {"from": "a@b"},
            "headers_blocks": [{}],
            "from": {"email": "a@b"},
        }
        mock_parse.side_effect = [original_parsed, RuntimeError("flanker exploded")]
        mock_auth_check.return_value = VERDICT_UNVERIFIED
        mock_create_message.return_value = True

        mailbox = factories.MailboxFactory()
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox,
            raw_data=RAW_EMAIL,
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        call_kwargs = mock_create_message.call_args[1]
        # Prepend was reverted: raw_data is the original bytes.
        assert call_kwargs["raw_data"] == RAW_EMAIL
        # parsed_email is the original parse, also without the header.
        assert call_kwargs["parsed_email"] is original_parsed
