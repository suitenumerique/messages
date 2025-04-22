"""API endpoints"""
# pylint: disable=too-many-lines

import logging
import re
import smtplib
import time

from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models as db
from django.utils import timezone

import rest_framework as drf
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core import enums, models
from core.formats.rfc5322.composer import compose_email

from . import permissions, serializers

logger = logging.getLogger(__name__)

ITEM_FOLDER = "item"
UUID_REGEX = (
    r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"
)
FILE_EXT_REGEX = r"(\.[a-zA-Z0-9]+)?$"
MEDIA_STORAGE_URL_PATTERN = re.compile(
    f"{settings.MEDIA_URL:s}(?P<key>{ITEM_FOLDER:s}/(?P<pk>{UUID_REGEX:s})/.*{FILE_EXT_REGEX:s})$"
)

# pylint: disable=too-many-ancestors


class NestedGenericViewSet(viewsets.GenericViewSet):
    """
    A generic Viewset aims to be used in a nested route context.
    e.g: `/api/v1.0/resource_1/<resource_1_pk>/resource_2/<resource_2_pk>/`

    It allows to define all url kwargs and lookup fields to perform the lookup.
    """

    lookup_fields: list[str] = ["pk"]
    lookup_url_kwargs: list[str] = []

    def __getattribute__(self, item):
        """
        This method is overridden to allow to get the last lookup field or lookup url kwarg
        when accessing the `lookup_field` or `lookup_url_kwarg` attribute. This is useful
        to keep compatibility with all methods used by the parent class `GenericViewSet`.
        """
        if item in ["lookup_field", "lookup_url_kwarg"]:
            return getattr(self, item + "s", [None])[-1]

        return super().__getattribute__(item)

    def get_queryset(self):
        """
        Get the list of items for this view.

        `lookup_fields` attribute is enumerated here to perform the nested lookup.
        """
        queryset = super().get_queryset()

        # The last lookup field is removed to perform the nested lookup as it corresponds
        # to the object pk, it is used within get_object method.
        lookup_url_kwargs = (
            self.lookup_url_kwargs[:-1]
            if self.lookup_url_kwargs
            else self.lookup_fields[:-1]
        )

        filter_kwargs = {}
        for index, lookup_url_kwarg in enumerate(lookup_url_kwargs):
            if lookup_url_kwarg not in self.kwargs:
                raise KeyError(
                    f"Expected view {self.__class__.__name__} to be called with a URL "
                    f'keyword argument named "{lookup_url_kwarg}". Fix your URL conf, or '
                    "set the `.lookup_fields` attribute on the view correctly."
                )

            filter_kwargs.update(
                {self.lookup_fields[index]: self.kwargs[lookup_url_kwarg]}
            )

        return queryset.filter(**filter_kwargs)


class SerializerPerActionMixin:
    """
    A mixin to allow to define serializer classes for each action.

    This mixin is useful to avoid to define a serializer class for each action in the
    `get_serializer_class` method.

    Example:
    ```
    class MyViewSet(SerializerPerActionMixin, viewsets.GenericViewSet):
        serializer_class = MySerializer
        list_serializer_class = MyListSerializer
        retrieve_serializer_class = MyRetrieveSerializer
    ```
    """

    def get_serializer_class(self):
        """
        Return the serializer class to use depending on the action.
        """
        if serializer_class := getattr(self, f"{self.action}_serializer_class", None):
            return serializer_class
        return super().get_serializer_class()


class Pagination(drf.pagination.PageNumberPagination):
    """Pagination to display no more than 100 objects per page sorted by creation date."""

    ordering = "-created_on"
    max_page_size = 200
    page_size_query_param = "page_size"


class ResourceAccessViewsetMixin:
    """Mixin with methods common to all access viewsets."""

    def get_permissions(self):
        """User only needs to be authenticated to list resource accesses"""
        if self.action == "list":
            permission_classes = [permissions.IsAuthenticated]
        else:
            return super().get_permissions()

        return [permission() for permission in permission_classes]

    def get_serializer_context(self):
        """Extra context provided to the serializer class."""
        context = super().get_serializer_context()
        context["resource_id"] = self.kwargs["resource_id"]
        return context

    def get_queryset(self):
        """Return the queryset according to the action."""
        queryset = super().get_queryset()
        queryset = queryset.filter(
            **{self.resource_field_name: self.kwargs["resource_id"]}
        )

        if self.action == "list":
            user = self.request.user
            teams = user.teams
            user_roles_query = (
                queryset.filter(
                    db.Q(user=user) | db.Q(team__in=teams),
                    **{self.resource_field_name: self.kwargs["resource_id"]},
                )
                .values(self.resource_field_name)
                .annotate(roles_array=ArrayAgg("role"))
                .values("roles_array")
            )

            # Limit to resource access instances related to a resource THAT also has
            # a resource access
            # instance for the logged-in user (we don't want to list only the resource
            # access instances pointing to the logged-in user)
            queryset = (
                queryset.filter(
                    db.Q(**{f"{self.resource_field_name}__accesses__user": user})
                    | db.Q(
                        **{f"{self.resource_field_name}__accesses__team__in": teams}
                    ),
                    **{self.resource_field_name: self.kwargs["resource_id"]},
                )
                .annotate(user_roles=db.Subquery(user_roles_query))
                .distinct()
            )
        return queryset

    def destroy(self, request, *args, **kwargs):
        """Forbid deleting the last owner access"""
        instance = self.get_object()
        resource = getattr(instance, self.resource_field_name)

        # Check if the access being deleted is the last owner access for the resource
        if (
            instance.role == "owner"
            and resource.accesses.filter(role="owner").count() == 1
        ):
            return drf.response.Response(
                {"detail": "Cannot delete the last owner access for the resource."},
                status=drf.status.HTTP_403_FORBIDDEN,
            )

        return super().destroy(request, *args, **kwargs)

    def perform_update(self, serializer):
        """Check that we don't change the role if it leads to losing the last owner."""
        instance = serializer.instance

        # Check if the role is being updated and the new role is not "owner"
        if (
            "role" in self.request.data
            and self.request.data["role"] != models.RoleChoices.OWNER
        ):
            resource = getattr(instance, self.resource_field_name)
            # Check if the access being updated is the last owner access for the resource
            if (
                instance.role == models.RoleChoices.OWNER
                and resource.accesses.filter(role=models.RoleChoices.OWNER).count() == 1
            ):
                message = "Cannot change the role to a non-owner role for the last owner access."
                raise drf.exceptions.PermissionDenied({"detail": message})

        serializer.save()


class ItemMetadata(drf.metadata.SimpleMetadata):
    """Custom metadata class to add information"""

    def determine_metadata(self, request, view):
        """Add language choices only for the list endpoint."""
        simple_metadata = super().determine_metadata(request, view)

        if request.path.endswith("/items/"):
            simple_metadata["actions"]["POST"]["language"] = {
                "choices": [
                    {"value": code, "display_name": name}
                    for code, name in enums.ALL_LANGUAGES.items()
                ]
            }
        return simple_metadata


class ConfigView(drf.views.APIView):
    """API ViewSet for sharing some public settings."""

    permission_classes = [AllowAny]

    def get(self, request):
        """
        GET /api/v1.0/config/
            Return a dictionary of public settings.
        """
        array_settings = [
            "ENVIRONMENT",
            "FRONTEND_THEME",
            "MEDIA_BASE_URL",
            "POSTHOG_KEY",
            "LANGUAGES",
            "LANGUAGE_CODE",
            "SENTRY_DSN",
        ]
        dict_settings = {}
        for setting in array_settings:
            if hasattr(settings, setting):
                dict_settings[setting] = getattr(settings, setting)

        return drf.response.Response(dict_settings)


class UserViewSet(viewsets.ViewSet):
    """ViewSet for User model."""

    serializer_class = serializers.UserSerializer
    permission_classes = [permissions.IsSelf]

    @drf.decorators.action(
        detail=False,
        methods=["get"],
        url_name="me",
        url_path="me",
        permission_classes=[permissions.IsAuthenticated],
    )
    def get_me(self, request):
        """
        Return information on currently logged user
        """
        context = {"request": request}
        return drf.response.Response(
            self.serializer_class(request.user, context=context).data
        )


class MailboxViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    """ViewSet for Mailbox model."""

    serializer_class = serializers.MailboxSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        """Restrict results to the current user's mailboxes."""
        accesses = self.request.user.mailbox_accesses.all()
        return models.Mailbox.objects.filter(
            id__in=accesses.values_list("mailbox_id", flat=True)
        )


class ThreadViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    """ViewSet for Thread model."""

    serializer_class = serializers.ThreadSerializer
    permission_classes = [permissions.IsAllowedToAccessMailbox]

    def get_queryset(self):
        """Restrict results to threads of the current user's mailboxes."""
        accesses = self.request.user.mailbox_accesses.all()
        return models.Thread.objects.filter(
            mailbox__id__in=accesses.values_list("mailbox_id", flat=True)
        ).order_by("-updated_at")


class MessageViewSet(
    viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin
):
    """ViewSet for Message model."""

    serializer_class = serializers.MessageSerializer
    permission_classes = [permissions.IsAllowedToAccessMailbox]

    def get_queryset(self):
        """Restrict results to messages of the current user's mailbox."""
        return models.Message.objects.filter(
            thread__id=self.request.GET.get("thread_id")
        ).order_by("-received_at")


@extend_schema(
    tags=["messages"],
    request=inline_serializer(
        name="MessageCreateRequest",
        fields={
            "parentId": drf_serializers.UUIDField(
                required=False,
                allow_null=True,
                help_text="Message ID if replying to an existing message",
            ),
            "senderId": drf_serializers.UUIDField(
                required=True,
                help_text="Mailbox ID to use as sender",
            ),
            "subject": drf_serializers.CharField(
                required=True, help_text="Subject of the message"
            ),
            "htmlBody": drf_serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="HTML content of the message",
            ),
            "textBody": drf_serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Plain text content of the message",
            ),
            "to": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=True,
                help_text="List of recipient email addresses",
            ),
            "cc": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=False,
                default=list,
                help_text="List of CC recipient email addresses",
            ),
            "bcc": drf_serializers.ListField(
                child=drf_serializers.EmailField(),
                required=False,
                default=list,
                help_text="List of BCC recipient email addresses",
            ),
        },
    ),
    responses={
        201: serializers.MessageSerializer,
        400: OpenApiExample(
            "Validation Error", value={"detail": "Parent message does not exist."}
        ),
        403: OpenApiExample(
            "Permission Error",
            value={"detail": "You do not have permission to perform this action."},
        ),
    },
    description="""
    Create a new message or reply to an existing message.
    
    This endpoint allows you to:
    - Create a new message in a new thread
    - Reply to an existing message in an existing thread
    
    At least one of htmlBody or textBody must be provided.
    """,
    examples=[
        OpenApiExample(
            "New Message",
            value={
                "subject": "Hello",
                "textBody": "This is a test message",
                "to": ["recipient@example.com"],
                "cc": ["cc@example.com"],
                "bcc": ["bcc@example.com"],
            },
        ),
        OpenApiExample(
            "Reply",
            value={
                "parentId": "123e4567-e89b-12d3-a456-426614174000",
                "subject": "Re: Hello",
                "textBody": "This is a reply",
                "to": ["recipient@example.com"],
            },
        ),
    ],
)
class MessageCreateView(APIView):
    """Create a new message or reply to an existing message.

    This endpoint is used to create a new message or reply to an existing message.

    POST /api/v1.0/message-create/ with expected data:
        - parentId: str (message id if reply, None if first message)
        - senderId: str (contact id of the sender)
        - subject: str
        - htmlBody: str
        - textBody: str
        - to: list[str]
        - cc: list[str]
        - bcc: list[str]
        Return newly created message
    """

    permission_classes = [permissions.IsAllowedToCreateMessage]
    mailbox = None
    sender_contact = None

    def post(self, request):
        """Perform the create action."""

        subject = request.data.get("subject")

        # Then get the parent message if it's a reply
        parent_id = request.data.get("parentId")
        if parent_id:
            try:
                # Reply to an existing message in a thread
                reply_to_message = models.Message.objects.get(id=parent_id)
                thread = reply_to_message.thread
            except models.Message.DoesNotExist as exc:
                raise drf.exceptions.ValidationError(
                    "Parent message does not exist."
                ) from exc
        else:
            # Create a new thread
            thread = models.Thread.objects.create(
                # self.mailbox is set in the permission class
                mailbox=self.mailbox,
                subject=subject,
                # TODO: enhance to use htmlBody if is the only one provided
                snippet=request.data.get("textBody")[:100],
                is_read=True,
            )

        recipients = {
            "to": request.data.get("to") or [],
            "cc": request.data.get("cc") or [],
            "bcc": request.data.get("bcc") or [],
        }

        # Create contacts if they don't exist
        contacts = {
            kind: [
                models.Contact.objects.get_or_create(email=email)[0] for email in emails
            ]
            for kind, emails in recipients.items()
        }

        # Assemble the raw mime message
        # BCC recipients are not included in the raw mime message
        raw_mime = compose_email(
            {
                "from": [
                    {
                        "name": self.sender_contact.name,
                        "email": self.sender_contact.email,
                    }
                ],
                "to": [
                    {"name": contact.name, "email": contact.email}
                    for contact in contacts["to"]
                ],
                "cc": [
                    {"name": contact.name, "email": contact.email}
                    for contact in contacts["cc"]
                ],
                "subject": subject,
                "textBody": [request.data["textBody"]]
                if request.data.get("textBody")
                else [],
                "htmlBody": [request.data["htmlBody"]]
                if request.data.get("htmlBody")
                else [],
            }
        )

        # Create message instance with all data
        message = models.Message.objects.create(
            thread=thread,
            sender=self.sender_contact,
            raw_mime=raw_mime,
            subject=subject,
            created_at=timezone.now(),
            read_at=timezone.now(),
            mta_sent=False,
        )

        for kind, cts in contacts.items():
            for contact in cts:
                models.MessageRecipient.objects.create(
                    message=message,
                    contact=contact,
                    type=kind,
                )

        # TODO: Add DKIM signature
        raw_mime_signed = raw_mime

        # TODO: Sending to the MTA should be done asynchronously. Move this to a Celery task

        if not settings.MTA_OUT_HOST:
            logger.warning("MTA_OUT_HOST is not set, skipping message sending")
        else:
            for _ in range(5):
                try:
                    client = smtplib.SMTP(
                        settings.MTA_OUT_HOST.split(":")[0],
                        int(settings.MTA_OUT_HOST.split(":")[1]),
                    )
                    client.ehlo()
                    client.starttls()
                    client.ehlo()

                    # Authenticate
                    if (
                        settings.MTA_OUT_SMTP_USERNAME
                        and settings.MTA_OUT_SMTP_PASSWORD
                    ):
                        client.login(
                            settings.MTA_OUT_SMTP_USERNAME,
                            settings.MTA_OUT_SMTP_PASSWORD,
                        )

                    envelope_to = [
                        contact.email
                        for contact in contacts["to"] + contacts["cc"] + contacts["bcc"]
                    ]
                    envelope_from = message.sender.email
                    smtp_response = client.sendmail(
                        envelope_from, envelope_to, raw_mime_signed
                    )
                    logger.info("SMTP response: %s", smtp_response)

                    message.mta_sent = True
                    message.sent_at = timezone.now()
                    message.save()

                    break
                except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
                    logger.error("Error sending message to the MTA: %s", e)
                    time.sleep(1)

        return Response(
            serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED
        )
