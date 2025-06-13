"""Tests for message import functionality in the admin interface."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter

import datetime
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from core import factories
from core.models import Mailbox, MailDomain, Message, Thread
from core.tasks import process_eml_file_task, process_mbox_file_task


@pytest.fixture
def admin_user(db):
    """Create a superuser for admin access."""
    return factories.UserFactory(
        email="admin@example.com",
        password="adminpass123",
        full_name="Admin User",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def mailbox(db, domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def eml_file():
    """Get test eml file from test data."""
    with open("core/tests/resources/message.eml", "rb") as f:
        return f.read()


@pytest.fixture
def mbox_file():
    """Get test mbox file from test data."""
    with open("core/tests/resources/messages.mbox", "rb") as f:
        return f.read()


@pytest.fixture
def admin_client(client, admin_user):
    """Create an authenticated admin client."""
    client.force_login(admin_user)
    return client


def test_import_button_visibility(admin_client):
    """Test that the import button is visible to admin users."""
    url = reverse("admin:core_message_changelist")
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "Import Messages" in response.content.decode()


def test_import_form_access(admin_client, mailbox):
    """Test access to the import form."""
    url = reverse("admin:core_message_import_messages")
    response = admin_client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Import Messages" in content
    assert "Import File" in content
    assert "Mailbox Recipient" in content
    assert str(mailbox) in content  # Check that the mailbox appears in the dropdown


def test_import_eml_file(admin_client, eml_file, mailbox):
    """Test submitting the import form with a valid EML file."""
    url = reverse("admin:core_message_import_messages")

    # Create a SimpleUploadedFile from the bytes content
    test_file = SimpleUploadedFile(
        "test.eml",
        eml_file,  # eml_file is already bytes
        content_type="message/rfc822",
    )

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    with (
        patch("core.tasks.process_eml_file_task.delay") as mock_delay,
        patch.object(process_eml_file_task, "update_state", mock_task.update_state),
    ):
        mock_delay.return_value.id = "fake-task-id"
        # Submit the form
        response = admin_client.post(
            url, {"import_file": test_file, "recipient": mailbox.id}, follow=True
        )

        # Check response
        assert response.status_code == 200
        assert (
            f"Started processing EML file: test.eml for recipient {mailbox}"
            in response.content.decode()
        )
        mock_delay.assert_called_once()

        # Run the task synchronously for testing
        task_result = process_eml_file_task(
            file_content=eml_file, recipient_id=str(mailbox.id)
        )
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["message_status"] == "Completed processing message"
        assert task_result["result"]["type"] == "eml"
        assert task_result["result"]["total_messages"] == 1
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0
        assert task_result["result"]["current_message"] == 1

        # Verify progress updates were called correctly
        assert mock_task.update_state.call_count == 2  # PROGRESS + SUCCESS

        # Verify progress update
        mock_task.update_state.assert_any_call(
            state="PROGRESS",
            meta={
                "status": "PROGRESS",
                "result": {
                    "message_status": "Processing message 1 of 1",
                    "total_messages": 1,
                    "success_count": 0,
                    "failure_count": 0,
                    "type": "eml",
                    "current_message": 1,
                },
                "error": None,
            },
        )

        # Verify success update
        mock_task.update_state.assert_any_call(
            state="SUCCESS",
            meta=task_result,
        )

        # check that the message was created
        assert Message.objects.count() == 1
        message = Message.objects.first()
        assert message.subject == "Mon mail avec joli pj"
        assert message.attachments.count() == 1
        assert message.sender.email == "sender@example.com"
        assert message.recipients.get().contact.email == "recipient@example.com"
        assert message.sent_at == message.thread.messaged_at
        assert message.sent_at == (
            datetime.datetime(2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc)
        )


@pytest.mark.django_db
def test_process_mbox_file_task(mailbox, mbox_file):
    """Test the Celery task that processes MBOX files."""
    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    # Mock the task's update_state method to avoid database operations
    with patch.object(process_mbox_file_task, "update_state", mock_task.update_state):
        # Run the task synchronously for testing
        task_result = process_mbox_file_task(
            file_content=mbox_file, recipient_id=str(mailbox.id)
        )
        assert task_result["status"] == "SUCCESS"
        assert (
            task_result["result"]["message_status"] == "Completed processing messages"
        )
        assert task_result["result"]["type"] == "mbox"
        assert (
            task_result["result"]["total_messages"] == 3
        )  # Three messages in the test MBOX file
        assert task_result["result"]["success_count"] == 3
        assert task_result["result"]["failure_count"] == 0
        assert task_result["result"]["current_message"] == 3

        # Verify progress updates were called correctly
        assert mock_task.update_state.call_count == 4  # 3 PROGRESS + 1 SUCCESS

        # Verify progress updates
        for i in range(1, 4):
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "status": "PROGRESS",
                    "result": {
                        "message_status": f"Processing message {i} of 3",
                        "total_messages": 3,
                        "success_count": i - 1,  # Previous messages were successful
                        "failure_count": 0,
                        "type": "mbox",
                        "current_message": i,
                    },
                    "error": None,
                },
            )

        # Verify success update
        mock_task.update_state.assert_any_call(
            state="SUCCESS",
            meta=task_result,
        )

        # Verify messages were created
        assert Message.objects.count() == 3
        messages = Message.objects.order_by("created_at")

        # Check thread for each message
        assert messages[0].thread is not None
        assert messages[1].thread is not None
        assert messages[2].thread is not None
        assert messages[2].thread.messages.count() == 2
        assert messages[1].thread == messages[2].thread
        # Check created_at dates match between messages and threads
        assert messages[0].sent_at == messages[0].thread.messaged_at
        assert messages[2].sent_at == messages[1].thread.messaged_at
        assert messages[2].sent_at == (
            datetime.datetime(2025, 5, 26, 20, 18, 4, tzinfo=datetime.timezone.utc)
        )

        # Check messages
        assert messages[0].subject == "Mon mail avec joli pj"
        assert messages[0].attachments.count() == 1

        assert messages[1].subject == "Je t'envoie encore un message..."
        body1 = messages[1].get_parsed_field("textBody")[0]["content"]
        assert "Lorem ipsum dolor sit amet" in body1

        assert messages[2].subject == "Re: Je t'envoie encore un message..."
        body2 = messages[2].get_parsed_field("textBody")[0]["content"]
        assert "Yes !" in body2
        assert "Lorem ipsum dolor sit amet" in body2


def test_upload_mbox_file(admin_client, mailbox, mbox_file):
    """Test uploading and processing an mbox file."""
    url = reverse("admin:core_message_import_messages")

    # Create a test MBOX file
    mbox_file = SimpleUploadedFile(
        "test.mbox", mbox_file, content_type="application/mbox"
    )

    # Submit the form
    response = admin_client.post(
        url, {"import_file": mbox_file, "recipient": mailbox.id}, follow=True
    )

    # Check response
    assert response.status_code == 200
    assert (
        f"Started processing MBOX file: test.mbox for recipient {mailbox}"
        in response.content.decode()
    )
    assert Message.objects.count() == 3
    assert Thread.objects.count() == 2


def test_import_form_invalid_file(admin_client, mailbox):
    """Test submitting the import form with an invalid file."""
    url = reverse("admin:core_message_import_messages")

    # Create an invalid file (not EML or MBOX)
    invalid_file = SimpleUploadedFile(
        "test.txt", b"Not an email file", content_type="text/plain"
    )

    # Submit the form
    response = admin_client.post(
        url, {"import_file": invalid_file, "recipient": mailbox.id}, follow=True
    )

    # Check response
    assert response.status_code == 200
    # The form should still be displayed with an error
    assert "Import Messages" in response.content.decode()
    assert (
        "File must be either an EML (.eml) or MBOX (.mbox) file"
        in response.content.decode()
    )


def test_import_form_no_file(admin_client, mailbox):
    """Test submitting the import form without a file."""
    url = reverse("admin:core_message_import_messages")

    # Submit the form without a file but with recipient
    response = admin_client.post(url, {"recipient": mailbox.id}, follow=True)

    # Check response
    assert response.status_code == 200
    # The form should still be displayed with an error
    assert "Import Messages" in response.content.decode()


def test_import_form_no_recipient(admin_client, eml_file):
    """Test submitting the import form without a recipient."""
    url = reverse("admin:core_message_import_messages")

    # Create a test EML file
    eml_file = SimpleUploadedFile("test.eml", eml_file, content_type="message/rfc822")

    # Submit the form without recipient
    response = admin_client.post(url, {"import_file": eml_file}, follow=True)

    # Check response
    assert response.status_code == 200
    # The form should still be displayed with an error
    assert "Import Messages" in response.content.decode()
    assert "This field is required" in response.content.decode()
