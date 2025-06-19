"""Test messages import."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter

import datetime
import hashlib
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from rest_framework.test import APIClient

from core import factories
from core.enums import MailboxRoleChoices
from core.models import Blob, Mailbox, MailDomain, Message, Thread
from core.tasks import process_eml_file_task, process_mbox_file_task

pytestmark = pytest.mark.django_db

IMPORT_FILE_URL = "/api/v1.0/import/file/"
IMPORT_IMAP_URL = "/api/v1.0/import/imap/"


@pytest.fixture
def user(db):
    """Create a user."""
    return factories.UserFactory()


@pytest.fixture
def api_client(user):
    """Create an API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def mailbox(domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def eml_file_path():
    """Get the path to the EML file."""
    return "core/tests/resources/message.eml"


@pytest.fixture
def mbox_file_path():
    """Get the path to the MBOX file."""
    return "core/tests/resources/messages.mbox"


@pytest.fixture
def eml_file():
    """Get test eml file from test data."""
    with open("core/tests/resources/message.eml", "rb") as f:
        return SimpleUploadedFile("test.eml", f.read(), content_type="message/rfc822")


@pytest.fixture
def mbox_file(mbox_file_path):
    """Get test mbox file from test data."""
    with open(mbox_file_path, "rb") as f:
        return SimpleUploadedFile(
            "test.mbox", f.read(), content_type="application/mbox"
        )


@pytest.fixture
def blob_mbox(mbox_file, mailbox):
    """Create a blob from a file."""
    # Read the file content once
    file_content = mbox_file.read()
    expected_hash = hashlib.sha256(file_content).hexdigest()
    return Blob.objects.create(
        raw_content=file_content,
        type=mbox_file.content_type,
        size=mbox_file.size,
        mailbox=mailbox,
        sha256=expected_hash,
    )


@pytest.fixture
def blob_eml(eml_file, mailbox):
    """Create a blob from a file."""
    # Read the file content once
    file_content = eml_file.read()
    expected_hash = hashlib.sha256(file_content).hexdigest()
    return Blob.objects.create(
        raw_content=file_content,
        type=eml_file.content_type,
        size=eml_file.size,
        mailbox=mailbox,
        sha256=expected_hash,
    )


def test_import_eml_file(api_client, user, mailbox, blob_eml):
    """Test import of EML file."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Create a test EML file
    response = api_client.post(
        IMPORT_FILE_URL,
        {"blob": blob_eml.id, "recipient": str(mailbox.id)},
        format="multipart",
    )
    assert response.status_code == 202
    assert response.data["type"] == "eml"
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


def test_import_mbox_file(api_client, user, mailbox, blob_mbox):
    """Test import of MBOX file."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    response = api_client.post(
        IMPORT_FILE_URL,
        {"blob": blob_mbox.id, "recipient": str(mailbox.id)},
        format="multipart",
    )
    assert response.status_code == 202
    assert response.data["type"] == "mbox"
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


def test_import_mbox_async(api_client, user, mailbox, blob_mbox):
    """Test import of MBOX file asynchronously."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        mock_task.return_value.status = "PENDING"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_mbox.id, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        assert mock_task.call_count == 1
        assert mock_task.call_args[0][1] == str(mailbox.id)


def test_blob_no_access(api_client, domain, blob_eml):
    """Test import of EML file without access to mailbox."""
    # Create a mailbox the user does NOT have access to
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)
    response = api_client.post(
        IMPORT_FILE_URL,
        {"blob": blob_eml.id, "recipient": str(mailbox.id)},
        format="multipart",
    )
    assert response.status_code == 403
    assert "access" in response.data["detail"]


def test_import_text_plain_mime_type(api_client, user, mailbox, blob_mbox):
    """Test import of MBOX file with text/plain MIME type."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Create a file with text/plain MIME type
    blob_mbox.type = "text/plain"
    blob_mbox.save()

    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_mbox.id, "recipient": str(mailbox.id)},
            format="multipart",
        )

        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        assert response.data["task_id"] == "fake-task-id"
        mock_task.assert_called_once()


def test_import_imap_task(api_client, user, mailbox):
    """Test import of IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    with patch("core.tasks.import_imap_messages_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
            "folder": "INBOX",
            "max_messages": 0,
        }
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["task_id"] == "fake-task-id"
        assert response.data["type"] == "imap"
        mock_task.assert_called_once()


def test_import_imap(api_client, user, mailbox):
    """Test import of IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value
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

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
            "folder": "INBOX",
            "max_messages": 0,
        }
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"
        assert Message.objects.count() == 2
        message1 = Message.objects.first()
        assert message1.subject == "Test Message 2"
        assert message1.sender.email == "sender@example.com"
        assert message1.recipients.get().contact.email == "recipient@example.com"
        assert message1.sent_at == message1.thread.messaged_at
        assert message1.sent_at == datetime.datetime(
            2025, 5, 26, 11, 0, 0, tzinfo=datetime.timezone.utc
        )
        message2 = Message.objects.last()
        assert message2.subject == "Test Message 1"
        assert message2.sender.email == "sender@example.com"
        assert message2.recipients.get().contact.email == "recipient@example.com"
        assert message2.sent_at == message2.thread.messaged_at
        assert message2.sent_at == datetime.datetime(
            2025, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc
        )


def test_import_imap_no_access(api_client, domain):
    """Test import of IMAP messages without access to mailbox."""
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)
    data = {
        "recipient": str(mailbox.id),
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
        "use_ssl": True,
        "folder": "INBOX",
        "max_messages": 0,
    }
    response = api_client.post(IMPORT_IMAP_URL, data, format="json")
    assert response.status_code == 403
    assert "access" in response.data["detail"]


def test_import_duplicate_eml_file(api_client, user, mailbox, blob_eml):
    """Test that importing the same EML file twice only creates one message."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # create a copy of the blob because the blob is deleted after the import
    blob_eml2 = Blob.objects.create(
        raw_content=blob_eml.raw_content,
        type=blob_eml.type,
        size=blob_eml.size,
        mailbox=blob_eml.mailbox,
        sha256=blob_eml.sha256,
    )

    # Get file content from blob
    file_content = blob_eml.raw_content

    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # First import
    with patch("core.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_eml.id, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0
        # Verify a new message was created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1

    # Second import of the same file
    with patch("core.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_eml2.id, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1  # Still counts as success
        assert task_result["result"]["failure_count"] == 0

        # Verify no new message was created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1


def test_import_duplicate_mbox_file(api_client, user, mailbox, blob_mbox):
    """Test that importing the same MBOX file twice only creates each message once."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # create a copy of the blob because the blob is deleted after the import
    blob_mbox2 = Blob.objects.create(
        raw_content=blob_mbox.raw_content,
        type=blob_mbox.type,
        size=blob_mbox.size,
        mailbox=blob_mbox.mailbox,
        sha256=blob_mbox.sha256,
    )

    # Get file content from blob
    file_content = blob_mbox.raw_content

    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # First import
    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_mbox.id, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert (
            task_result["result"]["success_count"] == 3
        )  # Three messages in test file
        assert task_result["result"]["failure_count"] == 0

        # Verify messages were created
        assert Message.objects.count() == 3
        assert Thread.objects.count() == 2

    # Second import of the same file
    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob_mbox2.id, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 3  # Still counts as success
        assert task_result["result"]["failure_count"] == 0

        # Verify no new messages were created
        assert Message.objects.count() == 3
        assert Thread.objects.count() == 2


def test_import_eml_same_message_different_mailboxes(api_client, user, eml_file_path):
    """Test that the same message can be imported into different mailboxes."""
    # Create two mailboxes
    mailbox1 = factories.MailboxFactory()
    mailbox2 = factories.MailboxFactory()

    # Add access to both mailboxes
    mailbox1.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Read file content once
    with open(eml_file_path, "rb") as f:
        file_content = f.read()

    # Create blobs for each mailbox
    blob1 = Blob.objects.create(
        raw_content=file_content,
        type="message/rfc822",
        size=len(file_content),
        mailbox=mailbox1,
        sha256=hashlib.sha256(file_content).hexdigest(),
    )
    blob2 = Blob.objects.create(
        raw_content=file_content,
        type="message/rfc822",
        size=len(file_content),
        mailbox=mailbox2,
        sha256=hashlib.sha256(file_content).hexdigest(),
    )

    assert Message.objects.count() == 0

    # Import to first mailbox
    with patch("core.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob1.id, "recipient": str(mailbox1.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox1.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0

        # Verify a new message was created
        assert Message.objects.count() == 1

    # Import to second mailbox
    with patch("core.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob2.id, "recipient": str(mailbox2.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox2.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0

        # Verify only one new message was created
        assert Message.objects.count() == 2

        # Verify both mailboxes have the message
        assert (
            Message.objects.filter(thread__accesses__mailbox=mailbox1).count() == 1
        ), "Message not found in first mailbox"
        assert (
            Message.objects.filter(thread__accesses__mailbox=mailbox2).count() == 1
        ), "Message not found in second mailbox"


def test_import_mbox_same_message_different_mailboxes(api_client, user, mbox_file_path):
    """Test that the same message can be imported into different mailboxes."""
    # Create two mailboxes
    mailbox1 = factories.MailboxFactory()
    mailbox2 = factories.MailboxFactory()

    # Add access to both mailboxes
    mailbox1.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Read file content once
    with open(mbox_file_path, "rb") as f:
        file_content = f.read()

    # Create blobs for each mailbox
    blob1 = Blob.objects.create(
        raw_content=file_content,
        type="application/mbox",
        size=len(file_content),
        mailbox=mailbox1,
        sha256=hashlib.sha256(file_content).hexdigest(),
    )
    blob2 = Blob.objects.create(
        raw_content=file_content,
        type="application/mbox",
        size=len(file_content),
        mailbox=mailbox2,
        sha256=hashlib.sha256(file_content).hexdigest(),
    )

    assert Message.objects.count() == 0

    # Import to first mailbox
    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob1.id, "recipient": str(mailbox1.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox1.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 3
        assert task_result["result"]["failure_count"] == 0

        # Verify messages were created
        assert Message.objects.count() == 3

    # Import to second mailbox
    with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"blob": blob2.id, "recipient": str(mailbox2.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_content": file_content, "recipient_id": str(mailbox2.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 3
        assert task_result["result"]["failure_count"] == 0

        # Verify no new messages were created
        assert Message.objects.count() == 6

        # Verify both mailboxes have the message
        assert (
            Message.objects.filter(thread__accesses__mailbox=mailbox1).count() == 3
        ), "Message not found in first mailbox"
        assert (
            Message.objects.filter(thread__accesses__mailbox=mailbox2).count() == 3
        ), "Message not found in second mailbox"


def test_import_duplicate_imap_messages(api_client, user, mailbox):
    """Test import of duplicate IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value
        mock_imap_instance.select.return_value = ("OK", [b"1"])
        mock_imap_instance.search.return_value = ("OK", [b"1"])

        # Mock message with Message-ID header
        message = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message
Message-ID: <test123@example.com>
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body"""

        mock_imap_instance.fetch.return_value = ("OK", [(b"1", message)])

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
            "folder": "INBOX",
            "max_messages": 0,
        }

        # First import
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1

        # Second import of same message
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"

        # Verify no duplicate messages were created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1
        message = Message.objects.first()
        assert message.subject == "Test Message"
        assert message.sender.email == "sender@example.com"
        assert message.recipients.get().contact.email == "recipient@example.com"
        assert message.sent_at == message.thread.messaged_at
        assert message.sent_at == datetime.datetime(
            2025, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc
        )


def test_import_duplicate_imap_messages_different_mailboxes(api_client, user, mailbox):
    """Test import of duplicate IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2 = factories.MailboxFactory()
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value
        mock_imap_instance.select.return_value = ("OK", [b"1"])
        mock_imap_instance.search.return_value = ("OK", [b"1"])
        # Mock message with Message-ID header
        message = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message
Message-ID: <test123@example.com>
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body"""

        mock_imap_instance.fetch.return_value = ("OK", [(b"1", message)])

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
            "folder": "INBOX",
            "max_messages": 0,
        }

        # First import
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"
        assert Message.objects.count() == 1

        # Second import of same message
        data["recipient"] = str(mailbox2.id)
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"

        # Verify no duplicate messages were created
        assert Message.objects.count() == 2
        message = Message.objects.first()
        assert message.subject == "Test Message"
        assert message.sender.email == "sender@example.com"
        assert message.recipients.get().contact.email == "recipient@example.com"
        assert message.sent_at == message.thread.messaged_at
        assert message.sent_at == datetime.datetime(
            2025, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc
        )

        # Verify both mailboxes have the message
        assert Message.objects.filter(thread__accesses__mailbox=mailbox).count() == 1, (
            "Message not found in first mailbox"
        )
        assert (
            Message.objects.filter(thread__accesses__mailbox=mailbox2).count() == 1
        ), "Message not found in second mailbox"


# def test_import_mbox_multiple_times_threading(api_client, user, mailbox, mbox_file_path):
#     """Test that importing the same MBOX file multiple times maintains proper threading."""
#     # Add access to mailbox
#     mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

#     # Read file content once
#     with open(mbox_file_path, "rb") as f:
#         file_content = f.read()

#     assert Message.objects.count() == 0
#     assert Thread.objects.count() == 0

#     # First import
#     with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
#         mock_task.return_value.id = "fake-task-id-1"
#         with open(mbox_file_path, "rb") as f:
#             response = api_client.post(
#                 IMPORT_FILE_URL,
#                 {"blob": f, "recipient": str(mailbox.id)},
#                 format="multipart",
#             )
#             assert response.status_code == 202
#             assert response.data["type"] == "mbox"
#             mock_task.assert_called_once()

#             # Run the task synchronously for testing with a task_id
#             task_result = process_mbox_file_task.apply(
#                 kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
#                 task_id="fake-task-id-1",
#             ).get()
#             assert task_result["status"] == "SUCCESS"
#             assert task_result["result"]["success_count"] == 3
#             assert task_result["result"]["failure_count"] == 0

#             # Verify messages and threads were created
#             assert Message.objects.count() == 3
#             initial_thread_count = Thread.objects.count()
#             assert initial_thread_count == 2  # One thread for the message with attachment, one for the conversation

#             # Get initial thread IDs and message relationships
#             messages = Message.objects.order_by("created_at")
#             initial_thread_ids = {msg.thread.id for msg in messages}
#             initial_parent_relationships = {
#                 msg.mime_id: msg.parent.mime_id if msg.parent else None
#                 for msg in messages
#                 if msg.parent
#             }

#             # Verify specific threading relationships from the test MBOX file
#             # First message (with attachment) should be in its own thread
#             assert messages[0].thread != messages[1].thread
#             # Second and third messages (original and reply) should be in the same thread
#             assert messages[1].thread == messages[2].thread
#             # Third message should be a reply to the second
#             assert messages[2].parent == messages[1]
#             # Verify thread message counts
#             assert messages[0].thread.messages.count() == 1
#             assert messages[1].thread.messages.count() == 2

#     # Second import of the same file
#     with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
#         mock_task.return_value.id = "fake-task-id-2"
#         with open(mbox_file_path, "rb") as f:
#             response = api_client.post(
#                 IMPORT_FILE_URL,
#                 {"blob": f, "recipient": str(mailbox.id)},
#                 format="multipart",
#             )
#             assert response.status_code == 202
#             assert response.data["type"] == "mbox"
#             mock_task.assert_called_once()

#             # Run the task synchronously for testing with a task_id
#             task_result = process_mbox_file_task.apply(
#                 kwargs={"file_content": file_content, "recipient_id": str(mailbox.id)},
#                 task_id="fake-task-id-2",
#             ).get()
#             assert task_result["status"] == "SUCCESS"
#             assert task_result["result"]["success_count"] == 3  # Still counts as success
#             assert task_result["result"]["failure_count"] == 0

#             # Verify no new messages or threads were created
#             assert Message.objects.count() == 3
#             assert Thread.objects.count() == initial_thread_count

#             # Verify thread IDs and parent relationships are unchanged
#             messages = Message.objects.order_by("created_at")
#             current_thread_ids = {msg.thread.id for msg in messages}
#             assert current_thread_ids == initial_thread_ids

#             current_parent_relationships = {
#                 msg.mime_id: msg.parent.mime_id if msg.parent else None
#                 for msg in messages
#                 if msg.parent
#             }
#             assert current_parent_relationships == initial_parent_relationships

#             # Verify specific threading relationships are still maintained
#             # First message (with attachment) should still be in its own thread
#             assert messages[0].thread != messages[1].thread
#             # Second and third messages (original and reply) should still be in the same thread
#             assert messages[1].thread == messages[2].thread
#             # Third message should still be a reply to the second
#             assert messages[2].parent == messages[1]
#             # Verify thread message counts are unchanged
#             assert messages[0].thread.messages.count() == 1
#             assert messages[1].thread.messages.count() == 2

#     # Third import with a new message that should thread with existing ones
#     new_message_content = b"""From: sender@example.com
# To: recipient@example.com
# Subject: Re: Je t'envoie encore un message...
# Message-ID: <new-reply@example.com>
# In-Reply-To: <original@example.com>
# References: <original@example.com>
# Date: Mon, 26 May 2025 20:19:00 +0000

# This is another reply to the same thread."""

#     with patch("core.tasks.process_mbox_file_task.delay") as mock_task:
#         mock_task.return_value.id = "fake-task-id-3"
#         # Create a new MBOX file with just the new message
#         new_mbox_content = b"From \n" + new_message_content + b"\n\n"
#         new_mbox_file = SimpleUploadedFile(
#             "new_messages.mbox",
#             new_mbox_content,
#             content_type="text/plain",
#         )
#         response = api_client.post(
#             IMPORT_FILE_URL,
#             {"blob": new_mbox_file, "recipient": str(mailbox.id)},
#             format="multipart",
#         )
#         assert response.status_code == 202
#         assert response.data["type"] == "mbox"
#         mock_task.assert_called_once()

#         # Run the task synchronously for testing with a task_id
#         task_result = process_mbox_file_task.apply(
#             kwargs={"file_content": new_mbox_content, "recipient_id": str(mailbox.id)},
#             task_id="fake-task-id-3",
#         ).get()
#         assert task_result["status"] == "SUCCESS"
#         assert task_result["result"]["success_count"] == 1
#         assert task_result["result"]["failure_count"] == 0

#         # Verify the new message was added to the existing thread
#         assert Message.objects.count() == 4
#         assert Thread.objects.count() == initial_thread_count  # No new threads

#         # Get all messages ordered by creation
#         messages = Message.objects.order_by("created_at")
#         new_message = messages.last()

#         # Verify the new message was added to the correct thread
#         assert new_message.thread == messages[1].thread  # Should be in the conversation thread
#         assert new_message.parent == messages[1]  # Should be a reply to the original message
#         assert messages[1].thread.messages.count() == 3  # Thread should now have 3 messages
