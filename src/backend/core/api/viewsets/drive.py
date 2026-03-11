"""
DriveAPIView.
"""

import logging

from django.conf import settings
from django.utils.decorators import method_decorator

import requests
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from lasuite.oidc_login.decorators import refresh_oidc_access_token
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.api import utils
from core.api.serializers import PartialDriveItemSerializer

logger = logging.getLogger(__name__)


class DriveAPIView(APIView):
    """
    API View which acts as a proxy to requests Drive through its Resource Server.
    https://github.com/suitenumerique/drive/blob/main/docs/resource_server.md
    """

    permission_classes = [permissions.IsAuthenticated]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.drive_external_api = (
            f"{settings.DRIVE_CONFIG.get('base_url')}/external_api/v1.0"
        )

    @extend_schema(
        tags=["third-party/drive"],
        parameters=[
            OpenApiParameter(
                name="title",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search files by title.",
                required=False,
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Files found",
                response=inline_serializer(
                    name="PaginatedDriveItemResponse",
                    fields={
                        "count": serializers.IntegerField(),
                        "next": serializers.CharField(allow_null=True),
                        "previous": serializers.CharField(allow_null=True),
                        "results": PartialDriveItemSerializer(many=True),
                    },
                ),
            )
        },
    )
    @method_decorator(refresh_oidc_access_token)
    def get(self, request):
        """
        Search for files created by the current user.
        """
        access_token = request.session.get("oidc_access_token")

        filters = {
            "is_creator_me": True,
            "type": "file",
        }
        if title := request.query_params.get("title"):
            filters.update({"title": title})

        # Search for files at the root of the user's workspace
        try:
            response = requests.get(
                f"{self.drive_external_api}/items/",
                params=filters,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=5,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException:
            logger.exception("Failed to search files in Drive")
            return Response(
                status=status.HTTP_502_BAD_GATEWAY,
                data={"error": "Failed to search files in Drive"},
            )

        return Response(response.json())

    @extend_schema(
        tags=["third-party/drive"],
        description=(
            "Save an attachment to the user's Drive workspace. "
            "If the file already exists (matched by title and size), "
            "returns the existing item with a 200 status. "
            "Otherwise, creates a new file and returns it with a 201 status."
        ),
        request=inline_serializer(
            name="DriveUploadAttachment",
            fields={
                "blob_id": serializers.CharField(
                    required=True,
                    help_text="ID of the attachment to upload (format: msg_{message_id}_{attachment_index})",
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                description="File already exists in Drive",
                response=PartialDriveItemSerializer,
            ),
            201: OpenApiResponse(
                description="File created successfully",
                response=PartialDriveItemSerializer,
            ),
        },
    )
    @method_decorator(refresh_oidc_access_token)
    def post(self, request):
        """
        Save an attachment to the user's Drive workspace (get or create).
        """
        access_token = request.session.get("oidc_access_token")
        blob_id = request.data.get("blob_id")
        if not blob_id:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"error": "blob_id is required"},
            )

        try:
            attachment = utils.get_attachment_from_blob_id(blob_id, request.user)
        except (models.Blob.DoesNotExist, ValueError) as exc:
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data={"error": str(exc)}
            )

        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Check if file already exists in Drive (get_or_create pattern)
        try:
            existing_item = self._find_existing_drive_item(attachment, auth_headers)
        except requests.exceptions.RequestException:
            logger.exception("Failed to search Drive for existing file")
            return Response(
                status=status.HTTP_502_BAD_GATEWAY,
                data={"error": "Failed to search Drive for existing file"},
            )

        if existing_item:
            return Response(status=status.HTTP_200_OK, data=existing_item)

        # File doesn't exist, create it
        try:
            return self._create_drive_item(attachment, auth_headers)
        except requests.exceptions.RequestException:
            logger.exception("Failed to create file in Drive")
            return Response(
                status=status.HTTP_502_BAD_GATEWAY,
                data={"error": "Failed to create file in Drive"},
            )

    def _find_existing_drive_item(self, attachment, headers):
        """Search for an existing file in Drive matching the attachment name and size.

        Raises RequestException on network/server errors so callers don't
        silently fall through to creation when the lookup is inconclusive.
        """
        search_response = requests.get(
            f"{self.drive_external_api}/items/",
            params={
                "is_creator_me": True,
                "type": "file",
                "title": attachment["name"],
            },
            headers=headers,
            timeout=5,
        )
        search_response.raise_for_status()

        for item in search_response.json().get("results", []):
            if item.get("size") == attachment["size"]:
                return item

        return None

    def _create_drive_item(self, attachment, headers):
        """Create a new file in Drive and upload its content."""
        response = requests.post(
            f"{self.drive_external_api}/items/",
            json={
                "type": "file",
                "filename": attachment["name"],
            },
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        item = response.json()

        # Upload file content using the presigned URL
        upload_response = requests.put(
            item["policy"],
            data=attachment["content"],
            headers={"Content-Type": attachment["type"], "x-amz-acl": "private"},
            timeout=180,
        )
        upload_response.raise_for_status()

        # Tell the Drive API that the upload is ended
        response = requests.post(
            f"{self.drive_external_api}/items/{item['id']}/upload-ended/",
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()

        return Response(status=status.HTTP_201_CREATED, data=response.json())
