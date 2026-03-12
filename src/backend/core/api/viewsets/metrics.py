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

        queryset = Mailbox.objects.select_related("domain")

        # Apply filters
        domain = request.query_params.get("domain")
        account_email = request.query_params.get("account_email")
        account_type = request.query_params.get("account_type")
        account_id_key = request.query_params.get("account_id_key")
        account_id_value = request.query_params.get("account_id_value")

        allowed_keys = settings.SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN.get(
            "properties", {}
        ).keys()
        if account_id_key and account_id_key not in allowed_keys:
            return Response(
                {"error": "Invalid account_id_key."},
                status=400,
            )

        if domain:
            queryset = queryset.filter(domain__name=domain)
        if account_email:
            parts = account_email.rsplit("@", 1)
            if len(parts) == 2:
                queryset = queryset.filter(local_part=parts[0], domain__name=parts[1])
        if account_id_key and account_id_value:
            queryset = queryset.filter(
                **{f"domain__custom_attributes__{account_id_key}": account_id_value}
            )

        if account_type == "organization" and (
            not account_id_key or not account_id_value
        ):
            return Response(
                {
                    "error": "account_id_key and "
                    "account_id_value are required "
                    "for account_type=organization."
                },
                status=400,
            )

        storage_expr = (
            Coalesce(messages_count_subquery, Value(0)) * overhead
            + Coalesce(mime_blobs_subquery, Value(0))
            + Coalesce(draft_blobs_subquery, Value(0))
            + Coalesce(attachment_blobs_subquery, Value(0))
            + Coalesce(template_blobs_subquery, Value(0))
        )

        # Build results based on account_type
        if account_type == "organization":
            total = queryset.annotate(storage_used=storage_expr).aggregate(
                total=Coalesce(Sum("storage_used"), Value(0))
            )["total"]
            if not total and not queryset.exists():
                return Response({"count": 0, "results": []})
            results = [
                {
                    account_id_key: account_id_value,
                    "account": {"type": "organization"},
                    "metrics": {"storage_used": total},
                }
            ]

        elif account_type == "maildomain":
            domain_rows = (
                queryset.annotate(storage_used=storage_expr)
                .values("domain__name", "domain__custom_attributes")
                .annotate(domain_storage=Sum("storage_used"))
                .order_by("domain__name")
            )
            results = []
            for row in domain_rows:
                result = {
                    "account": {
                        "type": "maildomain",
                        "id": row["domain__name"],
                    },
                    "metrics": {"storage_used": row["domain_storage"]},
                }
                if account_id_key:
                    result[account_id_key] = (
                        row["domain__custom_attributes"] or {}
                    ).get(account_id_key, "")
                results.append(result)

        else:
            queryset = queryset.annotate(storage_used=storage_expr).order_by(
                "domain__name", "local_part"
            )
            results = []
            for mailbox in queryset:
                email = f"{mailbox.local_part}@{mailbox.domain.name}"
                result = {
                    "account": {"type": "mailbox", "email": email},
                    "metrics": {"storage_used": mailbox.storage_used},
                }
                if account_id_key:
                    result[account_id_key] = mailbox.domain.custom_attributes.get(
                        account_id_key, ""
                    )
                results.append(result)

        return Response({"count": len(results), "results": results})
