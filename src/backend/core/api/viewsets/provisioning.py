"""Provisioning API views (service-to-service, API key auth)."""

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from sentry_sdk import capture_exception

from core import models
from core.api.permissions import HasCalendarsApiKey, HasProvisioningApiKey
from core.api.serializers import (
    MailboxLightSerializer,
    ProvisioningMailDomainSerializer,
)
from core.enums import MailboxRoleChoices

logger = logging.getLogger(__name__)


class ProvisioningMailDomainView(APIView):
    """Provision mail domains from DeployCenter webhooks."""

    permission_classes = [HasProvisioningApiKey]
    authentication_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        """Provision mail domains from a list of domain names."""
        serializer = ProvisioningMailDomainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        domains = serializer.validated_data["domains"]
        custom_attributes = serializer.validated_data.get("custom_attributes", {})
        oidc_autojoin = serializer.validated_data["oidc_autojoin"]
        identity_sync = serializer.validated_data["identity_sync"]

        created = []
        existing = []
        errors = []

        for domain_name in domains:
            try:
                domain, was_created = models.MailDomain.objects.get_or_create(
                    name=domain_name,
                    defaults={
                        "custom_attributes": custom_attributes,
                        "oidc_autojoin": oidc_autojoin,
                        "identity_sync": identity_sync,
                    },
                )
                if was_created:
                    created.append(domain_name)
                else:
                    updated = False
                    if domain.custom_attributes != custom_attributes:
                        domain.custom_attributes = custom_attributes
                        updated = True
                    if domain.oidc_autojoin != oidc_autojoin:
                        domain.oidc_autojoin = oidc_autojoin
                        updated = True
                    if domain.identity_sync != identity_sync:
                        domain.identity_sync = identity_sync
                        updated = True
                    if updated:
                        domain.save()
                    existing.append(domain_name)
            except ValidationError as e:
                errors.append({"domain": domain_name, "error": str(e)})
            except IntegrityError as exc:
                capture_exception(exc)
                logger.exception(
                    "IntegrityError while provisioning domain %s", domain_name
                )
                errors.append(
                    {
                        "domain": domain_name,
                        "error": "Failed to provision domain.",
                    }
                )

        return Response(
            {
                "created": created,
                "existing": existing,
                "errors": errors,
            },
            status=status.HTTP_200_OK,
        )


class ProvisioningMailboxView(APIView):
    """List mailboxes for a user or look up a mailbox by email.

    GET /api/v1.0/provisioning/mailboxes/?user_email=...
    GET /api/v1.0/provisioning/mailboxes/?email=...
    """

    permission_classes = [HasCalendarsApiKey]
    authentication_classes = []

    @extend_schema(exclude=True)
    def get(self, request):
        """Return mailboxes filtered by user_email or email query parameter."""
        user_email = request.query_params.get("user_email")
        email = request.query_params.get("email")

        if user_email:
            return self._list_by_user(user_email)
        if email:
            return self._list_by_email(email)

        return Response(
            {"detail": "Provide either 'user_email' or 'email' query parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _list_by_user(self, user_email):
        accesses = models.MailboxAccess.objects.filter(
            user__email=user_email
        ).select_related("mailbox__domain", "mailbox__contact")

        results = []
        for access in accesses:
            data = MailboxLightSerializer(access.mailbox).data
            data["role"] = MailboxRoleChoices(access.role).label
            results.append(data)

        return Response({"results": results})

    def _list_by_email(self, email):
        if "@" not in email:
            return Response({"results": []})

        local_part, domain_name = email.rsplit("@", 1)
        mailboxes = models.Mailbox.objects.filter(
            local_part=local_part, domain__name=domain_name
        ).select_related("domain", "contact")

        results = MailboxLightSerializer(mailboxes, many=True).data
        return Response({"results": results})


class ProvisioningUserView(APIView):
    """List users who have access to a mailbox.

    GET /api/v1.0/provisioning/users/?mailbox=...
    """

    permission_classes = [HasCalendarsApiKey]
    authentication_classes = []

    @extend_schema(exclude=True)
    def get(self, request):
        """Return users who have access to the specified mailbox."""
        mailbox = request.query_params.get("mailbox")
        if not mailbox or "@" not in mailbox:
            return Response(
                {"detail": "Provide 'mailbox' query parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        local_part, domain_name = mailbox.rsplit("@", 1)
        accesses = models.MailboxAccess.objects.filter(
            mailbox__local_part=local_part,
            mailbox__domain__name=domain_name,
        ).select_related("user")

        results = [
            {
                "email": access.user.email,
                "role": MailboxRoleChoices(access.role).label,
            }
            for access in accesses
        ]

        return Response({"results": results})
