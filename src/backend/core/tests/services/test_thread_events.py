"""Test the ``core.services.thread_events`` service layer.

These tests previously lived in ``core/tests/test_signals.py`` when the
behaviour they cover was implemented via Django signals. The signals
are gone (see ``core/services/thread_events.py``); the assertions stay
the same — only the trigger changes from ``ThreadEventFactory(...)`` /
``access.delete()`` to an explicit service call.
"""
# pylint: disable=too-many-lines,missing-function-docstring

from unittest.mock import patch

from django.utils import timezone

import pytest

from core import enums, factories, models
from core.services import thread_events as thread_events_service

pytestmark = pytest.mark.django_db


def _setup_thread_with_mentioned_user():
    """Create a thread with a user who has access and can be mentioned.

    Returns ``(author, mentioned_user, thread, mailbox)``. The access
    chain ``mentioned_user → MailboxAccess → mailbox → ThreadAccess →
    thread`` is what the MENTION validation walks.
    """
    author = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(mailbox=mailbox, user=author)
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
    mentioned_user = factories.UserFactory()
    factories.MailboxAccessFactory(mailbox=mailbox, user=mentioned_user)
    return author, mentioned_user, thread, mailbox


def _setup_thread_with_assignable_user():
    """Create a thread plus a user who has full edit rights on it.

    Both author and target_user get ADMIN MailboxAccess and the shared
    ThreadAccess is EDITOR, so the assignment rule (full edit rights on
    the assignee) is satisfied deterministically.
    """
    author = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=author, role=enums.MailboxRoleChoices.ADMIN
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        thread=thread,
        mailbox=mailbox,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    target_user = factories.UserFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=target_user, role=enums.MailboxRoleChoices.ADMIN
    )
    return author, target_user, thread, mailbox


# ---------------------------------------------------------------------------
# sync_im_mentions
# ---------------------------------------------------------------------------


class TestSyncImMentions:
    """``thread_events_service.sync_im_mentions`` — IM MENTION reconciliation."""

    def test_creates_user_event_for_valid_mention(self):
        """A ThreadEvent IM with a valid mention creates one UserEvent MENTION."""
        author, mentioned_user, thread, _ = _setup_thread_with_mentioned_user()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Hello @John",
                "mentions": [{"id": str(mentioned_user.id), "name": "John"}],
            },
        )

        thread_events_service.sync_im_mentions(thread_event=event)

        user_events = models.UserEvent.objects.filter(
            thread_event=event, user=mentioned_user, type="mention"
        )
        assert user_events.count() == 1
        user_event = user_events.first()
        assert user_event.read_at is None
        assert user_event.thread == thread

    def test_deduplicates_mentions_within_same_event(self):
        author, mentioned_user, thread, _ = _setup_thread_with_mentioned_user()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Hello @John and @John again",
                "mentions": [
                    {"id": str(mentioned_user.id), "name": "John"},
                    {"id": str(mentioned_user.id), "name": "John"},
                ],
            },
        )

        thread_events_service.sync_im_mentions(thread_event=event)

        assert (
            models.UserEvent.objects.filter(
                thread_event=event, user=mentioned_user
            ).count()
            == 1
        )

    def test_multiple_events_create_separate_user_events(self):
        author, mentioned_user, thread, _ = _setup_thread_with_mentioned_user()
        mention_data = {
            "content": "Hello @John",
            "mentions": [{"id": str(mentioned_user.id), "name": "John"}],
        }
        event1 = factories.ThreadEventFactory(
            thread=thread, author=author, data=mention_data
        )
        event2 = factories.ThreadEventFactory(
            thread=thread, author=author, data=mention_data
        )

        thread_events_service.sync_im_mentions(thread_event=event1)
        thread_events_service.sync_im_mentions(thread_event=event2)

        user_events = models.UserEvent.objects.filter(
            user=mentioned_user, thread=thread, type="mention"
        )
        assert user_events.count() == 2
        assert set(user_events.values_list("thread_event_id", flat=True)) == {
            event1.id,
            event2.id,
        }

    def test_skips_mention_without_thread_access(self):
        author, _, thread, _ = _setup_thread_with_mentioned_user()
        no_access_user = factories.UserFactory()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Hello @Ghost",
                "mentions": [{"id": str(no_access_user.id), "name": "Ghost"}],
            },
        )

        with patch("core.services.thread_events.logger") as mock_logger:
            thread_events_service.sync_im_mentions(thread_event=event)

        assert models.UserEvent.objects.filter(user=no_access_user).count() == 0
        mock_logger.warning.assert_called()
        assert str(no_access_user.id) in str(mock_logger.warning.call_args)

    def test_skips_invalid_user_id(self):
        author, _, thread, _ = _setup_thread_with_mentioned_user()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Hello @Ghost",
                "mentions": [
                    {"id": "00000000-0000-0000-0000-000000000000", "name": "Ghost"}
                ],
            },
        )
        initial_count = models.UserEvent.objects.count()

        with patch("core.services.thread_events.logger") as mock_logger:
            thread_events_service.sync_im_mentions(thread_event=event)

        assert models.UserEvent.objects.count() == initial_count
        mock_logger.warning.assert_called()

    def test_ignores_non_im_events(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type="assign",
            data={"assignees": [{"id": str(target_user.id), "name": "Target"}]},
        )
        initial_count = models.UserEvent.objects.count()

        thread_events_service.sync_im_mentions(thread_event=event)

        assert models.UserEvent.objects.count() == initial_count

    def test_ignores_im_without_mentions(self):
        author, _, thread, _ = _setup_thread_with_mentioned_user()
        event = factories.ThreadEventFactory(
            thread=thread, author=author, data={"content": "Hello everyone"}
        )
        initial_count = models.UserEvent.objects.count()

        thread_events_service.sync_im_mentions(thread_event=event)

        assert models.UserEvent.objects.count() == initial_count

    def test_ignores_im_with_empty_mentions(self):
        author, _, thread, _ = _setup_thread_with_mentioned_user()
        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={"content": "Hello everyone", "mentions": []},
        )
        initial_count = models.UserEvent.objects.count()

        thread_events_service.sync_im_mentions(thread_event=event)

        assert models.UserEvent.objects.count() == initial_count


# ---------------------------------------------------------------------------
# assign_users
# ---------------------------------------------------------------------------


class TestAssignUsers:
    """``thread_events_service.assign_users`` — ASSIGN flow."""

    def test_assign_creates_thread_event_and_user_event(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()

        event = thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        assert event is not None
        assert event.type == enums.ThreadEventTypeChoices.ASSIGN
        user_events = models.UserEvent.objects.filter(
            thread_event=event,
            user=target_user,
            type=enums.UserEventTypeChoices.ASSIGN,
        )
        assert user_events.count() == 1
        assert user_events.first().read_at is None
        assert user_events.first().thread == thread

    def test_assign_with_two_valid_assignees_creates_two_user_events(self):
        author, target_user, thread, mailbox = _setup_thread_with_assignable_user()
        target_user2 = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=target_user2, role=enums.MailboxRoleChoices.ADMIN
        )

        event = thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[
                {"id": str(target_user.id), "name": "Target1"},
                {"id": str(target_user2.id), "name": "Target2"},
            ],
        )

        assert event is not None
        assert (
            models.UserEvent.objects.filter(
                thread_event=event,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 2
        )

    def test_assign_idempotent_returns_none_when_all_already_assigned(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()
        thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        second = thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )
        assert second is None
        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                user=target_user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 1
        )

    def test_assign_raises_when_assignee_lacks_edit_rights(self):
        author, _, thread, mailbox = _setup_thread_with_assignable_user()
        viewer_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=viewer_user, role=enums.MailboxRoleChoices.VIEWER
        )

        with pytest.raises(ValueError, match="editor access"):
            thread_events_service.assign_users(
                thread=thread,
                author=author,
                assignees_data=[{"id": str(viewer_user.id), "name": "Viewer"}],
            )

    def test_assign_raises_when_assignee_has_no_thread_access(self):
        author, _, thread, _ = _setup_thread_with_assignable_user()
        no_access_user = factories.UserFactory()

        with pytest.raises(ValueError, match="editor access"):
            thread_events_service.assign_users(
                thread=thread,
                author=author,
                assignees_data=[{"id": str(no_access_user.id), "name": "NoAccess"}],
            )


# ---------------------------------------------------------------------------
# unassign_users
# ---------------------------------------------------------------------------


class TestUnassignUsers:
    """``thread_events_service.unassign_users`` — UNASSIGN flow."""

    def test_unassign_removes_existing_assign_user_event(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()
        thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )
        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                user=target_user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 1
        )

        # The undo window absorbs this UNASSIGN if the same author requests
        # it within 120s. Use a different author to exercise the regular
        # unassign path.
        other_author = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=thread.accesses.first().mailbox,
            user=other_author,
            role=enums.MailboxRoleChoices.ADMIN,
        )

        event = thread_events_service.unassign_users(
            thread=thread,
            author=other_author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        assert event is not None
        assert event.type == enums.ThreadEventTypeChoices.UNASSIGN
        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                user=target_user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )

    def test_unassign_returns_none_when_no_active_assign(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()

        event = thread_events_service.unassign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        assert event is None
        assert not models.ThreadEvent.objects.filter(
            thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
        ).exists()

    def test_undo_window_absorbs_unassign_by_same_author(self):
        author, target_user, thread, _ = _setup_thread_with_assignable_user()
        assign_event = thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        result = thread_events_service.unassign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(target_user.id), "name": "Target"}],
        )

        # Original ASSIGN deleted, no UNASSIGN ThreadEvent emitted, no
        # surviving UserEvent ASSIGN.
        assert result is None
        assert not models.ThreadEvent.objects.filter(id=assign_event.id).exists()
        assert not models.ThreadEvent.objects.filter(
            thread=thread, type=enums.ThreadEventTypeChoices.UNASSIGN
        ).exists()
        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                user=target_user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )


# ---------------------------------------------------------------------------
# revoke_thread_access / downgrade_thread_access
# ---------------------------------------------------------------------------


class TestThreadAccessCleanup:
    """Cleanup of ASSIGN / MENTION on ThreadAccess delete or downgrade."""

    def _setup_assigned_user(self):
        """Set up an author, an assignee, and assign the user to the thread."""
        author = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=author, role=enums.MailboxRoleChoices.ADMIN
        )
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(assignee.id), "name": "Assignee"}],
        )
        assert models.UserEvent.objects.filter(
            user=assignee,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()
        return author, assignee, thread, mailbox

    def _assert_auto_unassigned(self, thread, assignee):
        assert not models.UserEvent.objects.filter(
            user=assignee,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()
        system_unassign = models.ThreadEvent.objects.filter(
            thread=thread,
            type=enums.ThreadEventTypeChoices.UNASSIGN,
            author__isnull=True,
        ).last()
        assert system_unassign is not None
        assignee_ids = [a["id"] for a in system_unassign.data["assignees"]]
        assert str(assignee.id) in assignee_ids

    def test_revoke_thread_access_unassigns_users_of_mailbox(self):
        _, assignee, thread, mailbox = self._setup_assigned_user()
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        # Caller deletes first; the service then runs cleanup using the
        # in-memory instance.
        thread_access.delete()

        thread_events_service.revoke_thread_access(thread_access=thread_access)

        self._assert_auto_unassigned(thread, assignee)

    def test_downgrade_thread_access_unassigns_users_of_mailbox(self):
        _, assignee, thread, mailbox = self._setup_assigned_user()
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        # Caller is responsible for actually moving the role; the service
        # only runs cleanup against the post-downgrade state.
        thread_access.role = enums.ThreadAccessRoleChoices.VIEWER
        thread_access.save()

        thread_events_service.downgrade_thread_access(thread_access=thread_access)

        self._assert_auto_unassigned(thread, assignee)

    def test_revoke_keeps_assignment_when_user_editor_via_other_mailbox(self):
        _, assignee, thread, mailbox = self._setup_assigned_user()
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=assignee,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=other_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        thread_access.delete()

        thread_events_service.revoke_thread_access(thread_access=thread_access)

        assert models.UserEvent.objects.filter(
            user=assignee,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()
        assert not models.ThreadEvent.objects.filter(
            thread=thread,
            type=enums.ThreadEventTypeChoices.UNASSIGN,
            author__isnull=True,
        ).exists()


class TestMailboxAccessCleanup:
    """Cleanup of ASSIGN on MailboxAccess delete or downgrade."""

    def _setup_assigned_user(self):
        author = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=author, role=enums.MailboxRoleChoices.ADMIN
        )
        assignee = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=assignee, role=enums.MailboxRoleChoices.ADMIN
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread_events_service.assign_users(
            thread=thread,
            author=author,
            assignees_data=[{"id": str(assignee.id), "name": "Assignee"}],
        )
        return author, assignee, thread, mailbox

    def test_revoke_mailbox_access_unassigns_user(self):
        _, assignee, thread, mailbox = self._setup_assigned_user()
        access = models.MailboxAccess.objects.get(user=assignee, mailbox=mailbox)
        access.delete()

        thread_events_service.revoke_mailbox_access(mailbox_access=access)

        assert not models.UserEvent.objects.filter(
            user=assignee,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()
        assert models.ThreadEvent.objects.filter(
            thread=thread,
            type=enums.ThreadEventTypeChoices.UNASSIGN,
            author__isnull=True,
        ).exists()

    def test_downgrade_mailbox_access_unassigns_user(self):
        _, assignee, thread, mailbox = self._setup_assigned_user()
        access = models.MailboxAccess.objects.get(user=assignee, mailbox=mailbox)
        access.role = enums.MailboxRoleChoices.VIEWER
        access.save()

        thread_events_service.downgrade_mailbox_access(mailbox_access=access)

        assert not models.UserEvent.objects.filter(
            user=assignee,
            thread=thread,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).exists()


# ---------------------------------------------------------------------------
# Mention cleanup on access change
# ---------------------------------------------------------------------------


class TestMentionCleanup:
    """Cleanup of MENTION rows when a user loses access to a thread."""

    def _setup_mentioned_user(self):
        author = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=author, role=enums.MailboxRoleChoices.ADMIN
        )
        mentioned_user = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=mentioned_user,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        mention_event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Hello @John",
                "mentions": [{"id": str(mentioned_user.id), "name": "John"}],
            },
        )
        thread_events_service.sync_im_mentions(thread_event=mention_event)
        assert models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()
        return author, mentioned_user, thread, mailbox, mention_event

    def _assert_mention_cleaned(self, thread, mentioned_user):
        assert not models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()
        # No system ThreadEvent is created for mention cleanup — the IM
        # event carrying the mention stays as historical source of truth.
        assert not models.ThreadEvent.objects.filter(
            thread=thread,
            author__isnull=True,
        ).exists()

    def test_revoke_thread_access_cleans_mentions(self):
        _, mentioned_user, thread, mailbox, _ = self._setup_mentioned_user()
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        thread_access.delete()

        thread_events_service.revoke_thread_access(thread_access=thread_access)

        self._assert_mention_cleaned(thread, mentioned_user)

    def test_revoke_mailbox_access_cleans_mentions(self):
        _, mentioned_user, thread, mailbox, _ = self._setup_mentioned_user()
        access = models.MailboxAccess.objects.get(user=mentioned_user, mailbox=mailbox)
        access.delete()

        thread_events_service.revoke_mailbox_access(mailbox_access=access)

        self._assert_mention_cleaned(thread, mentioned_user)

    def test_downgrade_thread_access_does_not_touch_mentions(self):
        _, mentioned_user, thread, mailbox, _ = self._setup_mentioned_user()
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        thread_access.role = enums.ThreadAccessRoleChoices.VIEWER
        thread_access.save()

        thread_events_service.downgrade_thread_access(thread_access=thread_access)

        # Downgrades do not invalidate mentions: the user can still read.
        assert models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()

    def test_downgrade_mailbox_access_does_not_touch_mentions(self):
        _, mentioned_user, thread, mailbox, _ = self._setup_mentioned_user()
        access = models.MailboxAccess.objects.get(user=mentioned_user, mailbox=mailbox)
        access.role = enums.MailboxRoleChoices.VIEWER
        access.save()

        thread_events_service.downgrade_mailbox_access(mailbox_access=access)

        assert models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()

    def test_revoke_keeps_mention_when_user_reaches_thread_via_other_mailbox(self):
        _, mentioned_user, thread, mailbox, _ = self._setup_mentioned_user()
        other_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=other_mailbox,
            user=mentioned_user,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=other_mailbox,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        access = models.MailboxAccess.objects.get(user=mentioned_user, mailbox=mailbox)
        access.delete()

        thread_events_service.revoke_mailbox_access(mailbox_access=access)

        assert models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()

    def test_revoke_removes_read_and_unread_mentions(self):
        author, mentioned_user, thread, mailbox, first_event = (
            self._setup_mentioned_user()
        )
        second_event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={
                "content": "Ping again @John",
                "mentions": [{"id": str(mentioned_user.id), "name": "John"}],
            },
        )
        thread_events_service.sync_im_mentions(thread_event=second_event)
        models.UserEvent.objects.filter(
            user=mentioned_user,
            thread_event=first_event,
            type=enums.UserEventTypeChoices.MENTION,
        ).update(read_at=timezone.now())
        assert (
            models.UserEvent.objects.filter(
                user=mentioned_user,
                thread=thread,
                type=enums.UserEventTypeChoices.MENTION,
            ).count()
            == 2
        )
        thread_access = models.ThreadAccess.objects.get(thread=thread, mailbox=mailbox)
        thread_access.delete()

        thread_events_service.revoke_thread_access(thread_access=thread_access)

        assert not models.UserEvent.objects.filter(
            user=mentioned_user,
            thread=thread,
            type=enums.UserEventTypeChoices.MENTION,
        ).exists()
