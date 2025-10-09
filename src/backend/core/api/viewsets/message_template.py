"""API ViewSet for message templates."""

from django.db.models import Q
from django.utils.functional import cached_property

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from core.api import permissions
from core.api.serializers import (
    MessageTemplateSerializer,
    ReadOnlyMessageTemplateSerializer,
)
from core.models import (
    Mailbox,
    MessageTemplate,
    MessageTemplateTypeChoices,
)


class MailboxMessageTemplateViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet for retrieving and rendering message templates for a mailbox."""

    permission_classes = [permissions.HasAccessToMailbox]
    serializer_class = MessageTemplateSerializer
    lookup_field = "pk"
    pagination_class = None

    @cached_property
    def mailbox(self):
        """Get mailbox from URL parameter."""
        return get_object_or_404(Mailbox, id=self.kwargs["mailbox_id"])

    def get_queryset(self):
        """Get message templates for a mailbox the user has access to."""
        if self.action == "render_template":
            return MessageTemplate.objects.filter(
                Q(mailbox=self.mailbox) | Q(maildomain=self.mailbox.domain)
            )
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
        responses={
            200: OpenApiResponse(
                description="Template rendered with provided context",
                response={
                    "type": "object",
                    "properties": {
                        "html_body": {"type": "string"},
                        "text_body": {"type": "string"},
                    },
                },
            ),
            404: OpenApiResponse(description="Template not found"),
        },
        description="Render a template with the provided context variables.",
    )
    @action(detail=True, methods=["get"], url_path="render")
    def render_template(self, request, mailbox_id, pk=None):  # pylint: disable=unused-argument
        """Render a template."""
        template = self.get_object()
        try:
            rendered = template.render_template(mailbox=self.mailbox, user=request.user)
            return Response(rendered)
        except (KeyError, ValueError, TypeError) as e:
            return Response(
                {"error": f"Failed to render template: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class AvailableMailboxMessageTemplateViewSet(
    mixins.ListModelMixin, viewsets.GenericViewSet
):
    """ViewSet for getting message templates for a mailbox."""

    permission_classes = [
        permissions.HasAccessToMailbox,
    ]
    serializer_class = ReadOnlyMessageTemplateSerializer
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
        If a forced template exists for a template type, user can only see it."""
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

        return queryset.distinct()

    @extend_schema(
        responses=ReadOnlyMessageTemplateSerializer(many=True),
        description="List message templates.",
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
        """List message templates."""
        return super().list(request, *args, **kwargs)
