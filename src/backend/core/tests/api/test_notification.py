"""Tests for the UserNotification API endpoints."""

import uuid

from django.urls import reverse

import pytest
from rest_framework import status

from core import factories

pytestmark = pytest.mark.django_db


def get_notification_url(notification_id=None):
    """Helper function to get the notification URL."""
    if notification_id:
        return reverse("notifications-detail", kwargs={"id": notification_id})
    return reverse("notifications-list")


def get_notification_action_url(action_name):
    """Helper to get notification action URLs (count, mark-all-done)."""
    return reverse(f"notifications-{action_name}")


class TestNotificationList:
    """Test the GET /notifications/ endpoint."""

    def test_list_notifications_success(self, api_client):
        """Test listing notifications for the current user."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        # Create notifications for this user
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory.create_batch(3, user=user, thread=thread)
        # Create notifications for another user (should not appear)
        factories.UserNotificationFactory.create_batch(2)

        response = api_client.get(get_notification_url())
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3

    def test_list_notifications_filter_by_is_done(self, api_client):
        """Test filtering notifications by done status."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, is_done=False)
        factories.UserNotificationFactory(user=user, thread=thread, is_done=True)

        response = api_client.get(f"{get_notification_url()}?is_done=false")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["is_done"] is False

    def test_list_notifications_filter_by_type(self, api_client):
        """Test filtering notifications by type."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, type="mention")
        factories.UserNotificationFactory(user=user, thread=thread, type="assignment")

        response = api_client.get(f"{get_notification_url()}?type=mention")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_list_notifications_unauthorized(self, api_client):
        """Test listing notifications without authentication."""
        response = api_client.get(get_notification_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestNotificationUpdate:
    """Test the PATCH /notifications/{id}/ endpoint."""

    def test_mark_notification_as_done(self, api_client):
        """Test marking a notification as done."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        notification = factories.UserNotificationFactory(user=user, is_done=False)

        response = api_client.patch(
            get_notification_url(notification.id),
            {"is_done": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_done"] is True

        notification.refresh_from_db()
        assert notification.is_done is True

    def test_update_notification_readonly_fields(self, api_client):
        """Test that read-only fields cannot be changed."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        notification = factories.UserNotificationFactory(user=user, type="mention")

        response = api_client.patch(
            get_notification_url(notification.id),
            {"type": "assignment"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        notification.refresh_from_db()
        assert notification.type == "mention"  # type should not change

    def test_update_notification_of_other_user(self, api_client):
        """Test that a user cannot update another user's notification."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        other_user = factories.UserFactory()
        notification = factories.UserNotificationFactory(user=other_user)

        response = api_client.patch(
            get_notification_url(notification.id),
            {"is_done": True},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestNotificationRetrieve:
    """Test the GET /notifications/{id}/ endpoint."""

    def test_retrieve_notification_success(self, api_client):
        """Test retrieving a notification."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        notification = factories.UserNotificationFactory(user=user)

        response = api_client.get(get_notification_url(notification.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(notification.id)

    def test_retrieve_notification_not_found(self, api_client):
        """Test retrieving a non-existent notification."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_notification_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestNotificationCount:
    """Test the GET /notifications/count/ endpoint."""

    def test_count_unread_notifications(self, api_client):
        """Test counting unread notifications."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory.create_batch(
            3, user=user, thread=thread, is_done=False
        )
        factories.UserNotificationFactory(user=user, thread=thread, is_done=True)

        response = api_client.get(get_notification_action_url("count"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"count": 3}

    def test_count_zero_when_all_done(self, api_client):
        """Test count returns 0 when all notifications are done."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, is_done=True)

        response = api_client.get(get_notification_action_url("count"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"count": 0}

    def test_count_zero_when_no_notifications(self, api_client):
        """Test count returns 0 when user has no notifications."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)

        response = api_client.get(get_notification_action_url("count"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"count": 0}

    def test_count_excludes_other_users(self, api_client):
        """Test count only counts current user's notifications."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, is_done=False)
        # Other user's notification should not be counted
        factories.UserNotificationFactory(thread=thread, is_done=False)

        response = api_client.get(get_notification_action_url("count"))
        assert response.data == {"count": 1}

    def test_count_unauthorized(self, api_client):
        """Test count endpoint requires authentication."""
        response = api_client.get(get_notification_action_url("count"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestNotificationMarkAllDone:
    """Test the POST /notifications/mark-all-done/ endpoint."""

    def test_mark_all_done_success(self, api_client):
        """Test marking all notifications as done."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory.create_batch(
            3, user=user, thread=thread, is_done=False
        )

        response = api_client.post(get_notification_action_url("mark-all-done"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"updated": 3}

        # Verify in database
        from core.models import UserNotification

        assert UserNotification.objects.filter(user=user, is_done=False).count() == 0

    def test_mark_all_done_only_current_user(self, api_client):
        """Test mark-all-done only affects current user's notifications."""
        user = factories.UserFactory()
        other_user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, is_done=False)
        factories.UserNotificationFactory(
            user=other_user, thread=thread, is_done=False
        )

        response = api_client.post(get_notification_action_url("mark-all-done"))
        assert response.data == {"updated": 1}

        # Other user's notification should still be undone
        from core.models import UserNotification

        assert (
            UserNotification.objects.filter(user=other_user, is_done=False).count() == 1
        )

    def test_mark_all_done_when_already_done(self, api_client):
        """Test mark-all-done when all are already done."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(user=user, thread=thread, is_done=True)

        response = api_client.post(get_notification_action_url("mark-all-done"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"updated": 0}

    def test_mark_all_done_unauthorized(self, api_client):
        """Test mark-all-done requires authentication."""
        response = api_client.post(get_notification_action_url("mark-all-done"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestNotificationNestedSerialization:
    """Test that notification list returns nested thread and thread_event data."""

    def test_list_contains_nested_thread(self, api_client):
        """Test notification list includes nested thread with id and subject."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory(subject="Test Subject")
        factories.UserNotificationFactory(user=user, thread=thread)

        response = api_client.get(get_notification_url())
        assert response.status_code == status.HTTP_200_OK
        notification = response.data["results"][0]
        assert notification["thread"]["id"] == str(thread.id)
        assert notification["thread"]["subject"] == "Test Subject"

    def test_list_contains_nested_thread_event_with_author(self, api_client):
        """Test notification list includes nested thread_event with author details."""
        user = factories.UserFactory()
        author = factories.UserFactory(full_name="John Doe")
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        thread_event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            data={"content": "Hello @user"},
        )
        factories.UserNotificationFactory(
            user=user, thread=thread, thread_event=thread_event
        )

        response = api_client.get(get_notification_url())
        notification = response.data["results"][0]
        assert notification["thread_event"]["id"] == str(thread_event.id)
        assert notification["thread_event"]["author"]["full_name"] == "John Doe"
        assert notification["thread_event"]["content"] == "Hello @user"

    def test_list_handles_null_thread_event(self, api_client):
        """Test notification list handles null thread_event gracefully."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        factories.UserNotificationFactory(
            user=user, thread=thread, thread_event=None
        )

        response = api_client.get(get_notification_url())
        notification = response.data["results"][0]
        assert notification["thread_event"] is None

    def test_list_handles_empty_content_in_data(self, api_client):
        """Test notification handles thread_event with empty data dict."""
        user = factories.UserFactory()
        api_client.force_authenticate(user=user)
        thread = factories.ThreadFactory()
        thread_event = factories.ThreadEventFactory(thread=thread, data={})
        factories.UserNotificationFactory(
            user=user, thread=thread, thread_event=thread_event
        )

        response = api_client.get(get_notification_url())
        notification = response.data["results"][0]
        assert notification["thread_event"]["content"] == ""
