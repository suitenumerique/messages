"""API ViewSet for Thread model."""

import logging

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q

import rest_framework as drf
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, viewsets, status
from rest_framework.response import Response

from core import enums, models
from core.ai.thread_summarizer import summarize_thread
from core.search import search_threads

from .. import permissions, serializers

logger = logging.getLogger(__name__)


class ThreadViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Thread model."""

    serializer_class = serializers.ThreadSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "pk"
    lookup_url_kwarg = "pk"

    def get_queryset(self):
        """Restrict results to threads accessible by the current user."""
        user = self.request.user
        mailbox_id = self.request.GET.get("mailbox_id")
        label_slug = self.request.GET.get("label_slug")

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
                    "You do not have access to this mailbox."
                ) from e

        if label_slug:
            # Filter threads by label slug, ensuring user has access to the label's mailbox
            # Get labels accessible to the user, joining with mailbox access
            labels = models.Label.objects.filter(
                slug=label_slug,
                mailbox__accesses__user=user,
            )
            if mailbox_id:
                # Further filter by mailbox if specified
                labels = labels.filter(mailbox__id=mailbox_id)

            # Filter threads that have any of these labels
            queryset = queryset.filter(labels__in=labels)

        # Apply boolean filters
        # These filters operate on the Thread model's boolean fields
        filter_mapping = {
            "has_trashed": "has_trashed",
            "has_draft": "has_draft",
            "has_starred": "has_starred",
            "has_sender": "has_sender",
            "has_active": "has_active",
            "has_messages": "has_messages",
            "has_attachments": "has_attachments",
            "is_spam": "is_spam",
        }

        for param, filter_field in filter_mapping.items():
            value = self.request.GET.get(param)
            if value is not None:
                if value == "1":
                    queryset = queryset.filter(**{filter_field: True})
                else:
                    queryset = queryset.filter(**{filter_field: False})

        queryset = queryset.order_by("-messaged_at", "-created_at")
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
                name="label_slug",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter threads by label slug.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search threads by content (subject, sender, recipients, message body).",
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
                name="has_attachments",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with attachments (1=true, 0=false).",
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
                description="""Comma-separated list of fields to aggregate.
                Special values: 'all' (count all threads), 'all_unread' (count all unread threads).
                Boolean fields: has_trashed, has_draft, has_starred, has_attachments, has_sender, has_active, is_spam, has_messages.
                Unread variants ('_unread' suffix): count threads where the condition is true AND the thread is unread.
                Examples: 'all,all_unread', 'has_starred,has_starred_unread', 'is_spam,is_spam_unread'""",
                enum=list(enums.THREAD_STATS_FIELDS_MAP.keys()),
                style="form",
                explode=False,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response={
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                description=(
                    "A dictionary containing the aggregated counts. "
                    "Keys correspond to the fields requested via the `stats_fields` query parameter. "
                    "Each value is an integer count. Keys not requested will not be present in the response."
                ),
            ),
            400: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                },
                description=(
                    "Returned if `stats_fields` parameter is missing or contains invalid fields."
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

        # Define valid base fields that can be counted
        valid_base_fields = {
            "has_trashed",
            "has_draft",
            "has_starred",
            "has_attachments",
            "has_sender",
            "has_active",
            "is_spam",
            "has_messages",
        }

        # Special fields
        special_fields = {"all", "all_unread"}

        # Validate requested fields
        for field in requested_fields:
            if field in special_fields:
                continue
            if field.endswith("_unread"):
                # Extract base field name and validate
                base_field = field[:-7]  # Remove "_unread" suffix
                if base_field not in valid_base_fields:
                    return drf.response.Response(
                        {"detail": f"Invalid base field in '{field}': {base_field}"},
                        status=drf.status.HTTP_400_BAD_REQUEST,
                    )
            elif field in valid_base_fields:
                continue
            else:
                return drf.response.Response(
                    {"detail": f"Invalid field requested in stats_fields: {field}"},
                    status=drf.status.HTTP_400_BAD_REQUEST,
                )

        aggregations = {}
        for field in requested_fields:
            # Use a unique key for the aggregation to avoid naming conflicts
            agg_key = f"count_{field}"

            if field == "all":
                # Count all threads matching the filter
                aggregations[agg_key] = Count("pk")
            elif field == "all_unread":
                # Count all unread threads matching the filter
                aggregations[agg_key] = Count("pk", filter=Q(has_unread=True))
            elif field.endswith("_unread"):
                # Count threads that match the condition AND are unread
                base_field = field[:-7]  # Remove "_unread" suffix
                base_condition = Q(**{base_field: True})
                unread_condition = Q(has_unread=True)
                aggregations[agg_key] = Count(
                    "pk", filter=base_condition & unread_condition
                )
            else:
                # Count threads where the boolean field is True
                aggregations[agg_key] = Count("pk", filter=Q(**{field: True}))

        if not aggregations:
            return drf.response.Response(
                {"detail": "No valid fields provided in stats_fields."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        aggregated_data = queryset.aggregate(**aggregations)

        # Map back to the original field names and replace None with 0
        result = {}
        for field in requested_fields:
            agg_key = f"count_{field}"
            value = aggregated_data.get(agg_key, 0)
            result[field] = value if value is not None else 0

        return drf.response.Response(result)

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
                name="label_slug",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter threads by label slug.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search threads by content (subject, sender, recipients, message body).",
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
                name="has_attachments",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with attachments (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_sender",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with messages sent by the user (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_active",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that have active messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_messages",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that have messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="is_spam",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that are spam (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="message_ids",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Comma-separated list of message IDs to filter threads by specific messages (used by AI search).",
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List threads with optional search functionality."""
        search_query = request.query_params.get("search", "").strip()
        message_ids = request.query_params.get("message_ids", "").strip()
        
        # Debug: Log what path we're taking
        print(f"DEBUG THREAD API START: search_query='{search_query}', message_ids='{message_ids}'")
        
        # Check if we have specific message IDs from deep search (without search query)
        if message_ids and not search_query:
            print(f"DEBUG THREAD API: Taking message_ids ONLY path (no search query)")
            mailbox_id = request.query_params.get("mailbox_id")
            
            # Parse message IDs from comma-separated string
            try:
                message_id_list = [mid.strip() for mid in message_ids.split(",") if mid.strip()]
                print(f"DEBUG: Parsed message_id_list: {message_id_list}")
                print(f"DEBUG: Number of message IDs: {len(message_id_list)}")
                
                # Check if this is the special "no results" UUID from contextual search
                if len(message_id_list) == 1 and message_id_list[0] == "00000000-0000-0000-0000-000000000000":
                    print("DEBUG: Detected contextual search empty results UUID - showing no emails as intended")
                    # Return empty result
                    page = self.paginate_queryset([])
                    if page is not None:
                        serializer = self.get_serializer(page, many=True)
                        return self.get_paginated_response(serializer.data)
                    return drf.response.Response([])
                
                # Get threads that contain these messages
                threads_with_messages = models.Thread.objects.filter(
                    messages__id__in=message_id_list
                ).distinct()
                
                print(f"DEBUG: Found {threads_with_messages.count()} threads with these messages")
                
                # Apply additional filters from query parameters (mailbox, etc.)
                queryset = self.get_queryset().filter(id__in=threads_with_messages)
                
                print(f"DEBUG: After applying additional filters: {queryset.count()} threads")
                
                # Use the paginator to create a paginated response
                page = self.paginate_queryset(queryset)
                if page is not None:
                    serializer = self.get_serializer(page, many=True)
                    print(f"DEBUG: Returning paginated response with {len(page)} threads")
                    return self.get_paginated_response(serializer.data)

                serializer = self.get_serializer(queryset, many=True)
                print(f"DEBUG: Returning non-paginated response with {queryset.count()} threads")
                return drf.response.Response(serializer.data)
                
            except Exception as e:
                logger.error(f"Error processing message_ids: {e}")
                print(f"DEBUG: Exception in message_ids processing: {e}")
                # Fall back to regular search if message_ids processing fails

        # If search is provided and OpenSearch is available, use it
        if search_query and len(settings.OPENSEARCH_HOSTS[0]) > 0:
            # Get the mailbox_id for filtering
            mailbox_id = request.query_params.get("mailbox_id")
            message_ids = request.query_params.get("message_ids", "").strip()

            # Debug: Print all received parameters
            print(f"DEBUG THREAD API: search_query='{search_query}', message_ids='{message_ids}'")
            print(f"DEBUG THREAD API: All query params: {dict(request.query_params)}")
            print(f"DEBUG THREAD API: Taking search+message_ids path")

            # Debug: Print the received message_ids parameter
            if message_ids:
                print(f"DEBUG: Received message_ids parameter: '{message_ids}'")

            # Check if we have specific message IDs from deep search (RAG)
            if message_ids:
                # Parse message IDs from comma-separated string
                try:
                    message_id_list = [mid.strip() for mid in message_ids.split(",") if mid.strip()]
                    print(f"DEBUG: Parsed message_id_list: {message_id_list}")
                    print(f"DEBUG: Number of message IDs: {len(message_id_list)}")
                    
                    # Check if this is the special "no results" UUID from contextual search
                    if len(message_id_list) == 1 and message_id_list[0] == "00000000-0000-0000-0000-000000000000":
                        print("DEBUG: Detected contextual search empty results UUID - showing no emails as intended")
                    
                    # Get threads that contain these messages
                    threads_with_messages = models.Thread.objects.filter(
                        messages__id__in=message_id_list
                    ).distinct()
                    
                    print(f"DEBUG: Found {threads_with_messages.count()} threads with these messages")
                    
                    # Apply additional filters from query parameters (mailbox, etc.)
                    queryset = self.get_queryset().filter(id__in=threads_with_messages)
                    
                    print(f"DEBUG: After applying additional filters: {queryset.count()} threads")
                    
                    # Use the paginator to create a paginated response
                    page = self.paginate_queryset(queryset)
                    if page is not None:
                        serializer = self.get_serializer(page, many=True)
                        print(f"DEBUG: Returning paginated response with {len(page)} threads")
                        return self.get_paginated_response(serializer.data)

                    serializer = self.get_serializer(queryset, many=True)
                    print(f"DEBUG: Returning non-paginated response with {queryset.count()} threads")
                    return drf.response.Response(serializer.data)
                    
                except Exception as e:
                    logger.error(f"Error processing message_ids: {e}")
                    print(f"DEBUG: Exception in message_ids processing: {e}")
                    # Fall back to regular search if message_ids processing fails

            # Build filters from query parameters
            # TODO: refactor as thread filters are not the same as message filters (has_messages, has_active)
            es_filters = {}
            for param, value in request.query_params.items():
                if param.startswith("has_") and value in {"0", "1"}:
                    # Remove 'has_' prefix
                    es_filters[f"is_{param[4:]}"] = value == "1"
                elif param.startswith("is_") and value in {"0", "1"}:
                    es_filters[param] = value == "1"

            # Get page parameters
            page = int(self.paginator.get_page_number(request, self))
            page_size = int(self.paginator.get_page_size(request))

            # Get search results from OpenSearch
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

        # Fall back to regular DB query if no search query or OpenSearch not available
        print(f"DEBUG THREAD API: Taking regular DB query path (no search or no OpenSearch)")
        return super().list(request, *args, **kwargs)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                    },
                },
                description="Thread summary retrieved successfully.",
            ),
            403: OpenApiResponse(
                response={"detail": "Permission denied"},
                description="User does not have permission to access this thread.",
            ),
        },
        tags=["threads"],
    )
    @drf.decorators.action(detail=True, methods=["get"], url_path="summary")
    def get_summary(self, request, pk):  # pylint: disable=unused-argument
        """Retrieve the summary of a thread."""
        thread = self.get_object()
        return drf.response.Response({"summary": thread.summary})

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                },
                description="Summary successfully refreshed.",
            ),
            403: OpenApiResponse(
                response={"detail": "Permission denied"},
                description="User does not have permission to refresh the summary of this thread.",
            ),
        },
        tags=["threads"],
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="refresh-summary",
        url_name="refresh-summary",
    )
    def refresh_summary(self, request, pk):  # pylint: disable=unused-argument
        """Refresh the summary of a thread."""
        thread = self.get_object()
        thread.summary = summarize_thread(thread)
        thread.save()
        return drf.response.Response(
            {"summary": thread.summary}, status=status.HTTP_200_OK
        )

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
