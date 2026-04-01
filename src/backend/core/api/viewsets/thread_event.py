"""API ViewSet for ThreadEvent model."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


@extend_schema(tags=["thread-events"])
class ThreadEventViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for ThreadEvent model."""

    serializer_class = serializers.ThreadEventSerializer
    pagination_class = None
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccess,
    ]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to events for the specified thread."""
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.ThreadEvent.objects.none()

        return (
            models.ThreadEvent.objects.filter(thread_id=thread_id)
            .select_related("author", "channel", "message")
            .order_by("created_at")
        )

    def perform_create(self, serializer):
        """Set thread from URL and author from request user."""
        thread = get_object_or_404(models.Thread, id=self.kwargs["thread_id"])
        mention_ids = [item.get("id") for item in self.request.data.get("data", {}).get("mentions", [])]
        event = serializer.save(thread=thread, author=self.request.user)

        # Create UserNotifications for mentions
        if mention_ids:
            # Deduplicate, exclude self-mention, and validate access
            unique_mention_ids = set(mention_ids) - {str(self.request.user.id)}
            if unique_mention_ids:
                valid_user_ids = set(
                    models.MailboxAccess.objects.filter(
                        mailbox__thread_accesses__thread=thread,
                        user_id__in=unique_mention_ids,
                    ).values_list("user_id", flat=True)
                )
                print('>>> Notify user mentionned')
                print(valid_user_ids)
                notifications = [
                    models.UserNotification(
                        user_id=user_id,
                        type="mention",
                        thread=thread,
                        thread_event=event,
                    )
                    for user_id in valid_user_ids
                ]
                models.UserNotification.objects.bulk_create(notifications)
