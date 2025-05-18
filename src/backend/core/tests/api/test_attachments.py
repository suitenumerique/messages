"""Tests for the blob (attachment) API."""

import email
import hashlib
import json
import random
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
from core.enums import MailboxRoleChoices, ThreadAccessRoleChoices


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
        """Create a mailbox for the test user with editor access."""
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
        assert blob.type == "text/plain"
        assert blob.sha256 == expected_hash
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


@pytest.mark.django_db
class TestDraftWithAttachments:
    """Tests for creating and updating drafts with attachments."""

    @pytest.fixture
    def api_client(self):
        """Return an authenticated API client."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def user_mailbox(self, api_client):
        """Create a mailbox for the test user with editor access."""
        _, user = api_client
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=MailboxRoleChoices.EDITOR,
        )
        return mailbox

    @pytest.fixture
    def blob(self, user_mailbox):
        """Create a test blob."""
        test_content = b"Test attachment content %i" % random.randint(0, 10000000)
        return models.Blob.objects.create(
            sha256=hashlib.sha256(test_content).hexdigest(),
            size=len(test_content),
            type="text/plain",
            raw_content=test_content,
            mailbox=user_mailbox,
        )

    @pytest.fixture
    def attachment(self, user_mailbox, blob):
        """Create a test attachment linked to a blob."""
        return models.Attachment.objects.create(
            mailbox=user_mailbox, name="test_attachment.txt", blob=blob
        )

    def test_create_draft_with_blob(self, api_client, user_mailbox, blob):
        """Test creating a draft message with a blob reference that becomes an attachment."""
        client, _ = api_client

        # Create a draft
        url = reverse("draft-message")
        response = client.post(
            url,
            {
                "senderId": str(user_mailbox.id),
                "subject": "Test draft with attachment",
                "draftBody": json.dumps(
                    {"text": "This is a test draft with an attachment"}
                ),
                "to": ["recipient@example.com"],
                "attachments": [
                    {
                        "partId": "att-1",
                        "blobId": str(blob.id),
                        "name": "test_attachment.txt",
                    }
                ],
            },
            format="json",
        )

        # Check response
        assert response.status_code == status.HTTP_201_CREATED

        # Verify the draft has an attachment created from the blob
        draft_id = response.data["id"]
        draft = models.Message.objects.get(id=draft_id)
        assert draft.attachments.count() == 1

        # Check the attachment properties
        attachment = draft.attachments.first()
        assert attachment.blob == blob

        # Check attachment appears in the serialized response
        assert "attachments" in response.data
        assert len(response.data["attachments"]) == 1
        assert response.data["attachments"][0]["blobId"] == str(blob.id)

    def test_add_attachment_to_existing_draft_and_send(
        self, api_client, user_mailbox, blob
    ):
        """Test adding a blob as attachment to an existing draft and sending it."""
        client, _ = api_client

        # Create a draft without attachments
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread, mailbox=user_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )

        # Create sender contact
        sender_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
        sender = factories.ContactFactory(
            mailbox=user_mailbox, email=sender_email, name=user_mailbox.local_part
        )

        # Create a draft message
        draft = factories.MessageFactory(
            thread=thread, sender=sender, is_draft=True, subject="Existing draft"
        )

        text_body = (
            f"This is a test draft with an attachment {random.randint(0, 10000000)}"
        )

        # Update the draft to add the blob as attachment
        url = reverse("draft-message-detail", kwargs={"message_id": draft.id})
        response = client.put(
            url,
            {
                "senderId": str(user_mailbox.id),
                "subject": "Updated draft with attachment",
                "attachments": [
                    {
                        "partId": "att-1",
                        "blobId": str(blob.id),
                        "name": "test_attachment.txt",
                    }
                ],
            },
            format="json",
        )

        # Check response
        assert response.status_code == status.HTTP_200_OK

        # Verify an attachment was created and linked to the draft
        draft.refresh_from_db()
        assert draft.attachments.count() == 1

        # Check the attachment properties
        attachment = draft.attachments.first()
        assert attachment.blob == blob
        assert attachment.mailbox == user_mailbox

        # Send the draft and check that the attachment is included in the raw mime
        send_response = client.post(
            reverse("send-message"),
            {
                "messageId": draft.id,
                "textBody": text_body,
                "htmlBody": f"<p>{text_body}</p>",
                "senderId": user_mailbox.id,
            },
            format="json",
        )

        # Assert the send response is successful
        assert send_response.status_code == status.HTTP_200_OK

        draft.refresh_from_db()
        assert draft.is_draft is False
        assert draft.attachments.count() == 1
        assert draft.attachments.first().blob == blob
        assert draft.attachments.first().mailbox == user_mailbox
        parsed_email = email.message_from_bytes(draft.raw_mime)

        # Check that the email is multipart
        assert parsed_email.is_multipart()

        # List MIME parts
        parts = list(parsed_email.walk())

        mime_types = [part.get_content_type() for part in parts]

        assert mime_types == [
            "multipart/mixed",
            "multipart/alternative",
            "text/plain",
            "text/html",
            "text/plain",
        ]

        assert parts[4].get_payload(decode=True).decode() == blob.raw_content.decode()
        assert parts[4].get_content_disposition() == "attachment"
        assert parts[4].get_filename() == "test_attachment.txt"
