"""Selfcheck reporting: webhook, Sentry crons, and structured logging."""

import logging
from typing import Optional, TypedDict

from django.conf import settings

import requests
from sentry_sdk.crons import capture_checkin
from sentry_sdk.crons.consts import MonitorStatus

logger = logging.getLogger(__name__)


class SelfCheckResult(TypedDict):
    """Result of a selfcheck run."""

    success: bool
    error: Optional[str]
    send_time: Optional[float]
    reception_time: Optional[float]


def report_selfcheck(result: SelfCheckResult):
    """Report selfcheck result via structured logging and webhook."""
    log_selfcheck_result(result)
    send_selfcheck_webhook(result)


def log_selfcheck_result(result: SelfCheckResult):
    """Emit a structured log line."""
    if result["success"]:
        send_time = result["send_time"]
        reception_time = result["reception_time"]
        if send_time is not None and reception_time is not None:
            logger.info(
                "selfcheck_completed success=true send_time=%.3f reception_time=%.3f",
                send_time,
                reception_time,
            )
        else:
            logger.info("selfcheck_completed success=true")
    else:
        logger.error(
            'selfcheck_completed success=false error="%s"',
            result.get("error", "unknown"),
        )


def start_sentry_checkin() -> Optional[str]:
    """Open a Sentry cron check-in if configured. Returns the check_in_id."""
    slug = settings.MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG
    if not slug:
        return None

    try:
        return capture_checkin(
            monitor_slug=slug,
            status=MonitorStatus.IN_PROGRESS,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to open Sentry selfcheck check-in", exc_info=True)
        return None


def finish_sentry_checkin(check_in_id: Optional[str], result: SelfCheckResult):
    """Close a previously opened Sentry cron check-in with OK/ERROR.

    Reports an explicit duration when both send and reception times are
    known — Sentry would otherwise infer it from the check-in timestamp
    delta, which also includes the post-run cleanup sleep.
    """
    slug = settings.MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG
    if not slug or not check_in_id:
        return

    status = MonitorStatus.OK if result["success"] else MonitorStatus.ERROR
    send_time = result["send_time"]
    reception_time = result["reception_time"]
    duration = (
        send_time + reception_time
        if send_time is not None and reception_time is not None
        else None
    )
    try:
        capture_checkin(
            monitor_slug=slug,
            check_in_id=check_in_id,
            status=status,
            duration=duration,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to close Sentry selfcheck check-in", exc_info=True)


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
