"""Tests for IMAP connection manager and security features."""

# pylint: disable=redefined-outer-name,invalid-name,protected-access

import imaplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from core.services.importer.imap import (
    IMAPConnectionManager,
    IMAPSecurityError,
    _IPPinnedIMAP4,
    _IPPinnedIMAP4SSL,
    _validate_imap_host,
)
from core.services.ssrf import SSRFValidationError

# Store reference to the real error class before any patching
# This is needed because patching imaplib.IMAP4 affects the module globally
IMAP4_ERROR = imaplib.IMAP4.error


class TestIMAPSSRFPinning:
    """The IMAP importer connects to the validated IP, not a re-resolved
    hostname — closing the DNS-rebinding (TOCTOU) SSRF window."""

    def test_validate_imap_host_returns_first_validated_ip(self):
        """The first validated IP is the address pinned for the connection."""
        with patch(
            "core.services.importer.imap.validate_hostname",
            return_value=["203.0.113.5", "203.0.113.6"],
        ):
            assert _validate_imap_host("imap.example.com") == "203.0.113.5"

    def test_validate_imap_host_rejects_blocked_address(self):
        """A host resolving to a blocked address raises ValueError."""
        with patch(
            "core.services.importer.imap.validate_hostname",
            side_effect=SSRFValidationError("resolves to private IP address"),
        ):
            with pytest.raises(ValueError, match="not allowed"):
                _validate_imap_host("internal.evil.test")

    def test_pinned_imap4_dials_validated_ip(self):
        """Plain IMAP4 connects to the pinned IP, never re-resolving the host."""
        inst = _IPPinnedIMAP4.__new__(_IPPinnedIMAP4)
        inst._connect_ip = "203.0.113.5"
        inst.port = 143
        fake_sock = MagicMock()
        with patch(
            "core.services.importer.imap.socket.create_connection",
            return_value=fake_sock,
        ) as mock_conn:
            result = inst._create_socket(30)
        mock_conn.assert_called_once_with(("203.0.113.5", 143), 30)
        assert result is fake_sock

    def test_pinned_imap4ssl_pins_ip_and_verifies_hostname(self):
        """SSL: dial the pinned IP but verify the cert against the hostname."""
        inst = _IPPinnedIMAP4SSL.__new__(_IPPinnedIMAP4SSL)
        inst._connect_ip = "203.0.113.5"
        inst.port = 993
        inst.host = "imap.example.com"
        inst.ssl_context = MagicMock()
        raw_sock, wrapped = MagicMock(), MagicMock()
        inst.ssl_context.wrap_socket.return_value = wrapped
        with patch(
            "core.services.importer.imap.socket.create_connection",
            return_value=raw_sock,
        ) as mock_conn:
            result = inst._create_socket(30)
        mock_conn.assert_called_once_with(("203.0.113.5", 993), 30)
        inst.ssl_context.wrap_socket.assert_called_once_with(
            raw_sock, server_hostname="imap.example.com"
        )
        assert result is wrapped


class TestIMAPConnectionManagerSSLDirect:
    """Tests for SSL direct connections (typically port 993)."""

    @patch("core.services.importer.imap._IPPinnedIMAP4SSL")
    def test_ssl_direct_success(self, mock_imap4_ssl):
        """Test successful SSL direct connection on port 993."""
        mock_conn = MagicMock()
        mock_imap4_ssl.return_value = mock_conn

        with IMAPConnectionManager(
            server="imap.example.com",
            port=993,
            username="user@example.com",
            password="password",
            use_ssl=True,
        ) as conn:
            assert conn is mock_conn
            mock_imap4_ssl.assert_called_once()
            mock_conn.login.assert_called_once_with("user@example.com", "password")

    @patch("core.services.importer.imap._IPPinnedIMAP4SSL")
    def test_ssl_direct_handshake_failure(self, mock_imap4_ssl):
        """Test SSL handshake failure raises IMAPSecurityError."""
        mock_imap4_ssl.side_effect = ssl.SSLError("handshake failed")

        with pytest.raises(IMAPSecurityError) as exc_info:
            with IMAPConnectionManager(
                server="imap.example.com",
                port=993,
                username="user@example.com",
                password="password",
                use_ssl=True,
            ):
                pass

        assert "SSL handshake failed" in str(exc_info.value)
        assert "Try port 143 with STARTTLS" in str(exc_info.value)


class TestIMAPConnectionManagerSTARTTLS:
    """Tests for STARTTLS connections (typically port 143 with use_ssl=True)."""

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_starttls_success(self, mock_imap4):
        """Test successful STARTTLS upgrade on port 143."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        mock_conn.capability.return_value = ("OK", [b"IMAP4rev1 STARTTLS AUTH=PLAIN"])
        mock_conn.starttls.return_value = ("OK", [b"Begin TLS negotiation now"])

        with IMAPConnectionManager(
            server="imap.example.com",
            port=143,
            username="user@example.com",
            password="password",
            use_ssl=True,
        ) as conn:
            assert conn is mock_conn
            mock_conn.capability.assert_called_once()
            mock_conn.starttls.assert_called_once()
            mock_conn.login.assert_called_once_with("user@example.com", "password")

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_starttls_not_supported(self, mock_imap4):
        """Test STARTTLS not supported raises IMAPSecurityError."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        # Server capabilities without STARTTLS
        mock_conn.capability.return_value = ("OK", [b"IMAP4rev1 AUTH=PLAIN"])

        with pytest.raises(IMAPSecurityError) as exc_info:
            with IMAPConnectionManager(
                server="imap.example.com",
                port=143,
                username="user@example.com",
                password="password",
                use_ssl=True,
            ):
                pass

        assert "does not support STARTTLS" in str(exc_info.value)
        mock_conn.logout.assert_called_once()

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_starttls_negotiation_failure(self, mock_imap4):
        """Test STARTTLS negotiation failure raises IMAPSecurityError."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        mock_conn.capability.return_value = ("OK", [b"IMAP4rev1 STARTTLS"])
        mock_conn.starttls.return_value = ("NO", [b"TLS not available"])

        with pytest.raises(IMAPSecurityError) as exc_info:
            with IMAPConnectionManager(
                server="imap.example.com",
                port=143,
                username="user@example.com",
                password="password",
                use_ssl=True,
            ):
                pass

        assert "STARTTLS failed" in str(exc_info.value)
        mock_conn.logout.assert_called_once()

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_starttls_capability_empty_response(self, mock_imap4):
        """Test STARTTLS with empty capability response raises IMAPSecurityError."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        # Empty capability response
        mock_conn.capability.return_value = ("OK", [])

        with pytest.raises(IMAPSecurityError) as exc_info:
            with IMAPConnectionManager(
                server="imap.example.com",
                port=143,
                username="user@example.com",
                password="password",
                use_ssl=True,
            ):
                pass

        assert "does not support STARTTLS" in str(exc_info.value)

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_starttls_capability_none_response(self, mock_imap4):
        """Test STARTTLS with None capability response raises IMAPSecurityError."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        # None in capability response
        mock_conn.capability.return_value = ("OK", [None])

        with pytest.raises(IMAPSecurityError) as exc_info:
            with IMAPConnectionManager(
                server="imap.example.com",
                port=143,
                username="user@example.com",
                password="password",
                use_ssl=True,
            ):
                pass

        assert "does not support STARTTLS" in str(exc_info.value)


class TestIMAPConnectionManagerUnencrypted:
    """Tests for unencrypted connections (use_ssl=False)."""

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_unencrypted_connection(self, mock_imap4):
        """Test unencrypted connection when use_ssl=False."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn

        with IMAPConnectionManager(
            server="imap.example.com",
            port=143,
            username="user@example.com",
            password="password",
            use_ssl=False,
        ) as conn:
            assert conn is mock_conn
            # Should NOT call starttls when use_ssl=False
            mock_conn.starttls.assert_not_called()
            mock_conn.login.assert_called_once()


class TestIMAPConnectionManagerAuthentication:
    """Tests for authentication handling."""

    @patch("core.services.importer.imap._IPPinnedIMAP4SSL")
    def test_authentication_failure_cleanup(self, mock_imap4_ssl):
        """Test connection is cleaned up after authentication failure."""
        mock_conn = MagicMock()
        mock_imap4_ssl.return_value = mock_conn
        mock_conn.login.side_effect = IMAP4_ERROR("AUTHENTICATIONFAILED")

        with pytest.raises(IMAP4_ERROR):
            with IMAPConnectionManager(
                server="imap.example.com",
                port=993,
                username="user@example.com",
                password="wrongpassword",
                use_ssl=True,
            ):
                pass

        # Connection should be cleaned up via logout
        mock_conn.logout.assert_called_once()

    @patch("core.services.importer.imap._IPPinnedIMAP4")
    def test_authentication_failure_after_starttls(self, mock_imap4):
        """Test auth failure after successful STARTTLS still cleans up."""
        mock_conn = MagicMock()
        mock_imap4.return_value = mock_conn
        # Preserve the real error class so except clause can catch it
        mock_imap4.error = IMAP4_ERROR
        mock_conn.capability.return_value = ("OK", [b"STARTTLS"])
        mock_conn.starttls.return_value = ("OK", [b"OK"])
        mock_conn.login.side_effect = IMAP4_ERROR("AUTHENTICATIONFAILED")

        with pytest.raises(IMAP4_ERROR):
            with IMAPConnectionManager(
                server="imap.example.com",
                port=143,
                username="user@example.com",
                password="wrongpassword",
                use_ssl=True,
            ):
                pass

        # Connection should be cleaned up
        mock_conn.logout.assert_called_once()
