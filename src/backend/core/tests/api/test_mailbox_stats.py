"""Tests for the mailbox statistics actions (overview + response times)."""
# pylint: disable=redefined-outer-name

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def _assign(thread, user):
    """Assign ``thread`` to ``user`` (ThreadEvent + UserEvent, as the app does)."""
    event = models.ThreadEvent.objects.create(
        thread=thread,
        type=enums.ThreadEventTypeChoices.ASSIGN,
        data={
            "assignees": [
                {"id": str(user.id), "name": user.full_name or "User"}
            ]
        },
    )
    models.UserEvent.objects.create(
        user=user,
        thread=thread,
        thread_event=event,
        type=enums.UserEventTypeChoices.ASSIGN,
    )


def _overview_url(mailbox):
    return reverse("mailboxes-stats-overview", kwargs={"pk": str(mailbox.id)})


def _response_times_url(mailbox):
    return reverse(
        "mailboxes-stats-response-times", kwargs={"pk": str(mailbox.id)}
    )


def _by_label_url(mailbox):
    return reverse(
        "mailboxes-stats-response-times-by-label", kwargs={"pk": str(mailbox.id)}
    )


def _set_created_at(message, when):
    """Force ``created_at`` (``auto_now_add`` ignores it at creation)."""
    models.Message.objects.filter(pk=message.pk).update(created_at=when)


@pytest.fixture
def admin_mailbox():
    """A user with admin access to a mailbox."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    return user, mailbox


def test_overview_requires_authentication():
    """Anonymous users cannot read stats."""
    mailbox = factories.MailboxFactory()
    assert APIClient().get(_overview_url(mailbox)).status_code == 401


def test_overview_requires_access():
    """A user without access to the mailbox gets a 404."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    client = APIClient()
    client.force_login(user)
    assert client.get(_overview_url(mailbox)).status_code == 404


def test_invalid_timeframe_returns_400(admin_mailbox):
    """An unknown timeframe is rejected."""
    user, mailbox = admin_mailbox
    client = APIClient()
    client.force_login(user)
    response = client.get(_overview_url(mailbox) + "?timeframe=nope")
    assert response.status_code == 400


def test_overview_counts(admin_mailbox):
    """Overview counts conversations, messages and sent emails in the window,
    excluding drafts and out-of-window messages."""
    user, mailbox = admin_mailbox
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)

    now = timezone.now()
    recent = now - timedelta(days=1)
    old = now - timedelta(days=40)

    incoming = factories.MessageFactory(thread=thread, is_sender=False)
    _set_created_at(incoming, recent)
    sent = factories.MessageFactory(thread=thread, is_sender=True)
    _set_created_at(sent, recent + timedelta(minutes=5))
    draft = factories.MessageFactory(
        thread=thread, is_sender=True, is_draft=True
    )
    _set_created_at(draft, recent)
    stale = factories.MessageFactory(thread=thread, is_sender=False)
    _set_created_at(stale, old)

    client = APIClient()
    client.force_login(user)
    response = client.get(_overview_url(mailbox) + "?timeframe=last_30_days")

    assert response.status_code == 200
    body = response.json()
    assert body["conversations"] == 1
    assert body["messages"] == 2
    assert body["sent"] == 1
    # The incoming email has a reply, so nothing is unreplied.
    assert body["unreplied"] == 0
    assert body["unreplied_assigned"] == 0
    assert body["unreplied_unassigned"] == 0


def test_response_times(admin_mailbox):
    """Average response time is the gap to the first later outgoing reply;
    incoming with no reply are unreplied; authorship is by sender_user."""
    user, mailbox = admin_mailbox
    now = timezone.now()
    base = now - timedelta(days=2)

    # Thread 1: incoming answered one hour later by Alice.
    thread1 = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread1)
    incoming1 = factories.MessageFactory(thread=thread1, is_sender=False)
    _set_created_at(incoming1, base)
    alice = factories.UserFactory(full_name="Alice")
    reply1 = factories.MessageFactory(
        thread=thread1, is_sender=True, sender_user=alice
    )
    _set_created_at(reply1, base + timedelta(hours=1))

    # Thread 2: incoming that never got a reply.
    thread2 = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread2)
    incoming2 = factories.MessageFactory(thread=thread2, is_sender=False)
    _set_created_at(incoming2, base)

    client = APIClient()
    client.force_login(user)
    response = client.get(
        _response_times_url(mailbox) + "?timeframe=last_30_days"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["incoming"] == 2
    assert data["replied"] == 1
    assert data["unreplied"] == 1
    assert data["average_response_seconds"] == 3600
    assert data["authors"] == [
        {
            "author": "Alice",
            "replied": 1,
            "average_response_seconds": 3600,
            "unanswered": 0,
        }
    ]


def test_unanswered_and_unread_splits(admin_mailbox):
    """Overview splits unreplied/unread by assignment; response-times reports
    each author's assigned-but-unanswered conversations."""
    user, mailbox = admin_mailbox
    now = timezone.now()
    base = now - timedelta(days=2)
    alice = factories.UserFactory(full_name="Alice")

    # Thread A: unanswered, assigned to Alice, read.
    thread_a = factories.ThreadFactory(messaged_at=base)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_a, read_at=now)
    _set_created_at(
        factories.MessageFactory(thread=thread_a, is_sender=False), base
    )
    _assign(thread_a, alice)

    # Thread B: unanswered, unassigned, unread (no read cursor).
    thread_b = factories.ThreadFactory(messaged_at=base)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_b)
    _set_created_at(
        factories.MessageFactory(thread=thread_b, is_sender=False), base
    )

    # Thread C: answered by Alice, read.
    thread_c = factories.ThreadFactory(messaged_at=base)
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_c, read_at=now)
    _set_created_at(
        factories.MessageFactory(thread=thread_c, is_sender=False), base
    )
    _set_created_at(
        factories.MessageFactory(
            thread=thread_c, is_sender=True, sender_user=alice
        ),
        base + timedelta(hours=1),
    )

    client = APIClient()
    client.force_login(user)

    overview = client.get(
        _overview_url(mailbox) + "?timeframe=last_30_days"
    ).json()
    assert overview["unreplied"] == 2
    assert overview["unreplied_assigned"] == 1
    assert overview["unreplied_unassigned"] == 1
    # A and C are read; only B is unread (and it is unassigned).
    assert overview["unread"] == 1
    assert overview["unread_assigned"] == 0
    assert overview["unread_unassigned"] == 1

    response_times = client.get(
        _response_times_url(mailbox) + "?timeframe=last_30_days"
    ).json()
    alice_row = next(
        row for row in response_times["authors"] if row["author"] == "Alice"
    )
    assert alice_row["replied"] == 1
    assert alice_row["unanswered"] == 1


def test_response_times_by_label(admin_mailbox):
    """Per-label totals: an email counts once per label on its thread; replied
    and summed response seconds only for answered ones."""
    user, mailbox = admin_mailbox
    now = timezone.now()
    base = now - timedelta(days=2)
    contact = factories.ContactFactory(mailbox=mailbox)

    bug = models.Label.objects.create(
        mailbox=mailbox, name="Support/Bug", slug="support-bug"
    )
    billing = models.Label.objects.create(
        mailbox=mailbox, name="Support/Billing", slug="support-billing"
    )

    # Thread 1: label Bug, answered 1h later.
    thread1 = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread1)
    thread1.labels.add(bug)
    _set_created_at(
        factories.MessageFactory(thread=thread1, sender=contact, is_sender=False),
        base,
    )
    _set_created_at(
        factories.MessageFactory(thread=thread1, sender=contact, is_sender=True),
        base + timedelta(hours=1),
    )

    # Thread 2: labels Bug + Billing, unanswered.
    thread2 = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread2)
    thread2.labels.add(bug, billing)
    _set_created_at(
        factories.MessageFactory(thread=thread2, sender=contact, is_sender=False),
        base,
    )

    client = APIClient()
    client.force_login(user)
    response = client.get(_by_label_url(mailbox) + "?timeframe=last_30_days")

    assert response.status_code == 200
    rows = {row["label"]: row for row in response.json()["labels"]}

    assert rows[str(bug.id)]["received"] == 2
    assert rows[str(bug.id)]["replied"] == 1
    assert rows[str(bug.id)]["response_seconds_total"] == 3600
    assert rows[str(billing.id)]["received"] == 1
    assert rows[str(billing.id)]["replied"] == 0
    assert rows[str(billing.id)]["response_seconds_total"] == 0
