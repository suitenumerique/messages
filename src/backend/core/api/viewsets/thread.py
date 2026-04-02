"""API ViewSet for Thread model."""

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Coalesce

import rest_framework as drf
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers

from core import enums, models
from core.ai.thread_summarizer import summarize_thread
from core.services.search import search_threads
from core.utils import extract_snippet

from .. import permissions, serializers


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

    def get_permissions(self):
        """Use HasThreadEditAccess for actions that require EDITOR role."""
        if self.action in ("destroy", "split"):
            return [permissions.HasThreadEditAccess()]
        return super().get_permissions()

    def get_serializer_context(self):
        """Add mailbox_id to serializer context for scoped serialization."""
        context = super().get_serializer_context()
        context["mailbox_id"] = self.request.GET.get("mailbox_id")
        return context

    def get_queryset(self, exclude_spam: bool = True, exclude_trashed: bool = True):
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

        queryset = queryset.annotate(
            _has_unread=models.ThreadAccess.thread_unread_filter(user, mailbox_id),
            _has_starred=models.ThreadAccess.thread_starred_filter(user, mailbox_id),
        )

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
            "has_unread": "_has_unread",
            "has_trashed": "has_trashed",
            "has_archived": "has_archived",
            "has_draft": "has_draft",
            "has_starred": "_has_starred",
            "has_sender": "has_sender",
            "has_active": "has_active",
            "has_messages": "has_messages",
            "has_attachments": "has_attachments",
            "has_delivery_pending": "has_delivery_pending",
            "is_trashed": "is_trashed",
            "is_spam": "is_spam",
        }

        query_params = self.request.GET
        for param, filter_field in filter_mapping.items():
            # Exclude fully trashed threads by default
            if exclude_trashed and param == "is_trashed":
                value = query_params.get(
                    param, None if query_params.get("has_trashed") == "1" else "0"
                )
            # Exclude spam by default except if we are looking for trashed threads
            elif exclude_spam and param == "is_spam":
                value = query_params.get(
                    param, None if query_params.get("has_trashed") == "1" else "0"
                )
            else:
                value = query_params.get(param)

            if value is not None and filter_field is not None:
                if value == "1":
                    queryset = queryset.filter(**{filter_field: True})
                else:
                    queryset = queryset.filter(**{filter_field: False})

        order_expression = self._get_order_expression(query_params)
        queryset = queryset.order_by(order_expression, "-created_at")
        return queryset

    @staticmethod
    def _get_order_expression(query_params):
        """Return the ordering expression based on the active view filter."""
        view_field_map = {
            "has_trashed": "trashed_messaged_at",
            "has_draft": "draft_messaged_at",
            "has_sender": "sender_messaged_at",
            "has_archived": "archived_messaged_at",
            "has_active": "active_messaged_at",
            "has_starred": "active_messaged_at",
        }
        for param, field in view_field_map.items():
            if query_params.get(param) == "1":
                return f"-{field}"

        # Draft-only threads have messaged_at=NULL, fall back to draft_messaged_at
        return Coalesce("messaged_at", "draft_messaged_at").desc()

    def destroy(self, request, *args, **kwargs):
        """Delete a thread, requiring EDITOR role on the thread."""
        thread = self.get_object()
        thread.delete()
        return drf.response.Response(status=status.HTTP_204_NO_CONTENT)

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
                name="has_archived",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that are archived (1=true, 0=false).",
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
                name="has_delivery_pending",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description=(
                    "Filter threads with delivery pending messages: sending, retry or failed (1=true, 0=false)."
                ),
            ),
            OpenApiParameter(
                name="stats_fields",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="""Comma-separated list of fields to aggregate.
                Special values: 'all' (count all threads), 'all_unread' (count all unread threads).
                Boolean fields: has_trashed, has_draft, has_starred, has_attachments, has_archived,
                has_sender, has_active, has_delivery_pending, has_delivery_failed, is_spam, has_messages.
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
        queryset = self.get_queryset(exclude_spam=False, exclude_trashed=False)
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
            "has_archived",
            "has_draft",
            "has_starred",
            "has_attachments",
            "has_sender",
            "has_active",
            "has_delivery_failed",
            "has_delivery_pending",
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

        # Build unread/starred conditions from annotations (always available)
        unread_condition = Q(_has_unread=True)
        starred_condition = Q(_has_starred=True)

        aggregations = {}
        for field in requested_fields:
            agg_key = f"count_{field}"

            if field == "all":
                aggregations[agg_key] = Count("pk")
            elif field == "all_unread":
                aggregations[agg_key] = Count("pk", filter=unread_condition)
            elif field == "has_starred":
                aggregations[agg_key] = Count("pk", filter=starred_condition)
            elif field == "has_starred_unread":
                aggregations[agg_key] = Count(
                    "pk", filter=starred_condition & unread_condition
                )
            elif field.endswith("_unread"):
                base_field = field[:-7]
                base_condition = Q(**{base_field: True})
                aggregations[agg_key] = Count(
                    "pk", filter=base_condition & unread_condition
                )
            else:
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
                description="Filter threads that have trashed messages (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="is_trashed",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that have all messages trashed (1=true, 0=false).",
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
                name="has_archived",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that have archived (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="is_spam",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads that are spam (1=true, 0=false).",
            ),
            OpenApiParameter(
                name="has_delivery_pending",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description=(
                    "Filter threads that have delivery pending messages: sending, retry or failed (1=true, 0=false)."
                ),
            ),
            OpenApiParameter(
                name="has_unread",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter threads with unread messages (1=true, 0=false). Requires mailbox_id.",
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List threads with optional search functionality."""
        search_query = request.query_params.get("search", "").strip()
        mailbox_id = request.query_params.get("mailbox_id")

        if mailbox_id:
            mailbox_access = models.MailboxAccess.objects.filter(
                mailbox=mailbox_id, user=request.user
            ).first()
            if mailbox_access:
                mailbox_access.mark_accessed()
            else:
                raise drf.exceptions.PermissionDenied(
                    "You do not have access to this mailbox."
                )

        # If search is provided and OpenSearch is available, use it
        if search_query and len(settings.OPENSEARCH_HOSTS[0]) > 0:
            # Get the mailbox_id for filtering

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

            # Retrieve and order threads from database
            ordered_threads = []
            if len(results["threads"]) > 0:
                # Get the thread IDs from the search results
                thread_ids = [thread["id"] for thread in results["threads"]]

                # Filter by access control: only return threads the user
                # can access via ThreadAccess. We don't use get_queryset()
                # because it applies extra filters (trashed, spam, labels,
                # booleans) that OpenSearch already handles.
                threads = models.Thread.objects.filter(
                    id__in=thread_ids,
                ).filter(
                    Exists(
                        models.ThreadAccess.objects.filter(
                            mailbox__accesses__user=request.user,
                            thread=OuterRef("pk"),
                        )
                    ),
                )
                threads = threads.annotate(
                    _has_unread=models.ThreadAccess.thread_unread_filter(
                        request.user, mailbox_id
                    ),
                    _has_starred=models.ThreadAccess.thread_starred_filter(
                        request.user, mailbox_id
                    ),
                )

                # Order the threads in the same order as the search results
                thread_dict = {str(thread.id): thread for thread in threads}
                ordered_threads = [
                    thread_dict[thread_id]
                    for thread_id in thread_ids
                    if thread_id in thread_dict
                ]

            # Return a response with minimal pagination info
            # (only page numbers for next/previous, not full URLs)
            # OpenSearch has already handled pagination, so we can't use the default DRF paginator.
            # The frontend only needs: total count, and non-null next/previous values
            # to determine if there are more pages available.
            serializer = self.get_serializer(ordered_threads, many=True)
            total_count = results.get("total", 0)
            return drf.response.Response(
                {
                    "count": total_count,
                    "next": page + 1 if (page * page_size) < total_count else None,
                    "previous": page - 1 if page > 1 else None,
                    "results": serializer.data,
                }
            )
        # Fall back to regular DB query if no search query or OpenSearch not available

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

    @extend_schema(
        tags=["threads"],
        request=inline_serializer(
            name="ThreadSplitRequest",
            fields={
                "message_id": drf_serializers.UUIDField(
                    required=True,
                    help_text="ID of the message to split from. This message and all "
                    "chronologically later messages will be moved to a new thread.",
                ),
            },
        ),
        responses={
            201: serializers.ThreadSerializer,
            400: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                },
                description="Validation error.",
            ),
            403: OpenApiResponse(
                response={"detail": "Permission denied"},
                description="User does not have editor permission on this thread.",
            ),
        },
        description="Split a thread by moving the specified message and all later "
        "messages to a new thread.",
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="split",
        url_name="split",
    )
    def split(self, request, pk=None):  # pylint: disable=unused-argument
        """Split a thread at the given message, moving it and all later messages to a new thread."""
        old_thread = self.get_object()

        # Validate request
        message_id = request.data.get("message_id")
        if not message_id:
            return drf.response.Response(
                {"detail": "message_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            split_message = models.Message.objects.get(id=message_id)
        except models.Message.DoesNotExist:
            return drf.response.Response(
                {"detail": "Message not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if split_message.thread_id != old_thread.id:
            return drf.response.Response(
                {"detail": "Message does not belong to this thread."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if split_message.is_draft:
            return drf.response.Response(
                {"detail": "Cannot split at a draft message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get all messages in the thread ordered chronologically
        all_messages = old_thread.messages.order_by("created_at")
        total_count = all_messages.count()

        if total_count <= 1:
            return drf.response.Response(
                {"detail": "Cannot split a thread with only one message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        first_message = all_messages.first()
        if first_message.id == split_message.id:
            return drf.response.Response(
                {"detail": "Cannot split at the first message in the thread."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Determine messages to move (split_message and all later)
        messages_to_move = all_messages.filter(created_at__gte=split_message.created_at)

        with transaction.atomic():
            new_subject = split_message.subject or old_thread.subject
            snippet = extract_snippet(
                split_message.get_parsed_data(),
                fallback=new_subject or "",
            )

            # Create new thread
            new_thread = models.Thread.objects.create(
                subject=new_subject,
                snippet=snippet,
            )

            # Copy ThreadAccess entries
            old_accesses = models.ThreadAccess.objects.filter(thread=old_thread)
            new_accesses = [
                models.ThreadAccess(
                    thread=new_thread,
                    mailbox=access.mailbox,
                    role=access.role,
                    read_at=access.read_at,
                    starred_at=access.starred_at
                    if access.starred_at
                    and access.starred_at >= split_message.created_at
                    else None,
                )
                for access in old_accesses
            ]
            models.ThreadAccess.objects.bulk_create(new_accesses, ignore_conflicts=True)

            # Copy labels
            for label in old_thread.labels.all():
                label.threads.add(new_thread)

            # Move messages
            messages_to_move.update(thread=new_thread)

            # Fix cross-thread parent references
            models.Message.objects.filter(
                thread=new_thread, parent__thread=old_thread
            ).update(parent=None)

            # Recalculate old thread snippet from its most recent remaining message
            last_remaining = old_thread.messages.order_by("-created_at").first()
            if last_remaining:
                old_thread.snippet = extract_snippet(
                    last_remaining.get_parsed_data(),
                    fallback=old_thread.subject or "",
                )
                old_thread.save(update_fields=["snippet"])

            # Update stats on both threads
            old_thread.update_stats()
            new_thread.update_stats()

            # Invalidate summaries
            models.Thread.objects.filter(id__in=[old_thread.id, new_thread.id]).update(
                summary=None
            )

        serializer = serializers.ThreadSerializer(
            new_thread, context={"request": request}
        )
        return drf.response.Response(serializer.data, status=status.HTTP_201_CREATED)

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
