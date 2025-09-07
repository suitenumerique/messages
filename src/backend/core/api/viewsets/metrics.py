"""API view to expose MailDomain Users custom metrics"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import HasMetricsApiKey
from core.models import MailboxAccess

# name: threshold (in days)
ACTIVE_USER_METRICS = {
    "tu": None,
    "yau": 365,
    "mau": 30,
    "wau": 7,
}


class MailDomainUsersMetricsApiView(APIView):
    """
    API view to expose MailDomain Users custom metrics
    """

    permission_classes = [HasMetricsApiKey]
    authentication_classes = []  # Disable any authentication

    @extend_schema(exclude=True)
    def get(self, request):
        """
        Handle GET requests for the metrics API endpoint.
        """
        group_by_custom_attribute_key = request.query_params.get(
            "group_by_maildomain_custom_attribute"
        )

        # group key => metrics dict
        metrics = defaultdict(lambda: {"metrics": {}})

        for metric, threshold in ACTIVE_USER_METRICS.items():
            # Build the base queryset
            queryset = MailboxAccess.objects.select_related(
                "mailbox", "mailbox__domain"
            )

            # Apply time filter if threshold is specified
            if threshold is not None:
                queryset = queryset.filter(
                    accessed_at__gte=timezone.now() - timedelta(days=threshold)
                )

            # Group by the custom attribute value and count unique users
            if group_by_custom_attribute_key:
                data = queryset.values(
                    f"mailbox__domain__custom_attributes__{group_by_custom_attribute_key}"
                ).annotate(count=Count("user", distinct=True))
            else:
                # As a fallback, group by the domain name
                data = queryset.values("mailbox__domain__name").annotate(
                    count=Count("user", distinct=True)
                )

            for result in data:
                if group_by_custom_attribute_key:
                    group_value = result[
                        f"mailbox__domain__custom_attributes__{group_by_custom_attribute_key}"
                    ]
                    group_key = group_by_custom_attribute_key
                else:
                    group_value = result["mailbox__domain__name"]
                    group_key = "domain"

                # Set the group key and value only once per group
                if group_key not in metrics[group_value]:
                    metrics[group_value][group_key] = group_value
                metrics[group_value]["metrics"][metric] = result["count"]

        return Response({"count": len(metrics), "results": list(metrics.values())})
