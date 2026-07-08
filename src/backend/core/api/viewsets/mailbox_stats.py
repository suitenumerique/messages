"""Mailbox statistics actions, split out of the main MailboxViewSet.

Read-only endpoints:

- ``storage_stats`` — total storage + top-100 largest threads (Storage tab).
- ``stats/overview`` — headline counts (conversations, messages, sent…).
- ``stats/response_times`` — average reply time, globally and per author.
- ``stats/response_times_by_label`` — the same, grouped by label.

The ``stats/*`` endpoints accept a ``timeframe`` query parameter (``this_month``
default, ``last_month``, ``last_30_days``, ``last_90_days``). The
``FEATURE_MAILBOX_STATS`` flag only hides the Statistics tab on the frontend;
none of these endpoints are gated on it server-side.
"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core import enums, models

from .. import serializers

# Timeframes accepted by the statistics endpoints.
STATS_TIMEFRAMES = ("this_month", "last_month", "last_30_days", "last_90_days")

TIMEFRAME_PARAMETER = OpenApiParameter(
    name="timeframe",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    enum=list(STATS_TIMEFRAMES),
    description=(
        "Period to aggregate over. One of `this_month` (default), "
        "`last_month`, `last_30_days`, `last_90_days`."
    ),
)


def resolve_timeframe(value):
    """Return the ``(start, end)`` datetimes for a timeframe query value.

    ``end`` is exclusive. Raises ``ValidationError`` on an unknown value.
    """
    value = value or "this_month"
    now = timezone.now()
    if value == "last_30_days":
        return now - timedelta(days=30), now
    if value == "last_90_days":
        return now - timedelta(days=90), now
    if value == "this_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    if value == "last_month":
        first_this_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        start = (first_this_month - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return start, first_this_month
    raise ValidationError(
        {"timeframe": f"Invalid timeframe. Expected one of {STATS_TIMEFRAMES}."}
    )


def _average(total_seconds, count):
    """Rounded integer average, or None when there is nothing to average."""
    return round(total_seconds / count) if count else None


def _unanswered_incoming_threads(thread_ids, start, end):
    """thread_id for each incoming email in ``[start, end)`` that never got a
    later outgoing reply — one entry per unanswered email (so a thread may
    appear more than once)."""
    incoming = list(
        models.Message.objects.filter(
            thread_id__in=thread_ids,
            is_sender=False,
            is_draft=False,
            is_trashed=False,
            is_spam=False,
            created_at__gte=start,
            created_at__lt=end,
        )
        .values("thread_id", "created_at")
        .order_by("created_at")
    )
    incoming_thread_ids = {msg["thread_id"] for msg in incoming}

    sent_by_thread = defaultdict(list)
    for reply in (
        models.Message.objects.filter(
            thread_id__in=incoming_thread_ids,
            is_sender=True,
            is_draft=False,
            is_trashed=False,
        )
        .values("thread_id", "created_at")
        .order_by("created_at")
    ):
        sent_by_thread[reply["thread_id"]].append(reply["created_at"])

    return [
        msg["thread_id"]
        for msg in incoming
        if not any(
            sent > msg["created_at"]
            for sent in sent_by_thread.get(msg["thread_id"], ())
        )
    ]


def _assignment_map(thread_ids):
    """Assignment info for the given threads.

    Returns ``(assigned_thread_ids, per_user)`` where ``assigned_thread_ids`` is
    the set of threads with any active assignment and ``per_user`` maps a user
    id to ``{"name": str, "threads": set(thread_id)}``.
    """
    assigned_thread_ids = set()
    per_user = {}
    for event in models.UserEvent.objects.filter(
        type=enums.UserEventTypeChoices.ASSIGN, thread_id__in=set(thread_ids)
    ).values("user_id", "thread_id", "user__full_name", "user__email"):
        assigned_thread_ids.add(event["thread_id"])
        entry = per_user.setdefault(
            event["user_id"],
            {
                "name": event["user__full_name"]
                or event["user__email"]
                or "—",
                "threads": set(),
            },
        )
        entry["threads"].add(event["thread_id"])
    return assigned_thread_ids, per_user


class MailboxStatsMixin:
    """Mixin adding the ``stats/<type>`` read-only actions to MailboxViewSet.

    Relies on the host viewset's ``get_object`` (which enforces mailbox
    access), so it carries no queryset or permissions of its own.

    The ``FEATURE_MAILBOX_STATS`` flag is enforced frontend-side only (it hides
    the Statistics tab); these endpoints are intentionally not gated on it.
    """

    @extend_schema(
        tags=["mailboxes"],
        parameters=[TIMEFRAME_PARAMETER],
        responses={200: serializers.MailboxStatsOverviewSerializer},
    )
    @action(detail=True, methods=["get"], url_path="stats/overview")
    def stats_overview(self, request, pk=None):
        """Headline counts for the mailbox over the given timeframe: active
        conversations, messages, and sent emails."""
        mailbox = self.get_object()
        start, end = resolve_timeframe(request.query_params.get("timeframe"))

        thread_ids = models.ThreadAccess.objects.filter(
            mailbox=mailbox
        ).values_list("thread_id", flat=True)

        messages = models.Message.objects.filter(
            thread_id__in=thread_ids,
            is_draft=False,
            is_trashed=False,
            created_at__gte=start,
            created_at__lt=end,
        )

        # Unanswered incoming emails in the period, split by whether their
        # conversation is assigned to anyone.
        unanswered_threads = _unanswered_incoming_threads(thread_ids, start, end)
        assigned_for_unanswered, _ = _assignment_map(unanswered_threads)
        unreplied_assigned = sum(
            1 for tid in unanswered_threads if tid in assigned_for_unanswered
        )

        # Conversations active in the period that are still unread for this
        # mailbox (no read cursor, or a message newer than it), split the same
        # way.
        period_thread_ids = list(
            messages.values_list("thread_id", flat=True).distinct()
        )
        read_at_subquery = Subquery(
            models.ThreadAccess.objects.filter(
                thread=OuterRef("pk"), mailbox=mailbox
            ).values("read_at")[:1]
        )
        unread_thread_ids = [
            thread["id"]
            for thread in models.Thread.objects.filter(
                id__in=period_thread_ids
            )
            .annotate(access_read_at=read_at_subquery)
            .values("id", "messaged_at", "access_read_at")
            if thread["messaged_at"]
            and (
                thread["access_read_at"] is None
                or thread["messaged_at"] > thread["access_read_at"]
            )
        ]
        assigned_for_unread, _ = _assignment_map(unread_thread_ids)
        unread_assigned = sum(
            1 for tid in unread_thread_ids if tid in assigned_for_unread
        )

        data = {
            "conversations": messages.values("thread_id").distinct().count(),
            "messages": messages.count(),
            "sent": messages.filter(is_sender=True).count(),
            "unreplied": len(unanswered_threads),
            "unreplied_assigned": unreplied_assigned,
            "unreplied_unassigned": len(unanswered_threads) - unreplied_assigned,
            "unread": len(unread_thread_ids),
            "unread_assigned": unread_assigned,
            "unread_unassigned": len(unread_thread_ids) - unread_assigned,
        }
        return Response(serializers.MailboxStatsOverviewSerializer(data).data)

    @extend_schema(
        tags=["mailboxes"],
        parameters=[TIMEFRAME_PARAMETER],
        responses={200: serializers.MailboxResponseTimesSerializer},
    )
    @action(detail=True, methods=["get"], url_path="stats/response_times")
    def stats_response_times(self, request, pk=None):
        """Average time between an incoming email and its first external reply,
        globally and per author.

        For each incoming email received in the period, the reply is the first
        outgoing (non-draft, non-trashed) message in the same thread sent after
        it — chronological matching by ``created_at``, the authoritative order
        everywhere in the codebase. Emails with no later reply count as
        unreplied. Authorship comes from ``Message.sender_user`` (the user who
        sent the reply), falling back to the sender contact.
        """
        mailbox = self.get_object()
        start, end = resolve_timeframe(request.query_params.get("timeframe"))

        thread_ids = models.ThreadAccess.objects.filter(
            mailbox=mailbox
        ).values_list("thread_id", flat=True)

        incoming = list(
            models.Message.objects.filter(
                thread_id__in=thread_ids,
                is_sender=False,
                is_draft=False,
                is_trashed=False,
                is_spam=False,
                created_at__gte=start,
                created_at__lt=end,
            )
            .values("thread_id", "created_at")
            .order_by("created_at")
        )

        incoming_thread_ids = {msg["thread_id"] for msg in incoming}

        sent_by_thread = defaultdict(list)
        for reply in (
            models.Message.objects.filter(
                thread_id__in=incoming_thread_ids,
                is_sender=True,
                is_draft=False,
                is_trashed=False,
            )
            .values(
                "thread_id",
                "created_at",
                "sender_user_id",
                "sender_user__full_name",
                "sender_user__email",
                "sender__name",
                "sender__email",
            )
            .order_by("created_at")
        ):
            sent_by_thread[reply["thread_id"]].append(reply)

        global_replied = 0
        global_seconds = 0.0
        unanswered_threads = []
        # keyed by sender_user_id (or display name when no user is attached)
        authors = {}

        def _author_entry(key, name):
            return authors.setdefault(
                key,
                {"author": name, "replied": 0, "seconds": 0.0, "unanswered": 0},
            )

        for msg in incoming:
            reply = next(
                (
                    sent
                    for sent in sent_by_thread.get(msg["thread_id"], ())
                    if sent["created_at"] > msg["created_at"]
                ),
                None,
            )
            if reply is None:
                unanswered_threads.append(msg["thread_id"])
                continue

            seconds = (reply["created_at"] - msg["created_at"]).total_seconds()
            global_replied += 1
            global_seconds += seconds

            name = (
                reply["sender_user__full_name"]
                or reply["sender_user__email"]
                or reply["sender__name"]
                or reply["sender__email"]
                or "—"
            )
            entry = _author_entry(reply["sender_user_id"] or name, name)
            entry["replied"] += 1
            entry["seconds"] += seconds

        # Per author: conversations assigned to them holding an unanswered
        # incoming email. Adds rows for assignees who have not replied at all.
        _, assigned_per_user = _assignment_map(unanswered_threads)
        for user_id, info in assigned_per_user.items():
            _author_entry(user_id, info["name"])["unanswered"] = len(
                info["threads"]
            )

        author_rows = sorted(
            (
                {
                    "author": entry["author"],
                    "replied": entry["replied"],
                    "average_response_seconds": _average(
                        entry["seconds"], entry["replied"]
                    ),
                    "unanswered": entry["unanswered"],
                }
                for entry in authors.values()
            ),
            key=lambda row: row["replied"],
            reverse=True,
        )

        return Response(
            serializers.MailboxResponseTimesSerializer(
                {
                    "incoming": len(incoming),
                    "replied": global_replied,
                    "unreplied": len(incoming) - global_replied,
                    "average_response_seconds": _average(
                        global_seconds, global_replied
                    ),
                    "authors": author_rows,
                }
            ).data
        )

    @extend_schema(
        tags=["mailboxes"],
        parameters=[TIMEFRAME_PARAMETER],
        responses={200: serializers.MailboxResponseTimesByLabelSerializer},
    )
    @action(detail=True, methods=["get"], url_path="stats/response_times_by_label")
    def stats_response_times_by_label(self, request, pk=None):
        """Reply-time totals grouped by label.

        Same incoming/reply model as ``stats/response_times`` but each incoming
        email is counted once per label on its thread. Returns raw totals per
        label (received, replied, summed response seconds); the client rolls
        them up the label tree and derives averages and no-reply counts.
        """
        mailbox = self.get_object()
        start, end = resolve_timeframe(request.query_params.get("timeframe"))

        thread_ids = models.ThreadAccess.objects.filter(
            mailbox=mailbox
        ).values_list("thread_id", flat=True)

        incoming = list(
            models.Message.objects.filter(
                thread_id__in=thread_ids,
                is_sender=False,
                is_draft=False,
                is_trashed=False,
                is_spam=False,
                created_at__gte=start,
                created_at__lt=end,
            )
            .values("thread_id", "created_at")
            .order_by("created_at")
        )
        incoming_thread_ids = {msg["thread_id"] for msg in incoming}

        sent_by_thread = defaultdict(list)
        for reply in (
            models.Message.objects.filter(
                thread_id__in=incoming_thread_ids,
                is_sender=True,
                is_draft=False,
                is_trashed=False,
            )
            .values("thread_id", "created_at")
            .order_by("created_at")
        ):
            sent_by_thread[reply["thread_id"]].append(reply["created_at"])

        # thread -> labels (only this mailbox's labels).
        labels_by_thread = defaultdict(list)
        for row in models.Label.objects.filter(
            mailbox=mailbox, threads__id__in=incoming_thread_ids
        ).values("id", "threads"):
            labels_by_thread[row["threads"]].append(row["id"])

        per_label = {}
        for msg in incoming:
            label_ids = labels_by_thread.get(msg["thread_id"])
            if not label_ids:
                continue
            reply_time = next(
                (
                    sent
                    for sent in sent_by_thread.get(msg["thread_id"], ())
                    if sent > msg["created_at"]
                ),
                None,
            )
            seconds = (
                (reply_time - msg["created_at"]).total_seconds()
                if reply_time is not None
                else 0.0
            )
            for label_id in label_ids:
                entry = per_label.setdefault(
                    label_id, {"received": 0, "replied": 0, "seconds": 0.0}
                )
                entry["received"] += 1
                if reply_time is not None:
                    entry["replied"] += 1
                    entry["seconds"] += seconds

        labels_data = [
            {
                "label": label_id,
                "received": entry["received"],
                "replied": entry["replied"],
                "response_seconds_total": round(entry["seconds"]),
            }
            for label_id, entry in per_label.items()
        ]

        return Response(
            serializers.MailboxResponseTimesByLabelSerializer(
                {"labels": labels_data}
            ).data
        )

    @extend_schema(
        tags=["mailboxes"],
        responses={200: serializers.MailboxStorageStatsSerializer},
    )
    @action(detail=True, methods=["get"], url_path="stats/storage")
    def storage_stats(self, request, pk=None):
        """Return total storage and top-100 largest threads for the mailbox.

        Storage is computed using the same formula as the metrics endpoints:
        ``message_count * OVERHEAD + mime_blobs + draft_blobs
        + attachment_blobs + template_blobs``.

        Backs the Storage settings tab.
        """
        mailbox = self.get_object()

        thread_ids_subquery = (
            models.ThreadAccess.objects.filter(mailbox=mailbox)
            .values_list("thread_id", flat=True)
        )

        total_mime = (
            models.Blob.objects.filter(
                messages__thread__id__in=thread_ids_subquery
            )
            .distinct()
            .aggregate(total=Coalesce(Sum("size_compressed"), Value(0)))["total"]
        )

        total_draft = (
            models.Blob.objects.filter(
                drafts__thread__id__in=thread_ids_subquery
            )
            .distinct()
            .aggregate(total=Coalesce(Sum("size_compressed"), Value(0)))["total"]
        )

        total_attachments = (
            models.Attachment.objects.filter(mailbox=mailbox)
            .aggregate(
                total=Coalesce(Sum("blob__size_compressed"), Value(0))
            )["total"]
        )

        total_templates = (
            models.MessageTemplate.objects.filter(
                mailbox=mailbox, blob__isnull=False
            )
            .aggregate(
                total=Coalesce(Sum("blob__size_compressed"), Value(0))
            )["total"]
        )

        message_count = (
            models.Message.objects.filter(thread__id__in=thread_ids_subquery)
            .count()
        )

        thread_count = (
            models.Thread.objects.filter(accesses__mailbox=mailbox).count()
        )

        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        total_storage = (
            message_count * overhead
            + total_mime
            + total_draft
            + total_attachments
            + total_templates
        )

        # Storage held by a subset of conversations (trash, spam). Trash =
        # ``Thread.is_trashed`` (every message trashed). Attachments/templates
        # are not thread-scoped, so this covers message overhead plus mime and
        # draft blobs — the same basis as the per-thread sizes below.
        def _storage_for_threads(thread_ids):
            mime = (
                models.Blob.objects.filter(messages__thread__id__in=thread_ids)
                .distinct()
                .aggregate(total=Coalesce(Sum("size_compressed"), Value(0)))[
                    "total"
                ]
            )
            draft = (
                models.Blob.objects.filter(drafts__thread__id__in=thread_ids)
                .distinct()
                .aggregate(total=Coalesce(Sum("size_compressed"), Value(0)))[
                    "total"
                ]
            )
            msg_count = models.Message.objects.filter(
                thread__id__in=thread_ids
            ).count()
            return msg_count * overhead + mime + draft

        trashed_storage = _storage_for_threads(
            models.Thread.objects.filter(
                accesses__mailbox=mailbox, is_trashed=True
            ).values_list("id", flat=True)
        )
        spam_storage = _storage_for_threads(
            models.Thread.objects.filter(
                accesses__mailbox=mailbox, is_spam=True
            ).values_list("id", flat=True)
        )

        # ``order_by()`` clears Blob's default ``Meta.ordering`` so it does not
        # leak into the GROUP BY and split the per-thread sum into per-blob rows
        # (which would make ``[:1]`` return a single blob's size). Same pattern
        # as the metrics endpoints and the message-count subquery below.
        thread_size_subquery = Subquery(
            models.Blob.objects.filter(
                messages__thread=OuterRef("pk")
            )
            .order_by()
            .values("messages__thread")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )

        thread_draft_size_subquery = Subquery(
            models.Blob.objects.filter(
                drafts__thread=OuterRef("pk")
            )
            .order_by()
            .values("drafts__thread")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )

        thread_msg_count_subquery = Subquery(
            models.Message.objects.filter(thread=OuterRef("pk"))
            .order_by()
            .values("thread")
            .annotate(cnt=Count("pk"))
            .values("cnt")[:1]
        )

        # This mailbox's own read cursor on each thread; unread is derived from
        # it below (same rule as the thread list: no cursor, or a message newer
        # than the cursor).
        read_at_subquery = Subquery(
            models.ThreadAccess.objects.filter(
                thread=OuterRef("pk"), mailbox=mailbox
            ).values("read_at")[:1]
        )

        largest_threads = (
            models.Thread.objects.filter(accesses__mailbox=mailbox)
            .annotate(
                blob_size=Coalesce(thread_size_subquery, Value(0)),
                draft_size=Coalesce(thread_draft_size_subquery, Value(0)),
                msg_count=Coalesce(thread_msg_count_subquery, Value(0)),
                access_read_at=read_at_subquery,
            )
            .annotate(
                total_size=F("blob_size") + F("draft_size")
            )
            .order_by("-total_size")[:100]
        )

        largest_threads_data = [
            {
                "id": str(thread.id),
                "subject": thread.subject,
                "size": int(thread.total_size),
                "message_count": int(thread.msg_count),
                "messaged_at": thread.messaged_at,
                "is_unread": bool(
                    thread.messaged_at
                    and (
                        thread.access_read_at is None
                        or thread.messaged_at > thread.access_read_at
                    )
                ),
            }
            for thread in largest_threads
        ]

        return Response(
            serializers.MailboxStorageStatsSerializer(
                {
                    "total_storage": total_storage,
                    "trashed_storage": trashed_storage,
                    "spam_storage": spam_storage,
                    "message_count": message_count,
                    "thread_count": thread_count,
                    "largest_threads": largest_threads_data,
                }
            ).data
        )
