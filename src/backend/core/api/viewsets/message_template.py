"""API ViewSet for message templates."""

from django.db.models import Q

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models
from core.api import permissions
from core.api.serializers import (
    MessageTemplateSerializer,
    ReadOnlyMessageTemplateSerializer,
)
from core.models import Mailbox, MailDomain, MessageTemplate, MessageTemplateTypeChoices


@extend_schema(tags=["message-templates"])
class MessageTemplateViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for managing message templates and signatures.

    This ViewSet provides endpoints for creating, reading, updating, and deleting
    message templates and signatures. Templates can be used for replies, new messages,
    and signatures.

    Filtering:
    - type: Filter by template type (reply, new_message, signature)
    - is_active: Filter by active status (true/false)
    - mailbox_id: Filter by mailbox UUID
    - maildomain_id: Filter by mail domain UUID
    - is_forced: Filter by forced status (true/false) - works with mailbox or maildomain
    """

    permission_classes = [permissions.IsAllowedToManageMessageTemplate]
    serializer_class = MessageTemplateSerializer
    pagination_class = None  # Disable pagination
    ordering_fields = [
        "name",
        "type",
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
        if self.action in [
            "retrieve",
            "update",
            "partial_update",
            "destroy",
            "render_template",
        ]:
            return self.get_queryset_for_detail()

        return self.get_queryset_for_list()

    def get_queryset_for_detail(self):
        """Get queryset for detail actions (retrieve, update, delete)."""
        user = self.request.user
        # Start with all templates, but filter by user permissions
        if user.is_superuser:
            queryset = MessageTemplate.objects.all()
        else:
            # Filter by user permissions for regular users
            # Get mailboxes user has direct access to
            accessible_mailboxes = Mailbox.objects.filter(accesses__user=user)
            # Get maildomains user has direct access to
            accessible_maildomains = MailDomain.objects.filter(accesses__user=user)
            # Get mailboxes user has access to through domain admin role
            mailboxes_through_domain = Mailbox.objects.filter(
                domain__accesses__user=user,
                domain__accesses__role=models.MailDomainAccessRoleChoices.ADMIN,
            )

            # Also include maildomains that the user can access through mailbox access
            accessible_maildomains_through_mailbox = MailDomain.objects.filter(
                mailbox__accesses__user=user
            )

            queryset = MessageTemplate.objects.filter(
                Q(mailbox__in=accessible_mailboxes)
                | Q(
                    mailbox__in=mailboxes_through_domain
                )  # Add access through domain admin
                | Q(maildomain__in=accessible_maildomains)
                | Q(maildomain__in=accessible_maildomains_through_mailbox)
            ).distinct()

        return queryset

    def get_queryset_for_list(self):
        """Get queryset for list action with filtering."""
        user = self.request.user

        # Start with all templates, but filter by user permissions
        if user.is_superuser:
            queryset = MessageTemplate.objects.all()
        else:
            # Filter by user permissions for regular users
            accessible_mailboxes = Mailbox.objects.filter(accesses__user=user)
            accessible_maildomains = MailDomain.objects.filter(accesses__user=user)

            # Also include maildomains that the user can access through mailbox access
            accessible_maildomains_through_mailbox = MailDomain.objects.filter(
                mailbox__accesses__user=user
            )

            queryset = MessageTemplate.objects.filter(
                Q(mailbox__in=accessible_mailboxes)
                | Q(maildomain__in=accessible_maildomains)
                | Q(maildomain__in=accessible_maildomains_through_mailbox)
            )

        # At least one of mailbox_id or maildomain_id is required (enforced by permission class)
        mailbox_id = self.request.query_params.get("mailbox_id")
        maildomain_id = self.request.query_params.get("maildomain_id")

        # Apply filters based on query parameters
        # Admin manage domain view (case 1)
        if maildomain_id:
            # When filtering by maildomain_id, only show templates the user has direct access to
            # through maildomain access (not through mailbox access)
            if not user.is_superuser:
                accessible_maildomains = MailDomain.objects.filter(accesses__user=user)
                queryset = queryset.filter(
                    maildomain_id=maildomain_id,
                    maildomain__in=accessible_maildomains,
                )
            else:
                queryset = queryset.filter(maildomain_id=maildomain_id)
        # New message creation view (case 2)
        elif mailbox_id:
            # Get the mailbox to find its domain
            try:
                mailbox = Mailbox.objects.get(id=mailbox_id)
                # When filtering by mailbox_id, show templates linked to this mailbox
                # AND templates linked to the maildomain of this mailbox (if user has access to mailbox)
                if not user.is_superuser:
                    # Check if user has access to the mailbox or its domain
                    has_mailbox_access = accessible_mailboxes.filter(
                        id=mailbox_id
                    ).exists()
                    has_domain_access = accessible_maildomains.filter(
                        id=mailbox.domain_id
                    ).exists()
                    if not (has_mailbox_access or has_domain_access):
                        return MessageTemplate.objects.none()

                    # User can see templates of both mailbox and maildomain when filtering by mailbox_id
                    # This is the expected behavior for "new message creation" scenario
                    queryset = queryset.filter(
                        Q(mailbox_id=mailbox_id) | Q(maildomain_id=mailbox.domain_id)
                    )
                else:
                    # Superuser can see all templates
                    queryset = queryset.filter(
                        Q(mailbox_id=mailbox_id) | Q(maildomain_id=mailbox.domain_id)
                    )
                # if a forced template exists, user can only see it
                forced_active_templates = queryset.filter(
                    is_forced=True, is_active=True
                )
                if forced_active_templates.exists():
                    queryset = forced_active_templates
            except Mailbox.DoesNotExist:
                return MessageTemplate.objects.none()

        # Apply additional filters
        is_forced = self.request.query_params.get("is_forced")
        template_type = self.request.query_params.get("type")
        is_active = self.request.query_params.get("is_active")

        if template_type:
            queryset = queryset.filter(
                type=MessageTemplateTypeChoices[template_type.upper()]
            )
        if is_active is not None:
            is_active_bool = is_active.lower() in ("true", "1", "yes")
            queryset = queryset.filter(is_active=is_active_bool)
        if is_forced is not None:
            is_forced_bool = is_forced.lower() in ("true", "1", "yes")
            queryset = queryset.filter(is_forced=is_forced_bool)

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
        """Render a template."""
        # TODO : access should be checked by permission class
        mailbox = Mailbox.objects.filter(id=request.query_params.get("mailbox_id")).first()

        # get template
        template = self.get_object()
        try:
            rendered = template.render_template(mailbox=mailbox, user=request.user)
            return Response(rendered)
        except (KeyError, ValueError, TypeError) as e:
            return Response(
                {"error": f"Failed to render template: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
