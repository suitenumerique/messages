import logging
import os
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

# Get environment variables
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


def test_send_simple_text_email(smtp_client, mock_smtp_server):
    """Test sending simple text email through the MTA-out service"""
    # Create a simple text email
    message = MIMEText("This is a test email from MTA-out")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test Simple Text Email"

    # Clear any previous messages and send the email
    mock_smtp_server.clear_messages()

    assert len(mock_smtp_server.get_messages()) == 0

    # Send through our authenticated SMTP client
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    # Give some time for the message to be relayed and received by the mock server
    max_retries = 10
    for attempt in range(max_retries):
        if len(mock_smtp_server.get_messages()) > 0:
            break
        time.sleep(0.5)

    assert len(mock_smtp_server.get_messages()) == 1

    # Verify the message was received by the mock SMTP server
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Simple Text Email"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"

    assert smtp_client.noop()[0] == 250, (
        "SMTP client should still be connected after sending"
    )


def test_send_simple_text_email_localhost(smtp_client, mock_smtp_server):
    """Test sending simple text email through the MTA-out service"""
    # Create a simple text email
    message = MIMEText("This is a test email from MTA-out")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@localhost"
    message["Subject"] = "Test Simple Text Email"

    # Clear any previous messages and send the email
    mock_smtp_server.clear_messages()

    assert len(mock_smtp_server.get_messages()) == 0

    # Send through our authenticated SMTP client
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    # Give some time for the message to be relayed and received by the mock server
    max_retries = 10
    for attempt in range(max_retries):
        if len(mock_smtp_server.get_messages()) > 0:
            break
        time.sleep(0.5)

    assert len(mock_smtp_server.get_messages()) == 1

    # Verify the message was received by the mock SMTP server
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Simple Text Email"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@localhost"

    assert smtp_client.noop()[0] == 250, (
        "SMTP client should still be connected after sending"
    )


def test_send_html_email(smtp_client, mock_smtp_server):
    """Test sending HTML email through the MTA-out service"""
    # Create a multipart message with HTML
    message = MIMEMultipart("alternative")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test HTML Email"

    # Add text and HTML parts
    text_part = MIMEText("This is a plain text part of the email", "plain")
    html_part = MIMEText(
        "<html><body><h1>Test HTML Email</h1><p>This is an HTML email</p></body></html>",
        "html",
    )

    message.attach(text_part)
    message.attach(html_part)

    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test HTML Email"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"


def test_send_multiple_recipients(smtp_client, mock_smtp_server):
    """Test sending email to multiple recipients"""
    # Create an email with multiple recipients
    message = MIMEText("This is a test email for multiple recipients")
    message["From"] = "sender@example.com"
    message["To"] = "recipient1@external-domain.com, recipient2@other-domain.com"
    message["Subject"] = "Test Multiple Recipients"

    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Multiple Recipients"
    assert received["from"] == "sender@example.com"
    assert (
        received["to"] == "recipient1@external-domain.com, recipient2@other-domain.com"
    )


def test_send_with_cc_and_bcc(smtp_client, mock_smtp_server):
    """Test sending email with CC and BCC fields"""
    # Create an email with CC and BCC
    message = MIMEText("This is a test email with CC and BCC")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["CC"] = "cc@external-domain.com"
    message["BCC"] = (
        "bcc@external-domain.com"  # BCC won't appear in headers after sending
    )
    message["Subject"] = "Test CC and BCC"

    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test CC and BCC"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"
    assert received["cc"] == "cc@external-domain.com"
    assert received["bcc"] is None  # BCC should not be included in headers
