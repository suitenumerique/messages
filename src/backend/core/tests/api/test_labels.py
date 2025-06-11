"""Tests for the label API endpoints."""

# pylint: disable=redefined-outer-name
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models
from core.factories import (
    LabelFactory,
    MailboxFactory,
    ThreadFactory,
    UserFactory,
)


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a test mailbox with admin access for the user."""
    mailbox = MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
    return mailbox


@pytest.fixture
def api_client(user):
    """Create an authenticated API client."""

    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def label(mailbox):
    """Create a test label."""
    return LabelFactory(mailbox=mailbox)


@pytest.mark.django_db
class TestLabelSerializer:
    """Test the LabelSerializer."""

    @pytest.mark.parametrize(
        "role", [models.MailboxRoleChoices.ADMIN, models.MailboxRoleChoices.EDITOR]
    )
    def test_create_label_valid_data(self, api_client, role, user):
        """Test creating a label with valid data."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=role)
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert models.Label.objects.count() == 1

        label = models.Label.objects.first()
        assert label.name == "Work/Projects"
        assert label.slug == "work-projects"
        assert label.color == "#FF0000"
        assert label.mailbox == mailbox

    @pytest.mark.parametrize("role", [models.MailboxRoleChoices.VIEWER])
    def test_create_label_invalid_mailbox_access(self, api_client, role, user):
        """Test creating a label for a mailbox the user doesn't have proper access to."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=role)
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You don't have access to this mailbox" in str(response.data["detail"])

    def test_create_label_mailbox_no_access(self, api_client):
        """Test creating a label for a mailbox the user doesn't have access to."""
        other_mailbox = MailboxFactory()
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects",
            "mailbox": str(other_mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You don't have access to this mailbox" in str(response.data["detail"])

    def test_create_label_missing_required_fields(self, api_client):
        """Test creating a label with missing required fields."""
        url = reverse("labels-list")
        data = {"color": "#FF0000"}  # Missing name and mailbox

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data
        assert "mailbox" in response.data

    def test_create_label_duplicate_name_in_mailbox(self, api_client, mailbox):
        """Test creating a label with a name that already exists in the mailbox."""
        LabelFactory(name="Work", mailbox=mailbox)
        url = reverse("labels-list")
        data = {
            "name": "Work",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Label with this Slug and Mailbox already exists." in str(
            response.data["__all__"]
        )


@pytest.mark.django_db
class TestLabelViewSet:
    """Test the LabelViewSet."""

    def test_list_labels(self, api_client, mailbox):
        """Test listing labels."""
        # Create exactly 3 labels
        LabelFactory.create_batch(3, mailbox=mailbox)
        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3

    def test_list_labels_filter_by_mailbox(self, api_client, mailbox, user):
        """Test listing labels filtered by mailbox."""
        # Create exactly one label in the target mailbox
        LabelFactory(mailbox=mailbox)
        other_mailbox = MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        LabelFactory(mailbox=other_mailbox)

        url = reverse("labels-list")
        response = api_client.get(url, {"mailbox_id": str(mailbox.id)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_update_label(self, api_client, mailbox, label):
        """Test updating a label."""
        url = reverse("labels-detail", args=[label.pk])
        data = {
            "name": "Updated Label",
            "mailbox": str(mailbox.id),
            "color": "#00FF00",
        }

        response = api_client.put(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK

        label.refresh_from_db()
        assert label.name == "Updated Label"
        assert label.slug == "updated-label"
        assert label.color == "#00FF00"

    def test_update_label_no_access(self, api_client, mailbox, label):
        """Test updating a label when user doesn't have proper access."""
        # Create a new user without access
        other_user = UserFactory()
        api_client.force_authenticate(user=other_user)

        url = reverse("labels-detail", args=[label.pk])
        data = {
            "name": "Updated Label",
            "mailbox": str(mailbox.id),
            "color": "#00FF00",
        }

        response = api_client.put(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You don't have access to this mailbox" in str(response.data["detail"])

    def test_delete_label(self, api_client, label):
        """Test deleting a label."""
        url = reverse("labels-detail", args=[label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Label.objects.filter(pk=label.pk).exists()

    def test_delete_label_no_access(self, api_client, label):
        """Test deleting a label when user doesn't have proper access."""
        # Create a new user without access
        other_user = UserFactory()
        api_client.force_authenticate(user=other_user)

        url = reverse("labels-detail", args=[label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You don't have access to this mailbox" in str(response.data["detail"])
        assert models.Label.objects.filter(pk=label.pk).exists()

    def test_add_threads_to_label(self, api_client, mailbox, label):
        """Test adding threads to a label."""
        threads = ThreadFactory.create_batch(3)
        for thread in threads:
            thread.accesses.create(
                mailbox=mailbox,
                role=models.ThreadAccessRoleChoices.EDITOR,
            )

        url = reverse("labels-add-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id) for thread in threads]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 3

    def test_add_threads_to_label_invalid_access(self, api_client, label):
        """Test adding threads to a label when user doesn't have access."""
        other_mailbox = MailboxFactory()
        thread = ThreadFactory()
        thread.accesses.create(
            mailbox=other_mailbox,
            role=models.ThreadAccessRoleChoices.EDITOR,
        )

        url = reverse("labels-add-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id)]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 0  # Thread not added

    def test_remove_threads_from_label(self, api_client, mailbox, label):
        """Test removing threads from a label."""
        threads = ThreadFactory.create_batch(3)
        for thread in threads:
            thread.accesses.create(
                mailbox=mailbox,
                role=models.ThreadAccessRoleChoices.EDITOR,
            )
            label.threads.add(thread)

        url = reverse("labels-remove-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id) for thread in threads]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 0

    def test_remove_threads_from_label_invalid_access(self, api_client, label):
        """Test removing threads from a label when user doesn't have access."""
        other_mailbox = MailboxFactory()
        thread = ThreadFactory()
        thread.accesses.create(
            mailbox=other_mailbox,
            role=models.ThreadAccessRoleChoices.EDITOR,
        )
        label.threads.add(thread)

        url = reverse("labels-remove-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id)]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 1  # Thread not removed

    def test_label_hierarchy(self, mailbox):
        """Test label hierarchy with slash-based naming."""
        parent_label = LabelFactory(name="Work", mailbox=mailbox)
        child_label = LabelFactory(name="Work/Projects", mailbox=mailbox)

        assert parent_label.parent_name is None
        assert parent_label.basename == "Work"
        assert parent_label.depth == 0

        assert child_label.parent_name == "Work"
        assert child_label.basename == "Projects"
        assert child_label.depth == 1

    def test_label_unique_constraint(self, api_client, mailbox):
        """Test that labels must have unique names within a mailbox."""
        models.Label.objects.all().delete()
        LabelFactory(name="Work", mailbox=mailbox)
        url = reverse("labels-list")
        data = {
            "name": "Work",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }
        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Label with this Slug and Mailbox already exists." in str(
            response.data["__all__"]
        )

    def test_list_labels_hierarchical_structure(self, api_client, mailbox, user):
        """Test that labels are returned in a proper hierarchical structure."""
        # Create a hierarchical structure of labels
        LabelFactory(mailbox=mailbox, name="Root1", color="#FF0000")
        LabelFactory(mailbox=mailbox, name="Root1/Child1", color="#00FF00")
        LabelFactory(mailbox=mailbox, name="Root1/Child2", color="#0000FF")
        LabelFactory(mailbox=mailbox, name="Root2", color="#FFFF00")
        
        # Create labels in another mailbox
        other_mailbox = MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        LabelFactory(mailbox=other_mailbox, name="Root3", color="#FF00FF")
        LabelFactory(
            mailbox=other_mailbox, name="Root3/Child1", color="#00FFFF"
        )

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Should only get labels from mailboxes user has access to
        assert len(data) == 3  # Root1, Root2, Root3

        # Find Root1 and verify its structure
        root1_data = next(label for label in data if label["name"] == "Root1")
        assert len(root1_data["children"]) == 2
        assert root1_data["color"] == "#FF0000"
        assert root1_data["display_name"] == "Root1"

        # Verify children are sorted alphabetically
        assert root1_data["children"][0]["name"] == "Root1/Child1"
        assert root1_data["children"][1]["name"] == "Root1/Child2"

        # Verify Root2 has no children
        root2_data = next(label for label in data if label["name"] == "Root2")
        assert len(root2_data["children"]) == 0

        # Verify Root3 and its child
        root3_data = next(label for label in data if label["name"] == "Root3")
        assert len(root3_data["children"]) == 1
        assert root3_data["children"][0]["name"] == "Root3/Child1"

    def test_list_labels_hierarchical_filter_by_mailbox(
        self, api_client, mailbox, user
    ):
        """Test filtering hierarchical labels by mailbox_id."""
        # Create labels in mailbox1
        LabelFactory(mailbox=mailbox, name="Root1", color="#FF0000")
        LabelFactory(mailbox=mailbox, name="Root1/Child1", color="#00FF00")
        LabelFactory(mailbox=mailbox, name="Root2", color="#FFFF00")
        
        # Create labels in another mailbox
        other_mailbox = MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        LabelFactory(mailbox=other_mailbox, name="Root3", color="#FF00FF")

        url = reverse("labels-list")

        # Test filtering by mailbox1
        response = api_client.get(f"{url}?mailbox_id={mailbox.id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Should only get labels from mailbox1
        assert len(data) == 2  # Root1, Root2
        assert all(label["name"] in ["Root1", "Root2"] for label in data)

        # Test filtering by other_mailbox
        response = api_client.get(f"{url}?mailbox_id={other_mailbox.id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Should only get labels from other_mailbox
        assert len(data) == 1  # Root3
        assert data[0]["name"] == "Root3"

    def test_list_labels_hierarchical_inaccessible_mailbox(
        self, api_client, mailbox, user
    ):
        """Test that labels from inaccessible mailboxes are not returned in hierarchical view."""
        # Create a label in an inaccessible mailbox
        inaccessible_mailbox = MailboxFactory()
        LabelFactory(
            mailbox=inaccessible_mailbox, name="Inaccessible", color="#000000"
        )

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify inaccessible label is not in the response
        assert not any(label["name"] == "Inaccessible" for label in data)

        # Try to filter by inaccessible mailbox
        response = api_client.get(f"{url}?mailbox_id={inaccessible_mailbox.id}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 0

    def test_list_labels_hierarchical_unauthorized(self, api_client):
        """Test that unauthorized users cannot access hierarchical labels."""
        api_client.force_authenticate(user=None)
        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_labels_hierarchical_deep_nesting(self, api_client, mailbox):
        """Test handling of deeply nested labels."""
        # Create a deeply nested label structure
        LabelFactory(mailbox=mailbox, name="Level1", color="#FF0000")
        LabelFactory(mailbox=mailbox, name="Level1/Level2", color="#00FF00")
        LabelFactory(mailbox=mailbox, name="Level1/Level2/Level3", color="#0000FF")
        LabelFactory(
            mailbox=mailbox, name="Level1/Level2/Level3/Level4", color="#FFFF00"
        )

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify the hierarchy is maintained
        level1 = next(label for label in data if label["name"] == "Level1")
        assert len(level1["children"]) == 1

        level2 = level1["children"][0]
        assert level2["name"] == "Level1/Level2"
        assert len(level2["children"]) == 1

        level3 = level2["children"][0]
        assert level3["name"] == "Level1/Level2/Level3"
        assert len(level3["children"]) == 1

        level4 = level3["children"][0]
        assert level4["name"] == "Level1/Level2/Level3/Level4"
        assert len(level4["children"]) == 0

    def test_list_labels_hierarchical_special_characters(self, api_client, mailbox):
        """Test handling of labels with special characters in names."""
        LabelFactory(mailbox=mailbox, name="Root/With/Slashes", color="#FF0000")
        LabelFactory(mailbox=mailbox, name="Root/With/Special@Chars", color="#00FF00")
        LabelFactory(mailbox=mailbox, name="Root/With/Spaces And More", color="#0000FF")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify the hierarchy is maintained with special characters
        root = next(label for label in data if label["name"] == "Root/With/Slashes")
        assert root["display_name"] == "Slashes"

        special = next(
            label for label in data if label["name"] == "Root/With/Special@Chars"
        )
        assert special["display_name"] == "Special@Chars"

        spaces = next(
            label for label in data if label["name"] == "Root/With/Spaces And More"
        )
        assert spaces["display_name"] == "Spaces And More"
