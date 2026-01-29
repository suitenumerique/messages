"""API ViewSet for handling binary data upload and download (JMAP-inspired implementation)."""

import logging

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core import enums, models
from core.api import permissions, utils

# Define logger
logger = logging.getLogger(__name__)


class BlobViewSet(ViewSet):
    """
    ViewSet for handling binary data (blobs) according to JMAP specification.

    Provides endpoints for uploading and downloading binary data to be used in messages.
    Following JMAP's two-step approach:
    1. Upload blob (raw binary data) to a specific mailbox
    2. Create attachment referencing the blob (in a separate endpoint)
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    @extend_schema(
        description="""Upload binary data and create a Blob record.
        This endpoint accepts multipart/form-data containing a file and returns a
        blob ID and other metadata. The blob is associated with the specified mailbox.
        """,
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=str,
                location=OpenApiParameter.PATH,
                description="ID of the mailbox to associate the blob with",
                required=True,
            )
        ],
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "The file to upload",
                    }
                },
                "required": ["file"],
            }
        },
        responses={
            201: OpenApiResponse(
                description="Blob created successfully",
                response={
                    "type": "object",
                    "properties": {
                        "blobId": {"type": "string", "format": "uuid"},
                        "type": {"type": "string"},
                        "size": {"type": "integer"},
                        "sha256": {"type": "string"},
                    },
                    "required": ["blobId", "type", "size", "sha256"],
                },
            ),
            400: OpenApiResponse(description="Bad request - No file provided"),
            403: OpenApiResponse(
                description="Forbidden - User does not have permission to upload to this mailbox"
            ),
            404: OpenApiResponse(description="Mailbox not found"),
            500: OpenApiResponse(description="Internal server error"),
        },
        tags=["blob"],
    )
    @method_decorator(csrf_exempt)
    @action(detail=False, methods=["post"], url_path="upload/(?P<mailbox_id>[^/.]+)")
    def upload(self, request, mailbox_id=None):
        """
        Upload binary data and create a Blob record.

        This endpoint accepts multipart/form-data containing a file and returns a
        blob ID and other metadata. The blob is associated with the specified mailbox.
        """
        try:
            # Verify the mailbox exists and user has access
            mailbox = models.Mailbox.objects.get(id=mailbox_id)
            if not models.MailboxAccess.objects.filter(
                mailbox=mailbox,
                user=request.user,
                role__in=enums.MAILBOX_ROLES_CAN_EDIT,
            ).exists():
                return Response(
                    {"error": "You do not have permission to upload to this mailbox"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Validate the file is included
            if "file" not in request.FILES:
                return Response(
                    {"error": "No file was provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            uploaded_file = request.FILES["file"]
            content_type = uploaded_file.content_type or "application/octet-stream"

            # Read file content
            content = uploaded_file.read()

            # Create the blob record using mailbox method
            blob = mailbox.create_blob(
                content=content,
                content_type=content_type,
            )

            # Return a response with the blob details
            # Following JMAP endpoint response structure
            return Response(
                {
                    "blobId": str(blob.id),
                    "type": content_type,
                    "size": len(content),
                    "sha256": blob.sha256.hex(),
                },
                status=status.HTTP_201_CREATED,
            )

        except models.Mailbox.DoesNotExist as e:
            raise NotFound("Mailbox not found") from e

        # pylint: disable=broad-exception-caught
        except Exception as e:
            logger.exception("Error uploading file: %s", str(e))
            return Response(
                {"error": "Error processing file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """
        Download binary data for the specified blob ID.

        This endpoint returns the raw binary content of a blob. Access is controlled
        by checking if the user has access to any mailbox that owns this blob.
        """
        try:
            # Blob IDs in the form msg_[message_id]_[attachment_number] are looked up
            # directly in the message's attachments.
            if pk.startswith("msg_"):
                try:
                    attachment = utils.get_attachment_from_blob_id(pk, request.user)
                except ValueError as e:
                    return Response(
                        status=status.HTTP_400_BAD_REQUEST, data={"error": str(e)}
                    )
                except models.Blob.DoesNotExist as e:
                    return Response(
                        status=status.HTTP_404_NOT_FOUND, data={"error": str(e)}
                    )

                # Create response with decompressed content
                response = HttpResponse(
                    attachment["content"], content_type=attachment["type"]
                )

                # Add appropriate headers for download
                response["Content-Disposition"] = (
                    f'attachment; filename="{attachment["name"]}"'
                )
                response["Content-Length"] = attachment["size"]
                # Enable browser caching for 30 days (inline images benefit from this)
                response["Cache-Control"] = "private, max-age=2592000"

            else:
                # Get the blob
                blob = models.Blob.objects.get(id=pk)

                # Check if user has access to the mailbox that owns this blob
                if not models.MailboxAccess.objects.filter(
                    mailbox=blob.mailbox, user=request.user
                ).exists():
                    return Response(
                        {"error": "You do not have permission to download this blob"},
                        status=status.HTTP_403_FORBIDDEN,
                    )

                # Get the first attachment name to use as filename (if available)
                attachment = models.Attachment.objects.filter(blob=blob).first()
                filename = attachment.name if attachment else f"blob-{blob.id}.bin"

                # Create response with decompressed content
                response = HttpResponse(
                    blob.get_content(), content_type=blob.content_type
                )

                # Add appropriate headers for download
                response["Content-Disposition"] = f'attachment; filename="{filename}"'
                response["Content-Length"] = blob.size
                # Enable browser caching for 30 days (inline images benefit from this)
                response["Cache-Control"] = "private, max-age=2592000"

            return response

        except models.Blob.DoesNotExist:
            # Same error to hide blob existence
            return Response(
                {"error": "You do not have permission to download this blob"},
                status=status.HTTP_403_FORBIDDEN,
            )
        # pylint: disable=broad-exception-caught
        except Exception as e:
            logger.exception("Error downloading file: %s", str(e))
            return Response(
                {"error": "Error downloading file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
