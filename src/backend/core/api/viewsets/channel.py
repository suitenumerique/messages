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
from core.enums import ChannelScopeLevel, ChannelTypes, WebhookAuthMethod

from .. import permissions, serializers


def _attach_credential(data: dict, channel: models.Channel) -> None:
    """Add the channel's freshly-minted credential to ``data``.

    Response key by credential *kind* (the channel's type + auth_method
    already tell the caller which to expect, so the keys are shared
    across channel types rather than prefixed per type):

      - ``api_key`` channels → ``api_key`` (plaintext, one-shot)
      - ``webhook`` channels (``auth_method='jwt'``) → ``secret``
        (the raw root from ``encrypted_settings["secret"]``)
      - ``webhook`` channels (``auth_method='api_key'``) → ``api_key``
        (HMAC-derived from the root) — same key name as api_key
        channels: both are an API key presented in a request header

    For api_key channels the plaintext is one-shot (we only store the
    hash), so callers must stash it on ``instance._generated_api_key``
    via the serializer's create flow. For webhook channels the raw root
    sits in ``encrypted_settings["secret"]`` and ``get_webhook_api_key``
    derives lazily — both readable straight off ``channel``.
    """
    if channel.type == ChannelTypes.API_KEY:
        plaintext = getattr(channel, "_generated_api_key", None)
        if plaintext:
            data["api_key"] = plaintext
        return
    # Webhook channels: the (jwt→secret / api_key→derived) rule lives on the
    # model so this and the Django-admin regenerate view can't drift.
    credential = channel.get_webhook_surfaced_credential()
    if credential:
        key, value = credential
        data[key] = value


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

    @extend_schema(
        request=serializers.ChannelSerializer,
        responses={
            201: OpenApiResponse(
                response=serializers.ChannelCreateResponseSerializer,
                description=(
                    "Channel created successfully. The response carries the "
                    "one-time plaintext credential (api_key / secret) which "
                    "is never returned again."
                ),
            ),
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
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

        # Surface the freshly-minted plaintext credential exactly once on
        # creation — subsequent GETs never return it.
        _attach_credential(data, instance)
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
                    name="RegeneratedSecretResponse",
                    fields={
                        "id": drf_serializers.CharField(help_text="Channel id."),
                        "api_key": drf_serializers.CharField(
                            required=False,
                            help_text=(
                                "Present for ``api_key`` channels and "
                                "webhook channels with "
                                "``auth_method='api_key'`` — the plaintext "
                                "API key. api_key channels send it as "
                                "``X-API-Key`` on inbound API calls; api_key "
                                "webhooks present it as ``Authorization: "
                                "Bearer``. Returned ONCE; for api_key webhooks "
                                "it changes whenever the root rotates."
                            ),
                        ),
                        "secret": drf_serializers.CharField(
                            required=False,
                            help_text=(
                                "Present for webhook channels with "
                                "``auth_method='jwt'`` — the freshly "
                                "minted root receivers use to verify the "
                                "HMAC sig and JWT."
                            ),
                        ),
                    },
                ),
                description=(
                    "Rotates the channel's secret. Single-active: the "
                    "previous credential is invalidated immediately. "
                    "The response carries exactly one of ``api_key`` / "
                    "``secret`` matching the channel's type (and, for "
                    "webhooks, its current ``auth_method``)."
                ),
            ),
            400: OpenApiResponse(description="Channel type has no rotatable secret"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Channel not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="regenerate-secret")
    def regenerate_secret(self, request, *args, **kwargs):
        """Rotate this channel's secret.

        Type-agnostic entry point: ``Channel.rotate_secret`` dispatches
        on ``self.type`` and persists the new credential in the
        appropriate storage shape (hash for ``api_key``, plaintext for
        ``webhook``). Channel types without a rotatable secret raise
        and surface as HTTP 400.

        Single-active rotation. Smooth (dual-active) rotation —
        appending a new hash without removing the old one so clients
        can migrate over a window — is intentionally a superadmin-only
        feature available via Django admin.
        """
        instance = self.get_object()

        # Guard before rotating: a webhook channel whose auth_method isn't
        # one ``_attach_credential`` knows how to surface would have its old
        # secret invalidated by ``rotate_secret`` while the freshly minted
        # one is dropped from the response — permanently bricking the
        # webhook with no way to learn the new secret. Reject up front so
        # rotation only runs when we can hand the result back.
        if (
            instance.type == ChannelTypes.WEBHOOK
            and (instance.settings or {}).get("auth_method") not in WebhookAuthMethod
        ):
            raise ValidationError(
                {
                    "settings": (
                        "webhook settings.auth_method must be 'jwt' or "
                        "'api_key' before the secret can be rotated."
                    )
                }
            )

        try:
            plaintext = instance.rotate_secret()
        except ValueError as exc:
            # Static message — don't reflect the internal exception text
            # back to the API caller.
            raise ValidationError(
                {"type": "This channel type does not support secret rotation."}
            ) from exc

        # api_key channels store only the hash; stash the just-minted
        # plaintext on the instance so ``_attach_credential`` can find
        # it (the field is read-once and never persisted).
        if instance.type == ChannelTypes.API_KEY:
            # pylint: disable-next=protected-access
            instance._generated_api_key = plaintext  # noqa: SLF001

        payload: dict = {"id": str(instance.id)}
        _attach_credential(payload, instance)
        return Response(payload, status=status.HTTP_200_OK)


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
