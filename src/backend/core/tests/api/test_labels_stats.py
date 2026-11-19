"""Tests for the GET /labels/stats/ endpoint."""

# pylint: disable=redefined-outer-name
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import enums
from core.factories import (
    LabelFactory,
    MailboxFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

URL = reverse("labels-stats")


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a mailbox the user can read."""
    return MailboxFactory(users_read=[user])


@pytest.fixture
def api_client(user):
    """Create an authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_thread(mailbox, *, labels=(), read=False, **thread_kwargs):
    """Create a thread seen by ``mailbox``, unread unless ``read``."""
    now = timezone.now()
    thread = ThreadFactory(has_messages=True, messaged_at=now, **thread_kwargs)
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
        read_at=now + timedelta(seconds=1) if read else None,
    )
    thread.labels.add(*labels)
    return thread


def test_labels_stats_counts_each_label(api_client, mailbox):
    """Every label gets its own counts; labels without threads are absent."""
    work = LabelFactory(mailbox=mailbox, name="Work")
    home = LabelFactory(mailbox=mailbox, name="Home")
    empty = LabelFactory(mailbox=mailbox, name="Empty")
    make_thread(mailbox, labels=[work])
    make_thread(mailbox, labels=[work, home], read=True)
    make_thread(mailbox, labels=[home])
    make_thread(mailbox)

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all,all_unread"}
    )

    assert response.status_code == 200
    assert response.data == {
        str(work.id): {"all": 2, "all_unread": 1},
        str(home.id): {"all": 2, "all_unread": 1},
    }
    assert str(empty.id) not in response.data


def test_labels_stats_excludes_spam_and_trash(api_client, mailbox):
    """Labels are counted as their folder lists threads by default."""
    label = LabelFactory(mailbox=mailbox)
    make_thread(mailbox, labels=[label])
    make_thread(mailbox, labels=[label], is_spam=True)
    make_thread(mailbox, labels=[label], is_trashed=True, has_trashed=True)

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all_unread"}
    )

    assert response.status_code == 200
    assert response.data == {str(label.id): {"all_unread": 1}}


def test_labels_stats_parent_does_not_count_children(api_client, mailbox):
    """Like the ``label_slug`` filter, a parent only counts its own threads."""
    child = LabelFactory(mailbox=mailbox, name="Work/Projects")
    parent = mailbox.labels.get(name="Work")
    make_thread(mailbox, labels=[child])

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all_unread"}
    )

    assert response.status_code == 200
    assert response.data == {str(child.id): {"all_unread": 1}}
    assert str(parent.id) not in response.data


def test_labels_stats_scoped_to_the_mailbox(api_client, user, mailbox):
    """A shared thread is counted with this mailbox's labels and read state."""
    other_mailbox = MailboxFactory(users_read=[user])
    label = LabelFactory(mailbox=mailbox)
    other_label = LabelFactory(mailbox=other_mailbox)
    thread = make_thread(mailbox, labels=[label, other_label], read=True)
    ThreadAccessFactory(
        mailbox=other_mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
        read_at=None,
    )
    # A thread of the other mailbox only, wearing this mailbox's label.
    make_thread(other_mailbox, labels=[label])

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all,all_unread"}
    )
    assert response.status_code == 200
    assert response.data == {str(label.id): {"all": 1, "all_unread": 0}}

    response = api_client.get(
        URL, {"mailbox_id": str(other_mailbox.id), "stats_fields": "all,all_unread"}
    )
    assert response.status_code == 200
    assert response.data == {str(other_label.id): {"all": 1, "all_unread": 1}}


def test_labels_stats_match_threads_stats_per_label(api_client, mailbox):
    """Each label count equals ``/threads/stats/?label_slug=`` for that label."""
    labels = [
        LabelFactory(mailbox=mailbox, name=name)
        for name in ("Work", "Work/Projects", "Home")
    ]
    make_thread(mailbox, labels=labels[:2])
    make_thread(mailbox, labels=labels[1:], read=True)
    make_thread(mailbox, labels=[labels[2]], is_spam=True)
    make_thread(mailbox, labels=[labels[0]], has_draft=True)
    fields = "all,all_unread,has_draft"

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": fields}
    )
    assert response.status_code == 200

    for label in labels:
        expected = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "label_slug": label.slug,
                "stats_fields": fields,
            },
        ).data
        assert response.data[str(label.id)] == expected


def test_labels_stats_query_count_does_not_grow_with_labels(api_client, mailbox):
    """The counts of all labels come from one grouped query."""

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            response = api_client.get(
                URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all_unread"}
            )
        assert response.status_code == 200
        return len(queries)

    make_thread(mailbox, labels=[LabelFactory(mailbox=mailbox)])
    with_one_label = count_queries()

    for _ in range(10):
        make_thread(mailbox, labels=[LabelFactory(mailbox=mailbox)])
    assert count_queries() == with_one_label


@pytest.mark.parametrize(
    "params",
    [
        {"stats_fields": "all_unread"},
        {"mailbox_id": "not-a-uuid", "stats_fields": "all_unread"},
    ],
)
def test_labels_stats_requires_a_valid_mailbox_id(api_client, params):
    """Unread counts only make sense for one mailbox."""
    response = api_client.get(URL, params)

    assert response.status_code == 400
    assert "mailbox_id" in response.data["detail"]


@pytest.mark.parametrize(
    ("stats_fields", "detail"),
    [
        ("", "Missing 'stats_fields' query parameter."),
        ("unknown", "Invalid field requested in stats_fields: unknown"),
        ("has_mention_unread", "Invalid field requested in stats_fields"),
    ],
)
def test_labels_stats_validates_stats_fields(api_client, mailbox, stats_fields, detail):
    """Fields are validated as for ``/threads/stats/``."""
    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": stats_fields}
    )

    assert response.status_code == 400
    assert detail in response.data["detail"]


def test_labels_stats_forbidden_without_mailbox_access(api_client):
    """Labels of a mailbox the user cannot access are not counted."""
    mailbox = MailboxFactory()
    LabelFactory(mailbox=mailbox)

    response = api_client.get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all_unread"}
    )

    assert response.status_code == 403


def test_labels_stats_requires_authentication(mailbox):
    """Anonymous users get a 401."""
    response = APIClient().get(
        URL, {"mailbox_id": str(mailbox.id), "stats_fields": "all_unread"}
    )

    assert response.status_code == 401
