"""End to end: a real SMTP session into pymta, delivered to this backend.

The only test that runs the two services together. Everything else on either
side talks to a stand-in for the other: pymta's suite to a mock MDA, this
suite to a hand-built request.

It needs the stack up and is skipped otherwise. Run it with
``make test-mta-in-py-e2e``, which starts pymta and a backend against a
throwaway database, and points this test at them. The test reads that same
database, so it must never run against one that holds real data: it only
does when ``MTA_E2E_HOST`` is set, which only that target does.
"""

import email.utils
import os
import smtplib
import ssl
import time
import uuid
import warnings

import pytest

from core import factories, models

MTA_HOST = os.environ.get("MTA_E2E_HOST", "")

pytestmark = pytest.mark.skipif(
    not MTA_HOST, reason="needs pymta and the backend running: make test-mta-in-py-e2e"
)

HELO = "mx.sender.example"
SENDER = "alice@sender.example"


@pytest.fixture(scope="module", name="recipient")
def fixture_recipient(django_db_blocker):
    """A mailbox in the database the running backend uses."""
    with django_db_blocker.unblock():
        mailbox = factories.MailboxFactory(
            local_part=f"e2e-{uuid.uuid4().hex[:8]}", domain__name="e2e-mta.example"
        )
        return f"{mailbox.local_part}@{mailbox.domain.name}"


def _tls_context(version=None):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if version is not None:
        # An old client: the handshake signatures of TLS 1.0 need level 0.
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = version
            context.maximum_version = version
    return context


def _send(recipient, *, tls=None, sender=SENDER):
    """Send one message over SMTP. Returns (Message-ID, negotiated TLS or None)."""
    message_id = f"<{uuid.uuid4().hex}@sender.example>"
    message = (
        f"From: Alice <{SENDER}>\r\nTo: <{recipient}>\r\nSubject: end to end\r\n"
        f"Message-ID: {message_id}\r\nDate: {email.utils.formatdate()}\r\n\r\nbody\r\n"
    ).encode()
    negotiated = None
    with smtplib.SMTP(MTA_HOST, 25, local_hostname=HELO, timeout=30) as client:
        client.ehlo()
        if tls is not None:
            client.starttls(context=tls)
            client.ehlo()
            negotiated = client.sock.cipher()
        client.sendmail(sender, [recipient], message)
    return message_id, negotiated


def _stored(message_id, django_db_blocker):
    """The (raw bytes, envelope) the backend stored for ``message_id``."""
    deadline = time.monotonic() + 20
    with django_db_blocker.unblock():
        while time.monotonic() < deadline:
            for inbound in models.InboundMessage.objects.order_by("-created_at")[:20]:
                raw = inbound.blob.get_content()
                if message_id.encode() in raw:
                    return raw, inbound.envelope
            time.sleep(0.3)
    raise AssertionError(f"{message_id} never reached the backend")


def _prepended(raw):
    """The header lines the backend put in front of what the sender sent."""
    head = raw.split(b"From: Alice", 1)[0].decode()
    return email.message_from_string(head + "\r\n")


def test_a_message_sent_in_clear_is_stored_with_our_trace(recipient, django_db_blocker):
    """Return-Path and one Received of ours, saying ESMTP and no TLS."""
    message_id, _ = _send(recipient)
    raw, envelope = _stored(message_id, django_db_blocker)
    ours = _prepended(raw)

    assert ours["Return-Path"] == f"<{SENDER}>"
    # One Received for our hop, written by us, above everything the sender sent.
    assert len(ours.get_all("Received", [])) == 1
    received = " ".join(ours["Received"].split())
    assert received.startswith(f"from {HELO} (["), received
    assert " by " in received
    # RFC 3848: ESMTP without TLS, ESMTPS with.
    assert " with ESMTP " in received, received
    assert " tls " not in received
    assert email.utils.parsedate_to_datetime(received.rsplit(";", 1)[1].strip())
    # The client address is the one the envelope keeps while the message is queued.
    assert f"[{envelope['ip']}]" in received
    assert envelope.get("tls_version") in (None, "")


def test_a_message_sent_over_tls_says_so(recipient, django_db_blocker):
    """ESMTPS with the cipher and version the client negotiated."""
    message_id, (cipher, version, _) = _send(recipient, tls=_tls_context())
    raw, envelope = _stored(message_id, django_db_blocker)
    received = " ".join(_prepended(raw)["Received"].split())

    assert " with ESMTPS " in received, received
    # RFC 8314 section 4.3: the "tls" clause names the cipher suite.
    assert f" tls {cipher} " in received, received
    assert f"({version})" in received, received
    assert (envelope["tls_version"], envelope["tls_cipher"]) == (version, cipher)


def test_a_message_sent_over_an_old_tls_version_is_marked_as_such(
    recipient, django_db_blocker
):
    """Encrypted, but with a protocol version no longer considered safe."""
    try:
        message_id, (cipher, version, _) = _send(
            recipient, tls=_tls_context(ssl.TLSVersion.TLSv1)
        )
    except (ssl.SSLError, smtplib.SMTPException) as error:
        pytest.skip(f"no TLS 1.0 handshake possible in this stack: {error}")
    # OpenSSL spells it either way depending on the build.
    old = ("TLSv1", "TLSv1.0")
    assert version in old
    raw, envelope = _stored(message_id, django_db_blocker)
    received = " ".join(_prepended(raw)["Received"].split())

    assert " with ESMTPS " in received, received
    assert f" tls {cipher} " in received, received
    assert envelope["tls_version"] in old
    assert f"({envelope['tls_version']})" in received, received


def test_the_sender_cannot_supply_our_trace(recipient, django_db_blocker):
    """Headers the sender places on top stay below ours.

    Spam rules and Authentication-Results trust what is above the first
    Received header. Without one of ours, that was whatever the sender put
    there.
    """
    message_id = f"<{uuid.uuid4().hex}@sender.example>"
    message = (
        "X-Spam: Ham\r\nAuthentication-Results: ours.example; dkim=pass\r\n"
        f"Received: from forged (forged [192.0.2.66]) by forged; {email.utils.formatdate()}\r\n"
        f"From: Alice <{SENDER}>\r\nTo: <{recipient}>\r\nSubject: forged trace\r\n"
        f"Message-ID: {message_id}\r\n\r\nbody\r\n"
    ).encode()
    with smtplib.SMTP(MTA_HOST, 25, local_hostname=HELO, timeout=30) as client:
        client.sendmail(SENDER, [recipient], message)
    raw, _ = _stored(message_id, django_db_blocker)

    names = [line.split(b":", 1)[0] for line in raw.split(b"\r\n")[:5]]
    assert names[0] == b"Return-Path"
    assert names[1] == b"Received"
    ours = raw.split(b"X-Spam: Ham", 1)[0]
    assert b"forged" not in ours
    assert ours.count(b"Received:") == 1


@pytest.mark.parametrize(
    ("sender", "stored"),
    [
        ('"al ice"@sender.example', '"al ice"@sender.example'),
        ("bounce-a%40b.example@esp.example", "bounce-a%40b.example@esp.example"),
    ],
)
def test_unusual_but_valid_senders_are_stored_as_sent(
    recipient, django_db_blocker, sender, stored
):
    """Quoted local-part and VERP percent: down to the backend."""
    message_id, _ = _send(recipient, sender=sender)
    raw, envelope = _stored(message_id, django_db_blocker)
    assert _prepended(raw)["Return-Path"] == f"<{stored}>"
    assert envelope["mail_from"] == stored
