"""API ViewSet for User model."""

from django.db.models import Q

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import viewsets

from core import models

from .. import permissions, serializers


class UserViewSet(viewsets.GenericViewSet):
    """
    ViewSet for User model.
    Allows searching users globally or in a specific maildomain only for admin users.
    Allows retrieving authenticated user information.
    """

    serializer_class = serializers.UserWithoutAbilitiesSerializer
    pagination_class = None
    queryset = models.User.objects.none()

    def get_serializer_class(self):
        """Get the serializer class to use for the action."""
        if self.action == "get_me":
            return serializers.UserWithAbilitiesSerializer
        return super().get_serializer_class()

    def get_permissions(self):
        """ "
        Get the permissions to use for the action.
        """
        if self.action == "get_me":
            permission_classes = [permissions.IsAuthenticated & permissions.IsSelf]

        elif self.action == "list":
            permission_classes = [
                permissions.IsSuperUser | permissions.IsMailDomainAdmin
            ]
        else:
            return super().get_permissions()

        return [permission() for permission in permission_classes]

    @extend_schema(
        tags=["admin-users-list"],
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search users by full name, short name or email.",
            ),
            OpenApiParameter(
                name="maildomain_pk",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter users by maildomain.",
            ),
        ],
        responses=serializers.UserWithoutAbilitiesSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        """
        List users.
        Search users by email, full name or maildomain.
        A search query of at least 3 characters is required.
        """
        query = request.query_params.get("q", "")
        maildomain_pk = request.query_params.get("maildomain_pk")
        is_superuser = request.user.is_superuser

        # If not superuser, a maildomain_pk is required and the user must be an admin of that maildomain
        if not is_superuser:
            if not maildomain_pk:
                raise drf.exceptions.PermissionDenied(
                    "You do not have permission to perform this action."
                )
            if not models.MailDomainAccess.objects.filter(
                user=request.user,
                maildomain_id=maildomain_pk,
                role=models.MailDomainAccessRoleChoices.ADMIN,
            ).exists():
                raise drf.exceptions.PermissionDenied(
                    "You do not have administrative rights for this mail domain."
                )

        if len(query) < 3:
            return drf.response.Response([])

        queryset = models.User.objects.filter(email__isnull=False, is_active=True)

        queryset = queryset.filter(
            Q(email__unaccent__icontains=query)
            | Q(full_name__unaccent__icontains=query)
        )

        if maildomain_pk:
            queryset = queryset.filter(
                Q(mailbox_accesses__mailbox__domain_id=maildomain_pk)
                | Q(
                    maildomain_accesses__maildomain_id=maildomain_pk,
                    maildomain_accesses__role=models.MailDomainAccessRoleChoices.ADMIN,
                )
            )

        serializer = self.get_serializer(
            queryset.distinct().order_by("full_name", "email"), many=True
        )
        return drf.response.Response(serializer.data)

    @drf.decorators.action(
        detail=False,
        methods=["get"],
        url_name="me",
        url_path="me",
    )
    def get_me(self, request):
        """
        Return information on currently logged user
        """
        context = {"request": request}
        serializer = self.get_serializer(request.user, context=context)
        return drf.response.Response(serializer.data)
