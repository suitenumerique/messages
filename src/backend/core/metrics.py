from django.apps import apps
from prometheus_client.core import GaugeMetricFamily
from django.db import ProgrammingError, connection
from .models import MessageRecipient
from .enums import MessageDeliveryStatusChoices
from django.db import models

class CustomDBMetricsCollector:

    def get_messages_with_status(self):
        messages_statuses_count = MessageRecipient.objects.values('delivery_status').annotate(count=models.Count('id'))
        gauges = [GaugeMetricFamily(
            f"message_{MessageDeliveryStatusChoices(message_status_count['delivery_status'])._label_}_count",
            f"Number of messages with status {MessageDeliveryStatusChoices(message_status_count['delivery_status'])._label_}",
            value=message_status_count['count']
        ) for message_status_count in messages_statuses_count]

        yield from gauges

    def collect(self):
        # Only run if apps are ready and model is migrated
        if not apps.ready or not apps.is_installed("core"):
            return

        yield from self.get_messages_with_status()
