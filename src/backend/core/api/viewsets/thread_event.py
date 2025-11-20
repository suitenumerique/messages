"""API ViewSet for ThreadEvent model."""

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


@extend_schema(tags=["thread-event"])
class ThreadEventViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.RetrieveModelMixin,
):
    """ViewSet for ThreadEvent model."""

    serializer_class = serializers.ThreadEventSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccess,
    ]
    lookup_field = "id"
    lookup_url_kwarg = "id"
    queryset = (
        models.ThreadEvent.objects.select_related("thread")
        .select_related("channel")
        .select_related("message")
        .all()
    )

    def get_serializer_class(self):
        """Use create serializer for CREATE, default for all other operations."""
        if self.action == "create":
            return serializers.ThreadEventCreateSerializer
        return serializers.ThreadEventSerializer

    def get_queryset(self):
        """Restrict results to thread events for the specified thread.
        ThreadAccess is checked by IsAllowedToAccess permission class.
        """
        # Get thread_id from URL kwargs (provided by nested router)
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadEvent.objects.none()

        # Filter by thread_id only - access control handled by permission class
        return self.queryset.filter(thread_id=thread_id).order_by("created_at")

    def perform_create(self, serializer):
        """Set the thread from URL kwargs when creating a ThreadEvent."""
        thread_id = self.kwargs.get("thread_id")
        thread = models.Thread.objects.get(id=thread_id)
        serializer.save(thread=thread)
