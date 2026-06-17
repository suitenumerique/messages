"""Tests for the user-webhook step and the inbound pipeline integration."""

# pylint: disable=protected-access,import-outside-toplevel,missing-function-docstring
# pylint: disable=missing-class-docstring,too-many-lines,too-many-public-methods

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, Set
from unittest.mock import Mock, patch

from django.utils import timezone as dj_timezone

import pytest
import requests as requests_lib

from core import enums, factories, models
from core.mda.dispatch_webhooks import (
    DEFAULT_FORMAT,
    FORMAT_EML,
    FORMAT_JMAP,
    PHASE_AFTER_SPAM,
    PHASE_BEFORE_SPAM,
    _classify_response_body,
    build_jmap_email,
    find_webhook_channels_for_mailbox,
    webhook_steps_for_mailbox,
)
from core.mda.inbound_pipeline import (
    RETRY_MAX_AGE,
    Decision,
    InboundContext,
)
from core.mda.inbound_tasks import process_inbound_message_task
from core.services.ssrf import SSRFValidationError


@dataclass
class _PhaseResult:
    """Aggregated result of running every webhook step for a phase
    against a fresh ``InboundContext``.

    ``decision`` is the most-severe step decision; ``is_spam_override``
    is the final ``ctx.is_spam`` when a step changed it from the initial
    value (``None`` = no step had an opinion); ``labels`` is the set the
    context accumulated.
    """

    decision: Decision = Decision.CONTINUE
    is_spam_override: Optional[bool] = None
    labels: Set[str] = field(default_factory=set)


def dispatch_webhooks(
    *,
    phase,
    mailbox,
    recipient_email,
    parsed_email,
    raw_data,
    is_spam=None,
):
    """Test helper: run every webhook step matching ``phase`` against a
    minimal ``InboundContext`` and return a phase-level aggregate."""
    ctx = InboundContext(
        mailbox=mailbox,
        inbound_message=Mock(id="test-inbound", created_at=dj_timezone.now()),
        recipient_email=recipient_email,
        raw_data=raw_data,
        parsed_email=parsed_email,
        spam_config={},
        is_spam=is_spam,
    )
    initial_is_spam = is_spam
    result = _PhaseResult()
    for step in webhook_steps_for_mailbox(mailbox, phase=phase):
        d = step(ctx)
        if d != Decision.CONTINUE:
            result.decision = d
            break
    if ctx.is_spam != initial_is_spam:
        result.is_spam_override = ctx.is_spam
    result.labels = ctx.labels
    return result


# --- shared fixtures --- #


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    return factories.MailboxFactory()


@pytest.fixture(name="parsed_email")
def fixture_parsed_email(mailbox):
    """A strict-JMAP Email object as ``jmap_email.parse_email`` emits it."""
    return {
        "subject": "Hello",
        "from": [{"email": "sender@example.com", "name": "Sender"}],
        "to": [{"email": str(mailbox), "name": ""}],
        "cc": [],
        "bcc": [],
        "sentAt": "2026-01-01T12:00:00Z",
        "messageId": ["mid@example.com"],
        "inReplyTo": ["parent@example.com"],
        "references": ["a@example.com", "b@example.com"],
        "textBody": [{"partId": "1", "type": "text/plain"}],
        "htmlBody": [{"partId": "2", "type": "text/html"}],
        "attachments": [],
        "hasAttachment": False,
        "bodyValues": {
            "1": {
                "value": "hi there",
                "isEncodingProblem": False,
                "isTruncated": False,
            },
            "2": {
                "value": "<p>hi</p>",
                "isEncodingProblem": False,
                "isTruncated": False,
            },
        },
        "headers": [
            {"name": "From", "value": "Sender <sender@example.com>"},
            {"name": "To", "value": str(mailbox)},
            {"name": "Subject", "value": "Hello"},
        ],
    }


def _make_response(status_code: int, body: bytes = b"") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.content = body
    # The dispatcher now reads the body via iter_content (stream=True)
    # with a size cap. The mock yields the whole body in one chunk —
    # tests that want to exercise the cap can pass a longer ``body``.
    response.iter_content = Mock(return_value=iter([body] if body else []))
    response.close = Mock()
    return response


# ChannelFactory auto-mints this for type=webhook so test channels are
# never silently skipped by the dispatcher's fail-closed signing path.
FACTORY_WEBHOOK_SECRET = "whsec_factory_test"


# --- find_webhook_channels_for_mailbox --- #


@pytest.mark.django_db
class TestFindWebhookChannels:
    def test_finds_mailbox_scoped(self, mailbox):
        ch = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/a",
                "events": ["message.received"],
            },
        )
        assert list(find_webhook_channels_for_mailbox(mailbox)) == [ch]

    def test_finds_maildomain_scoped(self, mailbox):
        ch = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=None,
            maildomain=mailbox.domain,
            settings={
                "url": "https://hook.example.com/d",
                "events": ["message.received"],
            },
        )
        result = list(find_webhook_channels_for_mailbox(mailbox))
        assert result == [ch]

    def test_finds_global_scoped(self, mailbox):
        """Global (instance-wide) webhooks must fire for every mailbox."""
        ch = models.Channel.objects.create(
            name="global-wh",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.GLOBAL,
            settings={
                "url": "https://hook.example.com/g",
                "events": ["message.received"],
            },
        )
        result = list(find_webhook_channels_for_mailbox(mailbox))
        assert result == [ch]

    def test_global_fires_for_other_mailbox_too(self):
        """A global webhook must match an unrelated mailbox."""
        mb_a = factories.MailboxFactory()
        mb_b = factories.MailboxFactory()
        ch = models.Channel.objects.create(
            name="global-wh",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.GLOBAL,
            settings={
                "url": "https://hook.example.com/g",
                "events": ["message.received"],
            },
        )
        assert ch in find_webhook_channels_for_mailbox(mb_a)
        assert ch in find_webhook_channels_for_mailbox(mb_b)

    def test_excludes_other_mailbox(self, mailbox):
        other = factories.MailboxFactory()
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=other,
            settings={
                "url": "https://hook.example.com/x",
                "events": ["message.received"],
            },
        )
        assert not list(find_webhook_channels_for_mailbox(mailbox))

    def test_excludes_other_types(self, mailbox):
        factories.ChannelFactory(
            type="widget", mailbox=mailbox, settings={"config": {"enabled": True}}
        )
        assert not list(find_webhook_channels_for_mailbox(mailbox))


# --- JMAP body builder --- #


class TestBuildJmapEmail:
    """``parse_email`` already emits a strict JMAP Email object, so
    ``build_jmap_email`` is mostly a pass-through: stamp ``receivedAt``,
    strip the parser's project extensions (``_ext`` and per-part
    ``content`` / ``sha256``)."""

    def test_minimal_email_shape(self):
        parsed = {
            "subject": "Hi",
            "from": [{"email": "alice@example.org", "name": "Alice"}],
            "to": [{"email": "bob@example.org", "name": "Bob"}],
            "cc": [],
            "bcc": [],
            "sentAt": "2026-01-01T00:00:00Z",
            "messageId": ["abc@example.org"],
            "inReplyTo": [],
            "references": [],
            "textBody": [{"partId": "1", "type": "text/plain"}],
            "htmlBody": [],
            "attachments": [],
            "hasAttachment": False,
            "bodyValues": {
                "1": {
                    "value": "hello",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                },
            },
            "headers": [{"name": "From", "value": "Alice <alice@example.org>"}],
        }
        email = build_jmap_email(parsed)
        # Strict-JMAP fields pass through unchanged.
        assert email["messageId"] == ["abc@example.org"]
        assert not email["inReplyTo"]
        assert not email["references"]
        assert email["from"] == [{"email": "alice@example.org", "name": "Alice"}]
        assert email["sentAt"] == "2026-01-01T00:00:00Z"
        # ``receivedAt`` is stamped at webhook-fire time.
        assert email["receivedAt"].endswith("Z")
        assert email["headers"] == [
            {"name": "From", "value": "Alice <alice@example.org>"},
        ]
        # bodyValues passes through unchanged.
        assert email["bodyValues"]["1"] == {
            "value": "hello",
            "isEncodingProblem": False,
            "isTruncated": False,
        }
        assert email["textBody"][0]["partId"] == "1"
        assert email["textBody"][0]["type"] == "text/plain"
        assert email["hasAttachment"] is False
        # Storage-time JMAP fields are absent at webhook-fire time.
        for absent in ("id", "blobId", "threadId", "mailboxIds", "keywords"):
            assert absent not in email

    def test_msgid_lists_pass_through(self):
        """``parse_email`` already returns ``Id[]`` lists with the angle
        brackets stripped — the builder passes them straight through."""
        parsed = {
            "subject": "x",
            "from": [{"email": "a@x"}],
            "to": [],
            "cc": [],
            "bcc": [],
            "sentAt": None,
            "messageId": ["m1"],
            "inReplyTo": ["parent@example.org"],
            "references": ["r1@x", "r2@x"],
            "textBody": [],
            "htmlBody": [],
            "attachments": [],
            "hasAttachment": False,
            "bodyValues": {},
            "headers": [],
        }
        email = build_jmap_email(parsed)
        assert email["inReplyTo"] == ["parent@example.org"]
        assert email["references"] == ["r1@x", "r2@x"]

    def test_attachments_stripped_of_parser_extensions(self):
        """Attachment parts keep their JMAP metadata but drop the
        parser's ``content`` bytes and ``sha256`` extension — neither is
        strict JMAP, and raw bytes aren't JSON-serialisable."""
        parsed = {
            "subject": "x",
            "from": [{"email": "a@x"}],
            "to": [],
            "cc": [],
            "bcc": [],
            "sentAt": None,
            "messageId": ["m1"],
            "textBody": [],
            "htmlBody": [],
            "hasAttachment": True,
            "bodyValues": {},
            "headers": [],
            "attachments": [
                {
                    "partId": "att-0",
                    "blobId": None,
                    "type": "image/png",
                    "name": "p.png",
                    "size": 42,
                    "disposition": "attachment",
                    "cid": "img1",
                    "content": b"\x89PNG\r\n",
                    "sha256": "deadbeef",
                },
            ],
        }
        email = build_jmap_email(parsed)
        assert email["hasAttachment"] is True
        assert email["attachments"][0]["type"] == "image/png"
        assert email["attachments"][0]["name"] == "p.png"
        assert email["attachments"][0]["size"] == 42
        assert email["attachments"][0]["cid"] == "img1"
        # Project extensions are stripped — bytes never travel in the body.
        assert "content" not in email["attachments"][0]
        assert "sha256" not in email["attachments"][0]
        assert email["attachments"][0]["blobId"] is None


# --- dispatch_webhooks --- #


@pytest.mark.django_db
class TestDispatchInboundWebhooks:
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_skips_when_no_channels(self, mock_session, mailbox, parsed_email):
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"",
        )
        assert outcome.decision == Decision.CONTINUE
        mock_session.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_skips_channel_with_wrong_phase(self, mock_session, mailbox, parsed_email):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
            },
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"",
        )
        assert outcome.decision == Decision.CONTINUE
        mock_session.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_skips_channel_without_matching_event(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.sent"],
            },
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"",
        )
        assert outcome.decision == Decision.CONTINUE
        mock_session.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_non_blocking_continues_on_5xx(self, mock_session, mailbox, parsed_email):
        """Non-blocking webhooks never influence delivery, even on 5xx."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": False,
            },
        )
        mock_session.return_value.post.return_value = _make_response(500)
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.CONTINUE
        mock_session.return_value.post.assert_called_once()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_5xx(self, mock_session, mailbox, parsed_email):
        """5xx is transient: caller should hold the InboundMessage and retry."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(503)
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_drops_on_4xx(self, mock_session, mailbox, parsed_email):
        """4xx is a definitive receiver rejection — drop the message."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(403)
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.DROP

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_408(self, mock_session, mailbox, parsed_email):
        """408 Request Timeout is conventionally retriable."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(408)
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_429(self, mock_session, mailbox, parsed_email):
        """429 Too Many Requests is rate-limit: back off and retry."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(429)
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_drops_on_ssrf_rejection(
        self, mock_session, mailbox, parsed_email
    ):
        """SSRF rejection is a config error on our side — retrying won't help."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://internal.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.side_effect = SSRFValidationError("blocked")
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.DROP

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_non_blocking_continues_on_ssrf_rejection(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://internal.example.com",
                "events": ["message.received"],
                "blocking": False,
            },
        )
        mock_session.return_value.post.side_effect = SSRFValidationError("blocked")
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.CONTINUE

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_timeout(self, mock_session, mailbox, parsed_email):
        """A connection timeout is transient: retry rather than lose the message."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.side_effect = requests_lib.Timeout("timed out")
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_connection_error(
        self, mock_session, mailbox, parsed_email
    ):
        """Connection refused / DNS failures are transient — retry."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.side_effect = requests_lib.ConnectionError(
            "refused"
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_unknown_exception(
        self, mock_session, mailbox, parsed_email
    ):
        """Unknown transport-level errors land as RETRY — the 7-day cap
        bounds how long we'll keep trying a busted receiver."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.side_effect = RuntimeError("boom")
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_phase_filtering_dispatches_only_matching(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/before",
                "events": ["message.received"],
                "phase": "before_spam",
            },
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/after",
                "events": ["message.received"],
                "phase": "after_spam",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_BEFORE_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"",
        )
        called_url = mock_session.return_value.post.call_args[0][0]
        assert called_url == "https://hook.example.com/before"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_eml_format_sends_raw_body(self, mock_session, mailbox, parsed_email):
        """Default format=eml posts message/rfc822 raw bytes."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw rfc822 bytes",
            is_spam=False,
        )
        kwargs = mock_session.return_value.post.call_args.kwargs
        assert kwargs["data"] == b"raw rfc822 bytes"
        assert "json" not in kwargs
        assert kwargs["headers"]["Content-Type"] == "message/rfc822"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jmap_format_sends_jmap_email_json(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "format": "jmap",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw rfc822 bytes",
            is_spam=False,
        )
        kwargs = mock_session.return_value.post.call_args.kwargs
        # We pre-serialise JSON to bytes so signing covers the exact wire
        # bytes — so the body lands in ``data``, not ``json``.
        assert "json" not in kwargs
        body = json.loads(kwargs["data"].decode("utf-8"))
        # Body IS the JMAP Email object — no wrapping envelope.
        assert body["messageId"] == ["mid@example.com"]
        assert body["from"] == [{"email": "sender@example.com", "name": "Sender"}]
        assert "X-StMsg-Event" not in body
        assert kwargs["headers"]["Content-Type"] == "application/json"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jmap_without_body_skips_body_parts(
        self, mock_session, mailbox, parsed_email
    ):
        """Notification variant: no textBody/htmlBody/bodyValues/attachments."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "format": "jmap_without_body",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        body = json.loads(
            mock_session.return_value.post.call_args.kwargs["data"].decode("utf-8")
        )
        # Envelope addresses + headers ARE present.
        assert body["subject"] == "Hello"
        assert body["from"] == [{"email": "sender@example.com", "name": "Sender"}]
        assert body["messageId"] == ["mid@example.com"]
        assert "headers" in body
        # Body content and attachments are NOT shipped.
        for absent in ("textBody", "htmlBody", "bodyValues", "attachments"):
            assert absent not in body
        # hasAttachment is preserved as a single bool that the receiver
        # may want for filtering.
        assert body["hasAttachment"] is False

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_envelope_headers_set_for_both_formats(
        self, mock_session, mailbox, parsed_email
    ):
        for fmt in (FORMAT_EML, FORMAT_JMAP):
            mock_session.reset_mock()
            models.Channel.objects.filter(
                type=enums.ChannelTypes.WEBHOOK, mailbox=mailbox
            ).delete()
            factories.ChannelFactory(
                type=enums.ChannelTypes.WEBHOOK,
                mailbox=mailbox,
                settings={
                    "url": "https://hook.example.com",
                    "events": ["message.received"],
                    "format": fmt,
                },
            )
            mock_session.return_value.post.return_value = _make_response(200)
            dispatch_webhooks(
                phase=PHASE_AFTER_SPAM,
                mailbox=mailbox,
                recipient_email=str(mailbox),
                parsed_email=parsed_email,
                raw_data=b"raw",
                is_spam=True,
            )
            headers = mock_session.return_value.post.call_args.kwargs["headers"]
            assert headers["X-StMsg-Event"] == "message.received"
            assert headers["X-StMsg-Phase"] == "after_spam"
            assert headers["X-StMsg-Mailbox"] == str(mailbox)
            assert headers["X-StMsg-Recipient"] == str(mailbox)
            assert headers["X-StMsg-Is-Spam"] == "true"
            assert headers["X-StMsg-Message-Id"] == "<mid@example.com>"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_is_spam_header_unknown_when_none(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_BEFORE_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=None,
        )
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert headers["X-StMsg-Is-Spam"] == "unknown"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_invalid_format_skips_dispatch(self, mock_session, mailbox, parsed_email):
        """A row that somehow has settings.format = junk must not silently
        POST in the wrong shape — skip it instead."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "format": "yaml",
            },
        )
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        mock_session.return_value.post.assert_not_called()

    def test_invalid_phase_raises(self, mailbox, parsed_email):
        with pytest.raises(ValueError):
            dispatch_webhooks(
                phase="never",
                mailbox=mailbox,
                recipient_email=str(mailbox),
                parsed_email=parsed_email,
                raw_data=b"",
            )

    def test_constants_default(self):
        assert DEFAULT_FORMAT == FORMAT_EML


@pytest.mark.django_db
class TestWebhookSigning:
    """Every outgoing webhook is HMAC-signed; receivers verify by
    recomputing HMAC-SHA256 over ``f"{ts}.{body}"`` with the channel
    secret and constant-time comparing against
    ``X-StMsg-Webhook-Signature``."""

    SECRET = FACTORY_WEBHOOK_SECRET

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_eml_signature_covers_raw_body(self, mock_session, mailbox, parsed_email):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "format": "eml",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        raw = b"From: a\r\n\r\nbody"
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=raw,
            is_spam=False,
        )
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        ts = headers["X-StMsg-Webhook-Timestamp"]
        sig_header = headers["X-StMsg-Webhook-Signature"]
        scheme, _, sig = sig_header.partition("=")
        assert scheme == "v1"

        expected = hmac.new(
            self.SECRET.encode("utf-8"),
            ts.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(sig, expected)

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jmap_signature_covers_exact_serialised_bytes(
        self, mock_session, mailbox, parsed_email
    ):
        """The body we sign MUST equal the body we POST byte-for-byte —
        otherwise ``requests`` could re-serialise JSON with different
        separators/key order and break the signature."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "format": "jmap",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        kwargs = mock_session.return_value.post.call_args.kwargs
        body_bytes = kwargs["data"]
        assert isinstance(body_bytes, bytes)
        ts = kwargs["headers"]["X-StMsg-Webhook-Timestamp"]
        sig = kwargs["headers"]["X-StMsg-Webhook-Signature"].split("=", 1)[1]
        expected = hmac.new(
            self.SECRET.encode("utf-8"),
            ts.encode("ascii") + b"." + body_bytes,
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(sig, expected)

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_api_key_mode_sends_only_api_key_header(
        self, mock_session, mailbox, parsed_email
    ):
        """auth_method=api_key: send X-StMsg-Api-Key, omit HMAC sig / JWT.

        Sending only the credential the receiver verifies keeps the
        unused presentation off the wire (and out of any receiver-side
        proxy/log)."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "auth_method": "api_key",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        # X-StMsg-Api-Key carries the HMAC-DERIVED value, NOT the root
        # secret — the root never travels on the wire.
        assert headers["X-StMsg-Api-Key"] == channel.get_webhook_api_key()
        assert headers["X-StMsg-Api-Key"] != self.SECRET
        # The HMAC + JWT presentation is NOT sent — that's the whole
        # point of the per-channel auth_method.
        assert "X-StMsg-Webhook-Signature" not in headers
        assert "X-StMsg-Webhook-Timestamp" not in headers
        assert "Authorization" not in headers

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jwt_mode_sends_only_hmac_and_jwt_headers(
        self, mock_session, mailbox, parsed_email
    ):
        """auth_method=jwt (default): HMAC sig + Authorization Bearer,
        but never the raw secret as an API key header."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert "X-StMsg-Webhook-Signature" in headers
        assert headers["Authorization"].startswith("Bearer ")
        # Receivers that verify HMAC never need the raw secret — keep it
        # off the wire so it can't leak through receiver-side logs.
        assert "X-StMsg-Api-Key" not in headers

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_auth_method_fails_closed(
        self, mock_session, mailbox, parsed_email
    ):
        """A row with auth_method missing/unknown is misconfigured —
        the dispatcher fails closed rather than POST with no auth."""
        # Bypass the factory's auto-fill so settings has no auth_method.
        models.Channel.objects.create(
            name="no-auth-method",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
            },
            encrypted_settings={"secret": "whsec_test"},
        )
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_api_key_value_is_derived_not_raw_secret(
        self, mock_session, mailbox, parsed_email
    ):
        """The X-StMsg-Api-Key value MUST NOT be the raw root secret —
        a receiver-side log leak of the API key would otherwise
        compromise HMAC/JWT verification."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "auth_method": "api_key",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        sent = mock_session.return_value.post.call_args.kwargs["headers"][
            "X-StMsg-Api-Key"
        ]
        root = channel.encrypted_settings["secret"]
        assert sent != root, "raw root secret must never travel as the API key"
        assert sent.startswith("whk_"), (
            "API key should use the dedicated prefix so receivers can "
            "distinguish it from the root secret"
        )
        # And the derivation is the stable one exposed by the model.
        assert sent == channel.get_webhook_api_key()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_secret_fails_closed(self, mock_session, mailbox, parsed_email):
        """A webhook channel with no secret is misconfigured — the
        dispatcher must skip it rather than POST an unsigned request."""
        # Build a channel directly so we can leave encrypted_settings
        # empty (factory would otherwise auto-fill the secret).
        models.Channel.objects.create(
            name="no-secret",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
            },
            encrypted_settings={},
        )
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_secret_blocks_when_blocking(
        self, mock_session, mailbox, parsed_email
    ):
        """If the misconfigured channel is also ``blocking``, the
        dispatcher MUST drop the message — better than POSTing an
        unsigned request that any verifying receiver will reject."""
        models.Channel.objects.create(
            name="no-secret-blocking",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
            encrypted_settings={},
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.DROP
        mock_session.return_value.post.assert_not_called()


# --- integration with process_inbound_message_task --- #


@pytest.mark.django_db
class TestPipelineIntegration:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_before_spam_blocking_drops_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
                "blocking": True,
            },
        )
        # 4xx → receiver definitively rejects this message → DROP.
        mock_session.return_value.post.return_value = _make_response(403)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["dropped_by"].endswith(":before_spam")
        mock_check_spam.assert_not_called()
        mock_create_message.assert_not_called()
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_after_spam_blocking_drops_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_check_spam.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(403)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["dropped_by"].endswith(":after_spam")
        mock_check_spam.assert_called_once()
        mock_create_message.assert_not_called()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_after_spam_is_spam_header(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n"
            b"Message-ID: <pipe-1@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
            },
        )
        mock_check_spam.return_value = (True, None, None)
        mock_session.return_value.post.return_value = _make_response(200)
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        # is_spam=True surfaces as the X-StMsg-Is-Spam header.
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert headers["X-StMsg-Is-Spam"] == "true"
        assert headers["X-StMsg-Phase"] == "after_spam"


# Keep dj_timezone import used to silence "imported but unused" if the
# linter wakes up after edits; it's referenced from fixtures via factories.
_ = dj_timezone


# --- response body parsing --- #


class TestClassifyResponseBody:
    """``_classify_response_body`` is the only thing that lets a
    receiver shape delivery beyond accept/drop. Cover the JSON contract
    carefully so a typo in production doesn't silently mis-route mail."""

    def test_empty_body_is_continue(self):
        outcome = _classify_response_body(b"")
        assert outcome.decision == Decision.CONTINUE
        assert outcome.is_spam_override is None
        assert outcome.labels == set()

    def test_non_json_body_is_continue(self):
        outcome = _classify_response_body(b"OK")
        assert outcome.decision == Decision.CONTINUE

    def test_json_array_is_continue(self):
        """Only top-level objects are interpreted as the contract."""
        outcome = _classify_response_body(b'["drop"]')
        assert outcome.decision == Decision.CONTINUE

    def test_non_bytes_input_is_continue(self):
        """Defensive against Mock or str leaking from tests/middleware."""
        outcome = _classify_response_body(None)  # type: ignore[arg-type]
        assert outcome.decision == Decision.CONTINUE

    def test_action_drop_sets_drop(self):
        outcome = _classify_response_body(b'{"action": "drop"}')
        assert outcome.decision == Decision.DROP

    def test_action_accept_is_continue(self):
        outcome = _classify_response_body(b'{"action": "accept"}')
        assert outcome.decision == Decision.CONTINUE

    def test_action_unknown_is_continue(self):
        """An unknown action falls through to CONTINUE — receivers
        adding new verbs we don't know about shouldn't surprise-drop."""
        outcome = _classify_response_body(b'{"action": "quarantine"}')
        assert outcome.decision == Decision.CONTINUE

    def test_is_spam_true_sets_override(self):
        outcome = _classify_response_body(b'{"is_spam": true}')
        assert outcome.decision == Decision.CONTINUE
        assert outcome.is_spam_override is True

    def test_is_spam_false_sets_override_explicitly(self):
        """Distinguish ham (explicit false) from no-opinion (missing)."""
        outcome = _classify_response_body(b'{"is_spam": false}')
        assert outcome.is_spam_override is False

    def test_is_spam_non_bool_is_ignored(self):
        """A receiver returning "true"/"false" as strings is ignored —
        keeps the contract strict."""
        outcome = _classify_response_body(b'{"is_spam": "true"}')
        assert outcome.is_spam_override is None

    def test_labels_uuids_collected(self):
        a = str(uuid.uuid4())
        b = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"labels": [a, b]}).encode("utf-8")
        )
        assert outcome.labels == {a, b}

    def test_labels_non_uuid_strings_skipped(self):
        good = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"labels": [good, "not-a-uuid", "", 42]}).encode("utf-8")
        )
        assert outcome.labels == {good}

    def test_labels_non_list_ignored(self):
        outcome = _classify_response_body(b'{"labels": "spam"}')
        assert outcome.labels == set()

    def test_combined_action_and_labels(self):
        """Drop + labels: drop wins; labels are still collected (caller
        won't apply them since the thread is never created, but the
        merge logic shouldn't lose them either)."""
        good = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"action": "drop", "labels": [good]}).encode("utf-8")
        )
        assert outcome.decision == Decision.DROP
        assert outcome.labels == {good}

    def test_assign_to_emails_lowercased_and_ordered(self):
        outcome = _classify_response_body(
            json.dumps({"assign_to": ["Alice@example.org", "bob@example.org"]}).encode(
                "utf-8"
            )
        )
        # Lowercased, order preserved.
        assert outcome.assign_to == ["alice@example.org", "bob@example.org"]

    def test_assign_to_dedupes_case_insensitive(self):
        outcome = _classify_response_body(
            json.dumps(
                {"assign_to": ["alice@example.org", "ALICE@example.org"]}
            ).encode("utf-8")
        )
        assert outcome.assign_to == ["alice@example.org"]

    def test_assign_to_skips_non_strings_and_non_emails(self):
        """Garbage doesn't pollute the list. Real users go through."""
        outcome = _classify_response_body(
            json.dumps(
                {
                    "assign_to": [
                        "alice@example.org",
                        "",  # empty after strip
                        "no-at-sign",  # no '@'
                        42,  # not a string
                        None,  # not a string
                    ]
                }
            ).encode("utf-8")
        )
        assert outcome.assign_to == ["alice@example.org"]

    def test_assign_to_non_list_ignored(self):
        outcome = _classify_response_body(b'{"assign_to": "alice@example.org"}')
        assert outcome.assign_to == []

    def test_bool_flags_only_true_is_honoured(self):
        """``true``-only semantics — false / missing / non-bool = no opinion."""
        outcome = _classify_response_body(
            json.dumps(
                {
                    "mark_starred": True,
                    "mark_read": True,
                    "mark_trashed": True,
                    "mark_archived": True,
                    "skip_autoreply": True,
                }
            ).encode("utf-8")
        )
        assert outcome.mark_starred is True
        assert outcome.mark_read is True
        assert outcome.mark_trashed is True
        assert outcome.mark_archived is True
        assert outcome.skip_autoreply is True

    def test_bool_flags_false_is_no_op(self):
        """Explicit ``false`` is the same as missing — no opinion. Lets
        a later webhook's ``true`` survive without being veto'd."""
        outcome = _classify_response_body(
            json.dumps(
                {
                    "mark_starred": False,
                    "mark_read": "yes",  # non-bool: dropped
                    "mark_trashed": 1,  # non-bool: dropped
                }
            ).encode("utf-8")
        )
        assert outcome.mark_starred is False
        assert outcome.mark_read is False
        assert outcome.mark_trashed is False

    def test_add_event_im(self):
        outcome = _classify_response_body(
            json.dumps(
                {
                    "add_event": [
                        {"type": "im", "content": "AI flagged: urgent"},
                        {"type": "im", "content": "  "},  # blank → skip
                        {"type": "im"},  # no content → skip
                        {"type": "iframe", "url": "https://x"},  # unknown type → skip
                        "not a dict",  # not a dict → skip
                    ]
                }
            ).encode("utf-8")
        )
        # Only the well-formed IM survived.
        assert outcome.events == [
            {"type": "im", "content": "AI flagged: urgent", "mentions": []}
        ]

    def test_add_event_non_list_ignored(self):
        outcome = _classify_response_body(b'{"add_event": {"type": "im"}}')
        assert outcome.events == []

    def test_reply_draft_template_uuid_canonicalised(self):
        tmpl_id = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"reply_draft": {"template": tmpl_id}}).encode("utf-8")
        )
        assert outcome.reply_draft_template_id == tmpl_id

    def test_reply_draft_non_uuid_template_rejected(self):
        outcome = _classify_response_body(
            b'{"reply_draft": {"template": "not-a-uuid"}}'
        )
        assert outcome.reply_draft_template_id is None

    def test_reply_draft_missing_template_field_rejected(self):
        outcome = _classify_response_body(b'{"reply_draft": {}}')
        assert outcome.reply_draft_template_id is None

    def test_reply_draft_non_object_ignored(self):
        outcome = _classify_response_body(b'{"reply_draft": "template-id"}')
        assert outcome.reply_draft_template_id is None

    def test_oversize_arrays_are_capped(self):
        """A receiver can't flood us with arbitrary numbers of labels /
        assignees / events from one webhook call. Entries past the
        per-action cap are silently dropped at parse time."""
        from core.mda.dispatch_webhooks import (
            MAX_ASSIGN_TO_PER_RESPONSE,
            MAX_EVENTS_PER_RESPONSE,
            MAX_LABELS_PER_RESPONSE,
        )

        labels = [str(uuid.uuid4()) for _ in range(MAX_LABELS_PER_RESPONSE + 10)]
        emails = [f"u{i}@example.org" for i in range(MAX_ASSIGN_TO_PER_RESPONSE + 10)]
        events = [
            {"type": "im", "content": f"#{i}"}
            for i in range(MAX_EVENTS_PER_RESPONSE + 10)
        ]
        outcome = _classify_response_body(
            json.dumps(
                {"labels": labels, "assign_to": emails, "add_event": events}
            ).encode("utf-8")
        )
        assert len(outcome.labels) == MAX_LABELS_PER_RESPONSE
        assert len(outcome.assign_to) == MAX_ASSIGN_TO_PER_RESPONSE
        assert len(outcome.events) == MAX_EVENTS_PER_RESPONSE

    def test_im_content_is_truncated_at_cap(self):
        """A single IM comment is bounded so a misconfigured receiver
        can't flood the timeline with multi-KB blobs per inbound."""
        from core.mda.dispatch_webhooks import MAX_IM_CONTENT_BYTES

        big = "x" * (MAX_IM_CONTENT_BYTES + 1000)
        outcome = _classify_response_body(
            json.dumps({"add_event": [{"type": "im", "content": big}]}).encode("utf-8")
        )
        # Truncated; still landed.
        assert len(outcome.events) == 1
        assert len(outcome.events[0]["content"]) <= MAX_IM_CONTENT_BYTES


# --- WebhookOutcome.merge precedence --- #


# (merge() and WebhookOutcome no longer exist — the pipeline applies
# side effects to InboundContext directly. Multi-step semantics
# (DROP-wins / labels-accumulate / is_spam-last-wins) are exercised by
# TestDispatchActionBody and TestPipelineIntegration below.)


# --- dispatch_webhooks JSON action body --- #


@pytest.mark.django_db
class TestDispatchActionBody:
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_drops_on_action_drop_body(
        self, mock_session, mailbox, parsed_email
    ):
        """HTTP 200 + {"action":"drop"} → DROP (receiver chose to reject)."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"action": "drop"}'
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.DROP

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_is_spam_override_continues(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"is_spam": true}'
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.CONTINUE
        assert outcome.is_spam_override is True

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_non_blocking_ignores_action_body(
        self, mock_session, mailbox, parsed_email
    ):
        """Non-blocking webhooks are fire-and-forget. A receiver's body
        should never affect delivery — protects against a non-blocking
        webhook accidentally returning {"action":"drop"}."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": False,
            },
        )
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"action": "drop", "is_spam": true}'
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.CONTINUE
        assert outcome.is_spam_override is None

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_multi_webhook_drop_wins_and_short_circuits(
        self, mock_session, mailbox, parsed_email
    ):
        """When two blocking webhooks fire, DROP from one stops the chain."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/first",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/second",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        # First call drops, second should never fire.
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"action": "drop"}'
        )
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.DROP
        # Exactly one call — the second channel never fires.
        assert mock_session.return_value.post.call_count == 1

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_response_body_is_capped(self, mock_session, mailbox, parsed_email):
        """A malicious / misconfigured receiver returning a multi-MB
        response must not OOM the worker. We read up to
        ``MAX_RESPONSE_BODY`` bytes via ``iter_content`` and ignore the
        rest."""
        from core.mda.dispatch_webhooks import MAX_RESPONSE_BODY

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "blocking": True,
            },
        )
        # Expose a stream far larger than the cap and count how much of
        # it the reader actually pulls. The reader must stop on its own
        # rather than draining the whole stream — if the cap logic ever
        # regresses, ``consumed`` blows past the bound and this test fails.
        oversize_chunk = b"x" * (MAX_RESPONSE_BODY // 2)
        consumed = {"bytes": 0}

        def _counting_iter(*_args, **_kwargs):
            # 20x the cap worth of chunks; a working reader takes only a
            # couple before stopping.
            for _ in range(40):
                consumed["bytes"] += len(oversize_chunk)
                yield oversize_chunk

        response = _make_response(200)
        response.iter_content = Mock(side_effect=_counting_iter)
        mock_session.return_value.post.return_value = response
        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        # Body was unparseable (all 'x'), so the result is plain CONTINUE.
        assert outcome.decision == Decision.CONTINUE
        # The reader stopped at the cap: it consumed at most one chunk
        # beyond ``MAX_RESPONSE_BODY``, never the whole oversize stream.
        assert consumed["bytes"] <= MAX_RESPONSE_BODY + len(oversize_chunk)
        # And the connection was returned to the pool.
        response.close.assert_called_once()


# --- pipeline integration: RETRY, label apply, antispam override --- #


@pytest.mark.django_db
class TestPipelineRetry:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_5xx_retries_and_keeps_inbound_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """Transient 5xx leaves the InboundMessage row in place for the
        5-minute sweep — no rspamd, no message creation."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(503)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["error"] == "retry"
        assert result["step"].endswith(":before_spam")
        # Row preserved → next sweep can retry.
        assert models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        mock_check_spam.assert_not_called()
        mock_create_message.assert_not_called()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_timeout_retries_and_keeps_inbound_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """A timeout must NOT drop the message — that was the original bug."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
                "blocking": True,
            },
        )
        mock_session.return_value.post.side_effect = requests_lib.Timeout("timed out")

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["error"] == "retry"
        assert models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        mock_check_spam.assert_not_called()
        mock_create_message.assert_not_called()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_retry_past_max_age_drops_with_loud_log(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """An InboundMessage held in retry for >7 days is dropped to
        prevent a broken receiver from pinning the row forever."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        # Backdate past the 7-day cap.
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now()
            - RETRY_MAX_AGE
            - dj_timezone.timedelta(minutes=1)
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(503)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["error"] == "retry_exhausted"
        # Row is gone — bounded retry budget.
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        mock_check_spam.assert_not_called()
        mock_create_message.assert_not_called()


@pytest.mark.django_db
class TestPipelineWebhookAntispam:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_before_spam_is_spam_override_short_circuits_rspamd(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """A before_spam webhook returning {"is_spam": true} replaces
        the rspamd verdict entirely — receivers can reimplement antispam."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "before_spam",
                "blocking": True,
            },
        )
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"is_spam": true}'
        )
        mock_create_message.return_value = Mock(spec=models.Message)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        # rspamd was skipped because the webhook decided.
        mock_check_spam.assert_not_called()
        assert result["is_spam"] is True
        assert mock_create_message.call_args.kwargs["is_spam"] is True

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_after_spam_is_spam_override_replaces_verdict(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """An after_spam webhook can flip rspamd's verdict — e.g. a
        reputation service deciding "actually, this is spam"."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        # rspamd says ham; webhook says spam.
        mock_check_spam.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"is_spam": true}'
        )
        mock_create_message.return_value = Mock(spec=models.Message)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        # The webhook flip wins.
        assert mock_create_message.call_args.kwargs["is_spam"] is True


@pytest.mark.django_db
class TestPipelineWebhookLabels:
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_webhook_label_attached_to_thread(self, mock_session, mock_check_spam):
        """Labels from a blocking webhook are attached to the new thread,
        but only when the UUID resolves to a label in the receiving
        mailbox (unknown UUIDs are skipped, not raised)."""
        mailbox = factories.MailboxFactory()
        good_label = factories.LabelFactory(mailbox=mailbox)
        other_mailbox = factories.MailboxFactory()
        other_label = factories.LabelFactory(mailbox=other_mailbox)
        unknown_id = str(uuid.uuid4())

        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: hello\r\n"
            b"Message-ID: <label-1@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_check_spam.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "labels": [
                        str(good_label.id),
                        str(other_label.id),  # wrong mailbox → skipped
                        unknown_id,  # unknown UUID → skipped
                    ]
                }
            ).encode("utf-8"),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        message = models.Message.objects.get(mime_id="label-1@example.com")
        thread_labels = set(message.thread.labels.values_list("id", flat=True))
        assert good_label.id in thread_labels
        assert other_label.id not in thread_labels


@pytest.mark.django_db
class TestPipelineWebhookAssign:
    """``assign_to`` in the webhook response body resolves OIDC emails
    to users, filters by editor-rights on the thread, and produces one
    ``ThreadEvent ASSIGN`` per webhook channel that asked. Unknown,
    ambiguous, and non-assignable users are silently skipped — delivery
    is never blocked because of an assign hiccup."""

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_assign_to_resolves_email_and_attributes_channel(
        self, mock_session, mock_check_spam
    ):
        mailbox = factories.MailboxFactory()
        editor_user = factories.UserFactory(email="editor@example.org")
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=editor_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )

        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: assign me\r\n"
            b"Message-ID: <assign-ok@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_check_spam.return_value = (False, None, None)
        # Email case differs from User.email to exercise iexact.
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps({"assign_to": ["EDITOR@example.org"]}).encode("utf-8"),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        thread = models.Message.objects.get(mime_id="assign-ok@example.com").thread
        events = list(
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            )
        )
        assert len(events) == 1
        event = events[0]
        # Channel FK preserved.
        assert event.channel_id == channel.id
        # Author intentionally None for webhook-driven assigns.
        assert event.author_id is None
        # Assignee resolved and present.
        assert event.data["assignees"][0]["id"] == str(editor_user.id)
        # And the per-user UserEvent landed (source of truth for
        # "currently assigned").
        assert models.UserEvent.objects.filter(
            user=editor_user,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_assign_to_skips_unknown_ambiguous_and_viewer(
        self, mock_session, mock_check_spam
    ):
        mailbox = factories.MailboxFactory()
        editor_user = factories.UserFactory(email="editor@example.org")
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=editor_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        # Viewer has access but the role isn't assignable.
        viewer_user = factories.UserFactory(email="viewer@example.org")
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=viewer_user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        # Ambiguous: two distinct users sharing the same email (this is
        # storable per the OIDC fallback model — see User.email
        # comment).
        factories.UserFactory(email="dup@example.org")
        factories.UserFactory(email="dup@example.org")

        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: assign mixed\r\n"
            b"Message-ID: <assign-mixed@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_check_spam.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "assign_to": [
                        "editor@example.org",  # OK
                        "viewer@example.org",  # has access but VIEWER → skipped
                        "unknown@example.org",  # no User row → skipped
                        "dup@example.org",  # ≥2 matches → skipped
                    ]
                }
            ).encode("utf-8"),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        thread = models.Message.objects.get(mime_id="assign-mixed@example.com").thread
        # Only the editor lands in the timeline.
        assignees = list(
            models.UserEvent.objects.filter(
                thread=thread, type=enums.UserEventTypeChoices.ASSIGN
            ).values_list("user_id", flat=True)
        )
        assert assignees == [editor_user.id]

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_two_webhooks_each_produce_own_threadevent(
        self, mock_session, mock_check_spam
    ):
        """One ``ThreadEvent ASSIGN`` per blocking webhook that asked,
        each carrying its own ``channel`` FK. Webhooks asking for the
        same user are absorbed by the partial UniqueConstraint, so the
        second ThreadEvent simply ends up empty and returns None."""
        mailbox = factories.MailboxFactory()
        alice = factories.UserFactory(email="alice@example.org")
        bob = factories.UserFactory(email="bob@example.org")
        for u in (alice, bob):
            factories.MailboxAccessFactory(
                mailbox=mailbox,
                user=u,
                role=enums.MailboxRoleChoices.EDITOR,
            )

        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: multi\r\n"
            b"Message-ID: <assign-multi@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        ch_a = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/a",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        ch_b = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/b",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_check_spam.return_value = (False, None, None)
        # Each webhook returns a distinct assignee. The mock fires both
        # in order (dispatcher iterates channels in DB order).
        mock_session.return_value.post.side_effect = [
            _make_response(200, body=b'{"assign_to": ["alice@example.org"]}'),
            _make_response(200, body=b'{"assign_to": ["bob@example.org"]}'),
        ]

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        thread = models.Message.objects.get(mime_id="assign-multi@example.com").thread
        events = list(
            models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.ASSIGN
            ).order_by("created_at")
        )
        # One event per webhook that contributed new assignees.
        assert len(events) == 2
        assert {e.channel_id for e in events} == {ch_a.id, ch_b.id}
        # Both users actually assigned.
        assert set(
            models.UserEvent.objects.filter(
                thread=thread, type=enums.UserEventTypeChoices.ASSIGN
            ).values_list("user_id", flat=True)
        ) == {alice.id, bob.id}


@pytest.mark.django_db
class TestPipelineWebhookFlagActions:
    """Blocking webhooks can flip per-message state flags
    (``star`` / ``mark_read`` / ``mark_trashed`` / ``mark_archived`` /
    ``skip_autoreply``). The pipeline applies them after the message
    + thread land; failures never block delivery."""

    def _send(self, mailbox, mime_id, action_body: bytes):
        """Create a minimal inbound message + after-spam blocking webhook
        channel, point the SSRFSafeSession mock at ``action_body``, and
        run the task. Returns the resulting ``Message``."""
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: action\r\n"
            b"Message-ID: <" + mime_id.encode() + b">\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        with (
            patch("core.mda.inbound_pipeline._call_rspamd") as mock_rspamd,
            patch("core.mda.dispatch_webhooks.SSRFSafeSession") as mock_session,
        ):
            mock_rspamd.return_value = (False, None, None)
            mock_session.return_value.post.return_value = _make_response(
                200, body=action_body
            )
            with patch.object(process_inbound_message_task, "update_state", Mock()):
                process_inbound_message_task.run(str(inbound_message.id))
        return models.Message.objects.get(mime_id=mime_id)

    def test_mark_starred_and_mark_read_set_threadaccess_fields(self):
        mailbox = factories.MailboxFactory()
        message = self._send(
            mailbox,
            "flag-starred@example.com",
            b'{"mark_starred": true, "mark_read": true}',
        )
        access = models.ThreadAccess.objects.get(thread=message.thread, mailbox=mailbox)
        assert access.starred_at is not None
        assert access.read_at is not None

    def test_mark_trashed_and_archived_set_message_fields(self):
        mailbox = factories.MailboxFactory()
        message = self._send(
            mailbox,
            "flag-trash@example.com",
            b'{"mark_trashed": true, "mark_archived": true}',
        )
        assert message.is_trashed is True
        assert message.is_archived is True

    @patch("core.mda.autoreply.try_send_autoreply")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_skip_autoreply_suppresses_autoreply_call(
        self, mock_session, mock_rspamd, mock_autoreply
    ):
        """``skip_autoreply: true`` short-circuits the autoreply path
        entirely — distinct from the ``is_spam=true`` route, which also
        suppresses but for a different reason."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: noreply\r\n"
            b"Message-ID: <flag-skip@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_rspamd.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200, body=b'{"skip_autoreply": true}'
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        mock_autoreply.assert_not_called()


@pytest.mark.django_db
class TestPipelineWebhookAddEvent:
    """``add_event`` persists ``ThreadEvent`` rows attributed to the
    firing channel. Today only ``type=im`` is honoured; unknown types
    are silently skipped at the classifier (the contract stays forward-
    compatible for future ``type=iframe``)."""

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_add_event_im_creates_threadevent(self, mock_session, mock_rspamd):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: comment\r\n"
            b"Message-ID: <flag-event@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_rspamd.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "add_event": [
                        {"type": "im", "content": "AI summary: budget Q4"},
                    ]
                }
            ).encode("utf-8"),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        message = models.Message.objects.get(mime_id="flag-event@example.com")
        events = list(
            models.ThreadEvent.objects.filter(
                thread=message.thread, type=enums.ThreadEventTypeChoices.IM
            )
        )
        assert len(events) == 1
        ev = events[0]
        # Channel FK preserved.
        assert ev.channel_id == channel.id
        # Author intentionally None for webhook-driven IMs.
        assert ev.author_id is None
        assert ev.data == {
            "content": "AI summary: budget Q4",
            "mentions": [],
        }


@pytest.mark.django_db
class TestPipelineWebhookReplyDraft:
    """``reply_draft: {"template": <uuid>}`` materialises a draft reply
    using the autoreply path's shared record helper. The template body
    lands in ``draft_blob`` (the rich-text editor's JSON shape) so the
    user can refine the draft inline before sending — same UI affordance
    as a hand-composed draft."""

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_reply_draft_creates_draft_with_template_body(
        self, mock_session, mock_rspamd
    ):
        mailbox = factories.MailboxFactory()
        template = factories.MessageTemplateFactory(
            mailbox=mailbox,
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            is_active=True,
            html_body="<p>Thanks for your message!</p>",
            text_body="Thanks for your message!",
            raw_body={"type": "doc", "content": [{"type": "paragraph"}]},
        )
        raw_data = (
            b"From: customer@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: I need help\r\n"
            b"Message-ID: <reply-draft@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_rspamd.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps({"reply_draft": {"template": str(template.id)}}).encode(
                "utf-8"
            ),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        inbound = models.Message.objects.get(mime_id="reply-draft@example.com")
        draft = models.Message.objects.filter(
            thread=inbound.thread,
            is_draft=True,
            parent=inbound,
        ).first()
        assert draft is not None
        # Draft is attributed to the firing webhook channel.
        assert draft.channel_id == channel.id
        # Subject auto-prefixed with Re:
        assert draft.subject.lower().startswith("re:")
        # Body lands in draft_blob (editor JSON) — not in blob (the
        # MIME blob the send pipeline produces). That's what lets the
        # user edit it inline.
        assert draft.draft_blob is not None
        assert draft.blob is None
        # And the bytes are exactly the template's raw_body json.
        assert draft.draft_blob.get_content() == template.raw_body.encode("utf-8")

    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_reply_draft_out_of_scope_template_skipped(self, mock_session, mock_rspamd):
        """A template belonging to a different mailbox / maildomain
        must not be usable as a webhook reply_draft source."""
        mailbox = factories.MailboxFactory()
        # Different domain, no mailbox FK back to ours.
        other_mailbox = factories.MailboxFactory()
        template = factories.MessageTemplateFactory(
            mailbox=other_mailbox,
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            is_active=True,
        )
        raw_data = (
            b"From: c@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: nope\r\n"
            b"Message-ID: <draft-oos@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_rspamd.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps({"reply_draft": {"template": str(template.id)}}).encode(
                "utf-8"
            ),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        inbound = models.Message.objects.get(mime_id="draft-oos@example.com")
        # No draft was created — out-of-scope template silently skipped.
        assert not models.Message.objects.filter(
            thread=inbound.thread,
            is_draft=True,
        ).exists()


@pytest.mark.django_db
class TestFinalizeStepIsolation:
    """A failure in one finalize step (labels / assigns / events /
    drafts / flags) must NOT skip the others — the message has already
    landed, and a partial failure on a downstream side effect should
    log loudly rather than swallow other receiver-requested changes."""

    @patch("core.mda.inbound_tasks.apply_pending_assigns")
    @patch("core.mda.inbound_pipeline._call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_assign_failure_does_not_skip_labels(
        self, mock_session, mock_rspamd, mock_apply_assigns
    ):
        mailbox = factories.MailboxFactory()
        label = factories.LabelFactory(mailbox=mailbox)
        user = factories.UserFactory(email="editor@example.org")
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )

        # Force the assigns step to blow up — labels MUST still apply.
        mock_apply_assigns.side_effect = RuntimeError("DB hiccup")

        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: isolation\r\n"
            b"Message-ID: <isolation@example.com>\r\n\r\nbody"
        )
        inbound_message = models.InboundMessage.objects.create(
            mailbox=mailbox, raw_data=raw_data
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "events": ["message.received"],
                "phase": "after_spam",
                "blocking": True,
            },
        )
        mock_rspamd.return_value = (False, None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "labels": [str(label.id)],
                    "assign_to": ["editor@example.org"],
                }
            ).encode("utf-8"),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        # Task reports success (message landed, finalize errors logged).
        assert result["success"] is True
        message = models.Message.objects.get(mime_id="isolation@example.com")
        # Labels still got attached even though assigns raised.
        assert label in list(message.thread.labels.all())
        mock_apply_assigns.assert_called_once()
