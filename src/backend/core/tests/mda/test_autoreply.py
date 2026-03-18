"""Tests for autoreply MDA logic."""
# pylint: disable=redefined-outer-name,unused-argument

import base64
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.test import override_settings
from django.utils import timezone

import pytest
from rest_framework.exceptions import ValidationError

from core import factories, models
from core.enums import (
    MessageRecipientTypeChoices,
    MessageTemplateTypeChoices,
)
from core.mda.autoreply import (
    _is_auto_reply_message,
    _is_recipient_explicit,
    send_autoreply_for_message,
    should_send_autoreply,
)
from core.services.throttle import ThrottleLimitExceeded, ThrottleManager

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mailbox():
    """Create a mailbox with contact."""
    mb = factories.MailboxFactory()
    if not mb.contact:
        contact = factories.ContactFactory(
            email=str(mb), name="Test Mailbox", mailbox=mb
        )
        mb.contact = contact
        mb.save()
    return mb


@pytest.fixture
def autoreply_template(mailbox):
    """Create an active autoreply template for the mailbox."""
    return factories.MessageTemplateFactory(
        name="Out of Office",
        type=MessageTemplateTypeChoices.AUTOREPLY,
        mailbox=mailbox,
        is_active=True,
        metadata={"schedule_type": "always"},
        html_body="<p>I am out of office.</p>",
        text_body="I am out of office.",
    )


@pytest.fixture
def inbound_message(mailbox):
    """Create an inbound message in the mailbox."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    sender_contact = factories.ContactFactory(
        email="sender@example.com", name="Sender", mailbox=mailbox
    )
    return factories.MessageFactory(
        thread=thread,
        sender=sender_contact,
        subject="Hello",
        is_sender=False,
    )


# ---------------------------------------------------------------------------
# TestIsAutoReplyMessage
# ---------------------------------------------------------------------------


class TestIsAutoReplyMessage:
    """Tests for _is_auto_reply_message header detection."""

    def test_normal_message_passes(self):
        """Normal message is not detected as auto-reply."""
        headers = {"From": "user@example.com", "Subject": "Hello"}
        assert _is_auto_reply_message(headers) is False

    def test_empty_headers(self):
        """Empty or None headers are not detected as auto-reply."""
        assert _is_auto_reply_message({}) is False
        assert _is_auto_reply_message(None) is False

    def test_auto_submitted_auto_replied(self):
        """Auto-Submitted: auto-replied is detected."""
        headers = {"Auto-Submitted": "auto-replied"}
        assert _is_auto_reply_message(headers) is True

    def test_auto_submitted_auto_generated(self):
        """Auto-Submitted: auto-generated is detected."""
        headers = {"Auto-Submitted": "auto-generated"}
        assert _is_auto_reply_message(headers) is True

    def test_auto_submitted_no_passes(self):
        """Auto-Submitted: no is not detected as auto-reply."""
        headers = {"Auto-Submitted": "no"}
        assert _is_auto_reply_message(headers) is False

    def test_precedence_bulk(self):
        """Precedence: bulk is detected."""
        headers = {"Precedence": "bulk"}
        assert _is_auto_reply_message(headers) is True

    def test_precedence_list(self):
        """Precedence: list is detected."""
        headers = {"Precedence": "list"}
        assert _is_auto_reply_message(headers) is True

    def test_precedence_junk(self):
        """Precedence: junk is detected."""
        headers = {"Precedence": "junk"}
        assert _is_auto_reply_message(headers) is True

    def test_list_id_header(self):
        """List-Id header is detected."""
        headers = {"List-Id": "<list.example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_list_unsubscribe_header(self):
        """List-Unsubscribe header is detected."""
        headers = {"List-Unsubscribe": "<mailto:unsub@example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_x_auto_response_suppress(self):
        """X-Auto-Response-Suppress header is detected."""
        headers = {"X-Auto-Response-Suppress": "All"}
        assert _is_auto_reply_message(headers) is True

    def test_x_autoreply(self):
        """X-Autoreply header is detected."""
        headers = {"X-Autoreply": "yes"}
        assert _is_auto_reply_message(headers) is True

    def test_x_autorespond(self):
        """X-Autorespond header is detected."""
        headers = {"X-Autorespond": "yes"}
        assert _is_auto_reply_message(headers) is True

    def test_auto_submitted_with_parameters(self):
        """Auto-Submitted with RFC 3834 parameters after semicolon is detected."""
        headers = {"Auto-Submitted": 'auto-replied; owner-email="user@example.com"'}
        assert _is_auto_reply_message(headers) is True

    def test_auto_submitted_no_with_parameters(self):
        """Auto-Submitted: no with parameters is not detected."""
        headers = {"Auto-Submitted": "no; some-param=value"}
        assert _is_auto_reply_message(headers) is False


class TestIsAutoReplyMessageExtended:
    """Tests for _is_auto_reply_message: Return-Path, List-*, and loop headers."""

    def test_return_path_null(self):
        """Return-Path: <> (null sender) is detected."""
        headers = {"Return-Path": "<>"}
        assert _is_auto_reply_message(headers) is True

    def test_return_path_empty(self):
        """Return-Path with empty value is detected."""
        headers = {"Return-Path": ""}
        assert _is_auto_reply_message(headers) is True

    def test_list_post_header(self):
        """List-Post header is detected."""
        headers = {"List-Post": "<mailto:list@example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_list_help_header(self):
        """List-Help header is detected."""
        headers = {"List-Help": "<mailto:help@example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_list_subscribe_header(self):
        """List-Subscribe header is detected."""
        headers = {"List-Subscribe": "<mailto:sub@example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_list_owner_header(self):
        """List-Owner header is detected."""
        headers = {"List-Owner": "<mailto:owner@example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_list_archive_header(self):
        """List-Archive header is detected."""
        headers = {"List-Archive": "<https://archive.example.com>"}
        assert _is_auto_reply_message(headers) is True

    def test_x_loop_header(self):
        """X-Loop header is detected."""
        headers = {"X-Loop": "yes"}
        assert _is_auto_reply_message(headers) is True

    def test_feedback_id_header(self):
        """Feedback-ID header is detected (Gmail newsletters)."""
        headers = {"Feedback-ID": "123:campaign:gmail"}
        assert _is_auto_reply_message(headers) is True


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for autoreply throttle limiting."""

    def _check(self, mailbox_id, sender_email):
        """Run the autoreply throttle check (check + increment)."""
        with ThrottleManager() as throttle:
            throttle.check_limit(
                "1/day",
                "autoreply",
                f"{mailbox_id}:{sender_email}",
                counter_type="sends",
            )

    def test_not_rate_limited_initially(self):
        """No rate limit exists initially."""
        self._check("mb-init", "sender@example.com")  # should not raise

    def test_rate_limited_after_set(self):
        """Rate limit is enforced after first send."""
        self._check("mb-rl", "sender@example.com")
        with pytest.raises(ThrottleLimitExceeded):
            self._check("mb-rl", "sender@example.com")

    def test_different_sender_not_limited(self):
        """Rate limit does not apply to a different sender."""
        self._check("mb-ds", "sender@example.com")
        self._check("mb-ds", "other@example.com")  # should not raise

    def test_different_mailbox_not_limited(self):
        """Rate limit does not apply to a different mailbox."""
        self._check("mb-dm1", "sender@example.com")
        self._check("mb-dm2", "sender@example.com")  # should not raise


# ---------------------------------------------------------------------------
# TestIsActiveAutoreply
# ---------------------------------------------------------------------------


class TestIsActiveAutoreply:
    """Tests for MessageTemplate.is_active_autoreply()."""

    def test_always_schedule(self, autoreply_template):
        """Always schedule is always active."""
        assert autoreply_template.is_active_autoreply() is True

    def test_inactive_template(self, autoreply_template):
        """Inactive template is not active."""
        autoreply_template.is_active = False
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is False

    def test_wrong_type(self, mailbox):
        """Non-autoreply template type is not active."""
        template = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
        )
        assert template.is_active_autoreply() is False

    def test_date_range_in_range(self, autoreply_template):
        """Date range covering now is active."""
        now = timezone.now()
        autoreply_template.metadata = {
            "schedule_type": "date_range",
            "start_at": (now - timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=1)).isoformat(),
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is True

    def test_date_range_out_of_range(self, autoreply_template):
        """Date range in the future is not active."""
        now = timezone.now()
        autoreply_template.metadata = {
            "schedule_type": "date_range",
            "start_at": (now + timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=2)).isoformat(),
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is False

    def test_date_range_expired(self, autoreply_template):
        """Expired date range is not active."""
        now = timezone.now()
        autoreply_template.metadata = {
            "schedule_type": "date_range",
            "start_at": (now - timedelta(days=2)).isoformat(),
            "end_at": (now - timedelta(days=1)).isoformat(),
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is False

    def test_recurring_weekly_correct_day(self, autoreply_template):
        """Test interval covering current day+time."""
        tz = ZoneInfo("UTC")
        now = timezone.now().astimezone(tz)
        # Build an interval covering the whole current day
        autoreply_template.metadata = {
            "schedule_type": "recurring_weekly",
            "intervals": [
                {
                    "start_day": now.isoweekday(),
                    "start_time": "00:00",
                    "end_day": now.isoweekday(),
                    "end_time": "23:59",
                }
            ],
            "timezone": "UTC",
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is True

    def test_recurring_weekly_wrong_day(self, autoreply_template):
        """Test interval not covering current day+time."""
        tz = ZoneInfo("UTC")
        now = timezone.now().astimezone(tz)
        wrong_day = (now.isoweekday() % 7) + 1  # A different day
        autoreply_template.metadata = {
            "schedule_type": "recurring_weekly",
            "intervals": [
                {
                    "start_day": wrong_day,
                    "start_time": "00:00",
                    "end_day": wrong_day,
                    "end_time": "23:59",
                }
            ],
            "timezone": "UTC",
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is False

    def test_recurring_weekly_with_time_window(self, autoreply_template):
        """Test same-day interval with time window covering now."""
        tz = ZoneInfo("UTC")
        now = timezone.now().astimezone(tz)
        start_time = (now - timedelta(hours=1)).strftime("%H:%M")
        end_time = (now + timedelta(hours=1)).strftime("%H:%M")
        autoreply_template.metadata = {
            "schedule_type": "recurring_weekly",
            "intervals": [
                {
                    "start_day": now.isoweekday(),
                    "start_time": start_time,
                    "end_day": now.isoweekday(),
                    "end_time": end_time,
                }
            ],
            "timezone": "UTC",
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply() is True

    def test_recurring_weekly_overnight_range(self, autoreply_template):
        """Test cross-week interval (Fri 18:00 → Mon 08:00)."""
        tz = ZoneInfo("UTC")
        # Monday 20:00 UTC should be within Fri 18:00 → Mon 23:59
        fixed_now = datetime(2026, 3, 2, 20, 0, tzinfo=tz)  # Monday 20:00 UTC
        autoreply_template.metadata = {
            "schedule_type": "recurring_weekly",
            "intervals": [
                {
                    "start_day": 5,  # Friday
                    "start_time": "18:00",
                    "end_day": 1,  # Monday
                    "end_time": "23:59",
                }
            ],
            "timezone": "UTC",
        }
        autoreply_template.save()
        assert autoreply_template.is_active_autoreply(now=fixed_now) is True

        # Wednesday 10:00 should NOT be within Fri 18:00 → Mon 23:59
        outside_now = datetime(2026, 3, 4, 10, 0, tzinfo=tz)  # Wednesday 10:00 UTC
        assert autoreply_template.is_active_autoreply(now=outside_now) is False


# ---------------------------------------------------------------------------
# TestShouldSendAutoreply
# ---------------------------------------------------------------------------


class TestShouldSendAutoreply:
    """Tests for should_send_autoreply()."""

    def test_eligible_message(self, mailbox, autoreply_template):
        """Eligible message triggers autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": str(mailbox)}],
            "subject": "Hello",
            "headers": {},
        }
        result = should_send_autoreply(mailbox, parsed)
        assert result is not None
        assert result.id == autoreply_template.id

    def test_skip_spam(self, mailbox, autoreply_template):
        """Spam messages do not trigger autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed, is_spam=True) is None

    def test_skip_auto_reply_message(self, mailbox, autoreply_template):
        """Auto-reply messages do not trigger autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "headers": {"Auto-Submitted": "auto-replied"},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_self_reply(self, mailbox, autoreply_template):
        """Messages from the mailbox itself do not trigger autoreply."""
        parsed = {
            "from": {"email": str(mailbox)},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_no_autoreply_template(self, mailbox):
        """No autoreply template means no autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": str(mailbox)}],
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_inactive_schedule(self, mailbox, autoreply_template):
        """Inactive schedule does not trigger autoreply."""
        now = timezone.now()
        autoreply_template.metadata = {
            "schedule_type": "date_range",
            "start_at": (now + timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=2)).isoformat(),
        }
        autoreply_template.save()
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": str(mailbox)}],
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    @override_settings(THROTTLE_AUTOREPLY_PER_SENDER="1/day")
    def test_rate_limited(self, mailbox, autoreply_template):
        """Rate-limited sender does not trigger autoreply."""
        # First call consumes the throttle allowance
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": str(mailbox)}],
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is not None
        # Second call is throttled
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_noreply_sender(self, mailbox, autoreply_template):
        """noreply@ sender does not trigger autoreply."""
        parsed = {
            "from": {"email": "noreply@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_mailer_daemon_sender(self, mailbox, autoreply_template):
        """mailer-daemon@ sender does not trigger autoreply."""
        parsed = {
            "from": {"email": "mailer-daemon@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_postmaster_sender(self, mailbox, autoreply_template):
        """postmaster@ sender does not trigger autoreply."""
        parsed = {
            "from": {"email": "postmaster@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_bounce_sender(self, mailbox, autoreply_template):
        """bounces-123@ sender does not trigger autoreply."""
        parsed = {
            "from": {"email": "bounces-123@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_owner_prefix_sender(self, mailbox, autoreply_template):
        """owner-list@ sender does not trigger autoreply."""
        parsed = {
            "from": {"email": "owner-list@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_missing_from(self, mailbox, autoreply_template):
        """Missing 'from' key in parsed headers returns None."""
        parsed = {
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_empty_sender_email(self, mailbox, autoreply_template):
        """Empty sender email returns None."""
        parsed = {
            "from": {"email": ""},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_case_insensitive_self_reply(self, mailbox, autoreply_template):
        """Case-insensitive self-reply detection."""
        parsed = {
            "from": {"email": str(mailbox).upper()},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_skip_bcc_recipient(self, mailbox, autoreply_template):
        """Mailbox not in To/Cc/Bcc suppresses autoreply (RFC 5230 §4.5)."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": "someone-else@example.com"}],
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None

    def test_mailbox_in_cc_triggers(self, mailbox, autoreply_template):
        """Mailbox in Cc still triggers autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "to": [{"email": "someone-else@example.com"}],
            "cc": [{"email": str(mailbox)}],
            "headers": {},
        }
        result = should_send_autoreply(mailbox, parsed)
        assert result is not None

    def test_skip_no_recipients(self, mailbox, autoreply_template):
        """No To/Cc/Bcc fields suppresses autoreply."""
        parsed = {
            "from": {"email": "sender@example.com"},
            "headers": {},
        }
        assert should_send_autoreply(mailbox, parsed) is None


# ---------------------------------------------------------------------------
# TestIsRecipientExplicit
# ---------------------------------------------------------------------------


class TestIsRecipientExplicit:
    """Tests for _is_recipient_explicit() per RFC 5230 §4.5."""

    def test_in_to(self):
        """Mailbox in To is explicit."""
        parsed = {"to": [{"email": "me@example.com"}]}
        assert _is_recipient_explicit("me@example.com", parsed) is True

    def test_in_cc(self):
        """Mailbox in Cc is explicit."""
        parsed = {"cc": [{"email": "me@example.com"}]}
        assert _is_recipient_explicit("me@example.com", parsed) is True

    def test_not_present(self):
        """Mailbox not in any field returns False."""
        parsed = {"to": [{"email": "other@example.com"}]}
        assert _is_recipient_explicit("me@example.com", parsed) is False

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        parsed = {"to": [{"email": "ME@EXAMPLE.COM"}]}
        assert _is_recipient_explicit("me@example.com", parsed) is True

    def test_empty_fields(self):
        """Empty/missing fields return False."""
        assert _is_recipient_explicit("me@example.com", {}) is False

    def test_multiple_recipients(self):
        """Mailbox found among multiple recipients."""
        parsed = {
            "to": [
                {"email": "a@example.com"},
                {"email": "me@example.com"},
                {"email": "b@example.com"},
            ]
        }
        assert _is_recipient_explicit("me@example.com", parsed) is True


# ---------------------------------------------------------------------------
# TestSendAutoreplyForMessage
# ---------------------------------------------------------------------------


class TestSendAutoreplyForMessage:
    """Tests for send_autoreply_for_message()."""

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_creates_message_in_thread(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply creates a new message in the same thread."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        # A new message should be in the same thread
        autoreply_msg = models.Message.objects.filter(
            thread=inbound_message.thread,
            parent=inbound_message,
            is_sender=True,
        ).last()
        assert autoreply_msg is not None
        assert autoreply_msg.subject.startswith("Re: ")
        assert autoreply_msg.is_draft is False

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_sets_correct_sender(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply sender is the mailbox address."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        assert autoreply_msg.sender.email == str(mailbox)

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_creates_recipient(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply creates a TO recipient for the original sender."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        recipient = models.MessageRecipient.objects.get(message=autoreply_msg)
        assert recipient.contact == inbound_message.sender
        assert recipient.type == MessageRecipientTypeChoices.TO

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_triggers_send_task(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply triggers send_message_task."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        mock_send_task.delay.assert_called_once_with(str(autoreply_msg.id))

    @override_settings(THROTTLE_AUTOREPLY_PER_SENDER="1/day")
    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_throttle_set_by_should_send(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """should_send_autoreply increments throttle, blocking subsequent calls."""
        parsed = {
            "from": {"email": inbound_message.sender.email},
            "to": [{"email": str(mailbox)}],
            "headers": {},
        }
        # First call succeeds and increments the throttle
        assert should_send_autoreply(mailbox, parsed) is not None
        # Second call is throttled
        assert should_send_autoreply(mailbox, parsed) is None

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_avoids_double_re_prefix(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Subject already starting with Re: is not doubled."""
        inbound_message.subject = "Re: Something"
        inbound_message.save()
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        assert autoreply_msg.subject == "Re: Something"

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_stores_blob(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply message has a blob attached."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        assert autoreply_msg.blob is not None

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_no_sender_email_skips(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Inbound message with no sender returns early without creating Message."""
        initial_count = models.Message.objects.count()
        # Simulate missing sender at runtime without violating the DB constraint
        with patch.object(
            type(inbound_message),
            "sender",
            new_callable=lambda: property(lambda s: None),
        ):
            send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        assert models.Message.objects.count() == initial_count
        mock_send_task.delay.assert_not_called()

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_auto_reply_headers_in_mime(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Composed MIME contains Auto-Submitted and Precedence headers."""
        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        mime_bytes = autoreply_msg.blob.get_content()
        mime_str = mime_bytes.decode("utf-8", errors="replace")
        assert "Auto-Submitted: auto-replied" in mime_str
        assert "Precedence: bulk" in mime_str

    @override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=10)
    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_signature_image_exceeding_attachment_size_not_sent(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply is not sent when signature inline images exceed the size limit."""
        # Create a signature whose base64 image (~100 bytes) exceeds the 10-byte limit
        large_b64 = base64.b64encode(b"\x89PNG" + b"\x00" * 100).decode()
        signature = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
            html_body=f'<img src="data:image/png;base64,{large_b64}">',
            text_body="",
        )
        autoreply_template.signature = signature
        autoreply_template.save()

        with pytest.raises(ValidationError, match="attachment size"):
            send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        mock_send_task.delay.assert_not_called()

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch(
        "core.mda.autoreply.compose_and_store_mime",
        side_effect=RuntimeError("MIME composition failed"),
    )
    def test_compose_failure_rolls_back_message_and_recipient(
        self, mock_compose, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """When compose_and_store_mime fails, Message and MessageRecipient are rolled back."""
        initial_msg_count = models.Message.objects.count()
        initial_rcpt_count = models.MessageRecipient.objects.count()

        with pytest.raises(RuntimeError, match="MIME composition failed"):
            send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        assert models.Message.objects.count() == initial_msg_count
        assert models.MessageRecipient.objects.count() == initial_rcpt_count
        mock_send_task.delay.assert_not_called()

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_signature_appended_to_mime(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Template with a linked signature includes the signature content in MIME."""
        signature = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
            html_body="<p>-- Best regards, Test</p>",
            text_body="-- Best regards, Test",
        )
        autoreply_template.signature = signature
        autoreply_template.save()

        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        mime_bytes = autoreply_msg.blob.get_content()
        mime_str = mime_bytes.decode("utf-8", errors="replace")
        assert "Best regards, Test" in mime_str

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_domain_forced_signature_overrides_template(
        self, mock_dkim, mock_send_task, mailbox, autoreply_template, inbound_message
    ):
        """Forced domain signature takes priority over the template's own signature."""
        template_sig = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
            html_body="<p>Template Signature</p>",
            text_body="Template Signature",
        )
        factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.SIGNATURE,
            maildomain=mailbox.domain,
            is_active=True,
            is_forced=True,
            html_body="<p>Forced Domain Signature</p>",
            text_body="Forced Domain Signature",
        )
        autoreply_template.signature = template_sig
        autoreply_template.save()

        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        mime_bytes = autoreply_msg.blob.get_content()
        mime_str = mime_bytes.decode("utf-8", errors="replace")
        assert "Forced Domain Signature" in mime_str
        # The template's own signature should NOT appear
        assert "Template Signature" not in mime_str

    @patch("core.mda.outbound_tasks.send_message_task", new_callable=MagicMock)
    @patch("core.mda.outbound.sign_message_dkim", return_value=None)
    def test_placeholder_resolution_in_body(
        self, mock_dkim, mock_send_task, mailbox, inbound_message
    ):
        """Template placeholders {name} and {recipient_name} are resolved in MIME."""
        # Ensure mailbox contact has a name
        mailbox.contact.name = "Alice Dupont"
        mailbox.contact.save()

        # Ensure inbound sender contact has a name (will be the autoreply TO recipient)
        inbound_message.sender.name = "Bob Martin"
        inbound_message.sender.save()

        # Create template with placeholders via factory (html_body is a read-only property)
        template = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
            html_body="<p>Bonjour {recipient_name}, de {name}</p>",
            text_body="Bonjour {recipient_name}, de {name}",
        )

        send_autoreply_for_message(template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        mime_bytes = autoreply_msg.blob.get_content()
        mime_str = mime_bytes.decode("utf-8", errors="replace")
        assert "Alice Dupont" in mime_str
        assert "Bob Martin" in mime_str

    def test_has_attachments_persisted_with_inline_signature_images(
        self, mailbox, autoreply_template, inbound_message
    ):
        """has_attachments is saved when signature contains inline base64 images."""
        small_b64 = base64.b64encode(b"\x89PNG" + b"\x00" * 4).decode()
        signature = factories.MessageTemplateFactory(
            type=MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
            html_body=f'<img src="data:image/png;base64,{small_b64}">',
            text_body="",
        )
        autoreply_template.signature = signature
        autoreply_template.save()

        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        autoreply_msg = models.Message.objects.filter(
            parent=inbound_message, is_sender=True
        ).last()
        # Reload from DB to verify persisted value
        autoreply_msg.refresh_from_db()
        assert autoreply_msg.has_attachments is True

    def test_does_not_update_sender_read_at(
        self, mailbox, autoreply_template, inbound_message
    ):
        """Autoreply must NOT mark the thread as read for the sender.

        The sender has autoreply enabled because they are away. The thread
        should remain unread so they can see new messages when they return.
        """
        access = models.ThreadAccess.objects.get(
            mailbox=mailbox, thread=inbound_message.thread
        )
        assert access.read_at is None

        send_autoreply_for_message(autoreply_template, mailbox, inbound_message)

        access.refresh_from_db()
        assert access.read_at is None
