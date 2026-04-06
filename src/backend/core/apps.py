"""Messages Core application"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    """Configuration class for the messages core app."""

    name = "core"
    app_label = "core"
    verbose_name = "messages core application"

    def ready(self):
        """Register signal handlers and prometheus collector when the app is ready."""
        # pylint: disable=unused-import, import-outside-toplevel

        from django.conf import settings

        if settings.ENABLE_PROMETHEUS:
            from prometheus_client.core import REGISTRY

            from .metrics import CustomDBPrometheusMetricsCollector

            REGISTRY.register(CustomDBPrometheusMetricsCollector())

        # Import signal handlers to register them
        # pylint: disable=unused-import, import-outside-toplevel
        import core.signals  # noqa

        # Deprecation warning for legacy static API keys.
        for deprecated in ("METRICS_API_KEY", "PROVISIONING_API_KEY"):
            if getattr(settings, deprecated, None):
                logger.warning(
                    "%s is set but deprecated and ignored. Migrate to a channel.",
                    deprecated,
                )
