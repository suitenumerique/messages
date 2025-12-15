"""Test messages import."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter, too-many-lines

import datetime
from unittest.mock import patch

from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from rest_framework.test import APIClient

from core import factories
from core.api.utils import get_file_key
from core.enums import MailboxRoleChoices, MessageDeliveryStatusChoices
from core.models import Mailbox, MailDomain, Message, Thread
from core.services.importer.tasks import process_eml_file_task, process_mbox_file_task

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
def eml_file(user):
    """Get test eml file from test data and put it in the message imports bucket."""
    with open("core/tests/resources/message.eml", "rb") as f:
        storage = storages["message-imports"]
        file_content = f.read()
        file = SimpleUploadedFile(
            "test.eml", file_content, content_type="message/rfc822"
        )
        s3_client = storage.connection.meta.client
        file_key = get_file_key(user.id, file.name)
        s3_client.put_object(
            Bucket=storage.bucket_name,
            Key=file_key,
            Body=file_content,
            ContentType=file.content_type,
        )

    yield file
    # Remove the file from the bucket at teardown
    s3_client.delete_object(
        Bucket=storage.bucket_name,
        Key=file_key,
    )


@pytest.fixture
def mbox_file(mbox_file_path, user):
    """Get test mbox file from test data and put it in the message imports bucket."""
    with open(mbox_file_path, "rb") as f:
        storage = storages["message-imports"]
        file_content = f.read()
        file = SimpleUploadedFile(
            "test.mbox", file_content, content_type="application/mbox"
        )
        s3_client = storage.connection.meta.client
        file_key = get_file_key(user.id, file.name)
        s3_client.put_object(
            Bucket=storage.bucket_name,
            Key=file_key,
            Body=file_content,
            ContentType=file.content_type,
        )

    yield file
    # Remove the file from the bucket at teardown
    s3_client.delete_object(
        Bucket=storage.bucket_name,
        Key=file_key,
    )


def test_api_import_eml_file(api_client, user, mailbox, eml_file):
    """Test import of EML file."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Create a test EML file
    response = api_client.post(
        IMPORT_FILE_URL,
        {"filename": eml_file.name, "recipient": str(mailbox.id)},
        format="multipart",
    )
    assert response.status_code == 202
    assert response.data["type"] == "eml"
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert message.subject == "Mon mail avec joli pj"
    assert message.has_attachments is True
    assert message.sender.email == "sender@example.com"
    assert message.recipients.get().contact.email == "recipient@example.com"
    assert message.sent_at == message.thread.messaged_at
    assert message.sent_at == datetime.datetime(
        2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc
    )
    assert message.recipients.get().delivery_status == MessageDeliveryStatusChoices.SENT


def test_api_import_mbox_file(api_client, user, mailbox, mbox_file):
    """Test import of MBOX file."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    response = api_client.post(
        IMPORT_FILE_URL,
        {"filename": mbox_file.name, "recipient": str(mailbox.id)},
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
    assert messages[0].has_attachments is True

    assert messages[1].subject == "Je t'envoie encore un message..."
    body1 = messages[1].get_parsed_field("textBody")[0]["content"]
    assert "Lorem ipsum dolor sit amet" in body1

    assert messages[2].subject == "Re: Je t'envoie encore un message..."
    body2 = messages[2].get_parsed_field("textBody")[0]["content"]
    assert "Yes !" in body2
    assert "Lorem ipsum dolor sit amet" in body2


def test_api_import_mbox_async(api_client, user, mailbox, mbox_file):
    """Test import of MBOX file asynchronously."""
    # add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    with patch(
        "core.services.importer.tasks.process_mbox_file_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id"
        mock_task.return_value.status = "PENDING"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": mbox_file.name, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        assert mock_task.call_count == 1
        assert mock_task.call_args[0][1] == str(mailbox.id)


def test_api_import_mailbox_no_access(api_client, domain, eml_file):
    """Test import of EML file without access to mailbox."""
    # Create a mailbox the user does NOT have access to
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)
    response = api_client.post(
        IMPORT_FILE_URL,
        {"filename": eml_file.name, "recipient": str(mailbox.id)},
        format="multipart",
    )
    assert response.status_code == 403
    assert "access" in response.data["detail"]


def test_api_import_imap_task(api_client, user, mailbox):
    """Test import of IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    with patch(
        "core.services.importer.tasks.import_imap_messages_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id"
        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
        }
        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["task_id"] == "fake-task-id"
        assert response.data["type"] == "imap"
        mock_task.assert_called_once()


def test_api_import_imap(api_client, user, mailbox):
    """Test import of IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value

        # Mock login
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])

        # Mock list folders - return INBOX folder
        mock_imap_instance.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"'],
        )

        # Mock select folder
        mock_imap_instance.select.return_value = ("OK", [b"1"])

        # Mock search for messages
        mock_imap_instance.search.return_value = ("OK", [b"1 2"])

        # Mock 2 messages with proper IMAP response format
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

        # Mock fetch responses with proper IMAP format including flags
        mock_imap_instance.fetch.side_effect = [
            # First message: flags + content
            ("OK", [(b"1 (FLAGS (\\Seen \\Answered))", message1)]),
            # Second message: flags + content
            ("OK", [(b"2 (FLAGS (\\Seen))", message2)]),
        ]

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
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


def test_api_import_imap_no_access(api_client, domain):
    """Test import of IMAP messages without access to mailbox."""
    mailbox = Mailbox.objects.create(local_part="noaccess", domain=domain)
    data = {
        "recipient": str(mailbox.id),
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
        "use_ssl": True,
    }
    response = api_client.post(IMPORT_IMAP_URL, data, format="json")
    assert response.status_code == 403
    assert "access" in response.data["detail"]


def test_api_import_duplicate_eml_file(api_client, user, mailbox, eml_file):
    """Test that importing the same EML file twice only creates one message."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # First import
    with patch("core.services.importer.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": eml_file.name, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        eml_key = get_file_key(user.id, eml_file.name)
        task_result = process_eml_file_task.apply(
            kwargs={"file_key": eml_key, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0
        # Verify a new message was created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1

    # Import again the same file
    with patch("core.services.importer.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": eml_file.name, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_key": eml_key, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1  # Still counts as success
        assert task_result["result"]["failure_count"] == 0

        # Verify no new message was created
        assert Message.objects.count() == 1
        assert Thread.objects.count() == 1


def test_api_import_duplicate_mbox_file(api_client, user, mailbox, mbox_file):
    """Test that importing the same MBOX file twice only creates each message once."""
    # Add access to mailbox
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mbox_key = get_file_key(user.id, mbox_file.name)
    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # First import
    with patch(
        "core.services.importer.tasks.process_mbox_file_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": mbox_file.name, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id

        task_result = process_mbox_file_task.apply(
            kwargs={"file_key": mbox_key, "recipient_id": str(mailbox.id)},
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
    with patch(
        "core.services.importer.tasks.process_mbox_file_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": mbox_file.name, "recipient": str(mailbox.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_key": mbox_key, "recipient_id": str(mailbox.id)},
            task_id="fake-task-id-2",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 3  # Still counts as success
        assert task_result["result"]["failure_count"] == 0

        # Verify no new messages were created
        assert Message.objects.count() == 3
        assert Thread.objects.count() == 2


def test_api_import_eml_same_message_different_mailboxes(api_client, user, eml_file):
    """Test that the same message can be imported into different mailboxes."""
    # Create two mailboxes
    mailbox1 = factories.MailboxFactory()
    mailbox2 = factories.MailboxFactory()

    # Add access to both mailboxes
    mailbox1.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    eml_key = get_file_key(user.id, eml_file.name)

    assert Message.objects.count() == 0

    # Import to first mailbox
    with patch("core.services.importer.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": eml_file.name, "recipient": str(mailbox1.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_key": eml_key, "recipient_id": str(mailbox1.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 1
        assert task_result["result"]["failure_count"] == 0

        # Verify a new message was created
        assert Message.objects.count() == 1

    # Import to second mailbox
    with patch("core.services.importer.tasks.process_eml_file_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": eml_file.name, "recipient": str(mailbox2.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "eml"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_eml_file_task.apply(
            kwargs={"file_key": eml_key, "recipient_id": str(mailbox2.id)},
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


def test_api_import_mbox_same_message_different_mailboxes(api_client, user, mbox_file):
    """Test that the same message can be imported into different mailboxes."""
    # Create two mailboxes
    mailbox1 = factories.MailboxFactory()
    mailbox2 = factories.MailboxFactory()

    # Add access to both mailboxes
    mailbox1.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    mbox_key = get_file_key(user.id, mbox_file.name)

    assert Message.objects.count() == 0

    # Import to first mailbox
    with patch(
        "core.services.importer.tasks.process_mbox_file_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id-1"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": mbox_file.name, "recipient": str(mailbox1.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_key": mbox_key, "recipient_id": str(mailbox1.id)},
            task_id="fake-task-id-1",
        ).get()
        assert task_result["status"] == "SUCCESS"
        assert task_result["result"]["success_count"] == 3
        assert task_result["result"]["failure_count"] == 0

        # Verify messages were created
        assert Message.objects.count() == 3

    # Import to second mailbox
    with patch(
        "core.services.importer.tasks.process_mbox_file_task.delay"
    ) as mock_task:
        mock_task.return_value.id = "fake-task-id-2"
        response = api_client.post(
            IMPORT_FILE_URL,
            {"filename": mbox_file.name, "recipient": str(mailbox2.id)},
            format="multipart",
        )
        assert response.status_code == 202
        assert response.data["type"] == "mbox"
        mock_task.assert_called_once()

        # Run the task synchronously for testing with a task_id
        task_result = process_mbox_file_task.apply(
            kwargs={"file_key": mbox_key, "recipient_id": str(mailbox2.id)},
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


def test_api_import_duplicate_imap_messages(api_client, user, mailbox):
    """Test import of duplicate IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    assert Message.objects.count() == 0
    assert Thread.objects.count() == 0

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value

        # Mock login
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])

        # Mock list folders - return INBOX folder
        mock_imap_instance.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"'],
        )

        # Mock select folder
        mock_imap_instance.select.return_value = ("OK", [b"1"])

        # Mock search for messages
        mock_imap_instance.search.return_value = ("OK", [b"1"])

        # Mock message with Message-ID header
        message = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message
Message-ID: <test123@example.com>
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body"""

        # Mock fetch response with proper IMAP format including flags
        mock_imap_instance.fetch.return_value = (
            "OK",
            [(b"1 (FLAGS (\\Seen))", message)],
        )

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
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


def test_api_import_duplicate_imap_messages_different_mailboxes(
    api_client, user, mailbox
):
    """Test import of duplicate IMAP messages."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    mailbox2 = factories.MailboxFactory()
    mailbox2.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)
    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value

        # Mock login
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])

        # Mock list folders - return INBOX folder
        mock_imap_instance.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"'],
        )

        # Mock select folder
        mock_imap_instance.select.return_value = ("OK", [b"1"])

        # Mock search for messages
        mock_imap_instance.search.return_value = ("OK", [b"1"])

        # Mock message with Message-ID header
        message = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Message
Message-ID: <test123@example.com>
Date: Mon, 26 May 2025 10:00:00 +0000

Test message body"""

        # Mock fetch response with proper IMAP format including flags
        mock_imap_instance.fetch.return_value = (
            "OK",
            [(b"1 (FLAGS (\\Seen))", message)],
        )

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
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


def test_api_import_imap_delivery_status_for_drafts(api_client, user, mailbox):
    """Test that imported draft messages have delivery_status set to None for recipients."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Create a draft message with the Draft flag
    draft_message = b"""From: sender@example.com
To: recipient1@example.com, recipient2@example.com
Cc: cc@example.com
Subject: Draft Message
Date: Mon, 26 May 2025 10:00:00 +0000

This is a draft message."""

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value

        # Mock login
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])

        # Mock list folders - return INBOX folder
        mock_imap_instance.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"'],
        )

        # Mock select folder
        mock_imap_instance.select.return_value = ("OK", [b"1"])

        # Mock search for messages
        mock_imap_instance.search.return_value = ("OK", [b"1"])

        # Mock fetch response with Draft flag
        mock_imap_instance.fetch.return_value = (
            "OK",
            [(b"1 (FLAGS (\\Draft))", draft_message)],
        )

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
        }

        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"

    # Verify the message was created as a draft
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert message is not None
    assert message.is_draft is True

    # Verify delivery_status is None for all recipients of draft messages
    # because drafts have not been sent yet
    recipients = message.recipients.all()
    assert recipients.count() == 3  # 2 To + 1 Cc
    for recipient in recipients:
        assert recipient.delivery_status is None


def test_api_import_imap_delivery_status_for_non_draft_messages(
    api_client, user, mailbox
):
    """Test that imported non-draft messages have delivery_status set to SENT."""
    mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

    # Create a regular (non-draft) message
    regular_message = b"""From: sender@example.com
To: recipient@example.com
Subject: Regular Message
Date: Mon, 26 May 2025 10:00:00 +0000

This is a regular message."""

    # Mock IMAP connection and responses
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_imap_instance = mock_imap.return_value

        # Mock login
        mock_imap_instance.login.return_value = ("OK", [b"Logged in"])

        # Mock list folders
        mock_imap_instance.list.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"'],
        )

        # Mock select folder
        mock_imap_instance.select.return_value = ("OK", [b"1"])

        # Mock search for messages
        mock_imap_instance.search.return_value = ("OK", [b"1"])

        # Mock fetch response without Draft flag
        mock_imap_instance.fetch.return_value = (
            "OK",
            [(b"1 (FLAGS (\\Seen))", regular_message)],
        )

        data = {
            "recipient": str(mailbox.id),
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "test@example.com",
            "password": "password123",
            "use_ssl": True,
        }

        response = api_client.post(IMPORT_IMAP_URL, data, format="json")
        assert response.status_code == 202
        assert response.data["type"] == "imap"

    # Verify the message was created as a non-draft
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert message is not None
    assert message.is_draft is False

    # Verify delivery_status is SENT for all recipients
    recipients = message.recipients.all()
    assert recipients.count() == 1
    for recipient in recipients:
        assert recipient.delivery_status == MessageDeliveryStatusChoices.SENT


# def test_api_import_mbox_multiple_times_threading(api_client, user, mailbox, mbox_file_path):
#     """Test that importing the same MBOX file multiple times maintains proper threading."""
#     # Add access to mailbox
#     mailbox.accesses.create(user=user, role=MailboxRoleChoices.ADMIN)

#     # Read file content once
#     with open(mbox_file_path, "rb") as f:
#         file_content = f.read()

#     assert Message.objects.count() == 0
#     assert Thread.objects.count() == 0

#     # First import
#     with patch("core.mda.inbound_tasks.process_mbox_file_task.delay") as mock_task:
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
#     with patch("core.mda.inbound_tasks.process_mbox_file_task.delay") as mock_task:
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

#     with patch("core.mda.inbound_tasks.process_mbox_file_task.delay") as mock_task:
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
