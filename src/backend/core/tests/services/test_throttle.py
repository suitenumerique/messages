"""Tests for the throttle service."""

# pylint: disable=redefined-outer-name

from django.core.cache import cache
from django.test import override_settings

import pytest

from core import factories
from core.mda.inbound import count_external_recipients
from core.services.throttle import (
    ThrottleLimitExceeded,
    ThrottleManager,
    check_and_increment_throttle,
    format_duration,
    get_current_usage,
    get_period_key,
    get_throttle_cache_key,
    get_throttle_status,
    increment_counter,
)
from core.utils import ThrottleRateValue


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


class TestThrottleRateValue:
    """Tests for ThrottleRateValue configuration class."""

    def test_parse_valid_rate_per_day(self):
        """Test parsing a valid rate per day."""
        value = ThrottleRateValue()
        result = value.to_python("1000/day")
        assert result == (1000, "day", 86400)

    def test_parse_valid_rate_per_hour(self):
        """Test parsing a valid rate per hour."""
        value = ThrottleRateValue()
        result = value.to_python("100/hour")
        assert result == (100, "hour", 3600)

    def test_parse_valid_rate_per_minute(self):
        """Test parsing a valid rate per minute."""
        value = ThrottleRateValue()
        result = value.to_python("10/minute")
        assert result == (10, "minute", 60)

    def test_parse_none_returns_none(self):
        """Test that None input returns None."""
        value = ThrottleRateValue()
        assert value.to_python(None) is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None."""
        value = ThrottleRateValue()
        assert value.to_python("") is None

    def test_parse_invalid_format_raises(self):
        """Test that invalid format raises ValueError."""
        value = ThrottleRateValue()
        with pytest.raises(ValueError, match="Invalid throttle rate format"):
            value.to_python("invalid")
        with pytest.raises(ValueError, match="Invalid throttle rate format"):
            value.to_python("100")
        with pytest.raises(ValueError, match="Invalid throttle rate format"):
            value.to_python("/day")

    def test_parse_invalid_period_raises(self):
        """Test that invalid period raises ValueError."""
        value = ThrottleRateValue()
        with pytest.raises(ValueError, match="Invalid throttle period"):
            value.to_python("100/week")
        with pytest.raises(ValueError, match="Invalid throttle period"):
            value.to_python("100/year")


class TestGetPeriodKey:
    """Tests for get_period_key function."""

    def test_period_key_day(self):
        """Test period key for day."""
        key = get_period_key("day")
        # Should be in format YYYY-MM-DD
        assert len(key) == 10
        assert key.count("-") == 2

    def test_period_key_hour(self):
        """Test period key for hour."""
        key = get_period_key("hour")
        # Should be in format YYYY-MM-DD-HH
        assert len(key) == 13
        assert key.count("-") == 3

    def test_period_key_minute(self):
        """Test period key for minute."""
        key = get_period_key("minute")
        # Should be in format YYYY-MM-DD-HH-MM
        assert len(key) == 16
        assert key.count("-") == 4


class TestGetThrottleCacheKey:
    """Tests for get_throttle_cache_key function."""

    def test_mailbox_cache_key(self):
        """Test cache key for mailbox."""
        key = get_throttle_cache_key("mailbox", "123", "2026-01-25")
        assert key == "throttle:mailbox:123:ext_recip:2026-01-25"

    def test_maildomain_cache_key(self):
        """Test cache key for maildomain."""
        key = get_throttle_cache_key("maildomain", "456", "2026-01-25-14")
        assert key == "throttle:maildomain:456:ext_recip:2026-01-25-14"


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_duration(30) == "30s"
        assert format_duration(59) == "59s"

    def test_format_minutes(self):
        """Test formatting minutes."""
        assert format_duration(60) == "1m"
        assert format_duration(90) == "2m"
        assert format_duration(3599) == "60m"

    def test_format_hours(self):
        """Test formatting hours."""
        assert format_duration(3600) == "1h"
        assert format_duration(7200) == "2h"
        assert format_duration(86400) == "24h"


@pytest.mark.django_db
class TestCountExternalRecipients:
    """Tests for count_external_recipients function."""

    def test_count_all_external(self):
        """Test counting when all recipients are external."""
        # Create a message with external recipients
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add external recipients (domains not in our system)
        external_contact1 = factories.ContactFactory(
            mailbox=mailbox, email="user1@external.com"
        )
        external_contact2 = factories.ContactFactory(
            mailbox=mailbox, email="user2@otherdomain.org"
        )
        factories.MessageRecipientFactory(message=message, contact=external_contact1)
        factories.MessageRecipientFactory(message=message, contact=external_contact2)

        count = count_external_recipients(message)
        assert count == 2

    def test_count_all_internal(self):
        """Test counting when all recipients are internal."""
        # Create mailboxes (internal)
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        internal_mailbox = factories.MailboxFactory(domain=domain)

        sender_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{mailbox.local_part}@{domain.name}",
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add internal recipient - use an actual mailbox email that exists
        internal_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{internal_mailbox.local_part}@{internal_mailbox.domain.name}",
        )
        factories.MessageRecipientFactory(message=message, contact=internal_contact)

        count = count_external_recipients(message)
        assert count == 0

    def test_count_mixed_recipients(self):
        """Test counting with mixed internal and external recipients."""
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        internal_mailbox = factories.MailboxFactory(domain=domain)

        sender_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{mailbox.local_part}@{domain.name}",
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add one internal and one external recipient
        internal_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{internal_mailbox.local_part}@{internal_mailbox.domain.name}",
        )
        factories.MessageRecipientFactory(message=message, contact=internal_contact)
        external_contact = factories.ContactFactory(
            mailbox=mailbox, email="user@external.com"
        )
        factories.MessageRecipientFactory(message=message, contact=external_contact)

        count = count_external_recipients(message)
        assert count == 1


@pytest.mark.django_db
class TestCheckAndIncrementThrottle:
    """Tests for check_and_increment_throttle function."""

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS=None,
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_no_throttle_configured(self):
        """Test that no throttling occurs when not configured."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )
        external_contact = factories.ContactFactory(
            mailbox=mailbox, email="user@external.com"
        )
        factories.MessageRecipientFactory(message=message, contact=external_contact)

        # Should not raise
        check_and_increment_throttle(mailbox, mailbox.domain, message)

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="10/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_mailbox_throttle_allows_under_limit(self):
        """Test that requests under the mailbox limit are allowed."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )
        external_contact = factories.ContactFactory(
            mailbox=mailbox, email="user@external.com"
        )
        factories.MessageRecipientFactory(message=message, contact=external_contact)

        # Should not raise - first request
        check_and_increment_throttle(mailbox, mailbox.domain, message)

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="2/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_mailbox_throttle_blocks_over_limit(self):
        """Test that requests over the mailbox limit are blocked."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add 3 external recipients - should exceed limit of 2
        for i in range(3):
            contact = factories.ContactFactory(
                mailbox=mailbox, email=f"user{i}@external.com"
            )
            factories.MessageRecipientFactory(message=message, contact=contact)

        with pytest.raises(ThrottleLimitExceeded) as exc_info:
            check_and_increment_throttle(mailbox, mailbox.domain, message)

        assert exc_info.value.entity_type == "mailbox"
        assert exc_info.value.limit == 2

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS=None,
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS="5/day",
    )
    def test_maildomain_throttle_allows_under_limit(self):
        """Test that requests under the maildomain limit are allowed."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )
        external_contact = factories.ContactFactory(
            mailbox=mailbox, email="user@external.com"
        )
        factories.MessageRecipientFactory(message=message, contact=external_contact)

        # Should not raise
        check_and_increment_throttle(mailbox, mailbox.domain, message)

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="100/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS="2/day",
    )
    def test_maildomain_throttle_blocks_over_limit(self):
        """Test that requests over the maildomain limit are blocked."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add 3 external recipients - should exceed maildomain limit of 2
        for i in range(3):
            contact = factories.ContactFactory(
                mailbox=mailbox, email=f"user{i}@external.com"
            )
            factories.MessageRecipientFactory(message=message, contact=contact)

        with pytest.raises(ThrottleLimitExceeded) as exc_info:
            check_and_increment_throttle(mailbox, mailbox.domain, message)

        assert exc_info.value.entity_type == "maildomain"
        assert exc_info.value.limit == 2

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="5/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS="10/day",
    )
    def test_increments_counters(self):
        """Test that counters are incremented after successful check."""
        mailbox = factories.MailboxFactory()
        sender_contact = factories.ContactFactory(
            mailbox=mailbox, email=f"sender@{mailbox.domain.name}"
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add 2 external recipients
        for i in range(2):
            contact = factories.ContactFactory(
                mailbox=mailbox, email=f"user{i}@external.com"
            )
            factories.MessageRecipientFactory(message=message, contact=contact)

        # First call should succeed
        check_and_increment_throttle(mailbox, mailbox.domain, message)

        # Check the status - should show 2 used
        status = get_throttle_status(mailbox=mailbox, maildomain=mailbox.domain)
        assert status["mailbox"]["current"] == 2
        assert status["maildomain"]["current"] == 2

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="10/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_no_external_recipients_no_increment(self):
        """Test that no increment happens when there are no external recipients."""
        domain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=domain)
        internal_mailbox = factories.MailboxFactory(domain=domain)

        sender_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{mailbox.local_part}@{domain.name}",
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=True,
        )

        # Add only internal recipient - use an actual mailbox email
        internal_contact = factories.ContactFactory(
            mailbox=mailbox,
            email=f"{internal_mailbox.local_part}@{internal_mailbox.domain.name}",
        )
        factories.MessageRecipientFactory(message=message, contact=internal_contact)

        # Should not raise and should not increment
        check_and_increment_throttle(mailbox, mailbox.domain, message)

        status = get_throttle_status(mailbox=mailbox)
        assert status["mailbox"]["current"] == 0


@pytest.mark.django_db
class TestGetThrottleStatus:
    """Tests for get_throttle_status function."""

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS=None,
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_no_throttle_configured_returns_empty_dict(self):
        """Test that empty dict is returned when no throttle is configured."""
        mailbox = factories.MailboxFactory()
        status = get_throttle_status(mailbox=mailbox, maildomain=mailbox.domain)
        assert not status

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="100/day",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS=None,
    )
    def test_mailbox_status_only(self):
        """Test getting status for mailbox only."""
        mailbox = factories.MailboxFactory()
        status = get_throttle_status(mailbox=mailbox)

        assert "mailbox" in status
        assert status["mailbox"]["current"] == 0
        assert status["mailbox"]["limit"] == 100
        assert status["mailbox"]["period"] == "day"

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS=None,
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS="1000/hour",
    )
    def test_maildomain_status_only(self):
        """Test getting status for maildomain only."""
        maildomain = factories.MailDomainFactory()
        status = get_throttle_status(maildomain=maildomain)

        assert "maildomain" in status
        assert status["maildomain"]["current"] == 0
        assert status["maildomain"]["limit"] == 1000
        assert status["maildomain"]["period"] == "hour"

    @override_settings(
        THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS="50/hour",
        THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS="500/hour",
    )
    def test_both_status(self):
        """Test getting status for both mailbox and maildomain."""
        mailbox = factories.MailboxFactory()
        status = get_throttle_status(mailbox=mailbox, maildomain=mailbox.domain)

        assert "mailbox" in status
        assert "maildomain" in status
        assert status["mailbox"]["limit"] == 50
        assert status["maildomain"]["limit"] == 500


class TestThrottleManager:
    """Tests for the ThrottleManager context manager."""

    def test_rate_setting_none_does_nothing(self):
        """check_limit with None rate does not raise and leaves cache empty."""
        with ThrottleManager() as throttle:
            throttle.check_limit(None, "mailbox", "mb-noop")

        key = get_throttle_cache_key("mailbox", "mb-noop", get_period_key("day"))
        assert get_current_usage(key) == 0

    def test_rollback_on_generic_exception(self):
        """Counters are rolled back when a non-throttle exception occurs."""
        with pytest.raises(RuntimeError):
            with ThrottleManager() as throttle:
                throttle.check_limit("5/day", "mailbox", "mb-generic", amount=3)
                raise RuntimeError("boom")

        key = get_throttle_cache_key("mailbox", "mb-generic", get_period_key("day"))
        assert get_current_usage(key) == 0

    def test_rollback_on_throttle_exceeded(self):
        """Both calls are rolled back when the second exceeds the limit."""
        with pytest.raises(ThrottleLimitExceeded):
            with ThrottleManager() as throttle:
                throttle.check_limit("3/day", "mailbox", "mb-exceed", amount=2)
                throttle.check_limit("3/day", "mailbox", "mb-exceed", amount=2)

        key = get_throttle_cache_key("mailbox", "mb-exceed", get_period_key("day"))
        assert get_current_usage(key) == 0

    def test_multiple_checks_second_fails_rollback_first(self):
        """Two different limits: if the second fails, the first is also rolled back."""
        with pytest.raises(ThrottleLimitExceeded):
            with ThrottleManager() as throttle:
                throttle.check_limit("10/day", "mailbox", "mb-multi", amount=2)
                throttle.check_limit("1/day", "maildomain", "md-multi", amount=2)

        mb_key = get_throttle_cache_key("mailbox", "mb-multi", get_period_key("day"))
        md_key = get_throttle_cache_key("maildomain", "md-multi", get_period_key("day"))
        assert get_current_usage(mb_key) == 0
        assert get_current_usage(md_key) == 0

    def test_race_condition_rollback(self):
        """When increment_counter returns above the limit, rollback is triggered."""
        # Pre-seed the counter just under the limit so that increment pushes it over
        key = get_throttle_cache_key("mailbox", "mb-race", get_period_key("day"))
        increment_counter(key, 4, 86400)  # 4 out of 5

        with pytest.raises(ThrottleLimitExceeded):
            with ThrottleManager() as throttle:
                throttle.check_limit("5/day", "mailbox", "mb-race", amount=2)

        # The race-condition path decrements back
        assert get_current_usage(key) == 4
