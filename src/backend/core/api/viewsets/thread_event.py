"""API ViewSet for ThreadEvent model."""

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404
from django.utils import timezone

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from core import enums, models
from core.services import thread_events as thread_events_service

from .. import permissions, serializers


@extend_schema(tags=["thread-events"])
class ThreadEventViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for ThreadEvent model."""

    serializer_class = serializers.ThreadEventSerializer
    pagination_class = None
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccess,
    ]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_permissions(self):
        """Route write actions through the type-aware permission class.

        Reads (``list``, ``retrieve``) and the personal ``read_mention``
        acknowledgement only need read access on the thread. Writes are
        gated by :class:`HasThreadEventWriteAccess`, which relaxes the
        ThreadAccess role for ``im`` (comment) events while keeping the
        stricter full-edit-rights rule for every other event type.
        """
        if self.action in ["list", "retrieve", "read_mention"]:
            return [permissions.IsAuthenticated(), permissions.IsAllowedToAccess()]

        return [permissions.HasThreadEventWriteAccess()]

    def get_queryset(self):
        """Restrict results to events for the specified thread."""
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadEvent.objects.none()

        queryset = models.ThreadEvent.objects.filter(
            thread_id=thread_id
        ).select_related("author", "channel", "message")

        # Annotate with unread mention status for the current user
        if self.request.user.is_authenticated:
            queryset = queryset.annotate(
                _has_unread_mention=Exists(
                    models.UserEvent.objects.filter(
                        thread_event=OuterRef("pk"),
                        user=self.request.user,
                        type=enums.UserEventTypeChoices.MENTION,
                        read_at__isnull=True,
                    )
                )
            )

        return queryset.order_by("created_at")

    @transaction.atomic
    def perform_update(self, serializer):
        """Reject IM updates after the edit delay; re-sync mentions on success."""
        if not serializer.instance.is_editable():
            raise PermissionDenied(
                "This event can no longer be edited (edit delay expired)."
            )
        thread_event = serializer.save()
        # IM is the only editable type; re-sync MENTION rows so an edit
        # that changes the mentions list adds/removes notifications.
        thread_events_service.sync_im_mentions(thread_event=thread_event)

    @extend_schema(
        request=None,
        responses={
            204: OpenApiResponse(description="No response body"),
            404: OpenApiResponse(description="Thread event not found"),
        },
    )
    @action(detail=True, methods=["patch"], url_path="read-mention")
    def read_mention(self, request, **kwargs):
        """Mark the current user's unread MENTION on this ThreadEvent as read.

        Returns 204 even when no UserEvent matches (idempotent); the thread
        event itself is resolved via the standard ``get_object`` lookup so a
        missing event yields 404.
        """
        thread_event = self.get_object()
        models.UserEvent.objects.filter(
            user=request.user,
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create a ThreadEvent.

        For ASSIGN/UNASSIGN, delegates to the service layer which owns the
        idempotence rules, edit-rights validation and the undo window.
        For IM, persists the event via the serializer and then re-syncs
        MENTION rows.

        Returns 204 when the service decides nothing was new (every
        assignee already assigned, full UNASSIGN absorbed by undo, …).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        event_type = serializer.validated_data.get("type")
        thread = get_object_or_404(
            models.Thread.objects.select_for_update(),
            id=self.kwargs["thread_id"],
        )

        if event_type == enums.ThreadEventTypeChoices.ASSIGN:
            assignees_data = serializer.validated_data["data"]["assignees"]
            try:
                thread_event = thread_events_service.assign_users(
                    thread=thread, author=request.user, assignees_data=assignees_data
                )
            except ValueError as exc:
                raise ValidationError(
                    "Assignee must have editor access on the thread"
                ) from exc
            if thread_event is None:
                return Response(status=status.HTTP_204_NO_CONTENT)
            return self._serialize_created(thread_event)

        if event_type == enums.ThreadEventTypeChoices.UNASSIGN:
            assignees_data = serializer.validated_data["data"]["assignees"]
            thread_event = thread_events_service.unassign_users(
                thread=thread, author=request.user, assignees_data=assignees_data
            )
            if thread_event is None:
                return Response(status=status.HTTP_204_NO_CONTENT)
            return self._serialize_created(thread_event)

        # IM and any future regular event type: persist via serializer,
        # then sync MENTION rows when applicable.
        thread_event = serializer.save(thread=thread, author=request.user)
        thread_events_service.sync_im_mentions(thread_event=thread_event)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def _serialize_created(self, thread_event):
        """Build the 201 response from a ThreadEvent created by the service.

        The service path bypasses ``serializer.save()`` (it owns the
        ThreadEvent + UserEvent atomicity) so we re-serialize the result
        through a fresh serializer to keep the response shape identical
        to the legacy path.
        """
        serializer = self.get_serializer(thread_event)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )
