"""
Test Drive API endpoints in the messages core app.
"""
# pylint: disable=redefined-outer-name

import json
import uuid
from unittest.mock import patch

from django.urls import reverse

import pytest
import responses
from rest_framework import status
from rest_framework.test import APIClient

from core import factories
from core.enums import MailboxRoleChoices, ThreadAccessRoleChoices

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client_with_user():
    """Return an authenticated API client with user and session."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_authenticate(user=user)
    # Setup session with OIDC access token
    session = client.session
    session["oidc_access_token"] = "test-access-token"
    session.save()
    return client, user


@pytest.fixture
def mailbox_with_message(api_client_with_user):
    """Create a mailbox with a message containing an attachment."""
    _, user = api_client_with_user

    # Create mailbox and give user access
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=MailboxRoleChoices.EDITOR,
    )

    # Create thread and give access
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        thread=thread,
        mailbox=mailbox,
        role=ThreadAccessRoleChoices.EDITOR,
    )

    # Create a message with attachment in raw mime
    raw_mime_content = b"""From: sender@example.com
To: recipient@example.com
Subject: Test message with attachment
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary-string"

--boundary-string
Content-Type: text/plain; charset="utf-8"

This is a test message.

--boundary-string
Content-Type: text/plain
Content-Disposition: attachment; filename="test_file.txt"

Test file content for Drive upload.
--boundary-string--
"""

    message = factories.MessageFactory(
        thread=thread,
        raw_mime=raw_mime_content,
        has_attachments=True,
    )

    return mailbox, message


class TestDriveAPIView:
    """Tests for the Drive API View endpoints."""

    @pytest.fixture(autouse=True)
    def configure_settings(self, settings):
        """Configure settings for tests."""
        settings.DRIVE_CONFIG = {
            "app_name": "Drive",
            "base_url": "http://drive.test",
            "sdk_url": "/sdk",
            "api_url": "/api/v1.0",
            "file_url": "/explorer/items/files",
        }

    def test_api_third_party_drive_get_anonymous(self):
        """Test that GET endpoint requires authentication."""
        client = APIClient()
        response = client.get(reverse("drive"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_get_should_refresh_token(
        self, mock, api_client_with_user
    ):
        """Test that GET endpoint checks if the token is expired and refreshes it if needed."""
        client, _ = api_client_with_user
        assert mock.call_count == 0

        # Mock the items endpoint that the view now calls directly
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            status=status.HTTP_200_OK,
        )
        client.get(reverse("drive") + "?title=test_document")
        assert mock.call_count == 1

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_get_search_by_title(
        self, _mock, api_client_with_user
    ):
        """Test searching for files by title."""
        client, _ = api_client_with_user

        file_id = str(uuid.uuid4())

        # Mock the items search endpoint
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": file_id,
                        "title": "test_document.pdf",
                        "type": "file",
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                ],
            },
            status=status.HTTP_200_OK,
        )

        response = client.get(reverse("drive") + "?title=test_document")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == file_id
        assert data["results"][0]["title"] == "test_document.pdf"

        # Verify a single request was made with correct parameters
        assert len(responses.calls) == 1
        assert (
            responses.calls[0].request.headers["Authorization"]
            == "Bearer test-access-token"
        )
        assert "items/" in responses.calls[0].request.url
        assert "is_creator_me=True" in responses.calls[0].request.url
        assert "type=file" in responses.calls[0].request.url
        assert "title=test_document" in responses.calls[0].request.url

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_get_without_title_filter(
        self, _mock, api_client_with_user
    ):
        """Test searching for files without title filter."""
        client, _ = api_client_with_user

        # Mock the items search endpoint
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

        response = client.get(reverse("drive"))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 0

        # Verify a single request with filters but no title
        assert len(responses.calls) == 1
        assert "title" not in responses.calls[0].request.url
        assert "is_creator_me=True" in responses.calls[0].request.url
        assert "type=file" in responses.calls[0].request.url

    def test_api_third_party_drive_post_anonymous(self):
        """Test that POST endpoint requires authentication."""
        client = APIClient()
        response = client.post(
            reverse("drive"),
            {"blob_id": str(uuid.uuid4())},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_should_refresh_token(
        self, mock, api_client_with_user
    ):
        """Test that POST endpoint checks if the token is expired."""
        client, _ = api_client_with_user
        assert mock.call_count == 0

        # POST without blob_id returns 400 before any external call,
        # but the OIDC refresh decorator still runs
        client.post(reverse("drive"))
        assert mock.call_count == 1

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_missing_blob_id(
        self, _mock, api_client_with_user
    ):
        """Test uploading file without blob_id."""
        client, _ = api_client_with_user

        response = client.post(reverse("drive"), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "blob_id is required"

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_invalid_blob_id(
        self, _mock, api_client_with_user
    ):
        """Test uploading file with invalid blob_id format."""
        client, _ = api_client_with_user

        # Use an invalid blob_id format
        response = client.post(
            reverse("drive"),
            {"blob_id": "invalid_blob_id"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "Invalid blob ID"

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_attachment_no_access(
        self, _mock, api_client_with_user
    ):
        """Test uploading file from a message the user doesn't have access to."""
        client, _ = api_client_with_user

        # Create a message without giving the user access
        other_mailbox = factories.MailboxFactory()
        other_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=other_thread,
            mailbox=other_mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
        )

        raw_mime = b"""From: sender@example.com
To: recipient@example.com
Subject: Test message with attachment
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary-string"

--boundary-string
Content-Type: text/plain; charset="utf-8"

This is a test message.

--boundary-string
Content-Type: text/plain
Content-Disposition: attachment; filename="test_file.txt"

Test file content for Drive upload without access.
--boundary-string--
"""
        other_message = factories.MessageFactory(
            thread=other_thread,
            raw_mime=raw_mime,
            has_attachments=True,
        )

        # Try to upload with a blob_id from this message
        blob_id = f"msg_{other_message.id}_0"

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_success(
        self, _mock, api_client_with_user, mailbox_with_message
    ):
        """Test successfully uploading a file to Drive when it doesn't exist yet."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        blob_id = f"msg_{message.id}_0"

        file_id = str(uuid.uuid4())
        presigned_url = "http://s3.test/presigned-upload-url"

        # Mock the search for existing file (not found)
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file creation response
        responses.add(
            responses.POST,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "id": file_id,
                "title": "test_file.txt",
                "type": "file",
                "policy": presigned_url,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            status=status.HTTP_200_OK,
        )

        # Mock the presigned URL upload
        responses.add(
            responses.PUT,
            presigned_url,
            status=status.HTTP_200_OK,
        )

        # Mock the upload-ended confirmation
        responses.add(
            responses.POST,
            f"http://drive.test/external_api/v1.0/items/{file_id}/upload-ended/",
            json={
                "id": file_id,
                "title": "test_file.txt",
                "type": "file",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            status=status.HTTP_200_OK,
        )

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["id"] == file_id
        assert data["title"] == "test_file.txt"

        # Verify the 4 expected API calls (search → create → S3 upload → upload-ended)
        assert len(responses.calls) == 4

        # Verify search request
        assert "is_creator_me=True" in responses.calls[0].request.url
        assert "title=test_file.txt" in responses.calls[0].request.url

        # Verify file creation request
        assert responses.calls[1].request.method == "POST"
        file_creation_body = json.loads(responses.calls[1].request.body)
        assert file_creation_body["type"] == "file"
        assert file_creation_body["filename"] == "test_file.txt"

        # Verify presigned URL upload
        assert responses.calls[2].request.url == presigned_url
        assert responses.calls[2].request.headers["x-amz-acl"] == "private"

        # Verify upload-ended confirmation
        assert f"items/{file_id}/upload-ended/" in responses.calls[3].request.url

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_file_already_exists(
        self, _mock, api_client_with_user, mailbox_with_message
    ):
        """Test that POST returns 200 when the file already exists in Drive."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        blob_id = f"msg_{message.id}_0"
        existing_file_id = str(uuid.uuid4())

        # The attachment "test_file.txt" has content "Test file content for Drive upload."
        # which is 35 bytes
        attachment_size = len(b"Test file content for Drive upload.")

        # Mock the search returning an existing file with matching size
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": existing_file_id,
                        "filename": "test_file.txt",
                        "mimetype": "text/plain",
                        "size": attachment_size,
                    }
                ],
            },
            status=status.HTTP_200_OK,
        )

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == existing_file_id

        # Only the search request should have been made (no create/upload)
        assert len(responses.calls) == 1
        assert "title=test_file.txt" in responses.calls[0].request.url

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_same_name_different_size(
        self, _mock, api_client_with_user, mailbox_with_message
    ):
        """Test that POST creates a new file when existing file has same name but different size."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        blob_id = f"msg_{message.id}_0"
        file_id = str(uuid.uuid4())
        presigned_url = "http://s3.test/presigned-upload-url"

        # Mock the search returning a file with same name but different size
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": str(uuid.uuid4()),
                        "filename": "test_file.txt",
                        "mimetype": "text/plain",
                        "size": 999999,
                    }
                ],
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file creation (since size didn't match)
        responses.add(
            responses.POST,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "id": file_id,
                "title": "test_file.txt",
                "type": "file",
                "policy": presigned_url,
            },
            status=status.HTTP_200_OK,
        )
        responses.add(responses.PUT, presigned_url, status=status.HTTP_200_OK)
        responses.add(
            responses.POST,
            f"http://drive.test/external_api/v1.0/items/{file_id}/upload-ended/",
            json={"id": file_id, "title": "test_file.txt", "type": "file"},
            status=status.HTTP_200_OK,
        )

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        # search + create + S3 upload + upload-ended
        assert len(responses.calls) == 4

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_message_without_attachments(
        self, _mock, api_client_with_user
    ):
        """Test uploading file from a message without attachments."""
        client, user = api_client_with_user

        # Create mailbox and give user access
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=MailboxRoleChoices.EDITOR,
        )

        # Create thread and give access
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
        )

        # Create a message without attachments
        raw_mime = b"From: test@example.com\nSubject: Test\n\nBody without attachments"
        message = factories.MessageFactory(
            thread=thread,
            raw_mime=raw_mime,
        )

        # Try to upload with a blob_id referencing an attachment
        blob_id = f"msg_{message.id}_0"

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        # Should fail because message doesn't have attachments
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_post_upload_to_s3_fails(
        self, _mock, api_client_with_user, mailbox_with_message
    ):
        """Test handling of S3 upload failure."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        blob_id = f"msg_{message.id}_0"
        file_id = str(uuid.uuid4())
        presigned_url = "http://s3.test/presigned-upload-url"

        # Mock the search for existing file (not found)
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file creation response
        responses.add(
            responses.POST,
            "http://drive.test/external_api/v1.0/items/",
            json={
                "id": file_id,
                "title": "test_file.txt",
                "type": "file",
                "policy": presigned_url,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            status=status.HTTP_200_OK,
        )

        # Mock the presigned URL upload to fail
        responses.add(
            responses.PUT,
            presigned_url,
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.json()["error"] == "Failed to create file in Drive"
