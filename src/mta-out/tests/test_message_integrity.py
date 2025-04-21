import pytest
import time
import logging
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as default_policy

logger = logging.getLogger(__name__)

# Define a sample raw MIME message
# Using EmailMessage for easier construction and header handling
original_msg = EmailMessage()
original_msg["Subject"] = "Test Message Integrity - проверка"  # Include non-ASCII
original_msg["From"] = "integrity-sender@example.com"
original_msg["To"] = "recipient@mock-server.com"
original_msg["X-Custom-Header"] = "KeepThisValue"
original_msg.set_content("""This is the plain text body.
With multiple lines.""")
original_msg.add_alternative(
    "<html><body><h1>HTML Body</h1><p>Ceci est le corps HTML.</p></body></html>",
    subtype="html",
)
# Get the raw bytes as they would be sent
# Use CRLF line endings as per SMTP standard
original_bytes = original_msg.as_bytes(policy=default_policy.clone(linesep="\r\n"))

# Headers that Postfix might add and we should ignore during comparison
HEADERS_TO_IGNORE = {"x-peer", "x-mailfrom", "x-rcptto"}


def test_mime_message_unmodified(smtp_client, mock_smtp_server):
    """
    Test that Postfix relays a raw MIME message without modifying headers or body,
    except for adding standard trace headers like 'Received'.
    """
    sender = original_msg["From"]
    recipient = original_msg["To"]

    # Clear any previous messages from the mock server
    mock_smtp_server.clear_messages()
    logger.info("Cleared messages from mock SMTP server.")

    # Send the raw message bytes using sendmail
    try:
        logger.info(f"Sending raw message from {sender} to {recipient}...")
        # sendmail expects bytes for the message
        smtp_client.sendmail(sender, recipient, original_bytes)
        logger.info("Raw message sent successfully via smtp_client.")
    except Exception as e:
        logger.error(f"Failed to send raw message: {e}")
        pytest.fail(f"SMTP sendmail failed: {e}")

    # Wait for the message to arrive at the mock server
    received_message_data = None
    max_wait_time = 15  # seconds
    poll_interval = 0.5  # seconds
    waited_time = 0

    logger.info("Waiting for message arrival at mock SMTP server...")
    while waited_time < max_wait_time:
        messages = mock_smtp_server.get_messages()
        if len(messages) > 0:
            if len(messages) > 1:
                pytest.fail(f"Expected 1 message, but received {len(messages)}")
            received_message_data = messages[0]
            logger.info(
                f"Message received by mock server from {received_message_data.get('from')} to {received_message_data.get('to')}"
            )
            break
        time.sleep(poll_interval)
        waited_time += poll_interval
        if waited_time >= max_wait_time:
            logger.warning("Timeout waiting for message at mock server.")
            pytest.fail("Timeout waiting for message at mock SMTP server")

    assert received_message_data is not None, "No message received by mock server"

    # Parse the received raw message bytes
    received_bytes = received_message_data["raw_message"]
    # Use BytesParser with the same policy used for sending
    received_msg = BytesParser(policy=default_policy).parsebytes(received_bytes)

    # Compare headers (ignoring specified headers and case)
    original_headers = {k.lower(): v for k, v in original_msg.items()}
    received_headers = {k.lower(): v for k, v in received_msg.items()}

    # Remove ignored headers from the received set for comparison
    for header in HEADERS_TO_IGNORE:
        received_headers.pop(header, None)

    # Check if all original headers are present and identical in the received message
    missing_headers = set(original_headers.keys()) - set(received_headers.keys())
    assert not missing_headers, (
        f"Missing headers in received message: {missing_headers}"
    )

    mismatched_headers = {}
    extra_headers = {}

    for k, received_v in received_headers.items():
        if k in original_headers:
            original_v = original_headers[k]
            # Simple string comparison should be sufficient for most headers
            if original_v != received_v:
                mismatched_headers[k] = {"expected": original_v, "got": received_v}
        else:
            # If it's not an original header and not in IGNORE list, it's extra
            extra_headers[k] = received_v

    assert not mismatched_headers, f"Header values mismatch: {mismatched_headers}"
    assert not extra_headers, f"Unexpected extra headers found: {extra_headers}"

    # Direct byte comparison of the body
    assert original_bytes == received_bytes, "Raw message body differs"

    logger.info(
        "Message integrity test passed: Headers (excluding ignored) and body match."
    )
