"""Tests for SMTP submission server."""

import smtplib
from email.mime.text import MIMEText

import pytest

from .conftest import SMTP_HOST, SMTP_PORT


class TestSMTPAuth:
    """Test SMTP authentication."""

    def test_auth_success(self, smtp_client, test_channel):
        """Authenticated client should be connected."""
        # smtp_client fixture already authenticates successfully
        assert smtp_client.sock is not None

    def test_auth_wrong_password(self, smtp_connection, test_channel):
        """Wrong password should be rejected."""
        with pytest.raises(smtplib.SMTPAuthenticationError):
            smtp_connection.login(test_channel["mailbox_email"], "wrong-password")

    def test_auth_nonexistent_mailbox(self, smtp_connection):
        """Non-existent email address should be rejected."""
        with pytest.raises(smtplib.SMTPAuthenticationError):
            smtp_connection.login("nobody@nonexistent.example", "some-password")

    def test_auth_empty_password(self, smtp_connection, test_channel):
        """Empty password should be rejected."""
        with pytest.raises(smtplib.SMTPAuthenticationError):
            smtp_connection.login(test_channel["mailbox_email"], "")


class TestSMTPSendMessage:
    """Test SMTP message submission."""

    def test_send_message(self, smtp_client, test_channel, mock_api):
        """Sending a message via SMTP should succeed."""
        msg = MIMEText("Hello, this is a test message.")
        msg["Subject"] = "Test from SMTP"
        msg["From"] = "test@example.com"
        msg["To"] = "recipient@example.com"

        initial_count = len(mock_api.submitted_messages)
        smtp_client.sendmail(
            "test@example.com",
            ["recipient@example.com"],
            msg.as_string(),
        )

        assert len(mock_api.submitted_messages) == initial_count + 1
        submitted = mock_api.submitted_messages[-1]
        assert submitted["channel_token"]  # JWT token was forwarded
        assert submitted["mail_from"] == "test@example.com"
        assert "recipient@example.com" in submitted["rcpt_to"]

    def test_send_without_auth(self, smtp_server):
        """Sending without authentication should fail."""
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        client.ehlo()

        msg = MIMEText("Unauthorized message.")
        msg["Subject"] = "Should fail"
        msg["From"] = "attacker@example.com"
        msg["To"] = "victim@example.com"

        with pytest.raises(smtplib.SMTPSenderRefused):
            client.sendmail(
                "attacker@example.com",
                ["victim@example.com"],
                msg.as_string(),
            )
        client.quit()

    def test_send_multiple_recipients(self, smtp_client, test_channel, mock_api):
        """Sending to multiple recipients should work."""
        msg = MIMEText("Multi-recipient test.")
        msg["Subject"] = "Multi-recipient"
        msg["From"] = "test@example.com"
        msg["To"] = "user1@example.com, user2@example.com"

        initial_count = len(mock_api.submitted_messages)
        smtp_client.sendmail(
            "test@example.com",
            ["user1@example.com", "user2@example.com"],
            msg.as_string(),
        )

        assert len(mock_api.submitted_messages) == initial_count + 1
        submitted = mock_api.submitted_messages[-1]
        assert "user1@example.com" in submitted["rcpt_to"]
        assert "user2@example.com" in submitted["rcpt_to"]
