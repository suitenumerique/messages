"""API ViewSet for ThreadEvent model."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models

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

    def get_queryset(self):
        """Restrict results to events for the specified thread."""
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadEvent.objects.none()

        return (
            models.ThreadEvent.objects.filter(thread_id=thread_id)
            .select_related("author", "channel", "message")
            .order_by("created_at")
        )

    def perform_create(self, serializer):
        """Set thread from URL and author from request user."""
        thread = get_object_or_404(models.Thread, id=self.kwargs["thread_id"])
        serializer.save(thread=thread, author=self.request.user)
