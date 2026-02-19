"""Outbound message throttling service.

Throttles external recipients sent from mailboxes and maildomains using Django
cache counters with fixed time windows.
"""

import logging
import math
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.mda.inbound import count_external_recipients
from core.utils import ThrottleRateValue

logger = logging.getLogger(__name__)

# Shared instance for parsing rate strings
_rate_parser = ThrottleRateValue()


def _normalize_rate(rate):
    """Normalize a throttle rate setting.

    Accepts a pre-parsed tuple (from ThrottleRateValue in settings) or a raw
    string (from override_settings in tests). Returns the parsed tuple or None.
    """
    if rate is None:
        return None
    if isinstance(rate, tuple):
        return rate
    return _rate_parser.to_python(rate)


class ThrottleLimitExceeded(Exception):
    """Raised when a throttle limit is exceeded."""

    def __init__(
        self,
        message: str,
        entity_type: str,
        current: int,
        limit: int,
        retry_after: int,
    ):
        self.entity_type = entity_type  # "mailbox" or "maildomain"
        self.current = current
        self.limit = limit
        self.retry_after = retry_after  # seconds until window resets
        super().__init__(message)


def get_period_key(period_name: str) -> str:
    """
    Get the cache key suffix for the current time period.

    For "day": "2026-01-25"
    For "hour": "2026-01-25-14"
    For "minute": "2026-01-25-14-30"
    """
    now = timezone.now()
    if period_name == "day":
        return now.strftime("%Y-%m-%d")
    elif period_name == "hour":
        return now.strftime("%Y-%m-%d-%H")
    elif period_name == "minute":
        return now.strftime("%Y-%m-%d-%H-%M")
    else:
        return now.strftime("%Y-%m-%d")


def get_period_expiry(period_name: str) -> int:
    """Get the number of seconds until the current period expires."""
    now = timezone.now()

    if period_name == "day":
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((tomorrow - now).total_seconds())
    elif period_name == "hour":
        next_hour = (now + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
        return int((next_hour - now).total_seconds())
    elif period_name == "minute":
        next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        return int((next_minute - now).total_seconds())
    else:
        return 86400


def get_throttle_cache_key(entity_type: str, entity_id: str, period_key: str) -> str:
    """Build the cache key for a throttle counter."""
    return f"throttle:{entity_type}:{entity_id}:ext_recip:{period_key}"


def get_current_usage(cache_key: str) -> int:
    """Get the current counter value from cache."""
    value = cache.get(cache_key)
    return int(value) if value is not None else 0


def increment_counter(cache_key: str, amount: int, expiry_seconds: int) -> int:
    """
    Increment a counter in cache and return the new value.

    Uses cache.incr() for atomic operations. Falls back to cache.set()
    when the key doesn't exist yet.
    """
    try:
        return cache.incr(cache_key, amount)
    except ValueError:
        # Key doesn't exist yet — initialize it
        cache.set(cache_key, amount, expiry_seconds)
        return amount


def decrement_counter(cache_key: str, amount: int, expiry_seconds: int) -> int:
    """
    Decrement a counter in cache and return the new value.

    Used for rollback when a race condition is detected.
    """
    try:
        new_value = cache.decr(cache_key, amount)
        return max(0, new_value)
    except ValueError:
        # Key doesn't exist — nothing to rollback
        return 0


def check_and_increment_throttle(mailbox, maildomain, message) -> None:
    """
    Check throttle limits and increment counters for external recipients.

    Raises ThrottleLimitExceeded if either mailbox or maildomain limit would be exceeded.

    Flow:
    1. Check if throttling is configured, return early if not
    2. Count external recipients in the message
    3. If zero external recipients, return immediately
    4. Get current usage for both mailbox and maildomain
    5. Check if adding would exceed either limit
    6. If OK, increment both counters
    7. Verify post-increment (race condition check), rollback if exceeded
    """
    mailbox_rate = _normalize_rate(
        settings.THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS
    )
    maildomain_rate = _normalize_rate(
        settings.THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS
    )

    # If no throttling configured, allow
    if not mailbox_rate and not maildomain_rate:
        return

    # Count external recipients (DB query)
    external_count = count_external_recipients(message)
    if external_count == 0:
        return  # No external recipients, nothing to throttle

    # Build cache keys and get current values
    checks = []

    if mailbox_rate:
        limit, period_name, _ = mailbox_rate
        period_key = get_period_key(period_name)
        cache_key = get_throttle_cache_key("mailbox", str(mailbox.id), period_key)
        current = get_current_usage(cache_key)
        expiry = get_period_expiry(period_name)
        checks.append(
            {
                "entity_type": "mailbox",
                "entity_name": str(mailbox),
                "cache_key": cache_key,
                "current": current,
                "limit": limit,
                "period_name": period_name,
                "expiry": expiry,
            }
        )

    if maildomain_rate:
        limit, period_name, _ = maildomain_rate
        period_key = get_period_key(period_name)
        cache_key = get_throttle_cache_key("maildomain", str(maildomain.id), period_key)
        current = get_current_usage(cache_key)
        expiry = get_period_expiry(period_name)
        checks.append(
            {
                "entity_type": "maildomain",
                "entity_name": maildomain.name,
                "cache_key": cache_key,
                "current": current,
                "limit": limit,
                "period_name": period_name,
                "expiry": expiry,
            }
        )

    # Check if adding external_count would exceed any limit
    for check in checks:
        if check["current"] + external_count > check["limit"]:
            raise ThrottleLimitExceeded(
                message=(
                    f"Rate limit exceeded: {check['current']}/{check['limit']} external recipients "
                    f"this {check['period_name']}. Tried to add {external_count} more. "
                    f"Resets in {format_duration(check['expiry'])}."
                ),
                entity_type=check["entity_type"],
                current=check["current"],
                limit=check["limit"],
                retry_after=check["expiry"],
            )

    # Increment all counters
    incremented = []
    try:
        for check in checks:
            new_value = increment_counter(
                check["cache_key"], external_count, check["expiry"]
            )
            incremented.append((check, new_value))

            # Race condition check: verify we didn't exceed after increment
            if new_value > check["limit"]:
                # Rollback all incremented counters
                for inc_check, _ in incremented:
                    decrement_counter(
                        inc_check["cache_key"], external_count, inc_check["expiry"]
                    )

                raise ThrottleLimitExceeded(
                    message=(
                        f"Rate limit exceeded: {check['limit']}/{check['limit']} external recipients "
                        f"this {check['period_name']}. Resets in {format_duration(check['expiry'])}."
                    ),
                    entity_type=check["entity_type"],
                    current=new_value - external_count,
                    limit=check["limit"],
                    retry_after=check["expiry"],
                )
    except ThrottleLimitExceeded:
        raise
    except Exception as e:
        # On any error, try to rollback
        logger.error("Error during throttle increment, rolling back: %s", e)
        for inc_check, _ in incremented:
            try:
                decrement_counter(
                    inc_check["cache_key"], external_count, inc_check["expiry"]
                )
            except Exception as rollback_error:
                logger.error("Failed to rollback throttle counter: %s", rollback_error)
        raise


def get_throttle_status(mailbox=None, maildomain=None) -> dict[str, Any]:
    """
    Get current throttle status for Django admin display.

    Returns dict with current usage and limits (empty dict if no throttling configured).
    """
    result = {}

    if mailbox:
        mailbox_rate = _normalize_rate(
            settings.THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS
        )
        if mailbox_rate:
            limit, period_name, _ = mailbox_rate
            period_key = get_period_key(period_name)
            cache_key = get_throttle_cache_key("mailbox", str(mailbox.id), period_key)
            current = get_current_usage(cache_key)
            expiry = get_period_expiry(period_name)
            result["mailbox"] = {
                "current": current,
                "limit": limit,
                "period": period_name,
                "reset_in_seconds": expiry,
                "reset_in_human": format_duration(expiry),
            }

    if maildomain:
        maildomain_rate = _normalize_rate(
            settings.THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS
        )
        if maildomain_rate:
            limit, period_name, _ = maildomain_rate
            period_key = get_period_key(period_name)
            cache_key = get_throttle_cache_key(
                "maildomain", str(maildomain.id), period_key
            )
            current = get_current_usage(cache_key)
            expiry = get_period_expiry(period_name)
            result["maildomain"] = {
                "current": current,
                "limit": limit,
                "period": period_name,
                "reset_in_seconds": expiry,
                "reset_in_human": format_duration(expiry),
            }

    return result


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = math.ceil(seconds / 60)
        return f"{minutes}m"
    else:
        hours = math.ceil(seconds / 3600)
        return f"{hours}h"
