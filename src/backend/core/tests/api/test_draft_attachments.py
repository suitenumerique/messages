"""Tests for draft attachments API."""

# pylint: disable=too-many-lines

import base64
import email
import json
import random
import uuid

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
from core.enums import MailboxRoleChoices, ThreadAccessRoleChoices


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
            role=MailboxRoleChoices.SENDER,
        )
        return mailbox

    @pytest.fixture
    def blob(self, user_mailbox):
        """Create a test blob."""
        test_content = b"Test attachment content %i" % random.randint(0, 10000000)
        return user_mailbox.create_blob(
            content=test_content,
            content_type="text/plain",
        )

    @pytest.fixture
    def attachment(self, user_mailbox, blob):
        """Create a test attachment linked to a blob."""
        return models.Attachment.objects.create(
            mailbox=user_mailbox, name="test_attachment.txt", blob=blob
        )

    def test_draft_create_with_blob(self, api_client, user_mailbox, blob):
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

        # Check we can delete the draft
        response = client.delete(reverse("messages-detail", kwargs={"id": draft_id}))
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Check the draft is deleted
        assert models.Message.objects.count() == 0

        # Check the attachment is deleted
        assert models.Attachment.objects.count() == 0

        # Check the blob is deleted
        assert models.Blob.objects.count() == 0

    def test_draft_add_attachment_to_existing_draft_and_send(
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

        # attachment blob should already be created
        assert models.Blob.objects.count() == 1
        assert models.Blob.objects.first().content_type == "text/plain"

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

        # still a single blob
        assert models.Blob.objects.count() == 1

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
        assert draft.attachments.count() == 0

        # Original attachment blob should be deleted.
        assert models.Blob.objects.count() == 1
        assert models.Blob.objects.first().content_type == "message/rfc822"

        parsed_email = email.message_from_bytes(draft.blob.get_content())

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

        assert parts[4].get_payload(decode=True).decode() == blob.get_content().decode()
        assert parts[4].get_content_disposition() == "attachment"
        assert parts[4].get_filename() == "test_attachment.txt"

    def test_draft_attachment_size_limit_exceeded(self, api_client, user_mailbox):
        """Test that adding attachments exceeding the size limit raises ValidationError."""
        client, _ = api_client

        # Set a small attachment size limit for testing (1 KB)
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=1024):
            # Create a large blob (2 KB) that exceeds the limit
            large_content = b"x" * 2048
            blob = user_mailbox.create_blob(
                content=large_content,
                content_type="text/plain",
            )

            # Try to create a draft with the large attachment
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft with large attachment",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob.id),
                            "name": "large_file.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should fail with validation error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

    def test_draft_attachment_cumulative_size_limit(self, api_client, user_mailbox):
        """Test that cumulative attachment size is validated when adding multiple attachments."""
        client, _ = api_client

        # Set attachment size limit to 2 KB
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=2048):
            # Create first blob (1 KB)
            blob1_content = b"x" * 1024
            blob1 = user_mailbox.create_blob(
                content=blob1_content,
                content_type="text/plain",
            )

            # Create draft with first attachment
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft with attachments",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should succeed
            assert response.status_code == status.HTTP_201_CREATED
            draft_id = response.data["id"]

            # Create second blob (1.5 KB)
            blob2_content = b"y" * 1536
            blob2 = user_mailbox.create_blob(
                content=blob2_content,
                content_type="text/plain",
            )

            # Try to add second attachment (total would be 2.5 KB > 2 KB limit)
            url = reverse("draft-message-detail", kwargs={"message_id": draft_id})
            response = client.put(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        },
                        {
                            "partId": "att-2",
                            "blobId": str(blob2.id),
                            "name": "file2.txt",
                        },
                    ],
                },
                format="json",
            )

            # Should fail with validation error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

    def test_draft_attachment_within_size_limit(self, api_client, user_mailbox):
        """Test that attachments within the size limit are accepted."""
        client, _ = api_client

        # Set attachment size limit to 10 KB
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=10240):
            # Create two blobs totaling 8 KB (within limit)
            blob1_content = b"x" * 4096
            blob1 = user_mailbox.create_blob(
                content=blob1_content,
                content_type="text/plain",
            )

            blob2_content = b"y" * 4096
            blob2 = user_mailbox.create_blob(
                content=blob2_content,
                content_type="text/plain",
            )

            # Create draft with both attachments
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft with multiple attachments",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        },
                        {
                            "partId": "att-2",
                            "blobId": str(blob2.id),
                            "name": "file2.txt",
                        },
                    ],
                },
                format="json",
            )

            # Should succeed
            assert response.status_code == status.HTTP_201_CREATED
            assert len(response.data["attachments"]) == 2

    def test_draft_replace_attachment_allows_new_within_limit(
        self, api_client, user_mailbox
    ):
        """Test that removing an attachment allows adding a new one within the limit."""
        client, _ = api_client

        # Set attachment size limit to 2 KB
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=2048):
            # Create first blob (1.5 KB)
            blob1_content = b"x" * 1536
            blob1 = user_mailbox.create_blob(
                content=blob1_content,
                content_type="text/plain",
            )

            # Create draft with first attachment
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        }
                    ],
                },
                format="json",
            )

            assert response.status_code == status.HTTP_201_CREATED
            draft_id = response.data["id"]

            # Create second blob (1.5 KB)
            blob2_content = b"y" * 1536
            blob2 = user_mailbox.create_blob(
                content=blob2_content,
                content_type="text/plain",
            )

            # Replace first attachment with second (removing first, adding second)
            url = reverse("draft-message-detail", kwargs={"message_id": draft_id})
            response = client.put(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "attachments": [
                        {
                            "partId": "att-2",
                            "blobId": str(blob2.id),
                            "name": "file2.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should succeed since we're replacing, not adding
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data["attachments"]) == 1
            assert response.data["attachments"][0]["blobId"] == str(blob2.id)

    def test_draft_send_with_attachments_exceeding_size_limit(
        self, api_client, user_mailbox
    ):
        """Test that sending a draft with attachments exceeding the size limit fails."""
        client, _ = api_client

        # Set a small attachment size limit for testing (1 KB)
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=1024):
            # Create a large blob (2 KB) that exceeds the limit
            large_content = b"x" * 2048
            blob = user_mailbox.create_blob(
                content=large_content,
                content_type="text/plain",
            )

            # Create attachment
            attachment = models.Attachment.objects.create(
                mailbox=user_mailbox, name="large_file.txt", blob=blob
            )

            # Create a draft thread and message
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(
                thread=thread,
                mailbox=user_mailbox,
                role=ThreadAccessRoleChoices.EDITOR,
            )

            sender_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
            sender = factories.ContactFactory(
                mailbox=user_mailbox, email=sender_email, name=user_mailbox.local_part
            )

            draft = factories.MessageFactory(
                thread=thread, sender=sender, is_draft=True, subject="Test draft"
            )

            # Manually add the attachment (bypassing the validation in draft.py)
            draft.attachments.add(attachment)

            # Try to send the draft
            send_response = client.post(
                reverse("send-message"),
                {
                    "messageId": draft.id,
                    "textBody": "Test email body",
                    "htmlBody": "<p>Test email body</p>",
                    "senderId": user_mailbox.id,
                },
                format="json",
            )

            # Should fail because the total message size exceeds the limit
            assert send_response.status_code == status.HTTP_400_BAD_REQUEST
            assert "message" in send_response.data
            assert "exceeds the" in str(send_response.data["message"])
            assert "MB limit" in str(send_response.data["message"])

    def test_draft_create_size_limit_exceeded_no_blob_or_attachment_created(
        self, api_client, user_mailbox
    ):
        """Test that when creating a draft with oversized attachment, no blob/attachment is created."""
        client, _ = api_client

        # Set a small attachment size limit for testing (1 KB)
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=1024):
            # Create a large blob (2 KB) that exceeds the limit
            large_content = b"0" * 2048
            blob = user_mailbox.create_blob(
                content=large_content,
                content_type="text/plain",
            )

            # Count after blob creation (this happens before the draft request)
            blob_count_before_draft = models.Blob.objects.count()
            attachment_count_before_draft = models.Attachment.objects.count()

            # Try to create a draft with the large attachment
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft with large attachment",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob.id),
                            "name": "large_file.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should fail with validation error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

            # Verify no new blob was created during the draft request
            # (the blob was created before, but no new blob should be created)
            assert models.Blob.objects.count() == blob_count_before_draft

            # Verify no new attachment was created
            assert models.Attachment.objects.count() == attachment_count_before_draft

            # Verify no draft message was created
            assert models.Message.objects.filter(is_draft=True).count() == 0

    def test_draft_update_size_limit_exceeded_no_blob_or_attachment_created(
        self, api_client, user_mailbox
    ):
        """Test that when updating a draft with oversized attachments, no new blob/attachment is created."""
        client, _ = api_client

        # Set attachment size limit to 2 KB
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=2048):
            # Create first blob (1 KB)
            blob1_content = b"x" * 1024
            blob1 = user_mailbox.create_blob(
                content=blob1_content,
                content_type="text/plain",
            )

            # Create draft with first attachment
            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Draft with attachments",
                    "draftBody": json.dumps({"text": "Test"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should succeed
            assert response.status_code == status.HTTP_201_CREATED
            draft_id = response.data["id"]

            # Create another blob (1.5 KB)
            blob2_content = b"y" * 1536
            blob2 = user_mailbox.create_blob(
                content=blob2_content,
                content_type="text/plain",
            )

            # Record counts before the failing update
            blob_count_before_update = models.Blob.objects.count()
            attachment_count_before_update = models.Attachment.objects.count()

            # Try to add second attachment (total would be 2.5 KB > 2 KB limit)
            url = reverse("draft-message-detail", kwargs={"message_id": draft_id})
            response = client.put(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "attachments": [
                        {
                            "partId": "att-1",
                            "blobId": str(blob1.id),
                            "name": "file1.txt",
                        },
                        {
                            "partId": "att-2",
                            "blobId": str(blob2.id),
                            "name": "file2.txt",
                        },
                    ],
                },
                format="json",
            )

            # Should fail with validation error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

            # Verify blob count didn't change during the failed update
            # (blob2 was created before the draft update, so count should be the same)
            assert models.Blob.objects.count() == blob_count_before_update

            # Verify no new attachment was created for blob2 during the failed update
            # (the transaction should have rolled back)
            assert models.Attachment.objects.count() == attachment_count_before_update

            # Verify the draft still has only the first attachment
            draft = models.Message.objects.get(id=draft_id)
            assert draft.attachments.count() == 1
            assert draft.attachments.first().blob == blob1


@pytest.mark.django_db
class TestDraftWithForwardedAttachments:
    """Tests for forwarding messages with attachments."""

    @pytest.fixture
    def api_client(self):
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
            role=MailboxRoleChoices.SENDER,
        )
        return mailbox

    @pytest.fixture
    def attachment_content(self):
        """Create test attachment content."""
        return b"Test attachment content for forwarding"

    @pytest.fixture
    def multipart_email_with_attachment(self, user_mailbox, attachment_content):
        """Create a multipart email with an attachment."""

        recipient_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
        boundary = "------------boundary123456789"

        email_template = f"""From: sender@example.com
To: {recipient_email}
Subject: Original message with attachment
Message-ID: <original-msg-{uuid.uuid4()}@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: 7bit

This is the original message body.

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="test_file.txt"

{base64.b64encode(attachment_content).decode()}
--{boundary}--
"""
        return email_template.encode("utf-8")

    @pytest.fixture
    def multipart_email_with_inline_image(self, user_mailbox):
        """Create a multipart email with an inline image."""

        recipient_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
        boundary = "------------boundary123456789"
        image_content = b"fake-image-content-for-testing"
        cid = "image001@example.com"

        email_template = f"""From: sender@example.com
To: {recipient_email}
Subject: Original message with inline image
Message-ID: <original-msg-{uuid.uuid4()}@example.com>
MIME-Version: 1.0
Content-Type: multipart/related; boundary="{boundary}"

--{boundary}
Content-Type: text/html; charset="UTF-8"
Content-Transfer-Encoding: 7bit

<html><body><p>Here is an image:</p><img src="cid:{cid}"></body></html>

--{boundary}
Content-Type: image/png
Content-Transfer-Encoding: base64
Content-Disposition: inline; filename="image.png"
Content-ID: <{cid}>

{base64.b64encode(image_content).decode()}
--{boundary}--
"""
        return email_template.encode("utf-8"), cid, image_content

    @pytest.fixture
    def received_message_with_attachment(
        self, user_mailbox, multipart_email_with_attachment
    ):
        """Create a received message with an attachment in the mailbox."""
        # Create thread
        thread = factories.ThreadFactory(subject="Original message with attachment")
        factories.ThreadAccessFactory(
            thread=thread, mailbox=user_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )

        # Create sender contact
        sender = factories.ContactFactory(
            mailbox=user_mailbox, email="sender@example.com", name="Sender"
        )

        # Create message blob with the raw MIME content
        blob = user_mailbox.create_blob(
            content=multipart_email_with_attachment,
            content_type="message/rfc822",
        )

        # Create message
        message = factories.MessageFactory(
            thread=thread,
            sender=sender,
            is_draft=False,
            is_sender=False,
            subject="Original message with attachment",
            blob=blob,
            has_attachments=True,
        )

        return message

    @pytest.fixture
    def received_message_with_inline_image(
        self, user_mailbox, multipart_email_with_inline_image
    ):
        """Create a received message with an inline image in the mailbox."""
        mime_content, cid, image_content = multipart_email_with_inline_image

        # Create thread
        thread = factories.ThreadFactory(subject="Original message with inline image")
        factories.ThreadAccessFactory(
            thread=thread, mailbox=user_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )

        # Create sender contact
        sender = factories.ContactFactory(
            mailbox=user_mailbox, email="sender@example.com", name="Sender"
        )

        # Create message blob with the raw MIME content
        blob = user_mailbox.create_blob(
            content=mime_content,
            content_type="message/rfc822",
        )

        # Create message
        message = factories.MessageFactory(
            thread=thread,
            sender=sender,
            is_draft=False,
            is_sender=False,
            subject="Original message with inline image",
            blob=blob,
            has_attachments=True,
        )

        return message, cid, image_content

    def test_draft_create_with_forwarded_attachments(
        self,
        api_client,
        user_mailbox,
        received_message_with_attachment,
        attachment_content,
    ):
        """Test creating a draft that forwards an attachment from a received message."""
        client, _ = api_client

        # The blobId format for message attachments is msg_{message_id}_{index}
        forwarded_blob_id = f"msg_{received_message_with_attachment.id}_0"

        # Create a forward draft with the attachment from the original message
        url = reverse("draft-message")
        response = client.post(
            url,
            {
                "senderId": str(user_mailbox.id),
                "parentId": str(received_message_with_attachment.id),
                "subject": "Fwd: Original message with attachment",
                "draftBody": json.dumps({"text": "Forwarding this message"}),
                "to": ["recipient@example.com"],
                "attachments": [
                    {
                        "blobId": forwarded_blob_id,
                        "name": "test_file.txt",
                    }
                ],
            },
            format="json",
        )

        # Check response
        assert response.status_code == status.HTTP_201_CREATED

        # Verify the draft has an attachment
        draft_id = response.data["id"]
        draft = models.Message.objects.get(id=draft_id)
        assert draft.attachments.count() == 1

        # Check the attachment was created with a new blob
        attachment = draft.attachments.first()
        assert attachment.name == "test_file.txt"

        # Verify the blob content matches the original attachment
        assert attachment.blob.get_content() == attachment_content

        # Verify the blobId in the response is a new UUID (not msg_* format)
        assert "attachments" in response.data
        assert len(response.data["attachments"]) == 1
        # The new blobId should be a valid UUID
        new_blob_id = response.data["attachments"][0]["blobId"]
        assert not new_blob_id.startswith("msg_")
        uuid.UUID(new_blob_id)  # Should not raise

    def test_draft_forward_attachments_inline_image_preserves_cid(
        self, api_client, user_mailbox, received_message_with_inline_image
    ):
        """Test that forwarding an inline image preserves its Content-ID."""
        client, _ = api_client
        message, expected_cid, image_content = received_message_with_inline_image

        # The blobId format for message attachments is msg_{message_id}_{index}
        forwarded_blob_id = f"msg_{message.id}_0"

        # Create a forward draft with the inline image from the original message
        url = reverse("draft-message")
        response = client.post(
            url,
            {
                "senderId": str(user_mailbox.id),
                "parentId": str(message.id),
                "subject": "Fwd: Original message with inline image",
                "draftBody": json.dumps({"text": "Forwarding with inline image"}),
                "to": ["recipient@example.com"],
                "attachments": [
                    {
                        "blobId": forwarded_blob_id,
                        "name": "image.png",
                    }
                ],
            },
            format="json",
        )

        # Check response
        assert response.status_code == status.HTTP_201_CREATED

        # Verify the draft has an attachment with the preserved cid
        draft_id = response.data["id"]
        draft = models.Message.objects.get(id=draft_id)
        assert draft.attachments.count() == 1

        attachment = draft.attachments.first()
        assert attachment.cid == expected_cid

        # Verify the blob content matches the original
        assert attachment.blob.get_content() == image_content

    def test_draft_forward_attachments_inaccessible_message_fails(
        self, api_client, user_mailbox
    ):
        """Test that forwarding an attachment from an inaccessible message fails."""
        client, _ = api_client

        # Create another mailbox that the user doesn't have access to
        other_mailbox = factories.MailboxFactory()

        # Create a message in the other mailbox
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread, mailbox=other_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(
            mailbox=other_mailbox, email="sender@example.com"
        )
        other_message = factories.MessageFactory(
            thread=thread, sender=sender, is_draft=False, has_attachments=True
        )

        # Try to create a draft using attachment from inaccessible message
        forwarded_blob_id = f"msg_{other_message.id}_0"

        url = reverse("draft-message")
        response = client.post(
            url,
            {
                "senderId": str(user_mailbox.id),
                "subject": "Trying to forward from inaccessible message",
                "draftBody": json.dumps({"text": "Test"}),
                "to": ["recipient@example.com"],
                "attachments": [
                    {
                        "blobId": forwarded_blob_id,
                        "name": "stolen_file.txt",
                    }
                ],
            },
            format="json",
        )

        # Draft should be created but without the attachment
        # (attachment is skipped due to permission check)
        assert response.status_code == status.HTTP_201_CREATED
        draft_id = response.data["id"]
        draft = models.Message.objects.get(id=draft_id)
        assert draft.attachments.count() == 0

    def test_draft_forward_attachments_add_to_existing_draft(
        self,
        api_client,
        user_mailbox,
        received_message_with_attachment,
        attachment_content,
    ):
        """Test adding a forwarded attachment to an existing draft."""
        client, _ = api_client

        # First create a draft without attachments
        url = reverse("draft-message")
        response = client.post(
            url,
            {
                "senderId": str(user_mailbox.id),
                "subject": "Draft without attachment",
                "draftBody": json.dumps({"text": "Initial draft"}),
                "to": ["recipient@example.com"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        draft_id = response.data["id"]

        # Now update the draft to add a forwarded attachment
        forwarded_blob_id = f"msg_{received_message_with_attachment.id}_0"

        url = reverse("draft-message-detail", kwargs={"message_id": draft_id})
        response = client.put(
            url,
            {
                "senderId": str(user_mailbox.id),
                "attachments": [
                    {
                        "blobId": forwarded_blob_id,
                        "name": "test_file.txt",
                    }
                ],
            },
            format="json",
        )

        # Check response
        assert response.status_code == status.HTTP_200_OK

        # Verify the attachment was added
        draft = models.Message.objects.get(id=draft_id)
        assert draft.attachments.count() == 1
        assert draft.attachments.first().blob.get_content() == attachment_content

    def test_draft_forward_attachments_size_limit_exceeded_no_blob_created(
        self, api_client, user_mailbox
    ):
        """Test that forwarding oversized attachments doesn't create blobs or attachments."""
        client, _ = api_client

        # Create a received message with a large attachment
        large_attachment_content = b"x" * 2048  # 2 KB

        recipient_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
        boundary = "------------boundary123456789"

        email_template = f"""From: sender@example.com
To: {recipient_email}
Subject: Large attachment message
Message-ID: <large-msg-{uuid.uuid4()}@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: 7bit

This message has a large attachment.

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="large_file.txt"

{base64.b64encode(large_attachment_content).decode()}
--{boundary}--
"""
        mime_content = email_template.encode("utf-8")

        # Create thread
        thread = factories.ThreadFactory(subject="Large attachment message")
        factories.ThreadAccessFactory(
            thread=thread, mailbox=user_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )

        # Create sender contact
        sender = factories.ContactFactory(
            mailbox=user_mailbox, email="sender@example.com", name="Sender"
        )

        # Create message blob with the raw MIME content
        blob = user_mailbox.create_blob(
            content=mime_content,
            content_type="message/rfc822",
        )

        # Create message
        message = factories.MessageFactory(
            thread=thread,
            sender=sender,
            is_draft=False,
            is_sender=False,
            subject="Large attachment message",
            blob=blob,
            has_attachments=True,
        )

        # Set a small attachment size limit for testing (1 KB)
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=1024):
            # Record counts before the failing request
            blob_count_before = models.Blob.objects.count()
            attachment_count_before = models.Attachment.objects.count()

            # Try to create a draft forwarding the large attachment
            forwarded_blob_id = f"msg_{message.id}_0"

            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Fwd: Large attachment message",
                    "draftBody": json.dumps({"text": "Forwarding"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "blobId": forwarded_blob_id,
                            "name": "large_file.txt",
                        }
                    ],
                },
                format="json",
            )

            # Should fail with validation error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

            # Verify no new blob was created (transaction should have rolled back)
            assert models.Blob.objects.count() == blob_count_before

            # Verify no new attachment was created
            assert models.Attachment.objects.count() == attachment_count_before

            # Verify no draft message was created
            assert models.Message.objects.filter(is_draft=True).count() == 0

    def test_draft_forward_attachments_update_size_limit_exceeded_no_blob_created(
        self, api_client, user_mailbox, received_message_with_attachment
    ):
        """Test that updating a draft with oversized forwarded attachment doesn't create blobs."""
        client, _ = api_client

        # Create a large attachment message (2 KB - will exceed 1 KB limit on its own)
        large_attachment_content = b"y" * 2048  # 2 KB

        recipient_email = f"{user_mailbox.local_part}@{user_mailbox.domain.name}"
        boundary = "------------boundary987654321"

        email_template = f"""From: sender2@example.com
To: {recipient_email}
Subject: Second large attachment
Message-ID: <large-msg2-{uuid.uuid4()}@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: 7bit

Second message.

--{boundary}
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="second_file.txt"

{base64.b64encode(large_attachment_content).decode()}
--{boundary}--
"""
        mime_content = email_template.encode("utf-8")

        # Create thread
        thread2 = factories.ThreadFactory(subject="Second attachment message")
        factories.ThreadAccessFactory(
            thread=thread2, mailbox=user_mailbox, role=ThreadAccessRoleChoices.EDITOR
        )

        sender2 = factories.ContactFactory(
            mailbox=user_mailbox, email="sender2@example.com", name="Sender2"
        )

        blob2 = user_mailbox.create_blob(
            content=mime_content,
            content_type="message/rfc822",
        )

        message2 = factories.MessageFactory(
            thread=thread2,
            sender=sender2,
            is_draft=False,
            is_sender=False,
            subject="Second large attachment",
            blob=blob2,
            has_attachments=True,
        )

        # Set attachment size limit to 1 KB
        # Second attachment alone (2048 bytes) > 1024 limit
        with override_settings(MAX_OUTGOING_ATTACHMENT_SIZE=1024):
            # Create a draft with the first forwarded attachment (fits within limit ~37 bytes)
            forwarded_blob_id_1 = f"msg_{received_message_with_attachment.id}_0"

            url = reverse("draft-message")
            response = client.post(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "subject": "Fwd: Multiple attachments",
                    "draftBody": json.dumps({"text": "Forwarding"}),
                    "to": ["recipient@example.com"],
                    "attachments": [
                        {
                            "blobId": forwarded_blob_id_1,
                            "name": "test_file.txt",
                        }
                    ],
                },
                format="json",
            )

            assert response.status_code == status.HTTP_201_CREATED
            draft_id = response.data["id"]

            # Record counts before the failing update
            blob_count_before_update = models.Blob.objects.count()
            attachment_count_before_update = models.Attachment.objects.count()

            # Try to add the second (large) forwarded attachment that alone exceeds the limit
            forwarded_blob_id_2 = f"msg_{message2.id}_0"

            url = reverse("draft-message-detail", kwargs={"message_id": draft_id})
            response = client.put(
                url,
                {
                    "senderId": str(user_mailbox.id),
                    "attachments": [
                        {
                            "blobId": forwarded_blob_id_1,
                            "name": "test_file.txt",
                        },
                        {
                            "blobId": forwarded_blob_id_2,
                            "name": "second_file.txt",
                        },
                    ],
                },
                format="json",
            )

            # Should fail with validation error (second attachment alone exceeds 1 KB)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "attachments" in response.data

            # Verify no new blob was created (transaction should have rolled back)
            assert models.Blob.objects.count() == blob_count_before_update

            # Verify no new attachment was created
            assert models.Attachment.objects.count() == attachment_count_before_update

            # Verify the draft still has only the first attachment
            draft = models.Message.objects.get(id=draft_id)
            assert draft.attachments.count() == 1
