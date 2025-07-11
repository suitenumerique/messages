"""Tests for the Thread API list endpoint."""

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from core import enums
from core.factories import (
    ContactFactory,
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
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
    assert thread_data["has_unread"] is True

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


# has_unread filter has been removed - use all_unread in stats instead


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
    # Thread 1: Not starred, not trashed
    thread1 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread1,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread1, is_starred=False, is_trashed=False)
    # Thread 2: Has trashed message (starred message is trashed, so has_starred=False)
    thread2 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread2,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread2, is_starred=True, is_trashed=True)
    # Thread 3: Starred, not trashed
    thread3 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread3,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(thread=thread3, is_starred=True, is_trashed=False)
    # Thread 4: Has both starred (not trashed) and trashed messages
    thread4 = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread4,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    MessageFactory(
        thread=thread4, is_starred=True, is_trashed=False
    )  # Starred, not trashed
    MessageFactory(
        thread=thread4, is_starred=False, is_trashed=True
    )  # Not starred, trashed

    for t in [thread1, thread2, thread3, thread4]:
        t.update_stats()

    # Filter: has_starred=1 AND has_trashed=1 (thread has both starred non-trashed AND trashed messages)
    response = api_client.get(API_URL, {"has_starred": "1", "has_trashed": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread4.id)

    # Filter: has_starred=1 AND has_trashed=0 (thread has starred non-trashed messages, no trashed messages)
    response = api_client.get(API_URL, {"has_starred": "1", "has_trashed": "0"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread3.id)

    # Filter: has_starred=0 AND has_trashed=0 (thread has no starred non-trashed messages, no trashed messages)
    response = api_client.get(API_URL, {"has_starred": "0", "has_trashed": "0"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread1.id)

    # Filter: has_starred=0 AND has_trashed=1 (thread has no starred non-trashed messages, but has trashed messages)
    response = api_client.get(API_URL, {"has_starred": "0", "has_trashed": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(thread2.id)


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

        # Create some threads with varying boolean flags
        thread1 = ThreadFactory(
            has_unread=True,
            has_messages=True,
            has_trashed=False,
            has_draft=True,
            has_starred=True,
            has_sender=True,
        )
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        thread2 = ThreadFactory(
            has_unread=True,
            has_messages=True,
            has_trashed=True,
            has_draft=False,
            has_starred=False,
            has_sender=True,
        )
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # Thread in another mailbox (should be excluded)
        other_mailbox = MailboxFactory()
        other_thread = ThreadFactory(has_unread=True)
        ThreadAccessFactory(
            mailbox=other_mailbox,
            thread=other_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            url,
            {
                "stats_fields": "has_messages,has_trashed,has_draft,has_starred,has_sender"
            },
        )

        assert response.status_code == 200
        assert response.data == {
            "has_messages": 2,  # Both threads have has_messages=True
            "has_trashed": 1,  # Only thread2 has has_trashed=True
            "has_draft": 1,  # Only thread1 has has_draft=True
            "has_starred": 1,  # Only thread1 has has_starred=True
            "has_sender": 2,  # Both threads have has_sender=True
        }

    def test_stats_with_mailbox_filter(self, api_client, url):
        """Test retrieving stats filtered by mailbox."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        mailbox2 = MailboxFactory()
        MailboxAccessFactory(user=user, mailbox=mailbox2)

        thread1 = ThreadFactory(has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread2 = ThreadFactory(has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox2,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        response = api_client.get(
            url, {"mailbox_id": str(mailbox.id), "stats_fields": "has_messages"}
        )

        assert response.status_code == 200
        assert response.data == {"has_messages": 1}

    def test_stats_with_flag_filter(self, api_client, url):
        """Test retrieving stats filtered by flags (e.g., has_starred=1)."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Starred thread
        thread1 = ThreadFactory(has_starred=True, has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Not starred thread
        thread2 = ThreadFactory(has_starred=False, has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            url, {"has_starred": "1", "stats_fields": "has_messages"}
        )

        assert response.status_code == 200
        # Should only count the starred thread
        assert response.data == {"has_messages": 1}

    def test_stats_with_zero_flag_filter(self, api_client, url):
        """Test retrieving stats filtered by flags with zero count (e.g., has_trashed=0)."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Not trashed thread
        thread1 = ThreadFactory(has_trashed=False, has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Trashed thread
        thread2 = ThreadFactory(has_trashed=True, has_unread=True, has_messages=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread2,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            url, {"has_trashed": "0", "stats_fields": "has_messages"}
        )

        assert response.status_code == 200
        # Should only count the non-trashed thread
        assert response.data == {"has_messages": 1}

        response = api_client.get(url, {"stats_fields": "has_messages"})

        assert response.status_code == 200
        # Get all threads
        assert response.data == {"has_messages": 2}

    def test_stats_specific_fields(self, api_client, url):
        """Test retrieving stats for specific fields."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        thread = ThreadFactory(has_unread=True, has_messages=True, has_draft=True)
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(url, {"stats_fields": "has_draft"})

        assert response.status_code == 200
        assert response.data == {"has_draft": 1}
        assert "has_messages" not in response.data

    def test_stats_no_matching_threads(self, api_client, url):
        """Test retrieving stats when no threads match the filters."""

        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        thread = ThreadFactory(has_trashed=True)  # Trashed
        ThreadAccessFactory(
            mailbox=mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = api_client.get(
            url,
            {
                "has_trashed": "0",
                "stats_fields": "has_messages",
            },  # Filter for non-trashed
        )

        assert response.status_code == 200
        assert response.data == {"has_messages": 0}

    def test_stats_all_and_all_unread(self, api_client, url):
        """Test the special 'all' and 'all_unread' stats fields."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Create threads with different unread states
        thread1 = ThreadFactory(has_unread=True, has_messages=True)
        thread2 = ThreadFactory(has_unread=False, has_messages=True)
        thread3 = ThreadFactory(has_unread=True, has_starred=True)

        for thread in [thread1, thread2, thread3]:
            ThreadAccessFactory(
                mailbox=mailbox,
                thread=thread,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )

        response = api_client.get(url, {"stats_fields": "all,all_unread"})

        assert response.status_code == 200
        assert response.data == {
            "all": 3,  # All 3 threads
            "all_unread": 2,  # thread1 and thread3 are unread
        }

    def test_stats_unread_variants(self, api_client, url):
        """Test the '_unread' variants of stats fields."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Create threads with different combinations of flags and unread status
        thread1 = ThreadFactory(
            has_unread=True,
            has_starred=True,
            has_sender=True,
            is_spam=False,
            has_active=True,
        )
        thread2 = ThreadFactory(
            has_unread=False,
            has_starred=True,
            has_sender=True,
            is_spam=False,
            has_active=True,
        )
        thread3 = ThreadFactory(
            has_unread=True,
            has_starred=False,
            has_sender=False,
            is_spam=True,
            has_active=False,
        )

        for thread in [thread1, thread2, thread3]:
            ThreadAccessFactory(
                mailbox=mailbox,
                thread=thread,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )

        response = api_client.get(
            url,
            {
                "stats_fields": (
                    "has_starred,"
                    "has_starred_unread,"
                    "has_sender,"
                    "has_sender_unread,"
                    "is_spam,"
                    "is_spam_unread,"
                    "has_active,"
                    "has_active_unread"
                )
            },
        )

        assert response.status_code == 200
        assert response.data == {
            "has_starred": 2,  # thread1 and thread2 have has_starred=True
            "has_starred_unread": 1,  # Only thread1 is starred AND unread
            "has_sender": 2,  # thread1 and thread2 have has_sender=True
            "has_sender_unread": 1,  # Only thread1 is sender AND unread
            "is_spam": 1,  # Only thread3 is spam
            "is_spam_unread": 1,  # thread3 is spam AND unread
            "has_active": 2,  # thread1 and thread2 have has_active=True
            "has_active_unread": 1,  # Only thread1 is active AND unread
        }

    def test_stats_with_filters_and_unread_variants(self, api_client, url):
        """Test stats with query filters combined with unread variants."""
        user = UserFactory()
        api_client.force_authenticate(user=user)
        mailbox = MailboxFactory(users_read=[user])

        # Create threads with different combinations
        thread1 = ThreadFactory(has_unread=True, has_starred=True, has_sender=True)
        thread2 = ThreadFactory(has_unread=False, has_starred=True, has_sender=True)
        thread3 = ThreadFactory(has_unread=True, has_starred=False, has_sender=True)

        for thread in [thread1, thread2, thread3]:
            ThreadAccessFactory(
                mailbox=mailbox,
                thread=thread,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )

        # Filter for only starred threads and get unread counts
        response = api_client.get(
            url,
            {"has_starred": "1", "stats_fields": "all,all_unread,has_sender_unread"},
        )

        assert response.status_code == 200
        assert response.data == {
            "all": 2,  # thread1 and thread2 are starred
            "all_unread": 1,  # Only thread1 is starred AND unread
            "has_sender_unread": 1,  # Only thread1 is starred, sender, AND unread
        }

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

        response = api_client.get(url, {"stats_fields": "has_messages,invalid_field"})
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

        thread = ThreadFactory(has_trashed=True)  # Trashed
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

        domain = MailDomainFactory(name="example.com")
        # Create first mailbox with authenticated user access
        cantine_mailbox = MailboxFactory(
            users_read=[authenticated_user], local_part="cantine", domain=domain
        )
        cantine_mailbox.contact = ContactFactory(
            email=str(cantine_mailbox), mailbox=cantine_mailbox
        )
        cantine_mailbox.save()
        # Create first thread with an access for cantine_mailbox
        thread1 = ThreadFactory()
        ThreadAccessFactory(
            mailbox=cantine_mailbox,
            thread=thread1,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        # Create two messages for the first thread
        MessageFactory(thread=thread1)
        MessageFactory(thread=thread1)

        # Create second mailbox with authenticated user access
        tresorie_mailbox = MailboxFactory(
            users_read=[authenticated_user], local_part="tresorie", domain=domain
        )
        tresorie_mailbox.contact = ContactFactory(
            email=str(tresorie_mailbox), mailbox=tresorie_mailbox
        )
        tresorie_mailbox.save()

        # Create second thread with an access for mailbox2
        thread2 = ThreadFactory()
        access2 = ThreadAccessFactory(
            mailbox=tresorie_mailbox,
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
            mailbox=tresorie_mailbox,
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
        # TODO: test with django_assert_num_queries
        response = api_client.get(url, {"mailbox_id": str(tresorie_mailbox.id)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        thread_ids = [t["id"] for t in response.data["results"]]
        assert str(thread1.id) not in thread_ids
        assert str(thread2.id) in thread_ids
        assert str(thread3.id) in thread_ids
        assert response.data["results"][0]["user_role"] == "viewer"
        # check that the accesses are returned
        assert len(response.data["results"][0]["accesses"]) == 1
        access = response.data["results"][1]["accesses"][0]
        assert access["id"] == str(access2.id)
        assert access["mailbox"]["id"] == str(access2.mailbox.id)
        assert access["mailbox"]["email"] == str(access2.mailbox)
        assert access["mailbox"]["name"] == access2.mailbox.contact.name
        assert access["role"] == enums.ThreadAccessRoleChoices(access2.role).label
        assert access["mailbox"]["id"] == str(access2.mailbox.id)
        assert access["mailbox"]["email"] == str(access2.mailbox)
        assert access["mailbox"]["name"] == access2.mailbox.contact.name
        assert access["role"] == enums.ThreadAccessRoleChoices(access2.role).label

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
