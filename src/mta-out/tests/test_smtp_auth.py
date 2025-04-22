import pytest
import smtplib
import logging
import os

logger = logging.getLogger(__name__)

# Get environment variables
MTA_OUT_SMTP_USERNAME = os.getenv("MTA_OUT_SMTP_USERNAME")
MTA_OUT_SMTP_PASSWORD = os.getenv("MTA_OUT_SMTP_PASSWORD")
MTA_OUT_HOSTNAME = os.getenv("MTA_OUT_HOST").split(":")[0]
MTA_OUT_PORT = int(os.getenv("MTA_OUT_HOST").split(":")[1])


def test_smtp_authentication_success(smtp_client):
    """Test successful SMTP authentication with correct credentials"""
    # Connection with authentication already established in fixture
    # Just verify that the client is connected and authenticated
    assert smtp_client.noop()[0] == 250


def test_smtp_authentication_invalid_password():
    """Test failed SMTP authentication with incorrect password"""
    client = smtplib.SMTP(MTA_OUT_HOSTNAME, MTA_OUT_PORT)
    client.ehlo()
    client.starttls()
    client.ehlo()

    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login(MTA_OUT_SMTP_USERNAME, "wrong_password")

    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass


def test_smtp_authentication_invalid_username():
    """Test failed SMTP authentication with incorrect username"""
    client = smtplib.SMTP(MTA_OUT_HOSTNAME, MTA_OUT_PORT)
    client.ehlo()
    client.starttls()
    client.ehlo()

    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login("wrong_username", MTA_OUT_SMTP_PASSWORD)

    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass


def test_smtp_authentication_empty_credentials():
    """Test failed SMTP authentication with empty credentials"""
    client = smtplib.SMTP(MTA_OUT_HOSTNAME, MTA_OUT_PORT)
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
    client = smtplib.SMTP(MTA_OUT_HOSTNAME, MTA_OUT_PORT)
    client.ehlo()
    client.starttls()
    client.ehlo()

    # Try to send email without logging in
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        client.sendmail(
            "sender@example.com",
            ["recipient@example.com"],
            "From: sender@example.com\nTo: recipient@example.com\nSubject: Test\n\nTest message",
        )

    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass
