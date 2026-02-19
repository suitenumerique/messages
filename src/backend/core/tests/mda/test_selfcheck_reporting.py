"""Tests for selfcheck reporting (webhook + structured logging)."""

import json

from django.test import TestCase, override_settings

import responses

from core.mda.selfcheck_reporting import SelfCheckResult, report_selfcheck

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
