"""API ViewSet for MailboxAccess model, managed by MailDomain admins or Mailbox admins."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers


@extend_schema(tags=["mailbox-accesses"])
class MailboxAccessViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for managing MailboxAccess records for a specific Mailbox.
    The mailbox_id is expected as part of the URL.
    Access is allowed if the user has MailboxAccess (ADMIN role)
    to the target Mailbox itself, or is a domain admin of the mailbox's domain.
    """

    permission_classes = [
        core_permissions.IsAuthenticated,
        core_permissions.IsMailboxAdmin,
    ]

    # The lookup_field for the MailboxAccess instance itself (for retrieve, update, destroy)
    lookup_field = "pk"
    # The URL kwarg 'mailbox_id' for the parent Mailbox will be passed by the nested router

    def get_serializer_class(self):
        """Select serializer based on action."""
        if self.action in ["create", "update", "partial_update"]:
            return core_serializers.MailboxAccessWriteSerializer
        return core_serializers.MailboxAccessReadSerializer

    def get_mailbox_object(self):
        """Helper to get the parent Mailbox object from URL kwarg."""
        return get_object_or_404(models.Mailbox, pk=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """
        Return MailboxAccess instances for the specific Mailbox from the URL.
        Permissions should have already verified the user can access this mailbox.
        """
        mailbox = self.get_mailbox_object()  # Ensures mailbox exists and handles 404
        return mailbox.accesses.select_related("user", "mailbox__domain").order_by(
            "-created_at"
        )

    def get_serializer_context(self):
        """Add mailbox to serializer context for validation."""
        context = super().get_serializer_context()
        if self.action == "create":
            # Add mailbox to context for validation in serializer
            context["mailbox"] = self.get_mailbox_object()
        return context

    def perform_create(self, serializer):
        """Set the mailbox from the URL when creating a MailboxAccess."""
        mailbox = self.get_mailbox_object()
        serializer.save(mailbox=mailbox)
