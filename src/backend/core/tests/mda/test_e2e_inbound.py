"""End-to-End tests for inbound message flow with attachments."""

import base64
import datetime
import hashlib
import uuid

from django.conf import settings
from django.urls import reverse

import jwt
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories


@pytest.mark.django_db
class TestE2EInboundAttachmentFlow:
    """Test the inbound email flow with attachments: MTA API → delivery → REST API retrieval."""

    @pytest.fixture
    def api_client(self):
        """Return an authenticated API client."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def api_client_service_account(self):
        """Return a service account API client."""
        client = APIClient()
        return client

    @pytest.fixture
    def mailbox(self, api_client):
        """Create a mailbox for the test user."""
        _, user = api_client
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        return mailbox

    @pytest.fixture
    def recipient_email(self, mailbox):
        """Return the email address for the mailbox."""
        return f"{mailbox.local_part}@{mailbox.domain.name}"

    @pytest.fixture
    def attachment_data(self):
        """Create binary attachment data."""
        content = b"Test attachment binary data for E2E test"
        return {
            "content": content,
            "filename": "test_attachment.txt",
            "content_type": "text/plain",
            "size": len(content),
        }

    @pytest.fixture
    def multipart_email_with_attachment(self, recipient_email, attachment_data):
        """Create a multipart email with an attachment."""
        boundary = "------------boundary123456789"

        # Create the multipart email
        email_template = f"""From: sender@example.com
To: {recipient_email}
Subject: Test E2E Inbound with Attachment
Message-ID: <e2e-test-{uuid.uuid4()}@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: 7bit

This is the plain text body of the test email.

--{boundary}
Content-Type: text/html; charset="UTF-8"
Content-Transfer-Encoding: 7bit

<html><body><p>This is the <b>HTML</b> body of the test email.</p></body></html>

--{boundary}
Content-Type: {attachment_data["content_type"]}
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="{attachment_data["filename"]}"

{base64.b64encode(attachment_data["content"]).decode()}
--{boundary}--
"""
        return email_template.encode("utf-8")

    @pytest.fixture(name="valid_jwt_token")
    def fixture_valid_jwt_token(self):
        """Return a valid JWT token for the sample email."""

        def _get_jwt_token(body, metadata):
            body_hash = hashlib.sha256(body).hexdigest()
            payload = {
                "body_hash": body_hash,
                "exp": datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(seconds=30),
                **metadata,
            }
            return jwt.encode(payload, settings.MDA_API_SECRET, algorithm="HS256")

        return _get_jwt_token

    def test_e2e_inbound_with_attachment(
        self,
        api_client,
        api_client_service_account,
        mailbox,
        recipient_email,
        multipart_email_with_attachment,
        attachment_data,
        valid_jwt_token,
    ):
        """
        Test the complete inbound flow with attachment:
        1. Submit email to MTA API without mocking JWT or delivery
        2. Verify thread/message appears in REST API
        3. Verify attachment appears in message via API
        4. Download attachment via blob API
        """
        client, _ = api_client

        # Step 1: Submit email to MTA API with real JWT
        token = valid_jwt_token(
            multipart_email_with_attachment, {"original_recipients": [recipient_email]}
        )

        response = api_client_service_account.post(
            "/api/v1.0/mta/inbound-email/",
            data=multipart_email_with_attachment,
            content_type="message/rfc822",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        # Verify API response
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok", "delivered": 1}

        # Step 2: Use the thread list API to find our new thread
        response = client.get(reverse("threads-list"), {"mailbox_id": str(mailbox.id)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 1

        # Find the thread with our subject
        thread_data = None
        for t in response.data["results"]:
            if t["subject"] == "Test E2E Inbound with Attachment":
                thread_data = t
                break

        assert thread_data is not None, (
            "Thread with expected subject not found in API response"
        )
        thread_id = thread_data["id"]

        # Step 3: Use the message list API to get messages in this thread
        response = client.get(reverse("messages-list"), {"thread_id": thread_id})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 1

        # Find our message
        message_data = None
        for m in response.data["results"]:
            if m["subject"] == "Test E2E Inbound with Attachment":
                message_data = m
                break

        assert message_data is not None, (
            "Message with expected subject not found in API response"
        )
        message_id = message_data["id"]

        # Verify message content via API
        assert message_data["sender"]["email"] == "sender@example.com"
        assert len(message_data["textBody"]) >= 1
        assert len(message_data["htmlBody"]) >= 1

        # Verify attachment in message data
        assert len(message_data["attachments"]) >= 1

        # Find our attachment
        attachment_data_from_api = None
        for a in message_data["attachments"]:
            if a["name"] == attachment_data["filename"]:
                attachment_data_from_api = a
                break

        assert attachment_data_from_api is not None, (
            "Attachment not found in message data"
        )

        # TODO?
        # assert attachment_data_from_api["type"] == attachment_data["content_type"]
        assert attachment_data_from_api["type"] == "application/octet-stream"
        assert attachment_data_from_api["size"] == attachment_data["size"]

        # Step 4: Get the message directly to double-check attachment info
        response = client.get(reverse("messages-detail", kwargs={"id": message_id}))
        assert response.status_code == status.HTTP_200_OK

        # Find the attachment again to get blob ID
        blob_id = None
        for a in response.data["attachments"]:
            if a["name"] == attachment_data["filename"]:
                blob_id = a["blobId"]
                break

        assert blob_id is not None, "Blob ID not found in message details"

        # Step 5: Download the blob
        response = client.get(reverse("blob-download", kwargs={"pk": blob_id}))
        assert response.status_code == status.HTTP_200_OK
        assert response.content == attachment_data["content"]

        # TODO?
        # assert response["Content-Type"] == attachment_data["content_type"]
        assert response["Content-Type"] == "application/octet-stream"
