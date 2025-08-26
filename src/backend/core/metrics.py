"""
Custom Prometheus metrics collector for the messages core application.

This module defines a collector that exposes database-related metrics
(such as message counts by status, attachment counts, and total attachment size)
to Prometheus via the /metrics endpoint.
"""

from django.apps import apps
from prometheus_client.core import GaugeMetricFamily
from .models import MessageRecipient, Attachment
from .enums import MessageDeliveryStatusChoices
from django.db import models

class CustomDBMetricsCollector:
    """
    Prometheus collector for custom database metrics.
    """

    def get_messages_with_status(self):
        """
        Yields a GaugeMetricFamily for each possible message delivery status,
        with the count of messages for that status. If no messages exist for a status,
        the count is 0.
        """
        messages_statuses_count = (
            MessageRecipient.objects.values('delivery_status')
            .annotate(count=models.Count('id'))
        )
        status_count_map = {row['delivery_status']: row['count'] for row in messages_statuses_count}

        for status in MessageDeliveryStatusChoices:
            label = status.label
            count = status_count_map.get(status.value, 0)
            yield GaugeMetricFamily(
                f"message_{label}_count",
                f"Number of messages with status {label}",
                value=count
            )

    def get_attachments_count(self):
        """
        Yields a GaugeMetricFamily with the total number of attachments.
        """
        attachments_count = Attachment.objects.count()
        yield GaugeMetricFamily(
            "attachment_count",
            "Number of attachments",
            value=attachments_count
        )

    def get_attachments_total_size(self):
        """
        Yields a GaugeMetricFamily with the total size (in bytes) of all attachments.
        """
        total_size = Attachment.objects.aggregate(models.Sum('blob__size'))['blob__size__sum'] or 0
        yield GaugeMetricFamily(
            "attachments_total_size_bytes",
            "Total size of all attachments in bytes",
            value=total_size
        )

    def collect(self):
        """
        Entrypoint for Prometheus metric collection.
        Yields all custom metrics if Django apps are ready and the 'core' app is installed.
        This ensures that we only collect metrics when the application is in a valid state,
        e.g. not during migrations.
        """
        # Only run if apps are ready and model is migrated
        if not apps.ready or not apps.is_installed("core"):
            return

        yield from self.get_messages_with_status()
        yield from self.get_attachments_count()
        yield from self.get_attachments_total_size()
