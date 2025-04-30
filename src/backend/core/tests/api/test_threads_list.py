"""Tests for the Thread API list endpoint."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MessageFactory,
    ThreadFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

API_URL = reverse("threads-list")


def test_list_threads_success(api_client):
    """Test listing threads successfully."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox1 = MailboxFactory(users_read=[user])
    mailbox2 = MailboxFactory(users_read=[user])
    other_mailbox = MailboxFactory()  # User doesn't have access

    # Create threads
    thread1 = ThreadFactory(mailbox=mailbox1)
    MessageFactory(thread=thread1, is_unread=True)
    thread2 = ThreadFactory(mailbox=mailbox2)
    MessageFactory(thread=thread2, is_unread=False, read_at=timezone.now())
    ThreadFactory(mailbox=other_mailbox)  # Inaccessible thread

    # Update counters after creating messages
    thread1.update_stats()
    thread2.update_stats()

    response = api_client.get(API_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 2  # Only accessible threads
    assert len(response.data["results"]) == 2

    # Check data for one thread (content depends on serializer)
    thread_data = next(
        (t for t in response.data["results"] if t["id"] == str(thread1.id)), None
    )
    assert thread_data is not None
    assert thread_data["count_unread"] == 1

    # Test filtering by mailbox
    response = api_client.get(API_URL, {"mailbox_id": str(mailbox1.id)})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)

    response = api_client.get(API_URL, {"mailbox_id": str(mailbox2.id)})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)


def test_list_threads_unauthorized(api_client):
    """Test listing threads without authentication."""
    response = api_client.get(API_URL)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_threads_no_access(api_client):
    """Test listing threads when user has no mailbox access."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    # Create threads in mailboxes the user doesn't have access to
    mailbox1 = MailboxFactory()
    ThreadFactory(mailbox=mailbox1)

    response = api_client.get(API_URL)
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 0
    assert len(response.data["results"]) == 0


# --- Tests for counter-based filters ---


def test_list_threads_filter_has_unread(api_client):
    """Test filtering threads by has_unread=1."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    # Thread 1: Has unread messages
    thread1 = ThreadFactory(mailbox=mailbox)
    message1 = MessageFactory(thread=thread1, is_unread=True)
    MessageFactory(thread=thread1, is_unread=False, read_at=timezone.now())
    message3 = MessageFactory(thread=thread1, is_unread=False, read_at=timezone.now())
    # Thread 2: No unread messages
    thread2 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread2, is_unread=False, read_at=timezone.now())

    thread1.update_stats()
    thread2.update_stats()

    response = api_client.get(API_URL, {"has_unread": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)
    assert response.data["results"][0]["sender_names"] == [
        message1.sender.name,
        message3.sender.name,
    ]

    response = api_client.get(API_URL, {"has_unread": "true"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1

    response = api_client.get(
        API_URL, {"has_unread": "0"}
    )  # Filter for threads with 0 unread
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)

    response = api_client.get(API_URL, {"has_unread": "false"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1


def test_list_threads_filter_has_trashed(api_client):
    """Test filtering threads by has_trashed=1."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    # Thread 1: Has trashed messages
    thread1 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread1, is_trashed=True)
    # Thread 2: No trashed messages
    thread2 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread2, is_trashed=False)

    thread1.update_stats()
    thread2.update_stats()

    response = api_client.get(API_URL, {"has_trashed": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)

    response = api_client.get(API_URL, {"has_trashed": "0"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)


def test_list_threads_filter_has_starred(api_client):
    """Test filtering threads by has_starred=1."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    # Thread 1: Has starred messages
    thread1 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread1, is_starred=True)
    # Thread 2: No starred messages
    thread2 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread2, is_starred=False)

    thread1.update_stats()
    thread2.update_stats()

    response = api_client.get(API_URL, {"has_starred": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)

    response = api_client.get(API_URL, {"has_starred": "0"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)


def test_list_threads_filter_combined(api_client):
    """Test filtering threads by combining filters."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    # Thread 1: Unread, not starred
    thread1 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread1, is_unread=True, is_starred=False)
    # Thread 2: Unread and starred
    thread2 = ThreadFactory(mailbox=mailbox)
    MessageFactory(thread=thread2, is_unread=True, is_starred=True)
    # Thread 3: Read, starred
    thread3 = ThreadFactory(mailbox=mailbox)
    MessageFactory(
        thread=thread3, is_unread=False, read_at=timezone.now(), is_starred=True
    )
    # Thread 4: Read, not starred
    thread4 = ThreadFactory(mailbox=mailbox)
    MessageFactory(
        thread=thread4, is_unread=False, read_at=timezone.now(), is_starred=False
    )

    for t in [thread1, thread2, thread3, thread4]:
        t.update_stats()

    # Filter: has_unread=1 AND has_starred=1
    response = api_client.get(API_URL, {"has_unread": "1", "has_starred": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)

    # Filter: has_unread=1 AND has_starred=0
    response = api_client.get(API_URL, {"has_unread": "1", "has_starred": "0"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)

    # Filter: has_unread=0 AND has_starred=1
    response = api_client.get(API_URL, {"has_unread": "0", "has_starred": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread3.id)


@pytest.mark.django_db
class TestThreadStatsAPI:
    """Test the GET /threads/stats/ endpoint."""

    @pytest.fixture
    def url(self):
        """Return the URL for the stats endpoint."""
        return reverse("threads-stats")

    def test_stats_no_filters(self, api_client, url):
        """Test retrieving stats with no filters."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Create some threads with varying counts
        ThreadFactory(
            mailbox=mailbox,
            count_unread=2,
            count_messages=5,
            count_trashed=0,
            count_draft=1,
            count_starred=1,
            count_sender=3,
        )
        ThreadFactory(
            mailbox=mailbox,
            count_unread=1,
            count_messages=3,
            count_trashed=1,
            count_draft=0,
            count_starred=0,
            count_sender=1,
        )
        # Thread in another mailbox (should be excluded)
        other_mailbox = MailboxFactory()
        ThreadFactory(mailbox=other_mailbox, count_unread=10)

        response = api_client.get(
            url, {"stats_fields": "unread,messages,trashed,draft,starred,sender"}
        )

        assert response.status_code == 200
        assert response.data == {
            "unread": 3,  # 2 + 1
            "messages": 8,  # 5 + 3
            "trashed": 1,  # 0 + 1
            "draft": 1,  # 1 + 0
            "starred": 1,  # 1 + 0
            "sender": 4,  # 3 + 1
        }

    def test_stats_with_mailbox_filter(self, api_client, url):
        """Test retrieving stats filtered by mailbox."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        mailbox2 = MailboxFactory()
        MailboxAccessFactory(user=user, mailbox=mailbox2)

        ThreadFactory(mailbox=mailbox, count_unread=5, count_messages=10)
        ThreadFactory(mailbox=mailbox2, count_unread=3, count_messages=6)

        response = api_client.get(
            url, {"mailbox_id": str(mailbox.id), "stats_fields": "unread,messages"}
        )

        assert response.status_code == 200
        assert response.data == {"unread": 5, "messages": 10}

    def test_stats_with_flag_filter(self, api_client, url):
        """Test retrieving stats filtered by flags (e.g., has_starred=1)."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Starred thread
        ThreadFactory(
            mailbox=mailbox, count_starred=1, count_unread=2, count_messages=5
        )
        # Not starred thread
        ThreadFactory(
            mailbox=mailbox, count_starred=0, count_unread=3, count_messages=7
        )

        response = api_client.get(
            url, {"has_starred": "1", "stats_fields": "unread,messages"}
        )

        assert response.status_code == 200
        # Should only sum counts from the starred thread
        assert response.data == {"unread": 2, "messages": 5}

    def test_stats_with_zero_flag_filter(self, api_client, url):
        """Test retrieving stats filtered by flags with zero count (e.g., has_trashed=0)."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Not trashed thread
        ThreadFactory(
            mailbox=mailbox, count_trashed=0, count_unread=4, count_messages=8
        )
        # Trashed thread
        ThreadFactory(
            mailbox=mailbox, count_trashed=1, count_unread=1, count_messages=2
        )

        response = api_client.get(
            url, {"has_trashed": "0", "stats_fields": "unread,messages"}
        )

        assert response.status_code == 200
        # Should only sum counts from the non-trashed thread
        assert response.data == {"unread": 4, "messages": 8}

        response = api_client.get(url, {"stats_fields": "unread,messages"})

        assert response.status_code == 200
        # Get all counts
        assert response.data == {"unread": 5, "messages": 10}

    def test_stats_specific_fields(self, api_client, url):
        """Test retrieving stats for specific fields."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        ThreadFactory(mailbox=mailbox, count_unread=5, count_messages=10, count_draft=2)

        response = api_client.get(url, {"stats_fields": "unread,draft"})

        assert response.status_code == 200
        assert response.data == {"unread": 5, "draft": 2}
        assert "messages" not in response.data

    def test_stats_no_matching_threads(self, api_client, url):
        """Test retrieving stats when no threads match the filters."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        ThreadFactory(mailbox=mailbox, count_trashed=1)  # Trashed

        response = api_client.get(
            url,
            {
                "has_trashed": "0",
                "stats_fields": "unread,messages",
            },  # Filter for non-trashed
        )

        assert response.status_code == 200
        assert response.data == {"unread": 0, "messages": 0}

    def test_stats_missing_stats_fields(self, api_client, url):
        """Test request without the required 'stats_fields' parameter."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        MailboxFactory(users_read=[user])

        response = api_client.get(url)
        assert response.status_code == 400
        assert "'stats_fields' parameter is required" in response.data["detail"]

    def test_stats_invalid_stats_field(self, api_client, url):
        """Test request with an invalid field in 'stats_fields'."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        MailboxFactory(users_read=[user])

        response = api_client.get(url, {"stats_fields": "unread,invalid_field"})
        assert response.status_code == 400
        assert (
            "Invalid field requested in stats_fields: invalid_field"
            in response.data["detail"]
        )

    def test_stats_empty_stats_fields(self, api_client, url):
        """Test request with an empty 'stats_fields' parameter."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        MailboxFactory(users_read=[user])

        response = api_client.get(url, {"stats_fields": ""})
        assert response.status_code == 400
        assert "'stats_fields' parameter is required" in response.data["detail"]

    def test_stats_anonymous_user(self, api_client, url):
        """Test stats endpoint with anonymous user."""

        user = UserFactory()
        mailbox = MailboxFactory(users_read=[user])

        ThreadFactory(mailbox=mailbox, count_trashed=1)  # Trashed

        response = api_client.get(url)
        assert response.status_code == 401
