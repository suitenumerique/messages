"""Tests for session token expiry in IMAP and SMTP paths.

Uses the running IMAP/SMTP servers with a channel whose JWT expires
in ~2 seconds to verify clean disconnection on expiry.
"""

import imaplib
import smtplib
import time
from email.mime.text import MIMEText

import pytest

from .conftest import EXPIRY_SECONDS, IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT


class TestIMAPSessionExpiry:
    """Test that IMAP sessions are closed when the JWT token expires."""

    def test_select_after_expiry(self, imap_server, expiring_channel):
        """SELECT should fail after the session token expires."""
        client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        client.login(expiring_channel["mailbox_email"], expiring_channel["password"])

        # Wait for the JWT to expire
        time.sleep(EXPIRY_SECONDS + 1)

        # SELECT triggers _load_messages() -> _check_token() -> SessionExpired -> CloseConnection
        with pytest.raises((imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError)):
            client.select("INBOX")

    def test_immediate_select_works(self, imap_server, expiring_channel):
        """SELECT should succeed when the token is still valid."""
        client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        client.login(expiring_channel["mailbox_email"], expiring_channel["password"])

        # Immediately select — token is fresh
        status, _ = client.select("INBOX")
        assert status == "OK"

        try:
            client.logout()
        except Exception:
            pass


class TestSMTPSessionExpiry:
    """Test that SMTP handle_DATA checks token expiry."""

    def test_send_after_expiry(self, smtp_server, expiring_channel):
        """Sending after token expiry should fail with 421."""
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        client.ehlo()
        client.login(expiring_channel["mailbox_email"], expiring_channel["password"])

        # Wait for the JWT to expire
        time.sleep(EXPIRY_SECONDS + 1)

        msg = MIMEText("This should fail - token expired.")
        msg["Subject"] = "Expiry test"
        msg["From"] = "expiring@example.com"
        msg["To"] = "recipient@example.com"

        with pytest.raises(smtplib.SMTPResponseException) as exc_info:
            client.sendmail(
                "expiring@example.com",
                ["recipient@example.com"],
                msg.as_string(),
            )
        assert exc_info.value.smtp_code == 421

    def test_immediate_send_works(self, smtp_server, expiring_channel, mock_api):
        """Sending immediately should succeed when the token is still valid."""
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        client.ehlo()
        client.login(expiring_channel["mailbox_email"], expiring_channel["password"])

        msg = MIMEText("This should succeed - token is fresh.")
        msg["Subject"] = "No expiry test"
        msg["From"] = "expiring@example.com"
        msg["To"] = "recipient@example.com"

        initial_count = len(mock_api.submitted_messages)
        client.sendmail(
            "expiring@example.com",
            ["recipient@example.com"],
            msg.as_string(),
        )
        assert len(mock_api.submitted_messages) == initial_count + 1

        try:
            client.quit()
        except Exception:
            pass
