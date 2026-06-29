"""Tests for the threads bulk-delete endpoint (permanent message deletion)."""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db

BULK_DELETE_URL = reverse("threads-bulk-delete")


def _editable_thread(user, mailbox=None):
    """Create a thread the ``user`` can fully edit, returning (thread, mailbox).

    Full edit rights require an EDITOR ThreadAccess backed by a MailboxAccess
    whose role is in ``MAILBOX_ROLES_CAN_EDIT`` on the same mailbox.
    """
    mailbox = mailbox or factories.MailboxFactory()
    if not models.MailboxAccess.objects.filter(mailbox=mailbox, user=user).exists():
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return thread, mailbox


def test_api_bulk_delete_anonymous():
    """An anonymous user cannot delete anything."""
    thread = factories.ThreadFactory()
    response = APIClient().post(
        BULK_DELETE_URL,
        {"scope": "draft", "thread_ids": [str(thread.id)]},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_api_bulk_delete_missing_scope():
    """A request without a scope is rejected."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL, {"thread_ids": [str(thread.id)]}, format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_api_bulk_delete_invalid_scope():
    """An unknown scope is rejected."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "archived", "thread_ids": [str(thread.id)]},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_api_bulk_delete_trashed_scope_rejected():
    """The 'trashed' scope is intentionally not supported: deleting trashed
    messages is never exposed, so the request is rejected and nothing removed."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    trashed = factories.MessageFactory(thread=thread, is_trashed=True)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "trashed", "thread_ids": [str(thread.id)]},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert models.Message.objects.filter(id=trashed.id).exists()


def test_api_bulk_delete_no_targets():
    """A request without thread_ids nor message_ids is rejected."""
    user = factories.UserFactory()
    _editable_thread(user)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(BULK_DELETE_URL, {"scope": "draft"}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_api_bulk_delete_drafts_requires_edit_rights():
    """A viewer cannot delete drafts: nothing is removed."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.VIEWER
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
    )
    draft = factories.MessageFactory(thread=thread, is_draft=True)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "draft", "thread_ids": [str(thread.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["deleted_count"] == 0
    assert models.Message.objects.filter(id=draft.id).exists()


def test_api_bulk_delete_drafts_only_thread_is_removed():
    """Deleting the sole draft of a thread removes the thread too."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    draft = factories.MessageFactory(thread=thread, is_draft=True)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "draft", "thread_ids": [str(thread.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"success": True, "deleted_count": 1}
    assert not models.Message.objects.filter(id=draft.id).exists()
    assert not models.Thread.objects.filter(id=thread.id).exists()


def test_api_bulk_delete_drafts_keeps_real_messages_of_reply_draft():
    """Deleting a reply draft keeps the thread and its real messages."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    real_message = factories.MessageFactory(thread=thread, is_draft=False)
    draft = factories.MessageFactory(thread=thread, is_draft=True, parent=real_message)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "draft", "thread_ids": [str(thread.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(id=draft.id).exists()
    assert models.Message.objects.filter(id=real_message.id).exists()

    thread.refresh_from_db()
    assert thread.has_draft is False
    assert thread.has_messages is True


def test_api_bulk_delete_drafts_scoped_by_message_ids():
    """``message_ids`` restricts deletion to the targeted drafts."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    thread_a, _ = _editable_thread(user, mailbox=mailbox)
    thread_b, _ = _editable_thread(user, mailbox=mailbox)
    draft_a = factories.MessageFactory(thread=thread_a, is_draft=True)
    draft_b = factories.MessageFactory(thread=thread_b, is_draft=True)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "draft", "message_ids": [str(draft_a.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(id=draft_a.id).exists()
    assert models.Message.objects.filter(id=draft_b.id).exists()


def test_api_bulk_delete_drafts_ignores_non_draft_messages():
    """The ``draft`` scope never touches trashed or active messages."""
    user = factories.UserFactory()
    thread, _ = _editable_thread(user)
    draft = factories.MessageFactory(thread=thread, is_draft=True)
    trashed = factories.MessageFactory(thread=thread, is_trashed=True)
    active = factories.MessageFactory(thread=thread, is_draft=False)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        BULK_DELETE_URL,
        {"scope": "draft", "thread_ids": [str(thread.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(id=draft.id).exists()
    assert models.Message.objects.filter(id=trashed.id).exists()
    assert models.Message.objects.filter(id=active.id).exists()
