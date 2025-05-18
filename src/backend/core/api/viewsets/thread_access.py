"""API ViewSet for ThreadAccess model."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework import mixins, viewsets

from core import models

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

    def get_queryset(self):
        """Restrict results to thread accesses for the specified thread."""
        # Get thread_id from URL kwargs (provided by nested router)
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadAccess.objects.none()

        # Filter by thread_id from URL
        queryset = self.queryset.filter(thread_id=thread_id)

        # Optional mailbox filter
        mailbox_id = self.request.GET.get("mailbox_id")
        if mailbox_id:
            queryset = queryset.filter(mailbox_id=mailbox_id)
        return queryset.distinct()

    def create(self, request, *args, **kwargs):
        """Create a new thread access."""
        request.data["thread"] = self.kwargs.get("thread_id")
        return super().create(request, *args, **kwargs)
