"""Tests for the ImportService class."""

import datetime
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest

import pytest

from core import factories
from core.enums import MailboxRoleChoices
from core.models import Mailbox, MailDomain, Message
from core.services.import_service import ImportService


@pytest.fixture
def user(db):
    """Create a user."""
    return factories.UserFactory()


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
def mailbox(domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def eml_file():
    """Get test eml file from test data."""
    with open("core/tests/resources/message.eml", "rb") as f:
        return SimpleUploadedFile("test.eml", f.read(), content_type="message/rfc822")


@pytest.fixture
def mbox_file():
    """Get test mbox file from test data."""
    with open("core/tests/resources/messages.mbox", "rb") as f:
        return SimpleUploadedFile(
            "test.mbox", f.read(), content_type="application/mbox"
        )


@pytest.fixture
def mock_request():
    """Create a mock request object."""
    request = MagicMock(spec=HttpRequest)
    request._messages = MagicMock()
    return request


@pytest.mark.django_db
def test_import_file_eml_by_superuser(admin_user, mailbox, eml_file, mock_request):
    """Test successful EML file import for superuser."""
    success, response_data = ImportService.import_file(
        file=eml_file,
        recipient=mailbox,
        user=admin_user,
        request=mock_request,
    )

    assert success is True
    assert response_data["type"] == "eml"
    assert response_data["success"] is True
    assert Message.objects.count() == 1

    message = Message.objects.first()
    assert message.subject == "Mon mail avec joli pj"
    assert message.attachments.count() == 1
    assert message.sender.email == "sender@example.com"
    assert message.recipients.get().contact.email == "recipient@example.com"
    assert message.sent_at == message.thread.messaged_at
    assert message.sent_at == datetime.datetime(
        2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc
    )


@pytest.mark.django_db
def test_import_file_eml_by_user_with_access(user, mailbox, eml_file, mock_request):
    """Test successful EML file import by user with access on mailbox."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    success, response_data = ImportService.import_file(
        file=eml_file,
        recipient=mailbox,
        user=user,
        request=mock_request,
    )

    assert success is True
    assert response_data["type"] == "eml"
    assert response_data["success"] is True
    assert Message.objects.count() == 1

    message = Message.objects.first()
    assert message.subject == "Mon mail avec joli pj"
    assert message.attachments.count() == 1
    assert message.sender.email == "sender@example.com"
    assert message.recipients.get().contact.email == "recipient@example.com"
    assert message.sent_at == message.thread.messaged_at
    assert message.sent_at == datetime.datetime(
        2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc
    )


@pytest.mark.django_db
def test_import_file_mbox_by_superuser_task(
    admin_user, mailbox, mbox_file, mock_request
):
    """Test successful MBOX file import by superuser."""

    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = ImportService.import_file(
            file=mbox_file,
            recipient=mailbox,
            user=admin_user,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "mbox"
        assert response_data["task_id"] == "fake-task-id"
        mock_task.assert_called_once()


@pytest.mark.django_db
def test_import_file_mbox_by_user_with_access_task(
    user, mailbox, mbox_file, mock_request
):
    """Test successful MBOX file import by user with access on mailbox."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = ImportService.import_file(
            file=mbox_file,
            recipient=mailbox,
            user=user,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "mbox"
        assert response_data["task_id"] == "fake-task-id"
        mock_task.assert_called_once()


@pytest.mark.django_db
def test_import_file_mbox_by_superuser_db_creation(
    admin_user, mailbox, mbox_file, mock_request
):
    """Test file import for a superuser"""
    success, response_data = ImportService.import_file(
        file=mbox_file,
        recipient=mailbox,
        user=admin_user,
        request=mock_request,
    )

    assert success is True
    assert response_data["type"] == "mbox"
    assert Message.objects.count() == 3
    message = Message.objects.last()
    assert message.subject == "Mon mail avec joli pj"
    assert message.attachments.count() == 1
    assert message.sender.email == "julie.sender@example.com"
    assert message.recipients.get().contact.email == "jean.recipient@example.com"
    assert message.sent_at == message.thread.messaged_at
    assert message.sent_at == datetime.datetime(
        2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc
    )


def test_import_file_no_access(user, domain, eml_file, mock_request):
    """Test file import without mailbox access."""
    # Create a mailbox the user does NOT have access to
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)

    success, response_data = ImportService.import_file(
        file=eml_file,
        recipient=mailbox,
        user=user,
        request=mock_request,
    )

    assert success is False
    assert "You do not have access to this mailbox" in response_data["detail"]
    assert Message.objects.count() == 0


def test_import_file_invalid_file(admin_user, mailbox, mock_request):
    """Test import with invalid file type."""
    invalid_file = SimpleUploadedFile(
        "test.txt", b"Not an email file", content_type="text/plain"
    )

    success, response_data = ImportService.import_file(
        file=invalid_file,
        recipient=mailbox,
        user=admin_user,
        request=mock_request,
    )

    assert success is False
    assert Message.objects.count() == 0


def test_import_imap_by_superuser(admin_user, mailbox, mock_request):
    """Test successful IMAP import."""
    with patch("core.tasks.import_imap_messages_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = ImportService.import_imap(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=admin_user,
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "imap"
        assert response_data["task_id"] == "fake-task-id"
        mock_task.assert_called_once()


def test_import_imap_by_user_with_access(user, mailbox, mock_request):
    """Test successful IMAP import by user with access on mailbox."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    with patch("core.tasks.import_imap_messages_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = ImportService.import_imap(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=user,
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "imap"
        assert response_data["task_id"] == "fake-task-id"
        mock_task.assert_called_once()


def test_import_imap_no_access(user, domain, mock_request):
    """Test IMAP import without mailbox access."""
    # Create a mailbox the user does NOT have access to
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)

    success, response_data = ImportService.import_imap(
        imap_server="imap.example.com",
        imap_port=993,
        username="test@example.com",
        password="password123",
        recipient=mailbox,
        user=user,
        use_ssl=True,
        folder="INBOX",
        max_messages=0,
        request=mock_request,
    )

    assert success is False
    assert "access" in response_data["detail"]


def test_import_imap_task_error(admin_user, mailbox, mock_request):
    """Test IMAP import with task error."""
    # Add access to mailbox
    mailbox.accesses.create(user=admin_user, role="admin")

    with patch("core.tasks.import_imap_messages_task.delay") as mock_task:
        mock_task.side_effect = Exception("Task error")
        success, response_data = ImportService.import_imap(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=admin_user,
            use_ssl=True,
            folder="INBOX",
            max_messages=0,
            request=mock_request,
        )

        assert success is False
        assert "detail" in response_data
        assert "Task error" in response_data["detail"]


def test_import_imap_messages_by_superuser(admin_user, mailbox, mock_request):
    """Test importing messages from IMAP server by superuser."""

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])
        mock_imap_instance.list.return_value = ("OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"',
            b'(\\HasNoChildren) "/" "[Gmail]/Drafts"'
        ])
        mock_imap_instance.select.return_value = ("OK", [b"1"])
        mock_imap_instance.search.return_value = ("OK", [b"1 2"])

        # Mock 2 messages
        message1 = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message 1
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body 1"""

        message2 = b"""From: sender@example.com 
To: recipient@example.com
Subject: Test Message 2
Date: Mon, 26 May 2025 11:00:00 +0000

Test message body 2"""

        mock_imap_instance.fetch.side_effect = [
            ("OK", [(b"1", message1)]),
            ("OK", [(b"2", message2)]),
        ]

        success, response_data = ImportService.import_imap(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=admin_user,
            use_ssl=True,
            folder="INBOX",
            max_messages=2,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "imap"
        assert "task_id" in response_data

        # Verify IMAP calls
        mock_imap_instance.login.assert_called_once_with(
            "test@example.com", "password123"
        )
        mock_imap_instance.select.assert_called_once_with("INBOX")
        mock_imap_instance.search.assert_called_once_with(None, "ALL")
        assert mock_imap_instance.fetch.call_count == 2
        assert Message.objects.count() == 2
        message = Message.objects.last()
        assert message.subject == "Test Message 1"
        assert message.sender.email == "sender@example.com"
        assert message.recipients.get().contact.email == "recipient@example.com"
        assert message.sent_at == datetime.datetime(
            2025, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc
        )


def test_import_imap_messages_user_with_access(user, mailbox, mock_request):
    """Test importing messages from IMAP server by user with access on mailbox."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])
        mock_imap_instance.list.return_value = ("OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"',
            b'(\\HasNoChildren) "/" "[Gmail]/Drafts"'
        ])
        mock_imap_instance.select.return_value = ("OK", [b"1"])
        mock_imap_instance.search.return_value = ("OK", [b"1 2"])

        # Mock 2 messages
        message1 = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message 1
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body 1"""

        message2 = b"""From: sender@example.com 
To: recipient@example.com
Subject: Test Message 2
Date: Mon, 26 May 2025 11:00:00 +0000

Test message body 2"""

        mock_imap_instance.fetch.side_effect = [
            ("OK", [(b"1", message1)]),
            ("OK", [(b"2", message2)]),
        ]

        success, response_data = ImportService.import_imap(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=user,
            use_ssl=True,
            folder="INBOX",
            max_messages=2,
            request=mock_request,
        )

        assert success is True
        assert response_data["type"] == "imap"
        assert "task_id" in response_data

        # Verify IMAP calls
        mock_imap_instance.login.assert_called_once_with(
            "test@example.com", "password123"
        )
        mock_imap_instance.select.assert_called_once_with("INBOX")
        mock_imap_instance.search.assert_called_once_with(None, "ALL")
        assert mock_imap_instance.fetch.call_count == 2
        assert Message.objects.count() == 2
        message = Message.objects.last()
        assert message.subject == "Test Message 1"
        assert message.sender.email == "sender@example.com"
        assert message.recipients.get().contact.email == "recipient@example.com"
        assert message.sent_at == datetime.datetime(
            2025, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc
        )
