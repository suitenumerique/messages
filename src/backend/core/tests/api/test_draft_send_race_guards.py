"""Endpoint-level guards of the draft-update/send race.

The MDA-layer invariants are covered in
``core/tests/mda/test_outbound_recipient_race.py``; these tests pin the
HTTP contract on top of them:

* the draft PUT scopes its (locked) fetch to ``is_draft=True``, so an
  autosave landing after the send finalized the message gets a 404
  instead of rewriting the recipients of a message being delivered;
* the send endpoint scopes its (locked) fetch the same way, so a message
  already handed to the outbound worker cannot be sent twice;
* both fetches actually take the row lock (``FOR UPDATE``) that
  serializes them against each other.
"""

# pylint: disable=unused-argument

from unittest.mock import MagicMock, patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """A user holding the sender role on the mailbox."""
    return factories.UserFactory()


@pytest.fixture(name="mailbox")
def fixture_mailbox(user):
    """A mailbox the user can draft and send from."""
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.SENDER
    )
    return mailbox


@pytest.fixture(name="thread")
def fixture_thread(mailbox):
    """A thread the mailbox can edit."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return thread


@pytest.fixture(name="draft_message")
def fixture_draft_message(thread, mailbox):
    """A draft message in the editable thread."""
    return factories.MessageFactory(
        thread=thread,
        sender=factories.ContactFactory(mailbox=mailbox),
        is_draft=True,
        subject="Draft under race",
    )


@pytest.fixture(name="finalized_message")
def fixture_finalized_message(draft_message):
    """The same message once a send has finalized it."""
    draft_message.is_draft = False
    draft_message.is_sender = True
    draft_message.save()
    return draft_message


@pytest.fixture(name="client")
def fixture_client(user):
    """An authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def locked_message_fetches(queries):
    """SQL of the captured queries that lock a ``messages_message`` row."""
    return [
        q["sql"]
        for q in queries.captured_queries
        if "FOR UPDATE" in q["sql"] and '"messages_message"' in q["sql"]
    ]


def test_put_draft_on_finalized_message_returns_404(client, mailbox, finalized_message):
    """An autosave landing after the send finalized the message gets a 404."""
    to_contact = factories.ContactFactory(mailbox=mailbox, email="to@example.com")
    recipient = factories.MessageRecipientFactory(
        message=finalized_message,
        contact=to_contact,
        type=enums.MessageRecipientTypeChoices.TO,
    )

    url = reverse("draft-message-detail", kwargs={"message_id": finalized_message.id})
    response = client.put(
        url,
        {
            "senderId": str(mailbox.id),
            "subject": "Rewritten during delivery",
            "to": ["other@example.com"],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    # The envelope of the message being delivered was left untouched.
    assert list(finalized_message.recipients.all()) == [recipient]
    finalized_message.refresh_from_db()
    assert finalized_message.subject == "Draft under race"


def test_put_draft_takes_row_lock(client, mailbox, draft_message):
    """The draft PUT must fetch the message FOR UPDATE.

    The lock is what serializes an autosave against a concurrent send;
    removing ``select_for_update`` from the view would reopen the race
    without failing any 404 assertion, hence this SQL-level check.
    """
    url = reverse("draft-message-detail", kwargs={"message_id": draft_message.id})
    with CaptureQueriesContext(connection) as queries:
        response = client.put(
            url,
            {"senderId": str(mailbox.id), "subject": "Still a draft"},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    locking_fetches = locked_message_fetches(queries)
    assert locking_fetches, "draft PUT no longer locks the message row"
    # The draft-state predicate must live in the locked query itself: an
    # unlocked ``is_draft`` read followed by a separate locked fetch would
    # reopen the race between the state check and the lock.
    assert any('"is_draft"' in sql for sql in locking_fetches), (
        "the locked draft PUT fetch no longer checks the draft state"
    )


def test_send_finalized_message_returns_404(client, mailbox, finalized_message):
    """A message already handed to the worker cannot be sent again.

    The delivery mocks pin the actual invariant: a 404 returned *after*
    having enqueued the outbound task would still double-send.
    """
    with (
        patch("core.api.viewsets.send.prepare_outbound_message") as mock_prepare,
        patch("core.api.viewsets.send.send_message_task") as mock_task,
    ):
        response = client.post(
            reverse("send-message"),
            {
                "messageId": str(finalized_message.id),
                "senderId": str(mailbox.id),
                "textBody": "Hello",
                "htmlBody": "<p>Hello</p>",
            },
            format="json",
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_prepare.assert_not_called()
    mock_task.apply_async.assert_not_called()


def test_send_takes_row_lock(client, mailbox, draft_message):
    """The send endpoint must fetch the message FOR UPDATE.

    Same rationale as the draft PUT: the lock pair is the fix under
    test, and only the captured SQL proves it is still taken.
    """
    with (
        patch("core.api.viewsets.send.prepare_outbound_message") as mock_prepare,
        patch("core.api.viewsets.send.send_message_task") as mock_task,
    ):
        mock_prepare.return_value = True
        mock_task.apply_async.return_value = MagicMock(id="task-123")
        with CaptureQueriesContext(connection) as queries:
            response = client.post(
                reverse("send-message"),
                {
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "Hello",
                    "htmlBody": "<p>Hello</p>",
                },
                format="json",
            )

    assert response.status_code == status.HTTP_200_OK
    locking_fetches = locked_message_fetches(queries)
    assert locking_fetches, "send endpoint no longer locks the message row"
    # Same rationale as the draft PUT: the state check must be part of
    # the locked fetch, not a separate unlocked read.
    assert any('"is_draft"' in sql for sql in locking_fetches), (
        "the locked send fetch no longer checks the draft state"
    )
