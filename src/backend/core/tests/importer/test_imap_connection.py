"""Tests for IMAP connection manager and security features."""

# pylint: disable=redefined-outer-name,invalid-name

import imaplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from core.services.importer.imap import IMAPConnectionManager, IMAPSecurityError

# Store reference to the real error class before any patching
# This is needed because patching imaplib.IMAP4 affects the module globally
IMAP4_ERROR = imaplib.IMAP4.error


class TestIMAPConnectionManagerSSLDirect:
    """Tests for SSL direct connections (typically port 993)."""

    @patch("core.services.importer.imap.imaplib.IMAP4_SSL")
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

    @patch("core.services.importer.imap.imaplib.IMAP4_SSL")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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

    @patch("core.services.importer.imap.imaplib.IMAP4_SSL")
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

    @patch("core.services.importer.imap.imaplib.IMAP4")
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
