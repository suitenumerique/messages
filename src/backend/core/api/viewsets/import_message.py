"""API ViewSet for importing messages via EML, MBOX, or IMAP."""

from django.core.files.storage import storages
from django.shortcuts import get_object_or_404

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.api.utils import generate_presigned_url, get_file_key
from core.models import Mailbox
from core.services.importer import ImportService

from .. import permissions
from ..serializers import (
    ImportFileSerializer,
    ImportFileUploadAbortSerializer,
    ImportFileUploadCompleteSerializer,
    ImportFileUploadPartSerializer,
    ImportFileUploadSerializer,
    ImportIMAPSerializer,
)


@extend_schema(tags=["import"])
class ImportViewSet(viewsets.ViewSet):
    """
    ViewSet for importing messages via EML/MBOX file or IMAP.

    This ViewSet provides endpoints for importing messages from:
    - EML/MBOX files uploaded directly
    - IMAP servers with configurable connection settings

    All imports are processed asynchronously and return a task ID for tracking.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        request=ImportFileSerializer,
        responses={
            202: OpenApiResponse(
                description="Import started. Returns Celery task ID for tracking.",
                response={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID for tracking the import",
                        },
                        "type": {
                            "type": "string",
                            "description": "Type of import (eml or mbox)",
                        },
                    },
                },
            ),
            400: OpenApiResponse(description="Invalid input data or file format"),
            403: OpenApiResponse(
                description="User does not have access to the specified mailbox"
            ),
            404: OpenApiResponse(description="Specified mailbox not found"),
        },
        description="""
        Import messages by uploading an EML or MBOX file.

        The import is processed asynchronously and returns a task ID for tracking.
        The file must be a valid EML or MBOX format. The recipient mailbox must exist
        and the user must have access to it.
        """,
    )
    @action(detail=False, methods=["post"], url_path="file")
    def import_file(self, request):
        """Import messages by uploading an EML or MBOX file."""
        serializer = ImportFileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipient_id = serializer.validated_data["recipient"]
        mailbox = get_object_or_404(Mailbox, id=recipient_id)
        file_key = get_file_key(request.user.id, serializer.validated_data["filename"])

        success, response_data = ImportService.import_file(
            file_key=file_key,
            recipient=mailbox,
            user=request.user,
        )

        if not success:
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        return Response(response_data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        request=ImportIMAPSerializer,
        responses={
            202: OpenApiResponse(
                description="IMAP import started. Returns Celery task ID for tracking the import progress.",
                response={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID for tracking the import",
                        },
                        "type": {
                            "type": "string",
                            "description": "Type of import (imap)",
                        },
                    },
                },
            ),
            400: OpenApiResponse(
                description="Invalid input data or IMAP connection parameters"
            ),
            403: OpenApiResponse(
                description="User does not have access to the specified mailbox or IMAP credentials are invalid"
            ),
            404: OpenApiResponse(description="Specified mailbox not found"),
        },
        description="""
        Import messages from an IMAP server.

        This endpoint initiates an asynchronous import process from an IMAP server.
        The import is processed in the background and returns a task ID for tracking.

        Required parameters:
        - imap_server: Hostname of the IMAP server
        - imap_port: Port number for the IMAP server
        - username: IMAP account username
        - password: IMAP account password
        - recipient: ID of the mailbox to import messages into

        Optional parameters:
        - use_ssl: Whether to use SSL for the connection (default: true)
        """,
    )
    @action(detail=False, methods=["post"], url_path="imap")
    def import_imap(self, request):
        """Import messages from an IMAP server."""
        serializer = ImportIMAPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mailbox = get_object_or_404(Mailbox, id=data["recipient"])

        success, response_data = ImportService.import_imap(
            imap_server=data["imap_server"],
            imap_port=data["imap_port"],
            username=data["username"],
            password=data["password"],
            recipient=mailbox,
            user=request.user,
            use_ssl=data.get("use_ssl", True),
        )

        if not success:
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        return Response(response_data, status=status.HTTP_202_ACCEPTED)


class MessagesArchiveUploadViewSet(viewsets.ViewSet):
    """ "
    APIView for uploading messages archive into the message imports bucket.
    It can be used to upload a file to the message imports bucket directly or in parts.
    """

    permission_classes = [permissions.IsAuthenticated]
    storage = storages["message-imports"]
    lookup_url_kwarg = "upload_id"
    lookup_field = "upload_id"

    def create(self, request):
        """
        Create a multipart upload or a direct upload for a file to the message imports bucket.
        - In case of a multipart upload, the upload_id is returned to be used for subsequent part uploads.
        - In case of a direct upload, a signed url is returned to directly upload
          the file to the message imports bucket.
        """
        serializer = ImportFileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filename = serializer.validated_data["filename"]
        file_key = get_file_key(request.user.id, filename)
        is_multipart = "multipart" in request.query_params
        content_type = serializer.validated_data["content_type"]

        if is_multipart:
            s3_client = self.storage.connection.meta.client
            metadata = s3_client.create_multipart_upload(
                Bucket=self.storage.bucket_name, Key=file_key, ContentType=content_type
            )
            return Response(
                {"filename": filename, "upload_id": metadata["UploadId"]},
                status=status.HTTP_201_CREATED,
            )

        url = generate_presigned_url(
            self.storage,
            ClientMethod="put_object",
            Params={
                "Bucket": self.storage.bucket_name,
                "Key": file_key,
                "ContentType": content_type,
            },
        )
        return Response(
            {"filename": filename, "url": url}, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="part")
    def create_part_upload(self, request, upload_id=None):
        """
        Create a presigned url to upload a part of a file to the message imports bucket.
        """
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadPartSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        filename = serializer.validated_data["filename"]
        file_key = get_file_key(request.user.id, filename)
        upload_id = serializer.validated_data["upload_id"]
        part_number = serializer.validated_data["part_number"]

        url = generate_presigned_url(
            self.storage,
            ClientMethod="upload_part",
            Params={
                "Bucket": self.storage.bucket_name,
                "Key": file_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
        )
        return Response(
            {
                "filename": filename,
                "part_number": part_number,
                "upload_id": upload_id,
                "url": url,
            },
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, upload_id=None):
        """
        Update a multipart upload to complete it by providing all part ETags.
        """
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadCompleteSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        filename = serializer.validated_data["filename"]
        file_key = get_file_key(request.user.id, filename)
        upload_id = serializer.validated_data["upload_id"]
        parts = serializer.validated_data["parts"]

        ordered_parts = sorted(parts, key=lambda x: x["PartNumber"])
        s3_client = self.storage.connection.meta.client
        s3_client.complete_multipart_upload(
            Bucket=self.storage.bucket_name,
            Key=file_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": ordered_parts},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, upload_id=None):
        """Abort a multipart upload of a file to the message imports bucket."""
        data = request.data.copy()
        data.update({"upload_id": upload_id})
        serializer = ImportFileUploadAbortSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        filename = serializer.validated_data["filename"]
        file_key = get_file_key(request.user.id, filename)
        upload_id = serializer.validated_data["upload_id"]

        s3_client = self.storage.connection.meta.client
        s3_client.abort_multipart_upload(
            Bucket=self.storage.bucket_name, Key=file_key, UploadId=upload_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
