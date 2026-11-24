"""Tests for revoked credentials in IMAP and SMTP sessions.

The backend checks the app password on every request, so rotating it in the
middle of a session must cut the session off.
"""

import imaplib
import smtplib
from email.mime.text import MIMEText

import pytest

from .conftest import IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT


def _message(sender):
    msg = MIMEText("Hello.")
    msg["Subject"] = "Revocation test"
    msg["From"] = sender
    msg["To"] = "recipient@example.com"
    return msg.as_string()


class TestIMAPRevocation:
    """IMAP sessions after the app password is rotated."""

    def test_select_works_while_valid(self, imap_server, revocable_channel):
        client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        client.login(revocable_channel["mailbox_email"], revocable_channel["password"])
        status, _ = client.select("INBOX")
        assert status == "OK"
        client.logout()

    def test_select_after_rotation(self, imap_server, revocable_channel, mock_api):
        client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        client.login(revocable_channel["mailbox_email"], revocable_channel["password"])

        mock_api.rotate_password(revocable_channel["channel_id"])

        # SELECT loads messages -> 401 -> CredentialsRevoked -> CloseConnection
        with pytest.raises((imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError)):
            client.select("INBOX")


class TestSMTPRevocation:
    """SMTP sessions after the app password is rotated."""

    def test_send_works_while_valid(self, smtp_server, revocable_channel, mock_api):
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        client.ehlo()
        client.login(revocable_channel["mailbox_email"], revocable_channel["password"])
        initial_count = len(mock_api.submitted_messages)
        sender = revocable_channel["mailbox_email"]
        client.sendmail(sender, ["recipient@example.com"], _message(sender))
        assert len(mock_api.submitted_messages) == initial_count + 1
        client.quit()

    def test_send_after_rotation(self, smtp_server, revocable_channel, mock_api):
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        client.ehlo()
        client.login(revocable_channel["mailbox_email"], revocable_channel["password"])

        mock_api.rotate_password(revocable_channel["channel_id"])

        sender = revocable_channel["mailbox_email"]
        with pytest.raises(smtplib.SMTPResponseException) as exc_info:
            client.sendmail(sender, ["recipient@example.com"], _message(sender))
        assert exc_info.value.smtp_code == 535
