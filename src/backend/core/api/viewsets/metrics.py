"""API views to expose custom metrics"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import HasMetricsApiKey
from core.models import Attachment, Blob, MailDomain, Mailbox, MailboxAccess, Message

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

        # Compute storage_used per domain.
        # When multiple mailboxes in the same domain share a thread,
        # messages and blobs are counted once (via .distinct()).
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        for domain in MailDomain.objects.all():
            domain_messages = Message.objects.filter(
                thread__accesses__mailbox__domain=domain
            ).distinct()

            msg_count = domain_messages.count()

            mime_size = (
                Blob.objects.filter(
                    messages__thread__accesses__mailbox__domain=domain
                )
                .distinct()
                .aggregate(total=Sum("size_compressed"))["total"]
                or 0
            )

            draft_size = (
                Blob.objects.filter(
                    draft__thread__accesses__mailbox__domain=domain
                )
                .distinct()
                .aggregate(total=Sum("size_compressed"))["total"]
                or 0
            )

            att_size = (
                Attachment.objects.filter(mailbox__domain=domain).aggregate(
                    total=Sum("blob__size_compressed")
                )["total"]
                or 0
            )

            storage = (
                msg_count * overhead + mime_size + draft_size + att_size
            )

            if storage == 0 and domain.name not in metrics:
                continue

            if group_by_custom_attribute_key:
                group_value = domain.custom_attributes.get(
                    group_by_custom_attribute_key
                )
                group_key = group_by_custom_attribute_key
            else:
                group_value = domain.name
                group_key = "domain"

            if group_key not in metrics[group_value]:
                metrics[group_value][group_key] = group_value
            metrics[group_value]["metrics"]["storage_used"] = (
                metrics[group_value]["metrics"].get("storage_used", 0)
                + storage
            )

        return Response({"count": len(metrics), "results": list(metrics.values())})


class MailboxUsageMetricsApiView(APIView):
    """
    API view to expose per-mailbox storage usage metrics.
    """

    permission_classes = [HasMetricsApiKey]
    authentication_classes = []  # Disable any authentication

    @extend_schema(exclude=True)
    def get(self, request):
        """
        Handle GET requests for the mailbox usage metrics endpoint.

        Returns per-mailbox storage usage computed as:
        storage_used = messages_count * OVERHEAD + sum(blobs.size_compressed)
        """
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        # Use subqueries to avoid cross-product issues.
        # All blob sizes are counted through their message/attachment
        # relationships (via ThreadAccess), NOT through blob.mailbox.

        messages_count_subquery = Subquery(
            Message.objects.filter(
                thread__accesses__mailbox=OuterRef("pk")
            )
            .order_by()
            .values("thread__accesses__mailbox")
            .annotate(cnt=Count("id", distinct=True))
            .values("cnt")[:1]
        )

        # Raw MIME blobs linked via Message.blob
        mime_blobs_subquery = Subquery(
            Blob.objects.filter(
                messages__thread__accesses__mailbox=OuterRef("pk")
            )
            .order_by()
            .values("messages__thread__accesses__mailbox")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )

        # Draft body blobs linked via Message.draft_blob
        draft_blobs_subquery = Subquery(
            Blob.objects.filter(
                draft__thread__accesses__mailbox=OuterRef("pk")
            )
            .order_by()
            .values("draft__thread__accesses__mailbox")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )

        # Attachment blobs linked via Attachment.mailbox
        attachment_blobs_subquery = Subquery(
            Attachment.objects.filter(mailbox=OuterRef("pk"))
            .order_by()
            .values("mailbox")
            .annotate(total=Sum("blob__size_compressed"))
            .values("total")[:1]
        )

        queryset = (
            Mailbox.objects.select_related("domain")
            .annotate(
                messages_count=Coalesce(messages_count_subquery, Value(0)),
                mime_blobs_size=Coalesce(mime_blobs_subquery, Value(0)),
                draft_blobs_size=Coalesce(draft_blobs_subquery, Value(0)),
                attachment_blobs_size=Coalesce(
                    attachment_blobs_subquery, Value(0)
                ),
            )
            .order_by("domain__name", "local_part")
        )

        results = []
        for mailbox in queryset:
            storage_used = (
                mailbox.messages_count * overhead
                + mailbox.mime_blobs_size
                + mailbox.draft_blobs_size
                + mailbox.attachment_blobs_size
            )
            results.append(
                {
                    "email": f"{mailbox.local_part}@{mailbox.domain.name}",
                    "storage_used": storage_used,
                }
            )

        return Response({"count": len(results), "results": results})
