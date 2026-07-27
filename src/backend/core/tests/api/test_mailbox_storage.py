"""Tests for the mailbox storage endpoint (Storage settings tab)."""
# pylint: disable=redefined-outer-name

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db


def _url(mailbox):
    return reverse("mailboxes-storage", kwargs={"pk": str(mailbox.id)})


def test_requires_authentication():
    """Anonymous users cannot read storage stats."""
    mailbox = factories.MailboxFactory()
    response = APIClient().get(_url(mailbox))
    assert response.status_code == 401


def test_requires_mailbox_access():
    """A user without access to the mailbox gets a 404 (queryset-filtered)."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()

    client = APIClient()
    client.force_login(user)
    assert client.get(_url(mailbox)).status_code == 404


def test_non_admin_member_forbidden():
    """A member without admin rights cannot read the storage breakdown."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
    )

    client = APIClient()
    client.force_login(user)
    assert client.get(_url(mailbox)).status_code == 403


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
    assert response.json() == {
        "total_storage": 0,
        "trashed_storage": 0,
        "spam_storage": 0,
        "message_count": 0,
        "thread_count": 0,
        "largest_threads": [],
    }


def test_storage_formula_and_largest_threads(settings):
    """Total follows the metrics formula; threads are ranked by size."""
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    thread_small = factories.ThreadFactory(subject="small")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_small)
    msg_small = factories.MessageFactory(
        thread=thread_small, sender=contact, raw_mime=b"s" * 100
    )

    thread_big = factories.ThreadFactory(subject="big", messaged_at=timezone.now())
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_big)
    # Distinct content so the blobs are not deduplicated by sha256.
    msg_big1 = factories.MessageFactory(
        thread=thread_big, sender=contact, raw_mime=b"b" * 5000
    )
    msg_big2 = factories.MessageFactory(
        thread=thread_big, sender=contact, raw_mime=b"c" * 5000
    )
    att = factories.AttachmentFactory(mailbox=mailbox, blob_size=500, message=msg_big1)

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
    assert [t["subject"] for t in data["largest_threads"]] == ["big", "small"]
    big = data["largest_threads"][0]
    assert big["message_count"] == 2
    assert big["size"] == (
        msg_big1.blob.size_compressed + msg_big2.blob.size_compressed
    )
    assert big["messaged_at"] is not None


def test_trashed_and_spam_storage(settings):
    """trashed_storage and spam_storage each sum only their own messages."""
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    live_thread = factories.ThreadFactory(subject="live")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=live_thread)
    factories.MessageFactory(thread=live_thread, sender=contact, raw_mime=b"live" * 100)

    trashed_thread = factories.ThreadFactory(subject="trashed", is_trashed=True)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=trashed_thread)
    trashed_msg = factories.MessageFactory(
        thread=trashed_thread, sender=contact, raw_mime=b"trash" * 100, is_trashed=True
    )

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

    assert data["trashed_storage"] == 1 * overhead + trashed_msg.blob.size_compressed
    assert data["spam_storage"] == 1 * overhead + spam_msg.blob.size_compressed
    assert data["trashed_storage"] < data["total_storage"]
    assert data["spam_storage"] < data["total_storage"]


def test_trashed_storage_is_scoped_per_message_not_per_thread(settings):
    """Only the trashed messages of a partly-trashed thread are counted.

    Thread.is_trashed is true only when *every* message is trashed, so scoping
    by the thread flag would report 0 here; Thread.is_spam mirrors the first
    message alone, so it would report the whole thread as spam. Both are
    denormalizations for the folder filters, not storage accounting.
    """
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    # One thread, one trashed message and one live one: the thread's own
    # is_trashed stays False because not all of its messages are trashed.
    thread = factories.ThreadFactory(subject="mixed")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    trashed_msg = factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"trash" * 100, is_trashed=True
    )
    factories.MessageFactory(thread=thread, sender=contact, raw_mime=b"live" * 100)

    thread.refresh_from_db()
    thread.update_stats()
    thread.refresh_from_db()
    assert thread.is_trashed is False

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    data = response.json()

    assert data["trashed_storage"] == 1 * overhead + trashed_msg.blob.size_compressed
    assert data["spam_storage"] == 0
