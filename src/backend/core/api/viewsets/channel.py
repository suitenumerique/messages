"""Admin ViewSet for Channel management."""

from django.db.models import F, Q
from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models
from core.api import permissions as core_permissions
from core.api import serializers as core_serializers


@extend_schema(tags=["channels"])
class AdminChannelViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for managing Channels.
    Endpoint: /channels/
    """

    serializer_class = core_serializers.ChannelSerializer
    permission_classes = [core_permissions.IsAuthenticated]

    def get_queryset(self):
        """Restrict results to channels the user has admin access to."""
        user = self.request.user
        if not user or not user.is_authenticated:
            return models.Channel.objects.none()

        if user.is_superuser:
            return models.Channel.objects.all().order_by("-created_at")

        # Only mailbox admins can access channels
        return models.Channel.objects.filter(
            Q(mailbox__accesses__user=user, mailbox__accesses__role=models.MailboxRoleChoices.ADMIN) | 
            Q(maildomain__accesses__user=user, maildomain__accesses__role=models.MailDomainAccessRoleChoices.ADMIN)
        ).distinct().order_by("-created_at")
