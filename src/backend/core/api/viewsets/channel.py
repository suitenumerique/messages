"""API ViewSet for Channel model."""

from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core import models
from core.enums import ChannelScopeLevel, ChannelTypes

from .. import permissions, serializers


@extend_schema(
    tags=["channels"], description="Manage integration channels for a mailbox"
)
class ChannelViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Channel model - allows mailbox admins to manage integration channels."""

    serializer_class = serializers.ChannelSerializer
    permission_classes = [permissions.IsMailboxAdmin]
    pagination_class = None
    lookup_field = "pk"

    @cached_property
    def mailbox(self):
        """Get mailbox from URL parameter."""
        return get_object_or_404(models.Mailbox, id=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """Get channels for the mailbox the user has admin access to.

        Defense-in-depth: filter explicitly on scope_level=MAILBOX even though
        the mailbox FK filter already excludes global/maildomain rows. Any
        accidentally force-inserted row with a non-null mailbox_id and a
        non-mailbox scope_level would be excluded here.
        """
        return models.Channel.objects.filter(
            mailbox=self.mailbox,
            scope_level=ChannelScopeLevel.MAILBOX,
        ).order_by("-created_at")

    def get_serializer_context(self):
        """Add mailbox to serializer context."""
        context = super().get_serializer_context()
        context["mailbox"] = self.mailbox
        return context

    @extend_schema(
        request=serializers.ChannelSerializer,
        responses={
            201: OpenApiResponse(
                response=serializers.ChannelSerializer,
                description="Channel created successfully",
            ),
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def get_save_kwargs(self):
        """Hook for subclasses to inject the scope-level + target FKs.

        This base class is mailbox-nested, so it always saves with
        scope_level=MAILBOX bound to the URL mailbox. ``user`` is stamped
        as the creator audit (the user FK doubles as the target on
        scope_level=user channels but is the audit creator everywhere
        else). Subclasses (e.g. UserChannelViewSet) override this to bind
        to a different scope.
        """
        return {
            "mailbox": self.mailbox,
            "scope_level": ChannelScopeLevel.MAILBOX,
            "user": self.request.user,
        }

    def create(self, request, *args, **kwargs):
        """Create a new channel.

        Always forces scope_level on save through ``get_save_kwargs``:
        non-superadmins cannot create global channels through DRF even if a
        validation bug slipped ``scope_level`` past the serializer.

        The response includes the row's ``id`` (which is also the value of
        the ``X-Channel-Id`` header on subsequent api_key calls), and on
        creation only, the freshly generated plaintext secrets — these
        cannot be retrieved later.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(**self.get_save_kwargs())
        data = serializer.data

        # Surface plaintext secrets exactly once on creation. Each generator
        # lives on the instance under `_generated_*` (see ChannelSerializer).
        # Subsequent GETs never return any of these.
        for attr, response_key in (
            ("_generated_password", "password"),
            ("_generated_api_key", "api_key"),
        ):
            value = getattr(instance, attr, None)
            if value:
                data[response_key] = value
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=serializers.ChannelSerializer,
        responses={
            200: OpenApiResponse(
                response=serializers.ChannelSerializer,
                description="Channel updated successfully",
            ),
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    def update(self, request, *args, **kwargs):
        """Update a channel."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(
        responses={
            204: OpenApiResponse(description="Channel deleted successfully"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a channel."""
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="RegeneratedApiKeyResponse",
                    fields={
                        "id": drf_serializers.CharField(
                            help_text="Channel id (also the X-Channel-Id header value).",
                        ),
                        "api_key": drf_serializers.CharField(
                            help_text=(
                                "Freshly generated plaintext api_key. Returned "
                                "ONCE on regeneration and cannot be retrieved later."
                            ),
                        ),
                    },
                ),
                description=(
                    "Returns the freshly generated plaintext api_key. The "
                    "previous secret is invalidated immediately. The plaintext "
                    "is shown ONCE and cannot be retrieved later."
                ),
            ),
            400: OpenApiResponse(description="Channel is not an api_key channel"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="regenerate-api-key")
    def regenerate_api_key(self, request, *args, **kwargs):
        """Regenerate the api_key on this channel.

        Single-active rotation: the new secret REPLACES the old one
        immediately, so any client still using the old secret will
        start failing on the next call. This is the only rotation
        flow exposed via DRF.

        Smooth (dual-active) rotation — appending a new hash without
        removing the old one so clients can migrate over a window — is
        intentionally a superadmin-only feature available via Django admin.
        """
        instance = self.get_object()
        if instance.type != ChannelTypes.API_KEY:
            raise ValidationError(
                {"type": "Only api_key channels can have their secret regenerated."}
            )

        plaintext = instance.rotate_api_key()

        return Response(
            {"id": str(instance.id), "api_key": plaintext},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["channels"],
    description="Manage personal (scope_level=user) integration channels",
)
# pylint: disable=too-many-ancestors
class UserChannelViewSet(ChannelViewSet):
    """Personal channels owned by the requesting user.

    Mounted at ``/api/v1.0/users/me/channels/``. Reuses the base class's
    create/update/destroy plumbing — the only differences are the queryset
    filter, the permission class, and the save kwargs that bind the row
    to ``scope_level=user``.
    """

    permission_classes = [permissions.IsAuthenticated]

    @cached_property
    def mailbox(self):
        """No mailbox in this nesting — explicitly disable the parent's
        cached property to make accidental access loud."""
        return None

    def get_queryset(self):
        return models.Channel.objects.filter(
            user=self.request.user,
            scope_level=ChannelScopeLevel.USER,
        ).order_by("-created_at")

    def get_serializer_context(self):
        # Skip the mailbox-context branch in ChannelSerializer.validate so
        # the serializer's validation falls back to the user_channel path.
        # The user-scope serializer.save() below still hardcodes scope_level
        # + user, so a body-supplied user= would be ignored regardless.
        context = super(ChannelViewSet, self).get_serializer_context()
        context["user_channel"] = True
        return context

    def get_save_kwargs(self):
        return {
            "user": self.request.user,
            "scope_level": ChannelScopeLevel.USER,
        }
