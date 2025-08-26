"""Messages Core application"""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
from prometheus_client.core import REGISTRY

class CoreConfig(AppConfig):
    """Configuration class for the messages core app."""

    name = "core"
    app_label = "core"
    verbose_name = _("messages core application")

    def ready(self):
        """Register signal handlers and prometheus collector when the app is ready."""
        # pylint: disable=unused-import, import-outside-toplevel
        from .metrics import CustomDBMetricsCollector  # noqa
        REGISTRY.register(CustomDBMetricsCollector())

        # Import signal handlers to register them
        # pylint: disable=unused-import, import-outside-toplevel
        import core.signals  # noqa
