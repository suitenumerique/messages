"""API ViewSet for proxying external images."""

import ipaddress
import logging
import socket
from urllib.parse import ParseResult, unquote, urlparse, urlunparse

from django.conf import settings
from django.http import HttpResponse

import magic
import requests
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from requests.adapters import HTTPAdapter
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core import enums, models
from core.api import permissions

logger = logging.getLogger(__name__)


class SSRFValidationError(Exception):
    """Exception raised when URL validation fails due to SSRF protection."""


class SSRFProtectedAdapter(HTTPAdapter):
    """
    HTTPAdapter that connects to a pre-validated IP address while maintaining
    proper TLS certificate verification against the original hostname.

    This prevents TOCTOU DNS rebinding attacks by:
    1. Connecting to the IP address that was validated (not re-resolving DNS)
    2. Verifying TLS certificates against the original hostname (for HTTPS)
    3. Setting the Host header correctly for virtual hosting
    """

    def __init__(
        self,
        dest_ip: str,
        dest_port: int,
        original_hostname: str,
        original_scheme: str,
        **kwargs,
    ):
        self.dest_ip = dest_ip
        self.dest_port = dest_port
        self.original_hostname = original_hostname
        self.original_scheme = original_scheme
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        """Initialize pool manager with TLS hostname verification settings."""
        if self.original_scheme == "https":
            # Ensure TLS certificate is verified against the original hostname
            # even though we're connecting to an IP address
            pool_kwargs["assert_hostname"] = self.original_hostname
            pool_kwargs["server_hostname"] = self.original_hostname
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        """Send request, rewriting URL to connect to the validated IP address."""
        parsed = urlparse(request.url)

        # Build URL with validated IP instead of hostname
        # IPv6 addresses need brackets in URLs
        if ":" in self.dest_ip:
            ip_netloc = f"[{self.dest_ip}]:{self.dest_port}"
        else:
            ip_netloc = f"{self.dest_ip}:{self.dest_port}"

        # Reconstruct URL with IP address
        request.url = urlunparse(
            (
                parsed.scheme,
                ip_netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

        # Set Host header to original hostname for virtual hosting
        # Include port only if non-standard
        if parsed.port and parsed.port not in (80, 443):
            request.headers["Host"] = f"{self.original_hostname}:{parsed.port}"
        else:
            request.headers["Host"] = self.original_hostname

        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


class SSRFSafeSession:
    """
    HTTP Session with built-in SSRF protection.

    This class provides a safe way to make HTTP requests by:
    1. Validating URL scheme (only http/https allowed)
    2. Blocking direct IP addresses (legitimate services use domain names)
    3. Resolving hostnames and blocking private/internal IPs
    4. Pinning resolved IPs to prevent DNS rebinding attacks (TOCTOU)

    Usage:
        try:
            response = SSRFSafeSession().get("https://example.com/image.png", timeout=10)
        except SSRFValidationError:
            # URL was blocked for security reasons
            pass
    """

    def _validate_url(self, parsed_url: ParseResult) -> list[str]:
        """
        Validate that a URL is safe to fetch (SSRF protection).

        This function prevents Server-Side Request Forgery (SSRF) attacks by
        validating URLs before making HTTP requests. It implements a defense-in-depth
        approach:

        1. Only allows http/https schemes
        2. Blocks all IP addresses (legitimate emails use domain names)
        3. Resolves hostnames and blocks if they resolve to private/internal IPs
        (prevents DNS rebinding attacks where attacker-controlled DNS returns
        127.0.0.1 or internal IPs)

        Blocked addresses include:
        - Any direct IP address (e.g., http://192.168.1.1/)
        - Private IP ranges (RFC1918: 10.x.x.x, 172.16-31.x.x, 192.168.x.x)
        - Loopback addresses (127.x.x.x, ::1)
        - Link-local addresses (169.254.x.x, fe80::/10)
        - Multicast and reserved addresses
        - Cloud provider metadata endpoints (169.254.169.254, fd00:ec2::254)

        Args:
            parsed_url: The parsed URL to validate

        Returns:
            List of validated IP addresses that the hostname resolves to

        Raises:
            SSRFValidationError: If the URL is unsafe
        """
        # Only allow http and https schemes
        if parsed_url.scheme not in {"http", "https"}:
            raise SSRFValidationError("Invalid URL scheme (only http/https allowed)")

        # Require a hostname
        if not parsed_url.hostname:
            raise SSRFValidationError("Invalid URL (missing hostname)")

        # Block all IP addresses (legitimate services use domain names)
        try:
            ipaddress.ip_address(parsed_url.hostname)
            raise SSRFValidationError(
                "IP addresses are not allowed (domain name required)"
            )
        except ValueError:
            # Not an IP address, continue validation
            pass

        # Resolve hostname to IP addresses
        try:
            addr_info = socket.getaddrinfo(
                parsed_url.hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise SSRFValidationError("Unable to resolve hostname") from exc

        # Check all resolved IP addresses
        valid_ips = []
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip_addr = ipaddress.ip_address(ip_str)

                if ip_addr.is_private:
                    raise SSRFValidationError("Domain resolves to private IP address")

                if ip_addr.is_loopback:
                    raise SSRFValidationError("Domain resolves to loopback address")

                if ip_addr.is_link_local:
                    raise SSRFValidationError("Domain resolves to link-local address")

                if ip_addr.is_multicast:
                    raise SSRFValidationError("Domain resolves to multicast address")

                if ip_addr.is_reserved:
                    raise SSRFValidationError("Domain resolves to reserved address")

                # Block known cloud metadata IPs
                if ip_str in ("169.254.169.254", "fd00:ec2::254"):
                    raise SSRFValidationError(
                        "Domain resolves to cloud metadata endpoint"
                    )

                valid_ips.append(ip_str)

            except ValueError as exc:
                raise SSRFValidationError("Invalid IP address in DNS response") from exc

        if not valid_ips:
            raise SSRFValidationError("No valid IP addresses found")

        return valid_ips

    def get(self, url: str, timeout: int, **kwargs) -> requests.Response:
        """
        Perform a safe HTTP GET request with SSRF protection and IP pinning.

        This method:
        1. Parses and validates the URL
        2. Resolves DNS and validates all returned IPs
        3. Creates a requests Session with a custom HTTPAdapter that:
           - Connects directly to the validated IP (preventing DNS rebinding)
           - Maintains proper TLS certificate verification against the hostname
           - Sets the Host header correctly for virtual hosting

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds
            **kwargs: Additional arguments passed to requests.Session.get()

        Returns:
            requests.Response object

        Raises:
            SSRFValidationError: If the URL fails security validation
            requests.RequestException: If the HTTP request fails
        """
        parsed_url = urlparse(url)
        valid_ips = self._validate_url(parsed_url)

        # Determine the port (explicit or default based on scheme)
        if parsed_url.port:
            port = parsed_url.port
        elif parsed_url.scheme == "http":
            port = 80
        else:
            port = 443

        # Create a session with our SSRF-protected adapter that pins to the validated IP
        session = requests.Session()
        adapter = SSRFProtectedAdapter(
            dest_ip=valid_ips[0],
            dest_port=port,
            original_hostname=parsed_url.hostname,
            original_scheme=parsed_url.scheme,
        )

        # Mount the adapter for both http and https schemes
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session.get(url, timeout=timeout, **kwargs)


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
                allow_redirects=False,
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
