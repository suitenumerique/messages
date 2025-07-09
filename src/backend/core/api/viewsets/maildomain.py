"""Admin ViewSets for MailDomain and Mailbox management."""

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _  # For user-facing error messages

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from rest_framework import (
    mixins,
    response,
    status,
    viewsets,
)
from rest_framework import (
    serializers as drf_serializers,
)
from rest_framework.response import Response

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers
from core.identity.keycloak import reset_keycloak_user_password


class AdminMailDomainViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    ViewSet for listing MailDomains the user administers.
    Provides a top-level entry for mail domain administration.
    Endpoint: /maildomains/
    """

    serializer_class = core_serializers.MailDomainAdminSerializer
    permission_classes = [core_permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return models.MailDomain.objects.none()

        if user.is_superuser and user.is_staff:
            return models.MailDomain.objects.all().order_by("name")

        return models.MailDomain.objects.filter(
            accesses__user=user, accesses__role=models.MailDomainAccessRoleChoices.ADMIN
        ).order_by("name")


class AdminMailDomainMailboxViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for managing Mailboxes within a specific MailDomain.
    Nested under /maildomains/{maildomain_pk}/mailboxes/
    Permissions are checked by IsMailDomainAdmin for the maildomain_pk.

    This viewset serves a different purpose than the one in mailbox.py (/api/v1.0/mailboxes/).
    That other one is for listing the mailboxes a user has access to in regular app use.
    This one is for managing mailboxes within a specific maildomain in the admin interface.
    """

    permission_classes = [
        core_permissions.IsAuthenticated,
        core_permissions.IsMailDomainAdmin,
    ]
    serializer_class = core_serializers.MailboxAdminSerializer

    def get_queryset(self):
        maildomain_pk = self.kwargs.get("maildomain_pk")
        return models.Mailbox.objects.filter(domain_id=maildomain_pk).order_by(
            "local_part"
        )

    @extend_schema(
        description="Create new mailbox in a specific maildomain.",
        request=inline_serializer(
            name="MailboxAdminCreatePayload",
            fields={
                "local_part": drf_serializers.CharField(required=True),
                "alias_of": drf_serializers.UUIDField(required=False),
                "metadata": inline_serializer(
                    name="MailboxAdminCreateMetadata",
                    fields={
                        "type": drf_serializers.ChoiceField(
                            choices=("personal", "shared", "redirect"), required=True
                        ),
                        "first_name": drf_serializers.CharField(
                            required=False, allow_blank=True
                        ),
                        "last_name": drf_serializers.CharField(
                            required=False, allow_blank=True
                        ),
                    },
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                response=core_serializers.MailboxAdminCreateSerializer(),
                description=(
                    "The new mailbox with one extra field `one_time_password` "
                    "if identity provider is keycloak."
                ),
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        maildomain_pk = self.kwargs.get("maildomain_pk")
        domain = get_object_or_404(models.MailDomain, pk=maildomain_pk)
        metadata = request.data.get("metadata", {})

        mailbox_type = metadata.get("type")
        local_part = request.data.get("local_part")
        alias_of_id = request.data.get("alias_of")

        # --- Validation for local_part ---
        if not local_part:
            return Response(
                {"local_part": [_("This field may not be blank.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Uniqueness Validation ---
        if models.Mailbox.objects.filter(
            domain=domain, local_part__iexact=local_part
        ).exists():
            return Response(
                {
                    "local_part": [
                        _(
                            "A mailbox with this local part already exists in this domain."
                        )
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        alias_of = None
        if alias_of_id:
            try:
                alias_of = models.Mailbox.objects.get(pk=alias_of_id, domain=domain)
            except models.Mailbox.DoesNotExist:
                return Response(
                    {
                        "alias_of": [
                            _(
                                "Invalid mailbox ID for alias, or mailbox not in the same domain."
                            )
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if alias_of.alias_of is not None:  # Prevent chaining aliases for now
                return Response(
                    {"alias_of": [_("Cannot create an alias of an existing alias.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # --- Create Mailbox ---
        # Will validate local_part format via the model's validator
        mailbox = models.Mailbox.objects.create(
            domain=domain, local_part=local_part, alias_of=alias_of
        )

        # --- Create user and mailbox access if type is personal ---
        if mailbox_type == "personal":
            email = f"{local_part}@{domain.name}"
            first_name = metadata.get("first_name")
            last_name = metadata.get("last_name")
            user, _created = models.User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": f"{first_name} {last_name}",
                    "short_name": first_name,
                    "password": "?",
                },
            )
            models.MailboxAccess.objects.create(
                mailbox=mailbox,
                user=user,
                role=models.MailboxRoleChoices.ADMIN,
            )

        serializer = self.get_serializer(mailbox)
        headers = self.get_success_headers(serializer.data)
        payload = serializer.data
        if mailbox_type == "personal" and settings.IDENTITY_PROVIDER == "keycloak":
            mailbox_password = reset_keycloak_user_password(email)
            payload["one_time_password"] = mailbox_password
        return Response(payload, status=status.HTTP_201_CREATED, headers=headers)


class AdminMailDomainUserViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    ViewSet for listing users in a specific MailDomain.
    Nested under /maildomains/{maildomain_pk}/users/
    Permissions are checked by IsMailDomainAdmin for the maildomain_pk.
    """

    permission_classes = [
        core_permissions.IsAuthenticated,
        core_permissions.IsMailDomainAdmin,
    ]
    serializer_class = core_serializers.UserSerializer
    pagination_class = None

    def get_queryset(self):
        """
        Get all users having an access to a mailbox or an admin access to the maildomain.
        """
        maildomain_pk = self.kwargs.get("maildomain_pk")
        # Get all users with an email ending with maildomain.name or with an admin access to the maildomain
        return (
            models.User.objects.filter(
                Q(mailbox_accesses__mailbox__domain_id=maildomain_pk)
                | Q(
                    maildomain_accesses__maildomain_id=maildomain_pk,
                    maildomain_accesses__role=models.MailDomainAccessRoleChoices.ADMIN,
                )
            )
            .distinct()
            .order_by("full_name", "short_name", "email")
        )

    @extend_schema(
        tags=["admin-maildomain-user"],
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search maildomains user by full name, short name or email.",
            ),
        ],
        responses=core_serializers.UserSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        """
        Search users by email, first name and last name.
        """
        queryset = self.get_queryset()

        if query := request.query_params.get("q", ""):
            queryset = queryset.filter(
                Q(email__unaccent__icontains=query)
                | Q(full_name__unaccent__icontains=query)
                | Q(short_name__unaccent__icontains=query)
            )

        serializer = core_serializers.UserSerializer(queryset, many=True)
        return response.Response(serializer.data)
