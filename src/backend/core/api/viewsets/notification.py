"""API ViewSet for UserNotification model."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers as drf_serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models

from .. import permissions, serializers


@extend_schema(
    tags=["notifications"],
    parameters=[
        OpenApiParameter(
            name="is_done",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description="Filter notifications by done status.",
            required=False,
        ),
        OpenApiParameter(
            name="type",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter notifications by type.",
            required=False,
        ),
        OpenApiParameter(
            name="thread_id",
            type=OpenApiTypes.UUID,
            location=OpenApiParameter.QUERY,
            description="Filter notifications by thread ID.",
            required=False,
        ),
    ],
)
class UserNotificationViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
):
    """ViewSet for UserNotification model."""

    serializer_class = serializers.UserNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to notifications for the current user."""
        queryset = models.UserNotification.objects.filter(
            user=self.request.user
        ).select_related("thread", "thread_event", "thread_event__author")

        # Apply optional filters
        is_done = self.request.query_params.get("is_done")
        if is_done is not None:
            queryset = queryset.filter(is_done=is_done.lower() in ("true", "1"))

        notification_type = self.request.query_params.get("type")
        if notification_type:
            queryset = queryset.filter(type=notification_type)

        thread_id = self.request.query_params.get("thread_id")
        if thread_id:
            queryset = queryset.filter(thread_id=thread_id)

        return queryset

    @extend_schema(
        responses={
            200: inline_serializer(
                name="NotificationCount",
                fields={"count": drf_serializers.IntegerField()},
            ),
        },
        description="Return the count of unread notifications for the authenticated user.",
    )
    @action(detail=False, methods=["get"], url_path="count", url_name="count")
    def count(self, request):
        """Return count of unread notifications."""
        count = self.get_queryset().filter(is_done=False).count()
        return Response({"count": count})

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="NotificationMarkAllDone",
                fields={"updated": drf_serializers.IntegerField()},
            ),
        },
        description="Mark all notifications as done for the authenticated user.",
    )
    @action(
        detail=False, methods=["post"], url_path="mark-all-done", url_name="mark-all-done"
    )
    def mark_all_done(self, request):
        """Mark all user notifications as done."""
        updated = self.get_queryset().filter(is_done=False).update(is_done=True)
        return Response({"updated": updated})
