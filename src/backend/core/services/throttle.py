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
    if period_name == "hour":
        return now.strftime("%Y-%m-%d-%H")
    if period_name == "minute":
        return now.strftime("%Y-%m-%d-%H-%M")
    return now.strftime("%Y-%m-%d")


def get_period_expiry(period_name: str) -> int:
    """Get the number of seconds until the current period expires."""
    now = timezone.now()

    if period_name == "day":
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((tomorrow - now).total_seconds())
    if period_name == "hour":
        next_hour = (now + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
        return int((next_hour - now).total_seconds())
    if period_name == "minute":
        next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        return int((next_minute - now).total_seconds())
    return 86400


def get_throttle_cache_key(
    entity_type: str, entity_id: str, period_key: str, counter_type: str = "ext_recip"
) -> str:
    """Build the cache key for a throttle counter."""
    return f"throttle:{entity_type}:{entity_id}:{counter_type}:{period_key}"


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


def decrement_counter(cache_key: str, amount: int, expiry_seconds: int) -> int:  # pylint: disable=unused-argument
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


class ThrottleManager:
    """Central interface for throttle operations.

    As a context manager, provides atomic check-and-increment with automatic
    rollback on any exception (including ThrottleLimitExceeded), mirroring
    the semantics of ``transaction.atomic()``.

    Also exposes ``get_status()`` for read-only throttle introspection.

    Usage::

        with ThrottleManager() as throttle:
            throttle.check_limit(rate1, "mailbox", mb_id, amount=5)
            throttle.check_limit(rate2, "maildomain", md_id, amount=5)

        status = ThrottleManager.get_status(rate, "mailbox", mb_id)
    """

    def __init__(self):
        self._incremented: list[tuple[str, int, int]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._rollback()
        return False

    def _rollback(self):
        for cache_key, amount, expiry in self._incremented:
            decrement_counter(cache_key, amount, expiry)
        self._incremented.clear()

    @staticmethod
    def _resolve(rate_setting, entity_type, entity_id, counter_type="ext_recip"):
        """Resolve a rate setting into throttle state.

        Returns (limit, period_name, cache_key, current, expiry) or None
        if disabled.
        """
        rate = _normalize_rate(rate_setting)
        if not rate:
            return None
        limit, period_name, _ = rate
        period_key = get_period_key(period_name)
        cache_key = get_throttle_cache_key(
            entity_type, entity_id, period_key, counter_type
        )
        current = get_current_usage(cache_key)
        expiry = get_period_expiry(period_name)
        return limit, period_name, cache_key, current, expiry

    def check_limit(
        self,
        rate_setting,
        entity_type: str,
        entity_id: str,
        amount: int = 1,
        counter_type: str = "ext_recip",
    ) -> None:
        """Check a throttle limit and increment the counter.

        Raises ThrottleLimitExceeded if the limit would be exceeded.
        Does nothing if rate_setting is None (throttling disabled).
        """
        state = self._resolve(rate_setting, entity_type, entity_id, counter_type)
        if not state:
            return

        limit, period_name, cache_key, current, expiry = state

        if current + amount > limit:
            raise ThrottleLimitExceeded(
                message=(
                    f"Rate limit exceeded: {current}/{limit} "
                    f"this {period_name}. Tried to add {amount} more. "
                    f"Resets in {format_duration(expiry)}."
                ),
                entity_type=entity_type,
                current=current,
                limit=limit,
                retry_after=expiry,
            )

        new_value = increment_counter(cache_key, amount, expiry)
        self._incremented.append((cache_key, amount, expiry))

        # Race condition check
        if new_value > limit:
            self._rollback()
            raise ThrottleLimitExceeded(
                message=(
                    f"Rate limit exceeded: {limit}/{limit} "
                    f"this {period_name}. Resets in {format_duration(expiry)}."
                ),
                entity_type=entity_type,
                current=new_value - amount,
                limit=limit,
                retry_after=expiry,
            )

    @staticmethod
    def get_status(
        rate_setting,
        entity_type: str,
        entity_id: str,
        counter_type: str = "ext_recip",
    ) -> dict[str, Any] | None:
        """Return current throttle status for a single entity, or None if disabled."""
        state = ThrottleManager._resolve(
            rate_setting, entity_type, entity_id, counter_type
        )
        if not state:
            return None
        limit, period_name, _, current, expiry = state
        return {
            "current": current,
            "limit": limit,
            "period": period_name,
            "reset_in_seconds": expiry,
            "reset_in_human": format_duration(expiry),
        }


def check_and_increment_throttle(mailbox, maildomain, message) -> None:
    """
    Check throttle limits and increment counters for external recipients.

    Raises ThrottleLimitExceeded if either mailbox or maildomain limit would be exceeded.
    Both counters are rolled back if either limit is exceeded.
    """
    mailbox_rate = _normalize_rate(
        settings.THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS
    )
    maildomain_rate = _normalize_rate(
        settings.THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS
    )

    if not mailbox_rate and not maildomain_rate:
        return

    external_count = count_external_recipients(message)
    if external_count == 0:
        return

    with ThrottleManager() as throttle:
        throttle.check_limit(
            mailbox_rate, "mailbox", str(mailbox.id), amount=external_count
        )
        throttle.check_limit(
            maildomain_rate, "maildomain", str(maildomain.id), amount=external_count
        )


def get_throttle_status(mailbox=None, maildomain=None) -> dict[str, Any]:
    """
    Get current throttle status for Django admin display.

    Returns dict with current usage and limits (empty dict if no throttling configured).
    """
    result = {}

    if mailbox:
        status = ThrottleManager.get_status(
            settings.THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS,
            "mailbox",
            str(mailbox.id),
        )
        if status:
            result["mailbox"] = status

    if maildomain:
        status = ThrottleManager.get_status(
            settings.THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS,
            "maildomain",
            str(maildomain.id),
        )
        if status:
            result["maildomain"] = status

    return result


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = math.ceil(seconds / 60)
        return f"{minutes}m"
    hours = math.ceil(seconds / 3600)
    return f"{hours}h"
