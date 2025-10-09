"""Admin ViewSets for MailDomain and Mailbox management."""

from logging import getLogger

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from rest_framework import (
    mixins,
    status,
    viewsets,
)
from rest_framework import (
    serializers as drf_serializers,
)
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers
from core.enums import MessageTemplateTypeChoices
from core.services.dns.check import check_dns_records

logger = getLogger(__name__)


class AdminMailDomainViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for listing MailDomains the user administers.
    Provides a top-level entry for mail domain administration.
    Endpoint: /maildomains/<maildomain_pk>/
    """

    serializer_class = core_serializers.MailDomainAdminSerializer
    permission_classes = [
        core_permissions.IsSuperUser | core_permissions.IsMailDomainAdmin
    ]
    lookup_url_kwarg = "maildomain_pk"

    def get_permissions(self):
        if self.action == "create":
            return [core_permissions.IsSuperUser()]
        return super().get_permissions()

    def get_serializer_class(self):
        """Select serializer based on action."""
        if self.action == "create":
            return core_serializers.MailDomainAdminWriteSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return models.MailDomain.objects.none()

        if user.is_superuser:
            # For superusers, preload accesses to avoid N+1 queries in get_abilities
            return models.MailDomain.objects.prefetch_related("accesses").order_by(
                "name"
            )
        # Optimization : one query with JOIN and annotation
        return (
            models.MailDomain.objects.filter(
                accesses__user=user,
                accesses__role=models.MailDomainAccessRoleChoices.ADMIN,
            )
            .annotate(user_role=F("accesses__role"))
            .distinct()
            .order_by("name")
        )

    @extend_schema(
        description="Check DNS records for a specific mail domain.",
        responses={
            200: inline_serializer(
                name="DNSCheckResponse",
                fields={
                    "domain": drf_serializers.CharField(),
                    "records": drf_serializers.ListField(
                        child=inline_serializer(
                            name="DNSRecordCheck",
                            fields={
                                "target": drf_serializers.CharField(),
                                "type": drf_serializers.CharField(),
                                "value": drf_serializers.CharField(),
                                "_check": inline_serializer(
                                    name="DNSCheckResult",
                                    fields={
                                        "status": drf_serializers.CharField(),
                                        "found": drf_serializers.ListField(
                                            child=drf_serializers.CharField(),
                                            required=False,
                                        ),
                                        "error": drf_serializers.CharField(
                                            required=False,
                                        ),
                                    },
                                ),
                            },
                        ),
                    ),
                },
            ),
        },
    )
    @action(detail=True, methods=["post"], url_path="check-dns")
    def check_dns(self, request, maildomain_pk=None):
        """
        Check DNS records for a specific mail domain.
        Returns the expected DNS records with their current status.
        """
        maildomain = get_object_or_404(models.MailDomain, pk=maildomain_pk)

        # Perform DNS check
        check_results = check_dns_records(maildomain)

        return Response(
            {
                "domain": maildomain.name,
                "records": check_results,
            }
        )


class AdminMailDomainMailboxViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
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
        core_permissions.IsSuperUser | core_permissions.IsMailDomainAdmin
    ]
    serializer_class = core_serializers.MailboxAdminSerializer

    def get_queryset(self):
        maildomain_pk = self.kwargs.get("maildomain_pk")
        return models.Mailbox.objects.filter(domain_id=maildomain_pk)

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
                        "name": drf_serializers.CharField(
                            required=False, allow_blank=True
                        ),
                        "custom_attributes": drf_serializers.JSONField(required=False),
                    },
                ),
            },
        ),
        responses={
            201: OpenApiResponse(
                response=core_serializers.MailboxAdminCreateSerializer(),
                description=(
                    "The new mailbox with one extra field `one_time_password` "
                    "if identity provider is keycloak."
                ),
            ),
        },
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        maildomain_pk = self.kwargs.get("maildomain_pk")
        domain = get_object_or_404(models.MailDomain, pk=maildomain_pk)
        metadata = request.data.get("metadata", {})

        # --- Create Mailbox ---
        # Will validate local_part format via the model's validator
        serializer = self.get_serializer(
            data=request.data, context={"domain": domain, "metadata": metadata}
        )
        serializer.is_valid(raise_exception=True)
        mailbox = serializer.save()
        payload = serializer.data

        # This is a somewhat hacky bypass of abstractions, but for now
        # we need to return a one time password synchronously
        if mailbox.can_reset_password:
            mailbox_password = mailbox.reset_password()
            payload["one_time_password"] = mailbox_password

        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(
        description="Partially update a mailbox in a specific maildomain.",
        request=inline_serializer(
            name="MailboxAdminPartialUpdatePayload",
            fields={
                "metadata": inline_serializer(
                    name="MailboxAdminUpdateMetadata",
                    fields={
                        "full_name": drf_serializers.CharField(
                            required=False, allow_blank=True
                        ),
                        "name": drf_serializers.CharField(
                            required=False, allow_blank=True
                        ),
                        "custom_attributes": drf_serializers.JSONField(required=False),
                    },
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                response=core_serializers.MailboxAdminSerializer(),
                description="The updated mailbox.",
            ),
        },
    )
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """Partial update a mailbox in a specific maildomain."""
        mailbox = self.get_object()
        metadata = request.data.get("metadata", {})

        serializer = self.get_serializer(
            partial=True,
            instance=mailbox,
            data=request.data,
            context={"domain": mailbox.domain, "metadata": metadata},
        )
        serializer.is_valid(raise_exception=True)
        mailbox = serializer.save()
        payload = serializer.data

        return Response(payload, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="maildomains_mailboxes_reset_password",
        description="Reset the Keycloak password for a specific mailbox.",
        request=inline_serializer(
            name="MailboxAdminResetPasswordPayload",
            fields={},
        ),
        responses={
            200: inline_serializer(
                name="ResetPasswordResponse",
                fields={"one_time_password": drf_serializers.CharField()},
            ),
            400: inline_serializer(
                name="ResetPasswordError",
                fields={"error": drf_serializers.CharField()},
            ),
            404: inline_serializer(
                name="ResetPasswordNotFound",
                fields={"error": drf_serializers.CharField()},
            ),
            500: inline_serializer(
                name="ResetPasswordInternalServerError",
                fields={"error": drf_serializers.CharField()},
            ),
        },
    )
    @action(detail=True, methods=["patch"], url_path="reset-password")
    def reset_password(self, request, *args, **kwargs):
        """
        Reset the Keycloak password for a specific mailbox.
        """

        if not settings.IDENTITY_PROVIDER == "keycloak":
            return Response(
                {"error": "Identity provider is not Keycloak."},
                status=status.HTTP_404_NOT_FOUND,
            )

        mailbox = self.get_object()

        if not mailbox.can_reset_password:
            return Response(
                {
                    "error": (
                        "Cannot reset password for this mailbox. "
                        "Mail domain identity sync is not enabled or the mailbox is not a personal mailbox."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mailbox_password = mailbox.reset_password()
        # pylint: disable=broad-exception-caught
        except Exception as e:
            logger.error("Error resetting password for mailbox %s: %s", mailbox, e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"one_time_password": mailbox_password}, status=status.HTTP_200_OK
        )


class AdminMailDomainMessageTemplateViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet for managing message templates for a maildomain."""

    permission_classes = [
        core_permissions.IsSuperUser | core_permissions.IsMailDomainAdmin
    ]
    serializer_class = core_serializers.MessageTemplateSerializer
    pagination_class = None
    ordering = ["-created_at"]

    def get_queryset(self):
        """Get queryset for list action with filtering."""
        queryset = models.MessageTemplate.objects.filter(
            maildomain_id=self.kwargs.get("maildomain_pk")
        )
        # filter by type if provided
        template_types = [
            MessageTemplateTypeChoices[template_type.upper()]
            for template_type in self.request.query_params.getlist("type")
            if template_type.upper() in MessageTemplateTypeChoices.names
        ]
        if template_types:
            queryset = queryset.filter(type__in=template_types)
        return queryset

    def get_serializer_context(self):
        """Add maildomain to serializer context."""
        context = super().get_serializer_context()
        context["domain"] = get_object_or_404(
            models.MailDomain, pk=self.kwargs.get("maildomain_pk")
        )
        return context

    @extend_schema(
        responses=core_serializers.MessageTemplateSerializer(many=True),
        description="List message templates for a maildomain.",
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                enum=[c[1] for c in MessageTemplateTypeChoices.choices],
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List message templates for a maildomain."""
        return super().list(request, *args, **kwargs)
