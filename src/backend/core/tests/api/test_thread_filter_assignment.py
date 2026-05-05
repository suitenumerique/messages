"""Tests for has_assigned_to_me and has_unassigned filters and stats on Thread API."""

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums, factories, models

pytestmark = pytest.mark.django_db


def setup_user_with_thread_access(role=enums.ThreadAccessRoleChoices.EDITOR):
    """Create a user with mailbox access and thread access."""
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=role,
    )
    return user, mailbox, thread


class TestThreadFilterAssignedToMe:
    """Test GET /api/v1.0/threads/?has_assigned_to_me=1 filter."""

    def test_filter_returns_threads_assigned_to_me(self, api_client):
        """Filter should return only threads with active ASSIGN UserEvent for current user."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create an active assignment on this thread for the current user
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        # Create another thread without assignment
        thread_no_assign = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_no_assign,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_assigned_to_me": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids
        assert str(thread_no_assign.id) not in thread_ids

    def test_filter_excludes_threads_assigned_to_me(self, api_client):
        """Filter with has_assigned_to_me=0 should return threads NOT assigned to current user."""
        user, mailbox, thread_assigned = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create an active assignment
        event = factories.ThreadEventFactory(thread=thread_assigned, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread_assigned,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        # Create another thread without assignment
        thread_no_assign = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_no_assign,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_assigned_to_me": "0"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread_no_assign.id) in thread_ids
        assert str(thread_assigned.id) not in thread_ids

    def test_unassigned_thread_not_shown(self, api_client):
        """After UNASSIGN, the UserEvent is deleted and the thread must disappear."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        factories.ThreadEventFactory(
            thread=thread,
            author=user,
            type=enums.ThreadEventTypeChoices.ASSIGN,
            data={"assignees": [{"id": str(user.id), "name": user.full_name or ""}]},
        )
        factories.ThreadEventFactory(
            thread=thread,
            author=user,
            type=enums.ThreadEventTypeChoices.UNASSIGN,
            data={"assignees": [{"id": str(user.id), "name": user.full_name or ""}]},
        )

        assert (
            models.UserEvent.objects.filter(
                thread=thread,
                user=user,
                type=enums.UserEventTypeChoices.ASSIGN,
            ).count()
            == 0
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_assigned_to_me": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0


class TestThreadFilterUnassigned:
    """Test GET /api/v1.0/threads/?has_unassigned=1 filter."""

    def test_filter_returns_unassigned_threads(self, api_client):
        """Filter should return threads with no active ASSIGN UserEvent from any user."""
        user, mailbox, thread_unassigned = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create an assigned thread (assigned to another user)
        thread_assigned = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_assigned,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread_assigned, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread_assigned,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unassigned": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread_unassigned.id) in thread_ids
        assert str(thread_assigned.id) not in thread_ids

    def test_filter_returns_assigned_threads(self, api_client):
        """Filter with has_unassigned=0 should return threads WITH at least one active assignment."""
        user, mailbox, thread_unassigned = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create an assigned thread
        thread_assigned = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_assigned,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread_assigned, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread_assigned,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unassigned": "0"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread_assigned.id) in thread_ids
        assert str(thread_unassigned.id) not in thread_ids


class TestThreadStatsAssignment:
    """Test GET /api/v1.0/threads/stats/ for assignment stats."""

    def test_stats_has_assigned_to_me(self, api_client):
        """Stats should return correct has_assigned_to_me count."""
        user, mailbox, thread1 = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread 1: assigned to me
        event1 = factories.ThreadEventFactory(thread=thread1, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread1,
            thread_event=event1,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        # Thread 2: not assigned
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_assigned_to_me",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_assigned_to_me"] == 1

    def test_stats_has_unassigned(self, api_client):
        """Stats should return correct has_unassigned count."""
        user, mailbox, _thread_unassigned = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread 2: assigned to another user
        thread_assigned = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_assigned,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread_assigned, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread_assigned,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_unassigned",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_unassigned"] == 1

    def test_stats_all_with_assigned_to_me_filter(self, api_client):
        """Stats with all field and has_assigned_to_me filter should return correct total."""
        user, mailbox, thread1 = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Assign thread1 to me
        event1 = factories.ThreadEventFactory(thread=thread1, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread1,
            thread_event=event1,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        # Create thread2 also assigned to me
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        event2 = factories.ThreadEventFactory(thread=thread2, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread2,
            thread_event=event2,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        # Thread3 not assigned
        thread3 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread3,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "all",
                "has_assigned_to_me": "1",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["all"] == 2


class TestThreadAssignedUsersField:
    """Test the ``assigned_users`` field exposed on GET /api/v1.0/threads/."""

    def test_empty_when_no_active_assignment(self, api_client):
        """Threads with no active ASSIGN UserEvent expose an empty list."""
        user, mailbox, _ = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-list"), {"mailbox_id": str(mailbox.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"][0]["assigned_users"] == []

    def test_exposes_all_current_assignees(self, api_client):
        """All users with an active ASSIGN UserEvent appear in ``assigned_users``."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        assignee_a = factories.UserFactory(full_name="Alice Martin")
        assignee_b = factories.UserFactory(full_name="Bob Durand")
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=assignee_a,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )
        factories.UserEventFactory(
            user=assignee_b,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )

        response = api_client.get(
            reverse("threads-list"), {"mailbox_id": str(mailbox.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        assigned = response.data["results"][0]["assigned_users"]
        assert {u["id"] for u in assigned} == {str(assignee_a.id), str(assignee_b.id)}
        assert {u["name"] for u in assigned} == {"Alice Martin", "Bob Durand"}

    def test_drops_unassigned_users(self, api_client):
        """Users whose ASSIGN UserEvent was removed must not appear anymore."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        kept = factories.UserFactory(full_name="Kept User")
        removed = factories.UserFactory(full_name="Removed User")
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=kept,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )
        factories.UserEventFactory(
            user=removed,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )
        # Simulate an unassign: the corresponding ASSIGN UserEvent is deleted
        # (mirrors delete_assign_user_events in core.signals).
        models.UserEvent.objects.filter(
            thread=thread,
            user=removed,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).delete()

        response = api_client.get(
            reverse("threads-list"), {"mailbox_id": str(mailbox.id)}
        )

        assert response.status_code == status.HTTP_200_OK
        assigned = response.data["results"][0]["assigned_users"]
        assert [u["id"] for u in assigned] == [str(kept.id)]
