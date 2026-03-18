"""API ViewSet to list users who have access to a thread."""

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


@extend_schema(tags=["thread-users"])
class ThreadUserViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
):
    """List distinct users who have access to a thread (via ThreadAccess → Mailbox → MailboxAccess)."""

    serializer_class = serializers.UserWithoutAbilitiesSerializer
    pagination_class = None
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToManageThreadAccess,
    ]

    def get_queryset(self):
        """Return distinct users who have access to the thread."""
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.User.objects.none()

        return (
            models.User.objects.filter(
                mailbox_accesses__mailbox__thread_accesses__thread_id=thread_id,
            )
            .distinct()
            .order_by("full_name", "email")
        )
