"""API ViewSet for import runs (Channels with type=import)."""

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from core import enums, models
from core.services.importer.channel import cancel_import

from .. import permissions
from ..serializers import MessageImportSerializer


@extend_schema(tags=["import"])
class MessageImportViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve and cancel import runs.

    An import run is a ``Channel`` with ``type=import`` grouping every
    message an import created. Visible to users with edit access to the
    target mailbox (and superusers); the object-level filter in
    ``get_queryset`` doubles as the authorization gate.
    """

    serializer_class = MessageImportSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Import channels the caller may act on."""
        user = self.request.user
        queryset = models.Channel.objects.filter(
            type=enums.ChannelTypes.IMPORT.value
        ).select_related("mailbox")
        if not user.is_superuser:
            queryset = queryset.filter(
                mailbox__accesses__user=user,
                mailbox__accesses__role__in=enums.MAILBOX_ROLES_CAN_EDIT,
            )
        return queryset.order_by("-created_at").distinct()

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="ImportCancelResponse",
                    fields={
                        "messages_deleted": drf_serializers.IntegerField(),
                        "threads_deleted": drf_serializers.IntegerField(),
                    },
                ),
                description=(
                    "Import cancelled: its imported messages are deleted and "
                    "threads left empty are removed."
                ),
            ),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Import not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, *args, **kwargs):
        """Cancel an import: delete its messages and clean orphan threads."""
        channel = self.get_object()
        summary = cancel_import(channel)
        return Response(summary, status=status.HTTP_200_OK)
