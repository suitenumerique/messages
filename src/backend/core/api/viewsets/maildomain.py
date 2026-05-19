"""Admin ViewSets for MailDomain and Mailbox management."""

from logging import getLogger

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Max, Q
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
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers
from core.api.viewsets.message_template import BODIES_PARAMETER
from core.api.viewsets.mixins import MessageTemplateResponseMixin
from core.enums import MailDomainAbilities, MessageTemplateTypeChoices
from core.services.dns.check import check_dns_records, invalidate_spf_check_cache
from core.services.identity import keycloak as keycloak_service

logger = getLogger(__name__)


class _MandatoryTotpPayloadSerializer(drf_serializers.Serializer):  # pylint: disable=abstract-method
    """Strict validation for the ``set_mandatory_totp`` request body."""

    enabled = drf_serializers.BooleanField()


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
            if not settings.FEATURE_MAILDOMAIN_CREATE:
                return [core_permissions.DenyAll()]
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
            queryset = (
                models.MailDomain.objects.prefetch_related("accesses")
                .annotate(mailbox_count=Count("mailbox"))
                .order_by("name")
            )
        else:
            # Optimization : one query with JOIN and annotation
            queryset = (
                models.MailDomain.objects.filter(
                    accesses__user=user,
                    accesses__role=models.MailDomainAccessRoleChoices.ADMIN,
                )
                .annotate(
                    user_role=F("accesses__role"),
                    mailbox_count=Count("mailbox"),
                )
                .distinct()
                .order_by("name")
            )

        search = (self.request.query_params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter domains whose name contains this value (case-insensitive).",
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List mail domains, optionally filtered by name with the `q` parameter."""
        return super().list(request, *args, **kwargs)

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

        # Perform DNS check (always fresh, never cached)
        check_results = check_dns_records(maildomain)

        # Invalidate outgoing SPF cache so send path picks up this fresh result
        invalidate_spf_check_cache(maildomain)

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
        queryset = (
            models.Mailbox.objects.filter(domain_id=maildomain_pk)
            .select_related("domain")
            .annotate(last_accessed_at=Max("accesses__accessed_at"))
            .order_by("local_part")
        )
        search = (self.request.query_params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(local_part__icontains=search) | Q(contact__name__icontains=search)
            ).distinct()
        return queryset

    def get_serializer(self, *args, **kwargs):
        """Inject the per-page mandatory TOTP membership dict when listing.

        Letting the standard ``ListModelMixin.list()`` paginate first means we
        hook in once with the page's rows already in ``args[0]`` and resolve
        membership in a single round-trip to the custom Keycloak provider.
        """
        if (
            kwargs.get("many")
            and self.action == "list"
            and args
            and keycloak_service.is_mandatory_totp_enabled()
        ):
            rows = args[0]
            usernames = [
                str(m) for m in rows if m.is_identity and m.domain.identity_sync
            ]
            try:
                membership = keycloak_service.batch_realm_role_membership(
                    usernames, settings.KEYCLOAK_TOTP_ROLE_ID
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                # Don't block the page; serializer renders `null` for these rows.
                logger.warning("Could not batch mandatory TOTP membership: %s", e)
                membership = None
            context = kwargs.setdefault("context", self.get_serializer_context())
            context["mandatory_totp_membership"] = membership
        return super().get_serializer(*args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description=(
                    "Filter mailboxes whose local part or contact name contains this "
                    "value (case-insensitive)."
                ),
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List mailboxes, optionally filtered by local part / contact name."""
        return super().list(request, *args, **kwargs)

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

    def _assert_mandatory_totp_available(self, mailbox):
        """Raise the appropriate DRF exception if the action can't proceed.

        - feature off / misconfigured → ``NotFound``
        - mailbox not eligible → ``ValidationError``
        - caller lacks the manage-mailboxes ability → ``PermissionDenied``

        ``IsMailDomainAdmin`` (the viewset permission) and the
        ``manage_mailboxes`` ability are equivalent today, but the explicit
        check pins the contract: this action requires the same ability the
        frontend gates the UI on.
        """
        if not keycloak_service.is_mandatory_totp_enabled():
            raise NotFound("Mandatory TOTP feature is not enabled.")
        if not mailbox.is_identity or not mailbox.domain.identity_sync:
            raise ValidationError(
                "Mandatory TOTP can only be set on personal mailboxes "
                "in identity-synced domains."
            )
        abilities = mailbox.domain.get_abilities(self.request.user)
        if not abilities.get(MailDomainAbilities.CAN_MANAGE_MAILBOXES):
            raise PermissionDenied("You cannot manage mailboxes in this domain.")

    @extend_schema(
        operation_id="maildomains_mailboxes_set_mandatory_totp",
        description=(
            "Toggle the Keycloak realm role indicated by KEYCLOAK_TOTP_ROLE_ID "
            "on the user backing this mailbox."
        ),
        request=inline_serializer(
            name="MailboxAdminMandatoryTotpPayload",
            fields={"enabled": drf_serializers.BooleanField()},
        ),
        responses={
            200: inline_serializer(
                name="MailboxAdminMandatoryTotpResponse",
                fields={"enabled": drf_serializers.BooleanField()},
            ),
        },
    )
    @action(detail=True, methods=["post"], url_path="mandatory-totp")
    def set_mandatory_totp(self, request, *args, **kwargs):
        """Assign or remove the configured TOTP realm role on the mailbox user."""
        # Validate the payload before doing any per-mailbox work — a malformed
        # body should always surface as 400 regardless of mailbox eligibility.
        payload = _MandatoryTotpPayloadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        enabled = payload.validated_data["enabled"]

        mailbox = self.get_object()
        self._assert_mandatory_totp_available(mailbox)

        try:
            keycloak_service.set_realm_role(
                str(mailbox), settings.KEYCLOAK_TOTP_ROLE_ID, assigned=enabled
            )
        except ValueError as e:
            # set_realm_role raises ValueError when the Keycloak user or role
            # can't be found — that's a 404 (resource doesn't exist),
            # not a server fault. Don't surface the raw Keycloak text — it can
            # carry the username (PII) or the configured role id.
            logger.warning("Mandatory TOTP target missing for mailbox %s", mailbox.id)
            raise NotFound("Keycloak resource not found for mailbox.") from e
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Error setting mandatory TOTP for mailbox %s", mailbox.id)
            return Response(
                {"error": "Could not update mandatory TOTP."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"enabled": enabled}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="maildomains_mailboxes_reset_totp",
        description=(
            "Remove existing OTP credentials and require the user to re-enroll "
            "in TOTP on next login."
        ),
        request=inline_serializer(
            name="MailboxAdminResetTotpPayload",
            fields={},
        ),
        responses={
            200: inline_serializer(
                name="MailboxAdminResetTotpResponse",
                fields={"removed_credentials": drf_serializers.IntegerField()},
            ),
        },
    )
    @action(detail=True, methods=["patch"], url_path="reset-totp")
    def reset_totp(self, request, *args, **kwargs):
        """Force-reset the TOTP enrollment for the mailbox user."""
        mailbox = self.get_object()
        self._assert_mandatory_totp_available(mailbox)

        try:
            result = keycloak_service.reset_keycloak_user_totp(str(mailbox))
        except ValueError as e:
            logger.warning("Reset TOTP target missing for mailbox %s", mailbox.id)
            raise NotFound("Keycloak resource not found for mailbox.") from e
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Error resetting TOTP for mailbox %s", mailbox.id)
            return Response(
                {"error": "Could not reset TOTP."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)


# pylint: disable=too-many-ancestors
class AdminMailDomainMessageTemplateViewSet(
    MessageTemplateResponseMixin,
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
        responses=core_serializers.ReadMessageTemplateSerializer(many=True),
        description="List message templates for a maildomain.",
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                enum=[c[1] for c in MessageTemplateTypeChoices.choices],
            ),
            BODIES_PARAMETER,
        ],
    )
    def list(self, request, *args, **kwargs):
        """List message templates for a maildomain."""
        return super().list(request, *args, **kwargs)

    @extend_schema(
        parameters=[BODIES_PARAMETER],
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a message template."""
        return super().retrieve(request, *args, **kwargs)
