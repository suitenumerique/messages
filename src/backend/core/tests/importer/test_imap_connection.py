"""Tests for IMAP connection manager and security features."""

# pylint: disable=redefined-outer-name,invalid-name,protected-access

import imaplib
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from core.services.importer.imap import (
    IMAPAuthError,
    IMAPConnectionManager,
    IMAPSecurityError,
    _extract_imap_flags_and_content,
    _IPPinnedIMAP4,
    _IPPinnedIMAP4SSL,
    _parse_imap_folder_info,
    _validate_imap_host,
    create_folder_mapping,
    decode_imap_utf7,
    select_imap_folder,
    uid_fetch_message,
    uid_search_all,
)
from core.services.importer.utils import TransientImportError
from core.services.ssrf import SSRFValidationError


class TestImapHelperHardening:
    def test_decode_imap_utf7_is_crash_safe(self):
        # A malformed base64 shift sequence must fall back to the raw name
        # rather than raising (it runs while building the folder map).
        assert decode_imap_utf7("Inbox&A-x") == "Inbox&A-x"
        # A well-formed sequence still decodes (&AOk- -> é).
        assert decode_imap_utf7("caf&AOk-") == "café"

    @patch("core.services.importer.imap._IPPinnedIMAP4SSL")
    def test_login_failure_raises_clean_actionable_message(self, mock_ssl):
        """A rejected login must surface a human-readable message (no ``b'...'``
        bytestring) — that string is shown to users."""
        conn = MagicMock()
        conn.login.side_effect = imaplib.IMAP4.error(
            b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        )
        mock_ssl.return_value = conn
        with pytest.raises(IMAPAuthError) as exc:
            with IMAPConnectionManager(
                server="imap.gmail.com",
                port=993,
                username="u@example.com",
                password="wrong",
                use_ssl=True,
            ):
                pass
        msg = str(exc.value)
        assert "b'" not in msg and 'b"' not in msg  # bytes repr stripped
        assert "Invalid credentials (Failure)" in msg
        assert "Check the username and password" in msg

    def test_uid_fetch_message_retries_transient_timeout(self):
        conn = MagicMock()
        state = {"n": 0}

        def _uid(_cmd, _uid_arg, _items):
            state["n"] += 1
            if state["n"] == 1:
                raise socket.timeout("blip")
            return ("OK", [(b"1 (FLAGS (\\Seen) BODY[] {3}", b"abc")])

        conn.uid.side_effect = _uid
        with patch("core.services.importer.imap.time.sleep"):
            flags, raw = uid_fetch_message(conn, 1)
        assert state["n"] == 2  # one retry, then success
        assert raw == b"abc"

    def test_uid_fetch_message_raises_after_retry_budget_exhausted(self):
        """A persistent timeout must escape after UID_FETCH_MAX_RETRIES (3)
        attempts — the caller treats it as transient and parks the run
        resumable."""
        conn = MagicMock()
        conn.uid.side_effect = socket.timeout("still down")
        with patch("core.services.importer.imap.time.sleep"):
            with pytest.raises(socket.timeout):
                uid_fetch_message(conn, 7)
        assert conn.uid.call_count == 3


class TestImapFolderHelpers:
    def test_parse_folder_info_variants(self):
        assert (
            _parse_imap_folder_info('(\\HasNoChildren) "/" "INBOX.Sent"')
            == "INBOX.Sent"
        )
        # Non-selectable folders are skipped entirely.
        assert _parse_imap_folder_info('(\\Noselect) "/" "[Gmail]"') is None
        # Malformed lines degrade to None, never raise.
        assert _parse_imap_folder_info("garbage") is None

    def test_select_imap_folder_tries_quoted_variant(self):
        """The first (bare) SELECT failing must not give up: the quoted
        variant is attempted next and its success is reported."""
        conn = MagicMock()
        conn.select.side_effect = [("NO", [b"bad"]), ("OK", [b"1"])]
        assert select_imap_folder(conn, "My Folder") is True
        assert conn.select.call_count == 2
        assert conn.select.call_args_list[1].args[0] == '"My Folder"'

    def test_select_imap_folder_gives_up_after_all_variants(self):
        conn = MagicMock()
        conn.select.return_value = ("NO", [b"nope"])
        assert select_imap_folder(conn, "Ghost") is False

    def test_create_folder_mapping_strips_inbox_prefix_for_orange(self):
        mapping = create_folder_mapping(
            ["INBOX/Envoy&AOk-s"], "user@orange.fr", "imap.orange.fr"
        )
        # Display name drops the INBOX/ prefix and decodes IMAP-UTF7.
        assert mapping["INBOX/Envoy&AOk-s"] == "Envoyés"

    def test_extract_flags_from_bytes_response_part(self):
        """Some servers send FLAGS as a bare bytes part, not tuple metadata."""
        raw = b"x" * 200
        flags, content = _extract_imap_flags_and_content(
            [b"1 (FLAGS (\\Seen \\Flagged))", raw]
        )
        assert flags == ["\\Seen", "\\Flagged"]
        assert content == raw


class TestUidSearchAll:
    def _conn(self, responses):
        """responses: list of (status, data) returned per successive .uid()."""
        conn = MagicMock()
        conn.uid.side_effect = list(responses)
        return conn

    def test_full_scan_empty_result_unions_fallback_criteria(self):
        """Some servers answer a full SEARCH ALL with nothing on a non-empty
        folder: the fallback criteria are tried and their results unioned."""
        conn = self._conn(
            [
                ("OK", [b""]),  # ALL -> empty
                ("OK", [b"1 3"]),  # RECENT
                ("OK", [b"2"]),  # UNSEEN
                RuntimeError("SEEN unsupported"),  # SEEN -> ignored
                ("NO", [b""]),  # NEW
                ("OK", [b""]),  # OLD
            ]
        )
        with patch("core.services.importer.imap.select_imap_folder", return_value=True):
            assert uid_search_all(conn, "INBOX") == [1, 2, 3]

    def test_incremental_empty_result_is_no_new_mail_when_uidnext_agrees(self):
        """An empty *incremental* search is the normal case — but it is only
        trusted after a cheap UIDNEXT cross-check confirms no UIDs exist above
        the watermark (buggy servers answer ranged searches empty too)."""
        conn = self._conn([("OK", [b""])])
        conn.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 11)'])
        with patch("core.services.importer.imap.select_imap_folder", return_value=True):
            assert uid_search_all(conn, "INBOX", since_uid=10) == []
        assert conn.uid.call_count == 1  # no full-scan fallback was needed

    def test_incremental_empty_result_falls_back_when_uidnext_disagrees(self):
        """A bogus empty ranged answer must not read as 'no new mail' when the
        server's own UIDNEXT says UIDs exist above the watermark — that would
        silently end a resume early / freeze a continuous poller forever."""
        conn = self._conn(
            [
                ("OK", [b""]),  # UID 11:* -> bogus empty
                ("OK", [b"1 5 12"]),  # ALL -> the truth
            ]
        )
        conn.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 13)'])
        with patch("core.services.importer.imap.select_imap_folder", return_value=True):
            # The caller filters ``> since_uid``; returning low UIDs is fine.
            assert uid_search_all(conn, "INBOX", since_uid=10) == [1, 5, 12]

    def test_incremental_empty_result_falls_back_when_status_denied(self):
        """An unreadable STATUS means the empty answer can't be verified —
        fall back to the full scan rather than trusting it."""
        conn = self._conn(
            [
                ("OK", [b""]),  # UID 11:* -> empty
                ("OK", [b"12"]),  # ALL
            ]
        )
        conn.status.return_value = ("NO", [b""])
        with patch("core.services.importer.imap.select_imap_folder", return_value=True):
            assert uid_search_all(conn, "INBOX", since_uid=10) == [12]

    def test_incremental_empty_result_reaches_criteria_union_fallback(self):
        """When even the full ALL answers empty, the alternative-criteria
        union (the original buggy-server workaround) applies to resumes too."""
        conn = self._conn(
            [
                ("OK", [b""]),  # UID 11:* -> empty
                ("OK", [b""]),  # ALL -> empty too
                ("OK", [b"3"]),  # RECENT
                ("OK", [b"12 13"]),  # UNSEEN
                ("OK", [b""]),  # SEEN
                ("OK", [b""]),  # NEW
                ("OK", [b""]),  # OLD
            ]
        )
        conn.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 14)'])
        with patch("core.services.importer.imap.select_imap_folder", return_value=True):
            assert uid_search_all(conn, "INBOX", since_uid=10) == [3, 12, 13]

    def test_unselectable_folder_raises_transient(self):
        """An unselectable folder must not read as empty — a oneshot run would
        complete with the folder's mail silently missing. Raising keeps the
        run resumable; a persistent failure surfaces via the stall budget."""
        conn = self._conn([])
        with patch(
            "core.services.importer.imap.select_imap_folder", return_value=False
        ):
            with pytest.raises(TransientImportError):
                uid_search_all(conn, "Ghost")
        conn.uid.assert_not_called()


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

        with pytest.raises(IMAPAuthError):
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

        with pytest.raises(IMAPAuthError):
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
