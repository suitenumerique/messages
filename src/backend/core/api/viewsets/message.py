"""API ViewSet for Message model."""

from django.db.models import Exists, OuterRef
from django.http import HttpResponse

import rest_framework as drf
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action

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
        permissions.IsAllowedToAccess,
    ]
    queryset = models.Message.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to messages in threads accessible by the current user."""
        user = self.request.user
        queryset = (
            super()
            .get_queryset()
            .filter(
                Exists(
                    models.ThreadAccess.objects.filter(
                        mailbox__accesses__user=user, thread=OuterRef("thread_id")
                    )
                )
            )
        )

        if self.action == "list":
            thread_id = self.request.GET.get("thread_id")
            if thread_id:
                queryset = queryset.filter(thread__id=thread_id).order_by("created_at")
            else:
                return queryset.none()

        return queryset

    def destroy(self, request, *args, **kwargs):
        """Delete a message. Object permission checked by IsAllowedToAccess."""
        # if message is the last of the thread, delete the thread
        message = self.get_object()
        thread = message.thread
        if thread.messages.count() == 1:
            # Deleting the thread will cascade delete the message
            thread.delete()
        else:
            message.delete()
            thread.update_stats()
        return drf.response.Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="eml")
    def eml(self, request, *args, **kwargs):
        """Return the EML file for a message."""
        text_plain = request.GET.get("text_plain", "0")
        if text_plain == "1":
            content_type = "text/plain; charset=utf-8"
            headers = {}
        else:
            content_type = "message/rfc822; charset=utf-8"
            headers = {
                "Content-Disposition": 'attachment; filename="message.eml"',
            }
        message = self.get_object()
        resp = HttpResponse(
            message.blob.get_content(),
            content_type=content_type,
            headers=headers,
        )
        return resp
