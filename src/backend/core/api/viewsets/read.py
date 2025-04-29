from django.utils import timezone

import rest_framework as drf
from rest_framework.views import APIView

from core import models

from .. import permissions


class ChangeReadStatusViewSet(APIView):
    """ViewSet for marking messages as read or unread."""

    permission_classes = [permissions.IsAllowedToAccessMailbox]
    action = "change_read_status"

    def post(self, request, *args, **kwargs):
        """
        Mark multiple messages or threads as read or unread.

        POST /api/v1.0/read/

        Expected parameters:
        - status: 1 (read) or 0 (unread)
        - message_ids: comma-separated list of message IDs (optional)
        - thread_ids: comma-separated list of thread IDs (optional)

        At least one of message_ids or thread_ids must be provided.
        """
        # Get status parameter (1 for read, 0 for unread)
        try:
            status_param = int(request.data.get("status", 1))
            is_read = bool(status_param)
        except (ValueError, TypeError):
            return drf.response.Response(
                {"detail": "Status must be 0 or 1"},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        # Get current timestamp for read status
        timestamp = timezone.now() if is_read else None

        # Process message IDs if provided
        message_ids = request.data.get("message_ids", "")
        thread_ids = request.data.get("thread_ids", "")

        if not message_ids and not thread_ids:
            return drf.response.Response(
                {"detail": "Either message_ids or thread_ids must be provided"},
                status=drf.status.HTTP_400_BAD_REQUEST,
            )

        updated_messages = []
        updated_threads = set()

        # Update messages directly specified
        if message_ids:
            if isinstance(message_ids, str):
                message_ids = [
                    mid.strip() for mid in message_ids.split(",") if mid.strip()
                ]

            accessible_messages = models.Message.objects.filter(
                thread__mailbox__in=self.request.user.mailbox_accesses.values_list(
                    "mailbox_id", flat=True
                ),
                id__in=message_ids,
            )
            for message in accessible_messages:
                message.read_at = timestamp
                message.save(update_fields=["read_at", "updated_at"])
                updated_messages.append(message)
                updated_threads.add(message.thread)

        # Update all messages in specified threads
        if thread_ids:
            if isinstance(thread_ids, str):
                thread_ids = [
                    tid.strip() for tid in thread_ids.split(",") if tid.strip()
                ]

            # Get all threads the user has access to
            accessible_threads = models.Thread.objects.filter(
                mailbox__in=self.request.user.mailbox_accesses.values_list(
                    "mailbox_id", flat=True
                ),
                id__in=thread_ids,
            )

            # Update all messages in these threads
            for thread in accessible_threads:
                thread_messages = thread.messages.all()
                thread_messages.update(read_at=timestamp, updated_at=timezone.now())
                updated_threads.add(thread)
                updated_messages.extend(list(thread_messages))

        # Update thread read status for all affected threads
        for thread in updated_threads:
            thread.update_read_status()

        return drf.response.Response(
            {
                "detail": (
                    f"Successfully marked {len(updated_messages)} messages as "
                    f"{'read' if is_read else 'unread'}"
                ),
                "updated_messages": len(updated_messages),
                "updated_threads": len(updated_threads),
            }
        )
