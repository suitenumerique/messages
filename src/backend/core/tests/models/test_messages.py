import base64

from django.test import override_settings

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core import models

private_key_for_tests = rsa.generate_private_key(public_exponent=3, key_size=1024)


@override_settings(
    MESSAGES_DKIM_DOMAINS=["example.com"],
    MESSAGES_DKIM_SELECTOR="testselector",
    MESSAGES_DKIM_PRIVATE_KEY_B64=base64.b64encode(
        private_key_for_tests.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("utf-8"),
)
@pytest.mark.django_db
def test_generate_dkim_signature():
    # Create sender contact with email in allowed domain
    mailbox = models.Mailbox.objects.create(
        local_part="alice", domain=models.MailDomain.objects.create(name="example.com")
    )
    sender = models.Contact.objects.create(email="alice@example.com")
    thread = models.Thread.objects.create(subject="Test", snippet="", mailbox=mailbox)
    message = models.Message.objects.create(
        thread=thread,
        subject="Test DKIM",
        sender=sender,
        raw_mime=b"Subject: Test DKIM\r\n\r\nBody",
    )

    sig = message.generate_dkim_signature()
    assert sig is not None
    assert sig.startswith(b"DKIM-Signature: ")
    assert b"s=testselector" in sig
