"""Mixins for API viewsets."""

from rest_framework.decorators import action
from rest_framework.response import Response

from core.api import serializers
from core.services.quota import get_period_display, get_period_start, quota_service


class QuotaMixin:
    """
    Mixin providing a quota action for viewsets.

    The entity is retrieved via get_object() (uses lookup_url_kwarg).
    Subclasses must implement get_quota_entity_type() to return "mailbox" or "domain".
    """

    def get_quota_entity_type(self) -> str:
        """Return the entity type ('mailbox' or 'domain'). Must be implemented by subclass."""
        raise NotImplementedError("Subclasses must implement get_quota_entity_type()")

    @action(detail=True, methods=["get"], url_path="quota")
    def quota(self, request, **kwargs):
        """
        Get the recipient quota status.

        Returns the current quota usage including:
        - period: The quota period type (d/m/y)
        - period_display: Human-readable period name
        - period_start: Start of the current quota period
        - recipient_count: Number of recipients sent during this period
        - quota_limit: Maximum allowed recipients
        - remaining: Number of recipients still available
        - usage_percentage: Percentage of quota used
        """
        entity = self.get_object()
        entity_type = self.get_quota_entity_type()

        # Get the effective max_recipients
        quota_limit, period = entity.get_max_recipients()

        # Get quota status from Redis
        status_data = quota_service.get_status(
            entity_type=entity_type,
            entity_id=str(entity.id),
            period=period,
            limit=quota_limit,
        )

        data = {
            "period": period,
            "period_display": get_period_display(period),
            "period_start": get_period_start(period),
            **status_data,
        }

        serializer = serializers.RecipientQuotaSerializer(data)
        return Response(serializer.data)
