"""Tests for DKIM signing functionality."""

import re
from unittest.mock import patch

import pytest
from dkim import sign as dkim_sign
from dkim import verify as dkim_verify

from core.enums import DKIMAlgorithmChoices
from core.mda import signing
from core.mda.signing import generate_dkim_key, sign_message_dkim, verify_message_dkim
from core.models import DKIMKey, Mailbox, MailDomain
from core.services.dns.resolver import (
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ServfailError,
)
from core.tests.dns_answers import txt_answer


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
def test_verify_message_dkim_returns_signing_domain():
    """verify_message_dkim returns the validated signature's d= domain.

    Callers rely on the returned domain (not a bare bool) to enforce
    From/DKIM alignment, so the contract is exercised end to end here with a
    mocked DNS resolver serving the public key.
    """
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    Mailbox.objects.create(local_part="test", domain=mail_domain)
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    # A real 2048-bit key does not fit in one 255-octet character-string, so
    # this fixture is chunked exactly as an authoritative server must serve it
    # — the reassembly is the part that has to be right.
    answer = txt_answer(f"v=DKIM1; k=rsa; p={public_key_str}")
    with patch("core.mda.signing.dkim_dns_txt", return_value=answer):
        assert verify_message_dkim(full_message_signed) == "example.com"


@pytest.mark.django_db
def test_verify_message_dkim_ignores_unrelated_txt_at_selector():
    """An unrelated TXT beside the key must not block verification.

    Selector names commonly carry a domain-verification token as well. The
    ambiguity guard counts DKIM *key records*, not every TXT at the name, so
    a good key alongside a token still verifies.
    """
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    Mailbox.objects.create(local_part="test", domain=mail_domain)
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    answer = txt_answer(
        "google-site-verification=abc123",
        f"v=DKIM1; k=rsa; p={public_key_str}",
    )
    with patch("core.mda.signing.dkim_dns_txt", return_value=answer):
        assert verify_message_dkim(full_message_signed) == "example.com"


@pytest.mark.django_db
def test_verify_message_dkim_require_dnssec_refuses_insecure_answer():
    """With ``require_dnssec``, an unvalidated answer is not a usable key."""
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    Mailbox.objects.create(local_part="test", domain=mail_domain)
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    insecure = txt_answer(f"v=DKIM1; k=rsa; p={public_key_str}", secure=False)
    with patch("core.mda.signing.dkim_dns_txt", return_value=insecure):
        assert verify_message_dkim(full_message_signed, require_dnssec=True) is None
        # The same answer without the flag is the default, and the default is
        # what the inbound path uses: most sending zones are still unsigned.
        assert verify_message_dkim(full_message_signed) == "example.com"

    secure = txt_answer(f"v=DKIM1; k=rsa; p={public_key_str}", secure=True)
    with patch("core.mda.signing.dkim_dns_txt", return_value=secure):
        assert verify_message_dkim(full_message_signed, require_dnssec=True) == (
            "example.com"
        )


@pytest.mark.django_db
def test_verify_message_dkim_refuses_two_key_records():
    """Two actual key records at one selector stay ambiguous, so verification fails."""
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    _other_private, other_public = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    Mailbox.objects.create(local_part="test", domain=mail_domain)
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )

    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    answer = txt_answer(
        f"v=DKIM1; k=rsa; p={public_key_str}",
        f"v=DKIM1; k=rsa; p={other_public}",
    )
    with patch("core.mda.signing.dkim_dns_txt", return_value=answer):
        assert verify_message_dkim(full_message_signed) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error,expected_log",
    [
        (NXDOMAINError("s._domainkey.example.com", "TXT"), "No DKIM key record"),
        (NoAnswerError("s._domainkey.example.com", "TXT"), "No DKIM key record"),
        # SERVFAIL says the lookup did not complete, not that the record is
        # absent — the line check_single_record draws. Grouping it with
        # NXDOMAIN logged "no such record" about a question never answered.
        (ServfailError("s._domainkey.example.com", "TXT"), "did not complete"),
        (ResolutionTimeoutError("s._domainkey.example.com", "TXT"), "did not complete"),
    ],
)
def test_verify_message_dkim_reports_settled_and_transient_apart(error, expected_log):
    """Both fail the verification, but they are not the same failure."""
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )
    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    full_message_signed = signature_header_bytes + b"\r\n" + raw_message

    # Asserted on the logger rather than caplog: the "core" loggers do not
    # propagate to root, so caplog never sees them.
    with (
        patch("core.mda.signing.dkim_dns_txt", side_effect=error),
        patch.object(signing.logger, "warning") as mock_warning,
    ):
        assert verify_message_dkim(full_message_signed) is None

    logged = " ".join(str(call.args[0]) for call in mock_warning.call_args_list)
    assert expected_log in logged


@pytest.mark.django_db
def test_verify_message_dkim_invalid_returns_none():
    """A message whose signature does not validate yields None (falsy)."""
    # Unsigned message -> no DKIM-Signature header -> cannot verify.
    raw_message = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody\r\n"
    assert verify_message_dkim(raw_message) is None


@pytest.mark.django_db
def test_verify_message_dkim_body_hash_mismatch_is_not_reported_to_sentry():
    """A rewritten body is routine inbound, not something to page anyone about.

    ``DKIM.verify`` does not catch its own exceptions, so dkimpy's
    ValidationError("body hash mismatch") reaches our handler. Every mailing
    list that appends a footer produces one, on every message it relays.
    """
    private_key_pem_str, public_key_str = generate_dkim_key(key_size=1024)
    mail_domain = MailDomain.objects.create(name="example.com")
    DKIMKey.objects.create(
        selector="testselector",
        private_key=private_key_pem_str,
        public_key=public_key_str,
        key_size=1024,
        is_active=True,
        domain=mail_domain,
    )
    raw_message = (
        b"From: test@example.com\r\nTo: recipient@other.com\r\n"
        b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
    )
    signature_header_bytes = sign_message_dkim(raw_message, mail_domain)
    # A list footer appended after signing: the body hash no longer matches.
    tampered = (
        signature_header_bytes + b"\r\n" + raw_message + b"-- \r\nSent via list\r\n"
    )

    answer = txt_answer(f"v=DKIM1; k=rsa; p={public_key_str}")
    with (
        patch("core.mda.signing.dkim_dns_txt", return_value=answer),
        patch("core.mda.signing.capture_exception") as mock_capture,
    ):
        assert verify_message_dkim(tampered) is None

    mock_capture.assert_not_called()


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


@pytest.mark.django_db
def test_sign_message_dkim_failure_raises():
    """A domain with an active key that cannot sign must not send unsigned."""
    mail_domain = MailDomain.objects.create(name="example.com")
    assert mail_domain.get_active_dkim_key() is not None
    with (
        patch("core.mda.signing.dkim_sign", side_effect=ValueError("bad key")),
        pytest.raises(signing.DKIMSigningError),
    ):
        sign_message_dkim(b"From: test@example.com\r\n\r\nBody", mail_domain)


# --- verify_dkim_signatures ---------------------------------------------------

_MESSAGE = (
    b"From: test@example.com\r\nTo: recipient@other.com\r\n"
    b"Subject: Test DKIM\r\n\r\nHello World!\r\n"
)


@pytest.fixture(scope="module")
def _keys():
    """Two 1024-bit key pairs, generated once (RSA generation is slow)."""
    return [generate_dkim_key(key_size=1024) for _ in range(2)]


def _sign(message, domain, private_key, selector="s1"):
    header = dkim_sign(
        message=message,
        selector=selector.encode(),
        domain=domain.encode(),
        privkey=private_key.encode(),
        include_headers=[b"From", b"To", b"Subject"],
    )
    return header + message


def _key_dns(keys_by_name):
    """``dkim_dns_txt`` stand-in: {fqdn: public key | exception}."""

    def _lookup(fqdn):
        value = keys_by_name.get(fqdn, NXDOMAINError(fqdn, "TXT"))
        if isinstance(value, Exception):
            raise value
        return txt_answer(f"v=DKIM1; k=rsa; p={value}")

    return _lookup


def test_verify_dkim_signatures_unsigned():
    assert signing.verify_dkim_signatures(_MESSAGE) == []


def test_verify_dkim_signatures_reports_every_signature(_keys):
    """An ESP signature on top must not hide the author domain's own."""
    (author_private, author_public), (esp_private, esp_public) = _keys
    message = _sign(_MESSAGE, "example.com", author_private)
    message = _sign(message, "esp.example.net", esp_private)
    dns = _key_dns(
        {
            "s1._domainkey.example.com": author_public,
            "s1._domainkey.esp.example.net": esp_public,
        }
    )
    with patch("core.mda.signing.dkim_dns_txt", side_effect=dns):
        results = signing.verify_dkim_signatures(message)
    assert results == [
        signing.DKIMSignatureResult("esp.example.net", "pass"),
        signing.DKIMSignatureResult("example.com", "pass"),
    ]


def test_verify_dkim_signatures_wrong_key_is_fail(_keys):
    (private, _), (_, other_public) = _keys
    message = _sign(_MESSAGE, "example.com", private)
    dns = _key_dns({"s1._domainkey.example.com": other_public})
    with patch("core.mda.signing.dkim_dns_txt", side_effect=dns):
        results = signing.verify_dkim_signatures(message)
    assert results == [signing.DKIMSignatureResult("example.com", "fail")]


def test_verify_dkim_signatures_no_key_published_is_fail(_keys):
    (private, _), _ = _keys
    message = _sign(_MESSAGE, "example.com", private)
    with patch("core.mda.signing.dkim_dns_txt", side_effect=_key_dns({})):
        results = signing.verify_dkim_signatures(message)
    assert results == [signing.DKIMSignatureResult("example.com", "fail")]


@pytest.mark.parametrize(
    "error",
    [
        ServfailError("s1._domainkey.example.com", "TXT"),
        ResolutionTimeoutError("s1._domainkey.example.com", "TXT"),
    ],
)
def test_verify_dkim_signatures_incomplete_lookup_is_temperror(_keys, error):
    """A DNS outage must not read as a broken signature."""
    (private, _), _ = _keys
    message = _sign(_MESSAGE, "example.com", private)
    dns = _key_dns({"s1._domainkey.example.com": error})
    with patch("core.mda.signing.dkim_dns_txt", side_effect=dns):
        results = signing.verify_dkim_signatures(message)
    assert results == [signing.DKIMSignatureResult("example.com", "temperror")]


def test_verify_dkim_signatures_tampered_body_is_fail(_keys):
    (private, public), _ = _keys
    message = _sign(_MESSAGE, "example.com", private).replace(b"Hello", b"Hullo")
    dns = _key_dns({"s1._domainkey.example.com": public})
    with patch("core.mda.signing.dkim_dns_txt", side_effect=dns):
        results = signing.verify_dkim_signatures(message)
    assert results == [signing.DKIMSignatureResult("example.com", "fail")]


def test_verify_dkim_signatures_malformed_header_is_permerror():
    message = b"DKIM-Signature: v=1; d=example.com; garbage\r\n" + _MESSAGE
    with patch("core.mda.signing.dkim_dns_txt") as dns:
        results = signing.verify_dkim_signatures(message)
    # The tag list does not parse, so not even d= can be trusted.
    assert results == [signing.DKIMSignatureResult(None, "permerror")]
    dns.assert_not_called()


def test_verify_dkim_signatures_requires_from_to_be_signed(_keys):
    """A signature not covering From can be replayed under any From."""
    (private, _), _ = _keys
    signed = _sign(_MESSAGE, "example.com", private)
    unsigned_from = re.sub(rb"h=[^;]*;", b"h=to : subject;", signed, count=1)
    with patch("core.mda.signing.dkim_dns_txt") as dns:
        results = signing.verify_dkim_signatures(unsigned_from)
    assert results == [signing.DKIMSignatureResult("example.com", "permerror")]
    dns.assert_not_called()


def test_verify_dkim_signatures_refuses_body_length_limit(_keys):
    """``l=`` signs a body prefix: anything appended below would verify."""
    (private, public), _ = _keys
    header = dkim_sign(
        message=_MESSAGE,
        selector=b"s1",
        domain=b"example.com",
        privkey=private.encode(),
        include_headers=[b"From", b"To", b"Subject"],
        length=True,
    )
    assert b"l=" in header
    dns = _key_dns({"s1._domainkey.example.com": public})
    with patch("core.mda.signing.dkim_dns_txt", side_effect=dns):
        results = signing.verify_dkim_signatures(header + _MESSAGE + b"appended\r\n")
    assert results == [signing.DKIMSignatureResult("example.com", "permerror")]


def test_verify_dkim_signatures_prefers_the_from_domain(_keys):
    """Relay signatures on top must not push the author's out of the cap."""
    (private, public), _ = _keys
    message = _sign(_MESSAGE, "example.com", private)
    for index in range(signing.MAX_DKIM_SIGNATURES + 2):
        message = _sign(message, f"relay{index}.example.net", private)
    with patch(
        "core.mda.signing.dkim_dns_txt",
        side_effect=lambda fqdn: txt_answer(f"v=DKIM1; k=rsa; p={public}"),
    ):
        results = signing.verify_dkim_signatures(message, prefer_domain="example.com")
    assert results[0] == signing.DKIMSignatureResult("example.com", "pass")
    assert len(results) == signing.MAX_DKIM_SIGNATURES


def test_oversized_key_is_refused():
    """dkimpy's RSA is pure Python: a sender-supplied huge key costs CPU."""
    answer = txt_answer("v=DKIM1; k=rsa; p=" + "A" * (signing.MAX_DKIM_KEY_B64 + 1))
    with patch("core.mda.signing.dkim_dns_txt", return_value=answer):
        assert signing.dkim_key_record("s1._domainkey.example.com") is None


def test_verify_dkim_signatures_ed25519():
    """RFC 8463 signatures verify (dkimpy needs PyNaCl for them)."""
    # pylint: disable=import-outside-toplevel
    import base64

    import nacl.signing

    key = nacl.signing.SigningKey.generate()
    private = base64.b64encode(bytes(key))
    public = base64.b64encode(bytes(key.verify_key)).decode()
    header = dkim_sign(
        message=_MESSAGE,
        selector=b"ed",
        domain=b"example.com",
        privkey=private,
        signature_algorithm=b"ed25519-sha256",
        include_headers=[b"From", b"To", b"Subject"],
    )
    answer = txt_answer(f"v=DKIM1; k=ed25519; p={public}")
    with patch("core.mda.signing.dkim_dns_txt", return_value=answer):
        results = signing.verify_dkim_signatures(header + _MESSAGE)
    assert results == [signing.DKIMSignatureResult("example.com", "pass")]


def test_verify_dkim_signatures_caps_the_number_verified(_keys):
    """Each signature costs a key lookup whose name the sender picks."""
    (private, public), _ = _keys
    message = _MESSAGE
    for index in range(signing.MAX_DKIM_SIGNATURES + 3):
        message = _sign(message, f"d{index}.example.com", private)
    with patch(
        "core.mda.signing.dkim_dns_txt",
        side_effect=lambda fqdn: txt_answer(f"v=DKIM1; k=rsa; p={public}"),
    ) as dns:
        results = signing.verify_dkim_signatures(message)
    assert len(results) == signing.MAX_DKIM_SIGNATURES
    assert dns.call_count == signing.MAX_DKIM_SIGNATURES
