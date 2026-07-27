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
    permission boundary. The thread stats are computed, as every write path
    does, so a spam message puts its thread in the Spam folder.
    """
    if thread_role is None:
        thread_role = models.ThreadAccessRoleChoices.EDITOR
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread, role=thread_role)
    message = factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"x" * 200, **flags
    )
    thread.update_stats()
    return message


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


def test_empty_spam_keeps_the_unflagged_messages_of_a_conversation(settings):
    """Only flagged messages are purged: the user's own reply here survives,
    and its thread leaves the Spam folder for the folders of what remains."""
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
    assert response.json()["deleted_count"] == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk__in=[reply.pk, live.pk]).count() == 2
    thread = models.Thread.objects.get(pk=spam.thread_id)
    assert thread.is_spam is False
    assert thread.has_sender is True


def test_empty_spam_deletes_a_thread_left_without_messages(settings):
    """A thread where every message is spam goes with its last message."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True)
    factories.MessageFactory(thread=spam.thread, sender=contact, is_spam=True)

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 2
    assert not models.Thread.objects.filter(pk=spam.thread_id).exists()


def test_targeted_spam_delete_keeps_the_unflagged_messages(settings):
    """Targeting a spam thread does not widen the selection past its spam."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True)
    clean = factories.MessageFactory(thread=spam.thread, sender=contact)

    assert trashbin.empty_trashbin(mailbox, "spam", user, message_ids=[clean.pk]) == 0
    assert (
        trashbin.empty_trashbin(mailbox, "spam", user, thread_ids=[spam.thread_id]) == 1
    )
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=clean.pk).exists()


def test_empty_spam_skips_a_thread_not_in_the_spam_folder(settings):
    """A spam message whose thread stats missed it does not get it purged.

    The Spam folder lists ``Thread.is_spam``: a thread left out of it by a
    failed stats update was never shown there, so emptying the folder keeps it.
    """
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True)
    models.Thread.objects.filter(pk=spam.thread_id).update(is_spam=False)

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 0
    assert models.Message.objects.filter(pk=spam.pk).exists()


def test_empty_spam_keeps_a_fully_trashed_thread_for_the_trash_folder(settings):
    """A fully trashed spam thread is only listed in Trash: only emptying Trash
    deletes it, even when it is targeted with the spam scope."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True, is_trashed=True)

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 0
    assert (
        trashbin.empty_trashbin(mailbox, "spam", user, thread_ids=[spam.thread_id]) == 0
    )
    assert models.Message.objects.filter(pk=spam.pk).exists()

    assert trashbin.empty_trashbin(mailbox, "trashed", user) == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()


def test_empty_spam_leaves_trashed_unflagged_messages_to_the_trash(settings):
    """A partly trashed spam thread is listed in Spam, but what was only trashed
    stays in the Trash folder."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True)
    trashed = factories.MessageFactory(
        thread=spam.thread, sender=contact, is_trashed=True
    )
    spam.thread.update_stats()

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=trashed.pk).exists()


def test_empty_spam_leaves_a_spam_message_trashed_on_its_own(settings):
    """It sits in the Trash folder, its thread in the inbox: only emptying the
    Trash deletes it, and the rest of the thread stays."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True, is_trashed=True)
    clean = factories.MessageFactory(thread=spam.thread, sender=contact)
    spam.thread.update_stats()
    assert spam.thread.is_spam is False

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 0
    assert models.Message.objects.filter(pk=spam.pk).exists()

    assert trashbin.empty_trashbin(mailbox, "trashed", user) == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=clean.pk).exists()


def test_empty_spam_leaves_a_trashed_spam_message_of_a_spam_thread(settings):
    """Another spam message keeps the thread in Spam: emptying it takes that
    one, and the one in the Trash stays restorable."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spam = _message(mailbox, contact, is_spam=True)
    trashed_spam = factories.MessageFactory(
        thread=spam.thread, sender=contact, is_spam=True, is_trashed=True
    )
    spam.thread.update_stats()
    assert spam.thread.is_spam is True

    assert trashbin.empty_trashbin(mailbox, "spam", user) == 1
    assert not models.Message.objects.filter(pk=spam.pk).exists()
    assert models.Message.objects.filter(pk=trashed_spam.pk).exists()


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


def test_empty_spam_batches_reach_every_spam_of_a_conversation(settings):
    """A batch takes all the spam of a thread: its first stats update would
    otherwise drop the rest from a selection requiring ``Thread.is_spam``."""
    settings.TRASHBIN_ALLOW_EMPTY = "admin"
    mailbox = factories.MailboxFactory()
    user = _member(mailbox, models.MailboxRoleChoices.ADMIN)
    contact = factories.ContactFactory(mailbox=mailbox)
    spams = [_message(mailbox, contact, is_spam=True) for _ in range(2)]
    spams += [
        factories.MessageFactory(thread=spam.thread, sender=contact, is_spam=True)
        for spam in spams
    ]
    follow_ups = [
        factories.MessageFactory(thread=spam.thread, sender=contact)
        for spam in spams[:2]
    ]

    deleted = trashbin.empty_trashbin(mailbox, "spam", user, batch_size=1)

    assert deleted == 4
    assert not models.Message.objects.filter(pk__in=[m.pk for m in spams]).exists()
    assert models.Message.objects.filter(pk__in=[m.pk for m in follow_ups]).count() == 2


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
