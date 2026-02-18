"""Selfcheck reporting: webhook and structured logging."""

import logging

import requests
from django.conf import settings

from core.mda.selfcheck import SelfCheckResult

logger = logging.getLogger(__name__)


def report_selfcheck(result: SelfCheckResult):
    """Report selfcheck result via structured logging and webhook."""
    log_selfcheck_result(result)
    send_selfcheck_webhook(result)


def log_selfcheck_result(result: SelfCheckResult):
    """Emit a structured log line."""
    if result["success"]:
        logger.info(
            "selfcheck_completed success=true send_time=%.3f reception_time=%.3f",
            result["send_time"],
            result["reception_time"],
        )
    else:
        logger.error(
            'selfcheck_completed success=false error="%s"',
            result.get("error", "unknown"),
        )


def send_selfcheck_webhook(result: SelfCheckResult):
    """POST to selfcheck webhook on success only."""
    webhook_url = settings.MESSAGES_SELFCHECK_WEBHOOK_URL
    if not webhook_url:
        return

    if not result["success"]:
        return

    try:
        response = requests.post(
            webhook_url,
            json={
                "send_time": result["send_time"],
                "reception_time": result["reception_time"],
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Failed to send selfcheck webhook", exc_info=True)
