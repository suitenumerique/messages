"""API ViewSet for changing flags on messages or threads."""

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework.views import APIView

from core import enums, models
from core.services.search.tasks import update_threads_mailbox_flags_task

from .. import permissions

# Define allowed flag types
ALLOWED_FLAGS = ["unread", "starred", "trashed", "archived", "spam"]


class ChangeFlagView(APIView):
    """ViewSet for changing flags on messages or threads."""

    permission_classes = [permissions.IsAllowedToAccess]
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
                "mailbox_id": drf_serializers.UUIDField(
                    required=False,
                    help_text="Mailbox UUID. Required when flag is 'unread' or 'starred'.",
                ),
                "read_at": drf_serializers.DateTimeField(
                    required=False,
                    allow_null=True,
                    help_text=(
                        "Timestamp up to which messages are considered read. "
                        "When provided with flag='unread', sets ThreadAccess.read_at directly. "
                        "null means nothing has been read (all messages unread)."
                    ),
                ),
                "starred_at": drf_serializers.DateTimeField(
                    required=False,
                    allow_null=True,
                    help_text=(
                        "Timestamp when the thread was starred. "
                        "When provided with flag='starred' and value=true, sets ThreadAccess.starred_at. "
                        "null or value=false removes the starred flag."
                    ),
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
            "Change a specific flag (unread, starred, trashed, archived, spam) for multiple messages "
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
                "Archive threads",
                value={
                    "flag": "archived",
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
        Change a specific flag (unread, starred, trashed, archived, spam) for messages or threads.

        Request Body Parameters:
        - flag: 'unread', 'starred', 'trashed', 'archived', or 'spam' (required)
        - value: true or false (required)
        - message_ids: list of message UUID strings (optional)
        - thread_ids: list of thread UUID strings (optional)
        - mailbox_id: mailbox UUID (required for 'unread' flag)

        At least one of message_ids or thread_ids must be provided.
        """
        flag = request.data.get("flag")
        value = request.data.get("value")
        message_ids = request.data.get("message_ids", [])
        thread_ids = request.data.get("thread_ids", [])
        mailbox_id = request.data.get("mailbox_id")

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

        if flag in ("unread", "starred") and not mailbox_id:
            return drf.response.Response(
                {"detail": f"mailbox_id is required for the '{flag}' flag."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        if flag == "unread" and "read_at" not in request.data:
            return drf.response.Response(
                {"detail": f"read_at is required for the '{flag}' flag."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        current_time = timezone.now()
        updated_threads = set()

        # Get IDs of threads the user has access to
        accessible_thread_ids_qs = models.ThreadAccess.objects.filter(
            mailbox__accesses__user=request.user,
        ).values_list("thread_id", flat=True)

        # Unread and starred are personal actions that don't require EDITOR access.
        if flag not in ("unread", "starred"):
            accessible_thread_ids_qs = accessible_thread_ids_qs.filter(
                role__in=enums.THREAD_ROLES_CAN_EDIT
            )

        if mailbox_id:
            accessible_thread_ids_qs = accessible_thread_ids_qs.filter(
                mailbox_id=mailbox_id
            )

        if flag in ("unread", "starred") and not thread_ids and message_ids:
            # If no thread_ids but we have message_ids, we need to get the thread_ids from the messages
            thread_ids = (
                models.Message.objects.filter(
                    id__in=message_ids,
                    thread_id__in=accessible_thread_ids_qs,
                )
                .values_list("thread_id", flat=True)
                .distinct()
            )

        with transaction.atomic():
            if flag == "unread":
                return self._handle_unread_flag(
                    request,
                    thread_ids,
                    mailbox_id,
                    accessible_thread_ids_qs,
                )

            if flag == "starred":
                return self._handle_starred_flag(
                    request,
                    thread_ids,
                    mailbox_id,
                    accessible_thread_ids_qs,
                )

            # --- Non-unread/starred flags: update Message fields as before ---
            if message_ids:
                messages_to_update = models.Message.objects.select_related(
                    "thread"
                ).filter(
                    id__in=message_ids,
                    thread_id__in=accessible_thread_ids_qs,
                )

                if messages_to_update.exists():
                    batch_update_data = {"updated_at": current_time}
                    if flag == "trashed":
                        batch_update_data["is_trashed"] = value
                        batch_update_data["trashed_at"] = (
                            current_time if value else None
                        )
                    elif flag == "archived":
                        batch_update_data["is_archived"] = value
                        batch_update_data["archived_at"] = (
                            current_time if value else None
                        )
                    elif flag == "spam":
                        batch_update_data["is_spam"] = value

                    messages_to_update.update(**batch_update_data)

                    # Cascade to draft children so restoring a message
                    # also restores its draft reply.
                    if flag in ("trashed", "archived", "spam"):
                        models.Message.objects.filter(
                            parent_id__in=message_ids,
                            is_draft=True,
                        ).update(**batch_update_data)

                    # Collect threads affected by direct message updates
                    updated_threads.update(
                        msg.thread for msg in messages_to_update
                    )  # In-memory objects ok here

            # --- Process thread IDs ---
            if thread_ids:
                # Filter threads by ID AND ensure they are accessible
                threads_to_process = models.Thread.objects.filter(
                    id__in=thread_ids,
                ).filter(
                    id__in=accessible_thread_ids_qs,  # Check access via subquery
                )

                if threads_to_process.exists():
                    # Find all messages within these accessible threads
                    messages_in_threads_qs = models.Message.objects.filter(
                        thread__in=threads_to_process
                    )

                    # Prepare update data for messages within these threads
                    batch_update_data = {"updated_at": current_time}
                    if flag == "trashed":
                        batch_update_data["is_trashed"] = value
                        batch_update_data["trashed_at"] = (
                            current_time if value else None
                        )
                    elif flag == "archived":
                        batch_update_data["is_archived"] = value
                        batch_update_data["archived_at"] = (
                            current_time if value else None
                        )
                    elif flag == "spam":
                        batch_update_data["is_spam"] = value
                    # Note: Trashing or Archiving a thread might have other side effects (e.g., updating thread state)
                    # This current logic only updates the is_trashed or is_archived flag on messages within.
                    # If Thread model itself has state, update threads_to_process separately.

                    # Apply the update to messages within the selected threads
                    messages_in_threads_qs.update(**batch_update_data)

                    # Add affected threads to the set for counter update
                    updated_threads.update(
                        threads_to_process
                    )  # Add the QuerySet directly

            # --- Update thread counters ---
            # Fetch threads from DB again to ensure consistency within transaction
            threads_to_update_stats = models.Thread.objects.filter(
                pk__in=[t.pk for t in updated_threads]
            )
            for thread in threads_to_update_stats:
                thread.update_stats()

        return drf.response.Response(
            {
                "success": True,
                "updated_threads": len(updated_threads),
            }
        )

    def _handle_unread_flag(
        self,
        request,
        thread_ids,
        mailbox_id,
        accessible_thread_ids_qs,
    ):
        """Handle the 'unread' flag by setting ThreadAccess.read_at directly.

        The caller sends the exact read_at timestamp:
        - read_at = timestamp → messages created before that timestamp are read
        - read_at = null → all messages are unread
        """
        read_at = request.data.get("read_at")

        # read_at must be None or a valid datetime string
        if read_at is not None and parse_datetime(str(read_at)) is None:
            return drf.response.Response(
                {"detail": "read_at must be a valid ISO 8601 datetime."},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        thread_qs = models.Thread.objects.filter(
            id__in=thread_ids,
        ).filter(
            id__in=accessible_thread_ids_qs,
        )
        accesses = models.ThreadAccess.objects.filter(
            thread__in=thread_qs,
            mailbox_id=mailbox_id,
        )

        thread_ids_to_sync = [
            str(tid) for tid in accesses.values_list("thread_id", flat=True)
        ]
        updated_count = accesses.update(read_at=read_at)

        if thread_ids_to_sync:
            transaction.on_commit(
                lambda ids=thread_ids_to_sync: update_threads_mailbox_flags_task.delay(
                    ids
                )
            )

        return drf.response.Response(
            {"success": True, "updated_threads": updated_count}
        )

    def _handle_starred_flag(
        self,
        request,
        thread_ids,
        mailbox_id,
        accessible_thread_ids_qs,
    ):
        """Handle the 'starred' flag by setting ThreadAccess.starred_at.

        The caller sends value=true to star and value=false to unstar.
        An optional starred_at timestamp can be provided; otherwise the
        current time is used when starring.
        """
        value = request.data.get("value")
        starred_at = request.data.get("starred_at")

        if value:
            # Use provided timestamp or default to now
            if starred_at is not None:
                parsed = parse_datetime(str(starred_at))
                if parsed is None:
                    return drf.response.Response(
                        {"detail": "starred_at must be a valid ISO 8601 datetime."},
                        status=drf.status.HTTP_400_BAD_REQUEST,
                    )
                starred_at = parsed
            else:
                starred_at = timezone.now()
        else:
            starred_at = None

        thread_qs = models.Thread.objects.filter(
            id__in=thread_ids,
        ).filter(
            id__in=accessible_thread_ids_qs,
        )
        accesses = models.ThreadAccess.objects.filter(
            thread__in=thread_qs,
            mailbox_id=mailbox_id,
        )

        thread_ids_to_sync = [
            str(tid) for tid in accesses.values_list("thread_id", flat=True)
        ]
        updated_count = accesses.update(starred_at=starred_at)

        if thread_ids_to_sync:
            transaction.on_commit(
                lambda ids=thread_ids_to_sync: update_threads_mailbox_flags_task.delay(
                    ids
                )
            )

        return drf.response.Response(
            {"success": True, "updated_threads": updated_count}
        )
