"""API ViewSet for Channel model."""

from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models

from .. import permissions, serializers
from ..serializers import generate_base58_password


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
        extra = {"mailbox": self.mailbox}
        if serializer.validated_data.get("type") == "client-bridge":
            extra["user"] = request.user
        instance = serializer.save(**extra)
        data = serializer.data
        # Include the generated password in the response (shown once)
        generated_password = getattr(instance, "_generated_password", None)
        if generated_password:
            data["password"] = generated_password
        return Response(data, status=status.HTTP_201_CREATED)

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

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                name="RotatePasswordResponse",
                fields={"password": drf_serializers.CharField()},
            ),
            400: OpenApiResponse(
                description="Channel type does not support password rotation"
            ),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="rotate-password")
    def rotate_password(self, request, *args, **kwargs):
        """Generate a new app-specific password for a client-bridge channel."""
        instance = self.get_object()
        if instance.type != "client-bridge":
            return Response(
                {
                    "detail": "Password rotation is only available for client-bridge channels."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        password = generate_base58_password()
        encrypted = instance.encrypted_settings or {}
        encrypted["password"] = password
        instance.encrypted_settings = encrypted
        instance.save(update_fields=["encrypted_settings", "updated_at"])
        return Response({"password": password})
