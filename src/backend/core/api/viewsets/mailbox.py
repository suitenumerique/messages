"""API ViewSet for Mailbox model."""

from django.conf import settings
from django.db.models import Count, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core import enums, models
from core.entitlements import (
    EntitlementsUnavailableError,
    get_mailbox_entitlements,
)
from core.services.storage import compute_mailbox_storage_used
from core.services.trashbin import empty_trashbin

from .. import permissions, serializers


class MailboxViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin
):
    """ViewSet for Mailbox model."""

    serializer_class = serializers.MailboxSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Restrict results to the current user's mailboxes."""
        user = self.request.user

        return (
            models.Mailbox.objects.filter(accesses__user=user)
            .prefetch_related("accesses__user", "domain")
            .annotate(
                user_role=Subquery(
                    models.MailboxAccess.objects.filter(
                        mailbox=OuterRef("pk"), user=user
                    ).values("role")[:1]
                )
            )
            .order_by("-created_at")
        )

    def get_permissions(self):
        """Require mailbox-admin rights to edit or read the storage breakdown;
        other reads stay open to any member of the mailbox."""
        if self.action in ("partial_update", "storage"):
            return [
                permissions.IsAuthenticated(),
                permissions.IsMailboxAdminObject(),
            ]
        return super().get_permissions()

    @extend_schema(
        tags=["mailboxes"],
        request=serializers.MailboxNameUpdateSerializer,
        responses=serializers.MailboxSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        """Rename a mailbox (its display contact name). Mailbox admins only.

        ``partial=True`` keeps true PATCH semantics: omitting ``name`` is a no-op
        rather than a 400, so the runtime matches the optional request schema.
        """
        mailbox = self.get_object()
        serializer = serializers.MailboxNameUpdateSerializer(
            instance=mailbox, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = self.get_serializer(mailbox)
        return Response(output.data)

    @extend_schema(
        tags=["mailboxes"],
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search mailboxes by domain, local part and contact name.",
            ),
        ],
        responses=serializers.MailboxLightSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def search(self, request, **kwargs):
        """
        Search mailboxes by domain, local part and contact name.

        Query parameters:
        - q: Optional search query for local part and contact name
        """
        domain = self.get_object().domain

        # Start with all mailboxes in the same domain except the current one
        queryset = models.Mailbox.objects.filter(domain=domain).exclude(
            id=self.get_object().id
        )

        # Add filters for local part and contact name if provided
        if query := request.query_params.get("q", ""):
            queryset = queryset.filter(
                Q(local_part__unaccent__icontains=query)
                | Q(contact__name__unaccent__icontains=query)
            )  # exclude context mailbox

        # Order by contact name if available, otherwise by email
        queryset = queryset.order_by("contact__name", "local_part", "domain")

        serializer = serializers.MailboxLightSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["mailboxes"],
        responses=serializers.MailboxEntitlementsSerializer,
    )
    @action(detail=True, methods=["get"])
    def entitlements(self, request, **kwargs):
        """Return storage entitlements (usage and limits) for the mailbox.

        Quotas live on the mailbox, not the user. Access is restricted to the
        mailbox's own members through ``get_object`` (the viewset queryset is
        already filtered to the current user's mailboxes).

        When the entitlements backend is unavailable, the gauge is degraded
        rather than erroring the request: usage falls back to 0 and limits to
        null, which hides the gauge on the frontend.
        """
        mailbox = self.get_object()
        try:
            data = get_mailbox_entitlements(mailbox)
        except EntitlementsUnavailableError:
            data = {
                "account": {"storage_used": 0, "max_storage": None},
                "organization": None,
            }
        serializer = serializers.MailboxEntitlementsSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["mailboxes"],
        responses=serializers.MailboxStorageStatsSerializer,
    )
    @action(detail=True, methods=["get"], url_path="storage")
    def storage(self, request, **kwargs):
        """Return total storage and the top-100 largest threads for the mailbox.

        The total is computed with the shared storage service (the same formula
        the metrics endpoints and the quota gauge use), so the "Total storage
        used" here always matches the sidebar gauge. Per-thread sizes and the
        trash/spam subtotals cover message overhead plus MIME and draft blobs —
        attachments and templates are not thread-scoped.

        Backs the Storage settings tab; mailbox admins only.
        """
        mailbox = self.get_object()
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        thread_ids = models.ThreadAccess.objects.filter(mailbox=mailbox).values_list(
            "thread_id", flat=True
        )

        # Total mirrors the gauge exactly (message overhead + every blob the
        # mailbox reaches), so the two never disagree.
        total_storage = compute_mailbox_storage_used(mailbox)
        message_count = models.Message.objects.filter(thread__id__in=thread_ids).count()
        thread_count = models.Thread.objects.filter(accesses__mailbox=mailbox).count()

        # Storage held by trashed / spam mail: message overhead plus MIME and
        # draft blobs — the same basis as the per-thread sizes below
        # (attachments/templates are not thread-scoped).
        #
        # Scoped per MESSAGE, not per thread. The thread-level flags are
        # denormalizations that answer a different question: Thread.is_trashed
        # is true only when *every* message is trashed, and Thread.is_spam
        # mirrors the *first* message alone (see Thread.update_stats). Summing
        # whole threads through them therefore both under-counted (a partly
        # trashed thread contributed nothing) and over-counted (a spam-first
        # thread contributed its non-spam messages too). Message-level scoping
        # matches what ``empty_trashbin`` actually deletes.
        #
        # ``blob`` and ``draft_blob`` are both forward FKs, so joining them adds
        # no row fan-out and Count() stays one-per-message. Blobs are summed per
        # referencing message rather than deduplicated, which is the same
        # "storage felt" basis as the total above (see core.services.storage).
        def _storage_for_messages(condition):
            return models.Message.objects.filter(
                condition, thread_id__in=thread_ids
            ).aggregate(
                total=Coalesce(Sum("blob__size_compressed"), Value(0))
                + Coalesce(Sum("draft_blob__size_compressed"), Value(0))
                + Count("id") * overhead
            )["total"]

        trashed_storage = _storage_for_messages(Q(is_trashed=True))
        spam_storage = _storage_for_messages(Q(is_spam=True))

        # ``order_by()`` clears Blob's default Meta.ordering so it does not leak
        # into the GROUP BY and split the per-thread sum into per-blob rows.
        thread_size_subquery = Subquery(
            models.Blob.objects.filter(messages__thread=OuterRef("pk"))
            .order_by()
            .values("messages__thread")
            .annotate(total=Sum("size_compressed"))
            .values("total")[:1]
        )
        thread_draft_size_subquery = Subquery(
            models.Blob.objects.filter(drafts__thread=OuterRef("pk"))
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
        # This mailbox's own read cursor on each thread; unread is derived below
        # (same rule as the thread list: no cursor, or a message newer than it).
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
            .annotate(total_size=F("blob_size") + F("draft_size"))
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

        serializer = serializers.MailboxStorageStatsSerializer(
            {
                "total_storage": total_storage,
                "trashed_storage": trashed_storage,
                "spam_storage": spam_storage,
                "message_count": message_count,
                "thread_count": thread_count,
                "largest_threads": largest_threads_data,
            }
        )
        return Response(serializer.data)

    @extend_schema(
        tags=["mailboxes"],
        request=serializers.MailboxEmptyTrashRequestSerializer,
        responses={
            200: serializers.MailboxEmptyTrashResponseSerializer,
            403: OpenApiResponse(
                description=(
                    "Emptying the trashbin is not allowed for your role "
                    "(governed by TRASHBIN_ALLOW_EMPTY)."
                ),
            ),
        },
        description=(
            "Permanently delete trashed or spam messages in the mailbox (pick "
            "the folder with `scope`). Deletes the whole folder by default, or "
            "only the items named by `thread_ids` / `message_ids`. This cannot "
            "be undone. Allowed only to the roles named by the "
            "TRASHBIN_ALLOW_EMPTY policy."
        ),
    )
    @action(detail=True, methods=["post"], url_path="empty-trash")
    def empty_trash(self, request, **kwargs):
        """Permanently delete from one folder of the mailbox's trashbin.

        The trashbin is ``is_trashed OR is_spam``; ``scope`` selects which folder
        ("trashed" or "spam"). With no ``thread_ids``/``message_ids`` the whole
        folder is wiped; with them, only those items.

        Both cases go through one gate — the ``empty_trash`` mailbox ability,
        which encodes the ``TRASHBIN_ALLOW_EMPTY`` policy — because permanently
        deleting one selected message and emptying the folder are the same
        privilege. Keeping them on a single action means the two can never drift
        apart into different permission checks.
        """
        mailbox = self.get_object()

        if not mailbox.get_abilities(request.user).get(
            enums.MailboxAbilities.CAN_EMPTY_TRASH
        ):
            raise PermissionDenied(
                "You are not allowed to permanently delete from this trashbin. "
                "This action is irreversible and restricted by your "
                "administrator's policy."
            )

        serializer = serializers.MailboxEmptyTrashRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        deleted_count = empty_trashbin(
            mailbox,
            serializer.validated_data["scope"],
            request.user,
            thread_ids=serializer.validated_data["thread_ids"],
            message_ids=serializer.validated_data["message_ids"],
        )
        return Response({"success": True, "deleted_count": deleted_count})
