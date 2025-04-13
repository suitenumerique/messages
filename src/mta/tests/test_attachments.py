import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import time
import logging
import base64
import smtplib


logger = logging.getLogger(__name__)


def test_email_with_attachment(mock_api_server, smtp_client):

    mock_api_server.add_mailbox("test@example.com")

    # Create multipart message
    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "test@example.com"
    msg["Subject"] = "Email with attachment"
    
    # Add body
    msg.attach(MIMEText("This email has an attachment", "plain"))
    
    # Add attachment
    attachment = MIMEApplication("test file content".encode("utf-8"))
    attachment.add_header("Content-Disposition", "attachment", filename="test.txt")
    msg.attach(attachment)
    
    # Send email
    logger.info("Sending test email")
    smtp_client.send_message(msg)
    
    # Give MTA time to process
    logger.info("Waiting for email processing")
    mock_api_server.wait_for_email()
    
    # Check if our mock API received the email
    logger.info(f"Received emails: {len(mock_api_server.received_emails)}")
    
    # Verify email was received with attachment
    assert len(mock_api_server.received_emails) == 1
    email = mock_api_server.received_emails[0]
    assert email["metadata"]["original_recipients"] == ["test@example.com"]
    assert email["metadata"]["sender"] == "sender@example.com"
    assert email["email"]["subject"] == "Email with attachment"
    assert email["email"]["from"] == "sender@example.com"
    assert email["email"]["to"] == "test@example.com"
    
    assert email["email"].is_multipart() == True
    payloads = email["email"].get_payload()
    assert len(payloads) == 2
    assert payloads[0].get_content_type() == "text/plain"
    assert payloads[0].get_payload() == "This email has an attachment"

    attachment = payloads[1]
    assert attachment.get_content_type() == "application/octet-stream"
    assert attachment.get_payload().strip() == base64.b64encode("test file content".encode("utf-8")).decode("utf-8")


@pytest.mark.parametrize("attachment_size, will_fail", [
    (11 * 1024 * 1024, False),
    (40 * 1024 * 1024, True),
])
def test_email_with_large_attachments(mock_api_server, smtp_client, attachment_size, will_fail):
    mock_api_server.add_mailbox("test@example.com")

    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "test@example.com"
    msg["Subject"] = "Email with large attachment"

    # Create a large attachment
    large_attachment = "X" * attachment_size
    attachment = MIMEApplication(large_attachment.encode("utf-8"))
    attachment.add_header("Content-Disposition", "attachment", filename="large_file.txt")
    msg.attach(attachment)
    
    # Send email
    logger.info(f"Sending test email with attachment size {attachment_size}")

    if will_fail:
        with pytest.raises(smtplib.SMTPSenderRefused):
            smtp_client.send_message(msg)
    else:
        smtp_client.send_message(msg)
        
        # Give MTA time to process
        logger.info("Waiting for email processing")
        mock_api_server.wait_for_email()
        
        # Check if our mock API received the email
        assert len(mock_api_server.received_emails) == 1
        email = mock_api_server.received_emails[0]

        # Encoding makes it a bit bigger
        assert attachment_size < len(email['raw_email']) < attachment_size*2