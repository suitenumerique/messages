"""Admin ViewSets for MailDomain and Mailbox management."""

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _  # For user-facing error messages

from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers


class MailDomainAdminViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
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

        accessible_maildomain_ids = models.MailDomainAccess.objects.filter(
            user=user, role=models.MailDomainAccessRoleChoices.ADMIN
        ).values_list("maildomain_id", flat=True)

        return models.MailDomain.objects.filter(
            id__in=list(accessible_maildomain_ids)
        ).order_by("name")


class MailboxAdminViewSet(
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

    def create(self, request, *args, **kwargs):
        maildomain_pk = self.kwargs.get("maildomain_pk")
        domain = get_object_or_404(models.MailDomain, pk=maildomain_pk)

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

        serializer = self.get_serializer(mailbox)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )
