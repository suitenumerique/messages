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
from core.api.authentication import ChannelApiKeyAuthentication
from core.api.permissions import IsGlobalChannelMixin, channel_scope
from core.api.serializers import (
    MailboxLightSerializer,
    ProvisioningMailDomainSerializer,
)
from core.enums import ChannelApiKeyScope, MailboxRoleChoices

logger = logging.getLogger(__name__)


class ProvisioningMailDomainView(IsGlobalChannelMixin, APIView):
    """Provision mail domains from DeployCenter webhooks. Global-only."""

    authentication_classes = [ChannelApiKeyAuthentication]
    permission_classes = [channel_scope(ChannelApiKeyScope.MAILDOMAINS_CREATE)]

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


def _serialize_mailbox_with_users(
    mailbox, role=None, maildomain_custom_attributes=None
):
    """Serialize a mailbox with all its users and their roles."""
    data = MailboxLightSerializer(mailbox).data
    if role is not None:
        data["role"] = role
    data["users"] = [
        {
            "email": access.user.email,
            "role": MailboxRoleChoices(access.role).label,
        }
        for access in mailbox.accesses.select_related("user").all()
    ]
    if maildomain_custom_attributes:
        domain_attrs = mailbox.domain.custom_attributes or {}
        data["maildomain_custom_attributes"] = {
            key: domain_attrs.get(key) for key in maildomain_custom_attributes
        }
    return data


class ProvisioningMailboxView(IsGlobalChannelMixin, APIView):
    """List mailboxes for a user or look up a mailbox by email.

    Each mailbox includes a ``users`` array with all users who have
    access and their roles, so callers can sync shares in one request.

    GET /api/v1.0/provisioning/mailboxes/?user_email=...
    GET /api/v1.0/provisioning/mailboxes/?email=...

    **Global-scope api_key channels only.** This endpoint is intentionally
    not exposed to maildomain- or mailbox-scope keys, even though such a key
    could only see a subset of the data — the threat model is that any
    leak of a credential able to enumerate mailboxes is treated as a
    privileged event, and only ops/CI keys should be able to do it. The
    ``IsGlobalChannelMixin`` enforces that.
    """

    authentication_classes = [ChannelApiKeyAuthentication]
    permission_classes = [channel_scope(ChannelApiKeyScope.MAILBOXES_READ)]

    @extend_schema(exclude=True)
    def get(self, request):
        """Return mailboxes filtered by user_email or email query parameter."""
        user_email = request.query_params.get("user_email")
        email = request.query_params.get("email")

        # Optional: include specific keys from MailDomain.custom_attributes
        # e.g. ?add_maildomain_custom_attributes=siret,org_name
        raw = request.query_params.get("add_maildomain_custom_attributes", "")
        maildomain_attrs = [k.strip() for k in raw.split(",") if k.strip()] or None

        if user_email:
            return self._list_by_user(user_email, maildomain_attrs)
        if email:
            return self._list_by_email(email, maildomain_attrs)

        return Response(
            {"detail": "Provide either 'user_email' or 'email' query parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _list_by_user(self, user_email, maildomain_attrs=None):
        accesses = (
            models.MailboxAccess.objects.filter(user__email=user_email)
            .select_related("mailbox__domain", "mailbox__contact")
            .prefetch_related("mailbox__accesses__user")
        )

        results = [
            _serialize_mailbox_with_users(
                access.mailbox,
                role=MailboxRoleChoices(access.role).label,
                maildomain_custom_attributes=maildomain_attrs,
            )
            for access in accesses
        ]
        return Response({"results": results})

    def _list_by_email(self, email, maildomain_attrs=None):
        if "@" not in email:
            return Response({"results": []})

        local_part, domain_name = email.rsplit("@", 1)
        mailboxes = (
            models.Mailbox.objects.filter(
                local_part=local_part, domain__name=domain_name
            )
            .select_related("domain", "contact")
            .prefetch_related("accesses__user")
        )

        results = [
            _serialize_mailbox_with_users(
                mailbox, maildomain_custom_attributes=maildomain_attrs
            )
            for mailbox in mailboxes
        ]
        return Response({"results": results})
