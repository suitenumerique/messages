"""API ViewSet for message templates."""

from django.db.models import Case, IntegerField, Q, When
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import mixins, viewsets
from rest_framework.generics import get_object_or_404

from core.api import permissions
from core.api.serializers import (
    MessageTemplateSerializer,
    ReadMessageTemplateSerializer,
)
from core.api.viewsets.mixins import MessageTemplateResponseMixin
from core.models import (
    Mailbox,
    MessageTemplate,
    MessageTemplateTypeChoices,
)

BODIES_PARAMETER = OpenApiParameter(
    name="bodies",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description=(
        "Comma-separated list of body fields to include in the response. "
        "Allowed values: raw, html, text. "
        "Example: ?bodies=raw,html"
    ),
    required=False,
)


# pylint: disable=too-many-ancestors
class MailboxMessageTemplateViewSet(
    MessageTemplateResponseMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet for managing message templates for a mailbox."""

    permission_classes = [permissions.IsMailboxAdmin]
    serializer_class = MessageTemplateSerializer
    lookup_field = "pk"
    pagination_class = None

    def get_permissions(self):
        """Get permissions for the viewset."""
        if self.action in ["list", "retrieve"]:
            return [permissions.HasAccessToMailbox()]
        return super().get_permissions()

    @cached_property
    def mailbox(self):
        """Get mailbox from URL parameter."""
        return get_object_or_404(Mailbox, id=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """Get message templates for a mailbox the user has access to."""
        if self.action == "retrieve":
            queryset = MessageTemplate.objects.filter(
                Q(mailbox=self.mailbox) | Q(maildomain=self.mailbox.domain)
            )
        else:
            queryset = MessageTemplate.objects.filter(mailbox=self.mailbox)
        template_types = [
            MessageTemplateTypeChoices[template_type.upper()]
            for template_type in self.request.query_params.getlist("type")
            if template_type.upper() in MessageTemplateTypeChoices.names
        ]
        if template_types:
            queryset = queryset.filter(type__in=template_types)
        return queryset

    def get_serializer_context(self):
        """Add mailbox to serializer context."""
        context = super().get_serializer_context()
        context["mailbox"] = self.mailbox
        return context

    @extend_schema(
        responses=ReadMessageTemplateSerializer(many=True),
        description="List message templates for a mailbox.",
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                enum=[c[1] for c in MessageTemplateTypeChoices.choices],
                many=True,
            ),
            BODIES_PARAMETER,
        ],
    )
    def list(self, request, *args, **kwargs):
        """List message templates for a mailbox."""
        return super().list(request, *args, **kwargs)

    @extend_schema(
        parameters=[BODIES_PARAMETER],
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a message template."""
        return super().retrieve(request, *args, **kwargs)


class AvailableMailboxMessageTemplateViewSet(
    mixins.ListModelMixin, viewsets.GenericViewSet
):
    """ViewSet for getting message templates for a mailbox."""

    permission_classes = [
        permissions.HasAccessToMailbox,
    ]
    serializer_class = ReadMessageTemplateSerializer
    pagination_class = None
    ordering_fields = [
        "name",
        "type",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Get message templates active for a mailbox and its domain.
        If a forced template exists for a template type, user can only see it.
        Mailbox-level templates are returned before domain-level templates."""
        mailbox = get_object_or_404(Mailbox, id=self.kwargs["mailbox_id"])
        # get active message templates for mailbox and its domain
        queryset = MessageTemplate.objects.filter(
            Q(mailbox=mailbox) | Q(maildomain=mailbox.domain)
        ).filter(is_active=True)

        # apply additional filters
        template_type = self.request.query_params.get("type")
        if template_type is not None:
            queryset = queryset.filter(
                type=MessageTemplateTypeChoices[template_type.upper()]
            )
            # if a forced template exists, user can only see it
            forced_active_templates = queryset.filter(is_forced=True, is_active=True)
            if forced_active_templates.exists():
                queryset = forced_active_templates

        # Order by scope: mailbox templates first, then domain templates
        # This ensures mailbox-level defaults take priority over domain-level
        queryset = queryset.annotate(
            scope_order=Case(
                When(mailbox__isnull=False, then=0),  # Mailbox templates first
                default=1,  # Domain templates second
                output_field=IntegerField(),
            )
        ).order_by("scope_order", "-is_default", "-created_at")

        return queryset.distinct()

    @extend_schema(
        responses=ReadMessageTemplateSerializer(many=True),
        description="List message templates.",
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
        """List message templates."""
        return super().list(request, *args, **kwargs)
