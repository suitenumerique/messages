"""Tests for message import functionality in the admin interface."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter

import datetime
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from core import factories
from core.models import Mailbox, MailDomain, Message, Thread
from core.services.importer.tasks import process_eml_file_task, process_mbox_file_task


def mock_storage_open(content: bytes):
    """Helper to create a mock storage that returns the given content."""

    def create_file(*args, **kwargs):
        return BytesIO(content)

    mock_storage = Mock()
    mock_storage.open = Mock(side_effect=create_file)
    return mock_storage


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
        patch("core.services.importer.tasks.process_eml_file_task.delay") as mock_delay,
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
            f"Started processing EML file for recipient {mailbox}"
            in response.content.decode()
        )
        mock_delay.assert_called_once()

        # Mock storage for running task synchronously
        mock_storage = mock_storage_open(eml_file)

        with patch("core.services.importer.tasks.storages") as mock_storages:
            mock_storages.__getitem__.return_value = mock_storage
            # Run the task synchronously for testing
            task_result = process_eml_file_task(
                file_key="test-file-key.eml", recipient_id=str(mailbox.id)
            )
            assert (
                task_result["result"]["message_status"]
                == "Completed processing message"
            )
            assert task_result["result"]["type"] == "eml"
            assert task_result["result"]["total_messages"] == 1
            assert task_result["result"]["success_count"] == 1
            assert task_result["result"]["failure_count"] == 0
            assert task_result["result"]["current_message"] == 1

            # Verify progress updates were called correctly
            assert mock_task.update_state.call_count == 2

            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
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
            mock_task.update_state.assert_called_with(
                state="SUCCESS",
                meta={
                    "result": task_result["result"],
                    "error": None,
                },
            )

            # check that the message was created
            assert Message.objects.count() == 1
            message = Message.objects.first()
            assert message.subject == "Mon mail avec joli pj"
            assert message.has_attachments is True
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

    # Mock storage
    mock_storage = mock_storage_open(mbox_file)

    # Mock the task's update_state method to avoid database operations
    with (
        patch.object(process_mbox_file_task, "update_state", mock_task.update_state),
        patch("core.services.importer.tasks.storages") as mock_storages,
    ):
        mock_storages.__getitem__.return_value = mock_storage
        # Run the task synchronously for testing
        task_result = process_mbox_file_task(
            file_key="test-file-key.mbox", recipient_id=str(mailbox.id)
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
        assert mock_task.update_state.call_count == 5  # 4 PROGRESS + 1 SUCCESS

        # Verify progress updates
        for i in range(1, 4):
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
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
            meta={
                "result": task_result["result"],
                "error": None,
            },
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
        assert messages[0].has_attachments is True

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
        f"Started processing MBOX file for recipient {mailbox}"
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


@pytest.mark.django_db
def test_import_message_to_different_mailbox_same_domain(domain):
    """Test that importing a message addressed to another mailbox on the same domain
    should ONLY deliver it to the importing mailbox, not to the recipient mailbox.

    This test verifies correct behavior: even if a message is addressed to another
    mailbox on the same domain, during import it should only be imported into the
    importing mailbox. This prevents users from adding messages to their colleagues'
    mailboxes.

    The bug being tested: deliver_inbound_message calls check_local_recipient even
    during imports, which might cause issues if the importing mailbox validation fails
    or if there are edge cases with domain checking during import.
    """
    # Create two mailboxes on the same domain
    mailbox_a = Mailbox.objects.create(local_part="mailbox_a", domain=domain)
    mailbox_b = Mailbox.objects.create(local_part="mailbox_b", domain=domain)

    # Create an EML message addressed to mailbox_b (but we're importing from mailbox_a)
    eml_content = b"""From: sender@example.com
To: mailbox_b@example.com
Subject: Test message for mailbox_b
Date: Mon, 26 May 2025 20:13:44 +0200
Message-ID: <test-message-id@example.com>

This is a test message addressed to mailbox_b.
"""

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    # Mock storage
    mock_storage = mock_storage_open(eml_content)

    # Import from mailbox_a
    with (
        patch.object(process_eml_file_task, "update_state", mock_task.update_state),
        patch("core.services.importer.tasks.storages") as mock_storages,
    ):
        mock_storages.__getitem__.return_value = mock_storage
        # Run the task synchronously for testing, importing from mailbox_a
        task_result = process_eml_file_task(
            file_key="test-file-key.eml", recipient_id=str(mailbox_a.id)
        )

        # The import should succeed
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1

        # Verify correct behavior: message is ONLY in mailbox_a (the importing mailbox)
        # and NOT in mailbox_b (the recipient mailbox)
        messages_in_mailbox_a = Message.objects.filter(
            thread__accesses__mailbox=mailbox_a
        ).count()
        messages_in_mailbox_b = Message.objects.filter(
            thread__accesses__mailbox=mailbox_b
        ).count()

        # Correct behavior: message should be in importing mailbox only
        assert messages_in_mailbox_a == 1, (
            "Message was correctly delivered to importing mailbox"
        )
        assert messages_in_mailbox_b == 0, (
            "Message was correctly NOT delivered to colleague's mailbox"
        )

        # Verify the message recipient contact is recorded correctly
        message = Message.objects.filter(thread__accesses__mailbox=mailbox_a).first()
        assert message is not None
        # The recipient contact should be mailbox_b@example.com, but the message
        # should be in mailbox_a's threads
        recipient_emails = [
            recipient.contact.email for recipient in message.recipients.all()
        ]
        assert "mailbox_b@example.com" in recipient_emails, (
            "Recipient contact should be recorded"
        )


@pytest.mark.django_db
def test_import_message_with_from_equal_to_mailbox_sets_is_sender(domain):
    """Test that importing a message where From: equals the importing mailbox
    correctly sets is_sender=True.
    """
    # Create a mailbox
    mailbox = Mailbox.objects.create(local_part="testuser", domain=domain)
    mailbox_email = str(mailbox)  # e.g., "testuser@example.com"

    # Create an EML message with From: equal to the mailbox email
    eml_content = f"""From: {mailbox_email}
To: recipient@example.com
Subject: Test sent message
Date: Mon, 26 May 2025 20:13:44 +0200
Message-ID: <test-sent-message-id@example.com>

This is a test message sent from the mailbox.
""".encode("utf-8")

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    # Mock storage
    mock_storage = mock_storage_open(eml_content)

    # Import the message
    with (
        patch.object(process_eml_file_task, "update_state", mock_task.update_state),
        patch("core.services.importer.tasks.storages") as mock_storages,
    ):
        mock_storages.__getitem__.return_value = mock_storage
        # Run the task synchronously for testing
        task_result = process_eml_file_task(
            file_key="test-file-key.eml", recipient_id=str(mailbox.id)
        )

        # The import should succeed
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1

        # Verify the message was created
        assert Message.objects.count() == 1
        message = Message.objects.filter(thread__accesses__mailbox=mailbox).first()
        assert message is not None

        # Verify is_sender is correctly set to True
        assert message.is_sender is True, (
            "Message with From: equal to importing mailbox should have is_sender=True"
        )

        # Verify is_unread is False for sent messages
        assert message.is_unread is False, "Sent messages should not be unread"

        # Verify the sender contact is correct
        assert message.sender.email == mailbox_email
