"""API ViewSet for ThreadEvent model."""

from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404
from django.utils import timezone

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core import enums, models

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

    def perform_create(self, serializer):
        """Set thread from URL and author from request user."""
        thread = get_object_or_404(models.Thread, id=self.kwargs["thread_id"])
        serializer.save(thread=thread, author=self.request.user)

    def perform_update(self, serializer):
        """Reject updates made after the configured edit delay elapsed.

        Past the window, the event (and any UserEvent MENTION records derived
        from it) is considered historical and must remain immutable.
        """
        if not serializer.instance.is_editable():
            raise PermissionDenied(
                "This event can no longer be edited (edit delay expired)."
            )
        serializer.save()

    def perform_destroy(self, instance):
        """Reject deletions made after the configured edit delay elapsed.

        Deletion is gated alongside edits so users cannot circumvent the
        window by deleting and recreating an event.
        """
        if not instance.is_editable():
            raise PermissionDenied(
                "This event can no longer be deleted (edit delay expired)."
            )
        instance.delete()

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
