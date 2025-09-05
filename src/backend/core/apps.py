"""Messages Core application"""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    """Configuration class for the messages core app."""

    name = "core"
    app_label = "core"
    verbose_name = _("messages core application")

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
