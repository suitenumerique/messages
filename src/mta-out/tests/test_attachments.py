import pytest
import smtplib
import logging
import os
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage

logger = logging.getLogger(__name__)


def test_send_email_with_pdf_attachment(smtp_client, mock_smtp_server):
    """Test sending email with PDF attachment"""
    # Create a multipart message
    message = MIMEMultipart()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test Email with PDF Attachment"
    
    # Add text part
    text_part = MIMEText("This email contains a PDF attachment")
    message.attach(text_part)
    
    # Create a simple PDF (just a base64 encoded dummy PDF)
    pdf_data = base64.b64decode(
        "JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoKPDwvTGVuZ3RoIDMgMCBSL0ZpbHRlci9GbGF0ZURl"
        "Y29kZT4+CnN0cmVhbQp4nEWOOw6AIBDF9pyiR9gFdj1KYmGhjY3x/gVRYjQmvPm9GVhiDEbvBkp0"
        "HuXgN1oqpsR2B696MrwSm6LHTLQTNZAyI7zMs5YPIr8tef4KGbGGCZWd1eDOOxEK/S+UVzl9syAu"
        "RAplbmRzdHJlYW0KZW5kb2JqCjMgMCBvYmoKMTE3CmVuZG9iagoxIDAgb2JqCjw8L1RhYmxlcy9O"
        "dWxsL1R5cGUvQ2F0YWxvZy9QYWdlcyA0IDAgUj4+CmVuZG9iago0IDAgb2JqCjw8L0NvdW50IDEv"
        "S2lkc1s1IDAgUl0vVHlwZS9QYWdlcz4+CmVuZG9iago1IDAgb2JqCjw8L0NvbnRlbnRzIDIgMCBS"
        "L1R5cGUvUGFnZS9SZXNvdXJjZXM8PC9Qcm9jU2V0IFsvUERGIC9UZXh0IC9JbWFnZUIgL0ltYWdl"
        "QyAvSW1hZ2VJXS9Gb250PDwvRjEgNiAwIFI+Pj4+L1BhcmVudCA0IDAgUi9NZWRpYUJveFswIDAg"
        "NjEyIDc5Ml0+PgplbmRvYmoKNiAwIG9iago8PC9CYXNlRm9udC9IZWx2ZXRpY2EvVHlwZS9Gb250"
        "L0VuY29kaW5nL1dpbkFuc2lFbmNvZGluZy9TdWJ0eXBlL1R5cGUxPj4KZW5kb2JqCjcgMCBvYmoK"
        "PDwvQ3JlYXRpb25EYXRlKEQ6MjAyMzA2MTUxMjAwMDArMDInMDAnKS9Qcm9kdWNlcihQREZLaXQu"
        "TkVUKS9Nb2REYXRlKEQ6MjAyMzA2MTUxMjAwMDArMDInMDAnKT4+CmVuZG9iagp4cmVmCjAgOAow"
        "MDAwMDAwMDAwIDY1NTM1IGYNCjAwMDAwMDAyMDEgMDAwMDAgbg0KMDAwMDAwMDAxNSAwMDAwMCBu"
        "DQowMDAwMDAwMTgxIDAwMDAwIG4NCjAwMDAwMDAyNTQgMDAwMDAgbg0KMDAwMDAwMDMwOSAwMDAw"
        "MCBuDQowMDAwMDAwNDcyIDAwMDAwIG4NCjAwMDAwMDA1NjIgMDAwMDAgbg0KdHJhaWxlcgo8PC9J"
        "bmZvIDcgMCBSL0lEIFs8MTA4NzZkYTU1ODQ5ODZkZmIyOWVlNjRiZmYzZjRhZDE+IDwxMDg3NmRh"
        "NTU4NDk4NmRmYjI5ZWU2NGJmZjNmNGFkMT5dL1Jvb3QgMSAwIFIvU2l6ZSA4Pj4Kc3RhcnR4cmVm"
        "CjY2NAolJUVPRgo="
    )
    
    # Attach the PDF
    pdf_attachment = MIMEApplication(pdf_data, _subtype="pdf")
    pdf_attachment.add_header(
        "Content-Disposition", "attachment", filename="test.pdf"
    )
    message.attach(pdf_attachment)
    
    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Email with PDF Attachment"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"


def test_send_email_with_image_attachment(smtp_client, mock_smtp_server):
    """Test sending email with image attachment"""
    # Create a multipart message
    message = MIMEMultipart()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test Email with Image Attachment"
    
    # Add text part
    text_part = MIMEText("This email contains an image attachment")
    message.attach(text_part)
    
    # Create a simple 1x1 pixel PNG
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    
    # Attach the image
    img_attachment = MIMEImage(png_data, _subtype="png")
    img_attachment.add_header(
        "Content-Disposition", "attachment", filename="test.png"
    )
    message.attach(img_attachment)
    
    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Email with Image Attachment"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"


def test_send_email_with_inline_image(smtp_client, mock_smtp_server):
    """Test sending email with inline image in HTML"""
    # Create a multipart message
    message = MIMEMultipart("related")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test Email with Inline Image"
    
    # Create the HTML part with a reference to the inline image
    html = """
    <html>
      <body>
        <p>This is an HTML email with an inline image:</p>
        <img src="cid:image1" alt="Test Image">
      </body>
    </html>
    """
    html_part = MIMEText(html, "html")
    message.attach(html_part)
    
    # Create a simple 1x1 pixel PNG
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    
    # Attach the image with Content-ID for inline reference
    img = MIMEImage(png_data)
    img.add_header("Content-ID", "<image1>")
    img.add_header("Content-Disposition", "inline")
    message.attach(img)
    
    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict"

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Email with Inline Image"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"

def test_send_large_attachment(smtp_client, mock_smtp_server):
    """Test sending email with a large attachment"""
    # Create a multipart message
    message = MIMEMultipart()
    message["From"] = "sender@example.com"
    message["To"] = "recipient@external-domain.com"
    message["Subject"] = "Test Email with Large Attachment"
    
    # Add text part
    text_part = MIMEText("This email contains a large attachment")
    message.attach(text_part)
    
    # Create a large binary attachment (1MB of random-like data)
    large_data = b"\x00" * (1024 * 1024)  # 1MB of zeros
    
    # Attach the large file
    attachment = MIMEApplication(large_data, _subtype="octet-stream")
    attachment.add_header(
        "Content-Disposition", "attachment", filename="large_file.bin"
    )
    message.attach(attachment)
    
    mock_smtp_server.clear_messages()

    # Send the email
    response = smtp_client.send_message(message)
    assert not response, "Sending should succeed with empty response dict" 

    mock_smtp_server.wait_for_messages(1)
    received = mock_smtp_server.get_messages()[0]
    assert received["subject"] == "Test Email with Large Attachment"
    assert received["from"] == "sender@example.com"
    assert received["to"] == "recipient@external-domain.com"