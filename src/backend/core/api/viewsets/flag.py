"""API ViewSet for changing flags on messages or threads."""

from django.db import transaction
from django.utils import timezone

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework.views import APIView

from core import models

from .. import permissions

# Define allowed flag types
ALLOWED_FLAGS = ["unread", "starred", "trashed"]


class ChangeFlagViewSet(APIView):
    """ViewSet for changing flags on messages or threads."""

    permission_classes = [permissions.IsAllowedToAccessMailbox]
    action = "change_flag"

    @extend_schema(
        tags=["flags"],
        request=inline_serializer(
            name="ChangeFlagRequest",
            fields={
                "flag": drf_serializers.ChoiceField(
                    choices=ALLOWED_FLAGS, allow_blank=False
                ),
                "value": drf_serializers.BooleanField(required=True),
                "message_ids": drf_serializers.ListField(
                    child=drf_serializers.UUIDField(),
                    required=False,
                    allow_empty=True,
                    help_text="List of message UUIDs to apply the flag change to.",
                ),
                "thread_ids": drf_serializers.ListField(
                    child=drf_serializers.UUIDField(),
                    required=False,
                    allow_empty=True,
                    help_text="List of thread UUIDs where all messages should have the flag change applied.",
                ),
            },
        ),
        responses={
            200: OpenApiExample(
                "Success Response",
                value={
                    "success": True,
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
            "or all messages within multiple threads. Uses request body."
        ),
        examples=[
            OpenApiExample(
                "Mark messages as read",
                value={
                    "flag": "unread",
                    "value": False,
                    "message_ids": [
                        "123e4567-e89b-12d3-a456-426614174001",
                        "123e4567-e89b-12d3-a456-426614174002",
                    ],
                },
            ),
            OpenApiExample(
                "Trash threads",
                value={
                    "flag": "trashed",
                    "value": True,
                    "thread_ids": [
                        "a1b2c3d4-e5f6-7890-1234-567890abcdef",
                        "b2c3d4e5-f6a7-8901-2345-67890abcdef0",
                    ],
                },
            ),
            OpenApiExample(
                "Star messages and threads",
                value={
                    "flag": "starred",
                    "value": True,
                    "message_ids": ["123e4567-e89b-12d3-a456-426614174005"],
                    "thread_ids": ["a1b2c3d4-e5f6-7890-1234-567890abcdef"],
                },
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        """
        Change a specific flag (unread, starred, trashed) for messages or threads.
        Expects data in the request body, not query parameters.

        Request Body Parameters:
        - flag: 'unread', 'starred', or 'trashed' (required, string)
        - value: true or false (required, boolean)
        - message_ids: list of message UUID strings (optional, list[string])
        - thread_ids: list of thread UUID strings (optional, list[string])

        At least one of message_ids or thread_ids must be provided and contain items.
        """
        flag = request.data.get("flag")
        value = request.data.get("value")  # Should be boolean from parser
        message_ids = request.data.get("message_ids", [])
        thread_ids = request.data.get("thread_ids", [])

        # Validate input parameters
        if (
            (flag not in ALLOWED_FLAGS)
            or (value is None)
            or (not message_ids and not thread_ids)
        ):
            return drf.response.Response(
                {"detail": "Missing parameters"},
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
                thread.update_counters(
                    **{"counters": [flag]} if flag in ("unread", "starred") else {}
                )

        return drf.response.Response(
            {
                "success": True,
                "updated_threads": len(updated_threads),
            }
        )
