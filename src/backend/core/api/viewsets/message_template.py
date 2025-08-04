"""API ViewSet for message templates."""

from django.conf import settings
from django.db.models import Q

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api import permissions
from core.api.serializers import (
    MessageTemplateSerializer,
    ReadOnlyMessageTemplateSerializer,
)
from core.models import Mailbox, MailDomain, MessageTemplate, MessageTemplateKindChoices


@extend_schema(tags=["message-templates"])
class MessageTemplateViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for managing message templates and signatures.

    This ViewSet provides endpoints for creating, reading, updating, and deleting
    message templates and signatures. Templates can be used for replies, new messages,
    and signatures.

    Filtering:
    - kind: Filter by template kind (reply, new_message, signature)
    - is_active: Filter by active status (true/false)
    - mailbox_id: Filter by mailbox UUID
    - maildomain_id: Filter by mail domain UUID
    - is_default: Filter by default status (true/false) - works with mailbox or maildomain
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageTemplateSerializer
    pagination_class = None  # Disable pagination
    ordering_fields = [
        "name",
        "kind",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        """Get serializer class based on action."""
        if self.action == "list":
            return ReadOnlyMessageTemplateSerializer
        return MessageTemplateSerializer

    def get_queryset(self):
        """Get queryset filtered by user permissions and custom filters."""
        # For detail actions (retrieve, update, delete), use different logic
        if self.action in ["retrieve", "update", "partial_update", "destroy"]:
            return self.get_queryset_for_detail()

        return self.get_queryset_for_list()

    def get_queryset_for_detail(self):
        """Get queryset for detail actions (retrieve, update, delete)."""
        user = self.request.user

        if user.is_superuser:
            return MessageTemplate.objects.all()

        # Filter by user permissions for regular users
        accessible_mailboxes = Mailbox.objects.filter(accesses__user=user)
        accessible_maildomains = MailDomain.objects.filter(
            Q(accesses__user=user) | Q(mailbox__accesses__user=user)
        )

        return MessageTemplate.objects.filter(
            Q(template_mailboxes__mailbox__in=accessible_mailboxes)
            | Q(template_maildomains__maildomain__in=accessible_maildomains)
        ).distinct()

    def get_queryset_for_list(self):
        """Get queryset for list action with filtering."""
        user = self.request.user

        # At least one of mailbox_id or maildomain_id is required
        mailbox_id = self.request.query_params.get("mailbox_id")
        maildomain_id = self.request.query_params.get("maildomain_id")
        if not mailbox_id and not maildomain_id:
            return MessageTemplate.objects.none()

        # filter by user permissions
        if user.is_superuser:
            queryset = MessageTemplate.objects.all()
        elif maildomain_id:
            # CASE 1: admin manage domain
            accessible_maildomains = MailDomain.objects.filter(accesses__user=user)
            queryset = MessageTemplate.objects.filter(
                template_maildomains__maildomain_id__in=accessible_maildomains
            )
        elif mailbox_id:
            # CASE 2: user write a new message, need to get all templates linked to
            # mailbox AND templates linked to the maildomain of the mailbox
            accessible_mailboxes = Mailbox.objects.filter(accesses__user=user)
            accessible_maildomains = MailDomain.objects.filter(
                mailbox__accesses__user=user
            )
            queryset = MessageTemplate.objects.filter(
                Q(template_mailboxes__mailbox_id__in=accessible_mailboxes)
                | Q(template_maildomains__maildomain_id__in=accessible_maildomains)
            )

        # handle filters
        if maildomain_id:
            queryset = queryset.filter(
                template_maildomains__maildomain_id=maildomain_id
            )
        elif mailbox_id:
            # Get the mailbox to find its domain
            try:
                mailbox = Mailbox.objects.get(id=mailbox_id)
                # Filter by templates linked to this mailbox OR to the maildomain of this mailbox
                queryset = queryset.filter(
                    Q(template_mailboxes__mailbox_id=mailbox_id)
                    | Q(template_maildomains__maildomain_id=mailbox.domain_id)
                )
            except Mailbox.DoesNotExist:
                return MessageTemplate.objects.none()
        is_default = self.request.query_params.get("is_default")
        kind = self.request.query_params.get("kind")
        is_active = self.request.query_params.get("is_active")

        if kind:
            queryset = queryset.filter(kind=MessageTemplateKindChoices[kind.upper()])
        if is_active is not None:
            is_active_bool = is_active.lower() in ("true", "1", "yes")
            queryset = queryset.filter(is_active=is_active_bool)
        if is_default is not None:
            is_default_bool = is_default.lower() in ("true", "1", "yes")
            queryset = queryset.filter(
                Q(template_mailboxes__is_default=is_default_bool)
                | Q(template_maildomains__is_default=is_default_bool)
            )

        return queryset.distinct()

    def get_serializer_context(self):
        """Add mailbox_id and maildomain_id to serializer context."""
        context = super().get_serializer_context()
        context["mailbox_id"] = self.request.query_params.get("mailbox_id")
        context["maildomain_id"] = self.request.query_params.get("maildomain_id")
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
    def render_template(self, request, pk=None):  # pylint: disable=unused-argument
        """Render a template with the provided mailbox uuid or maildomain uuid."""

        # at least one of mailbox_id or maildomain_id is required
        mailbox_id = request.query_params.get("mailbox_id")
        maildomain_id = request.query_params.get("maildomain_id")
        if not mailbox_id and not maildomain_id:
            return Response(
                {"error": "At least one of mailbox_id or maildomain_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # get template
        template = self.get_object()
        user = request.user
        context = {
            "full_name": user.full_name,
        }
        schema = settings.SCHEMA_CUSTOM_ATTRIBUTES_USER
        schema_properties = schema.get("properties", {})

        for field_key in schema_properties.keys():
            context[field_key] = user.custom_attributes.get(field_key, "")

        try:
            rendered = template.render_template(context)
            return Response(rendered)
        except (KeyError, ValueError, TypeError) as e:
            return Response(
                {"error": f"Failed to render template: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
