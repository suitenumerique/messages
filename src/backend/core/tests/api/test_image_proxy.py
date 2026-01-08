"""Tests for the image proxy API."""
# pylint: disable=too-many-public-methods

import io
import socket
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from django.test import override_settings
from django.urls import reverse

import pytest
import requests
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories
from core.enums import MailboxRoleChoices


@pytest.mark.django_db
class TestImageProxyViewSet:
    """Tests for the image proxy API endpoints."""

    @pytest.fixture
    def api_client(self):
        """Return an authenticated API client."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def user_mailbox(self, api_client):
        """Create a mailbox for the test user with viewer access."""
        _, user = api_client
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=MailboxRoleChoices.VIEWER,
        )
        return mailbox

    def _get_image_proxy_url(self, mailbox_id, url):
        """Helper to construct the image proxy URL."""
        return (
            reverse("image-proxy-list", kwargs={"mailbox_id": mailbox_id})
            + f"?url={quote(url)}"
        )

    def _mock_requests_response(
        self,
        content=b"",
        content_type="image/jpeg",
        status_code=200,
        content_length=None,
        headers=None,
    ):
        """Helper to create a mock requests response."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.headers = headers or {}
        mock_response.headers.setdefault("Content-Type", content_type)
        if content_length is not None:
            mock_response.headers["Content-Length"] = str(content_length)

        # Mock iter_content to yield chunks
        # Note: iter_content is called multiple times, so we need to return a new iterator each time
        mock_response.iter_content = MagicMock(return_value=io.BytesIO(content))

        mock_response.raise_for_status = MagicMock()
        return mock_response

    # Authentication & Authorization Tests

    def test_api_image_proxy_unauthenticated_request_unauthorized(self):
        """Test that unauthenticated requests are rejected."""
        client = APIClient()
        mailbox = factories.MailboxFactory()
        url = self._get_image_proxy_url(mailbox.id, "https://example.com/image.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_image_proxy_mailbox_not_found(self, api_client):
        """Test that non-existent mailbox returns 404."""
        client, _ = api_client
        fake_mailbox_id = "00000000-0000-0000-0000-000000000000"
        url = self._get_image_proxy_url(
            fake_mailbox_id, "https://example.com/image.jpg"
        )

        response = client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Mailbox not found" in str(response.data)

    def test_api_image_proxy_user_without_mailbox_no_access(self, api_client):
        """Test that users without mailbox access are denied."""
        client, _ = api_client
        # Create a mailbox the user doesn't have access to
        mailbox = factories.MailboxFactory()
        url = self._get_image_proxy_url(mailbox.id, "https://example.com/image.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Forbidden" in str(response.data)

    @override_settings(IMAGE_PROXY_ENABLED=False)
    def test_api_image_proxy_feature_disabled(self, api_client, user_mailbox):
        """Test that proxy returns 403 when feature is disabled."""
        client, _ = api_client
        url = self._get_image_proxy_url(
            user_mailbox.id, "https://example.com/image.jpg"
        )

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Image proxy not enabled" in str(response.data)

    # URL Parameter Tests

    @override_settings(IMAGE_PROXY_ENABLED=True)
    def test_api_image_proxy_missing_url_parameter(self, api_client, user_mailbox):
        """Test that missing URL parameter returns 400."""
        client, _ = api_client
        url = reverse("image-proxy-list", kwargs={"mailbox_id": user_mailbox.id})

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing url parameter" in str(response.data)

    # SSRF Protection Tests

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @pytest.mark.parametrize(
        "test_url",
        [
            "ftp://example.com/image.jpg",
            "file:///etc/passwd",
            "data:image/png;base64,iVBORw0KGgo=",
            "javascript:alert(1)",
        ],
    )
    def test_api_image_proxy_invalid_url_scheme(
        self, api_client, user_mailbox, test_url
    ):
        """Test that non-http/https schemes are blocked."""
        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @pytest.mark.parametrize(
        "test_url",
        [
            "http://127.0.0.1/image.jpg",
            "http://192.168.1.1/image.jpg",
            "http://10.0.0.1/image.jpg",
            "http://[::1]/image.jpg",
            "http://[fe80::1]/image.jpg",
        ],
    )
    def test_api_image_proxy_ip_addresses_blocked(
        self, api_client, user_mailbox, test_url
    ):
        """Test that direct IP addresses are blocked."""
        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @pytest.mark.parametrize(
        "test_url",
        [
            "http://localhost/image.jpg",
            "http://localhost.localdomain/image.jpg",
            "https://localhost:8080/image.jpg",
        ],
    )
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    def test_api_image_proxy_localhost_hostname_blocked(
        self, mock_getaddrinfo, api_client, user_mailbox, test_url
    ):
        """Test that localhost as hostname is blocked."""
        # Mock DNS resolution to return localhost IP
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 80)),
        ]

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    def test_api_image_proxy_domain_resolving_to_private_ip_blocked(
        self, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that domains resolving to private IPs are blocked."""
        # Mock DNS resolution to return a private IP
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", ("192.168.1.1", 80)),
        ]

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://evil.com/image.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    def test_api_image_proxy_unresolvable_hostname(
        self, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that unresolvable hostnames are blocked."""
        mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")

        client, _ = api_client
        url = self._get_image_proxy_url(
            user_mailbox.id, "http://nonexistent.example/image.jpg"
        )

        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response["Content-Type"] == "image/svg+xml"

    # Content-Type Validation Tests

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_non_image_content_type_blocked(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that non-image content types are blocked."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock HTTP response with non-image content type
        mock_get.return_value = self._mock_requests_response(
            content=b"<html>Not an image</html>",
            content_type="text/html",
        )

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/page.html")

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_content_not_actually_image(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that content claimed as image but isn't is blocked."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock HTTP response claiming to be an image
        mock_get.return_value = self._mock_requests_response(
            content=b"<html>Fake image</html>",
            content_type="image/jpeg",
        )

        # Mock magic to detect it's actually HTML
        mock_magic.return_value = "text/html"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/fake.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    @pytest.mark.parametrize(
        "blacklisted_image_type",
        enums.BLACKLISTED_PROXY_IMAGE_MIME_TYPES,
    )
    def test_api_image_proxy_blacklisted_image_types_blocked(
        self,
        mock_magic,
        mock_get,
        mock_getaddrinfo,
        api_client,
        user_mailbox,
        blacklisted_image_type,
    ):
        """Test that blacklisted image types are blocked for security."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # SVG content
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        mock_get.return_value = self._mock_requests_response(
            content=svg_content,
            content_type=blacklisted_image_type,
        )

        # Mock magic to detect blacklisted image type
        mock_magic.return_value = blacklisted_image_type

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.svg")

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response["Content-Type"] == "image/svg+xml"

    # Size Limit Tests

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=1)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_image_too_large_via_content_length(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that images exceeding size limit via Content-Length are rejected."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock response with Content-Length exceeding limit (2MB > 1MB limit)
        mock_get.return_value = self._mock_requests_response(
            content=b"",
            content_type="image/jpeg",
            content_length=2 * 1024 * 1024,
        )

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/large.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        assert "Image too large" in str(response.data)

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=4096)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_image_too_large_actual_content(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that images exceeding size limit in actual content are rejected."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Create content larger than 1 byte limit
        large_content = b"0" * 10000
        mock_get.return_value = self._mock_requests_response(
            content=large_content,
            content_type="image/jpeg",
        )

        # Mock magic to accept as image
        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/large.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        assert "Image too large" in str(response.data)

    # Redirect Tests

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_redirects_blocked(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that redirects are not followed (SSRF protection)."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        client, _ = api_client
        url = self._get_image_proxy_url(
            user_mailbox.id, "http://example.com/redirect.jpg"
        )

        mock_get.return_value = self._mock_requests_response(
            content=b"", content_type="text/plain", status_code=302
        )

        # Call the endpoint
        client.get(url)

        # Verify that allow_redirects=False was passed to requests.get
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["allow_redirects"] is False

    # Success Cases

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_CACHE_TTL=3600)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_successfully_jpg_image(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test successfully proxying a valid image."""
        # Mock DNS resolution to a public IP
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Create a small valid image
        image_content = b"\xff\xd8\xff" + (b"x" * 1000)  # JPEG magic bytes + content
        mock_get.return_value = self._mock_requests_response(
            content=image_content,
            content_type="image/jpeg",
            content_length=len(image_content),
        )

        # Mock magic to detect JPEG
        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        test_url = "http://example.com/image.jpg"
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "public, max-age=3600"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_successfully_png_image(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test successfully proxying a PNG image."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # PNG magic bytes + content
        png_content = b"\x89PNG\r\n\x1a\n" + (b"x" * 1000)
        mock_get.return_value = self._mock_requests_response(
            content=png_content,
            content_type="image/png",
        )

        # Mock magic to detect PNG
        mock_magic.return_value = "image/png"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.png")

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/png"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_successfully_with_octet_stream_content_type(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test proxying image with generic octet-stream content type."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Image with generic content type (some servers do this)
        image_content = b"\xff\xd8\xff" + (b"x" * 1000)
        mock_get.return_value = self._mock_requests_response(
            content=image_content,
            content_type="application/octet-stream",
        )

        # Mock magic to detect actual JPEG
        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.bin")

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/jpeg"

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_CACHE_TTL=3600)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_no_content(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test that no content is returned when the content is None."""
        # Mock DNS resolution to a public IP
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Create a small valid image
        mock_get.return_value = self._mock_requests_response(
            content=None,
            content_type="image/jpeg",
            content_length=2_000_000,
        )

        # Mock magic to detect JPEG
        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        test_url = "http://example.com/image.jpg"
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response["Content-Type"] == "image/svg+xml"

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_url_with_special_characters(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test proxying URL with special characters (URL encoding)."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        image_content = b"\xff\xd8\xff" + (b"x" * 1000)
        mock_get.return_value = self._mock_requests_response(
            content=image_content,
            content_type="image/jpeg",
        )

        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        # URL with spaces and special chars
        test_url = "http://example.com/images/my photo (1).jpg?foo=bar&baz=qux"
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Verify the decoded URL was used in the request
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == test_url

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_CACHE_TTL=3600)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_successfully_secure_headers(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test successfully proxying should contain secure headers."""
        # Mock DNS resolution to a public IP
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Create a small valid image
        image_content = b"\xff\xd8\xff" + (b"x" * 1000)  # JPEG magic bytes + content
        mock_get.return_value = self._mock_requests_response(
            content=image_content,
            content_type="image/jpeg",
            content_length=len(image_content),
        )

        # Mock magic to detect JPEG
        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        test_url = "http://example.com/image.jpg"
        url = self._get_image_proxy_url(user_mailbox.id, test_url)

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "public, max-age=3600"
        assert response["X-Frame-Options"] == "DENY"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Referrer-Policy"] == "same-origin"
        assert response["Content-Security-Policy"] == "default-src 'none'"
        assert response["Permissions-Policy"] == "()"

    # Error Handling Tests

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_network_timeout(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test handling of network timeout errors."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Simulate timeout
        mock_get.side_effect = requests.Timeout("Connection timeout")

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/slow.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to fetch image" in str(response.data)

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_connection_error(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test handling of connection errors."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Simulate connection error
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.jpg")

        response = client.get(url)

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to fetch image" in str(response.data)

    @override_settings(IMAGE_PROXY_ENABLED=True, IMAGE_PROXY_MAX_SIZE=10)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    def test_api_image_proxy_http_error_404(
        self, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test handling of HTTP 404 errors."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock 404 response
        mock_response = self._mock_requests_response(
            content=b"Not Found",
            status_code=404,
        )
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        client, _ = api_client
        url = self._get_image_proxy_url(
            user_mailbox.id, "http://example.com/missing.jpg"
        )

        response = client.get(url)

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to fetch image" in str(response.data)

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_invalid_content_length_header(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test handling of invalid Content-Length header."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock response with invalid Content-Length
        image_content = b"\xff\xd8\xff" + (b"x" * 1000)
        mock_get.return_value = self._mock_requests_response(
            content=image_content,
            content_type="image/jpeg",
            headers={"Content-Length": "invalid"},
        )

        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.jpg")

        # Should succeed (falls back to streaming validation)
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK

    @override_settings(IMAGE_PROXY_ENABLED=True)
    @patch("core.api.viewsets.image_proxy.socket.getaddrinfo")
    @patch("core.api.viewsets.image_proxy.SSRFSafeSession.get")
    @patch("magic.from_buffer")
    def test_api_image_proxy_missing_content_length_header(
        self, mock_magic, mock_get, mock_getaddrinfo, api_client, user_mailbox
    ):
        """Test handling of missing Content-Length header."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("1.2.3.4", 80))]

        # Mock response without Content-Length
        image_content = b"\xff\xd8\xff" + (b"x" * 1000)
        mock_response = self._mock_requests_response(
            content=image_content,
            content_type="image/jpeg",
            content_length=None,
        )
        mock_get.return_value = mock_response

        mock_magic.return_value = "image/jpeg"

        client, _ = api_client
        url = self._get_image_proxy_url(user_mailbox.id, "http://example.com/image.jpg")

        # Should succeed (validates during streaming)
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
