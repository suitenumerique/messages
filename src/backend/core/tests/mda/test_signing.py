"""Tests for DKIM signing functionality."""

import base64

from django.test import override_settings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import verify as dkim_verify

# Assuming sign_message_dkim is the refactored function
from core.mda.signing import sign_message_dkim

# Generate a test key pair
private_key_for_tests = rsa.generate_private_key(public_exponent=65537, key_size=1024)
public_key_der = private_key_for_tests.public_key().public_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
private_key_pem = private_key_for_tests.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


@override_settings(
    MESSAGES_DKIM_DOMAINS=["example.com", "test.domain"],
    MESSAGES_DKIM_SELECTOR="testselector",
    # Provide the key directly for testing, avoiding file reads
    MESSAGES_DKIM_PRIVATE_KEY_B64=base64.b64encode(private_key_pem).decode("utf-8"),
    MESSAGES_DKIM_PRIVATE_KEY_FILE=None,  # Ensure file setting is ignored
)
def test_sign_message_dkim_success():
    """Test that sign_message_dkim generates a valid signature."""
    sender_email = "test@example.com"
    raw_message = b"From: test@example.com\r\nTo: recipient@other.com\r\nSubject: Test DKIM\r\n\r\nHello World!\r\n"

    signature_header_bytes = sign_message_dkim(raw_message, sender_email)

    assert signature_header_bytes is not None
    assert signature_header_bytes.startswith(b"DKIM-Signature:")
    assert b"d=example.com" in signature_header_bytes
    assert b"s=testselector" in signature_header_bytes

    # Verify the signature using the public key
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    def get_dns_txt(fqdn, **kwargs):
        # Mock DNS lookup for the public key
        if fqdn == b"testselector._domainkey.example.com.":
            # Format according to RFC 6376 TXT record format
            return b"v=DKIM1; k=rsa; p=" + base64.b64encode(public_key_der)
        return None

    assert dkim_verify(full_message_signed, dnsfunc=get_dns_txt)


@override_settings(
    MESSAGES_DKIM_DOMAINS=["example.com"],
    MESSAGES_DKIM_SELECTOR="testselector",
    MESSAGES_DKIM_PRIVATE_KEY_B64=base64.b64encode(private_key_pem).decode("utf-8"),
    MESSAGES_DKIM_PRIVATE_KEY_FILE=None,
)
def test_sign_message_dkim_domain_not_allowed():
    """Test that signing is skipped for domains not in MESSAGES_DKIM_DOMAINS."""
    sender_email = "test@otherdomain.com"  # Not in settings
    raw_message = b"From: test@otherdomain.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, sender_email)
    assert signature_header_bytes is None


@override_settings(
    MESSAGES_DKIM_DOMAINS=["example.com"],
    MESSAGES_DKIM_SELECTOR="testselector",
    MESSAGES_DKIM_PRIVATE_KEY_B64=None,  # No key provided
    MESSAGES_DKIM_PRIVATE_KEY_FILE=None,
)
def test_sign_message_dkim_no_key():
    """Test that signing is skipped if no private key is configured."""
    sender_email = "test@example.com"
    raw_message = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, sender_email)
    assert signature_header_bytes is None
