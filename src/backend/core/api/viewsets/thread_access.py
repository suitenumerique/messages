"""API ViewSet for ThreadAccess model."""

from django.db import transaction

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from core import enums, models
from core.services import thread_events as thread_events_service

from .. import permissions, serializers


@extend_schema(
    tags=["thread-access"],
    parameters=[
        OpenApiParameter(
            name="mailbox_id",
            type=OpenApiTypes.UUID,
            location=OpenApiParameter.QUERY,
            description="Filter thread accesses by mailbox ID.",
            required=False,
        ),
    ],
)
class ThreadAccessViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for ThreadAccess model."""

    serializer_class = serializers.ThreadAccessSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToManageThreadAccess,
    ]
    lookup_field = "id"
    lookup_url_kwarg = "id"
    queryset = models.ThreadAccess.objects.all()
    pagination_class = None

    def get_queryset(self):
        """Restrict results to thread accesses for the specified thread."""
        # Get thread_id from URL kwargs (provided by nested router)
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadAccess.objects.none()

        # Filter by thread_id from URL
        queryset = self.queryset.filter(thread_id=thread_id)

        # The list endpoint serializes the `users` field (non-viewer users
        # of each mailbox). Prefetching the access/user chain keeps the
        # query count constant regardless of the number of accesses.
        if self.action == "list":
            queryset = queryset.prefetch_related("mailbox__accesses__user")

        # Optional mailbox filter
        mailbox_id = self.request.GET.get("mailbox_id")
        if mailbox_id:
            queryset = queryset.filter(mailbox_id=mailbox_id)
        return queryset.distinct()

    def create(self, request, *args, **kwargs):
        """Create a new thread access."""
        request.data["thread"] = self.kwargs.get("thread_id")
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def perform_update(self, serializer):
        """Reject downgrading the last editor; cleanup assignments otherwise.

        The role transition is computed against the row stored in DB rather
        than the serializer's ``instance`` field because callers may submit
        a no-op patch — comparing the persisted state guarantees the
        cleanup runs exactly once per real downgrade.

        When the patch leaves the EDITOR role, every editor row of the
        thread is locked with FOR UPDATE in id order — same sequence as
        ``perform_destroy`` — so concurrent downgrades and deletions cannot
        deadlock and cannot collectively drain the thread of editors.
        """
        previous_role = serializer.instance.role
        new_role = serializer.validated_data.get("role", previous_role)
        if (
            previous_role == enums.ThreadAccessRoleChoices.EDITOR
            and new_role != enums.ThreadAccessRoleChoices.EDITOR
        ):
            editor_ids = list(
                models.ThreadAccess.objects.select_for_update()
                .filter(
                    thread_id=serializer.instance.thread_id,
                    role=enums.ThreadAccessRoleChoices.EDITOR,
                )
                .order_by("id")
                .values_list("id", flat=True)
            )
            if editor_ids == [serializer.instance.id]:
                raise ValidationError(
                    "Cannot downgrade the last editor access of a thread."
                )
        thread_access = serializer.save()
        if (
            previous_role == enums.ThreadAccessRoleChoices.EDITOR
            and thread_access.role != enums.ThreadAccessRoleChoices.EDITOR
        ):
            thread_events_service.downgrade_thread_access(thread_access=thread_access)

    @transaction.atomic
    def perform_destroy(self, instance):
        """Prevent deletion of the last editor access; cleanup after delete.

        Locks every editor row for the thread (including the one being
        deleted) with FOR UPDATE in id order so concurrent deletions acquire
        locks in the same sequence and cannot deadlock. Once the row is
        gone, ``thread_events_service.revoke_thread_access`` runs the
        assignment/mention cleanup using the still-in-memory ``mailbox``
        and ``thread`` references on the deleted instance.
        """
        if instance.role == enums.ThreadAccessRoleChoices.EDITOR:
            editor_ids = list(
                models.ThreadAccess.objects.select_for_update()
                .filter(
                    thread=instance.thread,
                    role=enums.ThreadAccessRoleChoices.EDITOR,
                )
                .order_by("id")
                .values_list("id", flat=True)
            )
            if editor_ids == [instance.id]:
                raise ValidationError(
                    "Cannot delete the last editor access of a thread."
                )
        super().perform_destroy(instance)
        thread_events_service.revoke_thread_access(thread_access=instance)
