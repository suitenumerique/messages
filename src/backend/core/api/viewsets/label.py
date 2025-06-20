"""API ViewSet for Label model."""
# pylint: disable=line-too-long

import uuid

from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404
from django.utils.text import slugify

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core import models

from .. import permissions, serializers


@extend_schema(tags=["labels"], description="View and manage labels")
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
    pagination_class = None
    lookup_field = "pk"
    lookup_url_kwarg = "pk"

    def get_object(self):
        """Get the object and check basic mailbox access."""
        obj = get_object_or_404(models.Label, pk=self.kwargs["pk"])
        # Check basic mailbox access
        if not obj.mailbox.accesses.filter(user=self.request.user).exists():
            raise PermissionDenied("You don't have access to this mailbox")
        return obj

    def get_queryset(self):
        """Restrict results to labels in mailboxes accessible by the current user."""
        user = self.request.user
        mailbox_id = self.request.GET.get("mailbox_id")

        # For read operations, allow any role
        queryset = models.Label.objects.filter(
            Exists(
                models.MailboxAccess.objects.filter(
                    mailbox=OuterRef("mailbox"),
                    user=user,
                )
            )
        )

        if mailbox_id:
            queryset = queryset.filter(mailbox_id=mailbox_id)

        return queryset.distinct()

    def check_mailbox_permissions(self, mailbox):
        """Check if user has EDITOR or ADMIN role for the mailbox."""
        if not mailbox.accesses.filter(
            user=self.request.user,
            role__in=[
                models.MailboxRoleChoices.ADMIN,
                models.MailboxRoleChoices.EDITOR,
            ],
        ).exists():
            raise PermissionDenied("You need EDITOR or ADMIN role to manage labels")

    @extend_schema(
        description="""
        List all labels accessible to the user in a hierarchical structure.

        The response returns labels in a tree structure where:
        - Labels are ordered alphabetically by name
        - Each label includes its children (sub-labels)
        - The hierarchy is determined by the label's name (e.g., "Inbox/Important" is a child of "Inbox")

        You can filter labels by mailbox using the mailbox_id query parameter.
        """,
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=uuid.UUID,
                location=OpenApiParameter.QUERY,
                description="""
                Filter labels by mailbox ID. If not provided, returns labels from all accessible mailboxes.
                """,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=serializers.TreeLabelSerializer(many=True),
                description="List of labels in hierarchical structure",
            ),
        },
    )
    def list(self, request, *args, **kwargs):
        """List labels in a hierarchical structure, ordered alphabetically by name."""
        queryset = self.get_queryset().order_by("name")

        # Get all labels and build the tree structure
        labels = list(queryset)
        label_dict = {}
        root_labels = []

        # First pass: create dictionary of all labels
        for label in labels:
            label_dict[label.id] = {
                "id": str(label.id),
                "name": label.name,
                "slug": label.slug,
                "color": label.color,
                "display_name": label.name.split("/")[-1],
                "children": [],
            }

        # Second pass: build the tree structure
        for label in labels:
            label_data = label_dict[label.id]
            parts = label.name.split("/")

            if len(parts) == 1:
                # This is a root label
                root_labels.append(label_data)
            else:
                # This is a child label
                parent_name = "/".join(parts[:-1])
                # Find parent label
                for potential_parent in labels:
                    if potential_parent.name == parent_name:
                        label_dict[potential_parent.id]["children"].append(label_data)
                        break

        # Sort children alphabetically by name
        for label_data in label_dict.values():
            label_data["children"].sort(key=lambda x: x["name"])

        # Sort root labels alphabetically
        root_labels.sort(key=lambda x: x["name"])

        return Response(root_labels)

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
            403: OpenApiResponse(
                response={"detail": "You need EDITOR or ADMIN role to manage labels"},
                description="Permission denied",
            ),
        },
    )
    def update(self, request, *args, **kwargs):
        """Update a label, including its slug if the name changes."""
        instance = self.get_object()  # Check basic access
        self.check_mailbox_permissions(instance.mailbox)  # Check EDITOR/ADMIN role
        partial = kwargs.pop("partial", False)
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
        request=serializers.LabelSerializer,
        responses={
            201: OpenApiResponse(
                response=serializers.TreeLabelSerializer(many=True),
                description="Created labels in hierarchical structure",
            ),
            400: OpenApiResponse(
                response={"detail": "Validation error"},
                description="Invalid input data",
            ),
            403: OpenApiResponse(
                response={"detail": "You need EDITOR or ADMIN role to manage labels"},
                description="Permission denied",
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a label, ensuring parent labels exist in the hierarchy."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Get the validated data
        name = serializer.validated_data["name"]
        mailbox = serializer.validated_data["mailbox"]

        # Check if user has EDITOR or ADMIN role for the mailbox
        self.check_mailbox_permissions(mailbox)

        color = serializer.validated_data.get(
            "color",
            models.Label._meta.get_field("color").default,  # noqa: SLF001
        )

        # Create the actual label with color if provided, otherwise use model default
        label = models.Label.objects.create(name=name, mailbox=mailbox, color=color)

        # Get all labels for the mailbox to build the tree structure
        all_labels = models.Label.objects.filter(mailbox=mailbox).order_by("name")
        label_dict = {}
        root_labels = []

        # Build the tree structure
        for label in all_labels:
            label_data = {
                "id": str(label.id),
                "name": label.name,
                "slug": label.slug,
                "color": label.color,
                "display_name": label.basename,
                "parent_name": label.parent_name,
                "depth": label.depth,
                "children": [],
            }
            label_dict[label.id] = label_data

            # Add to root labels or parent's children
            if label.parent_name is None:
                root_labels.append(label_data)
            else:
                # Find parent label by name
                parent_label = next(
                    (l for l in all_labels if l.name == label.parent_name), None
                )
                if parent_label:
                    label_dict[parent_label.id]["children"].append(label_data)

        # Sort children alphabetically by name
        for label_data in label_dict.values():
            label_data["children"].sort(key=lambda x: x["name"])

        # Sort root labels alphabetically
        root_labels.sort(key=lambda x: x["name"])

        return Response(root_labels, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={
            204: OpenApiResponse(description="Label deleted successfully"),
            403: OpenApiResponse(
                response={"detail": "You need EDITOR or ADMIN role to manage labels"},
                description="Permission denied",
            ),
            404: OpenApiResponse(description="Label not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a label."""
        instance = self.get_object()  # Check basic access
        self.check_mailbox_permissions(instance.mailbox)  # Check EDITOR/ADMIN role
        self.perform_destroy(instance)
        return drf.response.Response(status=status.HTTP_204_NO_CONTENT)

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
            403: OpenApiResponse(
                response={"detail": "You need EDITOR or ADMIN role to manage labels"},
                description="Permission denied",
            ),
        },
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="add-threads",
        url_name="add-threads",
    )
    def add_threads(self, request, pk=None):  # pylint: disable=unused-argument
        """Add threads to a label."""
        label = self.get_object()  # Check basic access
        self.check_mailbox_permissions(label.mailbox)  # Check EDITOR/ADMIN role
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
            403: OpenApiResponse(
                response={"detail": "You need EDITOR or ADMIN role to manage labels"},
                description="Permission denied",
            ),
        },
    )
    @drf.decorators.action(
        detail=True,
        methods=["post"],
        url_path="remove-threads",
        url_name="remove-threads",
    )
    def remove_threads(self, request, pk=None):  # pylint: disable=unused-argument
        """Remove threads from a label."""
        label = self.get_object()  # Check basic access
        self.check_mailbox_permissions(label.mailbox)  # Check EDITOR/ADMIN role
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
