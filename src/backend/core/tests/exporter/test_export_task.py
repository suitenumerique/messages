"""Tests for mailbox export functionality."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter

import gzip
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

from django.core.files.storage import storages
from django.urls import reverse

import pytest

from core import factories
from core.models import Blob, Label, Mailbox, MailDomain, Message, Thread, ThreadAccess
from core.services.exporter.tasks import export_mailbox_task
from core.services.importer.mbox_tasks import process_mbox_file_task


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
def mailbox_fixture(db, domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def admin_client(client, admin_user):
    """Create an authenticated admin client."""
    client.force_login(admin_user)
    return client


def create_test_message(mailbox_obj, subject, body, sender="sender@example.com"):
    """Helper to create a test message with blob."""
    eml_content = f"""From: {sender}
To: {mailbox_obj}
Subject: {subject}
Date: Mon, 26 May 2025 20:13:44 +0200
Message-ID: <test-{subject.replace(" ", "-")}@example.com>

{body}
""".encode()

    # Use create_blob to properly create with all required fields
    blob = Blob.objects.create_blob(
        content=eml_content,
        content_type="message/rfc822",
        mailbox=mailbox_obj,
    )

    # Create thread and message
    thread = Thread.objects.create(subject=subject)
    ThreadAccess.objects.create(thread=thread, mailbox=mailbox_obj)

    return Message.objects.create(
        thread=thread,
        blob=blob,
        subject=subject,
        sender=factories.ContactFactory(email=sender),
        is_sender=False,
    )


@pytest.fixture
def cleanup_exports():
    """Fixture to track and clean up exported files after tests."""
    exported_keys = []
    yield exported_keys
    # Cleanup after test
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    for key in exported_keys:
        try:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


@pytest.mark.django_db
def test_export_empty_mailbox(mailbox_fixture, admin_user, cleanup_exports):
    """Test exporting a mailbox with no messages creates empty MBOX."""
    mock_task = MagicMock()

    # Mock update_state (required when calling task directly, not via .delay())
    # and deliver_inbound_message to avoid creating notification
    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"
    assert result["result"]["exported_count"] == 0
    assert result["result"]["total_messages"] == 0

    # Track for cleanup
    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    # Verify file exists in S3
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    content = response["Body"].read()

    # Should be a valid (empty) gzip file
    with gzip.open(BytesIO(content), "rb") as f:
        mbox_content = f.read()
        assert mbox_content == b""


@pytest.mark.django_db
def test_export_single_message(mailbox_fixture, admin_user, cleanup_exports):
    """Test exporting a mailbox with one message."""
    create_test_message(mailbox_fixture, "Test Subject", "Test body content")
    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"
    assert result["result"]["exported_count"] == 1
    assert result["result"]["total_messages"] == 1

    # Track for cleanup
    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    # Verify MBOX content
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        assert b"Test Subject" in mbox_content
        assert b"Test body content" in mbox_content


@pytest.mark.django_db
def test_export_multiple_messages(mailbox_fixture, admin_user, cleanup_exports):
    """Test exporting a mailbox with multiple messages."""
    create_test_message(mailbox_fixture, "Message 1", "Body 1")
    create_test_message(mailbox_fixture, "Message 2", "Body 2")
    create_test_message(mailbox_fixture, "Message 3", "Body 3")
    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"
    assert result["result"]["exported_count"] == 3
    assert result["result"]["total_messages"] == 3

    cleanup_exports.append(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_skips_missing_blob(mailbox_fixture, admin_user, cleanup_exports):
    """Test that messages without blobs are skipped."""
    # Create message with blob
    create_test_message(mailbox_fixture, "Message with blob", "Has content")

    # Create message without blob
    thread = Thread.objects.create(subject="No blob message")
    ThreadAccess.objects.create(thread=thread, mailbox=mailbox_fixture)
    Message.objects.create(
        thread=thread,
        blob=None,  # No blob
        subject="No blob message",
        sender=factories.ContactFactory(email="sender@example.com"),
        is_sender=False,
    )

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"
    assert result["result"]["exported_count"] == 1
    assert result["result"]["skipped_count"] == 1
    assert result["result"]["total_messages"] == 2

    cleanup_exports.append(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_creates_notification_message(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Test that a notification message is created after export."""
    create_test_message(mailbox_fixture, "Test Message", "Test body")
    mock_task = MagicMock()

    deliver_called = []

    def mock_deliver(*args, **kwargs):
        deliver_called.append((args, kwargs))
        return True

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message",
            side_effect=mock_deliver,
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"
    # Verify deliver_inbound_message was called
    assert len(deliver_called) == 1
    args, kwargs = deliver_called[0]
    assert kwargs["recipient_email"] == str(mailbox_fixture)
    assert kwargs["is_import"] is True

    cleanup_exports.append(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_nonexistent_mailbox(admin_user):
    """Test exporting a non-existent mailbox returns failure."""
    mock_task = MagicMock()

    with patch.object(export_mailbox_task, "update_state", mock_task.update_state):
        result = export_mailbox_task(
            "00000000-0000-0000-0000-000000000000", str(admin_user.id)
        )

    assert result["status"] == "FAILURE"
    assert "not found" in result["error"]


@pytest.mark.django_db
def test_admin_export_button_visible(admin_client, mailbox_fixture):
    """Test that the export button is visible on the mailbox change form."""
    url = reverse("admin:core_mailbox_change", args=[mailbox_fixture.pk])
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "Export Messages" in response.content.decode()


@pytest.mark.django_db
def test_admin_export_view_requires_post(admin_client, mailbox_fixture):
    """Test that the export view requires POST method."""
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])
    response = admin_client.get(url)
    assert response.status_code == 405  # Method Not Allowed


@pytest.mark.django_db
def test_admin_export_view_starts_task(admin_client, mailbox_fixture):
    """Test that POST to export view starts the celery task."""
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])

    with (
        patch("core.admin.export_mailbox_task") as mock_task,
        patch("core.admin.register_task_owner"),
    ):
        mock_task.delay.return_value = Mock(id="test-task-id")

        response = admin_client.post(url)

        assert response.status_code == 302  # Redirect
        mock_task.delay.assert_called_once()


@pytest.mark.django_db
def test_export_reimport_roundtrip(domain, cleanup_exports):
    """
    E2E test: Export messages from mailbox A, then import the
    resulting MBOX into mailbox B, verify messages match.
    """
    # 1. Create source mailbox with test messages
    mailbox_a = Mailbox.objects.create(local_part="source", domain=domain)
    mailbox_b = Mailbox.objects.create(local_part="target", domain=domain)
    user = factories.UserFactory(is_superuser=True, is_staff=True)

    # Create multiple test messages with different content
    subjects = ["First message", "Second message", "Third message"]
    for i, subject in enumerate(subjects):
        msg = create_test_message(
            mailbox_a,
            subject,
            f"Body content for message {i + 1}",
            sender=f"sender{i}@example.com",
        )
        # Add a label to the first message for roundtrip verification
        if i == 0:
            label = Label.objects.create(
                name="roundtrip-test", slug="roundtrip-test", mailbox=mailbox_a
            )
            msg.thread.labels.add(label)

    original_count = Message.objects.filter(thread__accesses__mailbox=mailbox_a).count()
    assert original_count == 3

    # 2. Export mailbox A
    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        export_result = export_mailbox_task(str(mailbox_a.id), str(user.id))

    assert export_result["status"] == "SUCCESS"
    assert export_result["result"]["exported_count"] == 3

    s3_key = export_result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    # 3. Get the exported MBOX content and upload for import
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client

    # Download the exported file
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    # Decompress for import (importer expects uncompressed MBOX)
    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()

    # Upload uncompressed MBOX for import
    import_key = f"imports/{mailbox_b.id}/reimport.mbox"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=import_key,
        Body=mbox_content,
        ContentType="text/plain",
    )
    cleanup_exports.append(import_key)

    # 4. Import into mailbox B
    mock_import_task = MagicMock()

    with patch.object(
        process_mbox_file_task, "update_state", mock_import_task.update_state
    ):
        import_result = process_mbox_file_task(
            file_key=import_key, recipient_id=str(mailbox_b.id)
        )

    assert import_result["status"] == "SUCCESS"
    assert import_result["result"]["success_count"] == 3

    # 5. Verify messages in mailbox B
    imported_count = Message.objects.filter(thread__accesses__mailbox=mailbox_b).count()
    assert imported_count == 3

    # 6. Verify message content integrity
    imported_messages = Message.objects.filter(
        thread__accesses__mailbox=mailbox_b
    ).order_by("subject")

    imported_subjects = [msg.subject for msg in imported_messages]
    for subject in subjects:
        assert subject in imported_subjects, (
            f"Subject '{subject}' not found in imported messages"
        )

    # 7. Verify label roundtrip — the "roundtrip-test" label should be on a thread in mailbox B
    first_msg = imported_messages.filter(subject="First message").first()
    assert first_msg is not None, "Imported 'First message' not found in mailbox B"
    labeled_thread = first_msg.thread
    mailbox_b_labels = Label.objects.filter(mailbox=mailbox_b)
    imported_label = mailbox_b_labels.filter(name="roundtrip-test").first()
    assert imported_label is not None, (
        "Label 'roundtrip-test' was not created in target mailbox"
    )
    assert labeled_thread.labels.filter(id=imported_label.id).exists(), (
        "Label 'roundtrip-test' not attached to the imported thread"
    )


@pytest.mark.django_db
def test_export_includes_status_headers(mailbox_fixture, admin_user, cleanup_exports):
    """Test that exported messages include Status/X-Status headers for flags."""
    # Create a read, starred message
    msg = create_test_message(mailbox_fixture, "Starred Message", "Important content")
    msg.is_unread = False  # Read
    msg.is_starred = True  # Starred
    msg.save()

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"

    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    # Verify headers in MBOX content
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        # Read message should have Status: RO
        assert b"Status: RO" in mbox_content
        # Starred message should have X-Status: F
        assert b"X-Status: F" in mbox_content


@pytest.mark.django_db
def test_export_headers_prepended_before_received(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Test that Status/X-Keywords headers are prepended before Received: headers."""
    # Create a message with Received: headers in the raw content (as in real email)
    eml_content = b"""Received: from relay.example.com by our-server; Mon, 26 May 2025 20:13:44 +0200
Received: from sender-smtp.example.com by relay.example.com; Mon, 26 May 2025 20:13:40 +0200
From: sender@example.com
To: test@example.com
Subject: Message with Received headers
Date: Mon, 26 May 2025 20:13:44 +0200
Message-ID: <received-test@example.com>

Body content here
"""
    blob = Blob.objects.create_blob(
        content=eml_content,
        content_type="message/rfc822",
        mailbox=mailbox_fixture,
    )
    thread = Thread.objects.create(subject="Message with Received headers")
    ThreadAccess.objects.create(thread=thread, mailbox=mailbox_fixture)
    msg = Message.objects.create(
        thread=thread,
        blob=blob,
        subject="Message with Received headers",
        sender=factories.ContactFactory(email="sender@example.com"),
        is_sender=False,
        is_unread=False,
    )
    label = Label.objects.create(
        name="test-order", slug="test-order", mailbox=mailbox_fixture
    )
    msg.thread.labels.add(label)

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"

    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        # Injected headers should appear before the first Received: header
        status_pos = mbox_content.index(b"Status:")
        keywords_pos = mbox_content.index(b"X-Keywords:")
        received_pos = mbox_content.index(b"Received:")
        assert status_pos < received_pos, (
            "Status header should appear before Received headers"
        )
        assert keywords_pos < received_pos, (
            "X-Keywords header should appear before Received headers"
        )


@pytest.mark.django_db
def test_export_includes_labels_as_x_keywords(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Test that exported messages include X-Keywords header with labels."""
    # Create a message
    msg = create_test_message(mailbox_fixture, "Labeled Message", "Content with labels")

    # Create labels and attach to thread
    label1 = Label.objects.create(name="work", slug="work", mailbox=mailbox_fixture)
    label2 = Label.objects.create(
        name="important", slug="important", mailbox=mailbox_fixture
    )
    msg.thread.labels.add(label1, label2)

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"

    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    # Verify X-Keywords header in MBOX content
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        # Should have X-Keywords header with both labels
        assert b"X-Keywords:" in mbox_content
        assert b"work" in mbox_content
        assert b"important" in mbox_content


@pytest.mark.django_db
def test_export_labels_with_spaces_are_quoted(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Test that labels with spaces are quoted in X-Keywords header."""
    msg = create_test_message(mailbox_fixture, "Message with spaced label", "Content")

    # Create label with space
    label = Label.objects.create(
        name="project alpha", slug="project-alpha", mailbox=mailbox_fixture
    )
    msg.thread.labels.add(label)

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"

    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        # Label with space should be quoted
        assert b'X-Keywords: "project alpha"' in mbox_content


@pytest.mark.django_db
def test_export_unread_message_status(mailbox_fixture, admin_user, cleanup_exports):
    """Test that unread messages have correct Status header (O without R)."""
    # Create an unread message
    msg = create_test_message(mailbox_fixture, "Unread Message", "Unread content")
    msg.is_unread = True
    msg.save()

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(str(mailbox_fixture.id), str(admin_user.id))

    assert result["status"] == "SUCCESS"

    s3_key = result["result"]["s3_key"]
    cleanup_exports.append(s3_key)

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    gzip_content = response["Body"].read()

    with gzip.open(BytesIO(gzip_content), "rb") as f:
        mbox_content = f.read()
        # Unread message should have Status: O (old) but not R (read)
        assert b"Status: O\n" in mbox_content
        # Should NOT have RO which indicates read
        assert b"Status: RO" not in mbox_content
