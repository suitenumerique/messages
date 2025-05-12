"""API ViewSet for Thread model."""

from django.conf import settings
from django.db.models import Exists, OuterRef, Sum

import rest_framework as drf
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, viewsets

from core import enums, models
from core.search import search_threads

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
        """Restrict results to threads accessible by the current user."""
        user = self.request.user
        mailbox_id = self.request.GET.get("mailbox_id")

        # Base queryset: Threads the user has access to via ThreadAccess
        queryset = models.Thread.objects.filter(
            Exists(
                models.ThreadAccess.objects.filter(
                    mailbox__accesses__user=user, thread=OuterRef("pk")
                )
            )
        ).distinct()

        if mailbox_id:
            # Ensure the user actually has access to the specified mailbox_id itself
            try:
                mailbox = models.Mailbox.objects.get(id=mailbox_id, accesses__user=user)
                # Use the mailbox.threads_viewer property to get threads
                queryset = mailbox.threads_viewer
            except models.Mailbox.DoesNotExist as e:
                raise drf.exceptions.PermissionDenied(
                    "You do not have access to this mailbox context."
                ) from e

        # Apply boolean filters (has_unread, etc.)
        # These filters operate on the Thread model's aggregated fields
        filter_mapping = {
            "has_unread": "count_unread__gt",
            "has_trashed": "count_trashed__gt",
            "has_draft": "count_draft__gt",
            "has_starred": "count_starred__gt",
            "has_sender": "count_sender__gt",  # Assuming count_sender relates to messages from the user
        }

        for param, filter_lookup in filter_mapping.items():
            value = self.request.GET.get(param)
            if value is not None:
                if value == "1":
                    queryset = queryset.filter(**{filter_lookup: 0})
                else:
                    # Allow filtering for threads with zero count
                    queryset = queryset.filter(**{filter_lookup.replace("__gt", ""): 0})

        queryset = queryset.order_by("-messaged_at")
        return queryset

    @extend_schema(
        tags=["threads"],
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter threads by mailbox ID.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search threads by content (subject, sender, recipients, message body).",
            ),
            OpenApiParameter(
                name="has_unread",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with unread messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_trashed",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that are trashed (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_draft",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with draft messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_starred",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with starred messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_sender",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with messages sent by the user (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="stats_fields",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description=(
                    f"Comma-separated list of fields to aggregate. "
                    f"Allowed values: {', '.join(enums.THREAD_STATS_FIELDS_MAP.keys())}"
                ),
                enum=list(enums.THREAD_STATS_FIELDS_MAP.keys()),
                style="form",
                explode=False,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {
                        key: {"type": "integer"}
                        for key in enums.THREAD_STATS_FIELDS_MAP
                    },
                },
                description=(
                    "A dictionary containing the aggregated counts. "
                    "Keys correspond to the fields requested via the `stats_fields` query parameter. "
                    "All possible keys (derived from THREAD_STATS_FIELDS_MAP) are defined in the schema, "
                    "each mapping to an integer count. Keys not requested will not be present in the response."
                ),
            ),
            400: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                },
                description=(
                    f"Returned if `stats_fields` parameter is missing or contains invalid fields. "
                    f"Allowed fields: {', '.join(enums.THREAD_STATS_FIELDS_MAP.keys())}"
                ),
            ),
        },
        description="Get aggregated statistics for threads based on filters.",
    )
    @drf.decorators.action(
        detail=False,
        methods=["get"],
        url_path="stats",
        url_name="stats",
        permission_classes=[permissions.IsAuthenticated],
    )
    def stats(self, request):
        """Retrieve aggregated statistics for threads accessible by the user."""
        queryset = self.get_queryset()
        stats_fields_param = request.query_params.get("stats_fields", "")

        if not stats_fields_param:
            return drf.response.Response(
                {"detail": "Missing 'stats_fields' query parameter."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        requested_fields = [field.strip() for field in stats_fields_param.split(",")]

        aggregations = {}
        for field in requested_fields:
            model_field = enums.THREAD_STATS_FIELDS_MAP.get(field)
            if model_field:
                aggregations[field] = Sum(model_field)
            else:
                return drf.response.Response(
                    {"detail": f"Invalid field requested in stats_fields: {field}"},
                    status=drf.status.HTTP_400_BAD_REQUEST,
                )
        if not aggregations:
            # Should not happen if stats_fields_param is validated earlier, but good practice
            return drf.response.Response(
                {"detail": "No valid fields provided in stats_fields."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        aggregated_data = queryset.aggregate(**aggregations)

        # Replace None with 0 for sums where no matching threads exist
        for key, value in aggregated_data.items():
            if value is None:
                aggregated_data[key] = 0
        return drf.response.Response(aggregated_data)

    @extend_schema(
        tags=["threads"],
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter threads by mailbox ID.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search threads by content (subject, sender, recipients, message body).",
            ),
            OpenApiParameter(
                name="has_unread",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with unread messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_trashed",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that are trashed (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_draft",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with draft messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_starred",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with starred messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_sender",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with messages sent by the user (1=true, 0=false).",
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List threads with optional search functionality."""
        search_query = request.query_params.get("search", "").strip()

        # If search is provided and Elasticsearch is available, use it
        if search_query and hasattr(settings, "ELASTICSEARCH_HOSTS"):
            # Get the mailbox_id for filtering
            mailbox_id = request.query_params.get("mailbox_id")

            # Build filters from query parameters
            es_filters = {}
            for param, value in request.query_params.items():
                if param.startswith("has_") and value in ["0", "1"]:
                    field_name = param[4:]  # Remove 'has_' prefix
                    if value == "1":
                        es_filters[f"is_{field_name}"] = True
                    else:
                        es_filters[f"is_{field_name}"] = False

            # Get page parameters
            page = self.paginator.get_page_number(request, self)
            page_size = self.paginator.get_page_size(request)

            # Get search results from Elasticsearch
            results = search_threads(
                query=search_query,
                mailbox_ids=[mailbox_id] if mailbox_id else None,
                filters=es_filters,
                from_offset=(page - 1) * page_size,
                size=page_size,
            )

            ordered_threads = []
            if len(results["threads"]) > 0:
                # Get the thread IDs from the search results
                thread_ids = [thread["id"] for thread in results["threads"]]

                # Retrieve the actual thread objects from the database
                threads = models.Thread.objects.filter(id__in=thread_ids)

                # Order the threads in the same order as the search results
                thread_dict = {str(thread.id): thread for thread in threads}
                ordered_threads = [
                    thread_dict[thread_id]
                    for thread_id in thread_ids
                    if thread_id in thread_dict
                ]

            # Use the paginator to create a paginated response
            page = self.paginate_queryset(ordered_threads)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(ordered_threads, many=True)
            return drf.response.Response(serializer.data)

        # Fall back to regular DB query if no search query or Elasticsearch not available
        return super().list(request, *args, **kwargs)

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
