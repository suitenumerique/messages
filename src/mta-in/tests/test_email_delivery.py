import logging
import smtplib
import time
from email.mime.text import MIMEText

import pytest

logger = logging.getLogger(__name__)


def test_simple_email_delivery(mock_api_server, smtp_client):
    """Test simple email delivery via SMTP"""

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "sender@example.com"
    msg["To"] = "test@example.com"
    msg["Subject"] = "Simple Test Email"

    # Send email. At first, it won't be delivered because the mailbox doesn't exist.
    logger.info("Sending simple test email")

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        smtp_client.send_message(msg)
    # Permanent error
    assert excinfo.value.recipients["test@example.com"][0] // 100 == 5

    # Add the mailbox and try again. This time it will be delivered.
    mock_api_server.add_mailbox("test@example.com")

    smtp_client.send_message(msg)

    # Give MTA time to process
    logger.info("Waiting for email processing")
    mock_api_server.wait_for_email()

    assert len(mock_api_server.received_emails) == 1

    email = mock_api_server.received_emails[0]
    assert email["metadata"]["original_recipients"] == ["test@example.com"]
    assert email["metadata"]["sender"] == "sender@example.com"
    assert email["email"]["subject"] == "Simple Test Email"
    assert email["email"]["from"] == "sender@example.com"
    assert email["email"]["to"] == "test@example.com"

    assert not email["email"].is_multipart()
    body = email["email"].get_payload()

    # TODO: why the \n ?
    assert body == "This is a test email\r\n"


def test_simple_email_delivery_with_multiple_recipients(mock_api_server, smtp_client):
    """Test simple email delivery via SMTP with multiple recipients"""

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "sender@example.com"
    msg["To"] = "test@example.com, test2@example.com"
    msg["Subject"] = "Simple Test Email"

    # Send email. At first, it won't be delivered because the mailbox doesn't exist.
    logger.info("Sending simple test email")

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        smtp_client.send_message(msg)
    # Permanent error
    assert excinfo.value.recipients["test@example.com"][0] // 100 == 5
    assert excinfo.value.recipients["test2@example.com"][0] // 100 == 5

    # Add only one of the mailboxes and try again.
    # This would generate a bounce, that we'll test later.
    # For now the SMTP session should succeed, even if partially.
    mock_api_server.add_mailbox("test@example.com")

    smtp_client.send_message(msg)

    # Give MTA time to process
    logger.info("Waiting for email processing")
    mock_api_server.wait_for_email()

    assert len(mock_api_server.received_emails) == 1

    email = mock_api_server.received_emails[0]
    assert set(email["metadata"]["original_recipients"]) == set(["test@example.com"])
    assert email["metadata"]["sender"] == "sender@example.com"
    assert email["email"]["subject"] == "Simple Test Email"
    assert email["email"]["from"] == "sender@example.com"
    assert email["email"]["to"] == "test@example.com, test2@example.com"

    assert not email["email"].is_multipart()
    body = email["email"].get_payload()

    # TODO: why the \n ?
    assert body == "This is a test email\r\n"

    mock_api_server.received_emails = []

    # Add the other mailbox and try again. This time it will be delivered fully.
    mock_api_server.add_mailbox("test2@example.com")

    smtp_client.send_message(msg)

    # Give MTA time to process
    logger.info("Waiting for email processing")
    mock_api_server.wait_for_email()

    assert len(mock_api_server.received_emails) == 1

    email = mock_api_server.received_emails[0]
    assert set(email["metadata"]["original_recipients"]) == set(
        ["test@example.com", "test2@example.com"]
    )
    assert email["metadata"]["sender"] == "sender@example.com"
    assert email["email"]["subject"] == "Simple Test Email"
    assert email["email"]["from"] == "sender@example.com"
    assert email["email"]["to"] == "test@example.com, test2@example.com"

    assert not email["email"].is_multipart()
    body = email["email"].get_payload()

    # TODO: why the \n ?
    assert body == "This is a test email\r\n"


def test_email_delivery_with_non_utf8_header(mock_api_server, smtp_client):
    """A header carrying a raw non-UTF-8 byte must not crash the milter.

    pymilter hands header values to the callback decoded with
    utf-8/surrogateescape, so a Latin-1 byte (0xe9 = 'é') arrives as the lone
    surrogate '\\udce9'. Re-encoding it needs the same error handler — a plain
    "utf-8" encode raised UnicodeEncodeError and tempfailed the whole message.
    """

    mock_api_server.add_mailbox("test@example.com")

    # Raw 8-bit bytes: 0xe9 is 'é' in Latin-1 and is NOT valid UTF-8, both in a
    # standard header (Subject) and a custom one.
    raw_message = (
        b"From: sender@example.com\r\n"
        b"To: test@example.com\r\n"
        b"Subject: Caf\xe9 meeting\r\n"
        b"X-Custom-Header: R\xe9sum\xe9\r\n"
        b"\r\n"
        b"This is a test email\r\n"
    )

    # Before the fix this tempfailed (4xx) on the milter crash; it must now be
    # accepted and delivered.
    smtp_client.sendmail(
        "sender@example.com",
        ["test@example.com"],
        raw_message,
        mail_options=["BODY=8BITMIME"],
    )

    mock_api_server.wait_for_email()
    assert len(mock_api_server.received_emails) == 1

    # The raw 8-bit header bytes must round-trip to the MDA unchanged.
    raw_email = mock_api_server.received_emails[0]["raw_email"]
    assert b"Caf\xe9 meeting" in raw_email
    assert b"X-Custom-Header: R\xe9sum\xe9" in raw_email


def test_relay(mock_api_server, smtp_client):
    """Test sending outgoing emails. Should not be allowed."""

    mock_api_server.add_mailbox("test@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "other@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        smtp_client.send_message(msg)
    # Permanent error
    assert excinfo.value.recipients["other@example.com"][0] // 100 == 5

    # No email should be received
    time.sleep(1)  # Give some time for processing
    assert len(mock_api_server.received_emails) == 0


def test_check_recipients_error(mock_api_server, smtp_client):
    """Test check recipients error - should now result in temporary failure, not immediate rejection."""

    mock_api_server.add_mailbox("check-recipients-error@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "check-recipients-error@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        smtp_client.send_message(msg)
    # Temporary error
    assert excinfo.value.recipients["check-recipients-error@example.com"][0] // 100 == 4

    # No email should be received
    time.sleep(1)  # Give some time for processing
    assert len(mock_api_server.received_emails) == 0


def test_check_recipients_timeout(mock_api_server, smtp_client):
    """Test check recipients timeout - should now result in temporary failure, not immediate rejection."""

    mock_api_server.add_mailbox("check-recipients-timeout@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "check-recipients-timeout@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        smtp_client.send_message(msg)
    # Temporary error
    assert excinfo.value.recipients["check-recipients-timeout@example.com"][0] // 100 == 4

    # No email should be received
    time.sleep(1)  # Give some time for processing
    assert len(mock_api_server.received_emails) == 0


def test_inbound_email_error(mock_api_server, smtp_client):
    """Test inbound email error - should now result in temporary failure during delivery."""

    mock_api_server.add_mailbox("inbound-email-error@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "inbound-email-error@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPDataError) as excinfo:
        smtp_client.send_message(msg)
    # Temporary error
    assert excinfo.value.smtp_code // 100 == 4

    # No email should be received
    time.sleep(1)  # Give some time for processing
    assert len(mock_api_server.received_emails) == 0


def test_inbound_email_timeout(mock_api_server, smtp_client):
    """Test inbound email timeout - should now result in temporary failure during delivery."""

    mock_api_server.add_mailbox("inbound-email-timeout@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "inbound-email-timeout@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPDataError) as excinfo:
        smtp_client.send_message(msg)
    # Temporary error
    assert excinfo.value.smtp_code // 100 == 4

    # No email should be received
    time.sleep(1)  # Give some time for processing
    assert len(mock_api_server.received_emails) == 0
