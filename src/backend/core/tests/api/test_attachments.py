"""Tests for the blob (attachment) API."""

import hashlib
import random
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
from core.enums import MailboxRoleChoices


@pytest.mark.django_db
class TestBlobAPI:
    """Tests for the blob API endpoints."""

    @pytest.fixture
    def api_client(self):
        """Return an authenticated API client."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def api_client2(self):
        """Return an authenticated API client."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def user_mailbox(self, api_client):
        """Create a mailbox for the test user with sender access."""
        _, user = api_client
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=MailboxRoleChoices.EDITOR,
        )
        return mailbox

    def _create_test_file(self, filename="test.txt", content=b"Test file content"):
        """Helper to create a test file for upload."""
        test_file = SimpleUploadedFile(
            name=filename, content=content, content_type="text/plain"
        )
        return test_file

    def test_upload_download_blob(self, api_client, api_client2, user_mailbox):
        """Test uploading a file to create a blob and downloading it."""
        client, _ = api_client
        client2, _ = api_client2

        # Create a test file with random content to ensure uniqueness
        file_content = b"Random test content: %i" % random.randint(0, 10000000)

        # Calculate expected hash
        expected_hash = hashlib.sha256(file_content).hexdigest()

        # Upload via API
        url = reverse("blob-upload", kwargs={"mailbox_id": user_mailbox.id})

        # Create a fresh file for the request
        request_file = self._create_test_file(content=file_content)

        # Upload using multipart/form-data
        response = client.post(url, {"file": request_file}, format="multipart")

        # Check response
        assert response.status_code == status.HTTP_201_CREATED
        assert "blobId" in response.data
        assert response.data["sha256"] == expected_hash
        assert response.data["type"] == "text/plain"
        assert response.data["size"] == len(file_content)

        # Verify the blob was created in the database
        blob_id = uuid.UUID(response.data["blobId"])
        blob = models.Blob.objects.get(id=blob_id)
        assert blob.content_type == "text/plain"
        assert blob.sha256.hex() == expected_hash
        assert blob.size == len(file_content)
        assert blob.mailbox == user_mailbox

        # Download via API
        url = reverse("blob-download", kwargs={"pk": uuid.uuid4()})
        response = client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Download via API
        url = reverse("blob-download", kwargs={"pk": blob.id})
        response = client.get(url)

        # Check response
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/plain"
        assert (
            response["Content-Disposition"]
            == f'attachment; filename="blob-{blob.id}.bin"'
        )
        assert response.content == file_content

        # Download with another user
        response = client2.get(url)

        # Should be denied
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "role",
        [
            MailboxRoleChoices.EDITOR,
            MailboxRoleChoices.SENDER,
            MailboxRoleChoices.ADMIN,
        ],
    )
    def test_upload_blob_with_edit_roles(self, role):
        """Test that EDITOR, SENDER, and ADMIN can all upload blobs."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        mailbox = factories.MailboxFactory()

        def _post():
            url = reverse("blob-upload", kwargs={"mailbox_id": mailbox.id})
            return client.post(
                url, {"file": self._create_test_file()}, format="multipart"
            )

        response = _post()

        # Should be denied if there is no access
        assert response.status_code == status.HTTP_403_FORBIDDEN

        access = factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=MailboxRoleChoices.VIEWER
        )

        # Should be denied if there is only VIEWER access
        response = _post()
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Elevate to the parametrized role and verify success
        access.role = role
        access.save()

        # Should be allowed if there is access
        response = _post()
        assert response.status_code == status.HTTP_201_CREATED

    def test_upload_permission_denied(self, api_client):
        """Test permission check when user doesn't have access to the mailbox."""
        client, _ = api_client

        # Create a mailbox the user doesn't have access to
        mailbox = factories.MailboxFactory()

        # Try to upload
        url = reverse("blob-upload", kwargs={"mailbox_id": mailbox.id})
        response = client.post(
            url, {"file": self._create_test_file()}, format="multipart"
        )

        # Should be denied
        assert response.status_code == status.HTTP_403_FORBIDDEN
