"""Tests for the user-webhook step and the inbound pipeline integration."""

# pylint: disable=protected-access,import-outside-toplevel,missing-function-docstring
# pylint: disable=missing-class-docstring,too-many-lines,too-many-public-methods
# pylint: disable=use-implicit-booleaness-not-comparison

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, Set
from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils import timezone as dj_timezone

import jwt
import pytest
import requests as requests_lib

from core import enums, factories, models
from core.mda import outbound
from core.mda.dispatch_webhooks import (
    DEFAULT_FORMAT,
    FORMAT_EML,
    FORMAT_JMAP,
    PHASE_AFTER_SPAM,
    PHASE_BEFORE_SPAM,
    WEBHOOK_CONNECT_TIMEOUT,
    WEBHOOK_TIMEOUT,
    UserWebhookStep,
    _classify_response_body,
    _dispatch_webhook,
    _HttpResult,
    build_jmap_email,
    dispatch_webhook_task,
    find_webhook_channels_for_mailbox,
    load_cached_webhook_results,
    persist_cached_webhook_results,
    webhook_steps_for_mailbox,
)
from core.mda.inbound_pipeline import (
    DEFERRAL_MAX_AGE,
    Decision,
    InboundContext,
)
from core.mda.inbound_tasks import (
    process_inbound_message_task,
    process_inbound_messages_queue_task,
    purge_abandoned_inbound_messages_task,
)
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


def _queue_inbound(mailbox, content=b"raw", **extra):
    """Create a queued InboundMessage backed by a blob (blob-at-ingest)."""
    blob = models.Blob.objects.create_blob(
        content=content, content_type="message/rfc822"
    )
    return models.InboundMessage.objects.create(mailbox=mailbox, blob=blob, **extra)


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


def _logged_config_error(mock_logger, channel_id) -> bool:
    """True iff the patched module ``logger`` recorded a config-skip ERROR
    for ``channel_id`` (the "deliver past it; fix or disable" message).

    Asserting on the patched logger rather than ``caplog`` because the
    ``core`` logger does not propagate to the root handler caplog attaches
    to, so caplog never sees these records. The log uses lazy ``%``
    formatting, so the channel id rides in ``call.args``, not the
    pre-rendered string.
    """
    for call in mock_logger.error.call_args_list:
        fmt = call.args[0] if call.args else ""
        if "fix or disable" in fmt and channel_id in call.args:
            return True
    return False


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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
        # Strict JMAP Id[] contract: these must be empty lists, not just
        # any falsey value.
        assert email["inReplyTo"] == []
        assert email["references"] == []
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
        # Storage-time JMAP fields are absent when no persisted Message is
        # passed — i.e. the blocking, pre-creation path.
        for absent in ("id", "blobId", "threadId", "mailboxIds", "keywords"):
            assert absent not in email

    def test_storage_ids_present_only_when_provided(self):
        """``id`` / ``threadId`` are stamped only when the persisted Message
        exists (the post-creation ``message.delivered`` path passes them);
        ``blobId`` / ``mailboxIds`` / ``keywords`` stay absent regardless."""
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
            "attachments": [],
            "hasAttachment": False,
            "bodyValues": {},
            "headers": [],
        }
        email = build_jmap_email(parsed, message_id="msg-uuid", thread_id="thr-uuid")
        assert email["id"] == "msg-uuid"
        assert email["threadId"] == "thr-uuid"
        for absent in ("blobId", "mailboxIds", "keywords"):
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
                "trigger": "message.inbound",
                "auth_method": "jwt",
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
    def test_skips_channel_with_unknown_trigger(
        self, mock_session, mailbox, parsed_email
    ):
        """A channel whose trigger isn't a known WebhookTrigger fails closed
        — it builds no step and never fires (e.g. a not-yet-supported event
        like ``message.sent``)."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.sent",
                "auth_method": "jwt",
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
    def test_non_blocking_does_no_inline_io(self, mock_session, mailbox, parsed_email):
        """A non-blocking webhook never influences delivery and never
        touches the network on the inbound worker — it's recorded during
        the pipeline and fired from a task after the Message exists."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
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
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_5xx(self, mock_session, mailbox, parsed_email):
        """5xx is transient: caller should hold the InboundMessage and retry."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
    def test_blocking_retries_on_4xx(self, mock_session, mailbox, parsed_email):
        """A webhook error never drops the email: 4xx is held for RETRY.
        Only an explicit {"action": "drop"} on a 2xx drops the message."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        assert outcome.decision == Decision.RETRY

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_408(self, mock_session, mailbox, parsed_email):
        """408 Request Timeout is conventionally retriable."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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

    @patch("core.mda.dispatch_webhooks.logger")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_ssrf_rejection_continues(
        self, mock_session, mock_logger, mailbox, parsed_email
    ):
        """SSRF rejection at dispatch is a config error retry can't fix
        (create-time validation already rejects internal/unresolvable URLs,
        so this means a DNS rebind or a hand-edited row). Rather than stall
        the whole scope's inbound for 48h, deliver the mail past the broken
        webhook (CONTINUE) and log at ERROR so an admin fixes/disables it."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://internal.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        assert _logged_config_error(mock_logger, channel.id)

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_timeout(self, mock_session, mailbox, parsed_email):
        """A connection timeout is transient: retry rather than lose the message."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        """Unknown transport-level errors land as RETRY — the 48-hour
        deferral window bounds how long we'll keep trying a busted
        receiver."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.inbound",
                "auth_method": "jwt",
            },
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/after",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        assert "X-StMsg-Trigger" not in body
        assert kwargs["headers"]["Content-Type"] == "application/json"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jmap_metadata_skips_body_parts(self, mock_session, mailbox, parsed_email):
        """Notification variant: no textBody/htmlBody/bodyValues/attachments."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
                "format": "jmap_metadata",
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
                    "trigger": "message.delivering",
                    "auth_method": "jwt",
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
            assert headers["X-StMsg-Trigger"] == "message.delivering"
            assert headers["X-StMsg-Mailbox"] == str(mailbox)
            assert headers["X-StMsg-Mailbox-Id"] == str(mailbox.id)
            assert headers["X-StMsg-Recipient"] == str(mailbox)
            assert headers["X-StMsg-Is-Spam"] == "true"
            # The message-id is NOT a header — every body format already
            # carries it (messageId in jmap, raw Message-ID: in eml).
            assert "X-StMsg-Message-Mime-Id" not in headers
            # The instance URL header is opt-in — absent unless configured.
            assert "X-StMsg-Instance" not in headers

    @override_settings(INSTANCE_URL="https://messages-public-url.example.com")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_instance_header_set_when_configured(
        self, mock_session, mailbox, parsed_email
    ):
        # message.delivering is the blocking, after-spam trigger that fires
        # in PHASE_AFTER_SPAM (message.delivered fires post-persist, not here).
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        assert headers["X-StMsg-Instance"] == "https://messages-public-url.example.com"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_is_spam_header_unknown_when_none(
        self, mock_session, mailbox, parsed_email
    ):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
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
        assert headers["X-StMsg-Is-Spam"] == "pending"

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_invalid_format_skips_dispatch(self, mock_session, mailbox, parsed_email):
        """A row that somehow has settings.format = junk must not silently
        POST in the wrong shape — skip it instead."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
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
    """``auth_method=jwt`` carries a single HS256 JWT in
    ``Authorization: Bearer``; receivers verify it with the channel secret
    and it binds the exact posted body via the ``body_sha256`` claim."""

    SECRET = FACTORY_WEBHOOK_SECRET

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_eml_jwt_binds_raw_body(self, mock_session, mailbox, parsed_email):
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        token = headers["Authorization"].split(" ", 1)[1]
        claims = jwt.decode(token, self.SECRET, algorithms=["HS256"])
        # For eml the posted body IS the raw bytes; the JWT binds them.
        assert claims["body_sha256"] == hashlib.sha256(raw).hexdigest()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jmap_jwt_binds_exact_serialised_bytes(
        self, mock_session, mailbox, parsed_email
    ):
        """The body the JWT binds MUST equal the body we POST byte-for-byte —
        otherwise ``requests`` could re-serialise JSON with different
        separators/key order and break verification."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        token = kwargs["headers"]["Authorization"].split(" ", 1)[1]
        claims = jwt.decode(token, self.SECRET, algorithms=["HS256"])
        assert claims["body_sha256"] == hashlib.sha256(body_bytes).hexdigest()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_api_key_mode_sends_bearer_derived_key(
        self, mock_session, mailbox, parsed_email
    ):
        """auth_method=api_key: present the derived key as an opaque
        ``Authorization: Bearer`` token (uniform with jwt, auto-redacted by
        proxies/logs). The Bearer value is the HMAC-derived key, NOT the root
        secret — the root never travels on the wire."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        assert headers["Authorization"] == f"Bearer {channel.get_webhook_api_key()}"
        assert headers["Authorization"] != f"Bearer {self.SECRET}"
        # The legacy custom header is gone.
        assert "X-StMsg-Api-Key" not in headers

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jwt_mode_sends_only_bearer(self, mock_session, mailbox, parsed_email):
        """auth_method=jwt (default): a single ``Authorization: Bearer`` JWT
        and nothing else — no separate signature/timestamp headers."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        # A JWT has two dots (header.payload.signature); the api_key Bearer
        # is an opaque ``whk_`` token — this is how a receiver-agnostic check
        # would tell them apart, though receivers know their configured method.
        token = headers["Authorization"].split(" ", 1)[1]
        assert token.count(".") == 2
        # The old dual-signing headers are gone — the JWT is the only proof.
        assert "X-StMsg-Webhook-Signature" not in headers
        assert "X-StMsg-Webhook-Timestamp" not in headers

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_auth_method_fails_closed(
        self, mock_session, mailbox, parsed_email
    ):
        """A row with auth_method missing is misconfigured — the dispatcher
        fails closed (no POST) rather than sign with no method. A *blocking*
        trigger is used so the step actually reaches the inline signing path;
        a non-blocking trigger would never sign inline and pass trivially."""
        # Bypass the factory's auto-fill so settings has no auth_method.
        models.Channel.objects.create(
            name="no-auth-method",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        """The api_key Bearer token MUST NOT be the raw root secret —
        a receiver-side log leak of the API key would otherwise
        compromise JWT verification on other receivers."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        auth = mock_session.return_value.post.call_args.kwargs["headers"][
            "Authorization"
        ]
        sent = auth.split(" ", 1)[1]  # strip "Bearer "
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
                "trigger": "message.delivered",
                "auth_method": "jwt",
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

    @patch("core.mda.dispatch_webhooks.logger")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_secret_continues_when_blocking(
        self, mock_session, mock_logger, mailbox, parsed_email
    ):
        """A misconfigured (secret-less) ``blocking`` channel can't sign
        the POST. Re-minting the secret is the fix, but a 48h retry can't
        do that — so the dispatcher delivers the mail past the broken
        webhook (CONTINUE) and logs at ERROR, rather than stalling the
        scope's inbound. It still never POSTs an unsigned request."""
        channel = models.Channel.objects.create(
            name="no-secret-blocking",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        assert outcome.decision == Decision.CONTINUE
        # Never POSTs an unsigned request.
        mock_session.return_value.post.assert_not_called()
        assert _logged_config_error(mock_logger, channel.id)

    @pytest.mark.parametrize("config_error", ["secret", "auth_method", "ssrf"])
    @patch("core.mda.dispatch_webhooks.logger")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_config_errors_continue_and_log_error(
        self, mock_session, mock_logger, mailbox, parsed_email, config_error
    ):
        """Every config-error a 48h retry can never fix — missing secret,
        unknown auth_method, or an SSRF-rejected URL — makes a BLOCKING
        webhook deliver the mail past it (CONTINUE) and log at ERROR.
        (Transient failures still RETRY — covered separately. Missing-url is
        filtered before the step is built, so it can't reach the blocking
        path — covered via ``_dispatch_webhook`` directly below.)"""
        settings = {
            "url": "https://hook.example.com",
            "trigger": "message.delivering",
            "auth_method": "jwt",
        }
        encrypted = {"secret": "whsec_test"}
        if config_error == "secret":
            encrypted = {}
        elif config_error == "auth_method":
            settings["auth_method"] = "bogus"  # not a valid WebhookAuthMethod

        channel = models.Channel.objects.create(
            name=f"cfg-err-{config_error}",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings=settings,
            encrypted_settings=encrypted,
        )
        if config_error == "ssrf":
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
        assert _logged_config_error(mock_logger, channel.id)
        if config_error != "ssrf":
            # The secret/auth_method errors are caught before any POST.
            mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.logger")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_missing_url_continues_and_logs_error(
        self, mock_session, mock_logger, mailbox
    ):
        """A url-less blocking channel reaches ``_dispatch_webhook`` only via
        a direct call (``webhook_steps_for_mailbox`` filters url-less
        channels before building a step). Exercise the branch directly:
        CONTINUE past it + ERROR, never a POST."""
        channel = models.Channel.objects.create(
            name="no-url",
            type=enums.ChannelTypes.WEBHOOK,
            scope_level=enums.ChannelScopeLevel.MAILBOX,
            mailbox=mailbox,
            settings={"trigger": "message.delivering", "auth_method": "jwt"},
            encrypted_settings={"secret": "whsec_test"},
        )
        result = _dispatch_webhook(
            channel=channel,
            mailbox=mailbox,
            is_spam=False,
            recipient_email=str(mailbox),
            content_type="message/rfc822",
            body_bytes=b"raw",
            blocking=True,
        )
        assert result.decision == Decision.CONTINUE
        assert _logged_config_error(mock_logger, channel.id)
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_jwt_bearer_token_verifies_and_binds_body(
        self, mock_session, mailbox, parsed_email
    ):
        """The ``Authorization: Bearer`` JWT must actually verify with the
        channel secret (HS256) and carry the documented claims, including
        ``body_sha256`` bound to the exact bytes POSTed — a receiver
        relying on the standard JWT verify path depends on this."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"From: a\r\n\r\nbody",
            is_spam=False,
        )
        kwargs = mock_session.return_value.post.call_args.kwargs
        body_bytes = kwargs["data"]
        auth = kwargs["headers"]["Authorization"]
        assert auth.startswith("Bearer ")
        token = auth.split(" ", 1)[1]

        # Decoding with the right secret succeeds (signature + exp valid).
        claims = jwt.decode(token, self.SECRET, algorithms=["HS256"])
        assert claims["iss"] == "messages-webhook"
        assert claims["exp"] == claims["iat"] + 300
        assert claims["cid"] == str(channel.id)
        assert claims["jti"]  # replay nonce present
        # The JWT binds to the exact posted bytes.
        assert claims["body_sha256"] == hashlib.sha256(body_bytes).hexdigest()

        # A wrong secret must fail the signature check — the token is
        # genuinely keyed, not merely well-formed.
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, "wrong-secret", algorithms=["HS256"])

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_rotation_changes_signing_secret(self, mock_session, mailbox, parsed_email):
        """``rotate_secret`` invalidates the old secret immediately: the
        next dispatch's JWT verifies with the NEW secret and no longer
        verifies with the OLD one."""
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
                "format": "eml",
            },
        )
        raw = b"From: a\r\n\r\nbody"
        old_secret = channel.encrypted_settings["secret"]

        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=raw,
            is_spam=False,
        )

        # Rotate: a fresh secret is persisted; the dispatcher re-reads the
        # channel from the DB on the next sweep.
        new_secret = channel.rotate_secret()
        assert new_secret != old_secret

        mock_session.return_value.post.reset_mock()
        mock_session.return_value.post.return_value = _make_response(200)
        dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=raw,
            is_spam=False,
        )
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        token = headers["Authorization"].split(" ", 1)[1]

        # Verifies with the NEW secret...
        jwt.decode(token, new_secret, algorithms=["HS256"])
        # ...and no longer with the OLD one.
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, old_secret, algorithms=["HS256"])


# --- integration with process_inbound_message_task --- #


@pytest.mark.django_db
class TestPipelineIntegration:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_before_spam_blocking_retries_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
            },
        )
        # 4xx is a webhook error, not an explicit drop → hold for RETRY.
        mock_session.return_value.post.return_value = _make_response(403)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["error"] == "retry"
        assert result["step"].endswith(":before_spam")
        mock_check_spam.assert_not_called()
        mock_create_message.assert_not_called()
        # The email is NOT dropped — its row is kept for the next sweep.
        assert models.InboundMessage.objects.filter(id=inbound_message.id).exists()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_after_spam_blocking_retries_message(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("no action", None, None)
        # 4xx is a webhook error, not an explicit drop → hold for RETRY.
        mock_session.return_value.post.return_value = _make_response(403)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["error"] == "retry"
        assert result["step"].endswith(":after_spam")
        mock_check_spam.assert_called_once()
        mock_create_message.assert_not_called()

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("reject", None, None)
        mock_session.return_value.post.return_value = _make_response(200)
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        # is_spam=True surfaces as the X-StMsg-Is-Spam header.
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert headers["X-StMsg-Is-Spam"] == "true"
        assert headers["X-StMsg-Trigger"] == "message.delivering"

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
    def test_creation_failure_retries_then_abandons(self, mock_rspamd, mock_create):
        """A message that parses but can never be created is held for a
        bounded retry, then abandoned — it must not loop (re-firing the
        pipeline + webhooks) forever."""
        mock_rspamd.return_value = ("no action", None, None)
        mock_create.return_value = None  # creation always fails
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: s@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: t\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)

        # Within the deferral window → held for retry, row kept.
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))
        assert result["error"] == "retry"
        assert models.InboundMessage.objects.filter(id=inbound_message.id).exists()

        # Aged past the window → abandoned. The row is KEPT (its raw bytes
        # are the only copy of the mail) but stamped terminally failed so
        # the sweep stops re-running the pipeline + webhooks on it.
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now() - DEFERRAL_MAX_AGE - DEFERRAL_MAX_AGE
        )
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))
        assert result["error"] == "abandoned"
        assert models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        inbound_message.refresh_from_db()
        # Terminally marked via the typed field; error_message keeps the reason.
        assert inbound_message.abandoned_at is not None
        assert inbound_message.error_message


# --- non-blocking dispatch isolation --- #


@pytest.mark.django_db
class TestNonBlockingDispatch:
    """Non-blocking webhooks are recorded during the pipeline and fired
    from a Celery task after the Message exists — the task renders the
    payload from the durable ``Message.blob`` (no snapshot, nothing large
    on the broker), keeping the inbound worker free of webhook I/O."""

    def _ctx(self, mailbox, parsed_email, is_spam=False):
        return InboundContext(
            mailbox=mailbox,
            inbound_message=Mock(id="im", created_at=dj_timezone.now()),
            recipient_email=str(mailbox),
            raw_data=b"From: s@example.com\r\nTo: x\r\nSubject: t\r\n\r\nbody",
            parsed_email=parsed_email,
            spam_config={},
            is_spam=is_spam,
        )

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_non_blocking_records_pending_webhook(
        self, mock_session, mailbox, parsed_email
    ):
        # The step records the channel (with phase-time is_spam) and does
        # NO network I/O — the actual send happens later from the task.
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        ctx = self._ctx(mailbox, parsed_email, is_spam=False)

        for step in webhook_steps_for_mailbox(mailbox, phase=PHASE_AFTER_SPAM):
            assert step(ctx) == Decision.CONTINUE

        assert ctx.pending_webhooks == [(channel.id, False)]
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.dispatch_webhook_task")
    def test_dispatch_recorded_webhooks_enqueues_one_task_each(
        self, mock_task, mailbox
    ):
        from core.mda.dispatch_webhooks import dispatch_recorded_webhooks

        message = factories.MessageFactory(raw_mime=b"raw mime")
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        dispatch_recorded_webhooks(
            message,
            mailbox,
            [(c1, False), (c2, None)],
        )

        assert mock_task.delay.call_count == 2
        assert mock_task.delay.call_args_list[0][0] == (
            str(message.id),
            str(c1),
            str(mailbox.id),
            False,
        )
        assert mock_task.delay.call_args_list[1][0] == (
            str(message.id),
            str(c2),
            str(mailbox.id),
            None,
        )

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dispatch_webhook_task_posts_signed(self, mock_session, mailbox):
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)

        # A delivered message always belongs to the recipient mailbox via a
        # ThreadAccess on its thread; the dispatcher re-checks that ownership.
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(thread=thread, raw_mime=b"raw mime")
        dispatch_webhook_task(
            str(message.id),
            str(channel.id),
            str(mailbox.id),
            False,
        )

        mock_session.return_value.post.assert_called_once()
        # The signed body is the message blob content, rendered at task init.
        assert mock_session.return_value.post.call_args.kwargs["data"] == b"raw mime"
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert headers["X-StMsg-Trigger"] == "message.delivered"
        # Signed at send time (jwt auth_method) — a single Bearer JWT.
        assert headers["Authorization"].startswith("Bearer ")
        # Fired post-persist, so the platform's Message/Thread ids ride along
        # for receiver-side API callbacks.
        assert headers["X-StMsg-Message-Id"] == str(message.id)
        assert headers["X-StMsg-Thread-Id"] == str(message.thread_id)

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dispatch_skips_message_not_owned_by_mailbox(self, mock_session, mailbox):
        # A stale/mismatched task pointing at a message that does NOT belong to
        # this mailbox (no ThreadAccess for it) must not leak that message's
        # body: the dispatcher skips it, no POST.
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        # Message whose thread is owned by a *different* mailbox.
        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=factories.MailboxFactory(),
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        message = factories.MessageFactory(thread=other_thread, raw_mime=b"secret")
        dispatch_webhook_task(
            str(message.id),
            str(channel.id),
            str(mailbox.id),
            False,
        )
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dispatch_webhook_task_no_ops_when_channel_gone(
        self, mock_session, mailbox
    ):
        # Channel id that doesn't exist → best-effort no-op, no POST, no raise.
        message = factories.MessageFactory(raw_mime=b"raw mime")
        dispatch_webhook_task(
            str(message.id),
            str(uuid.uuid4()),
            str(mailbox.id),
            False,
        )
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dispatch_webhook_task_skips_when_message_gone(self, mock_session, mailbox):
        # The source message is gone before the task ran (e.g. deleted) →
        # re-validation at init fails closed: no POST, no guessed payload.
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        dispatch_webhook_task(
            str(uuid.uuid4()),
            str(channel.id),
            str(mailbox.id),
            False,
        )
        mock_session.return_value.post.assert_not_called()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dispatch_webhook_task_swallows_send_errors(self, mock_session, mailbox):
        # Non-blocking is fire-and-forget: a 5xx, a transport failure, or a
        # receiver body must never surface as an exception or side effect.
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        message = factories.MessageFactory(raw_mime=b"raw mime")
        args = (
            str(message.id),
            str(channel.id),
            str(mailbox.id),
            False,
        )

        # 5xx with a drop-shaped body — ignored, no raise.
        mock_session.return_value.post.return_value = _make_response(
            500, body=b'{"action": "drop"}'
        )
        dispatch_webhook_task(*args)

        # Transport failure — swallowed, no raise.
        mock_session.return_value.post.side_effect = SSRFValidationError("blocked")
        dispatch_webhook_task(*args)


# --- internal (mailbox-to-mailbox) delivery --- #


@pytest.mark.django_db
class TestInternalDeliveryWebhooks:
    """Internal mail (one local mailbox to another) must fire the
    recipient's ``message.inbound`` webhook exactly like external mail.

    To a webhook consumer an internal email should be indistinguishable
    from an external one — same event, same envelope headers. The
    sender and recipient may be fully unrelated tenants (different
    domains on the same instance), so the recipient's webhook outcome
    (drop / retry / failure) must NOT leak back into the sender's
    delivery status: the sender sees ``SENT_INTERNAL`` the moment the
    message is handed off to the recipient's async pipeline, and the webhook
    plays out on the recipient's side.
    """

    def _build_internal_message(self, sender_mailbox, recipient_email):
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=sender_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        sender_contact = factories.ContactFactory(mailbox=sender_mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            is_sender=True,
            subject="Internal hello",
        )
        raw_mime = (
            f"From: {sender_contact.email}\r\n"
            f"To: {recipient_email}\r\n"
            "Subject: Internal hello\r\n"
            "Message-ID: <internal-1@example.com>\r\n\r\nbody"
        ).encode()
        message.blob = factories.BlobFactory(
            mailbox=sender_mailbox,
            content=raw_mime,
            content_type="message/rfc822",
        )
        message.save()
        recipient_contact = factories.ContactFactory(
            mailbox=sender_mailbox, email=recipient_email
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=recipient_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )
        return message

    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_internal_delivery_fires_recipient_webhook(
        self, mock_session, _mock_rspamd
    ):
        # Sender and recipient live on *different* domains — unrelated
        # tenants that happen to share the instance.
        sender_mailbox = factories.MailboxFactory()
        recipient_mailbox = factories.MailboxFactory()
        recipient_email = str(recipient_mailbox)

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=recipient_mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)

        message = self._build_internal_message(sender_mailbox, recipient_email)
        outbound.send_message(message)

        # The recipient's webhook fired for the inbound message, addressed
        # to the recipient mailbox.
        assert mock_session.return_value.post.called
        headers = mock_session.return_value.post.call_args.kwargs["headers"]
        assert headers["X-StMsg-Trigger"] == "message.delivered"
        assert headers["X-StMsg-Recipient"] == recipient_email

        # Sender's view is decoupled from the recipient's webhook: it sees
        # a clean internal handoff (SENT_INTERNAL) regardless of what the webhook
        # returns.
        recipient = message.recipients.first()
        recipient.refresh_from_db()
        assert (
            recipient.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_INTERNAL
        )

    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_recipient_webhook_failure_does_not_affect_sender(
        self, mock_session, _mock_rspamd
    ):
        """A failing/blocking recipient webhook is the recipient tenant's
        problem: it holds *their* queue row for retry but never feeds back
        into the (possibly unrelated) sender's delivery status."""
        sender_mailbox = factories.MailboxFactory()
        recipient_mailbox = factories.MailboxFactory()
        recipient_email = str(recipient_mailbox)

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=recipient_mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
            },
        )
        # 4xx is a webhook error → the recipient pipeline holds for RETRY.
        mock_session.return_value.post.return_value = _make_response(403)

        message = self._build_internal_message(sender_mailbox, recipient_email)
        outbound.send_message(message)

        # Sender still sees a clean internal handoff (SENT_INTERNAL).
        recipient = message.recipients.first()
        recipient.refresh_from_db()
        assert (
            recipient.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_INTERNAL
        )

        # The recipient's queue row is held (not lost, not delivered),
        # and no message has landed in their mailbox yet.
        assert models.InboundMessage.objects.filter(
            mailbox=recipient_mailbox, envelope__origin="internal"
        ).exists()
        assert not models.Message.objects.filter(
            thread__accesses__mailbox=recipient_mailbox
        ).exists()

    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_internal_delivery_skips_spam_scan(self, mock_session, mock_rspamd):
        """Internal mail is trusted: the spam steps are skipped (rspamd is
        never consulted) while the message still lands for the recipient."""
        sender_mailbox = factories.MailboxFactory()
        recipient_mailbox = factories.MailboxFactory()
        recipient_email = str(recipient_mailbox)

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=recipient_mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(200)

        message = self._build_internal_message(sender_mailbox, recipient_email)
        outbound.send_message(message)

        mock_rspamd.assert_not_called()
        assert models.Message.objects.filter(
            thread__accesses__mailbox=recipient_mailbox
        ).exists()


# Keep dj_timezone import used to silence "imported but unused" if the
# linter wakes up after edits; it's referenced from fixtures via factories.
_ = dj_timezone


# --- response body parsing --- #


class TestReadCappedBody:
    """``_read_capped_body`` bounds memory (size cap) and time (deadline)
    so a hostile/slow receiver can't OOM or pin a worker."""

    class _FakeResponse:
        def __init__(self, chunks):
            self._chunks = chunks

        def iter_content(self, chunk_size, decode_unicode):  # pylint: disable=unused-argument
            yield from self._chunks

    def test_size_cap(self):
        from core.mda.dispatch_webhooks import MAX_RESPONSE_BODY, _read_capped_body

        resp = self._FakeResponse([b"x" * (MAX_RESPONSE_BODY + 1000)])
        assert len(_read_capped_body(resp)) == MAX_RESPONSE_BODY

    def test_deadline_exceeded_raises(self):
        import time

        from core.mda.dispatch_webhooks import _read_capped_body

        resp = self._FakeResponse([b"a", b"b"])
        # Deadline already in the past → first chunk trips the guard.
        with pytest.raises(TimeoutError):
            _read_capped_body(resp, deadline=time.monotonic() - 1)


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
        outcome = _classify_response_body(b'{"action": "not-a-real-action"}')
        assert outcome.decision == Decision.CONTINUE

    def test_action_retry_is_continue(self):
        """A 2xx body can no longer request a retry: ``action == "retry"``
        is treated like any other unknown action → CONTINUE. A 2xx is
        success; redelivery is signalled only by a non-2xx status, so the
        body cannot ask a successful response to be re-POSTed."""
        outcome = _classify_response_body(b'{"action": "retry"}')
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
            json.dumps({"add_labels": [a, b]}).encode("utf-8")
        )
        assert outcome.labels == {a, b}

    def test_labels_non_uuid_strings_skipped(self):
        good = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"add_labels": [good, "not-a-uuid", "", 42]}).encode("utf-8")
        )
        assert outcome.labels == {good}

    def test_labels_non_list_ignored(self):
        outcome = _classify_response_body(b'{"add_labels": "spam"}')
        assert outcome.labels == set()

    def test_combined_action_and_labels(self):
        """Drop + labels: drop wins; labels are still collected (caller
        won't apply them since the thread is never created, but the
        merge logic shouldn't lose them either)."""
        good = str(uuid.uuid4())
        outcome = _classify_response_body(
            json.dumps({"action": "drop", "add_labels": [good]}).encode("utf-8")
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
                {"add_labels": labels, "assign_to": emails, "add_event": events}
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
        """Non-blocking webhooks are fire-and-forget. They're not in the
        delivery decision path at all — the step only records them and does
        no inline I/O — so a receiver's body (even ``{"action":"drop"}``)
        can never affect delivery."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
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
        mock_session.return_value.post.assert_not_called()

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
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/second",
                "trigger": "message.delivering",
                "auth_method": "jwt",
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
                "trigger": "message.delivering",
                "auth_method": "jwt",
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

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_retries_on_response_read_timeout(
        self, mock_session, mailbox, parsed_email
    ):
        """A receiver that drip-feeds the body just under the per-read
        socket timeout trips the total-exchange deadline mid-read. That
        must surface as RETRY (transport failure), NOT a benign empty-body
        CONTINUE — otherwise a slow-loris receiver could silently deliver
        every message as accept."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        response = _make_response(200)
        # Reading the streamed body exceeds the time budget.
        response.iter_content = Mock(side_effect=TimeoutError("slow drip"))
        mock_session.return_value.post.return_value = response

        outcome = dispatch_webhooks(
            phase=PHASE_AFTER_SPAM,
            mailbox=mailbox,
            recipient_email=str(mailbox),
            parsed_email=parsed_email,
            raw_data=b"raw",
            is_spam=False,
        )
        assert outcome.decision == Decision.RETRY
        # The streamed response is still released back to the pool.
        response.close.assert_called_once()

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_post_uses_bounded_timeout_and_streaming(
        self, mock_session, mailbox, parsed_email
    ):
        """The POST must pass the (connect, read) timeout tuple and
        ``stream=True`` so a hostile receiver can't pin a worker on connect
        or OOM it on a giant unread body. Asserted explicitly so neither
        bound can be dropped silently."""
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
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
        kwargs = mock_session.return_value.post.call_args.kwargs
        assert kwargs["timeout"] == (WEBHOOK_CONNECT_TIMEOUT, WEBHOOK_TIMEOUT)
        assert kwargs["stream"] is True


# --- pipeline integration: RETRY, label apply, antispam override --- #


@pytest.mark.django_db
class TestPipelineRetry:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
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
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
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
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_webhook_deferral_delivers_flagged_after_window(
        self, mock_session, mock_check_spam, mock_create_message
    ):
        """Past the 48h window a still-failing blocking webhook stops
        holding: the message is delivered (never dropped), stamped
        ``X-StMsg-Processing-Failed``, and forced to the inbox so the
        warning banner is actually seen."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        # Backdate past the deferral window (auto_now_add → update()).
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now() - DEFERRAL_MAX_AGE - timedelta(minutes=1)
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
            },
        )
        mock_session.return_value.post.return_value = _make_response(503)
        mock_create_message.return_value = True

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        # The pipeline aborts at the failing before_spam webhook (RETRY),
        # so deferral is decided at the task level — generic, not
        # webhook-specific: rspamd is never reached this run.
        mock_check_spam.assert_not_called()
        # Delivered, not dropped.
        mock_create_message.assert_called_once()
        kwargs = mock_create_message.call_args.kwargs
        # Stamped for the UI banner — structurally in postmark, not in the bytes.
        assert kwargs["postmark"]["processing"] == "fail"
        assert b"X-StMsg-Processing-Failed" not in kwargs["raw_data"]
        # ...and forced to the inbox (is_spam=False) so the banner is seen.
        assert kwargs["is_spam"] is False
        # Queue row consumed — not pinned, not dropped silently.
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()

    @patch("core.mda.autoreply.try_send_autoreply")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_webhook_deferral_suppresses_autoreply(
        self, mock_session, _mock_rspamd, mock_autoreply
    ):
        """Force-delivering past an expired deferral forces ``is_spam=False``
        to surface the warning banner, but must NOT fire an autoreply: the
        spam verdict is
        unverified and the blocking step that might have suppressed the
        reply never completed."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: test\r\n"
            b"Message-ID: <deferral-autoreply@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        # Backdate past the deferral window so the failing webhook stops
        # holding and the message is force-delivered.
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now() - DEFERRAL_MAX_AGE - timedelta(minutes=1)
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
            },
        )
        # Webhook keeps failing → RETRY → force-delivered once deferral expires.
        mock_session.return_value.post.return_value = _make_response(503)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(inbound_message.id))

        # Force-delivered (row consumed) but no autoreply fired.
        assert not models.InboundMessage.objects.filter(id=inbound_message.id).exists()
        mock_autoreply.assert_not_called()


@pytest.mark.django_db
class TestPipelineWebhookAntispam:
    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",
                "auth_method": "jwt",
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
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        # rspamd says ham; webhook says spam.
        mock_check_spam.return_value = ("no action", None, None)
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
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("no action", None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "add_labels": [
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

    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("no action", None, None)
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

    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("no action", None, None)
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

    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        ch_a = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/a",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        ch_b = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com/b",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_check_spam.return_value = ("no action", None, None)
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        with (
            patch("core.mda.spam.call_rspamd") as mock_rspamd,
            patch("core.mda.dispatch_webhooks.SSRFSafeSession") as mock_session,
        ):
            mock_rspamd.return_value = ("no action", None, None)
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
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
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

    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_add_event_im_creates_threadevent(self, mock_session, mock_rspamd):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: comment\r\n"
            b"Message-ID: <flag-event@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
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

    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
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

    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
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
    @patch("core.mda.spam.call_rspamd")
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
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
        mock_session.return_value.post.return_value = _make_response(
            200,
            body=json.dumps(
                {
                    "add_labels": [str(label.id)],
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


@pytest.mark.django_db
class TestDeferralDelivery:
    """When a blocking webhook fails persistently past ``DEFERRAL_MAX_AGE``,
    the message is delivered anyway — stamped with the ``X-StMsg-Processing-
    Failed`` marker, forced to the inbox (``is_spam=False``), and the
    autoreply is suppressed."""

    @patch("core.mda.autoreply.try_send_autoreply")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_deferral_delivers_message_past_window(
        self, mock_session, mock_rspamd, mock_autoreply
    ):
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: deferral\r\n"
            b"Message-ID: <deferral-test@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        # Push ``created_at`` past the 48-hour deferral window so the
        # task takes the age > DEFERRAL_MAX_AGE branch on first attempt.
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now() - DEFERRAL_MAX_AGE - timedelta(hours=1)
        )
        inbound_message.refresh_from_db()

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
        # Non-2xx triggers RETRY from the blocking webhook.
        mock_session.return_value.post.return_value = _make_response(503)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        # Message delivered despite the blocking webhook failing.
        assert result["success"] is True
        assert result["is_spam"] is False
        message = models.Message.objects.get(mime_id="deferral-test@example.com")

        # Processing-failed stamp is recorded structurally in postmark (not
        # baked into the bytes) and surfaced via get_stmsg_headers for the UI.
        assert message.postmark["processing"] == "fail"
        assert message.get_stmsg_headers()["processing-failed"] == "true"
        assert b"X-StMsg-Processing-Failed" not in message.blob.get_content()

        # Autoreply suppressed when the deferral window expired.
        mock_autoreply.assert_not_called()

    @patch("core.mda.autoreply.try_send_autoreply")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_deferral_force_is_spam_false(
        self, mock_session, mock_rspamd, mock_autoreply
    ):
        """Even when the pipeline sets is_spam=True (e.g. rspamd),
        force-delivering past an expired deferral forces is_spam=False so the
        message lands in the inbox where the recipient sees the warning
        banner."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: spammer@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: deferral spam\r\n"
            b"Message-ID: <deferral-spam@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        models.InboundMessage.objects.filter(id=inbound_message.id).update(
            created_at=dj_timezone.now() - DEFERRAL_MAX_AGE - timedelta(hours=1)
        )
        inbound_message.refresh_from_db()

        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        # Rspamd votes spam, webhook errors → RETRY → force-delivery (once the
        # deferral window expires) should still force is_spam=False.
        mock_rspamd.return_value = ("reject", None, None)
        mock_session.return_value.post.return_value = _make_response(503)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        assert result["is_spam"] is False
        message = models.Message.objects.get(mime_id="deferral-spam@example.com")
        assert message.postmark["processing"] == "fail"
        assert message.get_stmsg_headers()["processing-failed"] == "true"
        assert b"X-StMsg-Processing-Failed" not in message.blob.get_content()
        mock_autoreply.assert_not_called()


@pytest.mark.django_db
class TestAbandonedRowHandling:
    """A row that fails to be CREATED past the deferral window is
    abandoned: kept (its raw bytes are the only copy of the mail) but
    stamped via ``abandoned_at`` so neither the 5-min sweep nor a direct
    re-dispatch ever re-runs the pipeline (and re-fires every webhook)
    on it again."""

    def test_sweep_skips_abandoned_rows(self):
        """The retry sweep must not re-queue an abandoned row."""
        mailbox = factories.MailboxFactory()
        abandoned = _queue_inbound(
            mailbox,
            b"From: s@example.com\r\nSubject: t\r\n\r\nbody",
            error_message="persistent failure",
            abandoned_at=dj_timezone.now(),
        )
        # Age it past the 5-minute retry threshold so it would otherwise
        # be picked up by the sweep.
        models.InboundMessage.objects.filter(id=abandoned.id).update(
            created_at=dj_timezone.now() - timedelta(minutes=10)
        )

        with patch(
            "core.mda.inbound_tasks.process_inbound_message_task.delay"
        ) as mock_delay:
            process_inbound_messages_queue_task.run()

        dispatched = [call.args[0] for call in mock_delay.call_args_list]
        assert str(abandoned.id) not in dispatched

    @patch("core.mda.inbound_tasks._create_message_from_inbound")
    @patch("core.mda.spam.call_rspamd")
    def test_redispatch_of_abandoned_row_early_returns(self, mock_rspamd, mock_create):
        """A direct re-dispatch of an abandoned row early-returns
        ``{"error": "abandoned"}`` without ever running the pipeline —
        rspamd is never consulted and no creation is attempted."""
        mailbox = factories.MailboxFactory()
        abandoned = _queue_inbound(
            mailbox,
            b"From: s@example.com\r\nSubject: t\r\n\r\nbody",
            error_message="persistent failure",
            abandoned_at=dj_timezone.now(),
        )

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(abandoned.id))

        assert result["error"] == "abandoned"
        mock_rspamd.assert_not_called()
        mock_create.assert_not_called()
        # Row preserved for operator inspection / replay.
        assert models.InboundMessage.objects.filter(id=abandoned.id).exists()

    def test_purge_deletes_old_abandoned_rows(self):
        """The retention purge reclaims rows abandoned past the window."""
        mailbox = factories.MailboxFactory()
        old = _queue_inbound(
            mailbox,
            b"x",
            error_message="persistent failure",
            abandoned_at=dj_timezone.now() - timedelta(days=8),
        )

        result = purge_abandoned_inbound_messages_task.run()

        assert result["purged"] == 1
        assert not models.InboundMessage.objects.filter(id=old.id).exists()

    def test_purge_keeps_recent_abandoned_rows(self):
        """Rows abandoned within the retention window are kept."""
        mailbox = factories.MailboxFactory()
        recent = _queue_inbound(
            mailbox,
            b"x",
            error_message="persistent failure",
            abandoned_at=dj_timezone.now() - timedelta(days=1),
        )

        result = purge_abandoned_inbound_messages_task.run()

        assert result["purged"] == 0
        assert models.InboundMessage.objects.filter(id=recent.id).exists()

    def test_purge_ignores_live_rows(self):
        """A non-abandoned row is never touched by the purge, however old."""
        mailbox = factories.MailboxFactory()
        live = _queue_inbound(mailbox, b"x")
        models.InboundMessage.objects.filter(id=live.id).update(
            created_at=dj_timezone.now() - timedelta(days=30)
        )

        result = purge_abandoned_inbound_messages_task.run()

        assert result["purged"] == 0
        assert models.InboundMessage.objects.filter(id=live.id).exists()


@pytest.mark.django_db
class TestPipelineIdempotency:
    """A duplicate inbound email — most often an upstream MTA redelivering
    the same Message-ID (SMTP retry / greylisting / relay double-send), so a
    second ``InboundMessage`` is processed later — must hit the
    ``(mailbox, mime_id)`` dedup branch in ``_create_message_from_inbound``
    (``_created_now=False``) and skip the one-shot finalize side effects (IM
    events, draft replies, autoreply, the non-blocking ``message.delivered``
    webhook) that already ran for the original create — re-running them would
    duplicate them."""

    @patch("core.mda.autoreply.try_send_autoreply")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_dedup_hit_skips_finalize_side_effects(
        self, mock_session, mock_rspamd, mock_autoreply
    ):
        mailbox = factories.MailboxFactory()
        template = factories.MessageTemplateFactory(
            mailbox=mailbox,
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            is_active=True,
            html_body="<p>Thanks!</p>",
            text_body="Thanks!",
            raw_body={"type": "doc", "content": [{"type": "paragraph"}]},
        )
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
        action_body = json.dumps(
            {
                "add_event": [{"type": "im", "content": "AI: urgent"}],
                "reply_draft": {"template": str(template.id)},
            }
        ).encode("utf-8")
        mock_session.return_value.post.return_value = _make_response(
            200, body=action_body
        )

        mime = "idem-1@example.com"
        raw_data = (
            b"From: customer@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: help\r\n"
            b"Message-ID: <" + mime.encode() + b">\r\n\r\nbody"
        )

        # --- First pass: creates the message + all side effects. ---
        im1 = _queue_inbound(mailbox, raw_data)
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            r1 = process_inbound_message_task.run(str(im1.id))
        assert r1["success"] is True

        message = models.Message.objects.get(mime_id=mime)
        thread = message.thread

        def im_event_count():
            return models.ThreadEvent.objects.filter(
                thread=thread, type=enums.ThreadEventTypeChoices.IM
            ).count()

        def draft_count():
            return models.Message.objects.filter(thread=thread, is_draft=True).count()

        assert im_event_count() == 1
        assert draft_count() == 1
        assert mock_autoreply.call_count == 1  # fired once, on create

        # --- Second pass: SAME Message-ID → dedup hit (_created_now=False). ---
        im2 = _queue_inbound(mailbox, raw_data)
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(im2.id))

        # The pipeline DID run again (the blocking webhook fired a 2nd time),
        # proving the skip is gated on _created_now, not on the pipeline
        # being short-circuited.
        assert mock_session.return_value.post.call_count == 2
        # ...but NO finalize side effect was repeated.
        assert im_event_count() == 1
        assert draft_count() == 1
        assert mock_autoreply.call_count == 1  # still just the original
        # The duplicate queue row was still consumed (one Message exists).
        assert models.Message.objects.filter(mime_id=mime).count() == 1
        assert not models.InboundMessage.objects.filter(id=im2.id).exists()

    @patch("core.mda.dispatch_webhooks.dispatch_webhook_task.delay")
    @patch("core.mda.spam.call_rspamd")
    def test_dedup_hit_does_not_refire_nonblocking_webhook(
        self, mock_rspamd, mock_delay
    ):
        """A duplicate delivery must not re-enqueue the non-blocking
        ``message.delivered`` webhook — its dispatch is one of the finalize
        side effects gated on ``_created_now`` (and ``message.delivered`` is
        already at-least-once at the Celery layer, so a duplicate *enqueue*
        here would compound that)."""
        mailbox = factories.MailboxFactory()
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivered",
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)

        mime = "idem-nb@example.com"
        raw_data = (
            b"From: customer@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: help\r\n"
            b"Message-ID: <" + mime.encode() + b">\r\n\r\nbody"
        )

        im1 = _queue_inbound(mailbox, raw_data)
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(im1.id))
        # Enqueued exactly once, on the original create.
        assert mock_delay.call_count == 1

        # Duplicate delivery: same Message-ID, separate queue row.
        im2 = _queue_inbound(mailbox, raw_data)
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            process_inbound_message_task.run(str(im2.id))

        # Still 1 — the dedup hit skipped the non-blocking dispatch.
        assert mock_delay.call_count == 1
        assert models.Message.objects.filter(mime_id=mime).count() == 1


# --- cross-retry blocking-webhook result cache --- #


class TestHttpResultCacheSerialization:
    """``_HttpResult.to_cache`` / ``from_cache`` are the JSON-able bridge
    for the cross-retry result cache. They must round-trip every field and
    tolerate partial/old data (a deploy that changes the dataclass must not
    fail to load 48h-old entries)."""

    def test_roundtrip_preserves_every_field(self):
        original = _HttpResult(
            decision=Decision.DROP,
            is_spam_override=True,
            labels={str(uuid.uuid4()), str(uuid.uuid4())},
            assign_to=["alice@example.org", "bob@example.org"],
            mark_starred=True,
            mark_read=True,
            mark_trashed=True,
            mark_archived=True,
            skip_autoreply=True,
            events=[{"type": "im", "content": "x", "mentions": []}],
            reply_draft_template_id=str(uuid.uuid4()),
        )
        # The intermediate form is a plain JSON-able dict (no enums/sets).
        cached = original.to_cache()
        assert isinstance(cached, dict)
        assert cached["decision"] == int(Decision.DROP)
        assert isinstance(cached["labels"], list)

        restored = _HttpResult.from_cache(cached)
        assert restored.decision == original.decision
        assert restored.is_spam_override == original.is_spam_override
        assert restored.labels == original.labels
        assert restored.assign_to == original.assign_to
        assert restored.mark_starred == original.mark_starred
        assert restored.mark_read == original.mark_read
        assert restored.mark_trashed == original.mark_trashed
        assert restored.mark_archived == original.mark_archived
        assert restored.skip_autoreply == original.skip_autoreply
        assert restored.events == original.events
        assert restored.reply_draft_template_id == original.reply_draft_template_id
        # Dataclass equality covers all fields at once.
        assert restored == original

    def test_from_cache_empty_yields_defaults(self):
        """Missing keys (old/partial entry) degrade to a default CONTINUE
        result, never an exception."""
        restored = _HttpResult.from_cache({})
        assert restored == _HttpResult()
        assert restored.decision == Decision.CONTINUE
        assert restored.is_spam_override is None
        assert restored.labels == set()


@pytest.mark.django_db
class TestWebhookResultCachePersistence:
    """``load_cached_webhook_results`` / ``persist_cached_webhook_results``
    against the real Django cache. The cache is a best-effort optimisation:
    every miss/error must degrade to an empty map (re-fire)."""

    def test_load_miss_returns_empty(self):
        assert load_cached_webhook_results(str(uuid.uuid4())) == {}

    def test_persist_then_load_roundtrip(self):
        inbound_id = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        label = str(uuid.uuid4())
        results = {
            (cid, PHASE_BEFORE_SPAM): _HttpResult(
                decision=Decision.DROP,
                is_spam_override=True,
                labels={label},
                assign_to=["a@example.org"],
                skip_autoreply=True,
            ),
        }
        persist_cached_webhook_results(inbound_id, results)

        loaded = load_cached_webhook_results(inbound_id)
        assert set(loaded.keys()) == {(cid, PHASE_BEFORE_SPAM)}
        got = loaded[(cid, PHASE_BEFORE_SPAM)]
        want = results[(cid, PHASE_BEFORE_SPAM)]
        assert got.decision == want.decision
        assert got.is_spam_override == want.is_spam_override
        assert got.labels == want.labels
        assert got.assign_to == want.assign_to
        assert got.skip_autoreply == want.skip_autoreply

    def test_persist_noops_on_empty(self):
        inbound_id = str(uuid.uuid4())
        persist_cached_webhook_results(inbound_id, {})
        # Nothing was written → still a clean miss.
        assert load_cached_webhook_results(inbound_id) == {}


@pytest.mark.django_db
class TestBlockingWebhookResultCache:
    """The ``UserWebhookStep`` blocking branch replays a memoised result
    instead of re-POSTing, while still applying its side effects."""

    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_cache_hit_skips_post_but_applies_side_effects(
        self, mock_session, mailbox, parsed_email
    ):
        channel = factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",  # before-spam, blocking
                "auth_method": "jwt",
            },
        )
        label_id = str(uuid.uuid4())
        cached = _HttpResult(is_spam_override=True, labels={label_id})
        ctx = InboundContext(
            mailbox=mailbox,
            inbound_message=Mock(id="im", created_at=dj_timezone.now()),
            recipient_email=str(mailbox),
            raw_data=b"raw",
            parsed_email=parsed_email,
            spam_config={},
            is_spam=None,
            blocking_webhook_results={
                (str(channel.id), PHASE_BEFORE_SPAM): cached,
            },
        )

        step = UserWebhookStep(channel, phase=PHASE_BEFORE_SPAM)
        decision = step(ctx)

        # The memoised result short-circuits the network call entirely...
        assert decision == Decision.CONTINUE
        mock_session.return_value.post.assert_not_called()
        # ...yet its side effects are still replayed onto the context.
        assert ctx.is_spam is True
        assert label_id in ctx.labels

    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_blocking_webhook_not_refired_across_retries(
        self, mock_session, mock_rspamd
    ):
        """The core storm-fix regression guard: a before-spam blocking
        webhook that succeeded on attempt 1 is NOT re-POSTed on attempt 2
        when a sustained rspamd outage keeps the message in RETRY."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: storm\r\n"
            b"Message-ID: <storm-1@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.inbound",  # before-spam, blocking
                "auth_method": "jwt",
            },
        )
        # Webhook succeeds (empty 200 → no is_spam opinion, so the later
        # rspamd step still runs); rspamd is erroring → pipeline RETRYs.
        mock_session.return_value.post.return_value = _make_response(200)
        mock_rspamd.return_value = (None, "rspamd unreachable", None)

        # Attempt 1: webhook POSTed once, message held for retry.
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            r1 = process_inbound_message_task.run(str(inbound_message.id))
        assert r1["error"] == "retry"
        assert mock_session.return_value.post.call_count == 1
        # The success was memoised to Redis on the retry path.
        inbound_message.refresh_from_db()
        assert inbound_message.error_message  # marks this as a retry attempt

        # Attempt 2 on the SAME row: the cached result is replayed, so the
        # webhook is NOT POSTed again — still RETRYs (rspamd still down).
        with patch.object(process_inbound_message_task, "update_state", Mock()):
            r2 = process_inbound_message_task.run(str(inbound_message.id))
        assert r2["error"] == "retry"
        assert mock_session.return_value.post.call_count == 1  # served from cache

    @patch("core.mda.inbound_tasks.persist_cached_webhook_results")
    @patch("core.mda.inbound_tasks.load_cached_webhook_results")
    @patch("core.mda.spam.call_rspamd")
    @patch("core.mda.dispatch_webhooks.SSRFSafeSession")
    def test_happy_path_does_not_touch_cache(
        self, mock_session, mock_rspamd, mock_load, mock_persist
    ):
        """A normal single-pass delivery never reads (first attempt → no
        ``error_message``) nor writes (no RETRY) the result cache."""
        mailbox = factories.MailboxFactory()
        raw_data = (
            b"From: sender@example.com\r\n"
            b"To: " + str(mailbox).encode() + b"\r\n"
            b"Subject: happy\r\n"
            b"Message-ID: <happy-1@example.com>\r\n\r\nbody"
        )
        inbound_message = _queue_inbound(mailbox, raw_data)
        factories.ChannelFactory(
            type=enums.ChannelTypes.WEBHOOK,
            mailbox=mailbox,
            settings={
                "url": "https://hook.example.com",
                "trigger": "message.delivering",  # after-spam, blocking
                "auth_method": "jwt",
            },
        )
        mock_rspamd.return_value = ("no action", None, None)
        mock_session.return_value.post.return_value = _make_response(200)

        with patch.object(process_inbound_message_task, "update_state", Mock()):
            result = process_inbound_message_task.run(str(inbound_message.id))

        assert result["success"] is True
        mock_load.assert_not_called()
        mock_persist.assert_not_called()
