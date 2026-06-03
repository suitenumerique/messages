"""Tests for selfcheck reporting (webhook + structured logging)."""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

import responses

from core.mda.selfcheck_reporting import (
    SelfCheckResult,
    finish_sentry_checkin,
    report_selfcheck,
    start_sentry_checkin,
)

WEBHOOK_URL = "https://example.com/api/checks/xxxx/webhook"

SUCCESS_RESULT: SelfCheckResult = {
    "success": True,
    "error": None,
    "send_time": 0.150,
    "reception_time": 2.340,
}

FAILURE_RESULT: SelfCheckResult = {
    "success": False,
    "error": "Message not received within 60 seconds",
    "send_time": None,
    "reception_time": None,
}


class TestLogSelfcheckResult(TestCase):
    """Tests for structured log output."""

    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=None)
    def test_success_log(self):
        """INFO log with timing data on success."""
        with self.assertLogs("core.mda.selfcheck_reporting", level="INFO") as cm:
            report_selfcheck(SUCCESS_RESULT)

        log_output = "\n".join(cm.output)
        self.assertIn(
            "selfcheck_completed success=true send_time=0.150 reception_time=2.340",
            log_output,
        )

    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=None)
    def test_success_log_without_timing(self):
        """INFO log without timing data when times are None."""
        result: SelfCheckResult = {
            "success": True,
            "error": None,
            "send_time": None,
            "reception_time": None,
        }
        with self.assertLogs("core.mda.selfcheck_reporting", level="INFO") as cm:
            report_selfcheck(result)

        log_output = "\n".join(cm.output)
        self.assertIn("selfcheck_completed success=true", log_output)
        self.assertNotIn("send_time", log_output)

    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=None)
    def test_failure_log(self):
        """ERROR log with error message on failure."""
        with self.assertLogs("core.mda.selfcheck_reporting", level="ERROR") as cm:
            report_selfcheck(FAILURE_RESULT)

        log_output = "\n".join(cm.output)
        self.assertIn(
            'selfcheck_completed success=false error="Message not received within 60 seconds"',
            log_output,
        )


class TestSendSelfcheckWebhook(TestCase):
    """Tests for selfcheck webhook sending."""

    @responses.activate
    def test_no_request_when_url_not_configured(self):
        """No HTTP call when MESSAGES_SELFCHECK_WEBHOOK_URL is None."""
        report_selfcheck(SUCCESS_RESULT)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=WEBHOOK_URL)
    def test_no_request_on_failure(self):
        """No HTTP call when selfcheck failed."""
        report_selfcheck(FAILURE_RESULT)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=WEBHOOK_URL)
    def test_webhook_sent_on_success(self):
        """POST with timing data on success."""
        responses.add(responses.POST, WEBHOOK_URL, status=200)

        report_selfcheck(SUCCESS_RESULT)

        self.assertEqual(len(responses.calls), 1)
        call = responses.calls[0]
        self.assertEqual(call.request.url, WEBHOOK_URL)
        payload = json.loads(call.request.body)
        self.assertEqual(payload, {"send_time": 0.15, "reception_time": 2.34})

    @responses.activate
    @override_settings(MESSAGES_SELFCHECK_WEBHOOK_URL=WEBHOOK_URL)
    def test_webhook_http_error_logged_not_raised(self):
        """HTTP 500 logs warning but doesn't raise."""
        responses.add(responses.POST, WEBHOOK_URL, status=500)

        with self.assertLogs("core.mda.selfcheck_reporting", level="WARNING") as cm:
            # Should not raise
            report_selfcheck(SUCCESS_RESULT)

        self.assertTrue(
            any("Failed to send selfcheck webhook" in line for line in cm.output)
        )


SENTRY_SLUG = "messages-selfcheck"
SENTRY_DSN = "https://public@sentry.example.com/1"


class TestStartSentryCheckin(TestCase):
    """Tests for opening a Sentry cron check-in."""

    @override_settings(MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=None)
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_noop_when_slug_unset(self, mock_capture):
        """No Sentry call when slug is not configured; returns None."""
        self.assertIsNone(start_sentry_checkin())
        mock_capture.assert_not_called()

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=None
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_warns_and_skips_when_dsn_missing(self, mock_capture):
        """Slug set without SENTRY_DSN: warn loudly and skip the call."""
        with self.assertLogs("core.mda.selfcheck_reporting", level="WARNING") as cm:
            self.assertIsNone(start_sentry_checkin())

        mock_capture.assert_not_called()
        self.assertTrue(
            any(
                "MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG is set but SENTRY_DSN is not"
                in line
                for line in cm.output
            )
        )

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_opens_in_progress_checkin(self, mock_capture):
        """Sends IN_PROGRESS check-in and returns the check_in_id."""
        mock_capture.return_value = "abc123"

        check_in_id = start_sentry_checkin()

        self.assertEqual(check_in_id, "abc123")
        mock_capture.assert_called_once_with(
            monitor_slug=SENTRY_SLUG,
            status="in_progress",
        )

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_error_swallowed(self, mock_capture):
        """Failure in capture_checkin logs a warning and returns None."""
        mock_capture.side_effect = RuntimeError("sentry down")

        with self.assertLogs("core.mda.selfcheck_reporting", level="WARNING") as cm:
            self.assertIsNone(start_sentry_checkin())

        self.assertTrue(
            any(
                "Failed to open Sentry selfcheck check-in" in line for line in cm.output
            )
        )


class TestFinishSentryCheckin(TestCase):
    """Tests for closing a Sentry cron check-in."""

    @override_settings(MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=None)
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_noop_when_slug_unset(self, mock_capture):
        """No Sentry call when slug is not configured."""
        finish_sentry_checkin("abc123", SUCCESS_RESULT)
        mock_capture.assert_not_called()

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_noop_when_check_in_id_missing(self, mock_capture):
        """No Sentry call when start_sentry_checkin returned None."""
        finish_sentry_checkin(None, SUCCESS_RESULT)
        mock_capture.assert_not_called()

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=None
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_skips_when_dsn_missing(self, mock_capture):
        """Slug set without SENTRY_DSN: skip the call.

        (No warning expected here — start_sentry_checkin already warned and
        returned None, so finish_sentry_checkin short-circuits on
        check_in_id before the misconfig check.)
        """
        finish_sentry_checkin("abc123", SUCCESS_RESULT)
        mock_capture.assert_not_called()

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_ok_with_duration_on_success(self, mock_capture):
        """OK status with send+reception duration on success."""
        finish_sentry_checkin("abc123", SUCCESS_RESULT)

        mock_capture.assert_called_once_with(
            monitor_slug=SENTRY_SLUG,
            check_in_id="abc123",
            status="ok",
            duration=0.150 + 2.340,
        )

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_error_without_duration_on_failure(self, mock_capture):
        """ERROR status and no duration when timing data is missing."""
        finish_sentry_checkin("abc123", FAILURE_RESULT)

        mock_capture.assert_called_once_with(
            monitor_slug=SENTRY_SLUG,
            check_in_id="abc123",
            status="error",
            duration=None,
        )

    @override_settings(
        MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG=SENTRY_SLUG, SENTRY_DSN=SENTRY_DSN
    )
    @patch("core.mda.selfcheck_reporting.capture_checkin")
    def test_error_swallowed(self, mock_capture):
        """Failure in capture_checkin logs a warning, no raise."""
        mock_capture.side_effect = RuntimeError("sentry down")

        with self.assertLogs("core.mda.selfcheck_reporting", level="WARNING") as cm:
            finish_sentry_checkin("abc123", SUCCESS_RESULT)

        self.assertTrue(
            any(
                "Failed to close Sentry selfcheck check-in" in line
                for line in cm.output
            )
        )
