import pytest
import smtplib
import logging
import os

logger = logging.getLogger(__name__)

# Get environment variables
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
MTA_OUT_HOST = os.getenv("MTA_OUT_HOST")


def test_smtp_authentication_success(smtp_client):
    """Test successful SMTP authentication with correct credentials"""
    # Connection with authentication already established in fixture
    # Just verify that the client is connected and authenticated
    assert smtp_client.noop()[0] == 250


def test_smtp_authentication_invalid_password():
    """Test failed SMTP authentication with incorrect password"""
    client = smtplib.SMTP(MTA_OUT_HOST, 587)
    client.ehlo()
    client.starttls()
    client.ehlo()
    
    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login(SMTP_USERNAME, "wrong_password")
    
    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass


def test_smtp_authentication_invalid_username():
    """Test failed SMTP authentication with incorrect username"""
    client = smtplib.SMTP(MTA_OUT_HOST, 587)
    client.ehlo()
    client.starttls()
    client.ehlo()
    
    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login("wrong_username", SMTP_PASSWORD)
    
    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass


def test_smtp_authentication_empty_credentials():
    """Test failed SMTP authentication with empty credentials"""
    client = smtplib.SMTP(MTA_OUT_HOST, 587)
    client.ehlo()
    client.starttls()
    client.ehlo()
    
    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login("", "")
    
    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass


def test_unauthenticated_relay_attempt():
    """Test rejection of relay attempt without authentication"""
    # Create client without authentication
    client = smtplib.SMTP(MTA_OUT_HOST, 587)
    client.ehlo()
    client.starttls()
    client.ehlo()
    
    # Try to send email without logging in
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        client.sendmail(
            "sender@example.com",
            ["recipient@example.com"],
            "From: sender@example.com\nTo: recipient@example.com\nSubject: Test\n\nTest message"
        )
    
    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass 