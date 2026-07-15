"""Tests for the import-start service functions."""

# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter
import datetime
from unittest.mock import patch

from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile

import pytest

from core import enums, factories
from core.api.utils import generate_file_key
from core.enums import MailboxRoleChoices
from core.models import Channel, Mailbox, MailDomain, Message
from core.services.importer.service import start_file_import, start_imap_import


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
def eml_file(user):
    """Get test eml file from test data."""
    with open("core/tests/resources/message.eml", "rb") as f:
        storage = storages["message-imports"]
        file_content = f.read()
        file = SimpleUploadedFile(
            "test.eml", file_content, content_type="message/rfc822"
        )
        s3_client = storage.connection.meta.client
        file_key = generate_file_key(user.id)
        file.s3_key = file_key  # the minted key travels with the fixture
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
def mbox_file_path():
    """Get test mbox file path from test data."""
    return "core/tests/resources/messages.mbox"


@pytest.fixture
def mbox_file(user, mbox_file_path):
    """Get test mbox file from test data."""
    with open(mbox_file_path, "rb") as f:
        storage = storages["message-imports"]
        file_content = f.read()
        file = SimpleUploadedFile(
            "test.mbox", file_content, content_type="application/mbox"
        )
        s3_client = storage.connection.meta.client
        file_key = generate_file_key(user.id)
        file.s3_key = file_key  # the minted key travels with the fixture
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
def eml_key(user, eml_file):
    """Get the key for the EML file."""
    return eml_file.s3_key


@pytest.fixture
def mbox_key(user, mbox_file):
    """Get the key for the MBOX file."""
    return mbox_file.s3_key


@pytest.mark.django_db
def test_import_file_eml_by_superuser(admin_user, mailbox, eml_key):
    """Test successful EML file import for superuser."""
    with patch("core.services.importer.service.run_import_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = start_file_import(
            file_key=eml_key,
            recipient=mailbox,
            user=admin_user,
        )

        assert success is True
        assert response_data["type"] == "eml"
        assert response_data["task_id"] == "fake-task-id"
        assert "import_id" in response_data
        mock_task.assert_called_once()


@pytest.mark.django_db
def test_import_file_rejects_oversized(admin_user, mailbox, eml_key):
    """An archive above the cap is rejected before a worker is dispatched."""
    from django.test import override_settings

    with (
        override_settings(MESSAGES_IMPORT_MAX_FILE_SIZE=1),
        patch("core.services.importer.service.run_import_task.delay") as mock_task,
    ):
        success, response_data = start_file_import(
            file_key=eml_key,
            recipient=mailbox,
            user=admin_user,
        )
    assert success is False
    assert "too large" in response_data["detail"].lower()
    mock_task.assert_not_called()


@pytest.mark.django_db
def test_import_file_mbox_by_superuser_task(admin_user, mailbox, mbox_key):
    """Test successful MBOX file import by superuser."""

    with patch("core.services.importer.service.run_import_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = start_file_import(
            file_key=mbox_key,
            recipient=mailbox,
            user=admin_user,
        )

        assert success is True
        assert response_data["type"] == "mbox"
        assert response_data["task_id"] == "fake-task-id"
        assert "import_id" in response_data
        mock_task.assert_called_once()


@pytest.mark.django_db
def test_import_file_mbox_by_superuser_db_creation(admin_user, mailbox, mbox_key):
    """Test file import for a superuser"""
    success, response_data = start_file_import(
        file_key=mbox_key,
        recipient=mailbox,
        user=admin_user,
    )

    assert success is True
    assert response_data["type"] == "mbox"
    assert Message.objects.count() == 3
    message = Message.objects.last()
    assert message.subject == "Mon mail avec joli pj"
    assert message.has_attachments is True
    assert message.sender.email == "julie.sender@example.com"
    assert message.recipients.get().contact.email == "jean.recipient@example.com"
    assert message.sent_at == message.thread.messaged_at
    assert message.sent_at == datetime.datetime(
        2025, 5, 26, 20, 13, 44, tzinfo=datetime.timezone.utc
    )


@pytest.mark.django_db
def test_import_file_invalid_file(admin_user, mailbox):
    """Test import with an invalid file."""
    # Create an invalid file (not EML or MBOX)
    # Use real PDF magic bytes so python-magic detects it as application/pdf
    invalid_content = b"%PDF-1.4 invalid content"
    invalid_file = SimpleUploadedFile(
        "test.mbox", invalid_content, content_type="application/mbox"
    )
    invalid_file_key = generate_file_key(admin_user.id)
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=invalid_file_key,
        Body=invalid_content,
        ContentType=invalid_file.content_type,
    )

    try:
        with patch("core.services.importer.service.run_import_task.delay") as mock_task:
            success, response_data = start_file_import(
                file_key=invalid_file_key,
                recipient=mailbox,
                user=admin_user,
            )

            assert success is False
            assert "detail" in response_data
            assert "Invalid file format" in response_data["detail"]
            assert Message.objects.count() == 0
            # The task should not be called for invalid files
            mock_task.assert_not_called()
    finally:
        # Clean up: delete the file from S3 after the test
        s3_client.delete_object(
            Bucket=storage.bucket_name,
            Key=invalid_file_key,
        )


@pytest.mark.django_db
def test_import_file_mbox_misclassified_by_libmagic(admin_user, mailbox):
    """Regression: an mbox file must be recognized even when libmagic
    misclassifies it (e.g. returns text/html because the first message body
    contains HTML strong enough to outrank the ``From `` envelope signature).

    Observed against libmagic 5.41 (Ubuntu 22.04, the libmagic Scalingo's
    scalingo-22 stack ships) on a real multipart/alternative Zimbra-exported
    mbox: libmagic 5.41 returns text/html and the upload is rejected. Debian
    13's libmagic 5.46 — used in the dev container and the distroless image
    built from src/backend/Dockerfile — has tuned scoring so the same bytes
    classify as application/mbox, which is why this test mocks
    ``magic.from_buffer`` rather than relying on real bytes (a real-bytes
    fixture passes trivially on the dev libmagic regardless of the fix).

    RFC 4155 requires every mbox file to start with a ``From `` envelope line
    at offset 0; we trust that signature ahead of libmagic.
    """
    mbox_content = (
        b"From sender@example.com Mon Jun 01 00:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"To: jean.recipient@example.com\r\n"
        b"Subject: HTML body trips libmagic\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<!DOCTYPE html><html><body><h1>Hello</h1></body></html>\r\n"
    )
    uploaded = SimpleUploadedFile(
        # Deliberately no .mbox extension so the extension fallback can't rescue us.
        "ambiguous-upload",
        mbox_content,
        content_type="application/octet-stream",
    )
    file_key = generate_file_key(admin_user.id)
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=mbox_content,
        ContentType=uploaded.content_type,
    )

    try:
        with (
            patch(
                "core.services.importer.service.magic.from_buffer",
                return_value="text/html",
            ) as mock_magic,
            patch("core.services.importer.service.run_import_task.delay") as mock_task,
        ):
            mock_task.return_value.id = "fake-task-id"
            success, response_data = start_file_import(
                file_key=file_key,
                recipient=mailbox,
                user=admin_user,
                filename=uploaded.name,
            )

            assert success is True, response_data
            assert response_data["type"] == "mbox"
            mock_task.assert_called_once()
            # The import channel records the resolved source type.
            channel = Channel.objects.get(id=response_data["import_id"])
            assert (
                channel.settings["import"]["source_type"]
                == enums.ImportSource.MBOX.value
            )
            # The RFC 4155 ``From `` envelope at offset 0 must short-circuit
            # detection — libmagic is never consulted on this branch.
            mock_magic.assert_not_called()
    finally:
        s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)


def test_import_imap_by_regular_user(user, mailbox):
    """The service layer does not authorize (see docs/permissions.md): the API
    path is gated by IsMailboxAdmin and the Django admin path is trusted
    operator tooling — so any caller-approved user goes through."""
    with patch("core.services.importer.service.run_import_task.delay") as mock_task:
        mock_task.return_value.id = "fake-task-id"
        success, response_data = start_imap_import(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=user,
            use_ssl=True,
        )

    assert success is True
    assert "import_id" in response_data
    mock_task.assert_called_once()


def test_import_imap_task_error(admin_user, mailbox):
    """Test IMAP import with task error."""
    # Add access to mailbox
    mailbox.accesses.create(user=admin_user, role=MailboxRoleChoices.ADMIN)

    with patch("core.services.importer.service.run_import_task.delay") as mock_task:
        mock_task.side_effect = Exception("Task error")
        success, response_data = start_imap_import(
            imap_server="imap.example.com",
            imap_port=993,
            username="test@example.com",
            password="password123",
            recipient=mailbox,
            user=admin_user,
            use_ssl=True,
        )

        assert success is False
        assert "detail" in response_data
        assert "error" in response_data["detail"].lower()


# --- Filename disambiguation tests ---


@pytest.mark.django_db
def test_import_file_eml_disambiguated_by_filename(admin_user, mailbox):
    """Test that a .eml file detected as text/plain is routed to EML task via filename."""
    # Create a file that magic detects as text/plain but has .eml extension
    eml_content = b"From: sender@example.com\r\nTo: recipient@example.com\r\nSubject: Test\r\n\r\nBody"
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    file_key = generate_file_key(admin_user.id)
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=eml_content,
        ContentType="text/plain",
    )

    try:
        with patch("core.services.importer.service.run_import_task.delay") as mock_task:
            mock_task.return_value.id = "fake-eml-task-id"
            success, response_data = start_file_import(
                file_key=file_key,
                recipient=mailbox,
                user=admin_user,
                filename="test.eml",
            )

            # Routing is now by import source_type on a single unified task:
            # the .eml is disambiguated to the EML source.
            assert success is True
            assert response_data["type"] == "eml"
            mock_task.assert_called_once()
            channel = Channel.objects.get(id=response_data["import_id"])
            assert (
                channel.settings["import"]["source_type"]
                == enums.ImportSource.EML.value
            )
    finally:
        s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)


@pytest.mark.django_db
def test_import_file_mbox_disambiguated_by_filename(admin_user, mailbox):
    """Test that a .mbox file detected as text/plain is routed to MBOX task via filename."""
    # text/plain content with .mbox extension
    mbox_content = (
        b"From sender@example.com Mon Jan  1 00:00:00 2025\r\n"
        b"From: sender@example.com\r\nSubject: Test\r\n\r\nBody"
    )
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    file_key = generate_file_key(admin_user.id)
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=mbox_content,
        ContentType="text/plain",
    )

    try:
        with patch(
            "core.services.importer.service.run_import_task.delay"
        ) as mock_mbox_task:
            mock_mbox_task.return_value.id = "fake-mbox-task-id"
            success, response_data = start_file_import(
                file_key=file_key,
                recipient=mailbox,
                user=admin_user,
                filename="test.mbox",
            )

            assert success is True
            assert response_data["type"] == "mbox"
            mock_mbox_task.assert_called_once()
            channel = Channel.objects.get(id=response_data["import_id"])
            assert (
                channel.settings["import"]["source_type"]
                == enums.ImportSource.MBOX.value
            )
    finally:
        s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)


@pytest.mark.django_db
def test_import_file_without_filename_falls_back_to_mime(admin_user, mailbox):
    """Without a filename hint, detection relies on libmagic alone — which
    recognises RFC822 headers, so a bare message routes to the EML runner."""
    eml_content = b"From: sender@example.com\r\nTo: recipient@example.com\r\nSubject: Test\r\n\r\nBody"
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    file_key = generate_file_key(admin_user.id)
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=eml_content,
        ContentType="text/plain",
    )

    try:
        with patch(
            "core.services.importer.service.run_import_task.delay"
        ) as mock_mbox_task:
            mock_mbox_task.return_value.id = "fake-task-id"
            # libmagic sees RFC822 headers => message/rfc822 => EML.
            success, response_data = start_file_import(
                file_key=file_key,
                recipient=mailbox,
                user=admin_user,
            )

            assert success is True
            assert response_data["type"] == "eml"
            channel = Channel.objects.get(id=response_data["import_id"])
            assert (
                channel.settings["import"]["source_type"]
                == enums.ImportSource.EML.value
            )
    finally:
        s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)
