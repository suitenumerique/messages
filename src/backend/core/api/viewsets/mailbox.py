"""API ViewSet for Mailbox model."""

from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


class MailboxViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    """ViewSet for Mailbox model."""

    serializer_class = serializers.MailboxSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Restrict results to the current user's mailboxes."""
        accesses = self.request.user.mailbox_accesses.all()
        return models.Mailbox.objects.filter(
            id__in=accesses.values_list("mailbox_id", flat=True)
        ).order_by("-created_at")
