"""Tests for spam processing with rspamd."""

# pylint: disable=too-many-lines

from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils import timezone

import pytest
import requests
from jmap_email import parse_email

from core import factories, models
from core.mda.inbound import deliver_inbound_message
from core.mda.inbound_pipeline import (
    Decision,
    InboundContext,
    _make_rspamd_step,
)
from core.mda.inbound_tasks import (
    process_inbound_message_task,
    process_inbound_messages_queue_task,
)
from core.mda.spam import call_rspamd, check_hardcoded_rules


def _queue_inbound(mailbox, content=b"raw", envelope=None):
    """Create a queued InboundMessage backed by a blob (blob-at-ingest)."""
    blob = models.Blob.objects.create_blob(
        content=content, content_type="message/rfc822"
    )
    return models.InboundMessage.objects.create(
        mailbox=mailbox, blob=blob, envelope=envelope or {}
    )


@pytest.mark.django_db
class TestDeliverInboundMessageQueueing:
    """Test that deliver_inbound_message queues messages instead of creating them directly."""

    @patch("core.mda.inbound_tasks.process_inbound_message_task.delay")
    def test_deliver_inbound_message_queues_message(self, mock_task_delay):
        """Test that deliver_inbound_message creates an InboundMessage in the queue."""
        mailbox = factories.MailboxFactory()
        recipient_email = f"{mailbox.local_part}@{mailbox.domain.name}"

        parsed_email = {
            "subject": "Test Email",
            "from": [{"email": "sender@example.com", "name": "Test Sender"}],
            "to": [{"email": recipient_email}],
            "sentAt": timezone.now(),
        }
        raw_data = (
            b"From: sender@example.com\r\nTo: "
            + recipient_email.encode()
            + b"\r\n\r\nTest"
        )

        result = deliver_inbound_message(recipient_email, parsed_email, raw_data)

        assert result is True

        # Check that an InboundMessage was created, with the bytes committed
        # to a blob at ingest (blob-at-ingest; no inline plaintext).
        inbound_message = models.InboundMessage.objects.get(mailbox=mailbox)
        assert inbound_message.blob is not None
        assert inbound_message.get_raw_bytes() == raw_data
        assert inbound_message.mailbox == mailbox

        # Check that the task was queued
        mock_task_delay.assert_called_once_with(str(inbound_message.id))

        # Check that no Message was created yet
        assert models.Message.objects.count() == 0

    def test_deliver_inbound_message_handles_duplicate(self):
        """Test that duplicate messages are handled correctly."""
        mailbox = factories.MailboxFactory()
        recipient_email = f"{mailbox.local_part}@{mailbox.domain.name}"

        # Create an existing message
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        mime_id = "test-message-id@example.com"
        factories.MessageFactory(thread=thread, mime_id=mime_id)

        parsed_email = {
            "messageId": [mime_id],
            "subject": "Test Email",
            "from": [{"email": "sender@example.com"}],
            "to": [{"email": recipient_email}],
        }
        raw_data = b"Test email"

        result = deliver_inbound_message(recipient_email, parsed_email, raw_data)

        assert result is True

        # Check that no InboundMessage was created for duplicate
        assert models.InboundMessage.objects.count() == 0


@pytest.mark.django_db
class TestRspamdSpamCheck:
    """Test rspamd spam checking functionality."""

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.requests.post")
    def test_check_spam_with_rspamd_spam(self, mock_post):
        """Test that spam messages are correctly identified."""
        spam_config = {"rspamd_url": "http://rspamd:8010/_api"}
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "reject",
            "score": 20.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Spam email content"
        action, error, rspamd_result = call_rspamd(raw_data, spam_config)

        # call_rspamd returns the raw action; the action -> verdict mapping
        # lives in the step (see TestRspamdStepFailureHandling).
        assert action == "reject"
        assert error is None
        assert rspamd_result is not None
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://rspamd:8010/_api/checkv2"
        assert call_args[1]["data"] == raw_data

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.requests.post")
    def test_check_spam_with_rspamd_not_spam(self, mock_post):
        """Test that non-spam messages are correctly identified."""
        spam_config = {"rspamd_url": "http://rspamd:8010/_api"}
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 5.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Legitimate email content"
        action, error, _rspamd_result = call_rspamd(raw_data, spam_config)

        assert action == "no action"
        assert error is None

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.requests.post")
    def test_call_rspamd_forwards_smtp_envelope_headers(self, mock_post):
        """The SMTP envelope is forwarded via rspamd's scan headers, and
        CR/LF in attacker-influenced fields (HELO/hostname) is stripped."""
        spam_config = {"rspamd_url": "http://rspamd:8010/_api"}
        mock_response = Mock()
        mock_response.json.return_value = {"action": "no action", "score": 0.0}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        envelope = {
            "origin": "mta",
            "mail_from": "sender@example.com",
            "rcpt_to": "rcpt@example.com",
            "ip": "203.0.113.7",
            "helo": "evil\r\nX-Injected: 1",
            "hostname": "mail.example.com",
        }
        call_rspamd(b"content", spam_config, envelope=envelope)

        headers = mock_post.call_args[1]["headers"]
        assert headers["From"] == "sender@example.com"
        assert headers["Rcpt"] == "rcpt@example.com"
        assert headers["IP"] == "203.0.113.7"
        assert headers["Hostname"] == "mail.example.com"
        # CR/LF stripped: no header injection into the rspamd request.
        assert headers["Helo"] == "evilX-Injected: 1"
        assert "\r" not in headers["Helo"] and "\n" not in headers["Helo"]

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.requests.post")
    def test_call_rspamd_omits_absent_envelope_headers(self, mock_post):
        """Fields we don't have (widget/internal mail has no HELO/rDNS) are
        omitted rather than sent empty, which would skew scoring."""
        spam_config = {"rspamd_url": "http://rspamd:8010/_api"}
        mock_response = Mock()
        mock_response.json.return_value = {"action": "no action", "score": 0.0}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        call_rspamd(
            b"content",
            spam_config,
            envelope={"origin": "widget", "mail_from": "u@example.com"},
        )

        headers = mock_post.call_args[1]["headers"]
        assert headers["From"] == "u@example.com"
        assert "Helo" not in headers
        assert "Hostname" not in headers
        assert "IP" not in headers
        assert "Rcpt" not in headers

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://rspamd:8010/_api",
            "rspamd_auth": "Bearer token123",
        }
    )
    @patch("core.mda.spam.requests.post")
    def test_check_spam_with_rspamd_auth_header(self, mock_post):
        """Test that Authorization header is included when configured."""
        spam_config = {
            "rspamd_url": "http://rspamd:8010/_api",
            "rspamd_auth": "Bearer token123",
        }
        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 5.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        raw_data = b"Email content"
        call_rspamd(raw_data, spam_config)

        call_args = mock_post.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer token123"

    @override_settings(SPAM_CONFIG={})
    def test_check_spam_without_rspamd_config(self):
        """When rspamd isn't configured the call returns ``action=None``
        (= "no opinion"), so the step falls through without deciding a
        verdict instead of silently marking the message as ham."""
        spam_config = {}
        raw_data = b"Email content"
        action, error, rspamd_result = call_rspamd(raw_data, spam_config)

        assert action is None
        assert error is None
        assert rspamd_result is None

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.requests.post")
    def test_check_spam_with_rspamd_error(self, mock_post):
        """On error, ``action`` is None and the error is surfaced separately;
        the step turns that into a RETRY (never fails open)."""
        spam_config = {"rspamd_url": "http://rspamd:8010/_api"}
        mock_post.side_effect = requests.exceptions.RequestException("Connection error")

        raw_data = b"Email content"
        action, error, rspamd_result = call_rspamd(raw_data, spam_config)

        assert action is None
        assert error is not None
        assert rspamd_result is None

    @override_settings(
        SPAM_CONFIG={
            "rspamd_url": "http://global:8010/_api",
            "rspamd_auth": "Bearer global",
        }
    )
    @patch("core.mda.spam.requests.post")
    def test_check_spam_with_maildomain_override(self, mock_post):
        """Test that maildomain custom_settings can override SPAM_CONFIG."""
        # Create a maildomain with custom spam config
        maildomain = factories.MailDomainFactory(
            custom_settings={
                "SPAM_CONFIG": {
                    "rspamd_url": "http://domain:8010/_api",
                    "rspamd_auth": "Bearer domain",
                }
            }
        )
        mailbox = factories.MailboxFactory(domain=maildomain)

        mock_response = Mock()
        mock_response.json.return_value = {
            "action": "no action",
            "score": 5.0,
            "required_score": 15.0,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        spam_config = mailbox.domain.get_spam_config()
        raw_data = b"Email content"
        call_rspamd(raw_data, spam_config)

        # Verify that the domain-specific URL was used
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://domain:8010/_api/checkv2"
        assert call_args[1]["headers"]["Authorization"] == "Bearer domain"


@pytest.mark.django_db
class TestHardcodedSpamRules:  # pylint: disable=too-many-public-methods
    """Test hardcoded spam rules functionality."""

    def test_check_spam_with_hardcoded_rules_spam(self):
        """Test that spam messages are correctly identified by hardcoded rules."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:yes", "action": "spam"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_ham(self):
        """Test that ham messages are correctly identified by hardcoded rules."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: no

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:no", "action": "ham"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is False

    def test_check_spam_with_hardcoded_rules_no_match(self):
        """Test that messages without matching rules return None."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: maybe

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:yes", "action": "spam"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is None

    def test_check_spam_with_hardcoded_rules_no_rules(self):
        """Test that messages with no rules return None."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is None

    def test_check_spam_with_hardcoded_rules_multiple_headers(self):
        """Test that when multiple header values exist, the first one in the block is used (most recent).

        Headers are prepended by relays, so the first value in the block is the most recent.
        """
        # Email with multiple X-Spam headers (simulating relay prepending)
        raw_email = b"""X-Spam: no
X-Spam: yes
From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:no", "action": "ham"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is False

    def test_check_spam_with_hardcoded_rules_case_insensitive(self):
        """Test that header matching is case-insensitive."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: YES

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:yes", "action": "spam"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_value_with_colon(self):
        """Test that header values containing colons are handled correctly."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Custom: value:with:colons

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match": "X-Custom:value:with:colons", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_header_match_regex_spam(self):
        """Test that spam messages are correctly identified by header_match_regex."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: this is spam content

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match_regex": "X-Spam:.*spam.*", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_header_match_regex_spam_no_fullmatch(self):
        """Test that spam messages are correctly identified by header_match_regex."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: this is spam content

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match_regex": "X-Spam:spam", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is None

    def test_check_spam_with_hardcoded_rules_header_match_regex_case_insensitive(self):
        """Test that header_match_regex matching is case-insensitive."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: THIS IS SPAM CONTENT

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match_regex": "X-Spam:.*spam.*", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_header_match_regex_pattern(self):
        """Test that regex patterns work correctly with header_match_regex."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam-Level: 5

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match_regex": "X-Spam-Level:[4-9]", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_regex_uppercase_metacharacter_preserved(self):
        """Regression: the regex pattern must NOT be lowercased. Lowercasing
        would flip an uppercase metaclass like ``\\D`` (non-digit) into ``\\d``
        (digit) and silently change what the rule matches."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Token: abc

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        # ``\D+`` matches the non-digit "abc"; if the pattern were lowercased
        # to ``\d+`` it would not match and the rule would be missed.
        spam_config = {
            "rules": [{"header_match_regex": r"X-Token:\D+", "action": "spam"}]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_regex_invalid_pattern_is_skipped(self):
        """Regression: a malformed regex must be skipped (logged), not raise
        and abort the whole hardcoded-rule check."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Token: abc
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [
                # Unbalanced "[" — an invalid pattern that must be skipped.
                {"header_match_regex": "X-Token:[", "action": "spam"},
                # A later valid rule still gets evaluated.
                {"header_match": "X-Spam:yes", "action": "spam"},
            ]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_default_action(self):
        """Test that default action is spam when not specified."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [
                {"header_match": "X-Spam:yes"}  # No action specified
            ]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_reject_action(self):
        """Test that reject action is treated as spam."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:yes", "action": "reject"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_no_action(self):
        """Test that no action is treated as ham."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: no

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {"rules": [{"header_match": "X-Spam:no", "action": "no action"}]}

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is False

    def test_check_spam_with_hardcoded_rules_multiple_rules_order(self):
        """Test that multiple rules are evaluated in order and first match wins."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: maybe
X-Custom: ham

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [
                # First rule: doesn't match (different header value)
                {"header_match": "X-Spam:yes", "action": "spam"},
                # Second rule: matches and should win (returns ham)
                {"header_match": "X-Custom:ham", "action": "ham"},
                # Third rule: also matches but shouldn't be evaluated (would return spam)
                {"header_match": "X-Custom:ham", "action": "spam"},
            ]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        # Should return False (ham) because second rule matched first
        # Third rule should not be evaluated
        assert result is False

    def test_check_spam_with_hardcoded_rules_multiple_rules_first_match_wins(self):
        """Test that the first matching rule stops evaluation."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: yes

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [
                # First rule: matches and should win (returns spam)
                {"header_match": "X-Spam:yes", "action": "spam"},
                # Second rule: also matches but shouldn't be evaluated (would return ham)
                {"header_match": "X-Spam:yes", "action": "ham"},
            ]
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        # Should return True (spam) because first rule matched
        # Second rule should not be evaluated
        assert result is True

    def test_check_spam_with_hardcoded_rules_x_spam_single_relay(self):
        """Test that X-Spam header from relay is trusted when relay adds its own header."""
        # Email with our MTA's Received header and a relay's Received header + X-Spam
        raw_email = b"""Received: from our_mta.example.com (our_mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our_mta_id;
    Mon, 1 Jan 2024 12:02:00 +0000
X-Spam: Yes
Received: from relay.example.com (relay.example.com [1.2.3.4])
    by mail.example.com with SMTP id abc123;
    Mon, 1 Jan 2024 12:00:00 +0000
From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match": "X-Spam:Yes", "action": "spam"}],
            "trusted_relays": 1,  # Trust block 0 and block 1
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        assert result is True

    def test_check_spam_with_hardcoded_rules_x_spam_raw_email_relay_no_header(self):
        """Test X-Spam header is ignored with raw email when relay doesn't add header.

        When a relay adds a Received header but doesn't add its own X-Spam header,
        the sender's X-Spam header (which comes after the Received header) should be ignored.
        """
        # Raw email where:
        # 1. Our MTA prepends Received header (first Received - ours)
        # 2. Relay prepends Received header (second Received - from relay)
        # 3. Sender's original email has X-Spam: Yes (at bottom, after Received headers)
        # 4. Relay does NOT add its own X-Spam header
        # When parsed, this creates:
        # Block 0: Received (our MTA)
        # Block 1: Received (relay) - no X-Spam
        # Block 2: X-Spam: Yes (from sender) - not in trusted blocks
        raw_email = b"""Received: from our_mta.example.com (our_mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our_mta_id;
    Mon, 1 Jan 2024 12:02:00 +0000
Received: from relay.example.com (relay.example.com [1.2.3.4])
    by mail.example.com with SMTP id abc123;
    Mon, 1 Jan 2024 12:00:00 +0000
From: sender@example.com
To: recipient@example.com
Subject: Test Email
X-Spam: Yes

This is a test email body.
"""

        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match": "X-Spam:Yes", "action": "spam"}],
            "trusted_relays": 1,  # Trust block 0 and block 1
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        # Should return None (no match) because sender's X-Spam is in block 2, not in trusted blocks
        assert result is None

    @pytest.mark.parametrize("has_source_header", [True, False])
    def test_check_spam_with_hardcoded_rules_x_spam_raw_email_with_relay(
        self, has_source_header
    ):
        """Test X-Spam header handling with raw email that went through a relay.

        When an email goes through an SMTP relay chain, we should trust only the
        last relay's X-Spam header. The sender's X-Spam header should be ignored.
        Headers are prepended, so the most recent header is at the top.
        """
        # Raw email where headers are prepended by relays (most recent at top):
        # 1. Our MTA prepends: Received (first Received - ours)
        # 2. Relay prepends: X-Spam: No and Received (second Received - from relay)
        # 3. Original email from sender: X-Spam: Yes (at bottom - oldest, after second Received)
        raw_email = (
            b"""Received: from our-mta.example.com (our-mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our123;
    Mon, 1 Jan 2024 12:00:00 +0000
X-Spam: No
Received: from relay1.example.com (relay1.example.com [1.2.3.4])
    by mail.example.com with SMTP id abc123;
    Mon, 1 Jan 2024 12:00:00 +0000
From: sender@example.com
To: recipient@example.com
Subject: Test Email
"""
            + (b"X-Spam: Yes" if has_source_header else b"")
            + b"""

This is a test email body.
"""
        )

        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [{"header_match": "X-Spam:No", "action": "ham"}],
            "trusted_relays": 1,  # Trust block 0 and block 1
        }

        result = check_hardcoded_rules(parsed_email, spam_config)

        # Should match the first X-Spam header (No from last relay), not the sender's (Yes)
        assert result is False  # ham = False (not spam)

    @pytest.mark.parametrize(
        "trusted_relays_setting, expected_result", [(0, None), (1, False), (2, False)]
    )
    def test_check_spam_with_hardcoded_rules_trusted_relays(
        self, trusted_relays_setting, expected_result
    ):
        """Test that trusted_relays setting correctly limits which headers are considered.

        The function uses the first (most recent) match found in trusted blocks.
        With trusted_relays=0: Only block 0 (our MTA) is checked, no X-Spam -> None
        With trusted_relays=1: Blocks 0-1 are checked, finds "Ham" in block 1 -> False (ham)
        With trusted_relays=2: Blocks 0-2 are checked, finds "Ham" in block 1 (first match) -> False (ham)
        """
        # Raw email with 3 Received headers (our MTA, relay1, relay2) and X-Spam headers
        # Headers are prepended, so order in raw email is:
        # Our MTA's Received (first)
        # X-Spam: Ham (from relay2)
        # Relay2's Received (second)
        # X-Spam: Spam (from relay1)
        # Relay1's Received (third)
        # X-Spam: SenderSpam (from sender)
        # When parsed, this creates blocks:
        # Block 0: Our MTA's Received
        # Block 1: X-Spam: Ham + Relay2's Received
        # Block 2: X-Spam: Spam + Relay1's Received
        # Block 3: X-Spam: SenderSpam + original headers
        raw_email = b"""Received: from our_mta.example.com (our_mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our_mta_id;
    Mon, 1 Jan 2024 12:02:00 +0000
X-Spam: Ham
Received: from relay2.example.com (relay2.example.com [5.6.7.8])
    by mail.example.com with SMTP id def456;
    Mon, 1 Jan 2024 12:01:00 +0000
X-Spam: Spam
Received: from relay1.example.com (relay1.example.com [1.2.3.4])
    by mail.example.com with SMTP id abc123;
    Mon, 1 Jan 2024 12:00:00 +0000
X-Spam: SenderSpam
From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        spam_config = {
            "rules": [
                {"header_match": "X-Spam:Ham", "action": "ham"},
                {"header_match": "X-Spam:Spam", "action": "spam"},
                {"header_match": "X-Spam:SenderSpam", "action": "spam"},
            ],
            "trusted_relays": trusted_relays_setting,
        }

        result = check_hardcoded_rules(parsed_email, spam_config)
        assert result is expected_result

    def test_default_ignores_sender_injected_ham_header(self):
        """By default (no trusted_relays) a sender cannot whitelist itself.

        The attacker prepends one fake ``Received`` to lift a forged
        ``X-Spam: Ham`` (action="ham") into what used to be the trusted slice.
        Block 0 is our own MTA's Received (no X-Spam); the forged ham sits in
        block 1. With the secure default of trusted_relays=0 only block 0 is
        trusted, so the forged ham rule does NOT match and the result is None
        (falls through to real spam scanning) — not False (ham bypass).
        """
        raw_email = b"""Received: from our_mta.example.com (our_mta.example.com [10.0.0.1])
    by mail.example.com with SMTP id our_mta_id;
    Mon, 1 Jan 2024 12:02:00 +0000
X-Spam: Ham
Received: from forged.attacker.example (forged [6.6.6.6])
    by mail.example.com with SMTP id forged_id;
    Mon, 1 Jan 2024 12:01:00 +0000
From: sender@example.com
To: recipient@example.com
Subject: Test Email

This is a test email body.
"""
        parsed_email = parse_email(raw_email)
        # No trusted_relays key -> defaults to 0 (only our own block trusted).
        spam_config = {
            "rules": [
                {"header_match": "X-Spam:Ham", "action": "ham"},
            ],
        }

        result = check_hardcoded_rules(parsed_email, spam_config)
        assert result is None  # forged ham not honoured


@pytest.mark.django_db
class TestProcessInboundMessageTask:
    """Test the process_inbound_message_task."""

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_process_inbound_message_task_spam(
        self, mock_create_message, mock_check_spam
    ):
        """Test processing an inbound message that is spam."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Spam content"

        inbound_message = _queue_inbound(mailbox, raw_data)

        mock_check_spam.return_value = ("reject", None, None)  # spam
        mock_create_message.return_value = True

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        assert result["is_spam"] is True

        # Check that message was created with is_spam=True
        mock_create_message.assert_called_once()
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["is_spam"] is True

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.call_rspamd")
    def test_discard_action_blackholes_message(self, mock_call):
        """rspamd 'discard' drops the message end-to-end: no Message is
        created, the queue row is consumed, and the task reports it dropped."""
        mailbox = factories.MailboxFactory()
        inbound_message = _queue_inbound(
            mailbox, b"From: s@example.com\r\nSubject: t\r\n\r\nbody"
        )
        mock_call.return_value = ("discard", None, {"action": "discard"})

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["dropped_by"] == "rspamd"
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        assert models.Message.objects.count() == 0

        # Check that inbound message was deleted after successful processing
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_process_inbound_message_task_not_spam(
        self, mock_create_message, mock_check_spam
    ):
        """Test processing an inbound message that is not spam."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Legitimate content"

        inbound_message = _queue_inbound(mailbox, raw_data)

        mock_check_spam.return_value = ("no action", None, None)  # ham
        mock_create_message.return_value = True

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        assert result["is_spam"] is False

        # Check that message was created with is_spam=False
        call_kwargs = mock_create_message.call_args[1]
        assert call_kwargs["is_spam"] is False

    @override_settings(SPAM_CONFIG={"rspamd_url": "http://rspamd:8010/_api"})
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    def test_process_inbound_message_task_failure(
        self, mock_create_message, mock_check_spam
    ):
        """Test handling of failures in message creation."""
        mailbox = factories.MailboxFactory()
        raw_data = b"Test content"

        inbound_message = _queue_inbound(mailbox, raw_data)

        mock_check_spam.return_value = ("no action", None, None)
        mock_create_message.return_value = False  # Creation failed

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is False
        assert "error" in result

        # Check that inbound message was kept for retry (not deleted)
        inbound_message.refresh_from_db()
        assert inbound_message.error_message is not None


@pytest.mark.django_db
class TestProcessInboundMessagesQueueTask:
    """Test the process_inbound_messages_queue_task."""

    @patch("core.mda.inbound_tasks.process_inbound_message_task.delay")
    def test_process_inbound_messages_queue_task(self, mock_task_delay):
        """Test that the queue processing task triggers individual message processing."""
        mailbox = factories.MailboxFactory()

        # Create multiple pending messages older than 5 minutes (for retry processing)
        old_time = timezone.now() - timezone.timedelta(minutes=6)
        for i in range(3):
            # Distinct bytes per row so blob dedup doesn't collapse them.
            inbound_message = _queue_inbound(mailbox, f"Content {i}".encode())
            # Update created_at to make it old enough for retry
            models.InboundMessage.objects.filter(id=inbound_message.id).update(
                created_at=old_time
            )

        # Call the bound task directly using .run() method
        with patch.object(process_inbound_messages_queue_task, "update_state", Mock()):
            result = process_inbound_messages_queue_task.run(10)

        assert result["success"] is True
        assert result["processed"] == 3
        assert result["total"] == 3
        assert mock_task_delay.call_count == 3


@pytest.mark.django_db
class TestRspamdStepFailureHandling:
    """rspamd never fails open: an error holds the message for retry
    (deferring it) rather than delivering it unchecked."""

    def _ctx(self, spam_config):
        mailbox = factories.MailboxFactory()
        inbound = _queue_inbound(mailbox, b"raw")
        return InboundContext(
            mailbox=mailbox,
            inbound_message=inbound,
            recipient_email=str(mailbox),
            raw_data=b"raw",
            parsed_email={},
            spam_config=spam_config,
        )

    @patch("core.mda.spam.call_rspamd")
    def test_error_holds_for_retry(self, mock_call):
        """On rspamd error, never fail open — hold the message for retry."""
        spam_config = {"rspamd_url": "http://rspamd:11334"}
        # On error call_rspamd returns action=None + an error message.
        mock_call.return_value = (None, "connection refused", None)

        decision = _make_rspamd_step(spam_config)(self._ctx(spam_config))

        # Never fail open — hold, don't deliver unchecked.
        assert decision == Decision.RETRY

    @patch("core.mda.spam.call_rspamd")
    def test_not_configured_continues(self, mock_call):
        """When rspamd isn't configured, continue without a verdict."""
        # rspamd absent is "no opinion" (action=None, no error) → keep moving.
        mock_call.return_value = (None, None, None)
        ctx = self._ctx({})

        decision = _make_rspamd_step({})(ctx)

        assert decision == Decision.CONTINUE
        assert ctx.is_spam is None

    # The single source of truth for the rspamd action -> outcome mapping.
    @pytest.mark.parametrize(
        "action,decision,is_spam,marker",
        [
            ("no action", Decision.CONTINUE, False, None),
            ("add header", Decision.CONTINUE, False, "possible"),
            ("rewrite subject", Decision.CONTINUE, False, "likely"),
            ("quarantine", Decision.CONTINUE, True, None),
            ("reject", Decision.CONTINUE, True, None),
            ("greylist", Decision.RETRY, None, None),
            ("soft reject", Decision.RETRY, None, None),
            ("discard", Decision.DROP, None, None),
            # An unknown/future action is delivered to the inbox (not Junk),
            # unmarked — safest default.
            ("telephone", Decision.CONTINUE, False, None),
        ],
    )
    @patch("core.mda.spam.call_rspamd")
    def test_action_mapping(self, mock_call, action, decision, is_spam, marker):
        """Every rspamd action maps to (decision, Junk verdict, spam marker):
        isolate → Junk, flag → inbox+marker, defer → RETRY, discard → DROP."""
        spam_config = {"rspamd_url": "http://rspamd:11334"}
        mock_call.return_value = (action, None, {"action": action})
        ctx = self._ctx(spam_config)

        assert _make_rspamd_step(spam_config)(ctx) == decision
        assert ctx.is_spam is is_spam
        assert ctx.postmark.get("spam") == marker
