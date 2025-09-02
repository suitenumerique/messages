import logging
import time
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


# SMTP via Proxy Tests with Connection Info Logging
def test_smtp_connection_direct(smtp_client_direct, mock_smtp_server):
    """Test direct SMTP connection without proxy and log connection info"""

    smtp_client_direct.ehlo()

    assert smtp_client_direct.noop()[0] == 250, "Direct SMTP connection should work"
    
    message = MIMEText("Test direct connection email")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@localhost"
    message["Subject"] = "Test Direct Connection"
    
    mock_smtp_server.clear_messages()
    response = smtp_client_direct.send_message(message)
    assert not response, "Sending should succeed"
    
    # Wait for message and get connection info
    time.sleep(1)
    messages = mock_smtp_server.get_messages()
    assert len(messages) == 1, "Message should be received"
    assert messages[0]["subject"] == "Test Direct Connection"
    
    # Log connection info for debugging
    connection_info = messages[0].get("connection_info", {})
    logger.info(f"Direct SMTP connection info: {connection_info}")

    assert connection_info["peer_host"] == "127.0.0.1"


def test_smtp_connection_via_proxy(smtp_client_via_proxy, mock_smtp_server):
    """Test SMTP connection through SOCKS proxy and log connection info"""

    smtp_client_via_proxy.ehlo()

    assert smtp_client_via_proxy.noop()[0] == 250, "Proxy SMTP connection should work"
    
    message = MIMEText("Test proxy connection email")
    message["From"] = "sender@example.com"
    message["To"] = "recipient@localhost"
    message["Subject"] = "Test Proxy Connection"
    
    mock_smtp_server.clear_messages()
    response = smtp_client_via_proxy.send_message(message)
    assert not response, "Sending should succeed"
    
    # Wait for message and get connection info
    time.sleep(1)
    messages = mock_smtp_server.get_messages()
    assert len(messages) == 1, "Message should be received"
    assert messages[0]["subject"] == "Test Proxy Connection"
    
    # Log connection info for debugging
    connection_info = messages[0].get("connection_info", {})
    logger.info(f"Proxy SMTP connection info: {connection_info}")

    assert connection_info["peer_host"] != "127.0.0.1", "Proxy SMTP connection should not be direct"

# TODO: stress test with https://pypi.org/project/pytest-run-parallel/ ?