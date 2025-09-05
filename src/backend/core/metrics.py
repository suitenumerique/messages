"""
Custom Prometheus metrics collector for the messages core application.

This module defines a collector that exposes database-related metrics
(such as message counts by status, attachment counts, and total attachment size)
to Prometheus via the /metrics endpoint.
"""

from datetime import timedelta

from django.apps import apps
from django.db import models
from django.utils import timezone

from drf_spectacular.utils import (
    extend_schema,
)
from prometheus_client.core import GaugeMetricFamily
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .enums import MessageDeliveryStatusChoices
from .models import Attachment, MailboxAccess, MessageRecipient


class CustomDBPrometheusMetricsCollector:
    """
    Prometheus collector for custom database metrics.
    """

    def get_messages_with_status(self):
        """
        Yields a GaugeMetricFamily for each possible message delivery status,
        with the count of messages for that status. If no messages exist for a status,
        the count is 0.
        """
        messages_statuses_count = MessageRecipient.objects.values(
            "delivery_status"
        ).annotate(count=models.Count("id"))
        status_count_map = {
            row["delivery_status"]: row["count"] for row in messages_statuses_count
        }

        gauge = GaugeMetricFamily(
            "message_status_count",
            "Number of messages by delivery status",
            labels=["status"],
        )

        for status in MessageDeliveryStatusChoices:
            label = status.label
            count = status_count_map.get(status.value, 0)
            gauge.add_metric([label], count)

        yield gauge

    def get_draft_attachments_count(self):
        """
        Yields a GaugeMetricFamily with the total number of draft attachments.
        """
        attachments_count = Attachment.objects.count()
        yield GaugeMetricFamily(
            "draft_attachment_count",
            "Number of draft attachments",
            value=attachments_count,
        )

    def get_draft_attachments_total_size(self):
        """
        Yields a GaugeMetricFamily with the total size (in bytes) of all draft attachments.
        """
        total_size = (
            Attachment.objects.aggregate(models.Sum("blob__size"))["blob__size__sum"]
            or 0
        )
        yield GaugeMetricFamily(
            "draft_attachments_total_size_bytes",
            "Total size of all draft attachments in bytes",
            value=total_size,
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
        yield from self.get_draft_attachments_count()
        yield from self.get_draft_attachments_total_size()


class MailDomainUsersMetricsApiView(APIView):
    """
    API view to expose MailDomain Users custom metrics
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # Disable any authentication

    def _get_metrics_from_users_and_mailboxes(
        self, users_and_mailboxes: dict[str, list[str]]
    ) -> dict:
        unique_user_ids = set(users_and_mailboxes["users"])
        yau = []
        mau = []
        wau = []
        for user_id in unique_user_ids:
            last_access = (
                MailboxAccess.objects.filter(
                    mailbox__in=users_and_mailboxes["mailboxes"],
                    user_id=user_id,
                )
                .aggregate(models.Max("accessed_at"))
                .get("accessed_at__max")
            )

            if last_access:
                if last_access >= timezone.now() - timedelta(days=365):
                    yau.append(user_id)
                if last_access >= timezone.now() - timedelta(days=30):
                    mau.append(user_id)
                if last_access >= timezone.now() - timedelta(days=7):
                    wau.append(user_id)
        return {
            "tu": len(unique_user_ids),
            "yau": len(yau),
            "mau": len(mau),
            "wau": len(wau),
        }

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"])
    def get(self, request):
        """
        Handle GET requests for the metrics API endpoint.
        """
        group_by_key = request.query_params.get("group_by_maildomain_custom_attribute")

        if not group_by_key:
            mailboxes_accesses = (
                MailboxAccess.objects.select_related("mailbox")
                .select_related("user")
                .all()
            )
            metrics = self._get_metrics_from_users_and_mailboxes(
                {
                    "users": [ma.user.id.hex for ma in mailboxes_accesses],
                    "mailboxes": [ma.mailbox.id.hex for ma in mailboxes_accesses],
                }
            )
            return Response({"results": {"metrics": metrics}})

        group_by_to_users_and_mailboxes = {}
        mailbox_accesses = MailboxAccess.objects.select_related("mailbox__domain").all()
        for mailbox_access in mailbox_accesses:
            group_by = mailbox_access.mailbox.domain.custom_attributes.get(group_by_key)
            if group_by is None:
                continue
            if group_by not in group_by_to_users_and_mailboxes:
                group_by_to_users_and_mailboxes[group_by] = {
                    "users": [],
                    "mailboxes": [],
                }
            group_by_to_users_and_mailboxes[group_by]["users"].append(
                mailbox_access.user.id.hex
            )
            group_by_to_users_and_mailboxes[group_by]["mailboxes"].append(
                mailbox_access.mailbox.id.hex
            )
        metrics = []

        for group_by, users_and_mailboxes in group_by_to_users_and_mailboxes.items():
            metrics.append(
                {
                    group_by_key: group_by,
                    "metrics": self._get_metrics_from_users_and_mailboxes(
                        users_and_mailboxes
                    ),
                }
            )

        return Response({"count": len(metrics), "results": metrics})
