"""Throttling modules for the API."""
# pylint: disable=attribute-defined-outside-init

from django.conf import settings

from lasuite.drf.throttling import MonitoredThrottleMixin
from rest_framework.throttling import SimpleRateThrottle
from sentry_sdk import capture_message

from core.models import Mailbox


def sentry_throttle_failure(message: str) -> None:
    """Log throttle failure to Sentry."""
    capture_message(message, "warning")


class BaseOutboundThrottle(MonitoredThrottleMixin, SimpleRateThrottle):
    """
    Base throttle for outbound message endpoint, per mailbox.

    This throttle limits the rate at which messages can be sent from a specific mailbox.
    The rate can be configured at:
    1. Maildomain level (maildomain.custom_settings.outbound_message_throttle_rate_burst/sustained)
    2. Instance level (settings)
    3. Default from REST_FRAMEWORK.DEFAULT_THROTTLE_RATES

    Subclasses must define:
    - scope: The throttle scope (e.g., "outbound_burst", "outbound_sustained")
    - throttle_type: Either "burst" or "sustained"
    - custom_settings_key: Key in domain.custom_settings
    - instance_setting_name: Setting name
    """

    throttle_type: str = ""
    custom_settings_key: str = ""
    instance_setting_name: str = ""

    def get_cache_key(self, request, view):
        """
        Generate a cache key based on the sender mailbox ID.

        The mailbox ID is extracted from the request data (senderId).
        """
        sender_id = request.data.get("senderId")
        if not sender_id:
            # Fallback to user-based throttling if no sender
            if request.user and request.user.is_authenticated:
                return f"throttle_outbound_{self.throttle_type}_user_{request.user.pk}"
            return f"throttle_outbound_{self.throttle_type}_{self.get_ident(request)}"

        return f"throttle_outbound_{self.throttle_type}_mailbox_{sender_id}"

    def get_rate(self, request=None):
        """
        Get the throttle rate, checking maildomain custom_settings first.

        Priority:
        1. Maildomain custom_settings (e.g., outbound_message_throttle_rate_burst)
        2. Instance setting (e.g., OUTBOUND_MESSAGE_THROTTLE_RATE_BURST)
        3. Default from REST_FRAMEWORK.DEFAULT_THROTTLE_RATES
        """
        # Try to get rate from maildomain custom_settings
        sender_id = request.data.get("senderId") if request else None

        if sender_id:
            try:
                mailbox = Mailbox.objects.select_related("domain").get(id=sender_id)
                domain_rate = mailbox.domain.custom_settings.get(
                    self.custom_settings_key
                )
                if domain_rate:
                    return domain_rate
            except (Mailbox.DoesNotExist, AttributeError, KeyError):
                pass

        # Fallback to instance setting
        instance_rate = getattr(settings, self.instance_setting_name, None)
        if instance_rate:
            return instance_rate

        # Fallback to DRF default
        return self.THROTTLE_RATES.get(self.scope)

    def allow_request(self, request, view):
        """Check if the request should be allowed."""
        # Get the rate for this request
        self.rate = self.get_rate(request)
        if self.rate is None:
            return True

        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True

        self.history = self.cache.get(self.key, [])
        self.now = self.timer()

        # Drop requests older than duration
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()

        if len(self.history) >= self.num_requests:
            return self.throttle_failure()

        return self.throttle_success()


class OutboundThrottleBurst(BaseOutboundThrottle):
    """
    Burst throttle for outbound messages.

    Protects against short-term spikes (e.g., 10 messages per minute).
    """

    scope = "outbound_burst"
    throttle_type = "burst"
    custom_settings_key = "outbound_message_throttle_rate_burst"
    instance_setting_name = "OUTBOUND_MESSAGE_THROTTLE_RATE_BURST"


class OutboundThrottleSustained(BaseOutboundThrottle):
    """
    Sustained throttle for outbound messages.

    Controls long-term sending rate (e.g., 100 messages per hour).
    """

    scope = "outbound_sustained"
    throttle_type = "sustained"
    custom_settings_key = "outbound_message_throttle_rate_sustained"
    instance_setting_name = "OUTBOUND_MESSAGE_THROTTLE_RATE_SUSTAINED"


class BaseInboundThrottle(MonitoredThrottleMixin, SimpleRateThrottle):
    """
    Base throttle for inbound message endpoints.

    This throttle limits the rate at which messages can be received.
    Throttling is based on the source IP address.

    Subclasses must define:
    - scope: The throttle scope (e.g., "inbound_burst", "inbound_sustained")
    - throttle_type: Either "burst" or "sustained"
    - instance_setting_name: Setting name
    """

    throttle_type: str = ""
    instance_setting_name: str = ""

    def get_cache_key(self, request, view):
        """Generate a cache key based on the source IP address."""
        return f"throttle_inbound_{self.throttle_type}_{self.get_ident(request)}"

    def get_rate(self):
        """
        Get the throttle rate.

        Priority:
        1. Instance setting (e.g., INBOUND_MESSAGE_THROTTLE_RATE_BURST)
        2. Default from REST_FRAMEWORK.DEFAULT_THROTTLE_RATES
        """
        # Fallback to instance setting
        instance_rate = getattr(settings, self.instance_setting_name, None)
        if instance_rate:
            return instance_rate

        # Fallback to DRF default
        return self.THROTTLE_RATES.get(self.scope)

    def allow_request(self, request, view):
        """Check if the request should be allowed."""
        self.rate = self.get_rate()
        if self.rate is None:
            return True

        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True

        self.history = self.cache.get(self.key, [])
        self.now = self.timer()

        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()

        if len(self.history) >= self.num_requests:
            return self.throttle_failure()

        return self.throttle_success()


class InboundThrottleBurst(BaseInboundThrottle):
    """
    Burst throttle for inbound messages.

    Protects against short-term spikes (e.g., 20 messages per minute).
    """

    scope = "inbound_burst"
    throttle_type = "burst"
    instance_setting_name = "INBOUND_MESSAGE_THROTTLE_RATE_BURST"


class InboundThrottleSustained(BaseInboundThrottle):
    """
    Sustained throttle for inbound messages.

    Controls long-term receiving rate (e.g., 200 messages per hour).
    """

    scope = "inbound_sustained"
    throttle_type = "sustained"
    instance_setting_name = "INBOUND_MESSAGE_THROTTLE_RATE_SUSTAINED"
