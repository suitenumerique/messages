import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, viewsets
from rest_framework import serializers as drf_serializers

from core import models

from .. import permissions, serializers


class ThreadViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.DestroyModelMixin
):
    """ViewSet for Thread model."""

    serializer_class = serializers.ThreadSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccessMailbox,
    ]
    lookup_field = "id"
    lookup_url_kwarg = "id"
    queryset = models.Thread.objects.all()

    def get_queryset(self):
        """Restrict results to threads of the current user's mailboxes."""
        mailbox_id = self.request.GET.get("mailbox_id")
        accesses = self.request.user.mailbox_accesses.all()
        queryset = models.Thread.objects.filter(
            mailbox__id__in=accesses.values_list("mailbox_id", flat=True)
        ).order_by("-messages__created_at")
        if mailbox_id:
            return queryset.filter(mailbox__id=mailbox_id)
        return queryset

    @extend_schema(
        tags=["threads"],
        request=inline_serializer(
            name="ThreadBulkDeleteRequest",
            fields={
                "thread_ids": drf_serializers.ListField(
                    child=drf_serializers.UUIDField(),
                    required=True,
                    help_text="List of thread IDs to delete",
                ),
            },
        ),
        responses={
            200: OpenApiExample(
                "Success Response",
                value={"detail": "Successfully deleted 5 threads", "deleted_count": 5},
            ),
            400: OpenApiExample(
                "Validation Error", value={"detail": "thread_ids must be provided"}
            ),
        },
        description="Delete multiple threads at once by providing a list of thread IDs.",
    )
    @drf.decorators.action(
        detail=False,
        methods=["post"],
        url_path="bulk-delete",
        url_name="bulk-delete",
    )
    def bulk_delete(self, request):
        """Delete multiple threads at once."""
        thread_ids = request.data.get("thread_ids", [])

        if not thread_ids:
            return drf.response.Response(
                {"detail": "thread_ids must be provided"},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        # Get threads the user has access to
        # Check if user has delete permission for each thread
        threads_to_delete = []
        forbidden_threads = []

        for thread_id in thread_ids:
            try:
                thread = models.Thread.objects.get(id=thread_id)
                # Check if user has permission to delete this thread
                try:
                    self.check_object_permissions(self.request, thread)
                except drf.exceptions.PermissionDenied:
                    forbidden_threads.append(thread_id)
                else:
                    threads_to_delete.append(thread_id)
            except models.Thread.DoesNotExist:
                # Skip threads that don't exist
                pass

        if forbidden_threads and not threads_to_delete:
            # If all requested threads are forbidden, return 403
            return drf.response.Response(
                {"detail": "You don't have permission to delete these threads"},
                status=drf.status.HTTP_403_FORBIDDEN,
            )

        # Update thread_ids to only include those with proper permissions
        accessible_threads = self.get_queryset().filter(id__in=threads_to_delete)

        # Count before deletion
        count = accessible_threads.count()

        # Delete the threads
        accessible_threads.delete()

        return drf.response.Response(
            {
                "detail": f"Successfully deleted {count} threads",
                "deleted_count": count,
            }
        )
