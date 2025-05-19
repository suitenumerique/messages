"""Tests for the Thread API list endpoint."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums
from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MessageFactory,
    ThreadAccessFactory,
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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread1, is_unread=True)
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox2,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread2, is_unread=False, read_at=timezone.now())
    thread3 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=other_mailbox,
        thread=thread3,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox1,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )

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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    message1 = MessageFactory(thread=thread1, is_unread=True)
    MessageFactory(thread=thread1, is_unread=False, read_at=timezone.now())
    message3 = MessageFactory(thread=thread1, is_unread=False, read_at=timezone.now())
    # Thread 2: No unread messages
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread1, is_trashed=True)
    # Thread 2: No trashed messages
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread1, is_starred=True)
    # Thread 2: No starred messages
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
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
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread1, is_unread=True, is_starred=False)
    # Thread 2: Unread and starred
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread2, is_unread=True, is_starred=True)
    # Thread 3: Read, starred
    thread3 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread3,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(
        thread=thread3, is_unread=False, read_at=timezone.now(), is_starred=True
    )
    # Thread 4: Read, not starred
    thread4 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread4,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
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
        thread1 = ThreadFactory(
            count_unread=2,
            count_messages=5,
            count_trashed=0,
            count_draft=1,
            count_starred=1,
            count_sender=3,
        )
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        thread2 = ThreadFactory(
            count_unread=1,
            count_messages=3,
            count_trashed=1,
            count_draft=0,
            count_starred=0,
            count_sender=1,
        )
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Thread in another mailbox (should be excluded)
        other_mailbox = MailboxFactory()
        other_thread = ThreadFactory(count_unread=10)
        ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

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

        thread1 = ThreadFactory(count_unread=5, count_messages=10)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread2 = ThreadFactory(count_unread=3, count_messages=6)
        ThreadAccessFactory(
            mailbox=mailbox2,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
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
        thread1 = ThreadFactory(count_starred=1, count_unread=2, count_messages=5)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Not starred thread
        thread2 = ThreadFactory(count_starred=0, count_unread=3, count_messages=7)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
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
        thread1 = ThreadFactory(count_trashed=0, count_unread=4, count_messages=8)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Trashed thread
        thread2 = ThreadFactory(count_trashed=1, count_unread=1, count_messages=2)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
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

        thread = ThreadFactory(count_unread=5, count_messages=10, count_draft=2)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(url, {"stats_fields": "unread,draft"})

        assert response.status_code == 200
        assert response.data == {"unread": 5, "draft": 2}
        assert "messages" not in response.data

    def test_stats_no_matching_threads(self, api_client, url):
        """Test retrieving stats when no threads match the filters."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        thread = ThreadFactory(count_trashed=1)  # Trashed
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

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
        assert "Missing 'stats_fields' query parameter" in response.data["detail"]

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
        assert "Missing 'stats_fields' query parameter" in response.data["detail"]

    def test_stats_anonymous_user(self, api_client, url):
        """Test stats endpoint with anonymous user."""

        user = UserFactory()
        mailbox = MailboxFactory(users_read=[user])

        thread = ThreadFactory(count_trashed=1)  # Trashed
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(url)
        assert response.status_code == 401


# TODO: merge first tests below with the ones above
@pytest.mark.django_db
class TestThreadListAPI:
    """Test the GET /threads/ endpoint."""

    @pytest.fixture
    def url(self):
        """Return the URL for the list endpoint."""
        return reverse("threads-list")

    def test_list_threads_success(self, api_client, url):
        """Test listing threads successfully."""
        authenticated_user = UserFactory()
        api_client.force_authenticate(user=authenticated_user)

        # Create first mailbox with authenticated user access
        mailbox1 = MailboxFactory(users_read=[authenticated_user])
        # Create first thread with an access for mailbox1
        thread1 = ThreadFactory()
        ThreadAccessFactory(
            mailbox=mailbox1,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Create two messages for the first thread
        MessageFactory(thread=thread1)
        MessageFactory(thread=thread1)

        # Create second mailbox with authenticated user access
        mailbox2 = MailboxFactory(users_read=[authenticated_user])
        # Create second thread with an access for mailbox2
        thread2 = ThreadFactory()
        ThreadAccessFactory(
            mailbox=mailbox2,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )
        # Create three messages for the second thread
        MessageFactory(thread=thread2)
        MessageFactory(thread=thread2)
        MessageFactory(thread=thread2)

        # Create other thread for mailbox2
        thread3 = ThreadFactory()
        ThreadAccessFactory(
            mailbox=mailbox2,
            thread=thread3,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        # Create other thread for mailbox3 with no access for authenticated user
        mailbox3 = MailboxFactory()
        thread4 = ThreadFactory()
        ThreadAccessFactory(
            mailbox=mailbox3,
            thread=thread4,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        # Check that all threads for the authenticated user are returned
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

        # Check data for one thread (content depends on serializer)
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread1.id) in thread_ids
        assert str(thread2.id) in thread_ids
        assert str(thread3.id) in thread_ids
        assert str(thread4.id) not in thread_ids
        # no filter by mailbox should return None for user_role
        assert response.data["results"][0]["user_role"] is None

        # Test filtering by mailbox
        response = api_client.get(url, {"mailbox_id": str(mailbox2.id)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread1.id) not in thread_ids
        assert str(thread2.id) in thread_ids
        assert str(thread3.id) in thread_ids
        assert (
            response.data["results"][0]["user_role"]
            == enums.ThreadAccessRoleChoices.VIEWER
        )
        # check that the accesses are returned
        assert len(response.data["results"][0]["accesses"]) == 1
        assert response.data["results"][0]["accesses"] == [
            {
                "id": access.id,
                "mailbox": {
                    "id": access.mailbox.id,
                    "email": str(access.mailbox),
                },
                "role": access.role,
            }
            for access in thread2.accesses.all()
        ]

    def test_list_threads_unauthorized(self, api_client, url):
        """Test listing threads without authentication."""
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_threads_no_access(self, api_client, url):
        """Test listing threads when user has no mailbox access."""
        # Test filtering by mailbox that user doesn't have access to
        mailbox = MailboxFactory()
        user = UserFactory()
        api_client.force_authenticate(user=user)
        response = api_client.get(url, {"mailbox_id": str(mailbox.id)})
        assert response.status_code == status.HTTP_403_FORBIDDEN
