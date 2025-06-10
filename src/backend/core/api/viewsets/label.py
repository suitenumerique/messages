"""API ViewSet for Label model."""

from django.db.models import Exists, OuterRef
from django.utils.text import slugify

import rest_framework as drf
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets

from core import models

from .. import permissions, serializers


@extend_schema(tags=["labels"])
class LabelViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Label model."""

    serializer_class = serializers.LabelSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "pk"
    lookup_url_kwarg = "pk"

    def get_queryset(self):
        """Restrict results to labels in mailboxes accessible by the current user."""
        user = self.request.user
        mailbox_id = self.request.GET.get("mailbox_id")

        queryset = models.Label.objects.filter(
            Exists(
                models.MailboxAccess.objects.filter(
                    mailbox=OuterRef("mailbox"), user=user
                )
            )
        )

        if mailbox_id:
            queryset = queryset.filter(mailbox_id=mailbox_id)

        return queryset.distinct()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter labels by mailbox ID.",
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List labels with optional mailbox filtering."""
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=serializers.LabelSerializer,
        responses={
            201: OpenApiResponse(
                response=serializers.LabelSerializer,
                description="Label created successfully",
            ),
            400: OpenApiResponse(
                response={"detail": "Validation error"},
                description="Invalid input data",
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new label."""
        return super().create(request, *args, **kwargs)

    @extend_schema(
        request=serializers.LabelSerializer,
        responses={
            200: OpenApiResponse(
                response=serializers.LabelSerializer,
                description="Label updated successfully",
            ),
            400: OpenApiResponse(
                response={"detail": "Validation error"},
                description="Invalid input data",
            ),
        },
    )
    def update(self, request, *args, **kwargs):
        """Update a label, including its slug if the name changes."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        # If name is being updated, update the slug
        if "name" in serializer.validated_data:
            serializer.validated_data["slug"] = slugify(
                serializer.validated_data["name"].replace("/", "-")
            )

        self.perform_update(serializer)
        return drf.response.Response(serializer.data)

    @extend_schema(
        responses={
            204: OpenApiResponse(
                description="Label deleted successfully",
            ),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a label."""
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "thread_ids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "description": "List of thread IDs to add to this label",
                    },
                },
            }
        },
        responses={
            200: OpenApiResponse(
                response=serializers.LabelSerializer,
                description="Threads added to label successfully",
            ),
            400: OpenApiResponse(
                response={"detail": "Validation error"},
                description="Invalid input data",
            ),
        },
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="add-threads",
        url_name="add-threads",
    )
    def add_threads(self, request, pk=None):
        """Add threads to a label."""
        label = self.get_object()
        thread_ids = request.data.get("thread_ids", [])
        if not thread_ids:
            return drf.response.Response(
                {"detail": "No thread IDs provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        accessible_threads = models.Thread.objects.filter(
            Exists(
                models.ThreadAccess.objects.filter(
                    mailbox__accesses__user=request.user,
                    thread=OuterRef("pk"),
                )
            ),
            id__in=thread_ids,
        )
        label.threads.add(*accessible_threads)
        serializer = self.get_serializer(label)
        return drf.response.Response(serializer.data)

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "thread_ids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "description": "List of thread IDs to remove from this label",
                    },
                },
            }
        },
        responses={
            200: OpenApiResponse(
                response=serializers.LabelSerializer,
                description="Threads removed from label successfully",
            ),
            400: OpenApiResponse(
                response={"detail": "Validation error"},
                description="Invalid input data",
            ),
        },
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="remove-threads",
        url_name="remove-threads",
    )
    def remove_threads(self, request, pk=None):
        """Remove threads from a label."""
        label = self.get_object()
        thread_ids = request.data.get("thread_ids", [])
        if not thread_ids:
            return drf.response.Response(
                {"detail": "No thread IDs provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        accessible_threads = models.Thread.objects.filter(
            Exists(
                models.ThreadAccess.objects.filter(
                    mailbox__accesses__user=request.user,
                    thread=OuterRef("pk"),
                )
            ),
            id__in=thread_ids,
        )
        label.threads.remove(*accessible_threads)
        serializer = self.get_serializer(label)
        return drf.response.Response(serializer.data)
