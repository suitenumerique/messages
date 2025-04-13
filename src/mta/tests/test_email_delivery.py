import pytest
import smtplib
from email.mime.text import MIMEText
import time
import logging
import os
import subprocess
import requests
import socket

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

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        smtp_client.send_message(msg)
    
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

    assert email["email"].is_multipart() == False
    body = email["email"].get_payload()

    # TODO: why the \n ?
    assert body == "This is a test email\n"


def test_simple_email_delivery_with_multiple_recipients(mock_api_server, smtp_client):
    """Test simple email delivery via SMTP with multiple recipients"""

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "sender@example.com"
    msg["To"] = "test@example.com, test2@example.com"


def test_relay(mock_api_server, smtp_client):
    """Test sending outgoing emails. Should not be allowed."""

    mock_api_server.add_mailbox("test@example.com")

    # Create a simple text email
    msg = MIMEText("This is a test email\n")
    msg["From"] = "test@example.com"
    msg["To"] = "other@example.com"
    msg["Subject"] = "Simple Test Email"

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        smtp_client.send_message(msg)
    