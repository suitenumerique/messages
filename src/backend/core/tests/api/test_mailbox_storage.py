"""Tests for the mailbox storage endpoint (Storage settings tab)."""
# pylint: disable=redefined-outer-name

import os

from django.db import connection
from django.test.utils import CaptureQueriesContext
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

    # Ranked by size descending: big thread first. Same basis as the
    # subtotals — message overhead plus MIME and draft blobs.
    assert [t["subject"] for t in data["largest_threads"]] == ["big", "small"]
    big = data["largest_threads"][0]
    assert big["message_count"] == 2
    assert big["size"] == (
        2 * overhead + msg_big1.blob.size_compressed + msg_big2.blob.size_compressed
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
    by the thread flag would report 0 here.
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


def test_spam_storage_counts_whole_conversations(settings):
    """A spam conversation counts in full, unflagged messages included.

    "Empty spam" deletes whole conversations, so the subtotal must announce
    the space it really frees — here the user's own reply too.
    """
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    thread = factories.ThreadFactory(subject="spam")
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    spam_msg = factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"spam" * 100, is_spam=True
    )
    reply = factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"reply" * 100, is_sender=True
    )

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    assert response.json()["spam_storage"] == (
        2 * overhead + spam_msg.blob.size_compressed + reply.blob.size_compressed
    )


def test_largest_threads_report_this_mailbox_read_state(settings):
    """``is_unread`` follows this mailbox's own read cursor on the thread."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    now = timezone.now()

    read_thread = factories.ThreadFactory(subject="read", messaged_at=now)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=read_thread, read_at=now)
    factories.MessageFactory(thread=read_thread, sender=contact, raw_mime=b"r" * 100)

    unread_thread = factories.ThreadFactory(subject="unread", messaged_at=now)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=unread_thread)
    # Another mailbox having read it must not count for this one.
    factories.ThreadAccessFactory(thread=unread_thread, read_at=now)
    factories.MessageFactory(thread=unread_thread, sender=contact, raw_mime=b"u" * 100)

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    unread_by_subject = {
        t["subject"]: t["is_unread"] for t in response.json()["largest_threads"]
    }
    assert unread_by_subject == {"read": False, "unread": True}


def test_largest_threads_are_capped_at_100(settings):
    """Only the 100 largest threads are returned, largest first."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    for index in range(101):
        thread = factories.ThreadFactory(subject=f"t{index}")
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        # Random bytes do not compress, so sizes really grow with the index.
        factories.MessageFactory(
            thread=thread, sender=contact, raw_mime=os.urandom(200 + index * 20)
        )

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    data = response.json()
    assert data["thread_count"] == 101
    sizes = [t["size"] for t in data["largest_threads"]]
    assert len(sizes) == 100
    assert sizes == sorted(sizes, reverse=True)
    # The smallest thread is the one cut.
    assert "t0" not in {t["subject"] for t in data["largest_threads"]}


def test_query_count_does_not_grow_with_threads(settings):
    """The breakdown costs a fixed number of queries, whatever the mailbox size.

    Guards against N+1 regressions, such as a per-thread query issued while
    building ``largest_threads``. It does not catch correlated subqueries
    nested inside a single query — those stay one query each.
    """
    settings.STORAGE_USAGE_CACHE_TTL = 0
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    client = APIClient()
    client.force_login(user)

    def _add_threads(count):
        for _ in range(count):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
            factories.MessageFactory(thread=thread, sender=contact)

    def _queries():
        with CaptureQueriesContext(connection) as context:
            assert client.get(_url(mailbox)).status_code == 200
        return len(context)

    _add_threads(1)
    with_one_thread = _queries()
    _add_threads(20)

    assert _queries() == with_one_thread


def test_largest_threads_leave_out_the_trashbin(settings):
    """Trashbin items are not weighed in the largest conversations.

    The subtotals already account for them, and the list offers to move a
    conversation to the trash: one already there would otherwise stay listed.
    A partly trashed conversation is listed with its live messages only, and a
    conversation holding spam not at all (it sits in the Spam folder).
    """
    overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)

    def _thread(subject, *messages_flags):
        thread = factories.ThreadFactory(subject=subject)
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        messages = [
            factories.MessageFactory(
                thread=thread, sender=contact, raw_mime=os.urandom(200), **flags
            )
            for flags in messages_flags
        ]
        thread.update_stats()
        return messages

    _thread("trashed", {"is_trashed": True})
    _thread("spam", {}, {"is_spam": True})
    live_msg, _ = _thread("partly trashed", {}, {"is_trashed": True})
    _thread("inbox", {})

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    threads = {t["subject"]: t for t in response.json()["largest_threads"]}
    assert set(threads) == {"partly trashed", "inbox"}

    partly_trashed = threads["partly trashed"]
    assert partly_trashed["message_count"] == 1
    assert partly_trashed["size"] == overhead + live_msg.blob.size_compressed
    # The folder flags still let the frontend open it where it is listed.
    assert partly_trashed["has_active"] is True
    assert partly_trashed["has_trashed"] is True
    assert threads["inbox"]["has_active"] is True


def test_largest_threads_report_whether_they_can_be_edited():
    """A thread shared with the mailbox as a viewer cannot be trashed.

    Mailbox admin rights do not reach it: the flag endpoint only edits threads
    the mailbox holds as an editor, so the list must not offer to trash it.
    """
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    for subject, role in [
        ("editor", models.ThreadAccessRoleChoices.EDITOR),
        ("viewer", models.ThreadAccessRoleChoices.VIEWER),
    ]:
        thread = factories.ThreadFactory(subject=subject)
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread, role=role)
        factories.MessageFactory(thread=thread, sender=contact)

    client = APIClient()
    client.force_login(user)
    response = client.get(_url(mailbox))

    assert response.status_code == 200
    abilities = {
        t["subject"]: t["abilities"] for t in response.json()["largest_threads"]
    }
    assert abilities == {"editor": {"edit": True}, "viewer": {"edit": False}}
