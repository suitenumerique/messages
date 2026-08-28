"""Test changing flags on messages or threads."""

# pylint: disable=redefined-outer-name,too-many-lines

import json
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums, models
from core.factories import (
    MailboxFactory,
    MessageFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

API_URL = reverse("change-flag")


# --- Tests for Unread Flag using read_at (operates on ThreadAccess.read_at) ---


def test_api_flag_unread_resolves_thread_ids_from_message_ids(api_client):
    """Sending unread flag with message_ids (no thread_ids) resolves threads from messages."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread)
    msg2 = MessageFactory(thread=thread)

    assert access.read_at is None

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "message_ids": [str(msg1.id), str(msg2.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.read_at is not None
    assert access.read_at.isoformat() == read_at_value


def test_api_flag_unread_resolves_thread_ids_from_message_ids_multiple_threads(
    api_client,
):
    """Sending unread flag with message_ids spanning multiple threads updates all."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])

    thread1 = ThreadFactory(messaged_at=timezone.now())
    access1 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread1)

    thread2 = ThreadFactory(messaged_at=timezone.now())
    access2 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg2 = MessageFactory(thread=thread2)

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "message_ids": [str(msg1.id), str(msg2.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 2

    access1.refresh_from_db()
    access2.refresh_from_db()
    assert access1.read_at.isoformat() == read_at_value
    assert access2.read_at.isoformat() == read_at_value


def test_api_flag_unread_resolves_thread_ids_from_message_ids_ignores_inaccessible(
    api_client,
):
    """Messages in inaccessible threads are ignored when resolving thread_ids."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])

    # Accessible thread
    thread1 = ThreadFactory(messaged_at=timezone.now())
    access1 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread1)

    # Inaccessible thread (different mailbox, user has no access)
    other_mailbox = MailboxFactory()
    thread2 = ThreadFactory(messaged_at=timezone.now())
    ThreadAccessFactory(
        mailbox=other_mailbox, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg2 = MessageFactory(thread=thread2)

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "message_ids": [str(msg1.id), str(msg2.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access1.refresh_from_db()
    assert access1.read_at is not None


def test_api_flag_unread_with_thread_ids_ignores_message_ids_resolution(api_client):
    """When thread_ids are provided, message_ids should not trigger thread resolution."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])

    thread1 = ThreadFactory(messaged_at=timezone.now())
    access1 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
    )

    thread2 = ThreadFactory(messaged_at=timezone.now())
    access2 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg_in_thread2 = MessageFactory(thread=thread2)

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread1.id)],
        "message_ids": [str(msg_in_thread2.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    # Only thread1 (from thread_ids) should be updated, not thread2 (from message_ids)
    assert response.data["updated_threads"] == 1

    access1.refresh_from_db()
    access2.refresh_from_db()
    assert access1.read_at is not None
    assert access2.read_at is None


def test_api_flag_read_at_sets_timestamp(api_client):
    """Sending read_at=timestamp sets ThreadAccess.read_at to that exact value."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)
    MessageFactory(thread=thread)

    assert access.read_at is None

    read_at_value = (timezone.now() - timedelta(minutes=5)).isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.read_at is not None
    assert access.read_at.isoformat() == read_at_value


def test_api_flag_read_at_null_marks_all_unread(api_client):
    """Sending read_at=null resets ThreadAccess.read_at to None (all unread)."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    # Start with everything read
    access.read_at = timezone.now()
    access.save(update_fields=["read_at"])

    data = {
        "flag": "unread",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": None,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.read_at is None


def test_api_flag_unread_requires_read_at(api_client):
    """Test that unread flag requires read_at parameter."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    data = {
        "flag": "unread",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        # Missing read_at
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "read_at" in response.data["detail"]


def test_api_flag_read_at_rejects_invalid_datetime(api_client):
    """Sending an invalid read_at value should return a 400 error."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": "not-a-date",
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "read_at" in response.data["detail"]


def test_api_flag_unread_per_mailbox_isolation(api_client):
    """Marking as read in one mailbox does not affect another mailbox's read state."""
    user1 = UserFactory()
    user2 = UserFactory()
    mailbox1 = MailboxFactory(users_read=[user1])
    mailbox2 = MailboxFactory(users_read=[user2])
    thread = ThreadFactory(messaged_at=timezone.now())
    access1 = ThreadAccessFactory(
        mailbox=mailbox1, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    access2 = ThreadAccessFactory(
        mailbox=mailbox2, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    # User1 marks thread as read
    api_client.force_authenticate(user=user1)
    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox1.id),
        "read_at": timezone.now().isoformat(),
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    # User1's access should be read, user2's should remain unread
    access1.refresh_from_db()
    access2.refresh_from_db()
    assert access1.read_at is not None
    assert access2.read_at is None


@pytest.mark.django_db(transaction=True)
def test_api_flag_read_at_syncs_opensearch(api_client, settings):
    """Sending read_at should explicitly sync OpenSearch."""
    settings.OPENSEARCH_INDEX_THREADS = True

    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": timezone.now().isoformat(),
    }

    with patch("core.api.viewsets.flag.update_threads_mailbox_flags_task") as mock_task:
        response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    mock_task.delay.assert_called_once_with([str(thread.id)])


def test_api_flag_read_at_multiple_threads(api_client):
    """Sending read_at with multiple thread_ids updates all accesses."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread1 = ThreadFactory(messaged_at=timezone.now())
    access1 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread1)
    thread2 = ThreadFactory(messaged_at=timezone.now())
    access2 = ThreadAccessFactory(
        mailbox=mailbox, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread2)

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread1.id), str(thread2.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 2

    access1.refresh_from_db()
    access2.refresh_from_db()
    assert access1.read_at.isoformat() == read_at_value
    assert access2.read_at.isoformat() == read_at_value


def test_api_flag_read_at_viewer_can_update(api_client):
    """A user with VIEWER ThreadAccess role can update read_at."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory(messaged_at=timezone.now())
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
    )
    MessageFactory(thread=thread)

    assert access.read_at is None

    read_at_value = timezone.now().isoformat()
    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
        "read_at": read_at_value,
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.read_at is not None
    assert access.read_at.isoformat() == read_at_value


def test_api_flag_unread_requires_mailbox_id(api_client):
    """Test that unread flag requires mailbox_id parameter."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )

    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        # Missing mailbox_id
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_api_flag_mark_messages_unauthorized(api_client):
    """Test marking messages without authentication."""
    response = api_client.post(API_URL, data={}, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_api_flag_mark_messages_no_permission(api_client):
    """Test marking threads in a mailbox the user doesn't have access to."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    other_mailbox = MailboxFactory()  # User does not have access
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=other_mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )

    data = {
        "flag": "unread",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(other_mailbox.id),
        "read_at": timezone.now().isoformat(),
    }
    response = api_client.post(API_URL, data=data, format="json")

    # User without edit access: returns 200 but no threads updated
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 0


@pytest.mark.parametrize(
    "data",
    [
        {"value": True, "message_ids": lambda msg: [str(msg.id)]},  # missing flag
        {"flag": "unread", "message_ids": lambda msg: [str(msg.id)]},  # missing value
        {"flag": "unread", "value": True},  # missing message_ids and thread_ids
        {
            "flag": "invalid_flag",
            "value": True,
            "message_ids": lambda msg: [str(msg.id)],
        },  # invalid flag
        {
            "flag": "unread",
            "value": "maybe",
            "message_ids": lambda msg: [str(msg.id)],
        },  # invalid value
        {
            "flag": "unread",
            "value": True,
            "message_ids": [],
            "thread_ids": [],
        },  # empty ids
        {"flag": "unread", "value": True, "message_ids": ["aa"]},  # invalid message ids
        {
            "flag": "unread",
            "value": True,
            "message_ids": {"test": "test"},
        },  # invalid message ids
    ],
)
def test_api_flag_mark_messages_invalid_requests(api_client, data):
    """
    Parametrized test for invalid flag, missing ids, and invalid value.
    """
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg = MessageFactory(thread=thread)
    if callable(data.get("message_ids", None)):
        data["message_ids"] = json.loads(json.dumps(data["message_ids"](msg)))
    if callable(data.get("thread_ids", None)):
        data["thread_ids"] = json.loads(json.dumps(data["thread_ids"](thread)))
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- Tests for Starred Flag (operates on ThreadAccess.starred_at) ---


def test_api_flag_starred_requires_mailbox_id(api_client):
    """Test that starring requires a mailbox_id."""
    user = UserFactory()
    api_client.force_authenticate(user=user)

    data = {
        "flag": "starred",
        "value": True,
        "thread_ids": ["00000000-0000-0000-0000-000000000001"],
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "mailbox_id" in response.data["detail"]


@pytest.mark.django_db(transaction=True)
@patch("core.api.viewsets.flag.update_threads_mailbox_flags_task")
def test_api_flag_mark_thread_starred_success(mock_task, api_client):
    """Test starring a thread sets starred_at on the ThreadAccess."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
    )
    MessageFactory(thread=thread)

    assert access.starred_at is None

    data = {
        "flag": "starred",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.starred_at is not None
    mock_task.delay.assert_called_once()


@pytest.mark.django_db(transaction=True)
@patch("core.api.viewsets.flag.update_threads_mailbox_flags_task")
def test_api_flag_mark_thread_unstarred_success(mock_task, api_client):
    """Test unstarring a thread clears starred_at on the ThreadAccess."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    access = ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.VIEWER,
        starred_at=timezone.now(),
    )
    MessageFactory(thread=thread)

    assert access.starred_at is not None

    data = {
        "flag": "starred",
        "value": False,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    access.refresh_from_db()
    assert access.starred_at is None
    mock_task.delay.assert_called_once()


def test_api_flag_starred_scoped_to_mailbox(api_client):
    """Test that starring via mailbox A does not affect mailbox B."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox_a = MailboxFactory(users_read=[user])
    mailbox_b = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    access_a = ThreadAccessFactory(mailbox=mailbox_a, thread=thread)
    access_b = ThreadAccessFactory(mailbox=mailbox_b, thread=thread)
    MessageFactory(thread=thread)

    data = {
        "flag": "starred",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox_a.id),
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK

    access_a.refresh_from_db()
    access_b.refresh_from_db()
    assert access_a.starred_at is not None
    assert access_b.starred_at is None


def test_api_flag_starred_no_permission_on_mailbox(api_client):
    """Test that starring via a mailbox the user doesn't have access to does nothing."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    other_mailbox = MailboxFactory()  # User does not have access
    thread = ThreadFactory()
    access = ThreadAccessFactory(
        mailbox=other_mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    MessageFactory(thread=thread)

    data = {
        "flag": "starred",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(other_mailbox.id),
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 0

    access.refresh_from_db()
    assert access.starred_at is None


def test_api_flag_viewer_can_star_thread(api_client):
    """Test that a VIEWER can star a thread (personal action)."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    access = ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
    )
    MessageFactory(thread=thread)

    data = {
        "flag": "starred",
        "value": True,
        "thread_ids": [str(thread.id)],
        "mailbox_id": str(mailbox.id),
    }
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    access.refresh_from_db()
    assert access.starred_at is not None


@pytest.mark.parametrize(
    "flag,field",
    [
        ("trashed", "is_trashed"),
        ("archived", "is_archived"),
        ("spam", "is_spam"),
    ],
)
def test_api_flag_viewer_mailbox_with_editor_thread_access_forbidden(
    api_client, flag, field
):
    """A user with VIEWER MailboxAccess cannot mutate shared-state flags
    via message_ids even when the mailbox has EDITOR ThreadAccess on the thread.
    """
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])  # VIEWER mailbox role
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,  # Mailbox has edit on thread
    )
    message = MessageFactory(thread=thread)

    data = {"flag": flag, "value": True, "message_ids": [str(message.id)]}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 0

    message.refresh_from_db()
    assert getattr(message, field) is False

    # Elevating MailboxAccess to EDITOR lets the same request succeed.
    models.MailboxAccess.objects.filter(user=user, mailbox=mailbox).update(
        role=enums.MailboxRoleChoices.EDITOR
    )
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1


# --- Tests for Trashed Flag ---


def test_api_flag_mark_messages_trashed_success(api_client):
    """Test marking messages as trashed successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_trashed=False)
    msg2 = MessageFactory(thread=thread, is_trashed=True)  # Already trashed

    thread.refresh_from_db()
    thread.update_stats()
    assert thread.has_trashed is True

    message_ids = [str(msg1.id)]
    data = {"flag": "trashed", "value": True, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_trashed is True
    assert msg1.trashed_at is not None
    assert msg2.is_trashed is True

    thread.refresh_from_db()
    assert thread.has_trashed is True


def test_api_flag_mark_messages_untrashed_success(api_client):
    """Test marking messages as untrashed successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_trashed=True, trashed_at=timezone.now())
    msg2 = MessageFactory(thread=thread, is_trashed=False)  # Already untrashed

    thread.refresh_from_db()
    thread.update_stats()
    assert thread.has_trashed is True

    message_ids = [str(msg1.id)]
    data = {"flag": "trashed", "value": False, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_trashed is False
    assert msg1.trashed_at is None
    assert msg2.is_trashed is False

    thread.refresh_from_db()
    assert thread.has_trashed is False


# --- trashed_at belongs to the trashbin pair (is_trashed OR is_spam) ---
#
# The cutoff sweep ages an item by trashed_at, falling back to created_at when
# it is NULL. Clearing one half of the pair while the other is still set would
# leave the message in the bin with a NULL timestamp, so old mail would be
# permanently deleted on the next nightly run instead of getting its grace
# period. See core.services.trashbin and viewsets/flag.trashbin_timestamp.


def test_api_flag_unspam_keeps_trashed_at_while_still_trashed(api_client):
    """Un-spamming a message that is also trashed must keep trashed_at set."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    binned_at = timezone.now() - timedelta(days=3)
    msg = MessageFactory(
        thread=thread, is_trashed=True, is_spam=True, trashed_at=binned_at
    )

    data = {"flag": "spam", "value": False, "message_ids": [str(msg.id)]}
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    msg.refresh_from_db()
    assert msg.is_spam is False
    assert msg.is_trashed is True
    # Still in the bin, so the original entry time survives untouched.
    assert msg.trashed_at == binned_at


def test_api_flag_untrash_keeps_trashed_at_while_still_spam(api_client):
    """Un-trashing a message that is also spam must keep trashed_at set."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    binned_at = timezone.now() - timedelta(days=3)
    msg = MessageFactory(
        thread=thread, is_trashed=True, is_spam=True, trashed_at=binned_at
    )

    data = {"flag": "trashed", "value": False, "message_ids": [str(msg.id)]}
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    msg.refresh_from_db()
    assert msg.is_trashed is False
    assert msg.is_spam is True
    assert msg.trashed_at == binned_at


def test_api_flag_leaving_the_bin_entirely_clears_trashed_at(api_client):
    """Clearing the last remaining trashbin flag does clear trashed_at."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg = MessageFactory(thread=thread, is_spam=True, trashed_at=timezone.now())

    data = {"flag": "spam", "value": False, "message_ids": [str(msg.id)]}
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    msg.refresh_from_db()
    assert msg.is_spam is False
    assert msg.trashed_at is None


def test_api_flag_reflagging_does_not_restart_the_retention_clock(api_client):
    """Flagging the other half of the pair keeps the original bin entry time.

    Otherwise toggling spam on an already-trashed message would postpone its
    permanent deletion indefinitely.
    """
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    binned_at = timezone.now() - timedelta(days=20)
    msg = MessageFactory(thread=thread, is_trashed=True, trashed_at=binned_at)

    data = {"flag": "spam", "value": True, "message_ids": [str(msg.id)]}
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    msg.refresh_from_db()
    assert msg.is_spam is True
    assert msg.trashed_at == binned_at


def test_api_flag_entering_the_bin_stamps_trashed_at(api_client):
    """A message with no timestamp gets one when it first enters the bin."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg = MessageFactory(thread=thread)
    assert msg.trashed_at is None

    data = {"flag": "spam", "value": True, "message_ids": [str(msg.id)]}
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    msg.refresh_from_db()
    assert msg.is_spam is True
    assert msg.trashed_at is not None


# --- Tests for Archived Flag ---


def test_api_flag_mark_messages_archived_success(api_client):
    """Test marking messages as archived successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_archived=False)
    msg2 = MessageFactory(thread=thread, is_archived=True)  # Already archived

    thread.refresh_from_db()
    thread.update_stats()
    assert thread.has_archived is True

    message_ids = [str(msg1.id)]
    data = {"flag": "archived", "value": True, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_archived is True
    assert msg1.archived_at is not None
    assert msg2.is_archived is True

    thread.refresh_from_db()
    assert thread.has_archived is True


def test_api_flag_mark_messages_unarchived_success(api_client):
    """Test marking messages as unarchived successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_archived=True, archived_at=timezone.now())
    msg2 = MessageFactory(thread=thread, is_archived=False)  # Already unarchived

    thread.refresh_from_db()
    thread.update_stats()
    assert thread.has_archived is True

    message_ids = [str(msg1.id)]
    data = {"flag": "archived", "value": False, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_archived is False
    assert msg1.archived_at is None
    assert msg2.is_archived is False

    thread.refresh_from_db()
    assert thread.has_archived is False


# --- Tests for Spam Flag ---
def test_api_flag_mark_messages_spam_success(api_client):
    """Test marking messages as spam successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_spam=False)
    msg2 = MessageFactory(thread=thread, is_spam=True)  # Already spam

    thread.refresh_from_db()
    thread.update_stats()
    # To know if the thread is spam, we check the first message
    assert thread.is_spam is False

    message_ids = [str(msg1.id)]
    data = {"flag": "spam", "value": True, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_spam is True
    assert msg2.is_spam is True

    thread.refresh_from_db()
    assert thread.is_spam is True


def test_api_flag_mark_messages_not_spam_success(api_client):
    """Test marking messages as not spam successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    msg1 = MessageFactory(thread=thread, is_spam=True)
    msg2 = MessageFactory(thread=thread, is_spam=False)  # Already not spam

    thread.refresh_from_db()
    thread.update_stats()
    assert thread.is_spam is True

    message_ids = [str(msg1.id)]
    data = {"flag": "spam", "value": False, "message_ids": message_ids}
    response = api_client.post(API_URL, data=data, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated_threads"] == 1

    msg1.refresh_from_db()
    msg2.refresh_from_db()
    assert msg1.is_spam is False
    assert msg2.is_spam is False

    thread.refresh_from_db()
    assert thread.is_spam is False


# --- Tests for Draft Children Cascade ---


@pytest.mark.parametrize(
    "flag,field,date_field",
    [
        ("trashed", "is_trashed", "trashed_at"),
        ("archived", "is_archived", "archived_at"),
        ("spam", "is_spam", None),
    ],
)
def test_api_flag_cascade_to_draft_children(api_client, flag, field, date_field):
    """Flagging a message by message_id cascades to its draft children."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    parent_msg = MessageFactory(thread=thread)
    draft_child = MessageFactory(thread=thread, parent=parent_msg, is_draft=True)

    data = {
        "flag": flag,
        "value": True,
        "message_ids": [str(parent_msg.id)],
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    parent_msg.refresh_from_db()
    draft_child.refresh_from_db()
    assert getattr(parent_msg, field) is True
    assert getattr(draft_child, field) is True
    if date_field:
        assert getattr(draft_child, date_field) is not None


@pytest.mark.parametrize(
    "flag,field,date_field",
    [
        ("trashed", "is_trashed", "trashed_at"),
        ("archived", "is_archived", "archived_at"),
        ("spam", "is_spam", None),
    ],
)
def test_api_flag_cascade_unflag_to_draft_children(api_client, flag, field, date_field):
    """Unflagging a message by message_id cascades to its draft children."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    kwargs = {field: True}
    if date_field:
        kwargs[date_field] = timezone.now()
    parent_msg = MessageFactory(thread=thread, **kwargs)
    draft_child = MessageFactory(
        thread=thread, parent=parent_msg, is_draft=True, **kwargs
    )

    data = {
        "flag": flag,
        "value": False,
        "message_ids": [str(parent_msg.id)],
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    parent_msg.refresh_from_db()
    draft_child.refresh_from_db()
    assert getattr(parent_msg, field) is False
    assert getattr(draft_child, field) is False
    if date_field:
        assert getattr(draft_child, date_field) is None


def test_api_flag_cascade_does_not_affect_non_draft_children(api_client):
    """Trashing a message does NOT cascade to non-draft children."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_admin=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
    )
    parent_msg = MessageFactory(thread=thread)
    non_draft_child = MessageFactory(thread=thread, parent=parent_msg, is_draft=False)

    data = {
        "flag": "trashed",
        "value": True,
        "message_ids": [str(parent_msg.id)],
    }
    response = api_client.post(API_URL, data=data, format="json")
    assert response.status_code == status.HTTP_200_OK

    parent_msg.refresh_from_db()
    non_draft_child.refresh_from_db()
    assert parent_msg.is_trashed is True
    assert non_draft_child.is_trashed is False
