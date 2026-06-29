"""API ViewSet for Mailbox model."""

from django.db.models import OuterRef, Q, Subquery

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models

from .. import permissions, serializers


class MailboxViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin
):
    """ViewSet for Mailbox model."""

    serializer_class = serializers.MailboxSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Restrict results to the current user's mailboxes."""
        user = self.request.user

        return (
            models.Mailbox.objects.filter(accesses__user=user)
            .prefetch_related("accesses__user", "domain")
            .annotate(
                user_role=Subquery(
                    models.MailboxAccess.objects.filter(
                        mailbox=OuterRef("pk"), user=user
                    ).values("role")[:1]
                )
            )
            .order_by("-created_at")
        )

    def get_permissions(self):
        """Require mailbox-admin rights to edit; reading stays open to any
        member of the mailbox."""
        if self.action == "partial_update":
            return [
                permissions.IsAuthenticated(),
                permissions.IsMailboxAdminObject(),
            ]
        return super().get_permissions()

    @extend_schema(
        tags=["mailboxes"],
        request=serializers.MailboxNameUpdateSerializer,
        responses=serializers.MailboxSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        """Rename a mailbox (its display contact name). Mailbox admins only.

        ``partial=True`` keeps true PATCH semantics: omitting ``name`` is a no-op
        rather than a 400, so the runtime matches the optional request schema.
        """
        mailbox = self.get_object()
        serializer = serializers.MailboxNameUpdateSerializer(
            instance=mailbox, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = self.get_serializer(mailbox)
        return Response(output.data)

    @extend_schema(
        tags=["mailboxes"],
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search mailboxes by domain, local part and contact name.",
            ),
        ],
        responses=serializers.MailboxLightSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def search(self, request, **kwargs):
        """
        Search mailboxes by domain, local part and contact name.

        Query parameters:
        - q: Optional search query for local part and contact name
        """
        domain = self.get_object().domain

        # Start with all mailboxes in the same domain except the current one
        queryset = models.Mailbox.objects.filter(domain=domain).exclude(
            id=self.get_object().id
        )

        # Add filters for local part and contact name if provided
        if query := request.query_params.get("q", ""):
            queryset = queryset.filter(
                Q(local_part__unaccent__icontains=query)
                | Q(contact__name__unaccent__icontains=query)
            )  # exclude context mailbox

        # Order by contact name if available, otherwise by email
        queryset = queryset.order_by("contact__name", "local_part", "domain")

        serializer = serializers.MailboxLightSerializer(queryset, many=True)
        return Response(serializer.data)
