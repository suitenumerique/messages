"""API views to expose custom metrics"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count, Exists, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.authentication import ChannelApiKeyAuthentication
from core.api.permissions import IsGlobalChannelMixin, channel_scope
from core.enums import ChannelApiKeyScope
from core.mda.addresses import ascii_lower, normalize_domain, split_address
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


class MetricsPagination(LimitOffsetPagination):
    """Limit/offset pagination over metric groups."""

    default_limit = 10
    max_limit = 100


class MailDomainUsersMetricsApiView(IsGlobalChannelMixin, APIView):
    """
    API view to expose MailDomain Users custom metrics. Global-only.

    ``METRICS_READ`` is in CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY (so the
    permission layer rejects non-global api_key channels), and
    ``IsGlobalChannelMixin`` re-asserts the same invariant in the view as
    a second defense-in-depth layer.
    """

    authentication_classes = [ChannelApiKeyAuthentication]
    permission_classes = [channel_scope(ChannelApiKeyScope.METRICS_READ)]

    @extend_schema(exclude=True)
    def get(self, request):
        """
        Handle GET requests for the metrics API endpoint.
        """
        group_by_custom_attribute_key = request.query_params.get(
            "group_by_maildomain_custom_attribute"
        )
        group_key = group_by_custom_attribute_key or "domain"

        # Groups are listed from the domain table only (cheap), so that the
        # expensive user and storage aggregations can be restricted to the
        # domains of the requested page. Domains with no users and no
        # storage are left out.
        group_domain_ids = defaultdict(list)
        group_created_at = {}
        for domain in MailDomain.objects.filter(
            Exists(MailboxAccess.objects.filter(mailbox__domain=OuterRef("pk")))
            | Exists(
                Message.objects.filter(thread__accesses__mailbox__domain=OuterRef("pk"))
            )
            | Exists(Attachment.objects.filter(mailbox__domain=OuterRef("pk")))
            | Exists(
                MessageTemplate.objects.filter(
                    maildomain=OuterRef("pk"), blob__isnull=False
                )
            )
        ).only("id", "name", "custom_attributes", "created_at"):
            if group_by_custom_attribute_key:
                group_value = domain.custom_attributes.get(
                    group_by_custom_attribute_key
                )
            else:
                group_value = domain.name
            group_domain_ids[group_value].append(domain.id)
            group_created_at[group_value] = min(
                group_created_at.get(group_value, domain.created_at), domain.created_at
            )

        # Oldest group first, so that new domains are appended at the end and
        # do not shift the pages already fetched. The value breaks ties.
        ordered_groups = sorted(
            group_domain_ids, key=lambda value: (group_created_at[value], str(value))
        )
        paginator = MetricsPagination()
        page_groups = paginator.paginate_queryset(ordered_groups, request, view=self)
        domain_ids = [
            domain_id for group in page_groups for domain_id in group_domain_ids[group]
        ]

        # group key => metrics dict
        metrics = {group: {group_key: group, "metrics": {}} for group in page_groups}

        for metric, threshold in ACTIVE_USER_METRICS.items():
            queryset = MailboxAccess.objects.filter(mailbox__domain_id__in=domain_ids)

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
                else:
                    group_value = result["mailbox__domain__name"]

                metrics[group_value]["metrics"][metric] = result["count"]

        # Compute storage_used for the domains of the page. Each component is
        # aggregated for all of them at once instead of through per-domain
        # correlated subqueries, which scale as domains x messages.
        # When multiple mailboxes in the same domain share a thread,
        # messages and blobs are counted once per domain.
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        # Messages and blobs are deduplicated by PK per domain before
        # summing: Sum(distinct=True) would deduplicate by *value* (wrong).
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH domain_messages AS (
                    SELECT DISTINCT mb.domain_id, m.id AS message_id,
                        m.blob_id, m.draft_blob_id
                    FROM messages_threadaccess ta
                    JOIN messages_mailbox mb ON mb.id = ta.mailbox_id
                    JOIN messages_message m ON m.thread_id = ta.thread_id
                    WHERE mb.domain_id = ANY(%s)
                )
                SELECT domain_id, 'msg', COUNT(*) FROM domain_messages
                GROUP BY domain_id
                UNION ALL
                SELECT dm.domain_id, 'mime', SUM(b.size_compressed)
                FROM (
                    SELECT DISTINCT domain_id, blob_id FROM domain_messages
                    WHERE blob_id IS NOT NULL
                ) dm
                JOIN messages_blob b ON b.id = dm.blob_id
                GROUP BY dm.domain_id
                UNION ALL
                SELECT dm.domain_id, 'draft', SUM(b.size_compressed)
                FROM (
                    SELECT DISTINCT domain_id, draft_blob_id FROM domain_messages
                    WHERE draft_blob_id IS NOT NULL
                ) dm
                JOIN messages_blob b ON b.id = dm.draft_blob_id
                GROUP BY dm.domain_id
                """,
                [domain_ids],
            )
            msg_counts, mime_sizes, draft_sizes = {}, {}, {}
            targets = {"msg": msg_counts, "mime": mime_sizes, "draft": draft_sizes}
            for domain_id, kind, value in cursor.fetchall():
                targets[kind][domain_id] = value or 0

        att_sizes = {
            row["mailbox__domain"]: row["total"] or 0
            for row in Attachment.objects.filter(mailbox__domain__in=domain_ids)
            .order_by()
            .values("mailbox__domain")
            .annotate(total=Sum("blob__size_compressed"))
        }

        template_sizes = {
            row["maildomain"]: row["total"] or 0
            for row in MessageTemplate.objects.filter(
                maildomain__in=domain_ids, blob__isnull=False
            )
            .order_by()
            .values("maildomain")
            .annotate(total=Sum("blob__size_compressed"))
        }

        for group in page_groups:
            metrics[group]["metrics"]["storage_used"] = sum(
                msg_counts.get(domain_id, 0) * overhead
                + mime_sizes.get(domain_id, 0)
                + draft_sizes.get(domain_id, 0)
                + att_sizes.get(domain_id, 0)
                + template_sizes.get(domain_id, 0)
                for domain_id in group_domain_ids[group]
            )

        return paginator.get_paginated_response(list(metrics.values()))


class MailboxUsageMetricsApiView(IsGlobalChannelMixin, APIView):
    """
    API view to expose per-mailbox storage usage metrics. Global-only —
    see ``MailDomainUsersMetricsApiView`` for the rationale.
    """

    authentication_classes = [ChannelApiKeyAuthentication]
    permission_classes = [channel_scope(ChannelApiKeyScope.METRICS_READ)]

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
            Blob.objects.filter(drafts__thread__accesses__mailbox=OuterRef("pk"))
            .order_by()
            .values("drafts__thread__accesses__mailbox")
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

        # Both filters match stored values, which are canonicalized, so the
        # caller's spelling has to be canonicalized too.
        if domain:
            queryset = queryset.filter(domain__name=normalize_domain(domain))
        if account_email:
            parts = split_address(account_email)
            if parts:
                queryset = queryset.filter(
                    local_part=ascii_lower(parts[0]),
                    domain__name=normalize_domain(parts[1]),
                )
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
