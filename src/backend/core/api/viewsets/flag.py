import uuid
from typing import Literal

from django.db import transaction
from django.utils import timezone

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
)
from rest_framework.views import APIView

from core import models

from .. import permissions

# Define allowed flag types
FlagType = Literal["unread", "starred", "trashed"]


class ChangeFlagViewSet(APIView):
    """ViewSet for changing flags on messages or threads."""

    permission_classes = [permissions.IsAllowedToAccessMailbox]
    action = "change_flag"

    @extend_schema(
        tags=["flags"],
        parameters=[
            OpenApiParameter(
                name="flag",
                type=str,
                enum=["unread", "starred", "trashed"],
                required=True,
                description="The flag to change.",
            ),
            OpenApiParameter(
                name="value",
                type=bool,
                required=True,
                description="The value to set the flag to (true/false).",
            ),
            OpenApiParameter(
                name="message_ids",
                type=str,
                required=False,
                description="Comma-separated list of message UUIDs (optional).",
            ),
            OpenApiParameter(
                name="thread_ids",
                type=str,
                required=False,
                description="Comma-separated list of thread UUIDs (optional).",
            ),
        ],
        request=None,  # Body is not used, parameters are in query
        responses={
            200: OpenApiExample(
                "Success Response",
                value={
                    "detail": "Successfully updated flag 'unread' for 5 messages and 2 threads",
                    "updated_messages": 5,
                    "updated_threads": 2,
                },
            ),
            400: OpenApiExample(
                "Validation Error",
                value={
                    "detail": "Flag parameter is required and must be one of: unread, starred, trashed."
                },
            ),
            403: OpenApiExample(
                "Permission Error",
                value={
                    "detail": "You don't have permission to modify some of these resources."
                },
            ),
        },
        description=(
            "Change a specific flag (unread, starred, trashed) for multiple messages "
            "or all messages within multiple threads."
        ),
    )
    def post(self, request, *args, **kwargs):
        """
        Change a specific flag (unread, starred, trashed) for messages or threads.

        Query Parameters:
        - flag: 'unread', 'starred', or 'trashed' (required)
        - value: 'true' or 'false' (required)
        - message_ids: comma-separated list of message UUIDs (optional)
        - thread_ids: comma-separated list of thread UUIDs (optional)

        At least one of message_ids or thread_ids must be provided.
        """
        flag: FlagType = request.data.get("flag")
        value_param = request.data.get("value")
        message_ids_str = request.data.get("message_ids", "")
        thread_ids_str = request.data.get("thread_ids", "")

        # Validate flag parameter
        allowed_flags = ["unread", "starred", "trashed"]
        if flag not in allowed_flags:
            return drf.response.Response(
                {
                    "detail": f"Flag parameter is required and must be one of: {', '.join(allowed_flags)}."
                },
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        # Validate value parameter
        if value_param is None or value_param.lower() not in ["true", "false"]:
            return drf.response.Response(
                {"detail": "Value parameter must be 'true' or 'false'."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )
        value = value_param.lower() == "true"

        # Validate IDs presence
        if not message_ids_str and not thread_ids_str:
            return drf.response.Response(
                {"detail": "Either message_ids or thread_ids must be provided"},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        # Parse IDs
        try:
            message_ids = (
                [
                    uuid.UUID(mid.strip())
                    for mid in message_ids_str.split(",")
                    if mid.strip()
                ]
                if message_ids_str
                else []
            )
            thread_ids = (
                [
                    uuid.UUID(tid.strip())
                    for tid in thread_ids_str.split(",")
                    if tid.strip()
                ]
                if thread_ids_str
                else []
            )
        except ValueError:
            return drf.response.Response(
                {"detail": "Invalid UUID format in message_ids or thread_ids."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        # Get accessible mailboxes
        accessible_mailbox_ids = self.request.user.mailbox_accesses.values_list(
            "mailbox_id", flat=True
        )

        updated_threads = set()
        current_time = timezone.now()

        # Use a transaction to ensure atomicity
        with transaction.atomic():
            # --- Process direct message IDs ---
            if message_ids:
                messages_to_update = models.Message.objects.select_related(
                    "thread"
                ).filter(
                    thread__mailbox__id__in=accessible_mailbox_ids, id__in=message_ids
                )

                batch_update_data = {"updated_at": current_time}
                if flag == "unread":
                    batch_update_data["is_unread"] = value
                    batch_update_data["read_at"] = None if value else current_time
                elif flag == "starred":
                    batch_update_data["is_starred"] = value
                elif flag == "trashed":
                    batch_update_data["is_trashed"] = value
                    batch_update_data["trashed_at"] = current_time if value else None

                messages_to_update.update(**batch_update_data)
                updated_threads.update(msg.thread for msg in messages_to_update)

            # --- Process thread IDs (update all messages within) ---
            if thread_ids:
                # Get all threads the user has access to that match the IDs
                accessible_threads = models.Thread.objects.filter(
                    mailbox__id__in=accessible_mailbox_ids, id__in=thread_ids
                )

                # Get all message IDs within these accessible threads
                messages_in_threads_qs = models.Message.objects.filter(
                    thread__in=accessible_threads
                )

                batch_update_data = {"updated_at": current_time}
                if flag == "unread":
                    batch_update_data["is_unread"] = value
                    batch_update_data["read_at"] = None if value else current_time
                elif flag == "starred":
                    batch_update_data["is_starred"] = value
                elif flag == "trashed":
                    batch_update_data["is_trashed"] = value
                    batch_update_data["trashed_at"] = current_time if value else None

                # Apply the update to messages within the selected threads
                messages_in_threads_qs.update(**batch_update_data)

                # Add affected threads to the set for counter update
                updated_threads.update(accessible_threads)

            # --- Update thread counters ---
            for thread in updated_threads:
                # Refresh thread from DB within transaction if needed, though update_counters handles it
                thread.update_counters(counters=[flag])

        # Simplified response message
        response_detail = (
            f"Successfully updated flag '{flag}' to {value} for "
            f"{len(updated_threads)} threads."
        )
        # Adjust detail message if only messages were specified (meaning 1 thread was affected implicitly)
        if message_ids_str and not thread_ids_str and len(updated_threads) == 1:
            response_detail = f"Successfully updated flag '{flag}' to {value} for messages in 1 thread."
        elif message_ids_str and not thread_ids_str:
            # Handle cases where multiple threads might be affected by individual message updates
            response_detail = f"Successfully updated flag '{flag}' to {value} for messages across {len(updated_threads)} threads."
        elif thread_ids_str and not message_ids_str:
            response_detail = f"Successfully updated flag '{flag}' to {value} for {len(updated_threads)} threads."
        # Default message handles the combined case reasonably

        return drf.response.Response(
            {
                "detail": response_detail,
                "updated_threads": len(updated_threads),
            }
        )
