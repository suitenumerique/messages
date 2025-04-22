import pytest
import smtplib
import time
import logging
import os
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Message
from aiosmtpd.smtp import AuthResult, LoginPassword
from email.parser import BytesParser

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get environment variables
MTA_OUT_HOST = os.getenv("MTA_OUT_HOST")
MTA_OUT_SMTP_USERNAME = os.getenv("MTA_OUT_SMTP_USERNAME")
MTA_OUT_SMTP_PASSWORD = os.getenv("MTA_OUT_SMTP_PASSWORD")


class MessageStore:
    """Simple storage for received email messages"""

    def __init__(self):
        self.messages = []

    def add_message(self, message_data):
        """Add a message to the store"""
        self.messages.append(message_data)

    def clear(self):
        """Clear all stored messages"""
        self.messages = []


class MockAuthHandler(LoginPassword):
    """Handle SMTP authentication"""

    def __init__(self, valid_username, valid_password):
        self.valid_username = valid_username
        self.valid_password = valid_password

    def verify(self, username, password):
        """Verify authentication credentials"""
        if username == self.valid_username and password == self.valid_password:
            return AuthResult(success=True)
        return AuthResult(success=False)


class MockSMTPHandler(Message):
    """Handle SMTP messages and store them"""

    def __init__(self, message_store):
        super().__init__()
        self.message_store = message_store

    async def handle_DATA(self, server, session, envelope):
        message = self.prepare_message(session, envelope)
        self.handle_message(message, envelope.content)
        return "250 OK"

    def handle_message(self, message, raw_message):
        """Process received messages"""
        logger.info("Received message in mock SMTP server")

        # Parse the message
        parsed_message = BytesParser().parsebytes(message.as_bytes())

        # Store message details
        message_data = {
            "from": parsed_message.get("From"),
            "to": parsed_message.get("To"),
            "subject": parsed_message.get("Subject"),
            "raw_message": raw_message,
            "cc": parsed_message.get("Cc"),
            "bcc": parsed_message.get("Bcc"),
            "parsed_message": parsed_message,
        }

        self.message_store.add_message(message_data)
        logger.info(f"Stored message: {message_data['subject']}")


class MockSMTPServer:
    """Mock SMTP server for testing outgoing emails"""

    def __init__(self, host="0.0.0.0", port=2525):
        """Initialize the SMTP server"""
        self.host = host
        self.port = port
        self.message_store = MessageStore()

        # Create handler that stores messages
        handler = MockSMTPHandler(self.message_store)

        # Create controller with TLS support
        self.controller = Controller(
            handler,
            hostname=self.host,
            port=self.port,
        )

    def start(self):
        """Start the SMTP server"""
        logger.info(f"Starting mock SMTP server on {self.host}:{self.port}")
        self.controller.start()

    def stop(self):
        """Stop the SMTP server"""
        logger.info("Stopping mock SMTP server")
        self.controller.stop()

    def clear_messages(self):
        """Clear all stored messages"""
        self.message_store.clear()

    def get_messages(self):
        """Get all stored messages"""
        return self.message_store.messages

    def wait_for_messages(self, n=1, timeout=10):
        """Wait for a message to be stored"""
        start_time = time.time()
        while len(self.get_messages()) < n:
            if time.time() - start_time > timeout:
                raise TimeoutError("Timed out waiting for message")
            time.sleep(0.1)


@pytest.fixture(scope="session")
def mock_smtp_server():
    """Create a mock SMTP server for testing"""
    server = MockSMTPServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="function")
def smtp_client():
    """Create an SMTP client connected to the MTA-out service"""
    # Wait for Postfix to be ready
    max_retries = 50
    for attempt in range(max_retries):
        try:
            # First check if SMTP connection can be established
            client = smtplib.SMTP(MTA_OUT_HOST.split(":")[0], int(MTA_OUT_HOST.split(":")[1]))
            client.ehlo()
            client.starttls()
            client.ehlo()

            # Authenticate
            client.login(MTA_OUT_SMTP_USERNAME, MTA_OUT_SMTP_PASSWORD)

            logger.info("SMTP connection established and authenticated")
            break
        except (ConnectionRefusedError, smtplib.SMTPException) as e:
            if attempt == max_retries - 1:
                raise
            if attempt % 10 == 0:
                logger.warning(
                    f"SMTP connection attempt {attempt + 1} failed ({str(e)}), retrying..."
                )
            time.sleep(0.2)

    yield client

    try:
        client.quit()
    except smtplib.SMTPServerDisconnected:
        pass
