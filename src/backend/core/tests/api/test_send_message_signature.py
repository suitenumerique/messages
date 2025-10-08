"""Tests for signature handling in the SendMessageView API."""

# pylint: disable=unused-argument

from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models

pytestmark = pytest.mark.django_db


SCHEMA_CUSTOM_ATTRIBUTES = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/user",
    "type": "object",
    "title": "User custom fields",
    "additionalProperties": False,
    "properties": {
        "job_title": {"type": "string"},
        "department": {"type": "string"},
    },
    "required": [],
}


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user with custom attributes."""
    return factories.UserFactory(
        full_name="John Doe",
        custom_attributes={
            "job_title": "Software Engineer",
            "department": "Engineering",
        },
    )


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="mailbox_access")
def fixture_mailbox_access(user, mailbox):
    """Create mailbox access for the user with SENDER role."""
    return factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.SENDER
    )


@pytest.fixture(name="thread")
def fixture_thread(mailbox):
    """Create a thread for a mailbox."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return thread


@pytest.fixture(name="draft_message")
def fixture_draft_message(thread, mailbox):
    """Create a draft message for testing."""
    sender_contact = factories.ContactFactory(mailbox=mailbox)
    return factories.MessageFactory(
        thread=thread,
        sender=sender_contact,
        is_draft=True,
        subject="Test Draft Message",
    )


@pytest.fixture(name="signature_template")
def fixture_signature_template(mailbox):
    """Create a signature template."""
    return factories.MessageTemplateFactory(
        name="Professional Signature",
        html_body="<p>Best regards,<br>{name}<br>{job_title}<br>{department}</p>",
        text_body="Best regards,\n{name}\n{job_title}\n{department}",
        type=enums.MessageTemplateTypeChoices.SIGNATURE,
        is_active=True,
        mailbox=mailbox,
    )


class TestSendMessageAPIView:
    """Test SendMessageAPIView."""

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_api_send_message_with_signature_placeholder(
        self,
        user,
        mailbox_access,
        mailbox,
        draft_message,
        signature_template,
    ):
        """Test sending a message with signature via FK relationship."""
        # Set the signature on the draft message
        draft_message.signature = signature_template
        draft_message.save()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request
            response = client.post(
                reverse("send-message"),
                {
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "Hello world!",
                    "htmlBody": "<p>Hello world!</p>",
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            message = models.Message.objects.get(id=draft_message.id)
            content = message.blob.get_content().decode()
            assert "Hello world!" in content
            assert (
                "Best regards,<br>John Doe<br>Software Engineer<br>Engineering"
                in content
            )

    def test_api_send_message_without_signature_placeholder(
        self, user, mailbox_access, mailbox, draft_message
    ):
        """Test sending a message without signature placeholder."""
        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the prepare_outbound_message function
        with patch("core.api.viewsets.send.prepare_outbound_message") as mock_prepare:
            mock_prepare.return_value = True

            # Mock the send_message_task
            with patch("core.api.viewsets.send.send_message_task") as mock_task:
                mock_task_instance = MagicMock()
                mock_task_instance.id = "task-123"
                mock_task.delay.return_value = mock_task_instance

                # Send request
                response = client.post(
                    reverse("send-message"),
                    {
                        "messageId": str(draft_message.id),
                        "senderId": str(mailbox.id),
                        "textBody": "Hello world!",
                        "htmlBody": "<p>Hello world!</p>",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
                assert response.data["task_id"] == "task-123"

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_api_send_message_with_text_body_only(
        self,
        user,
        mailbox_access,
        mailbox,
        draft_message,
        signature_template,
    ):
        """Test sending a message with text body only and signature via FK."""
        # Set the signature on the draft message
        draft_message.signature = signature_template
        draft_message.save()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request with text body only
            response = client.post(
                reverse("send-message"),
                {
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "Hello world!",
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            message = models.Message.objects.get(id=draft_message.id)
            content = message.blob.get_content().decode()
            assert "Hello world!" in content
            assert (
                "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering"
                in content
            )

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_api_send_message_with_html_body_only(
        self,
        user,
        mailbox_access,
        mailbox,
        draft_message,
        signature_template,
    ):
        """Test sending a message with HTML body only and signature via FK."""
        # Set the signature on the draft message
        draft_message.signature = signature_template
        draft_message.save()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request with HTML body only
            response = client.post(
                reverse("send-message"),
                {
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "htmlBody": "<p>Hello world!</p>",
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            message = models.Message.objects.get(id=draft_message.id)
            content = message.blob.get_content().decode()
            assert "Hello world!" in content
            assert (
                "Best regards,<br>John Doe<br>Software Engineer<br>Engineering"
                in content
            )

    @override_settings(SCHEMA_CUSTOM_ATTRIBUTES_USER=SCHEMA_CUSTOM_ATTRIBUTES)
    def test_api_send_message_with_signature_and_reply(
        self,
        user,
        mailbox_access,
        mailbox,
        draft_message,
        signature_template,
    ):
        """Test sending a message with signature via FK and reply content."""
        # Create a parent message for reply
        parent_message = factories.MessageFactory(
            thread=draft_message.thread,
            sender=draft_message.sender,
            subject="Re: Original Subject",
        )
        draft_message.parent = parent_message
        draft_message.signature = signature_template
        draft_message.save()

        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request
            response = client.post(
                reverse("send-message"),
                {
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "This is a reply",
                    "htmlBody": "<p>This is a reply</p>",
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            message = models.Message.objects.get(id=draft_message.id)
            content = message.blob.get_content().decode()
            assert "This is a reply" in content
            assert (
                "Best regards,\r\nJohn Doe\r\nSoftware Engineer\r\nEngineering"
                in content
            )

    def test_api_send_message_with_archive_true(
        self,
        user,
        mailbox_access,
        mailbox,
        draft_message,
        signature_template,
    ):
        """Test sending a message with archive=True passes the parameter to the task."""
        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request with HTML body only
            response = client.post(
                reverse("send-message"),
                format="json",
                data={
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "htmlBody": "<p>Hello world!</p>",
                    "archive": True,
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            mock_task.delay.assert_called_once_with(
                str(draft_message.id), must_archive=True
            )

    def test_api_send_message_with_archive_false(
        self, user, mailbox_access, mailbox, draft_message
    ):
        """Test sending a message with archive=False passes the parameter to the task."""
        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request with archive=False
            response = client.post(
                reverse("send-message"),
                format="json",
                data={
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "Hello world!",
                    "htmlBody": "<p>Hello world!</p>",
                    "archive": False,
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            # Verify the task was called with must_archive=False
            mock_task.delay.assert_called_once_with(
                str(draft_message.id), must_archive=False
            )

    def test_api_send_message_without_archive_parameter(
        self, user, mailbox_access, mailbox, draft_message
    ):
        """Test sending a message without archive parameter defaults to False."""
        # Authenticate user
        client = APIClient()
        client.force_authenticate(user=user)

        # Mock the send_message_task
        with patch("core.api.viewsets.send.send_message_task") as mock_task:
            mock_task_instance = MagicMock()
            mock_task_instance.id = "task-123"
            mock_task.delay.return_value = mock_task_instance

            # Send request without archive parameter
            response = client.post(
                reverse("send-message"),
                format="json",
                data={
                    "messageId": str(draft_message.id),
                    "senderId": str(mailbox.id),
                    "textBody": "Hello world!",
                    "htmlBody": "<p>Hello world!</p>",
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["task_id"] == "task-123"

            # Verify the task was called with must_archive=False (default)
            mock_task.delay.assert_called_once_with(
                str(draft_message.id), must_archive=False
            )
