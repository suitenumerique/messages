"""API views to expose custom metrics"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, OuterRef, Subquery, Sum, Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import HasMetricsApiKey
from core.models import (
    Attachment,
    Blob,
    Mailbox,
    MailboxAccess,
    MailDomain,
    Message,
    MessageTemplate,
)

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

        # Compute storage_used per domain in a single query.
        # When multiple mailboxes in the same domain share a thread,
        # messages and blobs are counted once per domain.
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        # Count(distinct=True) deduplicates by PK — correct for message counts.
        msg_count_subquery = Subquery(
            Message.objects.filter(thread__accesses__mailbox__domain=OuterRef("pk"))
            .order_by()
            .values("thread__accesses__mailbox__domain")
            .annotate(cnt=Count("id", distinct=True))
            .values("cnt")[:1]
        )

        # For blob sizes, Sum(distinct=True) deduplicates by *value* (wrong),
        # and .distinct() before .values().annotate() puts DISTINCT on the
        # aggregated output (also wrong).  Use a raw subselect that first
        # deduplicates blob rows by PK, then sums.
        mime_size_subquery = RawSQL(
            """
            SELECT COALESCE(SUM(sub.size_compressed), 0)
            FROM (
                SELECT DISTINCT b.id, b.size_compressed
                FROM messages_blob b
                JOIN messages_message m ON m.blob_id = b.id
                JOIN messages_thread t ON m.thread_id = t.id
                JOIN messages_threadaccess ta ON ta.thread_id = t.id
                JOIN messages_mailbox mb ON ta.mailbox_id = mb.id
                WHERE mb.domain_id = messages_maildomain.id
            ) sub
            """,
            (),
        )

        draft_size_subquery = RawSQL(
            """
            SELECT COALESCE(SUM(sub.size_compressed), 0)
            FROM (
                SELECT DISTINCT b.id, b.size_compressed
                FROM messages_blob b
                JOIN messages_message m ON m.draft_blob_id = b.id
                JOIN messages_thread t ON m.thread_id = t.id
                JOIN messages_threadaccess ta ON ta.thread_id = t.id
                JOIN messages_mailbox mb ON ta.mailbox_id = mb.id
                WHERE mb.domain_id = messages_maildomain.id
            ) sub
            """,
            (),
        )

        att_size_subquery = Subquery(
            Attachment.objects.filter(mailbox__domain=OuterRef("pk"))
            .order_by()
            .values("mailbox__domain")
            .annotate(total=Sum("blob__size_compressed"))
            .values("total")[:1]
        )

        template_size_subquery = Subquery(
            MessageTemplate.objects.filter(
                maildomain=OuterRef("pk"), blob__isnull=False
            )
            .order_by()
            .values("maildomain")
            .annotate(total=Sum("blob__size_compressed"))
            .values("total")[:1]
        )

        for domain in MailDomain.objects.annotate(
            msg_count=Coalesce(msg_count_subquery, Value(0)),
            mime_size=mime_size_subquery,
            draft_size=draft_size_subquery,
            att_size=Coalesce(att_size_subquery, Value(0)),
            template_size=Coalesce(template_size_subquery, Value(0)),
        ):
            storage = (
                domain.msg_count * overhead
                + domain.mime_size
                + domain.draft_size
                + domain.att_size
                + domain.template_size
            )

            if group_by_custom_attribute_key:
                group_value = domain.custom_attributes.get(
                    group_by_custom_attribute_key
                )
                group_key = group_by_custom_attribute_key
            else:
                group_value = domain.name
                group_key = "domain"

            if storage == 0 and group_value not in metrics:
                continue

            if group_key not in metrics[group_value]:
                metrics[group_value][group_key] = group_value
            metrics[group_value]["metrics"]["storage_used"] = (
                metrics[group_value]["metrics"].get("storage_used", 0) + storage
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
            Message.objects.filter(thread__accesses__mailbox=OuterRef("pk"))
            .order_by()
            .values("thread__accesses__mailbox")
            .annotate(cnt=Count("id", distinct=True))
            .values("cnt")[:1]
        )

        # Raw MIME blobs linked via Message.blob
        mime_blobs_subquery = Subquery(
            Blob.objects.filter(messages__thread__accesses__mailbox=OuterRef("pk"))
            .order_by()
            .values("messages__thread__accesses__mailbox")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )

        # Draft body blobs linked via Message.draft_blob
        draft_blobs_subquery = Subquery(
            Blob.objects.filter(draft__thread__accesses__mailbox=OuterRef("pk"))
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

        # Template/signature blobs linked via MessageTemplate.mailbox
        template_blobs_subquery = Subquery(
            MessageTemplate.objects.filter(mailbox=OuterRef("pk"), blob__isnull=False)
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
                attachment_blobs_size=Coalesce(attachment_blobs_subquery, Value(0)),
                template_blobs_size=Coalesce(template_blobs_subquery, Value(0)),
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
                + mailbox.template_blobs_size
            )
            results.append(
                {
                    "email": f"{mailbox.local_part}@{mailbox.domain.name}",
                    "storage_used": storage_used,
                }
            )

        return Response({"count": len(results), "results": results})
