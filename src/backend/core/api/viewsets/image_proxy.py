"""API ViewSet for proxying external images."""

import logging
from urllib.parse import unquote

from django.conf import settings
from django.http import HttpResponse

import magic
import requests
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core import enums, models
from core.api import permissions
from core.services.ssrf import SSRFSafeSession, SSRFValidationError

logger = logging.getLogger(__name__)


class ImageProxySuspiciousResponse(HttpResponse):
    """
    Response for suspicious content that has been blocked by our image proxy.
    Returns a placeholder SVG image instead of JSON error for better UX.
    """

    def __init__(self, status: int):
        suspicious_placeholder = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" viewBox="0 0 16 16"><rect width="16" height="16" fill="#a75400" rx="4"/><path fill="#f6f8f9" fill-opacity=".95" d="M7.258 8.896q.027.81.828.81.774 0 .792-.81l.148-4.475a.8.8 0 0 0-.258-.654.97.97 0 0 0-.7-.267q-.423 0-.69.258a.83.83 0 0 0-.25.663zM7.313 12.477q.323.285.773.286.433 0 .756-.286a.93.93 0 0 0 .322-.727.93.93 0 0 0-.322-.727 1.08 1.08 0 0 0-.756-.286q-.45 0-.773.295A.93.93 0 0 0 7 11.75a.96.96 0 0 0 .313.727"/></svg>'  # pylint: disable=line-too-long
        super().__init__(
            content=suspicious_placeholder, content_type="image/svg+xml", status=status
        )


class ImageProxyViewSet(ViewSet):
    """
    ViewSet for proxying external images to protect user privacy.

    Images are fetched on-demand from external sources and served through
    the application. This prevents tracking pixels from leaking user IP
    addresses and browsing behavior to external servers.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        description="""Proxy an external image through the server.

        This endpoint fetches images from external sources and serves them
        through the application to protect user privacy. Requires the
        IMAGE_PROXY_ENABLED environment variable to be set to true.
        """,
        parameters=[
            OpenApiParameter(
                name="mailbox_id",
                type=str,
                location=OpenApiParameter.PATH,
                description="ID of the mailbox",
                required=True,
            ),
            OpenApiParameter(
                name="url",
                type=str,
                location=OpenApiParameter.QUERY,
                description="The external image URL to proxy",
                required=True,
            ),
        ],
        responses={
            200: OpenApiResponse(description="Image content"),
            400: OpenApiResponse(description="Invalid request"),
            403: OpenApiResponse(description="Forbidden"),
            413: OpenApiResponse(description="Image too large"),
            502: OpenApiResponse(description="Failed to fetch external image"),
        },
    )
    def list(self, request, mailbox_id=None):
        """Proxy an external image through the server."""
        try:
            mailbox = models.Mailbox.objects.get(pk=mailbox_id)
        except models.Mailbox.DoesNotExist:
            return Response(
                {"error": "Mailbox not found"}, status=http_status.HTTP_404_NOT_FOUND
            )

        if not mailbox.accesses.filter(user=request.user).exists():
            return Response(
                {"error": "Forbidden"}, status=http_status.HTTP_403_FORBIDDEN
            )

        if not settings.IMAGE_PROXY_ENABLED:
            return Response(
                {"error": "Image proxy not enabled"},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        url = request.query_params.get("url")
        if not url:
            return Response(
                {"error": "Missing url parameter"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        url = unquote(url)

        try:
            response = SSRFSafeSession().get(
                url,
                timeout=10,
                stream=True,
                headers={"User-Agent": "Messages-ImageProxy/1.0"},
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            # Filter out non-image content-types but keep generic content-type for further checking
            if content_type not in [
                "application/octet-stream",
                "binary/octet-stream",
            ] and not content_type.startswith("image/"):
                logger.warning("Content-Type is not an image: %s", content_type)
                return ImageProxySuspiciousResponse(
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Safely parse Content-Length header
            try:
                content_length = int(response.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                content_length = 0

            # Use Content-Length as a hint, but don't trust it completely
            if content_length and content_length > settings.IMAGE_PROXY_MAX_SIZE:
                return Response(
                    {"error": "Image too large"},
                    status=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            # Create a single iterator to avoid data loss between multiple iter_content calls
            chunk_size = 8192  # 8KB chunks
            content_iter = response.iter_content(chunk_size=chunk_size)

            # Validate that content is actually an image through the first chunk (defense in depth)
            try:
                head_chunk = next(content_iter)
            except StopIteration:
                logger.warning("No content found for %s", url)
                return ImageProxySuspiciousResponse(
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            mime_type = magic.from_buffer(head_chunk, mime=True)
            if not mime_type.startswith("image/"):
                logger.warning(
                    "Content from %s is not a valid image: %s", url, mime_type
                )
                # Return placeholder image for invalid content
                return ImageProxySuspiciousResponse(
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Check that mime type is not a blacklisted image type
            if mime_type in enums.BLACKLISTED_PROXY_IMAGE_MIME_TYPES:
                logger.warning(
                    "Content from %s is a blacklisted image type: %s", url, mime_type
                )
                # Return placeholder image for invalid content
                return ImageProxySuspiciousResponse(
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Last check the real file size of the image
            # Stream content in chunks to prevent memory exhaustion
            total_size = len(head_chunk)
            image_content = head_chunk
            size_exceeded = total_size > settings.IMAGE_PROXY_MAX_SIZE

            for chunk in content_iter:
                if not chunk:
                    continue

                total_size += len(chunk)

                # Enforce size limit while streaming
                if total_size > settings.IMAGE_PROXY_MAX_SIZE:
                    size_exceeded = True
                    break

                image_content += chunk

            if size_exceeded:
                logger.warning(
                    "Image from %s exceeds size limit: %d bytes", url, total_size
                )
                return Response(
                    {"error": "Image too large"},
                    status=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            return HttpResponse(
                image_content,
                content_type=mime_type,
                headers={
                    "Cache-Control": f"public, max-age={settings.IMAGE_PROXY_CACHE_TTL}",
                    "Content-Security-Policy": "default-src 'none'",
                    "Permissions-Policy": "()",
                },
            )

        except SSRFValidationError:
            logger.warning("Blocked unsafe URL: %s", url)
            return ImageProxySuspiciousResponse(status=http_status.HTTP_403_FORBIDDEN)

        except requests.RequestException as e:
            logger.warning("Failed to fetch external image from %s: %s", url, e)
            return Response(
                {"error": "Failed to fetch image"},
                status=http_status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            if "response" in locals():
                response.close()
