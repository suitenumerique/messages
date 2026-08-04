"""Shared fixtures for MDA tests."""

import pytest

from core import enums, factories, models


@pytest.fixture(name="relay_settings")
def fixture_relay_settings(settings):
    """SMTP relay configuration used by outbound send tests."""
    settings.MTA_OUT_MODE = "relay"
    settings.MTA_OUT_RELAY_HOST = "smtp.test:1025"
    settings.MTA_OUT_RELAY_USERNAME = "smtp_user"
    settings.MTA_OUT_RELAY_PASSWORD = "smtp_pass"
    settings.OPENSEARCH_INDEX_THREADS = False


@pytest.fixture(name="sendable_message")
def fixture_sendable_message():
    """A finalized outbound message with one external TO recipient."""
    sender_contact = factories.ContactFactory(email="sender@sendtest.com")
    mailbox = sender_contact.mailbox
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    message = factories.MessageFactory(
        thread=thread,
        sender=sender_contact,
        is_draft=False,
        is_sender=True,
        subject="Race repro",
    )
    message.blob = factories.BlobFactory(
        mailbox=mailbox,
        content=(
            b"From: sender@sendtest.com\n"
            b"To: to@example.com\n"
            b"Subject: Race repro\n\nBody"
        ),
        content_type="message/rfc822",
    )
    message.save()
    to_contact = factories.ContactFactory(mailbox=mailbox, email="to@example.com")
    factories.MessageRecipientFactory(
        message=message,
        contact=to_contact,
        type=models.MessageRecipientTypeChoices.TO,
    )
    return message
