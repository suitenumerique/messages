"""End to end: one pymta in front of two Messages instances.

Each instance is a backend on a database of its own, with an MDA secret of its
own. pymta routes by the tenants file ``make test-mta-in-py-e2e-tenants``
writes:

- a: the first backend, ``tenant-a.example``
- b: the second backend, ``tenant-b.example``
- c: the second backend, signed with a's secret, ``tenant-c.example``
- d: a backend that does not answer, ``tenant-d.example``

It needs that stack up and is skipped otherwise. The test reads and writes
both databases, so it must never run against ones that hold real data: it only
does when ``MTA_E2E_TENANTS_HOST`` is set, which only that target does.
"""

import contextlib
import email.utils
import os
import smtplib
import time
import uuid

from django.db import connections

import pytest

from core import factories, models

MTA_HOST = os.environ.get("MTA_E2E_TENANTS_HOST", "")
TENANT_B_DB = os.environ.get("MTA_E2E_TENANT_B_DB", "")

pytestmark = pytest.mark.skipif(
    not MTA_HOST,
    reason="needs pymta and two backends running: make test-mta-in-py-e2e-tenants",
)

HELO = "mx.sender.example"
SENDER = "alice@sender.example"


@contextlib.contextmanager
def _instance(db_name):
    """Point the ORM, signals included, at one instance's database."""
    connection = connections["default"]
    original = connection.settings_dict["NAME"]
    connection.close()
    connection.settings_dict["NAME"] = db_name or original
    try:
        yield
    finally:
        connection.close()
        connection.settings_dict["NAME"] = original


def _mailbox(domain):
    mailbox = factories.MailboxFactory(
        local_part=f"e2e-{uuid.uuid4().hex[:8]}", domain__name=domain
    )
    return f"{mailbox.local_part}@{domain}"


@pytest.fixture(scope="module", name="recipients")
def fixture_recipients(django_db_blocker):
    """One mailbox on a, and two on b: one for tenant b, one for tenant c."""
    with django_db_blocker.unblock():
        with _instance(None):
            a = _mailbox("tenant-a.example")
        with _instance(TENANT_B_DB):
            b = _mailbox("tenant-b.example")
            c = _mailbox("tenant-c.example")
    return {"a": a, "b": b, "c": c}


def _send(recipients):
    """Send one message to ``recipients`` over SMTP.

    Returns its Message-ID and the recipients refused, with their reply.
    """
    message_id = f"<{uuid.uuid4().hex}@sender.example>"
    message = (
        f"From: Alice <{SENDER}>\r\nTo: {', '.join(recipients)}\r\n"
        f"Subject: two tenants\r\nMessage-ID: {message_id}\r\n"
        f"Date: {email.utils.formatdate()}\r\n\r\nbody\r\n"
    ).encode()
    with smtplib.SMTP(MTA_HOST, 25, local_hostname=HELO, timeout=30) as client:
        refused = client.sendmail(SENDER, recipients, message)
    return message_id, refused


def _rcpt(address):
    """The reply to RCPT TO for ``address``, in a transaction of its own."""
    with smtplib.SMTP(MTA_HOST, 25, local_hostname=HELO, timeout=30) as client:
        client.ehlo()
        client.mail(SENDER)
        return client.rcpt(address)


def _stored_for(message_id):
    """The recipients this instance stored ``message_id`` for."""
    deadline = time.monotonic() + 20
    while True:
        stored = sorted(
            inbound.envelope["rcpt_to"]
            for inbound in models.InboundMessage.objects.all()
            if message_id.encode() in inbound.blob.get_content()
        )
        if stored or time.monotonic() > deadline:
            return stored
        time.sleep(0.3)


def test_each_instance_stores_the_message_for_its_own_recipient_only(
    recipients, django_db_blocker
):
    """One message, one recipient on each tenant: each instance gets its copy."""
    message_id, refused = _send([recipients["a"], recipients["b"]])
    assert not refused, refused
    with django_db_blocker.unblock():
        with _instance(None):
            assert _stored_for(message_id) == [recipients["a"]]
        with _instance(TENANT_B_DB):
            assert _stored_for(message_id) == [recipients["b"]]


def test_a_domain_no_tenant_lists_is_refused():
    """Answered like an unknown mailbox."""
    code, text = _rcpt("nobody@unknown.example")
    assert (code, text[:5]) == (550, b"5.1.1"), text


def test_a_tenant_signing_with_another_secret_is_deferred(recipients):
    """b's backend refuses a's secret: the sender is told to retry, not refused."""
    code, text = _rcpt(recipients["c"])
    assert code == 451, text


def test_a_tenant_down_does_not_hold_back_the_others(recipients, django_db_blocker):
    """d's backend does not answer: its recipient is deferred, a's gets the message."""
    message_id, refused = _send([recipients["a"], "anyone@tenant-d.example"])
    assert [(address, code) for address, (code, _) in refused.items()] == [
        ("anyone@tenant-d.example", 451)
    ]
    with django_db_blocker.unblock():
        with _instance(None):
            assert _stored_for(message_id) == [recipients["a"]]
