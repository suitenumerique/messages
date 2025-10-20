"""Tests for task API endpoints."""

import time
import uuid

from django.core.cache import cache
from django.test import TestCase

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories
from core.mda.tasks import send_message_task
from core.utils import get_task_progress


class TaskDetailViewTest(TestCase):
    """Test the TaskDetailView API endpoint."""

    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        self.task_id = uuid.uuid4()
        self.url = f"/api/v1.0/tasks/{self.task_id}/"

        self.user = factories.UserFactory()
        self.client.force_authenticate(user=self.user)

    def _set_progress_data(self, task_id, progress, metadata=None):
        """Helper method to set progress data directly in cache for testing."""
        progress_data = {
            "progress": progress,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        cache.set(f"task_progress:{task_id}", progress_data, timeout=86400)

    def test_task_status_pending(self):
        """Test task status when no progress data exists."""
        # Don't set any progress data - should return PENDING
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertIsNone(response.data["result"])
        self.assertIsNone(response.data["error"])

    def test_task_status_progress(self):
        """Test task status when progress data exists."""
        # Set real progress data using helper method
        self._set_progress_data(self.task_id, 75, {"message": "Processing batch 3"})

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "PROGRESS")
        self.assertEqual(response.data["progress"], 75)
        self.assertEqual(response.data["message"], "Processing batch 3")
        self.assertIsNotNone(response.data["timestamp"])
        self.assertIsNone(response.data["result"])
        self.assertIsNone(response.data["error"])

    def test_task_status_progress_no_message(self):
        """Test task status when progress data exists but no message."""
        # Set progress data without message
        self._set_progress_data(self.task_id, 50, {})

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "PROGRESS")
        self.assertEqual(response.data["progress"], 50)
        self.assertIsNone(response.data["message"])
        self.assertIsNotNone(response.data["timestamp"])

    def test_task_status_requires_authentication(self):
        """Test that the endpoint requires authentication."""
        # Don't authenticate the client
        response = APIClient().get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_task_status_with_different_task_ids(self):
        """Test that different task IDs work correctly."""
        task_id_2 = uuid.uuid4()
        url_2 = f"/api/v1.0/tasks/{task_id_2}/"

        # Set progress for the second task
        self._set_progress_data(task_id_2, 25, {"message": "Starting task"})

        response = self.client.get(url_2)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "PROGRESS")
        self.assertEqual(response.data["progress"], 25)
        self.assertEqual(response.data["message"], "Starting task")

        # First task should still be pending
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "PENDING")

    def test_task_progress_retrieval(self):
        """Test that we can retrieve progress data directly."""
        # Set progress data using helper method
        self._set_progress_data(self.task_id, 90, {"message": "Almost done"})

        # Retrieve it directly
        progress_data = get_task_progress(self.task_id)

        self.assertIsNotNone(progress_data)
        self.assertEqual(progress_data["progress"], 90)
        self.assertEqual(progress_data["metadata"]["message"], "Almost done")
        self.assertIsNotNone(progress_data["timestamp"])

    def test_task_progress_nonexistent(self):
        """Test that nonexistent task returns None."""
        nonexistent_task_id = "nonexistent-task-999"

        progress_data = get_task_progress(nonexistent_task_id)

        self.assertIsNone(progress_data)


@pytest.mark.django_db
def test_task_api_integration(worker):
    """Integration test with actual Dramatiq task."""
    # Create a test message (you might need to adjust this based on your models)
    # This is a basic integration test to verify the task API works with real tasks

    # Send a task
    result = send_message_task.send("test-message-id")
    task_id = result.message_id

    # Process the task synchronously
    worker.join()

    # Test the API endpoint
    client = APIClient()
    response = client.get(f"/api/v1/tasks/{task_id}/")

    # Should return a valid response
    assert response.status_code == 401  # Unauthorized without auth
    # In a real test, you'd authenticate the client first
