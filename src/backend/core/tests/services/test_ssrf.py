"""Unit tests for core.services.ssrf — focus on redirect handling.

Hostname/IP validation itself is covered via the image proxy integration tests
(see core/tests/api/test_image_proxy.py). This file targets the per-hop
validation loop in SSRFSafeSession.get, which is the main SSRF-correctness
surface for redirect responses.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.services.ssrf import (
    MAX_REDIRECTS,
    SSRFProtectedAdapter,
    SSRFSafeSession,
    SSRFValidationError,
    assert_public_ip,
)

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "192.168.1.1"


class TestAssertPublicIP:
    """``assert_public_ip`` — the IP guard reused by the outbound SMTP path."""

    def test_public_ip_passes(self):
        """A routable public address passes (returns None, does not raise)."""
        assert assert_public_ip(PUBLIC_IP) is None

    @pytest.mark.parametrize(
        "ip, match",
        [
            ("10.0.0.5", "private"),
            ("192.168.1.1", "private"),
            ("172.16.0.1", "private"),
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
            ("169.254.169.254", "cloud metadata"),
            ("169.254.0.1", "link-local"),
            ("224.0.0.1", "multicast"),
            # Shared address space / CGNAT (RFC 6598): not is_private nor
            # is_reserved in Python's ipaddress, caught by the is_global guard.
            ("100.64.0.1", "non-global"),
        ],
    )
    def test_non_public_ip_raises(self, ip, match):
        """Private, reserved, loopback, metadata and CGNAT addresses are rejected."""
        with pytest.raises(SSRFValidationError, match=match):
            assert_public_ip(ip, "mx.evil.test")

    def test_invalid_ip_raises(self):
        """A non-parseable IP string raises an Invalid IP error."""
        with pytest.raises(SSRFValidationError, match="Invalid IP"):
            assert_public_ip("not-an-ip")


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
        """Redirect whose Location is a raw IP is rejected (domains only).

        Same-scheme (https→https) so this isolates the IP-literal check from
        the HTTPS→HTTP downgrade guard (covered separately below)."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(
            302, location="https://203.0.113.5/stuff"
        )

        with pytest.raises(SSRFValidationError, match="IP addresses are not allowed"):
            SSRFSafeSession().get("https://legit.com/", timeout=10)

    @patch("core.services.ssrf.requests.Session.get")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_blocks_https_to_http_downgrade(self, mock_dns, mock_get):
        """A redirect that drops from HTTPS to cleartext HTTP is refused, even
        to an otherwise-valid public host."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_get.return_value = _mock_response(302, location="http://cdn.legit.com/img")

        with pytest.raises(SSRFValidationError, match="downgrade"):
            SSRFSafeSession().get("https://legit.com/img.png", timeout=10)

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


class TestSSRFSafeSessionPostRedirects:
    """Redirect-handling contract for SSRFSafeSession.post.

    POST follows redirects too (a webhook endpoint behind a load balancer /
    canonicaliser commonly 3xx-redirects), re-validating each hop and
    re-issuing the POST so the signed body reaches the final destination.
    """

    @patch("core.services.ssrf.requests.Session.post")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_no_redirect_returns_response_directly(self, mock_dns, mock_post):
        """A 2xx POST is returned without any extra request."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_post.return_value = _mock_response(200)

        response = SSRFSafeSession().post(
            "https://hook.legit.com/in", timeout=10, data=b"payload"
        )

        assert response.status_code == 200
        assert mock_post.call_count == 1

    @pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
    @patch("core.services.ssrf.requests.Session.post")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_post_follows_redirect_preserving_method_and_body(
        self, mock_dns, mock_post, redirect_status
    ):
        """A 3xx on POST is followed by re-POSTing the same body to the
        validated Location (method preserved, never downgraded to GET)."""
        mock_dns.return_value = _addrinfo(PUBLIC_IP)
        mock_post.side_effect = [
            _mock_response(redirect_status, location="https://cdn.legit.com/in"),
            _mock_response(200),
        ]

        response = SSRFSafeSession().post(
            "https://hook.legit.com/in", timeout=10, data=b"payload"
        )

        assert response.status_code == 200
        assert mock_post.call_count == 2
        # The body rode along on the followed hop, and we never fell back to GET.
        assert mock_post.call_args.kwargs.get("data") == b"payload"

    @patch("core.services.ssrf.requests.Session.post")
    @patch("core.services.ssrf.socket.getaddrinfo")
    def test_post_blocks_redirect_to_private_ip(self, mock_dns, mock_post):
        """A POST Location resolving to a private IP is rejected mid-chain."""

        def dns_side_effect(host, *_args, **_kwargs):
            if host == "hook.legit.com":
                return _addrinfo(PUBLIC_IP)
            if host == "internal.evil.com":
                return _addrinfo(PRIVATE_IP)
            raise AssertionError(f"unexpected DNS lookup: {host}")

        mock_dns.side_effect = dns_side_effect
        mock_post.return_value = _mock_response(
            302, location="https://internal.evil.com/pwn"
        )

        with pytest.raises(SSRFValidationError, match="private IP"):
            SSRFSafeSession().post("https://hook.legit.com/in", timeout=10, data=b"x")

        assert mock_post.call_count == 1


class TestSSRFProtectedAdapterPinning:
    """The IP-pinning enforcement in ``SSRFProtectedAdapter``.

    This is the load-bearing TOCTOU / DNS-rebinding defense: after a
    hostname is validated to a concrete IP, the adapter must dial *that
    exact IP* (never re-resolve the hostname at connect time) while still
    presenting the original hostname for ``Host:`` routing and TLS
    certificate verification. The redirect tests above prove the
    *decision* to validate each hop; these prove the *enforcement* —
    that the request actually goes to the pinned IP.
    """

    def _prepared(self, url: str) -> requests.PreparedRequest:
        req = requests.PreparedRequest()
        req.prepare(method="POST", url=url, headers={}, data=b"payload")
        return req

    @patch("requests.adapters.HTTPAdapter.send")
    def test_send_rewrites_url_to_pinned_ipv4_and_keeps_host(self, mock_super_send):
        """The request URL is rewritten to the validated IPv4 (with port),
        not the hostname, and the Host header is set to the original
        hostname so virtual-hosted receivers still route correctly."""
        adapter = SSRFProtectedAdapter(
            dest_ip="93.184.216.34",
            dest_port=443,
            original_hostname="example.com",
            original_scheme="https",
        )
        request = self._prepared("https://example.com/path?q=1")

        adapter.send(request)

        # The parent adapter actually dials the rewritten request.
        sent_request = mock_super_send.call_args.args[0]
        assert sent_request.url == "https://93.184.216.34:443/path?q=1"
        # Hostname preserved for routing + TLS SNI/cert verification.
        assert sent_request.headers["Host"] == "example.com"

    @patch("requests.adapters.HTTPAdapter.send")
    def test_send_rewrites_url_to_bracketed_ipv6(self, mock_super_send):
        """An IPv6 destination is rewritten using the ``[addr]:port``
        netloc form so the URL stays well-formed."""
        adapter = SSRFProtectedAdapter(
            dest_ip="2606:2800:220:1:248:1893:25c8:1946",
            dest_port=8443,
            original_hostname="example.com",
            original_scheme="https",
        )
        request = self._prepared("https://example.com/path?q=1")

        adapter.send(request)

        sent_request = mock_super_send.call_args.args[0]
        assert sent_request.url == (
            "https://[2606:2800:220:1:248:1893:25c8:1946]:8443/path?q=1"
        )
        # The Host header reflects the ORIGINAL request URL (no explicit
        # port → bare hostname); the pinned dest_port only steers the
        # socket, it doesn't appear in Host.
        assert sent_request.headers["Host"] == "example.com"

    @patch("requests.adapters.HTTPAdapter.send")
    def test_send_host_header_carries_original_nondefault_port(self, mock_super_send):
        """When the ORIGINAL URL names a non-default port, that port rides
        in the Host header so the receiver routes to the right vhost:port."""
        adapter = SSRFProtectedAdapter(
            dest_ip="93.184.216.34",
            dest_port=8443,
            original_hostname="example.com",
            original_scheme="https",
        )
        request = self._prepared("https://example.com:8443/path?q=1")

        adapter.send(request)

        sent_request = mock_super_send.call_args.args[0]
        assert sent_request.url == "https://93.184.216.34:8443/path?q=1"
        assert sent_request.headers["Host"] == "example.com:8443"

    @patch("requests.adapters.HTTPAdapter.init_poolmanager")
    def test_init_poolmanager_pins_tls_hostname_for_https(self, mock_super_init):
        """For https, the pool manager is configured to verify the cert
        against (and send SNI for) the ORIGINAL hostname, even though the
        socket connects to the pinned IP."""
        SSRFProtectedAdapter(
            dest_ip="93.184.216.34",
            dest_port=443,
            original_hostname="example.com",
            original_scheme="https",
        )

        # __init__ calls init_poolmanager once during HTTPAdapter setup.
        assert mock_super_init.called
        pool_kwargs = mock_super_init.call_args.kwargs
        assert pool_kwargs["assert_hostname"] == "example.com"
        assert pool_kwargs["server_hostname"] == "example.com"

    @patch("requests.adapters.HTTPAdapter.init_poolmanager")
    def test_init_poolmanager_does_not_pin_tls_for_http(self, mock_super_init):
        """Plain http has no TLS handshake, so no hostname pinning kwargs
        are injected (they'd be meaningless / could error)."""
        SSRFProtectedAdapter(
            dest_ip="93.184.216.34",
            dest_port=80,
            original_hostname="example.com",
            original_scheme="http",
        )

        assert mock_super_init.called
        pool_kwargs = mock_super_init.call_args.kwargs
        assert "assert_hostname" not in pool_kwargs
        assert "server_hostname" not in pool_kwargs
