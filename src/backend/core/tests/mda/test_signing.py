"""Tests for DKIM signing functionality."""

import pytest
from dkim import verify as dkim_verify

from core.enums import DKIMAlgorithmChoices
from core.mda.signing import generate_dkim_key, sign_message_dkim
from core.models import DKIMKey, Mailbox, MailDomain


@pytest.mark.django_db
def test_generate_dkim_key():
    """Test the generate_dkim_key function."""
    # Test default parameters (RSA 2048)
    private_key_str, public_key_str = generate_dkim_key()

    assert private_key_str.startswith("-----BEGIN PRIVATE KEY-----")
    assert private_key_str.endswith("-----END PRIVATE KEY-----\n")
    assert public_key_str  # Should have public key

    # Test with custom key size
    private_key_str_4096, public_key_str_4096 = generate_dkim_key(key_size=4096)
    assert private_key_str_4096.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_key_str_4096  # Should have public key

    # Test unsupported algorithm
    with pytest.raises(ValueError):
        generate_dkim_key(algorithm=DKIMAlgorithmChoices.ED25519)


@pytest.mark.django_db
def test_sign_message_dkim_success():
    """Test that sign_message_dkim generates a valid signature."""
    # Generate a test private key using our function
    private_key_pem_str, public_key_str = generate_dkim_key(
        key_size=1024
    )  # Smaller key for faster tests

    # Create a mail domain
    mail_domain = MailDomain.objects.create(name="example.com")

    # Create a mailbox
    Mailbox.objects.create(
        local_part="test",
        domain=mail_domain,
    )

    # Create a DKIM key associated with the domain
    dkim_key = DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = b"From: test@example.com\r\nTo: recipient@other.com\r\nSubject: Test DKIM\r\n\r\nHello World!\r\n"

    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)

    assert signature_header_bytes is not None
    assert signature_header_bytes.startswith(b"DKIM-Signature:")
    assert b"d=example.com" in signature_header_bytes
    assert b"s=testselector" in signature_header_bytes

    # Verify the signature using the public key from the DKIMKey model
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    def get_dns_txt(fqdn, **kwargs):
        # Mock DNS lookup for the public key
        if fqdn == b"testselector._domainkey.example.com.":
            # Use the public key stored in the model
            return f"v=DKIM1; k=rsa; p={dkim_key.public_key}".encode()
        return None

    assert dkim_verify(full_message_signed, dnsfunc=get_dns_txt)


@pytest.mark.django_db
def test_sign_message_dkim_no_dkim_key():
    """Test that signing is skipped if domain has no DKIM key configured."""
    # Create a mail domain without DKIM key
    mail_domain = MailDomain.objects.create(name="example.com")
    DKIMKey.objects.first().delete()

    raw_message = b"From: test@otherdomain.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    assert signature_header_bytes is None


@pytest.mark.django_db
def test_sign_message_dkim_inactive_key():
    """Test that signing is skipped if DKIM key is inactive."""

    # Create mail domain and mailbox
    mail_domain = MailDomain.objects.create(name="example.com")
    dkim_key = mail_domain.get_active_dkim_key()
    assert dkim_key is not None
    assert dkim_key.is_active is True

    dkim_key.is_active = False
    dkim_key.save()

    raw_message = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    assert signature_header_bytes is None


@pytest.mark.django_db
def test_sign_message_dkim_picks_most_recent_active():
    """Test that signing picks the most recent active DKIM key."""
    # Create mail domain and mailbox
    mail_domain = MailDomain.objects.create(name="example.com")
    Mailbox.objects.create(
        local_part="test",
        domain=mail_domain,
    )

    # Create first (older) DKIM key
    old_private_key, old_public_key = generate_dkim_key(key_size=1024)
    DKIMKey.objects.create(
        selector="old",
        private_key=old_private_key,
        public_key=old_public_key,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    # Create second (newer) DKIM key
    new_private_key, new_public_key = generate_dkim_key(key_size=1024)
    DKIMKey.objects.create(
        selector="new",
        private_key=new_private_key,
        public_key=new_public_key,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)

    # Should use the newer key
    assert signature_header_bytes is not None
    assert b"s=new" in signature_header_bytes


@pytest.mark.django_db
def test_mailbox_generate_dkim_key():
    """Test the generate_dkim_key method with auto-generated private key."""
    # Create a mail domain
    mail_domain = MailDomain.objects.create(name="example.com")

    # Generate DKIM key using the domain method
    dkim_key = mail_domain.generate_dkim_key(selector="auto")

    # Verify the key was created correctly
    assert dkim_key.selector == "auto"
    assert dkim_key.algorithm == DKIMAlgorithmChoices.RSA  # default
    assert dkim_key.key_size == 2048  # default
    assert dkim_key.is_active is True
    assert dkim_key.private_key.startswith("-----BEGIN PRIVATE KEY-----")
    assert dkim_key.domain == mail_domain
    assert dkim_key.public_key  # Should have a public key

    # Test that signing now works
    raw_message = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)

    assert signature_header_bytes is not None
    assert b"s=auto" in signature_header_bytes

    # Make sure the DKIM key is encrypted in the DB.
    raw_key = list(
        DKIMKey.objects.raw(
            "SELECT private_key as pik,id from messages_dkimkey WHERE selector='auto'"
        )
    )[0].pik
    assert raw_key is not None
    assert raw_key != dkim_key.private_key
    assert "BEGIN PRIVATE KEY" not in raw_key


@pytest.mark.django_db
def test_mailbox_generate_dkim_key_custom_parameters():
    """Test the generate_dkim_key method with custom parameters."""
    # Create a mail domain
    mail_domain = MailDomain.objects.create(name="example.com")

    # Generate DKIM key with custom parameters
    dkim_key = mail_domain.generate_dkim_key(
        selector="custom",
        key_size=4096,
    )

    # Verify the key was created correctly
    assert dkim_key.selector == "custom"
    assert dkim_key.algorithm == DKIMAlgorithmChoices.RSA
    assert dkim_key.key_size == 4096
    assert dkim_key.is_active is True
    assert dkim_key.private_key.startswith("-----BEGIN PRIVATE KEY-----")
    assert dkim_key.domain == mail_domain
    assert dkim_key.public_key  # Should have a public key


@pytest.mark.django_db
def test_dkim_key_get_dns_record_value():
    """Test the get_dns_record_value method on DKIMKey."""
    # Create a mail domain and mailbox
    mail_domain = MailDomain.objects.create(name="example.com")

    # Generate DKIM key
    dkim_key = mail_domain.generate_dkim_key(selector="test")

    # Test DNS record generation
    dns_value = dkim_key.get_dns_record_value()
    assert dns_value.startswith("v=DKIM1; k=rsa; p=")
    assert dkim_key.public_key in dns_value


@pytest.mark.django_db
def test_mail_domain_get_active_dkim_key():
    """Test the get_active_dkim_key method on MailDomain."""
    # Create a mail domain
    mail_domain = MailDomain.objects.create(name="example.com")

    dkim_key = mail_domain.get_active_dkim_key()
    assert dkim_key is not None
    assert dkim_key.is_active is True

    dkim_key.is_active = False
    dkim_key.save()

    dkim_key = mail_domain.get_active_dkim_key()
    assert dkim_key is None

    dkim_key = mail_domain.generate_dkim_key(selector="test")
    assert dkim_key is not None

    # Should return the key
    active_key = mail_domain.get_active_dkim_key()
    assert active_key == dkim_key

    # Create an inactive key
    inactive_private_key, inactive_public_key = generate_dkim_key(key_size=1024)
    inactive_key = DKIMKey.objects.create(
        selector="inactive",
        private_key=inactive_private_key,
        public_key=inactive_public_key,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )
    inactive_key.is_active = False
    inactive_key.save()

    # Should still return the active key
    active_key = mail_domain.get_active_dkim_key()
    assert active_key == dkim_key
