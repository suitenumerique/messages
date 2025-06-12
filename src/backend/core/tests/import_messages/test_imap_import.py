"""Tests for IMAP message import functionality."""

# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter
import datetime
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from django.urls import reverse

import pytest

from core import factories
from core.forms import IMAPImportForm
from core.models import Mailbox, MailDomain, Message, Thread
from core.tasks import import_imap_messages_task

from messages.celery_app import app as celery_app


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
def admin_client(client, admin_user):
    """Create an authenticated admin client."""
    client.force_login(admin_user)
    return client


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def mailbox(db, domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def sample_email():
    """Create a sample email message."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test Subject"
    msg["Date"] = "Thu, 1 Jan 2024 12:00:00 +0000"
    msg.set_content("This is a test message body.")
    return msg.as_bytes()


@pytest.fixture
def mock_imap_connection(sample_email):
    """Mock IMAP connection with sample messages."""
    mock_imap = MagicMock()

    # Mock successful login and folder selection
    mock_imap.login.return_value = ("OK", [b"Logged in"])
    mock_imap.select.return_value = ("OK", [b"1"])

    # Mock message search
    mock_imap.search.return_value = ("OK", [b"1 2 3"])  # Three messages

    # Mock message fetch
    mock_imap.fetch.return_value = ("OK", [(None, sample_email)])

    # Mock close and logout
    mock_imap.close.return_value = ("OK", [b"Closed"])
    mock_imap.logout.return_value = ("OK", [b"Logged out"])
    return mock_imap


def test_imap_import_form_validation(mailbox):
    """Test IMAP import form validation."""
    form_data = {
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
        "use_ssl": True,
        "folder": "INBOX",
        "max_messages": 0,
        "recipient": mailbox.id,  # Will be set in test
    }

    # Test with missing required fields
    form = IMAPImportForm({})
    assert not form.is_valid()
    assert "imap_server" in form.errors
    assert "username" in form.errors
    assert "password" in form.errors
    assert "recipient" in form.errors

    # Test with invalid port
    form_data["imap_port"] = -1
    form = IMAPImportForm(form_data)
    assert not form.is_valid()
    assert "imap_port" in form.errors

    # Test with invalid max_messages
    form_data["imap_port"] = 993
    form_data["max_messages"] = -1
    form = IMAPImportForm(form_data)
    assert not form.is_valid()
    assert "max_messages" in form.errors


def test_imap_import_form_view(admin_client, mailbox):
    """Test the IMAP import form view."""
    url = reverse("admin:core_message_import_imap")

    # Test GET request
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "Import Messages from IMAP" in response.content.decode()

    # Test POST with valid data
    form_data = {
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
        "use_ssl": True,
        "folder": "INBOX",
        "max_messages": 0,
        "recipient": mailbox.id,
    }

    with patch("core.tasks.import_imap_messages_task.delay") as mock_task:
        response = admin_client.post(url, form_data, follow=True)
        assert response.status_code == 200
        assert (
            "Started importing messages from IMAP server" in response.content.decode()
        )
        mock_task.assert_called_once()


@patch("imaplib.IMAP4_SSL")
@patch.object(celery_app.backend, "store_result")
def test_imap_import_task_success(
    mock_store_result, mock_imap4_ssl, mailbox, mock_imap_connection, sample_email
):
    """Test successful IMAP import task execution."""
    mock_imap4_ssl.return_value = mock_imap_connection
    mock_store_result.return_value = None

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    with patch.object(
        import_imap_messages_task, "update_state", mock_task.update_state
    ):
        # Run the task
        task = import_imap_messages_task(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            recipient_id=str(mailbox.id),
        )

        # Verify results
        assert task["status"] == "SUCCESS"
        assert task["result"]["message_status"] == "Completed processing messages"
        assert task["result"]["type"] == "imap"
        assert task["result"]["total_messages"] == 3
        assert task["result"]["success_count"] == 3
        assert task["result"]["failure_count"] == 0
        assert task["result"]["current_message"] == 3

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
                        "type": "imap",
                        "current_message": i,
                    },
                    "error": None,
                },
            )

        # Verify success update
        mock_task.update_state.assert_any_call(
            state="SUCCESS",
            meta=task,
        )

        # Verify messages were created
        assert Message.objects.count() == 3
        assert Thread.objects.count() == 3

        # check one of the messages
        message = Message.objects.last()
        assert message.subject == "Test Subject"
        assert message.sender.email == "sender@example.com"
        assert message.recipients.count() == 1
        assert message.recipients.first().contact.email == "recipient@example.com"
        assert (
            message.get_parsed_field("textBody")[0]["content"]
            == "This is a test message body.\n"
        )
        assert message.attachments.count() == 0
        assert message.thread.messages.count() == 1
        assert message.thread.messages.first() == message
        assert message.created_at == message.thread.messaged_at
        assert message.created_at == datetime.datetime(
            2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc
        )


@pytest.mark.django_db
def test_imap_import_task_login_failure(mailbox):
    """Test IMAP import task with login failure."""
    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    # Mock IMAP connection to raise an error on login
    with (
        patch.object(import_imap_messages_task, "update_state", mock_task.update_state),
        patch("core.tasks.imaplib.IMAP4_SSL") as mock_imap,
    ):
        mock_imap_instance = MagicMock()
        mock_imap.return_value = mock_imap_instance
        mock_imap_instance.login.side_effect = Exception("Login failed")

        # Run the task
        task_result = import_imap_messages_task(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="wrong_password",
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            recipient_id=str(mailbox.id),
        )

        # Verify task result
        assert task_result["status"] == "FAILURE"
        assert task_result["result"]["message_status"] == "Failed to process messages"
        assert task_result["result"]["type"] == "imap"
        assert task_result["result"]["total_messages"] == 0
        assert task_result["result"]["success_count"] == 0
        assert task_result["result"]["failure_count"] == 0
        assert task_result["result"]["current_message"] == 0
        assert "Login failed" in task_result["error"]

        # Verify only failure update was called
        assert mock_task.update_state.call_count == 1
        mock_task.update_state.assert_called_once_with(
            state="FAILURE",
            meta=task_result,
        )

        # Verify no messages were created
        assert Message.objects.count() == 0


@pytest.mark.django_db
def test_imap_import_task_folder_not_found(mailbox):
    """Test IMAP import task with non-existent folder."""
    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    # Mock IMAP connection to raise an error on folder selection
    with (
        patch.object(import_imap_messages_task, "update_state", mock_task.update_state),
        patch("core.tasks.imaplib.IMAP4_SSL") as mock_imap,
    ):
        mock_imap_instance = MagicMock()
        mock_imap.return_value = mock_imap_instance
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])
        mock_imap_instance.select.side_effect = Exception("Folder not found")

        # Run the task
        task_result = import_imap_messages_task(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            use_ssl=True,
            folder="NONEXISTENT",
            max_messages=0,
            recipient_id=str(mailbox.id),
        )

        # Verify task result
        assert task_result["status"] == "FAILURE"
        assert task_result["result"]["message_status"] == "Failed to process messages"
        assert task_result["result"]["type"] == "imap"
        assert task_result["result"]["total_messages"] == 0
        assert task_result["result"]["success_count"] == 0
        assert task_result["result"]["failure_count"] == 0
        assert task_result["result"]["current_message"] == 0
        assert "Folder not found" in task_result["error"]

        # Verify only failure update was called
        assert mock_task.update_state.call_count == 1
        mock_task.update_state.assert_called_once_with(
            state="FAILURE",
            meta=task_result,
        )

        # Verify no messages were created
        assert Message.objects.count() == 0


@patch("imaplib.IMAP4_SSL")
@patch.object(celery_app.backend, "store_result")
def test_imap_import_task_max_messages(
    mock_store_result, mock_imap4_ssl, mailbox, mock_imap_connection
):
    """Test IMAP import task with max_messages limit."""
    mock_store_result.return_value = None
    mock_imap4_ssl.return_value = mock_imap_connection

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    with patch.object(
        import_imap_messages_task, "update_state", mock_task.update_state
    ):
        # Run the task with max_messages=2
        task = import_imap_messages_task(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            use_ssl=True,
            folder="INBOX",
            max_messages=2,
            recipient_id=str(mailbox.id),
        )

        # Verify only 2 messages were processed
        assert task["status"] == "SUCCESS"
        assert task["result"]["message_status"] == "Completed processing messages"
        assert task["result"]["type"] == "imap"
        assert task["result"]["total_messages"] == 2
        assert task["result"]["success_count"] == 2
        assert task["result"]["failure_count"] == 0
        assert task["result"]["current_message"] == 2

        # Verify progress updates were called correctly
        assert mock_task.update_state.call_count == 3  # 2 PROGRESS + 1 SUCCESS

        # Verify progress updates
        for i in range(1, 3):
            mock_task.update_state.assert_any_call(
                state="PROGRESS",
                meta={
                    "status": "PROGRESS",
                    "result": {
                        "message_status": f"Processing message {i} of 2",
                        "total_messages": 2,
                        "success_count": i - 1,  # Previous messages were successful
                        "failure_count": 0,
                        "type": "imap",
                        "current_message": i,
                    },
                    "error": None,
                },
            )

        # Verify success update
        mock_task.update_state.assert_any_call(
            state="SUCCESS",
            meta=task,
        )


@patch("imaplib.IMAP4_SSL")
@patch.object(celery_app.backend, "store_result")
def test_imap_import_task_message_fetch_failure(
    mock_store_result, mock_imap4_ssl, mailbox
):
    """Test IMAP import task with message fetch failure."""
    mock_store_result.return_value = None
    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [b"Logged in"])
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1 2 3"])
    mock_imap.fetch.return_value = ("NO", [b"Message not found"])
    mock_imap4_ssl.return_value = mock_imap

    # Create a mock task instance
    mock_task = MagicMock()
    mock_task.update_state = MagicMock()

    with patch.object(
        import_imap_messages_task, "update_state", mock_task.update_state
    ):
        # Run the task
        task = import_imap_messages_task(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            recipient_id=str(mailbox.id),
        )

        # Verify all messages failed
        assert task["status"] == "SUCCESS"
        assert task["result"]["message_status"] == "Completed processing messages"
        assert task["result"]["type"] == "imap"
        assert task["result"]["total_messages"] == 3
        assert task["result"]["success_count"] == 0
        assert task["result"]["failure_count"] == 3
        assert task["result"]["current_message"] == 3

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
                        "success_count": 0,
                        "failure_count": i - 1,  # Previous messages failed
                        "type": "imap",
                        "current_message": i,
                    },
                    "error": None,
                },
            )

        # Verify success update
        mock_task.update_state.assert_any_call(
            state="SUCCESS",
            meta=task,
        )
