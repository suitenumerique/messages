"""API ViewSet to list users who have access to a thread."""

from django.db.models import Exists, OuterRef

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import enums, models

from .. import permissions, serializers


@extend_schema(tags=["thread-users"])
class ThreadUserViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
):
    """List distinct users who have access to a thread (via ThreadAccess → Mailbox → MailboxAccess)."""

    serializer_class = serializers.ThreadMentionableUserSerializer
    pagination_class = None
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.HasThreadCommentAccess,
    ]

    def get_queryset(self):
        """Return distinct users who have access to the thread."""
        thread_id = self.kwargs.get("thread_id")
        if not thread_id:
            return models.User.objects.none()

        # A user can post internal comments if they have at least one
        # MailboxAccess with an edit role on a mailbox that itself has
        # access to this thread. See _user_can_comment_on_thread in
        # permissions.py for the canonical rule.
        can_comment_subquery = models.MailboxAccess.objects.filter(
            user=OuterRef("pk"),
            role__in=enums.MAILBOX_ROLES_CAN_EDIT,
            mailbox__thread_accesses__thread_id=thread_id,
        )

        return (
            models.User.objects.filter(
                mailbox_accesses__mailbox__thread_accesses__thread_id=thread_id,
            )
            .annotate(can_post_comments=Exists(can_comment_subquery))
            .distinct()
            .order_by("full_name", "email")
        )
