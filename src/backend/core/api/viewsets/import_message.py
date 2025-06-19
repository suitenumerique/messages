"""API ViewSet for importing messages via EML, MBOX, or IMAP."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.models import Blob, Mailbox
from core.services.import_service import ImportService

from .. import permissions
from ..serializers import ImportFileSerializer, ImportIMAPSerializer


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

        blob_id = serializer.validated_data["blob"]
        blob = get_object_or_404(Blob, id=blob_id)

        success, response_data = ImportService.import_file(
            file=blob,
            recipient=mailbox,
            user=request.user,
        )
        # clean up the blob
        blob.delete()

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
        - folder: IMAP folder to import from (default: "INBOX")
        - max_messages: Maximum number of messages to import (default: 0, meaning all messages)
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
            folder=data.get("folder", "INBOX"),
            max_messages=data.get("max_messages", 0),
        )

        if not success:
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        return Response(response_data, status=status.HTTP_202_ACCEPTED)
