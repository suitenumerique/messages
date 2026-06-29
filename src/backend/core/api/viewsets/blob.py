"""API ViewSet for handling binary data upload and download (JMAP-inspired implementation)."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.utils.http import content_disposition_header

import magic
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import (
    APIException,
    NotFound,
    ParseError,
    PermissionDenied,
)
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core import enums, models
from core.api import permissions, utils
from core.services.blob_gc import upload_and_reserve_blob

# Number of leading bytes inspected by python-magic on the preview endpoint.
# Every format we allowlist is identified by a signature in its first few hundred
# bytes, so 2 KiB is a comfortable margin — not a tight bound. We cap the slice
# only to avoid handing magic the whole payload before deciding to refuse.
_PREVIEW_MAGIC_SNIFF_BYTES = 2048

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
            413: OpenApiResponse(
                description="Payload too large - exceeds MAX_OUTGOING_ATTACHMENT_SIZE"
            ),
            500: OpenApiResponse(description="Internal server error"),
        },
        tags=["blob"],
    )
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

            # Cap before ``.read()`` so an oversize upload never lands
            # in worker RAM. The draft-attach flow re-checks the
            # cumulative size; this cap is per-blob, that one per-message.
            if uploaded_file.size > settings.MAX_OUTGOING_ATTACHMENT_SIZE:
                max_mb = settings.MAX_OUTGOING_ATTACHMENT_SIZE / (1024 * 1024)
                return Response(
                    {
                        "error": (
                            f"File too large: {uploaded_file.size} bytes "
                            f"(max {max_mb:.0f} MB per attachment)."
                        )
                    },
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            # Read file content
            content = uploaded_file.read()

            # JMAP upload step: register a reservation so the blob_id
            # survives until the follow-up attach call.
            blob = upload_and_reserve_blob(mailbox, content, content_type)

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

    def _resolve_blob_source(self, pk, user):
        """Resolve a blob to its bytes and metadata.

        `msg_*` IDs are served from the parsed message attachment cache

        Returns:
            A dict with keys `content` (bytes), `declared_type` (str),
            `filename` (str), `size` (int).

        Raises:
            ParseError: malformed `msg_*` ID.
            NotFound: `msg_*` ID points at a missing attachment.
            PermissionDenied: blob doesn't exist or user has no access
        """
        if pk.startswith("msg_"):
            try:
                attachment = utils.get_attachment_from_blob_id(pk, user)
            except ValueError as e:
                raise ParseError("Invalid blob ID") from e
            except models.Blob.DoesNotExist as e:
                raise NotFound("Blob not found") from e
            return {
                "content": attachment["content"],
                "declared_type": attachment["type"],
                "filename": attachment["name"],
                "size": attachment["size"],
            }

        try:
            blob = models.Blob.objects.get(id=pk)
        except DjangoValidationError as e:
            # Non-UUID ``pk`` reaches the ORM as a ValidationError; surface
            # it as a 400 instead of falling through to the generic 500.
            raise ParseError("Invalid blob ID") from e
        except models.Blob.DoesNotExist as e:
            raise PermissionDenied(
                "You do not have permission to access this blob"
            ) from e

        if not models.Blob.objects.user_can_access(user, blob.id):
            raise PermissionDenied("You do not have permission to access this blob")
        attachment_row = models.Attachment.objects.filter(blob=blob).first()

        return {
            "content": blob.get_content(),
            "declared_type": blob.content_type,
            "filename": (
                attachment_row.name if attachment_row else f"blob-{blob.id}.bin"
            ),
            "size": blob.size,
        }

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """
        Download binary data for the specified blob ID.

        This endpoint returns the raw binary content of a blob. Access is controlled
        by checking if the user has access to any mailbox that owns this blob.
        """
        try:
            source = self._resolve_blob_source(pk, request.user)

            response = HttpResponse(
                source["content"], content_type=source["declared_type"]
            )
            response["Content-Disposition"] = content_disposition_header(
                True, source["filename"]
            )
            response["Content-Length"] = source["size"]
            # Enable browser caching for 30 days (inline images benefit from this)
            response["Cache-Control"] = "private, max-age=2592000"
            return response

        except APIException:
            # Let DRF convert ParseError / NotFound / PermissionDenied raised by
            # ``_resolve_blob_source`` into the proper Response.
            raise
        # pylint: disable=broad-exception-caught
        except Exception as e:
            logger.exception("Error downloading file: %s", str(e))
            return Response(
                {"error": "Error downloading file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        responses={
            (200, "application/octet-stream"): OpenApiResponse(
                description=(
                    "Inline preview of the blob. The Content-Type is the MIME "
                    "type detected server-side and is guaranteed to belong to "
                    "``PREVIEWABLE_MIME_TYPES``."
                ),
                response=OpenApiTypes.BINARY,
            ),
            400: OpenApiResponse(description="Invalid blob ID"),
            403: OpenApiResponse(
                description="Forbidden - User does not have permission to preview this blob"
            ),
            404: OpenApiResponse(description="Blob not found"),
            415: OpenApiResponse(
                description=(
                    "Unsupported media type for inline preview. The detected "
                    "MIME is not in ``PREVIEWABLE_MIME_TYPES`` or does not "
                    "match the declared Content-Type. The response body "
                    "includes a ``code`` field set to either ``suspicious`` "
                    "(declared type was previewable but bytes disagree) or "
                    "``unsupported`` (type is plainly not previewable)."
                ),
            ),
            500: OpenApiResponse(description="Internal server error"),
        },
        tags=["blob"],
    )
    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """
        Serve a blob inline for the FilePreview viewer.

        Sibling of ``download`` with the same authorization model but two
        extra guarantees:

        - the response Content-Type is the MIME type detected from the bytes
          (via ``python-magic``), not the value declared at upload time;
        - the detected MIME must belong to ``PREVIEWABLE_MIME_TYPES``,
          otherwise the endpoint refuses with 415.

        Returning 415 (rather than 200 with the raw payload) is the security
        contract that lets the frontend render the response inline: any byte
        we send back has been re-classified server-side as one of the safe
        previewable types.
        """
        try:
            source = self._resolve_blob_source(pk, request.user)
            content = source["content"]
            declared_type = source["declared_type"]

            # Normalize the declared Content-Type (e.g. image/PNG; charset=binary)
            declared_media_type = declared_type.partition(";")[0].strip().lower()
            detected_type = magic.from_buffer(
                content[:_PREVIEW_MAGIC_SNIFF_BYTES], mime=True
            ).lower()

            # A preview is served only when the detected bytes are an
            # allowlisted type AND match the declared Content-Type. Anything
            # else is refused — the browser must never render bytes the
            # uploader lied about. The blob can still be downloaded via
            # /download/.
            if (
                detected_type not in enums.PREVIEWABLE_MIME_TYPES
                or detected_type != declared_media_type
            ):
                # When the *declared* type was itself previewable, the bytes
                # don't back that claim — flag the attachment as suspicious.
                # Otherwise it's plainly not previewable.
                suspicious = declared_media_type in enums.PREVIEWABLE_MIME_TYPES
                code = (
                    enums.PreviewRefusalCode.SUSPICIOUS
                    if suspicious
                    else enums.PreviewRefusalCode.UNSUPPORTED
                )
                logger.log(
                    logging.WARNING if suspicious else logging.INFO,
                    "Refused preview for blob %s: declared %s, detected %s (%s)",
                    pk,
                    declared_type,
                    detected_type,
                    code.value,
                )
                return Response(
                    {
                        "error": "File type not supported for preview",
                        "code": code.value,
                    },
                    status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )

            response = HttpResponse(content, content_type=detected_type)
            response["Content-Disposition"] = content_disposition_header(
                False, source["filename"]
            )
            response["Content-Length"] = source["size"]
            response["Cache-Control"] = "private, max-age=2592000"
            # Defense in depth on top of the global SECURE_CONTENT_TYPE_NOSNIFF.
            response["X-Content-Type-Options"] = "nosniff"
            response["Referrer-Policy"] = "no-referrer"
            # Strict CSP: the response is expected to be loaded only via
            # <img>, <video>, <audio> or fetch() (PDF.js) — never as a
            # top-level document with scripts.
            response["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self' blob: data:; "
                "media-src 'self' blob:; sandbox"
            )
            return response

        except APIException:
            # Let DRF convert ParseError / NotFound / PermissionDenied raised by
            # ``_resolve_blob_source`` into the proper Response.
            raise
        # pylint: disable=broad-exception-caught
        except Exception as e:
            logger.exception("Error previewing file: %s", str(e))
            return Response(
                {"error": "Error previewing file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
