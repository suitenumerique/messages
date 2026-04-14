"""Unit tests for core.services.ssrf — focus on redirect handling.

Hostname/IP validation itself is covered via the image proxy integration tests
(see core/tests/api/test_image_proxy.py). This file targets the per-hop
validation loop in SSRFSafeSession.get, which is the main SSRF-correctness
surface for redirect responses.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

from core.services.ssrf import (
    MAX_REDIRECTS,
    SSRFSafeSession,
    SSRFValidationError,
)

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "192.168.1.1"


def _addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _mock_response(status_code: int = 200, location: str | None = None):
    """Build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    if location is not None:
        resp.headers["Location"] = location
    return resp


class TestSSRFSafeSessionRedirects:
    """Redirect-handling contract for SSRFSafeSession.get."""

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_no_redirect_returns_response_directly(self, mock_dns, mock_get):
        """A 200 response is returned without any extra request."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(200)

        response = SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

        assert response.status_code == 200
        assert mock_get.call_count == 1

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_follows_redirect_to_safe_url(self, mock_dns, mock_get):
        """A single redirect is followed to a validated destination."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.side_effect = [
            _mock_response(302, location="https://cdn.legit.com/img.png"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

        assert response.status_code == 200
        assert mock_get.call_count == 2

    @pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_all_redirect_statuses_followed(self, mock_dns, mock_get, redirect_status):
        """All standard redirect status codes trigger a new hop."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.side_effect = [
            _mock_response(redirect_status, location="https://b.com/img.png"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().get("https://a.com/img.png", timeout=10)

        assert response.status_code == 200
        assert mock_get.call_count == 2

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_follows_multiple_hops(self, mock_dns, mock_get):
        """Chained redirects are followed up to MAX_REDIRECTS."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.side_effect = [
            _mock_response(302, location="https://b.com/"),
            _mock_response(302, location="https://c.com/"),
            _mock_response(302, location="https://d.com/"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().get("https://a.com/", timeout=10)

        assert response.status_code == 200
        assert mock_get.call_count == 4

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_redirect_to_private_ip(self, mock_dns, mock_get):
        """A Location resolving to a private IP is rejected mid-chain."""

        def dns_side_effect(host, *_args, **_kwargs):
            if host == "legit.com":
                return _addrinfo(PUBLIC_IP)
            if host == "internal.evil.com":
                return _addrinfo(PRIVATE_IP)
            raise AssertionError(f"unexpected DNS lookup: {host}")

        mock_dns.side_effect = dns_side_effect
        mock_get.return_value = _mock_response(
            302, location="https://internal.evil.com/pwn"
        )

        with pytest.raises(SSRFValidationError, match="private IP"):
            SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

        # Only the first hop should have been issued; the second was blocked
        # before the request was made.
        assert mock_get.call_count == 1

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_redirect_to_loopback(self, mock_dns, mock_get):
        """Redirect pointing at loopback (DNS-rebinding style) is rejected."""

        def dns_side_effect(host, *_args, **_kwargs):
            if host == "legit.com":
                return _addrinfo(PUBLIC_IP)
            if host == "rebind.evil.com":
                return _addrinfo("127.0.0.1")
            raise AssertionError(f"unexpected DNS lookup: {host}")

        mock_dns.side_effect = dns_side_effect
        mock_get.return_value = _mock_response(302, location="https://rebind.evil.com/")

        with pytest.raises(SSRFValidationError, match="loopback"):
            SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_redirect_to_cloud_metadata(self, mock_dns, mock_get):
        """Redirect pointing at cloud metadata endpoint is rejected."""

        def dns_side_effect(host, *_args, **_kwargs):
            if host == "legit.com":
                return _addrinfo(PUBLIC_IP)
            if host == "meta.evil.com":
                return _addrinfo("169.254.169.254")
            raise AssertionError(f"unexpected DNS lookup: {host}")

        mock_dns.side_effect = dns_side_effect
        mock_get.return_value = _mock_response(
            302, location="https://meta.evil.com/latest/meta-data/"
        )

        with pytest.raises(SSRFValidationError, match="metadata"):
            SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_redirect_to_non_http_scheme(self, mock_dns, mock_get):
        """Redirect to file:// or other schemes is rejected."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(302, location="file:///etc/passwd")

        with pytest.raises(SSRFValidationError, match="scheme"):
            SSRFSafeSession().get("https://legit.com/", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_redirect_to_ip_literal(self, mock_dns, mock_get):
        """Redirect whose Location is a raw IP is rejected (domains only)."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(302, location="http://203.0.113.5/stuff")

        with pytest.raises(SSRFValidationError, match="IP addresses are not allowed"):
            SSRFSafeSession().get("https://legit.com/", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_protocol_relative_redirect_to_private(self, mock_dns, mock_get):
        """//host/path Location resolves against current scheme and is validated."""

        def dns_side_effect(host, *_args, **_kwargs):
            if host == "legit.com":
                return _addrinfo(PUBLIC_IP)
            if host == "internal":
                return _addrinfo(PRIVATE_IP)
            raise AssertionError(f"unexpected DNS lookup: {host}")

        mock_dns.side_effect = dns_side_effect
        mock_get.return_value = _mock_response(302, location="//internal/admin")

        with pytest.raises(SSRFValidationError, match="private IP"):
            SSRFSafeSession().get("https://legit.com/", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_follows_relative_redirect(self, mock_dns, mock_get):
        """Relative Location is resolved against the current URL and validated."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.side_effect = [
            _mock_response(302, location="/new-path"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().get("https://legit.com/old", timeout=10)

        assert response.status_code == 200
        # Second request should be made to https://legit.com/new-path.
        second_call_url = mock_get.call_args_list[1].args[0]
        assert second_call_url == "https://legit.com/new-path"

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_too_many_redirects(self, mock_dns, mock_get):
        """A redirect loop longer than MAX_REDIRECTS raises."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(302, location="https://legit.com/loop")

        with pytest.raises(SSRFValidationError, match="Too many redirects"):
            SSRFSafeSession().get("https://legit.com/", timeout=10)

        # Initial hop + MAX_REDIRECTS extra hops, then error.
        assert mock_get.call_count == MAX_REDIRECTS + 1

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_redirect_without_location_returned_as_is(self, mock_dns, mock_get):
        """A redirect status with no Location header is returned unchanged."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(302, location=None)

        response = SSRFSafeSession().get("https://legit.com/", timeout=10)

        assert response.status_code == 302
        assert mock_get.call_count == 1

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_caller_allow_redirects_true_is_ignored(self, mock_dns, mock_get):
        """Caller cannot opt out of per-hop validation by passing allow_redirects=True."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.side_effect = [
            _mock_response(302, location="https://cdn.legit.com/"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().get(
            "https://legit.com/", timeout=10, allow_redirects=True
        )

        assert response.status_code == 200
        # Each underlying Session.get must be called with allow_redirects=False.
        for call in mock_get.call_args_list:
            assert call.kwargs.get("allow_redirects") is False

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_intermediate_response_closed(self, mock_dns, mock_get):
        """Intermediate redirect responses are .close()d to release streams."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        intermediate = _mock_response(302, location="https://cdn.legit.com/")
        final = _mock_response(200)
        mock_get.side_effect = [intermediate, final]

        SSRFSafeSession().get("https://legit.com/", timeout=10, stream=True)

        intermediate.close.assert_called_once()
        final.close.assert_not_called()
