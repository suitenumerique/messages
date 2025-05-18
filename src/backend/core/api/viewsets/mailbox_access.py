"""API ViewSet for MailboxAccess model, managed by MailDomain admins or Mailbox admins."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _  # For user-facing error messages

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

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

    serializer_class = (
        core_serializers.MailboxAccessReadSerializer
    )  # Default for list, retrieve, and responses
    permission_classes = [
        core_permissions.IsAuthenticated,
        core_permissions.IsMailboxAdmin,
    ]

    # The lookup_field for the MailboxAccess instance itself (for retrieve, update, destroy)
    lookup_field = "pk"
    # The URL kwarg for the parent Mailbox will be passed by the nested router,
    # typically 'mailbox_id' or 'mailbox_pk'. Assuming 'mailbox_id' from previous context.

    def get_mailbox_object(self):
        """Helper to get the parent Mailbox object from URL kwarg."""
        return get_object_or_404(models.Mailbox, pk=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """
        Return MailboxAccess instances for the specific Mailbox from the URL.
        Permissions should have already verified the user can access this mailbox.
        """
        mailbox = self.get_mailbox_object()  # Ensures mailbox exists and handles 404
        return (
            models.MailboxAccess.objects.filter(mailbox=mailbox)
            .select_related("user", "mailbox__domain")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        mailbox = self.get_mailbox_object()

        user_id = request.data.get("user")
        role = request.data.get("role")

        # --- Validation ---
        errors = {}
        if not user_id:
            errors["user"] = [_("This field is required.")]
        else:
            try:
                user = models.User.objects.get(pk=user_id)
                if models.MailboxAccess.objects.filter(
                    mailbox=mailbox, user=user
                ).exists():
                    errors["user"] = [_("User already has access to this mailbox.")]
            except models.User.DoesNotExist:
                errors["user"] = [_("Invalid user ID.")]

        if not role:
            errors["role"] = [_("This field is required.")]
        elif role not in models.MailboxRoleChoices.values:
            errors["role"] = [_('"%(value)s" is not a valid choice.') % {"value": role}]

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        # --- Create Instance ---
        instance = models.MailboxAccess.objects.create(
            mailbox=mailbox, user=user, role=role
        )

        read_serializer = self.get_serializer(
            instance
        )  # Uses MailboxAccessReadSerializer
        headers = self.get_success_headers(read_serializer.data)
        return Response(
            read_serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()  # This is the MailboxAccess instance

        # User of an existing access record cannot be changed.
        if "user" in request.data and request.data["user"] != str(instance.user.pk):
            return Response(
                {
                    "user": [
                        _(
                            "Cannot change the user of an existing mailbox access record. Delete and create a new one."
                        )
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        role = request.data.get("role")
        if role is None and not kwargs.get(
            "partial", False
        ):  # Role must be provided for full update
            return Response(
                {"role": [_("This field is required for a full update.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Validation for Role (if provided) ---
        if role is not None:  # Allow role to be omitted in PATCH
            if role not in models.MailboxRoleChoices.values:
                return Response(
                    {
                        "role": [
                            _('"%(value)s" is not a valid choice.') % {"value": role}
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            instance.role = role
            instance.save(update_fields=["role", "updated_at"])
        else:  # If role is not in payload for PATCH, just refresh updated_at
            instance.updated_at = timezone.now()
            instance.save(update_fields=["updated_at"])

        read_serializer = self.get_serializer(
            instance
        )  # Uses MailboxAccessReadSerializer
        return Response(read_serializer.data)
