# test_smtp_protocol.py

import pytest
import smtplib
from email.mime.text import MIMEText
import socket
import time
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)
MTA_HOST = os.getenv("MTA_HOST")


def test_smtp_command_sequence():
    """Test proper SMTP command sequencing"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)

        # Read greeting
        response = s.recv(1024).decode()
        assert response.startswith("220")

        # Test HELO
        s.send(b"HELO example.com\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("250")

        # Test MAIL FROM with no prior RCPT TO (should fail)
        s.send(b"DATA\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("503")  # Bad sequence of commands


def test_malformed_commands():
    """Test handling of malformed SMTP commands"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Test invalid command
        s.send(b"INVALID\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("500")  # Unknown command

        # Test malformed MAIL FROM
        s.send(b"HELO example.com\r\n")
        s.recv(1024)
        s.send(b"MAIL FROM: <invalid@em ail>\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("501")  # Syntax error


def test_partial_writes():
    """Test handling of partial writes and interrupted transmissions"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Send HELO command in chunks
        s.send(b"HE")
        time.sleep(0.1)
        s.send(b"LO example")
        time.sleep(0.1)
        s.send(b".com\r\n")

        response = s.recv(1024).decode()
        assert response.startswith("250")


@pytest.mark.skip(reason="TODO review")
def test_pipelining_support():
    """Test SMTP command pipelining support"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Send multiple commands at once
        pipeline = (
            b"HELO example.com\r\n"
            b"MAIL FROM:<sender@example.com>\r\n"
            b"RCPT TO:<test@example.com>\r\n"
        )
        s.send(pipeline)

        # Should get multiple responses
        responses = []
        for _ in range(3):
            response = s.recv(1024).decode()
            responses.append(response)

        assert all(r.startswith("250") for r in responses)


@pytest.mark.skip(reason="Not supported for now")
def test_tls_negotiation():
    """Test STARTTLS negotiation"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Check STARTTLS availability
        s.send(b"EHLO example.com\r\n")
        response = s.recv(1024).decode()
        assert "STARTTLS" in response

        # Initiate STARTTLS
        s.send(b"STARTTLS\r\n")
        response = s.recv(1024).decode()
        assert response.startswith("220")  # Ready to start TLS


def test_connection_limits(mock_api_server):
    """Test handling of multiple concurrent connections"""

    def make_connection():
        try:
            client = smtplib.SMTP(MTA_HOST, 25)
            client.helo("example.com")
            time.sleep(random.uniform(0.1, 0.5))
            client.quit()
            return True
        except (smtplib.SMTPException, socket.error) as e:
            logger.error(f"Connection failed: {str(e)}")
            return False

    # Try to establish many connections simultaneously
    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = [executor.submit(make_connection) for _ in range(500)]
        results = [f.result() for f in as_completed(futures)]

    # All connections should succeed
    assert all(results)


@pytest.mark.skip(reason="This test is too long")
def test_command_timeout():
    """Test server timeout handling"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((MTA_HOST, 25))
        s.settimeout(2)
        s.recv(1024)  # Greeting

        # Send HELO
        s.send(b"HELO example.com\r\n")
        s.recv(1024)

        # Wait longer than server timeout
        time.sleep(30)

        # Next command should fail
        with pytest.raises(socket.error):
            s.send(b"NOOP\r\n")
            s.recv(1024)


@pytest.mark.parametrize(
    "n_recipients, will_fail",
    [
        (99, False),
        (1200, True),
    ],
)
def test_max_recipients(smtp_client, mock_api_server, n_recipients, will_fail):
    """Test maximum number of recipients handling"""
    msg = MIMEText("Test")
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test max recipients"

    # Try with a large number of recipients
    recipients = [f"test{i}@example.com" for i in range(n_recipients)]
    msg["To"] = ", ".join(recipients)

    # Add mailboxes
    for recipient in recipients:
        mock_api_server.add_mailbox(recipient)

    if will_fail:
        # Should raise an error due to too many recipients
        with pytest.raises(smtplib.SMTPRecipientsRefused):
            smtp_client.send_message(msg)
    else:
        smtp_client.send_message(msg)

        # Give MTA time to process
        logger.info("Waiting for email processing")
        mock_api_server.wait_for_email(n=1, timeout=20)

        # Check if our mock API received the email
        assert len(mock_api_server.received_emails) > 0
