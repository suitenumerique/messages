"""Tests for the label API endpoints."""

# pylint: disable=redefined-outer-name, unused-argument, too-many-public-methods, too-many-lines
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, models
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
        "role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_create_label_valid_data(self, api_client, role, user):
        """Test creating a label with valid data."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=role)
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects/Urgent",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # there should be 3 labels created: Work, Work/Projects, Work/Projects/Urgent
        assert models.Label.objects.count() == 3

        label = models.Label.objects.get(name="Work/Projects/Urgent")
        assert label.name == "Work/Projects/Urgent"
        assert label.slug == "work-projects-urgent"
        assert label.color == "#FF0000"
        assert label.mailbox == mailbox

        assert label.parent_name == "Work/Projects"
        parent = models.Label.objects.get(name="Work/Projects")
        assert parent.parent_name == "Work"
        assert parent.color == "#FF0000"
        assert parent.mailbox == mailbox

        grandparent = models.Label.objects.get(name="Work")
        assert grandparent.parent_name is None
        assert grandparent.color == "#FF0000"
        assert grandparent.mailbox == mailbox

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_create_label_valid_data_similar_to_existing_parent(
        self, api_client, role, user
    ):
        """Test creating a label with valid data."""
        mailbox = MailboxFactory()

        # create a label with the same name as a parent
        LabelFactory(name="Work", mailbox=mailbox, color="#000000")
        assert models.Label.objects.count() == 1

        mailbox.accesses.create(user=user, role=role)
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects/Urgent",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # there should be 2 more labels created: Work/Projects, Work/Projects/Urgent
        assert models.Label.objects.count() == 3

        label = models.Label.objects.get(name="Work/Projects/Urgent")
        assert label.name == "Work/Projects/Urgent"
        assert label.slug == "work-projects-urgent"
        assert label.color == "#FF0000"
        assert label.mailbox == mailbox

        assert label.parent_name == "Work/Projects"
        parent = models.Label.objects.get(name="Work/Projects")
        assert parent.parent_name == "Work"
        assert parent.color == "#FF0000"
        assert parent.mailbox == mailbox

        grandparent = models.Label.objects.get(name="Work")
        assert grandparent.parent_name is None
        assert grandparent.color == "#000000"
        assert grandparent.mailbox == mailbox

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
        assert "Label with this Slug and Mailbox already exists" in str(
            response.data["__all__"]
        )

    def test_create_label_with_parents(self, api_client, mailbox):
        """Test that creating a label with slashes automatically creates parent labels."""
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects/Urgent",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # Verify all labels were created
        assert models.Label.objects.filter(name="Work").exists()
        assert models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.filter(name="Work/Projects/Urgent").exists()

        # Verify colors
        work_label = models.Label.objects.get(name="Work")
        projects_label = models.Label.objects.get(name="Work/Projects")
        urgent_label = models.Label.objects.get(name="Work/Projects/Urgent")

        assert work_label.color == "#FF0000"
        assert projects_label.color == "#FF0000"
        assert urgent_label.color == "#FF0000"

    def test_create_label_with_existing_parents(self, api_client, mailbox):
        """Test creating a label when some parent labels already exist."""
        # Create some existing parent labels
        LabelFactory(mailbox=mailbox, name="Work", color="#0000FF")
        LabelFactory(mailbox=mailbox, name="Work/Projects", color="#00FF00")

        url = reverse("labels-list")
        data = {
            "name": "Work/Projects/New",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # Verify existing labels weren't modified
        work_label = models.Label.objects.get(name="Work")
        projects_label = models.Label.objects.get(name="Work/Projects")
        assert work_label.color == "#0000FF"
        assert projects_label.color == "#00FF00"

        # Verify new label was created
        new_label = models.Label.objects.get(name="Work/Projects/New")
        assert new_label.color == "#FF0000"

    def test_create_label_with_same_name_as_parent(self, api_client, mailbox):
        """Test creating a label that has the same name as a potential parent."""
        # First create a parent label
        LabelFactory(mailbox=mailbox, name="Work/Projects", color="#0000FF")

        # Try to create a label with the same name
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects",  # Same name as existing label
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Label with this Slug and Mailbox already exists" in str(
            response.data["__all__"]
        )

    def test_create_label_with_special_characters(self, api_client, mailbox):
        """Test creating labels with special characters in the hierarchy."""
        url = reverse("labels-list")
        data = {
            "name": "Root/With/Special@Chars/And Spaces",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # Verify all labels were created with proper names
        assert models.Label.objects.filter(name="Root").exists()
        assert models.Label.objects.filter(name="Root/With").exists()
        assert models.Label.objects.filter(name="Root/With/Special@Chars").exists()
        assert models.Label.objects.filter(
            name="Root/With/Special@Chars/And Spaces"
        ).exists()

        # Verify slugs were generated correctly
        root_label = models.Label.objects.get(name="Root")
        special_label = models.Label.objects.get(name="Root/With/Special@Chars")
        spaces_label = models.Label.objects.get(
            name="Root/With/Special@Chars/And Spaces"
        )

        assert root_label.slug == "root"
        assert special_label.slug == "root-with-specialchars"
        assert spaces_label.slug == "root-with-specialchars-and-spaces"

    def test_create_label_in_different_mailbox(self, api_client, mailbox, user):
        """Test creating labels with hierarchy across different mailboxes."""
        # Create another mailbox
        other_mailbox = MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)

        # Create a label in the first mailbox
        LabelFactory(mailbox=mailbox, name="Work", color="#0000FF")

        # Try to create a label in the second mailbox with same hierarchy
        url = reverse("labels-list")
        data = {
            "name": "Work/Projects",
            "mailbox": str(other_mailbox.id),
            "color": "#FF0000",
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

        # Verify labels were created in correct mailboxes
        assert models.Label.objects.filter(name="Work", mailbox=mailbox).exists()
        assert models.Label.objects.filter(name="Work", mailbox=other_mailbox).exists()
        assert models.Label.objects.filter(
            name="Work/Projects", mailbox=other_mailbox
        ).exists()
        assert not models.Label.objects.filter(
            name="Work/Projects", mailbox=mailbox
        ).exists()

    def test_create_label_response(self, api_client, mailbox):
        """Test that creating a label returns the new created label."""
        # Create some existing labels first
        LabelFactory(mailbox=mailbox, name="Existing", color="#000000")
        LabelFactory(mailbox=mailbox, name="Existing/Child", color="#111111")

        url = reverse("labels-list")
        data = {
            "name": "Existing/Child/Nested",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }

        assert models.Label.objects.count() == 2
        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()

        assert models.Label.objects.count() == 3

        assert data["color"] == "#FF0000"
        assert data["name"] == "Existing/Child/Nested"
        assert data["slug"] == "existing-child-nested"
        assert data["mailbox"] == str(mailbox.id)


@pytest.mark.django_db
class TestLabelViewSet:
    """Test the LabelViewSet."""

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.VIEWER,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_list_labels(self, api_client, role, user):
        """Test listing labels. All users with access to the mailbox should be able to list labels."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=role)
        # Create exactly 3 labels
        LabelFactory.create_batch(3, mailbox=mailbox)
        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 3  # The response is a list of labels

    def test_list_labels_no_access(self, api_client, user):
        """Test listing labels when user doesn't have access to the mailbox."""
        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 0

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
        data = response.json()
        assert len(data) == 1  # Should only get the label from the target mailbox

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_update_label(self, api_client, label, role, user):
        """Test updating a label."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=role)  # Add access to the mailbox
        label.mailbox = mailbox  # Set the label's mailbox to the new mailbox
        label.save()  # Save the label to the new mailbox
        url = reverse("labels-detail", args=[label.pk])
        data = {
            "name": "Updated Label",
            "mailbox": str(mailbox.id),
            "color": "#00FF00",
        }

        response = api_client.patch(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK

        label.refresh_from_db()
        assert label.name == "Updated Label"
        assert label.slug == "updated-label"
        assert label.color == "#00FF00"

        # Test partial update, we only change color
        new_data = {
            "color": "#CCCCCC",
        }
        response = api_client.patch(url, new_data, format="json")
        assert response.status_code == status.HTTP_200_OK
        label.refresh_from_db()
        assert label.color == "#CCCCCC"

    def test_update_label_with_similar_name_to_existing_label(
        self, api_client, label, user
    ):
        """Test updating a label with a similar name to an existing label."""
        LabelFactory(name="Work", mailbox=label.mailbox)
        url = reverse("labels-detail", args=[label.pk])
        data = {
            "name": "Work",
        }
        response = api_client.patch(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Label with this Slug and Mailbox already exists" in str(response.data)

    def test_update_label_access_denied(self, api_client, label, user):
        """Test updating a label when user doesn't have proper access."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        label.mailbox = mailbox
        label.save()

        url = reverse("labels-detail", args=[label.pk])
        data = {
            "name": "Updated Label",
            "mailbox": str(mailbox.id),
            "color": "#00FF00",
        }

        response = api_client.put(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You need EDITOR, SENDER or ADMIN role to manage labels" in str(
            response.data["detail"]
        )

    def test_update_label_no_access(self, api_client, mailbox, label):
        """Test updating a label when user doesn't have any access."""
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

    def test_delete_label_access_denied(self, api_client, label, user):
        """Test deleting a label when user doesn't have proper access."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        label.mailbox = mailbox
        label.save()
        url = reverse("labels-detail", args=[label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You need EDITOR, SENDER or ADMIN role to manage labels" in str(
            response.data["detail"]
        )

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

    @pytest.mark.parametrize(
        "role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_add_threads_to_label(self, api_client, label, role):
        """Test adding threads to a label."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=label.mailbox.accesses.first().user, role=role)
        threads = ThreadFactory.create_batch(3)
        for thread in threads:
            thread.accesses.create(
                mailbox=mailbox,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )

        url = reverse("labels-add-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id) for thread in threads]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 3

    def test_add_threads_to_label_access_denied(self, api_client, label, user):
        """Test adding threads to a label when user doesn't have proper access."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        label.mailbox = mailbox
        label.save()

        thread = ThreadFactory()
        thread.accesses.create(
            mailbox=mailbox, role=models.ThreadAccessRoleChoices.VIEWER
        )

        url = reverse("labels-add-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id)]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You need EDITOR, SENDER or ADMIN role to manage labels" in str(
            response.data["detail"]
        )
        assert label.threads.count() == 0  # Thread not added

    def test_add_threads_to_label_no_access(self, api_client, label, user):
        """Test adding threads to a label when user doesn't have any access."""
        mailbox = MailboxFactory()
        label.mailbox = mailbox
        label.save()

        thread = ThreadFactory()
        thread.accesses.create(
            mailbox=mailbox, role=models.ThreadAccessRoleChoices.VIEWER
        )

        url = reverse("labels-add-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id)]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "You don't have access to this mailbox" in str(response.data["detail"])
        assert label.threads.count() == 0  # Thread not added

    @pytest.mark.parametrize(
        "mailbox_role",
        [
            models.MailboxRoleChoices.ADMIN,
            models.MailboxRoleChoices.EDITOR,
            models.MailboxRoleChoices.SENDER,
        ],
    )
    def test_remove_threads_from_label(self, api_client, label, mailbox_role):
        """Test removing threads from a label."""
        mailbox = MailboxFactory()
        mailbox.accesses.create(
            user=label.mailbox.accesses.first().user, role=mailbox_role
        )
        threads = ThreadFactory.create_batch(3)
        for thread in threads:
            thread.accesses.create(
                mailbox=mailbox,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )
            label.threads.add(thread)

        url = reverse("labels-remove-threads", args=[label.pk])
        data = {"thread_ids": [str(thread.id) for thread in threads]}

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert label.threads.count() == 0

    def test_remove_threads_from_label_access_denied(self, api_client, label):
        """Test removing threads from a label when user doesn't have access."""
        other_mailbox = MailboxFactory()
        thread = ThreadFactory()
        thread.accesses.create(
            mailbox=other_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
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
        LabelFactory(name="Work", mailbox=mailbox)
        assert models.Label.objects.count() == 1
        url = reverse("labels-list")
        data = {
            "name": "Work",
            "mailbox": str(mailbox.id),
            "color": "#FF0000",
        }
        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Label with this Slug and Mailbox already exists" in str(response.data)
        assert models.Label.objects.count() == 1

    def test_rename_parent_label_has_to_update_children(
        self,
        api_client,
        mailbox,
        user,
    ):
        """
        Test that when a parent label is renamed, its children should also
        be renamed to maintain the hierarchy.
        """
        api_client.force_authenticate(user=user)

        # Create a parent label and child labels
        parent_label = LabelFactory(name="Work", mailbox=mailbox)
        child_label1 = LabelFactory(name="Work/Projects", mailbox=mailbox)
        child_label2 = LabelFactory(name="Work/Meetings", mailbox=mailbox)
        grandchild_label = LabelFactory(name="Work/Projects/Urgent", mailbox=mailbox)

        # Verify initial state
        assert models.Label.objects.filter(name="Work").exists()
        assert models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.filter(name="Work/Meetings").exists()
        assert models.Label.objects.filter(name="Work/Projects/Urgent").exists()

        # Rename the parent label from "Work" to "Job"
        url = reverse("labels-detail", kwargs={"pk": str(parent_label.id)})
        data = {"name": "Job", "mailbox": str(mailbox.id)}

        response = api_client.put(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Verify the parent label was renamed
        parent_label.refresh_from_db()
        assert parent_label.name == "Job"
        assert parent_label.slug == "job"

        child_label1.refresh_from_db()
        child_label2.refresh_from_db()
        grandchild_label.refresh_from_db()

        assert child_label1.name == "Job/Projects"
        assert child_label2.name == "Job/Meetings"
        assert grandchild_label.name == "Job/Projects/Urgent"

        assert models.Label.objects.filter(name="Job/Projects").exists()
        assert models.Label.objects.filter(name="Job/Meetings").exists()
        assert models.Label.objects.filter(name="Job/Projects/Urgent").exists()
        assert not models.Label.objects.filter(name="Work").exists()
        assert not models.Label.objects.filter(name="Work/Projects").exists()
        assert not models.Label.objects.filter(name="Work/Meetings").exists()
        assert not models.Label.objects.filter(name="Work/Projects/Urgent").exists()

        # edit middle label
        url = reverse("labels-detail", kwargs={"pk": str(child_label1.id)})
        data = {"name": "Job/Bidule", "mailbox": str(mailbox.id)}
        response = api_client.put(url, data, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Refresh the labels to get updated data
        child_label1.refresh_from_db()
        grandchild_label.refresh_from_db()

        # Verify the middle label was renamed
        assert child_label1.name == "Job/Bidule"
        assert child_label1.slug == "job-bidule"
        assert child_label1.parent_name == "Job"
        assert child_label1.depth == 1

        # Verify the grandchild was also renamed
        assert grandchild_label.name == "Job/Bidule/Urgent"

        # Verify new labels exist
        assert models.Label.objects.filter(name="Job/Bidule").exists()
        assert models.Label.objects.filter(name="Job").exists()
        assert models.Label.objects.filter(name="Job/Meetings").exists()
        assert models.Label.objects.filter(name="Job/Bidule/Urgent").exists()

        # Verify old labels no longer exist
        assert not models.Label.objects.filter(name="Work").exists()
        assert not models.Label.objects.filter(name="Work/Projects").exists()
        assert not models.Label.objects.filter(name="Work/Meetings").exists()
        assert not models.Label.objects.filter(name="Work/Projects/Urgent").exists()
        assert not models.Label.objects.filter(name="Job/Projects").exists()
        assert not models.Label.objects.filter(name="Job/Projects/Urgent").exists()

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
        LabelFactory(mailbox=other_mailbox, name="Root3/Child1", color="#00FFFF")

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
        LabelFactory(mailbox=inaccessible_mailbox, name="Inaccessible", color="#000000")

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
        # Create the complete label hierarchy
        LabelFactory(mailbox=mailbox, name="Root", color="#000000")
        LabelFactory(
            mailbox=mailbox, name="Root/With", color="#CCCCCC"
        )  # Create intermediate label
        LabelFactory(mailbox=mailbox, name="Root/With/Slashes", color="#FF0000")
        LabelFactory(mailbox=mailbox, name="Root/With/Special@Chars", color="#00FF00")
        LabelFactory(mailbox=mailbox, name="Root/With/Spaces And More", color="#0000FF")

        # Verify labels were created in the database
        assert models.Label.objects.filter(name="Root").exists()
        assert models.Label.objects.filter(name="Root/With").exists()
        assert models.Label.objects.filter(name="Root/With/Slashes").exists()
        assert models.Label.objects.filter(name="Root/With/Special@Chars").exists()
        assert models.Label.objects.filter(name="Root/With/Spaces And More").exists()

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Find the root label that should contain our special character labels
        root_label = next((label for label in data if label["name"] == "Root"), None)
        assert root_label is not None, "Root label not found in response"

        # Verify the hierarchy
        assert len(root_label["children"]) == 1, "Root should have one child (With)"
        with_label = root_label["children"][0]
        assert with_label["name"] == "Root/With"
        assert len(with_label["children"]) == 3, "With label should have three children"

        # Convert children to dict for easier lookup
        children_by_name = {child["name"]: child for child in with_label["children"]}

        # Verify each special character label exists and has correct display name
        assert "Root/With/Slashes" in children_by_name
        assert children_by_name["Root/With/Slashes"]["display_name"] == "Slashes"

        assert "Root/With/Special@Chars" in children_by_name
        assert (
            children_by_name["Root/With/Special@Chars"]["display_name"]
            == "Special@Chars"
        )

        assert "Root/With/Spaces And More" in children_by_name
        assert (
            children_by_name["Root/With/Spaces And More"]["display_name"]
            == "Spaces And More"
        )

    def test_delete_label_cascades_to_children(self, api_client, mailbox, user):
        """Test that deleting a parent label also deletes all its child labels."""
        # Create a hierarchical structure of labels
        LabelFactory(mailbox=mailbox, name="Work/Meetings", color="#0000FF")
        LabelFactory(mailbox=mailbox, name="Work/Projects/Urgent", color="#FFFF00")

        # Verify all labels exist
        parent_label = models.Label.objects.get(name="Work")
        assert models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.filter(name="Work/Meetings").exists()
        assert models.Label.objects.filter(name="Work/Projects/Urgent").exists()
        assert models.Label.objects.count() == 4

        # Delete the parent label
        url = reverse("labels-detail", args=[parent_label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify all child labels are also deleted
        assert not models.Label.objects.filter(name="Work").exists()
        assert not models.Label.objects.filter(name="Work/Projects").exists()
        assert not models.Label.objects.filter(name="Work/Meetings").exists()
        assert not models.Label.objects.filter(name="Work/Projects/Urgent").exists()
        assert models.Label.objects.count() == 0

    def test_delete_label_without_children(self, api_client, mailbox, user):
        """Test that deleting a label without children works normally."""
        # Create a simple label without children
        label = LabelFactory(mailbox=mailbox, name="Simple", color="#FF0000")
        assert models.Label.objects.count() == 1

        # Delete the label
        url = reverse("labels-detail", args=[label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify the label is deleted
        assert not models.Label.objects.filter(name="Simple").exists()
        assert models.Label.objects.count() == 0

    def test_delete_child_label_does_not_affect_parent(self, api_client, mailbox, user):
        """Test that deleting a child label does not affect its parent."""
        # Create parent and child labels
        LabelFactory(mailbox=mailbox, name="Work", color="#FF0000")
        child_label = LabelFactory(
            mailbox=mailbox, name="Work/Projects", color="#00FF00"
        )

        # Verify both labels exist
        assert models.Label.objects.filter(name="Work").exists()
        assert models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.count() == 2

        # Delete only the child label
        url = reverse("labels-detail", args=[child_label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify only the child is deleted, parent remains
        assert models.Label.objects.filter(name="Work").exists()
        assert not models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.count() == 1

    def test_delete_label_with_threads(self, api_client, mailbox, user):
        """Test that deleting a label removes it from all threads."""
        # Create a label and some threads
        label = LabelFactory(mailbox=mailbox, name="Important", color="#FF0000")
        thread1 = ThreadFactory()
        thread2 = ThreadFactory()

        # Add threads to the label
        label.threads.add(thread1, thread2)
        assert label.threads.count() == 2

        # Delete the label
        url = reverse("labels-detail", args=[label.pk])
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify the label is deleted and threads are no longer associated
        assert not models.Label.objects.filter(name="Important").exists()
        # Threads should still exist but not be associated with the deleted label
        assert models.Thread.objects.count() == 2

    def test_model_level_cascading_deletion(self, mailbox):
        """Test that cascading deletion works at the model level."""
        # Create a hierarchical structure of labels
        LabelFactory(mailbox=mailbox, name="Work/Meetings", color="#0000FF")
        LabelFactory(mailbox=mailbox, name="Work/Projects/Urgent", color="#FFFF00")

        # Verify all labels exist
        parent_label = models.Label.objects.get(name="Work")
        assert models.Label.objects.filter(name="Work/Projects").exists()
        assert models.Label.objects.filter(name="Work/Meetings").exists()
        assert models.Label.objects.filter(name="Work/Projects/Urgent").exists()
        assert models.Label.objects.count() == 4

        # Delete the parent label directly (bypassing the API)
        parent_label.delete()

        # Verify all child labels are also deleted
        assert not models.Label.objects.filter(name="Work").exists()
        assert not models.Label.objects.filter(name="Work/Projects").exists()
        assert not models.Label.objects.filter(name="Work/Meetings").exists()
        assert not models.Label.objects.filter(name="Work/Projects/Urgent").exists()
        assert models.Label.objects.count() == 0

    def test_list_labels_alphabetical_order_by_slug(self, api_client, mailbox, user):
        """Test that labels are returned in alphabetical order by slug."""
        # Create labels in random order to test ordering
        LabelFactory(mailbox=mailbox, name="Zebra")
        LabelFactory(mailbox=mailbox, name="Alpha")
        LabelFactory(mailbox=mailbox, name="Charlie")
        LabelFactory(mailbox=mailbox, name="Beta")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify labels are ordered alphabetically by slug
        assert len(data) == 4
        assert data[0]["slug"] == "alpha"
        assert data[1]["slug"] == "beta"
        assert data[2]["slug"] == "charlie"
        assert data[3]["slug"] == "zebra"

    def test_list_labels_alphabetical_order_with_numbers(
        self, api_client, mailbox, user
    ):
        """Test that labels with numbers in slugs are ordered correctly."""
        # Create labels with numbers in different positions
        LabelFactory(mailbox=mailbox, name="Label 10")
        LabelFactory(mailbox=mailbox, name="Label 1")
        LabelFactory(mailbox=mailbox, name="Label 2")
        LabelFactory(mailbox=mailbox, name="10 Label")
        LabelFactory(mailbox=mailbox, name="1 Label")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify alphabetical ordering (not numerical)
        assert len(data) == 5
        assert data[0]["slug"] == "1-label"
        assert data[1]["slug"] == "10-label"
        assert data[2]["slug"] == "label-1"
        assert data[3]["slug"] == "label-10"
        assert data[4]["slug"] == "label-2"

    def test_list_labels_alphabetical_order_with_accents(
        self, api_client, mailbox, user
    ):
        """Test that labels with accented characters are ordered correctly."""
        # Create labels with accented characters
        LabelFactory(mailbox=mailbox, name="État civil")
        LabelFactory(mailbox=mailbox, name="Enfance")
        LabelFactory(mailbox=mailbox, name="Urbanisme")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify alphabetical ordering by slug
        assert len(data) == 3
        assert data[0]["slug"] == "enfance"
        assert data[1]["slug"] == "etat-civil"
        assert data[2]["slug"] == "urbanisme"

    def test_list_labels_alphabetical_order_hierarchical(
        self, api_client, mailbox, user
    ):
        """Test that hierarchical labels maintain alphabetical order within each level."""
        # Create hierarchical labels in random order
        LabelFactory(mailbox=mailbox, name="Work/Meetings")
        LabelFactory(mailbox=mailbox, name="Work/Projects")
        LabelFactory(mailbox=mailbox, name="Personal/Family")
        LabelFactory(mailbox=mailbox, name="Personal/Friends")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify top-level labels are ordered alphabetically
        assert len(data) == 2
        assert data[0]["slug"] == "personal"
        assert data[1]["slug"] == "work"

        # Verify children within each parent are ordered alphabetically
        personal_children = data[0]["children"]
        assert len(personal_children) == 2
        assert personal_children[0]["slug"] == "personal-family"
        assert personal_children[1]["slug"] == "personal-friends"

        work_children = data[1]["children"]
        assert len(work_children) == 2
        assert work_children[0]["slug"] == "work-meetings"
        assert work_children[1]["slug"] == "work-projects"

    def test_list_labels_alphabetical_order_mixed_mailboxes(
        self, api_client, mailbox, user
    ):
        """Test that labels from different mailboxes maintain alphabetical order."""
        # Create another mailbox
        other_mailbox = MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)

        # Create labels in both mailboxes
        LabelFactory(mailbox=mailbox, name="Zebra")
        LabelFactory(mailbox=mailbox, name="Alpha")
        LabelFactory(mailbox=other_mailbox, name="Charlie")
        LabelFactory(mailbox=other_mailbox, name="Beta")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify all labels are ordered alphabetically by slug regardless of mailbox
        assert len(data) == 4
        assert data[0]["slug"] == "alpha"
        assert data[1]["slug"] == "beta"
        assert data[2]["slug"] == "charlie"
        assert data[3]["slug"] == "zebra"

    def test_list_labels_alphabetical_order_case_insensitive(
        self, api_client, mailbox, user
    ):
        """Test that label ordering is case-insensitive."""
        # Create labels with mixed case
        LabelFactory(mailbox=mailbox, name="ZEBRA")
        LabelFactory(mailbox=mailbox, name="alpha")
        LabelFactory(mailbox=mailbox, name="Charlie")
        LabelFactory(mailbox=mailbox, name="BETA")

        url = reverse("labels-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify case-insensitive alphabetical ordering
        assert len(data) == 4
        assert data[0]["slug"] == "alpha"
        assert data[1]["slug"] == "beta"
        assert data[2]["slug"] == "charlie"
        assert data[3]["slug"] == "zebra"
