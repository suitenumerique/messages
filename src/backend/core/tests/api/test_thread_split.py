"""Tests for the Thread split API endpoint."""

import uuid
from datetime import timedelta
from unittest.mock import patch

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
    ThreadFactory,
    UserFactory,
)
from core.models import Thread, ThreadAccess

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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_at_second_message_in_two_message_thread(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_multi_message(_mock_index_msg, _mock_reindex_thread, api_client):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_accesses_copied(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_accesses_preserve_read_at_and_starred_at(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_labels_copied(_mock_index_msg, _mock_reindex_thread, api_client):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_parent_references_fixed(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_stats_snippet_updated(
    _mock_index_msg, _mock_reindex_thread, api_client
):
    """Both old and new threads have their stats and snippet updated after split."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)

    # Simulate snippet set from the last message (as inbound_create does)
    thread.snippet = "Stale snippet from moved message"
    thread.save(update_fields=["snippet"])

    _setup_editor_access(user, mailbox, thread)

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[2].id)})
    assert response.status_code == status.HTTP_201_CREATED

    thread.refresh_from_db()
    new_thread = Thread.objects.get(id=response.data["id"])

    # Both threads should have messaged_at set
    assert thread.messaged_at is not None
    assert new_thread.messaged_at is not None

    # Old thread snippet should be recalculated (no longer the stale value)
    assert thread.snippet != "Stale snippet from moved message"
    assert new_thread.snippet is not None


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_summaries_invalidated(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_subject_inherited(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_subject_fallback_to_original(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_opensearch_reindex_called(
    mock_index_msg, mock_reindex_thread, api_client
):
    """OpenSearch reindex tasks are called for moved messages and both threads."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    mailbox = MailboxFactory()
    thread, messages = _create_thread_with_messages(mailbox, count=3)
    _setup_editor_access(user, mailbox, thread)

    # Reset mocks to ignore calls from setup (MessageFactory triggers signals)
    mock_index_msg.reset_mock()
    mock_reindex_thread.reset_mock()

    url = _get_split_url(thread.id)
    response = api_client.post(url, {"message_id": str(messages[1].id)})
    assert response.status_code == status.HTTP_201_CREATED

    new_thread_id = response.data["id"]

    # reindex_thread_task should be called for both threads
    thread_ids = {str(thread.id), new_thread_id}
    actual_thread_calls = {
        call.args[0] for call in mock_reindex_thread.delay.call_args_list
    }
    assert actual_thread_calls == thread_ids


@patch("core.signals.reindex_thread_task")
@patch("core.signals.index_message_task")
def test_split_thread_returns_new_thread_data(
    _mock_index_msg, _mock_reindex_thread, api_client
):
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
