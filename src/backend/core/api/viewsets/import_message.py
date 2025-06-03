"""API ViewSet for importing messages via EML, MBOX, or IMAP."""

from django.shortcuts import get_object_or_404

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.models import Mailbox
from core.services.import_service import ImportService

from .. import permissions
from ..serializers import ImportFileSerializer, ImportIMAPSerializer


class ImportViewSet(viewsets.ViewSet):
    """ViewSet for importing messages via EML/MBOX file or IMAP."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        request=ImportFileSerializer,
        responses={
            202: OpenApiResponse(description="Import started. Returns Celery task ID.")
        },
        tags=["import"],
        description="Import messages by uploading an EML or MBOX file.",
    )
    @action(detail=False, methods=["post"], url_path="file")
    def import_file(self, request):
        """Import messages by uploading an EML or MBOX file."""
        serializer = ImportFileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipient_id = serializer.validated_data["recipient"]
        import_file = serializer.validated_data["import_file"]
        mailbox = get_object_or_404(Mailbox, id=recipient_id)

        success, response_data = ImportService.import_file(
            file=import_file,
            recipient=mailbox,
            user=request.user,
        )

        if not success:
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        return Response(
            response_data,
            status=status.HTTP_202_ACCEPTED
            if response_data["type"] == "mbox"
            else status.HTTP_200_OK,
        )

    @extend_schema(
        request=ImportIMAPSerializer,
        responses={
            202: OpenApiResponse(
                description="IMAP import started. Returns Celery task ID."
            )
        },
        tags=["import"],
        description="Import messages from an IMAP server.",
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
