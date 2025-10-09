"""Tests for IMAP message import functionality."""

# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter
import datetime
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from django.urls import reverse

import pytest

from core import enums, factories
from core.forms import IMAPImportForm
from core.models import Mailbox, MailDomain, Message, Thread
from core.services.importer.tasks import import_imap_messages_task

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
def email_with_duplicate_recipients():
    """Create an email message with duplicate recipients."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com, recipient@example.com"  # Duplicate TO
    msg["Cc"] = "cc@example.com, cc@example.com"  # Duplicate CC
    msg["Subject"] = "Test Subject with Duplicates"
    msg["Message-ID"] = "<duplicate-test123@example.com>"
    msg["Date"] = "Thu, 1 Jan 2024 12:00:00 +0000"
    msg.set_content("This is a test message with duplicate recipients.")
    return msg.as_bytes()


@pytest.fixture
def mock_imap_connection(sample_email):
    """Mock IMAP connection with sample messages."""
    mock_imap = MagicMock()

    # Mock successful login and folder selection
    mock_imap.login.return_value = ("OK", [b"Logged in"])

    # Mock list folders - return INBOX folder
    mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    mock_imap.select.return_value = ("OK", [b"1"])

    # Mock message search
    mock_imap.search.return_value = ("OK", [b"1 2 3"])  # Three messages

    # Mock message fetch with proper IMAP format including flags
    mock_imap.fetch.return_value = ("OK", [(b"1 (FLAGS (\\Seen))", sample_email)])

    # Mock close and logout
    mock_imap.close.return_value = ("OK", [b"Closed"])
    mock_imap.logout.return_value = ("OK", [b"Logged out"])
    return mock_imap


@pytest.fixture
def mock_imap_connection_with_duplicates(
    mock_imap_connection, email_with_duplicate_recipients
):
    """Mock IMAP connection for duplicate recipient tests - override fetch behavior."""
    # Copy the original fixture configuration
    mock_imap = mock_imap_connection

    # Override specific behaviors for duplicate test
    mock_imap.search.return_value = ("OK", [b"1"])  # Only 1 message
    mock_imap.fetch.return_value = (
        "OK",
        [(b"1 (FLAGS (\\Seen))", email_with_duplicate_recipients)],
    )

    return mock_imap


def test_imap_import_form_validation(mailbox):
    """Test IMAP import form validation."""
    form_data = {
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
        "use_ssl": True,
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
        "recipient": mailbox.id,
    }

    with patch(
        "core.services.importer.tasks.import_imap_messages_task.delay"
    ) as mock_task:
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
            recipient_id=str(mailbox.id),
        )

        # Verify results
        assert task["status"] == "SUCCESS"
        assert (
            task["result"]["message_status"]
            == "Completed processing messages from folder 'INBOX'"
        )
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
                    "result": {
                        "message_status": f"Processing message {i} of 3",
                        "total_messages": 3,
                        "success_count": i,  # Current message was successful
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
        patch("core.services.importer.imap.imaplib.IMAP4_SSL") as mock_imap,
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
            recipient_id=str(mailbox.id),
        )

        # Verify task result
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
            meta={
                "result": task_result["result"],
                "error": task_result["error"],
            },
        )

        # Verify no messages were created
        assert Message.objects.count() == 0


@patch("imaplib.IMAP4_SSL")
@patch.object(celery_app.backend, "store_result")
def test_imap_import_task_message_fetch_failure(
    mock_store_result, mock_imap4_ssl, mailbox
):
    """Test IMAP import task with message fetch failure."""
    mock_store_result.return_value = None
    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [b"Logged in"])

    # Mock list folders - return INBOX folder
    mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1 2 3"])
    # Mock fetch to return error for all messages
    mock_imap.fetch.side_effect = Exception("Message fetch failed")
    mock_imap.close.return_value = ("OK", [b"Closed"])
    mock_imap.logout.return_value = ("OK", [b"Logged out"])
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
            recipient_id=str(mailbox.id),
        )

        # Verify all messages failed
        assert task["status"] == "SUCCESS"
        assert (
            task["result"]["message_status"]
            == "Completed processing messages from folder 'INBOX'"
        )
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
                    "result": {
                        "message_status": f"Processing message {i} of 3",
                        "total_messages": 3,
                        "success_count": 0,
                        "failure_count": i,  # Current message failed
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


@patch("core.mda.inbound.logger")
@patch("imaplib.IMAP4_SSL")
@patch.object(celery_app.backend, "store_result")
def test_imap_import_task_duplicate_recipients(
    mock_store_result,
    mock_imap4_ssl,
    mock_logger,
    mailbox,
    mock_imap_connection_with_duplicates,
):
    """Test IMAP import task with duplicate recipients handles deduplication correctly."""
    mock_imap4_ssl.return_value = mock_imap_connection_with_duplicates
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
            recipient_id=str(mailbox.id),
        )

        # Verify results
        assert task["status"] == "SUCCESS"
        assert (
            task["result"]["message_status"]
            == "Completed processing messages from folder 'INBOX'"
        )
        assert task["result"]["type"] == "imap"
        assert task["result"]["total_messages"] == 1
        assert task["result"]["success_count"] == 1
        assert task["result"]["failure_count"] == 0
        assert task["result"]["current_message"] == 1

        # Verify messages were created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1

        # Check the message
        message = Message.objects.first()
        assert message.subject == "Test Subject with Duplicates"
        assert message.sender.email == "sender@example.com"

        # Verify recipients - should have unique recipients only
        recipients = message.recipients.all()
        recipient_emails = [r.contact.email for r in recipients]

        # Should have unique recipients (no duplicates)
        assert len(recipient_emails) == len(set(recipient_emails))

        # Should have the expected recipients
        assert "recipient@example.com" in recipient_emails
        assert "cc@example.com" in recipient_emails

        # Verify recipient types
        to_recipients = message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.TO
        )
        cc_recipients = message.recipients.filter(
            type=enums.MessageRecipientTypeChoices.CC
        )

        assert to_recipients.count() == 1  # Only one TO recipient (duplicate removed)
        assert cc_recipients.count() == 1  # Only one CC recipient (duplicate removed)

        # Verify the content
        assert (
            message.get_parsed_field("textBody")[0]["content"]
            == "This is a test message with duplicate recipients.\n"
        )

        # Critical: Verify that no validation errors were logged
        # This ensures the deduplication logic works correctly
        mock_logger.error.assert_not_called()
