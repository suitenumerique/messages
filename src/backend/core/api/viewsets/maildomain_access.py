"""API ViewSet for MaildomainAccess model."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers


@extend_schema(tags=["maildomain-accesses"])
class MaildomainAccessViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for managing MaildomainAccess records for a specific Maildomain.
    Access is allowed if the user is maildomain admin or a super user.
    """

    permission_classes = [
        core_permissions.IsSuperUser | core_permissions.IsMailDomainAdmin
    ]

    # The lookup_field for the MailboxAccess instance itself (for retrieve, update, destroy)
    lookup_field = "pk"
    pagination_class = None

    def get_serializer_class(self):
        """Select serializer based on action."""
        if self.action in ["create", "update", "partial_update"]:
            return core_serializers.MaildomainAccessWriteSerializer
        return core_serializers.MaildomainAccessReadSerializer

    def get_queryset(self):
        """
        Return MailboxAccess instances for the specific Mailbox from the URL.
        Permissions should have already verified the user can access this mailbox.
        """
        return (
            models.MailDomainAccess.objects.filter(
                maildomain_id=self.kwargs["maildomain_pk"]
            )
            .select_related("user")
            .order_by("-created_at")
        )

    def get_maildomain_object(self):
        """Helper to get the parent MailDomain object from URL kwarg."""
        return get_object_or_404(models.MailDomain, pk=self.kwargs["maildomain_pk"])

    def perform_create(self, serializer):
        """Set the maildomain from the URL when creating a MailDomainAccess."""
        serializer.save(maildomain=self.get_maildomain_object())
