"""API endpoints"""
# pylint: disable=too-many-lines

import logging
import re
import email
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.search import TrigramSimilarity
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import models as db
from django.db import transaction
from django.db.models.expressions import RawSQL

import magic
import rest_framework as drf
from rest_framework import filters, status, viewsets
from rest_framework import response as drf_response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import UserRateThrottle

from core import enums, models

from . import permissions, serializers, utils

import jwt
import hashlib

logger = logging.getLogger(__name__)

ITEM_FOLDER = "item"
UUID_REGEX = (
    r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"
)
FILE_EXT_REGEX = r"(\.[a-zA-Z0-9]+)?$"
MEDIA_STORAGE_URL_PATTERN = re.compile(
    f"{settings.MEDIA_URL:s}"
    f"(?P<key>{ITEM_FOLDER:s}/(?P<pk>{UUID_REGEX:s})/.*{FILE_EXT_REGEX:s})$"
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


class MTAViewSet(viewsets.GenericViewSet):
    """ViewSet for MTA-related endpoints"""
    
    # Since we'll use JWT token validation, we can make the endpoint public
    permission_classes = [AllowAny]  
    authentication_classes = []
    
    @drf.decorators.action(
        detail=False,
        methods=["post"],
        url_path="incoming_mail",
        parser_classes=[drf.parsers.BaseParser],  # To handle raw email data
    )
    def incoming_mail(self, request):
        """Handle incoming email from MTA"""
        
        # Validate content type
        if request.content_type != "message/rfc822":
            return drf.response.Response(
                {"detail": "Content-Type must be message/rfc822"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate JWT token
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return drf.response.Response(
                {"detail": "Authorization header missing"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(
                jwt_token, 
                settings.MDA_API_SECRET, 
                algorithms=["HS256"]
            )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            return drf.response.Response(
                {"detail": str(e)},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Validate email hash
        raw_data = request.body
        email_hash = hashlib.sha256(raw_data).hexdigest()
        if email_hash != payload["email_hash"]:
            return drf.response.Response(
                {"detail": "Invalid email hash"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        logger.info(
            "Raw email received: %d bytes for %s",
            len(raw_data),
            payload["original_recipients"][0:4]
        )

        # Parse the email message
        email_message = email.message_from_bytes(raw_data)

        # Print details of the email message
        logger.info(f"Subject: {email_message['Subject']}")
        logger.info(f"From: {email_message['From']}")
        logger.info(f"To: {email_message['To']}")

        # Walk mime parts and display their metadata
        for part in email_message.walk():
            logger.info(f"Part: {part.get_content_type()}")
            logger.info(f" Content-Disposition: {part.get('Content-Disposition')}")
        
        
        return drf.response.Response({"status": "ok"})
