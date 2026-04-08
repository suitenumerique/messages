"""Tests for has_mention and has_unread_mention filters and stats on Thread API."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums, factories

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


class TestThreadFilterUnreadMention:
    """Test GET /api/v1.0/threads/?has_unread_mention=1 filter."""

    def test_thread_mention_unread_filter_returns_matching(self, api_client):
        """Filter should return only threads with active unread MENTION UserEvents."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create an unread mention on this thread
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        # Create another thread without mentions
        thread_no_mention = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_no_mention,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unread_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids
        assert str(thread_no_mention.id) not in thread_ids

    def test_thread_mention_unread_filter_empty_when_none(self, api_client):
        """Filter should return empty list when no unread mentions exist."""
        user, mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unread_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_thread_mention_unread_filter_excludes_read(self, api_client):
        """A thread whose only MENTION UserEvent has ``read_at`` set must be excluded.

        Guards the ``read_at__isnull=True`` clause in ``_has_unread_mention``:
        without it, the filter would collapse to ``has_mention`` and leak
        already-acknowledged mentions.
        """
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Seed a mention that has already been read.
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
            read_at=timezone.now(),
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unread_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) not in thread_ids
        assert len(response.data["results"]) == 0

    def test_thread_mention_unread_filter_ignores_other_user(self, api_client):
        """Mentions belonging to another user must not leak into the filter."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Another user has an unread mention on the same thread
        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_unread_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0


class TestThreadStatsUnreadMention:
    """Test GET /api/v1.0/threads/stats/?stats_fields=has_unread_mention."""

    def test_thread_mention_unread_stats_returns_count(self, api_client):
        """Stats should return correct has_unread_mention count."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Create unread mentions on two threads
        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

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
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_unread_mention",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_unread_mention"] == 2

    def test_thread_mention_unread_stats_ignores_other_user(self, api_client):
        """Stats must not count MENTION UserEvents belonging to another user."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_unread_mention",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_unread_mention"] == 0


class TestThreadEventHasUnreadMention:
    """Test GET /api/v1.0/threads/{id}/events/ returns has_unread_mention."""

    def test_thread_mention_unread_event_flag_true(self, api_client):
        """ThreadEvent with unread mention for current user should have has_unread_mention=True."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("thread-event-list", kwargs={"thread_id": thread.id}),
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["has_unread_mention"] is True

    def test_thread_mention_unread_event_flag_false_no_mention(self, api_client):
        """ThreadEvent without mention should have has_unread_mention=False."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        factories.ThreadEventFactory(thread=thread, author=user)

        response = api_client.get(
            reverse("thread-event-list", kwargs={"thread_id": thread.id}),
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["has_unread_mention"] is False

    def test_thread_mention_unread_event_flag_false_when_read(self, api_client):
        """ThreadEvent with already-read mention should have has_unread_mention=False."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
            read_at=timezone.now(),
        )

        response = api_client.get(
            reverse("thread-event-list", kwargs={"thread_id": thread.id}),
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["has_unread_mention"] is False

    def test_thread_mention_unread_event_flag_ignores_other_user(self, api_client):
        """has_unread_mention must ignore mentions whose UserEvent targets another user."""
        user, _mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("thread-event-list", kwargs={"thread_id": thread.id}),
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["has_unread_mention"] is False


class TestThreadFilterMention:
    """Test GET /api/v1.0/threads/?has_mention=1 filter.

    Unlike has_unread_mention which only returns threads with unread mentions,
    has_mention returns threads with ANY active mention (read or unread).
    """

    def test_thread_mention_any_filter_returns_matching(self, api_client):
        """Filter should return threads with both read and unread mentions."""
        user, mailbox, thread_unread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread with unread mention
        event1 = factories.ThreadEventFactory(thread=thread_unread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread_unread,
            thread_event=event1,
            type=enums.UserEventTypeChoices.MENTION,
        )

        # Thread with read mention
        thread_read = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_read,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        event2 = factories.ThreadEventFactory(thread=thread_read, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread_read,
            thread_event=event2,
            type=enums.UserEventTypeChoices.MENTION,
            read_at=timezone.now(),
        )

        # Thread without any mention
        thread_none = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread_none,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread_unread.id) in thread_ids
        assert str(thread_read.id) in thread_ids
        assert str(thread_none.id) not in thread_ids

    def test_thread_mention_any_filter_empty_when_none(self, api_client):
        """Filter should not return threads without any MENTION UserEvent."""
        user, mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_thread_mention_any_filter_includes_read(self, api_client):
        """Filter should include threads where the mention has been read."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        event = factories.ThreadEventFactory(thread=thread, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
            read_at=timezone.now(),
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread.id) in thread_ids

    def test_thread_mention_any_filter_ignores_other_user(self, api_client):
        """Mentions belonging to another user must not leak into the filter."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("threads-list"),
            {"mailbox_id": str(mailbox.id), "has_mention": "1"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0


class TestThreadStatsMention:
    """Test GET /api/v1.0/threads/stats/?stats_fields=has_mention."""

    def test_thread_mention_any_stats_returns_count(self, api_client):
        """Stats should count threads with any active mention (read or unread)."""
        user, mailbox, thread1 = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        # Thread 1: unread mention
        event1 = factories.ThreadEventFactory(thread=thread1, author=user)
        factories.UserEventFactory(
            user=user,
            thread=thread1,
            thread_event=event1,
            type=enums.UserEventTypeChoices.MENTION,
        )

        # Thread 2: read mention
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
            type=enums.UserEventTypeChoices.MENTION,
            read_at=timezone.now(),
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_mention",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_mention"] == 2

    def test_thread_mention_any_stats_ignores_other_user(self, api_client):
        """Stats must not count MENTION UserEvents belonging to another user."""
        user, mailbox, thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        event = factories.ThreadEventFactory(thread=thread, author=other_user)
        factories.UserEventFactory(
            user=other_user,
            thread=thread,
            thread_event=event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_mention",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_mention"] == 0


class TestThreadStatsMentionUnreadSuffixValidation:
    """Test that mention-related fields reject the generic '_unread' suffix.

    has_mention / has_unread_mention are annotation-only fields (no real model
    column), and "unread" in has_unread_mention refers to UserEvent.read_at, not
    ThreadAccess.read_at. Allowing the generic '_unread' suffix would both crash
    at runtime (Q(has_mention=True) has no column) and be semantically misleading.
    """

    def test_thread_mention_stats_rejects_has_mention_unread_suffix(self, api_client):
        """`has_mention_unread` must be rejected at validation with a 400."""
        user, mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_mention_unread",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "has_mention_unread" in response.data["detail"]

    def test_thread_mention_stats_rejects_has_unread_mention_unread_suffix(
        self, api_client
    ):
        """`has_unread_mention_unread` must be rejected at validation with a 400."""
        user, mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_unread_mention_unread",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "has_unread_mention_unread" in response.data["detail"]

    def test_thread_mention_stats_rejects_mixed_suffix_validation(self, api_client):
        """A valid field next to an invalid mention unread combo must still 400.

        Guards against a regression where one-off validation would let the
        invalid field reach the aggregation loop because another valid field
        was present in the same request.
        """
        user, mailbox, _thread = setup_user_with_thread_access()
        api_client.force_authenticate(user=user)

        response = api_client.get(
            reverse("threads-stats"),
            {
                "mailbox_id": str(mailbox.id),
                "stats_fields": "has_unread_mention,has_mention_unread",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "has_mention_unread" in response.data["detail"]
