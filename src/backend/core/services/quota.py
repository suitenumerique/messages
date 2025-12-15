"""
Recipient quota service.

This service provides high-performance quota tracking using Redis with automatic
TTL-based period resets. Quotas are independent (mailbox and domain quotas are
checked separately without dynamic capacity constraints).
"""

from datetime import datetime
from typing import Literal

from django.utils import timezone

EntityType = Literal["mailbox", "domain"]
PeriodType = Literal["d", "m", "y"]

PERIOD_DISPLAY_MAP = {"d": "day", "m": "month", "y": "year"}


def get_period_start(period: PeriodType, now: datetime | None = None) -> datetime:
    """
    Calculate the start of the current period.

    Args:
        period: "d" (day), "m" (month), or "y" (year)
        now: Current datetime (defaults to timezone.now())

    Returns:
        Start of the current period as datetime
    """
    if now is None:
        now = timezone.now()

    if period == "d":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "m":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "y":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now


def get_period_display(period: PeriodType) -> str:
    """
    Get human-readable period name.

    Args:
        period: "d", "m", or "y"

    Returns:
        "day", "month", or "year"
    """
    return PERIOD_DISPLAY_MAP.get(period, period)


class RecipientQuotaService:
    """Service for managing recipient quotas in Redis."""

    def __init__(self):
        """Initialize service (Redis connection is lazy-loaded)."""
        self._redis = None

    @property
    def redis(self):
        """Lazy-load Redis connection."""
        if self._redis is None:
            from django.conf import settings

            import redis

            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )

        return self._redis

    def _get_key(
        self, entity_type: EntityType, entity_id: str, period: PeriodType
    ) -> str:
        """
        Generate Redis key for a quota.

        Format: quota:{entity_type}:{entity_id}:{period}:{period_key}
        Example: quota:mailbox:abc-123:d:2025-12-11
        """
        period_key = self._get_period_key(period)
        return f"quota:{entity_type}:{entity_id}:{period}:{period_key}"

    def _get_period_key(self, period: PeriodType) -> str:
        """
        Get the period-specific key suffix.

        Returns:
            - "d" → "2025-12-11" (daily)
            - "m" → "2025-12" (monthly)
            - "y" → "2025" (yearly)
        """
        now = timezone.now()
        if period == "d":
            return now.strftime("%Y-%m-%d")
        elif period == "m":
            return now.strftime("%Y-%m")
        elif period == "y":
            return now.strftime("%Y")
        raise ValueError(f"Invalid period: {period}")

    def _get_ttl(self, period: PeriodType) -> int:
        """
        Get TTL in seconds for the period.

        Adds a small buffer to ensure the key expires after the period ends.
        """
        if period == "d":
            # Expire at end of day + 1 hour buffer
            now = timezone.now()
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
            return int((end_of_day - now).total_seconds()) + 3600
        elif period == "m":
            # 32 days = longest month (31 days) + 1 day safety buffer
            return 32 * 24 * 3600
        elif period == "y":
            # 367 days = leap year + 1 day safety buffer
            return 367 * 24 * 3600
        raise ValueError(f"Invalid period: {period}")

    def get_count(
        self, entity_type: EntityType, entity_id: str, period: PeriodType
    ) -> int:
        """
        Get current recipient count for an entity.

        Args:
            entity_type: "mailbox" or "domain"
            entity_id: UUID of the entity
            period: "d", "m", or "y"

        Returns:
            Current recipient count (0 if key doesn't exist)
        """
        key = self._get_key(entity_type, entity_id, period)
        value = self.redis.get(key)
        return int(value) if value else 0

    def increment(
        self,
        entity_type: EntityType,
        entity_id: str,
        period: PeriodType,
        count: int,
    ) -> int:
        """
        Atomically increment the recipient count.

        Args:
            entity_type: "mailbox" or "domain"
            entity_id: UUID of the entity
            period: "d", "m", or "y"
            count: Number to increment by (can be negative for rollback)

        Returns:
            New total count after increment
        """
        key = self._get_key(entity_type, entity_id, period)
        ttl = self._get_ttl(period)

        pipe = self.redis.pipeline()
        pipe.incrby(key, count)
        pipe.expire(key, ttl)
        results = pipe.execute()
        return results[0]  # New count after increment

    def check_and_increment(
        self,
        mailbox_id: str,
        mailbox_period: PeriodType,
        mailbox_limit: int,
        domain_id: str,
        domain_period: PeriodType,
        domain_limit: int,
        recipient_count: int,
    ) -> tuple[bool, str, int]:
        """
        Atomically check and increment mailbox and domain quotas.

        Uses a single Lua script to check both quotas and increment them
        atomically, avoiding race conditions and rollback logic.

        Args:
            mailbox_id: UUID of the mailbox
            mailbox_period: Period for mailbox ("d" or "m")
            mailbox_limit: Mailbox quota limit
            domain_id: UUID of the domain
            domain_period: Period for domain ("d" or "m")
            domain_limit: Domain quota limit
            recipient_count: Number of recipients to add

        Returns:
            Tuple of (success, failed_entity, remaining)
            - success: True if both quotas allowed and incremented
            - failed_entity: "mailbox" or "domain" if one failed, "" if success
            - remaining: Remaining capacity of the entity that failed
        """
        mailbox_key = self._get_key("mailbox", mailbox_id, mailbox_period)
        domain_key = self._get_key("domain", domain_id, domain_period)
        mailbox_ttl = self._get_ttl(mailbox_period)
        domain_ttl = self._get_ttl(domain_period)

        # Lua script for atomic check-and-increment of BOTH quotas
        # Returns: {success (0/1), failed_entity (0=none, 1=mailbox, 2=domain), remaining}
        lua_script = """
        local mailbox_key = KEYS[1]
        local domain_key = KEYS[2]
        local mailbox_limit = tonumber(ARGV[1])
        local domain_limit = tonumber(ARGV[2])
        local increment = tonumber(ARGV[3])
        local mailbox_ttl = tonumber(ARGV[4])
        local domain_ttl = tonumber(ARGV[5])

        -- Get current values
        local mailbox_current = tonumber(redis.call('GET', mailbox_key) or '0')
        local domain_current = tonumber(redis.call('GET', domain_key) or '0')

        -- Check mailbox quota first
        if mailbox_current + increment > mailbox_limit then
            return {0, 1, mailbox_limit - mailbox_current}  -- Mailbox failed
        end

        -- Check domain quota
        if domain_current + increment > domain_limit then
            return {0, 2, domain_limit - domain_current}  -- Domain failed
        end

        -- Both OK: increment both atomically
        redis.call('INCRBY', mailbox_key, increment)
        redis.call('EXPIRE', mailbox_key, mailbox_ttl)
        redis.call('INCRBY', domain_key, increment)
        redis.call('EXPIRE', domain_key, domain_ttl)

        return {1, 0, 0}  -- Success
        """

        result = self.redis.eval(
            lua_script,
            2,  # 2 keys
            mailbox_key,
            domain_key,
            mailbox_limit,
            domain_limit,
            recipient_count,
            mailbox_ttl,
            domain_ttl,
        )

        success = bool(result[0])
        failed_code = int(result[1])
        remaining = int(result[2])

        failed_entity_map = {0: "", 1: "mailbox", 2: "domain"}
        failed_entity = failed_entity_map.get(failed_code, "")

        return success, failed_entity, remaining

    def reset(
        self, entity_type: EntityType, entity_id: str, period: PeriodType
    ) -> None:
        """
        Manually reset a quota (delete the key).

        Normally not needed as TTL handles expiry automatically.

        Args:
            entity_type: "mailbox" or "domain"
            entity_id: UUID of the entity
            period: "d", "m", or "y"
        """
        key = self._get_key(entity_type, entity_id, period)
        self.redis.delete(key)

    def get_status(
        self,
        entity_type: EntityType,
        entity_id: str,
        period: PeriodType,
        limit: int,
    ) -> dict:
        """
        Get quota status with usage statistics.

        Args:
            entity_type: "mailbox" or "domain"
            entity_id: UUID of the entity
            period: "d", "m", or "y"
            limit: Maximum allowed recipients for this period

        Returns:
            Dictionary with:
                - recipient_count: Current count
                - quota_limit: Maximum limit
                - remaining: Recipients remaining
                - usage_percentage: Percentage used (0-100)
        """
        current_count = self.get_count(entity_type, entity_id, period)
        remaining = max(0, limit - current_count)
        usage_percentage = int((current_count / limit) * 100) if limit > 0 else 0

        return {
            "recipient_count": current_count,
            "quota_limit": limit,
            "remaining": remaining,
            "usage_percentage": min(100, usage_percentage),  # Cap at 100%
        }


# Singleton instance for quota management.
# This avoids creating a new Redis connection on each call.
# Usage: from core.services.quota import quota_service
quota_service = RecipientQuotaService()
