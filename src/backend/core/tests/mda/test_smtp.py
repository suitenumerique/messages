"""Tests for SMTP client functionality."""

import logging
import socket
import threading
import time

import pytest

from core.mda.smtp import SmtpProxy, send_smtp_mail

logger = logging.getLogger(__name__)


# pylint: disable=too-many-instance-attributes
class MixedResponseSMTPHandler:
    """Custom SMTP handler that returns different responses for different recipients."""

    def __init__(self):
        self.recipient_responses = {}
        self.mail_from_response = None  # Configure MAIL FROM response
        self.data_response = None  # Configure DATA command response
        self.ehlo_sleep = None  # Configure EHLO timeout
        self.advertise_starttls = False  # Add STARTTLS to EHLO extensions
        self.starttls_break_handshake = False  # Reply 220 then close socket
        self.server_socket = None
        self.server_thread = None
        self.running = False
        self.port = 0

    def configure_recipient_response(self, email: str, code: int, message: str):
        """Configure response for a specific recipient."""
        self.recipient_responses[email] = (code, message)

    def configure_mail_from_response(self, code: int, message: str):
        """Configure response for MAIL FROM command."""
        self.mail_from_response = (code, message)

    def configure_data_response(self, code: int, message: str):
        """Configure response for DATA command."""
        self.data_response = (code, message)

    def configure_ehlo_sleep(self, sleep_time: int):
        """Configure EHLO sleep time."""
        self.ehlo_sleep = sleep_time

    def start(self):  # pylint: disable=too-many-statements
        """Start the SMTP server."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen(1)
        self.running = True

        def handle_client(client_socket):  # pylint: disable=too-many-branches,too-many-statements
            try:
                # Send welcome message
                client_socket.send(b"220 Test SMTP Server\r\n")

                while self.running:
                    data = client_socket.recv(1024)
                    if not data:
                        break

                    raw = data.decode("utf-8", errors="ignore").strip()
                    verb, _, rest = raw.partition(" ")
                    command = verb.upper()

                    if command in {"EHLO", "HELO"}:
                        if self.ehlo_sleep:
                            time.sleep(self.ehlo_sleep)
                        if self.advertise_starttls:
                            client_socket.send(
                                b"250-mock.example\r\n250-STARTTLS\r\n250 OK\r\n"
                            )
                        else:
                            client_socket.send(b"250 OK\r\n")
                    elif command == "STARTTLS":
                        client_socket.send(b"220 Ready to start TLS\r\n")
                        if self.starttls_break_handshake:
                            # Don't speak TLS — client handshake will fail.
                            client_socket.close()
                            break
                    elif command == "MAIL" and rest.upper().startswith("FROM:"):
                        rest = rest[5:].strip()
                        if self.mail_from_response:
                            code, message = self.mail_from_response
                            response = f"{code} {message}\r\n".encode("utf-8")
                            client_socket.send(response)
                        else:
                            client_socket.send(b"250 OK\r\n")
                    elif command == "RCPT" and rest.upper().startswith("TO:"):
                        rest = rest[3:].strip()
                        # Extract email from RCPT TO command
                        email = (
                            rest.split("<")[1].split(">")[0]
                            if "<" in rest
                            else rest.split(":")[1].strip()
                        )
                        logger.info("RCPT TO: %s", email)

                        if email in self.recipient_responses:
                            code, message = self.recipient_responses[email]
                            response = f"{code} {message}\r\n".encode("utf-8")
                            logger.info(
                                "Sending specific response: %s %s", code, message
                            )
                            client_socket.send(response)
                        else:
                            logger.info("Sending success response for %s", email)
                            client_socket.send(b"250 OK\r\n")
                    elif command == "DATA":
                        client_socket.send(
                            b"354 Start mail input; end with <CRLF>.<CRLF>\r\n"
                        )
                        # Read the message data
                        while True:
                            data = client_socket.recv(1024)
                            if b"\r\n.\r\n" in data:
                                break
                        # Send configured response or default success
                        if self.data_response:
                            code, message = self.data_response
                            response = f"{code} {message}\r\n".encode("utf-8")
                            client_socket.send(response)
                        else:
                            client_socket.send(b"250 OK\r\n")
                    elif command == "QUIT":
                        client_socket.send(b"221 Bye\r\n")
                        break
                    else:
                        client_socket.send(b"500 Unknown command\r\n")

            finally:
                client_socket.close()

        # Start server thread
        def _server_thread():
            while self.running:
                try:
                    client_socket, _ = self.server_socket.accept()
                    threading.Thread(
                        target=handle_client, args=(client_socket,)
                    ).start()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug("SMTP handler error: %s", e)
                    break

        self.server_thread = threading.Thread(target=_server_thread)
        self.server_thread.daemon = True
        self.server_thread.start()

    def stop(self):
        """Stop the SMTP server."""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=1.0)


class MockSMTPServer:
    """Simple mock SMTP server for testing."""

    def __init__(self, host="127.0.0.1", port=0):
        """Initialize the mock SMTP server."""
        self.host = host
        self.port = port
        self.handler = MixedResponseSMTPHandler()
        self.running = False

    def start(self):
        """Start the mock SMTP server."""
        self.handler.start()
        self.port = self.handler.port
        self.running = True
        logger.info("Mock SMTP server started on %s:%s", self.host, self.port)

    def stop(self):
        """Stop the mock SMTP server."""
        if self.running:
            self.handler.stop()
            self.running = False
            logger.info("Mock SMTP server stopped")

    def configure_recipient_response(self, email: str, code: int, message: str):
        """Configure response for a specific recipient."""
        self.handler.configure_recipient_response(email, code, message)

    def configure_mail_from_response(self, code: int, message: str):
        """Configure response for MAIL FROM command."""
        self.handler.configure_mail_from_response(code, message)

    def configure_data_response(self, code: int, message: str):
        """Configure response for DATA command."""
        self.handler.configure_data_response(code, message)


@pytest.fixture(name="mock_smtp_server")
def fixture_mock_smtp_server():
    """Create a mock SMTP server for testing."""
    server = MockSMTPServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


class TestSMTPClient:
    """Test cases for SMTP client functionality."""

    def test_successful_delivery(self, mock_smtp_server):
        """Test successful delivery to all recipients."""
        recipients = {"user1@example.com", "user2@example.com"}
        message = b"Subject: Test\n\nHello World!"

        result = send_smtp_mail(
            smtp_host="127.0.0.1",
            smtp_port=mock_smtp_server.port,
            envelope_from="sender@example.com",
            recipient_emails=recipients,
            message_content=message,
            timeout=5,
        )

        assert len(result) == 2
        for recipient in recipients:
            assert result[recipient]["delivered"] is True

    def test_timeout_delivery(self):
        """Test delivery to all recipients with timeout."""

        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.configure_ehlo_sleep(10)
        smtp_handler.start()

        start_time = time.time()

        try:
            recipients = {"user1@example.com", "user2@example.com"}
            message = b"Subject: Test\n\nHello World!"

            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails=recipients,
                message_content=message,
                timeout=1,
            )

            assert len(result) == 2
            for recipient in recipients:
                assert result[recipient]["delivered"] is False
                assert result[recipient]["retry"] is True

            assert time.time() - start_time < 2

        finally:
            smtp_handler.stop()

    def test_mixed_recipient_responses(self):
        """Test mixed delivery scenarios using a real SMTP server with
        different responses per recipient."""
        # Create and start the custom SMTP server
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.configure_recipient_response(
            "user2@example.com", 450, "Temporary failure"
        )
        smtp_handler.configure_recipient_response(
            "user3@example.com", 550, "Permanent failure"
        )
        smtp_handler.configure_recipient_response(
            "USer1@example.com", 521, "Permanent failure"
        )
        smtp_handler.start()

        try:
            # Give the server a moment to start
            time.sleep(0.1)

            recipients = {
                "user1@example.com",
                "user2@example.com",
                "user3@example.com",
                "USer1@example.com",
            }
            message = b"Subject: Test\n\nHello World!"

            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails=recipients,
                message_content=message,
                timeout=5,
            )

            # Verify the results
            assert len(result) == 4

            # user1 should succeed (not in the recipients_refused dict)
            assert result["user1@example.com"]["delivered"] is True

            # user2 should fail with temporary error (retry=True)
            assert result["user2@example.com"]["delivered"] is False
            assert "Temporary failure" in result["user2@example.com"]["error"]
            assert result["user2@example.com"]["retry"] is True

            # user3 should fail with permanent error (retry=False)
            assert result["user3@example.com"]["delivered"] is False
            assert "Permanent failure" in result["user3@example.com"]["error"]
            assert result["user3@example.com"]["retry"] is False

            # USer1 (uppercase) should fail with permanent error (retry=False)
            assert result["USer1@example.com"]["delivered"] is False
            assert "Permanent failure" in result["USer1@example.com"]["error"]
            assert result["USer1@example.com"]["retry"] is False

        finally:
            smtp_handler.stop()

    def test_connection_refused(self):
        """Test handling of connection refused error."""
        recipients = {"user1@example.com"}
        message = b"Subject: Test\n\nHello World!"

        result = send_smtp_mail(
            smtp_host="127.0.0.1",
            smtp_port=9999,  # Port that should be closed
            envelope_from="sender@example.com",
            recipient_emails=recipients,
            message_content=message,
            timeout=1,
        )

        assert len(result) == 1
        assert result["user1@example.com"]["delivered"] is False
        assert result["user1@example.com"]["error"]
        assert result["user1@example.com"]["retry"] is True

    def test_proxy_parameters(self):
        """Test that proxy parameters are passed correctly."""
        recipients = {"user1@example.com"}
        message = b"Subject: Test\n\nHello World!"

        # Test with proxy parameters
        result = send_smtp_mail(
            smtp_host="127.0.0.1",
            smtp_port=9999,  # Port that should be closed
            envelope_from="sender@example.com",
            recipient_emails=recipients,
            message_content=message,
            timeout=1,
            proxy=SmtpProxy(
                host="proxy.example.com",
                port=1080,
                username="proxyuser",
                password="proxypass",
            ),
        )

        assert len(result) == 1
        assert result["user1@example.com"]["delivered"] is False
        # Should fail with proxy connection error (since proxy server doesn't exist)
        assert "proxy" in result["user1@example.com"]["error"].lower()

    def test_mail_from_failure(self):
        """Test MAIL FROM failure handling."""
        # Create and start the custom SMTP server
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.configure_mail_from_response(550, "Mailbox unavailable")
        smtp_handler.start()

        try:
            # Give the server a moment to start
            time.sleep(0.1)

            recipients = {"user1@example.com"}
            message = b"Subject: Test\n\nHello World!"

            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails=recipients,
                message_content=message,
                timeout=5,
            )

            # Should fail due to MAIL FROM rejection
            assert len(result) == 1
            assert result["user1@example.com"]["delivered"] is False
            assert "Mailbox unavailable" in result["user1@example.com"]["error"]
            assert (
                result["user1@example.com"]["retry"] is False
            )  # 550 is permanent error

        finally:
            smtp_handler.stop()

    def test_secure_defers_when_starttls_not_advertised(self):
        """At smtp_tls_security_level=secure, a server that doesn't advertise
        STARTTLS must cause delivery to defer rather than fall through to
        cleartext."""
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.start()

        try:
            time.sleep(0.1)
            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails={"user1@example.com"},
                message_content=b"Subject: Test\n\nHello",
                timeout=5,
                smtp_tls_security_level="secure",
            )

            assert result["user1@example.com"]["delivered"] is False
            assert result["user1@example.com"]["retry"] is True
            assert "STARTTLS" in result["user1@example.com"]["error"]
        finally:
            smtp_handler.stop()

    def test_may_falls_back_when_starttls_handshake_fails(self):
        """At smtp_tls_security_level=may, a TLS handshake failure (e.g. cert
        mismatch) must transparently fall back to cleartext and deliver.
        Reproduces the Mandrill/SES regression that motivated this code path."""
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.advertise_starttls = True
        smtp_handler.starttls_break_handshake = True
        smtp_handler.start()

        try:
            time.sleep(0.1)
            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails={"user1@example.com"},
                message_content=b"Subject: Test\n\nHello",
                timeout=5,
                smtp_tls_security_level="may",
            )

            assert result["user1@example.com"]["delivered"] is True
        finally:
            smtp_handler.stop()

    def test_secure_fails_on_starttls_handshake_failure(self):
        """At smtp_tls_security_level=secure, a STARTTLS handshake failure must
        defer delivery rather than fall through to cleartext."""
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.advertise_starttls = True
        smtp_handler.starttls_break_handshake = True
        smtp_handler.start()

        try:
            time.sleep(0.1)
            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails={"user1@example.com"},
                message_content=b"Subject: Test\n\nHello",
                timeout=5,
                smtp_tls_security_level="secure",
            )

            assert result["user1@example.com"]["delivered"] is False
            assert result["user1@example.com"]["retry"] is True
        finally:
            smtp_handler.stop()

    def test_data_failure(self):
        """Test DATA command failure handling."""
        # Create and start the custom SMTP server
        smtp_handler = MixedResponseSMTPHandler()
        smtp_handler.configure_data_response(550, "Message rejected")
        smtp_handler.start()

        try:
            # Give the server a moment to start
            time.sleep(0.1)

            recipients = {"user1@example.com"}
            message = b"Subject: Test\n\nHello World!"

            result = send_smtp_mail(
                smtp_host="127.0.0.1",
                smtp_port=smtp_handler.port,
                envelope_from="sender@example.com",
                recipient_emails=recipients,
                message_content=message,
                timeout=5,
            )

            # Should fail due to DATA command rejection
            assert len(result) == 1
            assert result["user1@example.com"]["delivered"] is False
            assert "Message rejected" in result["user1@example.com"]["error"]
            assert (
                result["user1@example.com"]["retry"] is False
            )  # 550 is permanent error

        finally:
            smtp_handler.stop()
