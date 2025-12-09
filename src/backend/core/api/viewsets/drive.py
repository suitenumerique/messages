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

    def _retrieve_main_workspace(self, access_token):
        """
        Retrieve the main workspace for the authenticated user.
        """
        response = requests.get(
            f"{self.drive_external_api}/users/me/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=5,
        )

        if not response.ok:
            logger.warning(
                "Failed to retrieve main workspace. (%s - %s)",
                response.status_code,
                response.text,
                exc_info=True,
            )
            return None

        data = response.json()
        return data.get("main_workspace")

    @extend_schema(
        tags=["third-party/drive"],
        parameters=[
            OpenApiParameter(
                name="title",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search files by title.",
                required=True,
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

        # Retrieve the main workspace
        main_workspace = self._retrieve_main_workspace(access_token)

        if not main_workspace:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={
                    "error": f"No {settings.DRIVE_CONFIG.get('app_name')} main workspace found"
                },
            )

        # Search for files at the root of the main workspace
        response = requests.get(
            f"{self.drive_external_api}/items/{main_workspace['id']}/children/",
            params=filters,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=5,
        )

        return Response(response.json())

    @extend_schema(
        tags=["third-party/drive"],
        description="Create a new file in the main workspace.",
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
            201: OpenApiResponse(
                description="File created successfully",
                response=PartialDriveItemSerializer,
            )
        },
    )
    @method_decorator(refresh_oidc_access_token)
    def post(self, request):
        """
        Create a new file in the main workspace.
        """
        # Get the access token from the session
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

        # Get the main workspace
        main_workspace = self._retrieve_main_workspace(access_token)

        if not main_workspace:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={
                    "error": f"No {settings.DRIVE_CONFIG.get('app_name')} main workspace found"
                },
            )

        # Create a new file in the main workspace
        response = requests.post(
            f"{self.drive_external_api}/items/{main_workspace['id']}/children/",
            json={
                "type": "file",
                "filename": attachment["name"],
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        response.raise_for_status()
        item = response.json()
        policy = item["policy"]

        # Upload file content using the presigned URL
        upload_response = requests.put(
            policy,
            data=attachment["content"],
            headers={"Content-Type": attachment["type"], "x-amz-acl": "private"},
            timeout=180,
        )
        upload_response.raise_for_status()

        # Tell the Drive API that the upload is ended
        response = requests.post(
            f"{self.drive_external_api}/items/{item['id']}/upload-ended/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        response.raise_for_status()

        return Response(status=status.HTTP_201_CREATED, data=response.json())
