"""API ViewSet for Mailbox model."""

import uuid

from django.conf import settings
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core import enums, models
from core.entitlements import (
    EntitlementsUnavailableError,
    get_mailbox_entitlements,
)
from core.services.storage import get_mailbox_storage_used
from core.services.trashbin import (
    EMPTY_TRASHBIN_SYNC_LIMIT,
    empty_trashbin,
    empty_trashbin_task,
    trashbin_messages,
    trashbin_scope_filter,
)
from core.utils import register_task_owner

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

        The total is the cached figure the sidebar gauge reads (see
        ``core.services.storage``), so "Total storage used" always matches it.
        Per-thread sizes and the trash/spam subtotals cover message overhead
        plus MIME and draft blobs — attachments and templates are not
        thread-scoped. Per-thread sizes leave out the trashbin, which the
        subtotals account for.

        Backs the Storage settings tab; mailbox admins only.
        """
        mailbox = self.get_object()
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        thread_ids = models.ThreadAccess.objects.filter(mailbox=mailbox).values_list(
            "thread_id", flat=True
        )
        mailbox_messages = models.Message.objects.filter(
            thread_id__in=thread_ids
        ).order_by()

        def _size(condition=None):
            """Overhead plus MIME and draft blobs of the matching messages.

            ``blob`` and ``draft_blob`` are both forward FKs, so joining them
            adds no row fan-out and Count() stays one-per-message. Blobs are
            summed per referencing message rather than deduplicated, the same
            "storage felt" basis as the total (see core.services.storage).
            """
            return (
                Coalesce(Sum("blob__size_compressed", filter=condition), Value(0))
                + Coalesce(
                    Sum("draft_blob__size_compressed", filter=condition), Value(0)
                )
                + Count("id", filter=condition) * overhead
            )

        # One pass over the mailbox's messages. The trash/spam subtotals use the
        # very filters ``empty_trashbin`` deletes with, so each is exactly the
        # space emptying that folder frees: trashed messages one by one
        # (Thread.is_trashed would miss partly trashed threads), and every
        # message of a spam conversation.
        totals = mailbox_messages.aggregate(
            message_count=Count("id"),
            trashed_storage=_size(trashbin_scope_filter("trashed")),
            spam_storage=_size(trashbin_scope_filter("spam")),
        )

        # The largest conversations only weigh what is outside the trashbin:
        # trashbin items are already counted by the subtotals above, and the
        # list offers to move conversations to the trash, so one already there
        # would keep coming back. A single GROUP BY thread, sorted and cut to
        # 100 in the database.
        largest = list(
            mailbox_messages.filter(is_trashed=False)
            .exclude(trashbin_scope_filter("spam"))
            .values("thread_id")
            .annotate(size=_size(), message_count=Count("id"))
            .order_by("-size", "thread_id")[:100]
        )
        # Those 100 threads as model instances for the serializer, carrying
        # their size, message count, this mailbox's own read cursor and whether
        # the user may edit them: mailbox admin rights do not reach a thread
        # shared with the mailbox as a viewer, which cannot be trashed.
        threads = (
            models.Thread.objects.filter(accesses__mailbox=mailbox)
            .annotate(
                read_at=F("accesses__read_at"),
                _can_edit=Exists(
                    models.ThreadAccess.objects.editable_by(
                        request.user, mailbox_id=mailbox.id
                    ).filter(thread=OuterRef("pk"))
                ),
            )
            .in_bulk([row["thread_id"] for row in largest])
        )
        largest_threads = []
        for row in largest:
            thread = threads[row["thread_id"]]
            thread.size = row["size"]
            thread.message_count = row["message_count"]
            largest_threads.append(thread)

        serializer = serializers.MailboxStorageStatsSerializer(
            {
                "total_storage": get_mailbox_storage_used(mailbox),
                "trashed_storage": totals["trashed_storage"],
                "spam_storage": totals["spam_storage"],
                "message_count": totals["message_count"],
                "thread_count": thread_ids.count(),
                "largest_threads": largest_threads,
            }
        )
        return Response(serializer.data)

    @extend_schema(
        tags=["mailboxes"],
        request=serializers.MailboxEmptyTrashRequestSerializer,
        responses={
            200: serializers.MailboxEmptyTrashResponseSerializer,
            202: serializers.MailboxEmptyTrashTaskResponseSerializer,
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
            "TRASHBIN_ALLOW_EMPTY policy. Up to 1000 messages the deletion runs "
            "within the request (200); beyond, it runs in a background task "
            "(202) whose progress is read from the task status endpoint."
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
        scope = serializer.validated_data["scope"]
        thread_ids = serializer.validated_data["thread_ids"]
        message_ids = serializer.validated_data["message_ids"]

        to_delete = trashbin_messages(
            mailbox,
            scope,
            request.user,
            thread_ids=thread_ids,
            message_ids=message_ids,
        ).count()
        if to_delete <= EMPTY_TRASHBIN_SYNC_LIMIT:
            deleted_count = empty_trashbin(
                mailbox,
                scope,
                request.user,
                thread_ids=thread_ids,
                message_ids=message_ids,
            )
            return Response({"success": True, "deleted_count": deleted_count})

        # Too large for the request: run it in the background. The task id is
        # pre-generated so its owner is registered before the task can start.
        task_id = str(uuid.uuid4())
        register_task_owner(task_id, request.user.id)
        empty_trashbin_task.apply_async(
            args=[str(mailbox.id), scope, str(request.user.id)],
            kwargs={
                "thread_ids": [str(thread_id) for thread_id in thread_ids],
                "message_ids": [str(message_id) for message_id in message_ids],
            },
            task_id=task_id,
        )
        return Response({"task_id": task_id}, status=status.HTTP_202_ACCEPTED)
