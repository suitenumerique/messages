"""API ViewSet for Channel model."""

from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from core import models

from .. import permissions, serializers


@extend_schema(
    tags=["channels"], description="Manage integration channels for a mailbox"
)
class ChannelViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Channel model - allows mailbox admins to manage integration channels."""

    serializer_class = serializers.ChannelSerializer
    permission_classes = [permissions.IsMailboxAdmin]
    pagination_class = None
    lookup_field = "pk"

    @cached_property
    def mailbox(self):
        """Get mailbox from URL parameter."""
        return get_object_or_404(models.Mailbox, id=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """Get channels for the mailbox the user has admin access to."""
        return models.Channel.objects.filter(mailbox=self.mailbox).order_by(
            "-created_at"
        )

    def get_serializer_context(self):
        """Add mailbox to serializer context."""
        context = super().get_serializer_context()
        context["mailbox"] = self.mailbox
        return context

    @extend_schema(
        request=serializers.ChannelSerializer,
        responses={
            201: OpenApiResponse(
                response=serializers.ChannelSerializer,
                description="Channel created successfully",
            ),
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new channel for the mailbox."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(mailbox=self.mailbox)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=serializers.ChannelSerializer,
        responses={
            200: OpenApiResponse(
                response=serializers.ChannelSerializer,
                description="Channel updated successfully",
            ),
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    def update(self, request, *args, **kwargs):
        """Update a channel."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(
        responses={
            204: OpenApiResponse(description="Channel deleted successfully"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a channel."""
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
