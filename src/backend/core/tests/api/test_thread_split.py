"""Tests for the Thread split API endpoint."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums
from core.factories import (
    ContactFactory,
    LabelFactory,
    MailboxAccessFactory,
    MailboxFactory,
    MessageFactory,
    ThreadAccessFactory,
    ThreadEventFactory,
    ThreadFactory,
    UserEventFactory,
    UserFactory,
)
from core.models import Thread, ThreadAccess, ThreadEvent, UserEvent

pytestmark = pytest.mark.django_db


def _get_split_url(thread_id):
    return reverse("threads-split", kwargs={"pk": str(thread_id)})


def _create_thread_with_messages(mailbox, count=3, **thread_kwargs):
    """Helper to create a thread with ordered messages."""
    thread = ThreadFactory(**thread_kwargs)
    contact = ContactFactory(mailbox=mailbox)
    now = timezone.now()
    messages = []
    for i in range(count):
        msg = MessageFactory(
            thread=thread,
            sender=contact,
            created_at=now + timedelta(minutes=i),
        )
        messages.append(msg)
    return thread, messages


def _setup_editor_access(user, mailbox, thread):
    """Give a user editor access to a thread via a mailbox."""
    MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )


# --- Permission tests ---


def test_split_thread_unauthenticated(api_client):
    """Unauthenticated users cannot split a thread."""
    thread = ThreadFactory()
    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(uuid.uuid4())})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_split_thread_no_access(api_client):
    """A user with no access to the thread cannot split it."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_split_thread_viewer_only(api_client):
    """A user with only VIEWER role cannot split a thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.VIEWER,
    )
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.VIEWER,
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_403_FORBIDDEN


# --- Feature flag tests ---


@override_settings(FEATURE_THREAD_SPLIT=False)
def test_split_thread_feature_disabled(api_client):
    """When FEATURE_THREAD_SPLIT is False the action is not reachable."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_404_NOT_FOUND
    # No actual split happened.
    assert Thread.objects.filter(id=thread.id).count() == 1


def test_split_thread_viewer_mailbox_with_editor_thread_access_forbidden(api_client):
    """Previously-missed scenario: a user with VIEWER MailboxAccess on a
    shared inbox cannot split a thread even when the mailbox itself has
    EDITOR ThreadAccess on the thread — the user's own mailbox role must
    be honoured.
    """
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory(users_read=[user])  # VIEWER mailbox role
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_403_FORBIDDEN


# --- Validation tests ---


def test_split_thread_missing_message_id(api_client):
    """Splitting without a message_id returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, _ = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "message_id" in response.data["detail"].lower()


def test_split_thread_message_not_found(api_client):
    """Splitting with a nonexistent message_id returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, _ = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(uuid.uuid4())})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "not found" in response.data["detail"].lower()


def test_split_thread_message_wrong_thread(api_client):
    """Splitting with a message from a different thread returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, _ = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    _, other_messages = _create_thread_with_messages(mailbox, count=2)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(other_messages[0].id)})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "does not belong" in response.data["detail"].lower()


def test_split_thread_at_draft_message(api_client):
    """Splitting at a draft message returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread = ThreadFactory()
    contact = ContactFactory(mailbox=mailbox)
    now = timezone.now()
    MessageFactory(thread=thread, sender=contact, created_at=now)
    draft_msg = MessageFactory(
        thread=thread,
        sender=contact,
        created_at=now + timedelta(minutes=1),
        is_draft=True,
    )
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(draft_msg.id)})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "draft" in response.data["detail"].lower()


def test_split_thread_single_message(api_client):
    """Splitting a thread with only one message returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=1)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[0].id)})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "only one message" in response.data["detail"].lower()


def test_split_thread_at_first_message(api_client):
    """Splitting at the first message returns 400."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[0].id)})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "first message" in response.data["detail"].lower()


# --- Success tests ---


def test_split_thread_at_second_message_in_two_message_thread(api_client):
    """Split at the 2nd message in a 2-message thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=2)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread_id = response.data["id"]
    new_thread = Thread.objects.get(id=new_thread_id)

    # Old thread should have 1 message, new thread should have 1
    assert thread.messages.count() == 1
    assert new_thread.messages.count() == 1

    # The moved message should be in the new thread
    messages[1].refresh_from_db()
    assert messages[1].thread_id == new_thread.id

    # The first message stays in the original thread
    messages[0].refresh_from_db()
    assert messages[0].thread_id == thread.id


def test_split_thread_multi_message(api_client):
    """Split at the 3rd message in a 5-message thread: msgs 3-5 move, 1-2 stay."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=5)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[2].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread_id = response.data["id"]
    new_thread = Thread.objects.get(id=new_thread_id)

    # Old thread: messages 0, 1 (2 messages)
    assert thread.messages.count() == 2
    # New thread: messages 2, 3, 4 (3 messages)
    assert new_thread.messages.count() == 3

    for msg in messages[:2]:
        msg.refresh_from_db()
        assert msg.thread_id == thread.id

    for msg in messages[2:]:
        msg.refresh_from_db()
        assert msg.thread_id == new_thread.id


def test_split_thread_accesses_copied(api_client):
    """All ThreadAccess entries are copied to the new thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox1 = MailboxFactory()
    mailbox2 = MailboxFactory()
    MailboxAccessFactory(
        mailbox=mailbox1, user=user, role=enums.MailboxRoleChoices.ADMIN
    )
    MailboxAccessFactory(
        mailbox=mailbox2, user=user, role=enums.MailboxRoleChoices.ADMIN
    )

    thread, messages = _create_thread_with_messages(mailbox1, count=3)
    ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    ThreadAccessFactory(
        mailbox=mailbox2,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.VIEWER,
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread = Thread.objects.get(id=response.data["id"])

    # Both accesses should be copied
    new_accesses = ThreadAccess.objects.filter(thread=new_thread)
    assert new_accesses.count() == 2
    assert new_accesses.filter(
        mailbox=mailbox1, role=enums.ThreadAccessRoleChoices.EDITOR
    ).exists()
    assert new_accesses.filter(
        mailbox=mailbox2, role=enums.ThreadAccessRoleChoices.VIEWER
    ).exists()


def test_split_thread_accesses_preserve_read_at_and_starred_at(api_client):
    """read_at is always preserved. starred_at is only preserved when it is
    more recent than the split message creation date."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox1 = MailboxFactory()
    mailbox2 = MailboxFactory()
    MailboxAccessFactory(
        mailbox=mailbox1, user=user, role=enums.MailboxRoleChoices.ADMIN
    )
    MailboxAccessFactory(
        mailbox=mailbox2, user=user, role=enums.MailboxRoleChoices.ADMIN
    )

    thread, messages = _create_thread_with_messages(mailbox1, count=3)
    split_message = messages[1]

    # starred_at before split message → should NOT be copied
    ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
        read_at=split_message.created_at + timedelta(hours=1),
        starred_at=split_message.created_at - timedelta(days=1),
    )
    # starred_at after split message → should be copied
    ThreadAccessFactory(
        mailbox=mailbox2,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.VIEWER,
        read_at=None,
        starred_at=split_message.created_at + timedelta(hours=1),
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(split_message.id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread = Thread.objects.get(id=response.data["id"])
    new_accesses = ThreadAccess.objects.filter(thread=new_thread)

    access1 = new_accesses.get(mailbox=mailbox1)
    assert access1.read_at == split_message.created_at + timedelta(hours=1)
    assert access1.starred_at is None  # starred before split → dropped

    access2 = new_accesses.get(mailbox=mailbox2)
    assert access2.read_at is None
    assert access2.starred_at == split_message.created_at + timedelta(hours=1)


def test_split_thread_labels_copied(api_client):
    """Labels from the old thread are also applied to the new thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    label1 = LabelFactory(mailbox=mailbox, threads=[thread])
    label2 = LabelFactory(mailbox=mailbox, threads=[thread])

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread = Thread.objects.get(id=response.data["id"])
    new_labels = set(new_thread.labels.values_list("id", flat=True))
    assert label1.id in new_labels
    assert label2.id in new_labels


def test_split_thread_parent_references_fixed(api_client):
    """Cross-thread parent references are set to None after split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread = ThreadFactory()
    contact = ContactFactory(mailbox=mailbox)
    now = timezone.now()

    msg1 = MessageFactory(thread=thread, sender=contact, created_at=now)
    msg2 = MessageFactory(
        thread=thread,
        sender=contact,
        created_at=now + timedelta(minutes=1),
        parent=msg1,
    )
    msg3 = MessageFactory(
        thread=thread,
        sender=contact,
        created_at=now + timedelta(minutes=2),
        parent=msg2,
    )

    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    # Split at msg2 -> msg2 and msg3 move to new thread
    response = api_client.post(url, {"message_id": str(msg2.id)})
    assert response.status_code == status.HTTP_201_CREATED

    # msg2's parent was msg1 which stays in old thread -> should be set to None
    msg2.refresh_from_db()
    assert msg2.parent is None

    # msg3's parent was msg2 which moved too -> should remain
    msg3.refresh_from_db()
    assert msg3.parent_id == msg2.id


def test_split_thread_stats_snippet_updated(api_client):
    """Both old and new threads have their stats and snippet updated after split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    contact = ContactFactory(mailbox=mailbox)
    thread = ThreadFactory()
    now = timezone.now()
    messages = [
        MessageFactory(
            thread=thread,
            sender=contact,
            created_at=now + timedelta(minutes=i),
            raw_mime=(
                b"From: a@example.com\r\nSubject: t\r\n\r\nBody " + str(i + 1).encode()
            ),
        )
        for i in range(3)
    ]
    thread.update_stats()
    assert thread.snippet == "Body 3"

    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[2].id)})
    assert response.status_code == status.HTTP_201_CREATED

    thread.refresh_from_db()
    new_thread = Thread.objects.get(id=response.data["id"])

    # Both threads should have messaged_at set
    assert thread.messaged_at is not None
    assert new_thread.messaged_at is not None

    # Each thread derives its snippet from its own latest remaining message
    assert thread.snippet == "Body 2"
    assert new_thread.snippet == "Body 3"


def test_split_thread_summaries_invalidated(api_client):
    """Both old and new thread summaries are set to None after split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    thread.summary = "Old summary"
    thread.save(update_fields=["summary"])
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    thread.refresh_from_db()
    assert thread.summary is None

    new_thread = Thread.objects.get(id=response.data["id"])
    assert new_thread.summary is None


def test_split_thread_subject_inherited(api_client):
    """New thread inherits subject from split message or original thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread = ThreadFactory(subject="Original Subject")
    contact = ContactFactory(mailbox=mailbox)
    now = timezone.now()

    MessageFactory(thread=thread, sender=contact, created_at=now, subject="First msg")
    msg2 = MessageFactory(
        thread=thread,
        sender=contact,
        created_at=now + timedelta(minutes=1),
        subject="Second msg subject",
    )
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(msg2.id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread = Thread.objects.get(id=response.data["id"])
    assert new_thread.subject == "Second msg subject"


def test_split_thread_subject_fallback_to_original(api_client):
    """When split message has no subject, new thread inherits from original."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread = ThreadFactory(subject="Original Subject")
    contact = ContactFactory(mailbox=mailbox)
    now = timezone.now()

    MessageFactory(thread=thread, sender=contact, created_at=now, subject="First msg")
    msg2 = MessageFactory(
        thread=thread,
        sender=contact,
        created_at=now + timedelta(minutes=1),
        subject=None,
    )
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(msg2.id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread = Thread.objects.get(id=response.data["id"])
    assert new_thread.subject == "Original Subject"


@override_settings(OPENSEARCH_INDEX_THREADS=True)
@patch("core.signals.enqueue_thread_reindex")
def test_split_thread_opensearch_reindex_called(
    mock_enqueue_reindex, api_client, django_capture_on_commit_callbacks
):
    """Both threads are scheduled for reindex after a split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    # Reset the mock to ignore calls from setup (MessageFactory triggers signals)
    mock_enqueue_reindex.reset_mock()

    url = _get_split_url(thread.id)
    # The reindex enqueue runs inside ``transaction.on_commit``; pytest-django's
    # rolling test transaction never commits, so we capture & fire the
    # callbacks manually to exercise the real signal path.
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread_id = response.data["id"]

    # enqueue_thread_reindex must be called for both threads.
    expected_thread_ids = {str(thread.id), str(new_thread_id)}
    actual_thread_ids = {
        str(call.args[0]) for call in mock_enqueue_reindex.call_args_list
    }
    assert expected_thread_ids <= actual_thread_ids


def test_split_thread_returns_new_thread_data(api_client):
    """The response contains the serialized new thread."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    # Response should contain standard thread fields
    assert "id" in response.data
    assert "subject" in response.data
    assert "messages" in response.data
    assert "accesses" in response.data


# --- ThreadEvent / UserEvent split tests ---


def _force_event_created_at(event, created_at):
    """Bypass ``auto_now_add`` to pin a ThreadEvent at a specific timestamp."""
    ThreadEvent.objects.filter(pk=event.pk).update(created_at=created_at)
    event.refresh_from_db()


def test_split_thread_moves_free_event_after_split_point(api_client):
    """A ThreadEvent without a message FK created at or after the split point
    must follow the new thread, symmetric with how messages are split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    # Free event posted after messages[1] (the split point)
    event_after = ThreadEventFactory(thread=thread, author=user, message=None)
    _force_event_created_at(event_after, messages[1].created_at + timedelta(seconds=30))
    # Free event posted before the split point — must stay on the old thread.
    # NOTE: ``_create_thread_with_messages`` doesn't bypass ``auto_now_add`` so
    # all messages[*].created_at are within microseconds of each other. We
    # anchor offsets on messages[1].created_at (the split point) to guarantee
    # strict ordering.
    event_before = ThreadEventFactory(thread=thread, author=user, message=None)
    _force_event_created_at(
        event_before, messages[1].created_at - timedelta(seconds=30)
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED
    new_thread_id = response.data["id"]

    event_after.refresh_from_db()
    event_before.refresh_from_db()
    assert str(event_after.thread_id) == new_thread_id
    assert event_before.thread_id == thread.id


def test_split_thread_moves_event_attached_to_moved_message(api_client):
    """A ThreadEvent attached to a moved message must follow its message, even
    if its own created_at is earlier than the split point."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    # Event attached to the split message itself, with a created_at BEFORE
    # the split point — the FK must win over the timestamp.
    event_on_moved = ThreadEventFactory(thread=thread, author=user, message=messages[1])
    _force_event_created_at(event_on_moved, messages[0].created_at - timedelta(hours=1))
    # Event attached to a message that stays — must not move.
    event_on_staying = ThreadEventFactory(
        thread=thread, author=user, message=messages[0]
    )
    _force_event_created_at(
        event_on_staying, messages[2].created_at + timedelta(hours=1)
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED
    new_thread_id = response.data["id"]

    event_on_moved.refresh_from_db()
    event_on_staying.refresh_from_db()
    assert str(event_on_moved.thread_id) == new_thread_id
    assert event_on_staying.thread_id == thread.id


def test_split_thread_moves_user_events_with_their_thread_event(api_client):
    """UserEvent.thread is denormalized from thread_event.thread. When an
    event is moved, its UserEvents must be updated to keep the invariant."""
    user = UserFactory()
    mentioned = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    event_after = ThreadEventFactory(thread=thread, author=user, message=None)
    _force_event_created_at(event_after, messages[1].created_at + timedelta(seconds=30))
    mention_after = UserEventFactory(
        user=mentioned, thread=thread, thread_event=event_after
    )

    event_before = ThreadEventFactory(thread=thread, author=user, message=None)
    _force_event_created_at(
        event_before, messages[1].created_at - timedelta(seconds=30)
    )
    mention_before = UserEventFactory(
        user=mentioned, thread=thread, thread_event=event_before
    )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED
    new_thread_id = response.data["id"]

    mention_after.refresh_from_db()
    mention_before.refresh_from_db()
    assert str(mention_after.thread_id) == new_thread_id
    assert mention_before.thread_id == thread.id

    # Invariant: every UserEvent.thread matches its thread_event.thread.
    for ue in UserEvent.objects.all():
        assert ue.thread_id == ue.thread_event.thread_id


def test_split_thread_events_are_counted_on_new_thread(api_client):
    """After split, events must be distributed so that counts add up on both
    threads — guards against a regression where events would stay on the old
    thread or be duplicated."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    # Two events before split, three after.
    for i in range(2):
        event = ThreadEventFactory(thread=thread, author=user, message=None)
        _force_event_created_at(
            event, messages[1].created_at - timedelta(seconds=i + 1)
        )
    for i in range(3):
        event = ThreadEventFactory(thread=thread, author=user, message=None)
        _force_event_created_at(
            event, messages[1].created_at + timedelta(seconds=i + 1)
        )

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED
    new_thread_id = response.data["id"]

    assert ThreadEvent.objects.filter(thread=thread).count() == 2
    assert ThreadEvent.objects.filter(thread_id=new_thread_id).count() == 3
