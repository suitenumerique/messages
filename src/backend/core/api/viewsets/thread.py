import rest_framework as drf
from rest_framework import mixins, viewsets

from core import models

from .. import permissions, serializers


class ThreadViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.DestroyModelMixin
):
    """ViewSet for Thread model."""

    serializer_class = serializers.ThreadSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to threads of the current user's mailboxes."""
        mailbox_id = self.request.GET.get("mailbox_id")
        accesses = self.request.user.mailbox_accesses.all()
        queryset = models.Thread.objects

        if mailbox_id:
            if not accesses.filter(mailbox__id=mailbox_id).exists():
                raise drf.exceptions.PermissionDenied(
                    "You do not have access to this mailbox."
                )
            queryset = queryset.filter(mailbox__id=mailbox_id)
        else:
            queryset = queryset.filter(
                mailbox__id__in=accesses.values_list("mailbox_id", flat=True)
            )
        queryset = queryset.order_by("-messaged_at")

        # Add filters based on thread counters
        filter_mapping = {
            "has_unread": "count_unread__gt",
            "is_trashed": "count_trashed__gt",
            "has_draft": "count_draft__gt",
            "has_starred": "count_starred__gt",
            "has_sender": "count_sender__gt",
        }

        for param, filter_lookup in filter_mapping.items():
            value = self.request.GET.get(param)
            if value is not None:
                if value == "1":
                    queryset = queryset.filter(**{filter_lookup: 0})
                else:
                    # Allow filtering for threads with zero count
                    queryset = queryset.filter(**{filter_lookup.replace("__gt", ""): 0})

        return queryset
