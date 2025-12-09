"""
Test Drive API endpoints in the messages core app.
"""
# pylint: disable=redefined-outer-name

import json
import uuid
from unittest.mock import patch

from django.urls import reverse

import pytest
import requests
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

        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            status=status.HTTP_401_UNAUTHORIZED,
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
        """Test searching for files in the main workspace."""
        client, _ = api_client_with_user

        # Mock the workspace listing response
        workspace_id = str(uuid.uuid4())
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            json={
                "id": "123",
                "email": "john.doe@test.local",
                "main_workspace": {
                    "id": workspace_id,
                    "type": "folder",
                    "title": "My Workspace",
                    "main_workspace": True,
                },
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file search response
        file_id = str(uuid.uuid4())
        responses.add(
            responses.GET,
            f"http://drive.test/external_api/v1.0/items/{workspace_id}/children/",
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

        # Make the request with title parameter
        response = client.get(reverse("drive") + "?title=test_document")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == file_id
        assert data["results"][0]["title"] == "test_document.pdf"

        # Verify the requests were made with correct parameters
        assert len(responses.calls) == 2

        # First request should be to main workspace
        assert (
            responses.calls[0].request.url
            == "http://drive.test/external_api/v1.0/users/me/"
        )
        assert (
            responses.calls[0].request.headers["Authorization"]
            == "Bearer test-access-token"
        )

        # Second request should include filters
        assert f"items/{workspace_id}/children/" in responses.calls[1].request.url
        assert "is_creator_me=True" in responses.calls[1].request.url
        assert "type=file" in responses.calls[1].request.url
        assert "title=test_document" in responses.calls[1].request.url

    @responses.activate
    @patch(
        "lasuite.oidc_login.middleware.RefreshOIDCAccessToken.is_expired",
        return_value=False,
    )
    def test_api_third_party_drive_get_no_main_workspace(
        self, _mock, api_client_with_user
    ):
        """Test searching for files when no main workspace exists."""
        client, _ = api_client_with_user

        # Mock the workspace listing with no main workspace
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            status=status.HTTP_401_UNAUTHORIZED,
        )

        response = client.get(reverse("drive") + "?title=test")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["error"] == "No Drive main workspace found"

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

        workspace_id = str(uuid.uuid4())

        # Mock the users me response
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            json={
                "id": "123",
                "email": "john.doe@test.local",
                "main_workspace": {
                    "id": workspace_id,
                    "type": "folder",
                    "title": "My Workspace",
                    "main_workspace": True,
                },
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file search response
        responses.add(
            responses.GET,
            f"http://drive.test/external_api/v1.0/items/{workspace_id}/children/",
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

        # Make request without title parameter
        response = client.get(reverse("drive"))

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 0

        # Verify filters don't include title
        assert "title" not in responses.calls[1].request.url
        assert "is_creator_me=True" in responses.calls[1].request.url
        assert "type=file" in responses.calls[1].request.url

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

        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/items/",
            status=status.HTTP_200_OK,
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
        )
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
        """Test successfully uploading a file to Drive."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        # Construct blob_id for the first attachment
        blob_id = f"msg_{message.id}_0"

        workspace_id = str(uuid.uuid4())
        file_id = str(uuid.uuid4())
        presigned_url = "http://s3.test/presigned-upload-url"

        # Mock the users me response
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            json={
                "id": "123",
                "email": "john.doe@test.local",
                "main_workspace": {
                    "id": workspace_id,
                    "type": "folder",
                    "title": "My Workspace",
                    "main_workspace": True,
                },
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file creation response
        responses.add(
            responses.POST,
            f"http://drive.test/external_api/v1.0/items/{workspace_id}/children/",
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

        # Make the request
        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["id"] == file_id
        assert data["title"] == "test_file.txt"

        # Verify all the expected API calls were made
        assert len(responses.calls) == 4

        # Verify workspace listing request
        assert (
            responses.calls[0].request.url
            == "http://drive.test/external_api/v1.0/users/me/"
        )
        assert (
            responses.calls[0].request.headers["Authorization"]
            == "Bearer test-access-token"
        )

        # Verify file creation request
        assert f"items/{workspace_id}/children/" in responses.calls[1].request.url
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
    def test_api_third_party_drive_post_no_main_workspace(
        self, _mock, api_client_with_user, mailbox_with_message
    ):
        """Test uploading file when no main workspace exists."""
        client, _ = api_client_with_user
        _, message = mailbox_with_message

        blob_id = f"msg_{message.id}_0"

        # Mock the users me as unauthenticated
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            status=status.HTTP_401_UNAUTHORIZED,
        )

        response = client.post(
            reverse("drive"),
            {"blob_id": blob_id},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["error"] == "No Drive main workspace found"

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
        workspace_id = str(uuid.uuid4())
        file_id = str(uuid.uuid4())
        presigned_url = "http://s3.test/presigned-upload-url"

        # Mock the users me response
        responses.add(
            responses.GET,
            "http://drive.test/external_api/v1.0/users/me/",
            json={
                "id": "123",
                "email": "john.doe@test.local",
                "main_workspace": {
                    "id": workspace_id,
                    "type": "folder",
                    "title": "My Workspace",
                    "main_workspace": True,
                },
            },
            status=status.HTTP_200_OK,
        )

        # Mock the file creation response
        responses.add(
            responses.POST,
            f"http://drive.test/external_api/v1.0/items/{workspace_id}/children/",
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

        # Make the request
        with pytest.raises(requests.exceptions.HTTPError) as excinfo:
            client.post(
                reverse("drive"),
                {"blob_id": blob_id},
                format="json",
            )

        assert (
            excinfo.value.response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        )
