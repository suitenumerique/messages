"""API ViewSet for Message model."""

import rest_framework as drf
from rest_framework import mixins, status, viewsets

from core import models

from .. import permissions, serializers


class MessageViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Message model."""

    serializer_class = serializers.MessageSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccessMailbox,
    ]
    queryset = models.Message.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to messages of the current user's mailbox."""
        queryset = super().get_queryset()
        if self.action == "list":
            thread_id = self.request.GET.get("thread_id")
            if thread_id:
                queryset = queryset.filter(thread__id=thread_id).order_by("created_at")
            else:
                return queryset.none()
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Delete a message."""
        # if message is the last of the thread, delete the thread
        message = self.get_object()
        if message.thread.messages.count() == 1:
            message.thread.delete()
        message.delete()
        return drf.response.Response(status=status.HTTP_204_NO_CONTENT)
