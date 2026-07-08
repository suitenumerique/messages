"""Tests for the mailbox storage_stats action."""
# pylint: disable=redefined-outer-name

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db


def _url(mailbox):
    return reverse("mailboxes-storage-stats", kwargs={"pk": str(mailbox.id)})


def test_requires_authentication():
    """Anonymous users cannot read storage stats."""
    mailbox = factories.MailboxFactory()
    response = APIClient().get(_url(mailbox))
    assert response.status_code == 401


def test_requires_mailbox_access():
    """A user without access to the mailbox cannot read its stats.

    ``get_object`` is restricted to the current user's mailboxes, so a mailbox
    the user has no access to is a 404 (not found in the queryset).
    """
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))
    assert response.status_code == 404


def test_empty_mailbox_returns_zeroes():
    """A mailbox with no threads reports zero storage and an empty list."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "total_storage": 0,
        "trashed_storage": 0,
        "spam_storage": 0,
        "message_count": 0,
        "thread_count": 0,
        "largest_threads": [],
    }


def test_storage_formula_and_largest_threads(settings):
    """Total storage follows the metrics formula and threads are ranked by size."""
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    # Small thread: one MIME message.
    thread_small = factories.ThreadFactory(subject="small")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_small)
    msg_small = factories.MessageFactory(
        thread=thread_small, sender=contact, raw_mime=b"s" * 100
    )

    # Big thread: two MIME messages + one attachment. ``messaged_at`` is
    # normally maintained by the delivery flow, so set it explicitly here.
    thread_big = factories.ThreadFactory(
        subject="big", messaged_at=timezone.now()
    )
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_big)
    # Distinct content so the two blobs are not deduplicated by sha256 (the
    # endpoint counts each distinct blob once).
    msg_big1 = factories.MessageFactory(
        thread=thread_big, sender=contact, raw_mime=b"b" * 5000
    )
    msg_big2 = factories.MessageFactory(
        thread=thread_big, sender=contact, raw_mime=b"c" * 5000
    )
    att = factories.AttachmentFactory(
        mailbox=mailbox, blob_size=500, message=msg_big1
    )

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    data = response.json()

    expected_total = (
        3 * overhead
        + msg_small.blob.size_compressed
        + msg_big1.blob.size_compressed
        + msg_big2.blob.size_compressed
        + att.blob.size_compressed
    )
    assert data["total_storage"] == expected_total
    assert data["message_count"] == 3
    assert data["thread_count"] == 2

    # Ranked by (mime + draft) blob size descending: big thread first.
    subjects = [t["subject"] for t in data["largest_threads"]]
    assert subjects == ["big", "small"]

    big = data["largest_threads"][0]
    assert big["message_count"] == 2
    assert big["size"] == (
        msg_big1.blob.size_compressed + msg_big2.blob.size_compressed
    )
    assert big["messaged_at"] is not None


def test_trashed_and_spam_storage(settings):
    """``trashed_storage`` and ``spam_storage`` each sum only their own threads
    (message overhead + mime/draft blobs), independently of one another."""
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    # A live thread (neither trashed nor spam).
    live_thread = factories.ThreadFactory(subject="live")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=live_thread)
    factories.MessageFactory(
        thread=live_thread, sender=contact, raw_mime=b"live" * 100
    )

    # A trashed thread.
    trashed_thread = factories.ThreadFactory(subject="trashed", is_trashed=True)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=trashed_thread)
    trashed_msg = factories.MessageFactory(
        thread=trashed_thread,
        sender=contact,
        raw_mime=b"trash" * 100,
        is_trashed=True,
    )

    # A spam thread.
    spam_thread = factories.ThreadFactory(subject="spam", is_spam=True)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=spam_thread)
    spam_msg = factories.MessageFactory(
        thread=spam_thread, sender=contact, raw_mime=b"spam" * 100, is_spam=True
    )

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    data = response.json()

    assert data["trashed_storage"] == (
        1 * overhead + trashed_msg.blob.size_compressed
    )
    assert data["spam_storage"] == (
        1 * overhead + spam_msg.blob.size_compressed
    )
    # The live thread's storage is not counted in either bucket.
    assert data["trashed_storage"] < data["total_storage"]
    assert data["spam_storage"] < data["total_storage"]
