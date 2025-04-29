"""API ViewSet for Thread model."""

import rest_framework as drf
from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


class ThreadViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.DestroyModelMixin
):
    """ViewSet for Thread model."""

    serializer_class = serializers.ThreadSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to threads of the current user's mailboxes."""
        mailbox_id = self.request.GET.get("mailbox_id")
        accesses = self.request.user.mailbox_accesses.all()
        queryset = models.Thread.objects

        if mailbox_id:
            if not accesses.filter(mailbox__id=mailbox_id).exists():
                raise drf.exceptions.PermissionDenied(
                    "You do not have access to this mailbox."
                )
            queryset = queryset.filter(mailbox__id=mailbox_id)
        else:
            queryset = queryset.filter(
                mailbox__id__in=accesses.values_list("mailbox_id", flat=True)
            )
        queryset = queryset.order_by("-messaged_at")

        # Add filters based on thread counters
        filter_mapping = {
            "has_unread": "count_unread__gt",
            "is_trashed": "count_trashed__gt",
            "has_draft": "count_draft__gt",
            "has_starred": "count_starred__gt",
            "has_sender": "count_sender__gt",
        }

        for param, filter_lookup in filter_mapping.items():
            value = self.request.GET.get(param)
            if value is not None:
                if value == "1":
                    queryset = queryset.filter(**{filter_lookup: 0})
                else:
                    # Allow filtering for threads with zero count
                    queryset = queryset.filter(**{filter_lookup.replace("__gt", ""): 0})

        return queryset

    # @extend_schema(
    #     tags=["threads"],
    #     request=inline_serializer(
    #         name="ThreadBulkDeleteRequest",
    #         fields={
    #             "thread_ids": drf_serializers.ListField(
    #                 child=drf_serializers.UUIDField(),
    #                 required=True,
    #                 help_text="List of thread IDs to delete",
    #             ),
    #         },
    #     ),
    #     responses={
    #         200: OpenApiExample(
    #             "Success Response",
    #             value={"detail": "Successfully deleted 5 threads", "deleted_count": 5},
    #         ),
    #         400: OpenApiExample(
    #             "Validation Error", value={"detail": "thread_ids must be provided"}
    #         ),
    #     },
    #     description="Delete multiple threads at once by providing a list of thread IDs.",
    # )
    # @drf.decorators.action(
    #     detail=False,
    #     methods=["post"],
    #     url_path="bulk-delete",
    #     url_name="bulk-delete",
    # )
    # def bulk_delete(self, request):
    #     """Delete multiple threads at once."""
    #     thread_ids = request.data.get("thread_ids", [])

    #     if not thread_ids:
    #         return drf.response.Response(
    #             {"detail": "thread_ids must be provided"},
    #             status=drf.status.HTTP_400_BAD_REQUEST,
    #         )

    #     # Get threads the user has access to
    #     # Check if user has delete permission for each thread
    #     threads_to_delete = []
    #     forbidden_threads = []

    #     for thread_id in thread_ids:
    #         try:
    #             thread = models.Thread.objects.get(id=thread_id)
    #             # Check if user has permission to delete this thread
    #             try:
    #                 self.check_object_permissions(self.request, thread)
    #             except drf.exceptions.PermissionDenied:
    #                 forbidden_threads.append(thread_id)
    #             else:
    #                 threads_to_delete.append(thread_id)
    #         except models.Thread.DoesNotExist:
    #             # Skip threads that don't exist
    #             pass

    #     if forbidden_threads and not threads_to_delete:
    #         # If all requested threads are forbidden, return 403
    #         return drf.response.Response(
    #             {"detail": "You don't have permission to delete these threads"},
    #             status=drf.status.HTTP_403_FORBIDDEN,
    #         )

    #     # Update thread_ids to only include those with proper permissions
    #     accessible_threads = self.get_queryset().filter(id__in=threads_to_delete)

    #     # Count before deletion
    #     count = accessible_threads.count()

    #     # Delete the threads
    #     accessible_threads.delete()

    #     return drf.response.Response(
    #         {
    #             "detail": f"Successfully deleted {count} threads",
    #             "deleted_count": count,
    #         }
    #     )
