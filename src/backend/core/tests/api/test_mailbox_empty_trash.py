"""Tests for the "empty trashbin" mailbox endpoint.

The trashbin is the union of trashed and spam messages (``is_trashed OR
is_spam``); this endpoint permanently deletes one folder of it, gated by the
``TRASHBIN_ALLOW_EMPTY`` policy.
"""
# pylint: disable=redefined-outer-name

from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from core import enums, factories, models
from core.api.viewsets import mailbox as mailbox_viewset
from core.enums import TrashbinAllowEmpty
from core.services import trashbin

pytestmark = pytest.mark.django_db


def _url(mailbox):
    return reverse("mailboxes-empty-trash", kwargs={"pk": str(mailbox.id)})


def _member(mailbox, role):
    user = factories.UserFactory()
    factories.MailboxAccessFactory(mailbox=mailbox, user=user, role=role)
    return user


def _message(mailbox, contact, thread_role=None, **flags):
    """Create a thread accessible to the mailbox with one flagged message.

    Defaults to EDITOR thread-access so ``editable_by`` (which empty_trashbin
    scopes to) includes it; pass ``thread_role=VIEWER`` to exercise the
    permission boundary.
    """
    if thread_role is None:
        thread_role = models.ThreadAccessRoleChoices.EDITOR
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread, role=thread_role)
    return factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"x" * 200, **flags
    )


def test_requires_authentication():
    """Anonymous users cannot empty the trashbin."""
    mailbox = factories.MailboxFactory()
    response = APIClient().post(_url(mailbox), {"scope": "trashed"})
    assert response.status_code == 401


def test_requires_mailbox_access():
    """A non-member gets 404 (queryset-filtered), not a permission leak."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    client = APIClient()
    client.force_login(user)
    assert client.post(_url(mailbox), {"scope": "trashed"}).status_code == 404


@pytest.mark.parametrize(
    "policy,role,allowed",
    [
        # admin policy (default): only ADMIN may empty.
        ("admin", models.MailboxRoleChoices.VIEWER, False),
        ("admin", models.MailboxRoleChoices.EDITOR, False),
        ("admin", models.MailboxRoleChoices.ADMIN, True),
        # editor policy: EDITOR and above.
        ("editor", models.MailboxRoleChoices.VIEWER, False),
        ("editor", models.MailboxRoleChoices.EDITOR, True),
        ("editor", models.MailboxRoleChoices.SENDER, True),
        ("editor", models.MailboxRoleChoices.ADMIN, True),
        # never policy: nobody, not even an admin.
        ("never", models.MailboxRoleChoices.ADMIN, False),
    ],
)
def test_permission_matrix(settings, policy, role, allowed):
    """The TRASHBIN_ALLOW_EMPTY policy gates who may empty, by role."""
    settings.TRASHBIN_ALLOW_EMPTY = policy
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, role)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == (200 if allowed else 403)


def test_every_enum_member_is_an_accepted_policy(settings):
    """The enum is the single source of truth for what get_abilities accepts."""
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)

    for policy in TrashbinAllowEmpty:
        settings.TRASHBIN_ALLOW_EMPTY = policy
        abilities = mailbox.get_abilities(user)
        assert abilities[enums.MailboxAbilities.CAN_EMPTY_TRASH] is (
            policy != TrashbinAllowEmpty.NEVER
        )


def test_empty_trashed_deletes_only_trashed(settings):
    """Emptying scope=trashed removes trashed messages, leaving spam/live."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    trashed = _message(mailbox, contact, is_trashed=True)
    spam = _message(mailbox, contact, is_spam=True)
    live = _message(mailbox, contact)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "deleted_count": 1}
    assert not models.Message.objects.filter(pk=trashed.pk).exists()
    assert models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=live.pk).exists()


def test_empty_spam_deletes_only_spam(settings):
    """Emptying scope=spam removes spam messages, leaving trashed/live."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    trashed = _message(mailbox, contact, is_trashed=True)
    spam = _message(mailbox, contact, is_spam=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "spam"})

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=trashed.pk).exists()


def test_empty_spam_deletes_whole_conversations(settings):
    """Spam is a conversation property: emptying it removes entire threads.

    Even a message not flagged itself (the user's own reply here) goes with
    its conversation, so nothing is left to resurface in another folder.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    spam = _message(mailbox, contact, is_spam=True)
    reply = factories.MessageFactory(thread=spam.thread, sender=contact, is_sender=True)
    live = _message(mailbox, contact)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "spam"})

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 2
    assert not models.Message.objects.filter(pk__in=[spam.pk, reply.pk]).exists()
    assert not models.Thread.objects.filter(pk=spam.thread_id).exists()
    assert models.Message.objects.filter(pk=live.pk).exists()


# --- Targeted deletion: same endpoint, same gate, narrower blast radius ---


def test_message_ids_deletes_only_those_messages(settings):
    """message_ids narrows the deletion; the rest of the folder survives."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    target = _message(mailbox, contact, is_trashed=True)
    other = _message(mailbox, contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(
        _url(mailbox),
        {"scope": "trashed", "message_ids": [str(target.id)]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(pk=target.pk).exists()
    assert models.Message.objects.filter(pk=other.pk).exists()


def test_thread_ids_deletes_only_that_thread(settings):
    """thread_ids narrows the deletion to one conversation."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    target = _message(mailbox, contact, is_trashed=True)
    other = _message(mailbox, contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(
        _url(mailbox),
        {"scope": "trashed", "thread_ids": [str(target.thread_id)]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(pk=target.pk).exists()
    assert models.Message.objects.filter(pk=other.pk).exists()


def test_targeting_still_respects_scope(settings):
    """An explicitly targeted message outside the scope is not deleted.

    Targeting must narrow the selection, never widen it past ``scope`` — asking
    to delete a live message under scope=trashed is a no-op, not a hard delete.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    live = _message(mailbox, contact)

    client = APIClient()
    client.force_login(user)
    response = client.post(
        _url(mailbox),
        {"scope": "trashed", "message_ids": [str(live.id)]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert models.Message.objects.filter(pk=live.pk).exists()


def test_empty_target_lists_still_empty_the_whole_folder(settings):
    """Explicit empty lists mean "everything", matching the omitted default.

    The opposite of bulk-delete, where no target is a 400 — here it is the
    primary use case.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    _message(mailbox, contact, is_trashed=True)
    _message(mailbox, contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(
        _url(mailbox),
        {"scope": "trashed", "thread_ids": [], "message_ids": []},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "trashed"},
        {"scope": "trashed", "message_ids": "__target__"},
    ],
    ids=["whole-folder", "single-message"],
)
def test_single_message_needs_the_same_permission_as_the_folder(settings, payload):
    """Deleting one message is gated exactly like emptying the folder.

    This is the whole reason targeted deletion lives on this endpoint: under
    TRASHBIN_ALLOW_EMPTY=never neither form is permitted. Routing per-message
    deletion through thread bulk-delete instead would have made the second case
    succeed, silently defeating the policy.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "never"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    target = _message(mailbox, contact, is_trashed=True)

    if payload.get("message_ids") == "__target__":
        payload = {**payload, "message_ids": [str(target.id)]}

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), payload, format="json")

    assert response.status_code == 403
    assert models.Message.objects.filter(pk=target.pk).exists()


def test_targeted_delete_cannot_reach_another_mailbox(settings):
    """Naming a message in someone else's mailbox deletes nothing.

    The accessible-thread scoping still applies on top of the explicit target.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)

    other_mailbox = factories.MailboxFactory()
    other_contact = factories.ContactFactory(mailbox=other_mailbox)
    victim = _message(other_mailbox, other_contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(
        _url(mailbox),
        {"scope": "trashed", "message_ids": [str(victim.id)]},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert models.Message.objects.filter(pk=victim.pk).exists()


def test_invalid_scope_rejected(settings):
    """An unknown scope is a 400, not a silent no-op."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "draft"})
    assert response.status_code == 400


def test_viewer_on_thread_cannot_hard_delete(settings):
    """A thread the mailbox only VIEWs is not emptied, even by a mailbox admin.

    Matches bulk_delete: hard-deleting shared-thread messages requires EDITOR
    thread-access, not just the mailbox-level empty_trash ability.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)

    viewer_msg = _message(
        mailbox,
        contact,
        thread_role=models.ThreadAccessRoleChoices.VIEWER,
        is_trashed=True,
    )

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert models.Message.objects.filter(pk=viewer_msg.pk).exists()


def test_only_targets_own_mailbox(settings):
    """A message in another mailbox's threads is untouched."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)

    other_mailbox = factories.MailboxFactory()
    other_contact = factories.ContactFactory(mailbox=other_mailbox)
    other_trashed = _message(other_mailbox, other_contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert models.Message.objects.filter(pk=other_trashed.pk).exists()


# --- Large folders: batched, and handed to a background task ---


def test_empty_trashbin_deletes_across_batches(settings):
    """Batching by thread never leaves anything behind."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    trashed = [_message(mailbox, contact, is_trashed=True) for _ in range(3)]
    live = _message(mailbox, contact)

    deleted = trashbin.empty_trashbin(mailbox, "trashed", user, batch_size=1)

    assert deleted == 3
    assert not models.Message.objects.filter(pk__in=[m.pk for m in trashed]).exists()
    assert models.Message.objects.filter(pk=live.pk).exists()


def test_empty_spam_batches_keep_conversations_whole(settings):
    """A spam batch takes a conversation whole, its unflagged messages included."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spams = [_message(mailbox, contact, is_spam=True) for _ in range(2)]
    follow_ups = [
        factories.MessageFactory(thread=spam.thread, sender=contact) for spam in spams
    ]

    deleted = trashbin.empty_trashbin(mailbox, "spam", user, batch_size=1)

    assert deleted == 4
    assert not models.Message.objects.filter(
        pk__in=[m.pk for m in spams + follow_ups]
    ).exists()


def test_small_folder_is_emptied_within_the_request(settings):
    """Up to the limit, the deletion happens before the response."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    _message(mailbox, contact, is_trashed=True)

    client = APIClient()
    client.force_login(user)
    with patch.object(trashbin.empty_trashbin_task, "apply_async") as dispatch:
        response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "deleted_count": 1}
    dispatch.assert_not_called()


def test_large_folder_is_emptied_in_a_background_task(settings, monkeypatch):
    """Beyond the limit, a task does the deletion and its owner can poll it."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    monkeypatch.setattr(mailbox_viewset, "EMPTY_TRASHBIN_SYNC_LIMIT", 1)
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    trashed = [_message(mailbox, contact, is_trashed=True) for _ in range(2)]

    client = APIClient()
    client.force_login(user)
    response = client.post(_url(mailbox), {"scope": "trashed"})

    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert cache.get(f"task_owner:{task_id}") == str(user.id)
    # Tasks run eagerly in tests: the deletion has already happened.
    assert not models.Message.objects.filter(pk__in=[m.pk for m in trashed]).exists()


def test_background_task_reports_the_deleted_count(settings):
    """The task result carries the count the synchronous response would."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    _message(mailbox, contact, is_spam=True)

    result = trashbin.empty_trashbin_task(str(mailbox.id), "spam", str(user.id))

    assert result == {"deleted_count": 1}
