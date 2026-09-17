"""Tests for mailbox export functionality."""
# pylint: disable=redefined-outer-name, unused-argument, no-value-for-parameter

import gzip
import re
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

from django.contrib.admin.widgets import AutocompleteSelect
from django.core.files.storage import storages
from django.urls import reverse
from django.utils import timezone

import pytest

from core import enums, factories
from core.mda.draft import create_draft
from core.mda.outbound import prepare_outbound_message
from core.models import (
    Blob,
    Label,
    Mailbox,
    MailboxAccess,
    MailDomain,
    Message,
    Thread,
    ThreadAccess,
)
from core.services.exporter.tasks import export_mailbox_task
from core.services.importer.channel import create_import_channel
from core.services.importer.mbox import run_mbox


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def admin_user(db, domain):
    """Create a superuser for admin access, with a mailbox of their own.

    Exports deliver the download link to a mailbox the requester picks among
    the ones they can access, so whoever triggers one needs such a mailbox.
    """
    user = factories.UserFactory(
        email=f"admin@{domain.name}",
        password="adminpass123",
        full_name="Admin User",
        is_superuser=True,
        is_staff=True,
    )
    mailbox = Mailbox.objects.create(local_part="admin", domain=domain)
    MailboxAccess.objects.create(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.ADMIN
    )
    return user


@pytest.fixture
def admin_mailbox(admin_user):
    """A mailbox the admin user can pick as export link destination."""
    return admin_user.mailbox_accesses.get().mailbox


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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

    assert result["status"] == "SUCCESS"
    assert result["result"]["exported_count"] == 1
    assert result["result"]["skipped_count"] == 1
    assert result["result"]["total_messages"] == 2

    cleanup_exports.append(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_creates_notification_message(
    mailbox_fixture, admin_user, admin_mailbox, cleanup_exports
):
    """The download link is delivered to the chosen mailbox, not the exported one."""
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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(admin_mailbox.id)
        )

    assert result["status"] == "SUCCESS"
    # Verify deliver_inbound_message was called
    assert len(deliver_called) == 1
    args, kwargs = deliver_called[0]
    assert kwargs["recipient_email"] == str(admin_mailbox)
    assert kwargs["recipient_email"] != str(mailbox_fixture)
    assert kwargs["is_import"] is True
    # The notification names the mailbox it is about, since it no longer
    # lands in that mailbox.
    assert str(mailbox_fixture) in kwargs["parsed_email"]["subject"]
    assert result["result"]["recipient"] == str(admin_mailbox)

    cleanup_exports.append(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_counts_drafts_apart_from_skipped(
    mailbox_fixture, admin_user, admin_mailbox, cleanup_exports
):
    """A draft is not a failure: it has no MIME form, so it has its own count.

    Lumped into "skipped" it looks like data loss to whoever reads the
    notification, with no way to tell it from a message that failed.
    """
    create_test_message(mailbox_fixture, "Real message", "Content")

    draft_thread = Thread.objects.create(subject="A draft")
    ThreadAccess.objects.create(thread=draft_thread, mailbox=mailbox_fixture)
    Message.objects.create(
        thread=draft_thread,
        subject="A draft",
        sender=factories.ContactFactory(email="me@example.com"),
        is_draft=True,
        is_sender=True,
    )

    # A blobless message that is *not* a draft stays a skip
    broken_thread = Thread.objects.create(subject="No blob")
    ThreadAccess.objects.create(thread=broken_thread, mailbox=mailbox_fixture)
    Message.objects.create(
        thread=broken_thread,
        subject="No blob",
        sender=factories.ContactFactory(email="sender@example.com"),
    )

    mock_task = MagicMock()
    delivered = []

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message",
            side_effect=lambda *a, **kw: delivered.append(kw) or True,
        ),
    ):
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(admin_mailbox.id)
        )

    assert result["status"] == "SUCCESS"
    cleanup_exports.append(result["result"]["s3_key"])
    assert result["result"]["exported_count"] == 1
    assert result["result"]["draft_count"] == 1
    assert result["result"]["skipped_count"] == 1

    raw = delivered[0]["raw_data"].decode("utf-8", errors="replace")
    assert "Drafts (not exportable):   1" in raw
    assert "Messages skipped:          1" in raw
    assert "Drafts are left out" in raw


@pytest.mark.django_db
def test_export_unknown_recipient_fails(mailbox_fixture, admin_user):
    """A recipient mailbox deleted while the task waited fails cleanly."""
    mock_task = MagicMock()
    missing_recipient_id = "00000000-0000-0000-0000-000000000000"

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch("core.services.exporter.tasks.storages") as mock_storages,
    ):
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), missing_recipient_id
        )

    assert result["status"] == "FAILURE"
    assert "not found" in result["error"]
    # Nothing was uploaded: the check runs before any S3 work.
    mock_storages.__getitem__.assert_not_called()


@pytest.mark.django_db
def test_export_unknown_requester_fails(mailbox_fixture):
    """An export queued for a user that no longer exists fails cleanly."""
    mock_task = MagicMock()
    missing_user_id = "00000000-0000-0000-0000-000000000000"

    with patch.object(export_mailbox_task, "update_state", mock_task.update_state):
        result = export_mailbox_task(
            str(mailbox_fixture.id), missing_user_id, str(mailbox_fixture.id)
        )

    assert result["status"] == "FAILURE"
    assert "not found" in result["error"]


@pytest.mark.django_db
def test_export_nonexistent_mailbox(admin_user, mailbox_fixture):
    """Test exporting a non-existent mailbox returns failure."""
    mock_task = MagicMock()

    with patch.object(export_mailbox_task, "update_state", mock_task.update_state):
        result = export_mailbox_task(
            "00000000-0000-0000-0000-000000000000",
            str(admin_user.id),
            str(mailbox_fixture.id),
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
def test_admin_export_form_renders(admin_client, mailbox_fixture, admin_mailbox):
    """GET on the export page renders the source/destination form."""
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])
    response = admin_client.get(url)

    assert response.status_code == 200
    assert "destination" in response.context["form"].fields
    assert response.context["form"].initial["mailbox"].pk == mailbox_fixture.pk


@pytest.mark.django_db
def test_admin_export_form_prefills_from_query_param(admin_client, mailbox_fixture):
    """The generic export page pre-fills the source from ?mailbox=."""
    url = reverse("admin:core_mailbox_export_form") + f"?mailbox={mailbox_fixture.pk}"
    response = admin_client.get(url)

    assert response.status_code == 200
    assert response.context["form"].initial["mailbox"].pk == mailbox_fixture.pk


@pytest.mark.django_db
def test_admin_export_view_starts_task(
    admin_client, admin_user, mailbox_fixture, admin_mailbox
):
    """POST with a chosen destination queues the export for that mailbox."""
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])

    with patch("core.admin.export_mailbox_task") as mock_task:
        mock_task.delay.return_value = Mock(id="test-task-id")

        response = admin_client.post(
            url,
            data={
                "mailbox": str(mailbox_fixture.pk),
                "destination": str(admin_mailbox.pk),
            },
        )

        assert response.status_code == 302  # Redirect
        mock_task.delay.assert_called_once_with(
            str(mailbox_fixture.id),
            str(admin_user.id),
            str(admin_mailbox.id),
        )


@pytest.mark.django_db
def test_admin_export_view_accepts_any_destination(
    admin_client, admin_user, mailbox_fixture, domain
):
    """A superuser may send the link to a mailbox they have no access to.

    The Django admin is superuser-only, and a superuser can already read any
    mailbox here, so the destination is not scoped to their own mailboxes. The
    delegated domain-admin API keeps that restriction — see
    ``test_admin_maildomains_mailbox_export_foreign_recipient``.
    """
    outsider_box = Mailbox.objects.create(local_part="outsider", domain=domain)
    assert not outsider_box.accesses.filter(user=admin_user).exists()
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])

    with patch("core.admin.export_mailbox_task") as mock_task:
        mock_task.delay.return_value = Mock(id="test-task-id")

        response = admin_client.post(
            url,
            data={
                "mailbox": str(mailbox_fixture.pk),
                "destination": str(outsider_box.pk),
            },
        )

        assert response.status_code == 302
        mock_task.delay.assert_called_once_with(
            str(mailbox_fixture.id),
            str(admin_user.id),
            str(outsider_box.id),
        )


@pytest.mark.django_db
def test_admin_export_view_rejects_unknown_destination(admin_client, mailbox_fixture):
    """A destination that does not exist still fails form validation."""
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])

    with patch("core.admin.export_mailbox_task") as mock_task:
        response = admin_client.post(
            url,
            data={
                "mailbox": str(mailbox_fixture.pk),
                "destination": "00000000-0000-0000-0000-000000000000",
            },
        )

        assert response.status_code == 200  # Form re-rendered with errors
        assert "destination" in response.context["form"].errors
        mock_task.delay.assert_not_called()


@pytest.mark.django_db
def test_admin_export_form_uses_autocomplete_widgets(admin_client, mailbox_fixture):
    """Neither picker renders the mailbox list inline.

    A plain ``<select>`` would embed every mailbox on the instance in the page;
    both fields must go through the admin autocomplete endpoint instead.
    """
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])
    response = admin_client.get(url)

    assert response.status_code == 200
    form = response.context["form"]
    autocomplete_url = reverse("admin:autocomplete")
    for name in ("mailbox", "destination"):
        widget = form.fields[name].widget
        assert isinstance(widget, AutocompleteSelect)
        attrs = widget.build_attrs({})
        assert attrs["data-ajax--url"] == autocomplete_url
        assert attrs["data-model-name"] == "mailboxaccess"
        assert attrs["data-field-name"] == "mailbox"

    # A mailbox that is neither selected nor searched for must not be in the page.
    other = Mailbox.objects.create(
        local_part="not-rendered", domain=mailbox_fixture.domain
    )
    response = admin_client.get(url)
    assert str(other).encode() not in response.content


@pytest.fixture
def outsider_with_mailbox(db, domain):
    """A user holding a mailbox of their own, to use as an export destination."""

    def _make(**user_kwargs):
        user = factories.UserFactory(**user_kwargs)
        mailbox = Mailbox.objects.create(
            local_part=f"box-{user.pk.hex[:8]}", domain=domain
        )
        MailboxAccess.objects.create(
            mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.ADMIN
        )
        return user, mailbox

    return _make


@pytest.mark.parametrize(
    "user_kwargs",
    [
        pytest.param(None, id="anonymous"),
        pytest.param({"is_staff": False, "is_superuser": False}, id="regular-user"),
        pytest.param({"is_staff": True, "is_superuser": False}, id="staff-not-super"),
    ],
)
@pytest.mark.parametrize(
    "url_name", ["core_mailbox_export", "core_mailbox_export_form"]
)
@pytest.mark.django_db
def test_admin_export_view_requires_superuser(
    client, mailbox_fixture, outsider_with_mailbox, user_kwargs, url_name
):
    """Only superusers reach the export endpoints, on GET and on POST.

    The whole Django admin is superuser-only, but this endpoint hands out a
    7-day link to a full mailbox archive, so it is pinned here on its own.
    """
    destination = None
    if user_kwargs is not None:
        user, destination = outsider_with_mailbox(**user_kwargs)
        client.force_login(user)

    if url_name == "core_mailbox_export":
        url = reverse(f"admin:{url_name}", args=[mailbox_fixture.pk])
    else:
        url = reverse(f"admin:{url_name}")

    with patch("core.admin.export_mailbox_task") as mock_task:
        get_response = client.get(url)
        post_response = client.post(
            url,
            data={
                "mailbox": str(mailbox_fixture.pk),
                "destination": str(destination.pk) if destination else "",
            },
        )

    for response in (get_response, post_response):
        assert response.status_code in (302, 403)
        if response.status_code == 302:
            assert response["Location"].startswith(reverse("admin:login"))
    mock_task.delay.assert_not_called()


@pytest.mark.django_db
def test_admin_export_view_refuses_staff_even_without_the_site_gate(
    client, mailbox_fixture, outsider_with_mailbox, monkeypatch
):
    """The view's own check holds when the site-wide superuser gate is gone.

    ``core.admin`` monkeypatches ``AdminSite.has_permission`` to superuser-only
    for every admin URL. This restores Django's default (``is_active and
    is_staff``) to prove the export endpoint is not relying on that patch.
    """
    monkeypatch.setattr(
        "django.contrib.admin.AdminSite.has_permission",
        lambda self, request: request.user.is_active and request.user.is_staff,
    )
    user, destination = outsider_with_mailbox(is_staff=True, is_superuser=False)
    client.force_login(user)
    url = reverse("admin:core_mailbox_export", args=[mailbox_fixture.pk])

    with patch("core.admin.export_mailbox_task") as mock_task:
        get_response = client.get(url)
        post_response = client.post(
            url,
            data={
                "mailbox": str(mailbox_fixture.pk),
                "destination": str(destination.pk),
            },
        )

    assert get_response.status_code == 403
    assert post_response.status_code == 403
    mock_task.delay.assert_not_called()


@pytest.mark.django_db
def test_mailbox_autocomplete_endpoint_searches(admin_client, domain):
    """The endpoint the pickers call filters server-side and pages results."""
    for i in range(30):
        Mailbox.objects.create(local_part=f"needle{i}", domain=domain)
    Mailbox.objects.create(local_part="haystack", domain=domain)

    response = admin_client.get(
        reverse("admin:autocomplete"),
        data={
            "app_label": "core",
            "model_name": "mailboxaccess",
            "field_name": "mailbox",
            "term": "needle",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    assert all("needle" in row["text"] for row in payload["results"])
    # Paginated, so thousands of mailboxes never land in one response.
    assert payload["pagination"]["more"] is True


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
    requester_mailbox = Mailbox.objects.create(local_part="requester", domain=domain)
    MailboxAccess.objects.create(
        mailbox=requester_mailbox,
        user=user,
        role=enums.MailboxRoleChoices.ADMIN,
    )

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
        export_result = export_mailbox_task(
            str(mailbox_a.id), str(user.id), str(requester_mailbox.id)
        )

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

    # 4. Import into mailbox B via the unified mbox runner
    channel = create_import_channel(
        recipient=mailbox_b,
        user=user,
        source_type=enums.ImportSource.MBOX.value,
        file_key=import_key,
    )
    success_count, failure_count, total = run_mbox(channel, {})

    assert total == 3
    assert success_count == 3
    assert failure_count == 0

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
    # Mark as read and starred via ThreadAccess
    access = ThreadAccess.objects.get(thread=msg.thread, mailbox=mailbox_fixture)
    access.read_at = timezone.now()
    access.starred_at = timezone.now()
    access.save(update_fields=["read_at", "starred_at"])

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
def test_export_starred_flag_is_mailbox_scoped(
    mailbox_fixture, domain, admin_user, cleanup_exports
):
    """Test that the starred flag in export is scoped per mailbox.

    When a thread is starred in one mailbox but not another, only the
    export for the starred mailbox should contain X-Status: F.
    """
    # Create a message tied to mailbox_fixture
    msg = create_test_message(mailbox_fixture, "Shared Thread", "Shared content")

    # Mark as read and starred for mailbox_fixture
    access = ThreadAccess.objects.get(thread=msg.thread, mailbox=mailbox_fixture)
    access.read_at = timezone.now()
    access.starred_at = timezone.now()
    access.save(update_fields=["read_at", "starred_at"])

    # Create another mailbox sharing the same thread, without starred_at
    other_mailbox = Mailbox.objects.create(local_part="other", domain=domain)
    ThreadAccess.objects.create(
        thread=msg.thread, mailbox=other_mailbox, read_at=timezone.now()
    )

    mock_task = MagicMock()

    # Export mailbox_fixture — should contain X-Status: F
    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result_starred = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

    assert result_starred["status"] == "SUCCESS"
    cleanup_exports.append(result_starred["result"]["s3_key"])

    # Export other_mailbox — should NOT contain X-Status: F
    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result_not_starred = export_mailbox_task(
            str(other_mailbox.id), str(admin_user.id), str(other_mailbox.id)
        )

    assert result_not_starred["status"] == "SUCCESS"
    cleanup_exports.append(result_not_starred["result"]["s3_key"])

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client

    # Verify starred mailbox export includes X-Status: F
    response = s3_client.get_object(
        Bucket=storage.bucket_name, Key=result_starred["result"]["s3_key"]
    )
    with gzip.open(BytesIO(response["Body"].read()), "rb") as f:
        starred_content = f.read()
        assert b"Status: RO" in starred_content
        assert b"X-Status: F" in starred_content

    # Verify other mailbox export does NOT include X-Status: F
    response = s3_client.get_object(
        Bucket=storage.bucket_name, Key=result_not_starred["result"]["s3_key"]
    )
    with gzip.open(BytesIO(response["Body"].read()), "rb") as f:
        other_content = f.read()
        assert b"Status: RO" in other_content
        assert b"X-Status: F" not in other_content


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
    )
    thread = Thread.objects.create(subject="Message with Received headers")
    access = ThreadAccess.objects.create(thread=thread, mailbox=mailbox_fixture)
    msg = Message.objects.create(
        thread=thread,
        blob=blob,
        subject="Message with Received headers",
        sender=factories.ContactFactory(email="sender@example.com"),
        is_sender=False,
    )
    # Mark as read via ThreadAccess.read_at
    access.read_at = timezone.now()
    access.save(update_fields=["read_at"])
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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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
    # Create an unread message (no read_at on ThreadAccess → unread)
    create_test_message(mailbox_fixture, "Unread Message", "Unread content")

    mock_task = MagicMock()

    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(
            str(mailbox_fixture.id), str(admin_user.id), str(mailbox_fixture.id)
        )

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


def read_export(s3_key):
    """Download and decompress an exported MBOX."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    response = s3_client.get_object(Bucket=storage.bucket_name, Key=s3_key)
    with gzip.open(BytesIO(response["Body"].read()), "rb") as f:
        return f.read()


def run_export(mailbox_obj, user, cleanup_exports):
    """Run the export task for a mailbox and return the exported MBOX bytes."""
    mock_task = MagicMock()
    with (
        patch.object(export_mailbox_task, "update_state", mock_task.update_state),
        patch(
            "core.services.exporter.tasks.deliver_inbound_message", return_value=True
        ),
    ):
        result = export_mailbox_task(
            str(mailbox_obj.id), str(user.id), str(mailbox_obj.id)
        )

    assert result["status"] == "SUCCESS"
    cleanup_exports.append(result["result"]["s3_key"])
    return read_export(result["result"]["s3_key"])


@pytest.mark.django_db
def test_export_sent_message_labels(mailbox_fixture, admin_user, cleanup_exports):
    """Sent mail is marked through X-Gmail-Labels, not through X-Status.

    ``A`` is \\Answered ("was replied to"), which says nothing about who sent
    the message, and IMAP has no per-message sent flag at all.
    """
    msg = create_test_message(mailbox_fixture, "Sent Message", "Content")
    Message.objects.filter(id=msg.id).update(is_sender=True)

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    assert b"X-Gmail-Labels: Sent, Unread\n" in mbox_content
    assert b"X-Status:" not in mbox_content
    # X-Keywords carries the user's own labels only: "Sent" is not an IMAP
    # keyword and would show up as a tag of the user's in Dovecot or mu4e.
    assert b"X-Keywords:" not in mbox_content


@pytest.mark.django_db
def test_export_system_labels_per_flag(mailbox_fixture, admin_user, cleanup_exports):
    """Each message flag maps to its Gmail-style system label."""
    trashed = create_test_message(mailbox_fixture, "Trashed", "Content")
    Message.objects.filter(id=trashed.id).update(
        is_trashed=True, trashed_at=timezone.now()
    )
    spam = create_test_message(mailbox_fixture, "Spammy", "Content")
    Message.objects.filter(id=spam.id).update(is_spam=True)
    archived = create_test_message(mailbox_fixture, "Archived", "Content")
    Message.objects.filter(id=archived.id).update(
        is_archived=True, archived_at=timezone.now()
    )
    create_test_message(mailbox_fixture, "Plain", "Content")

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    assert b"X-Gmail-Labels: Trash, Unread\n" in mbox_content
    assert b"X-Gmail-Labels: Spam, Unread\n" in mbox_content
    assert b"X-Gmail-Labels: Archived, Unread\n" in mbox_content
    # Nothing set: the message is in the inbox, as Takeout spells it
    assert b"X-Gmail-Labels: Inbox, Unread\n" in mbox_content
    # Trashed is \\Deleted in mbox flag terms
    assert b"X-Status: D" in mbox_content


@pytest.mark.django_db
def test_export_strips_stale_label_headers(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Label/status headers from a previous system are dropped, not merged.

    A message imported from Takeout keeps the headers it arrived with; the
    mailbox state has moved on since (here: untrashed, relabelled), and the
    importer reads every occurrence of those headers.
    """
    eml_content = (
        b"X-Gmail-Labels: Trash, Starred, old-label\r\n"
        b"X-Keywords: old-label,\r\n"
        b"\tsecond-old-label\r\n"
        b"Status: RO\r\n"
        b"X-Status: FD\r\n"
        b"X-Mozilla-Status: 0005\r\n"
        b"X-Mozilla-Status2: 10000000\r\n"
        b"X-Mozilla-Keys: old-tag\r\n"
        b"From: sender@example.com\r\n"
        b"To: test@example.com\r\n"
        b"Subject: Previously imported\r\n"
        b"Date: Mon, 26 May 2025 20:13:44 +0200\r\n"
        b"Message-ID: <stale-headers@example.com>\r\n"
        b"\r\n"
        b"Body content\r\n"
    )
    blob = Blob.objects.create_blob(content=eml_content, content_type="message/rfc822")
    thread = Thread.objects.create(subject="Previously imported")
    ThreadAccess.objects.create(thread=thread, mailbox=mailbox_fixture)
    Message.objects.create(
        thread=thread,
        blob=blob,
        subject="Previously imported",
        sender=factories.ContactFactory(email="sender@example.com"),
        is_sender=False,
    )

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    # Injected headers follow the message's own CRLF line endings
    assert b"X-Gmail-Labels: Inbox, Unread\r\n" in mbox_content
    assert b"old-label" not in mbox_content
    assert b"second-old-label" not in mbox_content
    assert b"Trash" not in mbox_content
    assert b"X-Status:" not in mbox_content
    assert b"Status: RO" not in mbox_content
    # Thunderbird's own state is stale the same way: the message is not read,
    # not starred and has no attachment in this mailbox
    assert b"X-Mozilla-Status: 0000\r\n" in mbox_content
    assert b"X-Mozilla-Status2: 00000000\r\n" in mbox_content
    assert b"X-Mozilla-Status: 0005" not in mbox_content
    assert b"X-Mozilla-Keys" not in mbox_content
    assert b"old-tag" not in mbox_content
    # The rest of the message is untouched
    assert b"Subject: Previously imported" in mbox_content
    assert b"Body content" in mbox_content


@pytest.mark.django_db
def test_export_includes_a_genuinely_sent_message(domain, cleanup_exports):
    """A message composed and sent through the app lands in the export, tagged Sent.

    The other sent-mail tests set ``is_sender`` with an UPDATE on a fabricated
    row. This one builds the message the way the app does, so it also covers
    the two things the export depends on that are set far away from it: the
    ThreadAccess ``create_draft`` grants (mda/draft.py) and the blob the send
    path fills in (mda/outbound.py). Either one going missing would empty sent
    mail out of every export while the fabricated tests stayed green.
    """
    mailbox = Mailbox.objects.create(local_part="sender", domain=domain)
    user = factories.UserFactory(is_superuser=True, is_staff=True)
    MailboxAccess.objects.create(
        mailbox=mailbox, user=user, role=enums.MailboxRoleChoices.ADMIN
    )

    message = create_draft(
        mailbox=mailbox,
        subject="A really sent message",
        draft_body='{"text": "hello"}',
        to_emails=["recipient@elsewhere.example"],
        user=user,
    )
    assert prepare_outbound_message(
        mailbox,
        message,
        "Body of a sent message",
        "<p>Body of a sent message</p>",
        user,
    )

    message.refresh_from_db()
    assert message.is_sender is True
    assert message.is_draft is False
    assert message.blob is not None

    mbox_content = run_export(mailbox, user, cleanup_exports)

    assert b"Subject: A really sent message" in mbox_content
    assert b"Body of a sent message" in mbox_content
    # Tagged as sent, and read: it is the sender's own message
    assert b"X-Gmail-Labels: Sent, Opened\r\n" in mbox_content
    assert b"X-Mozilla-Status: 0001\r\n" in mbox_content
    assert b"Status: RO\r\n" in mbox_content


@pytest.mark.django_db
def test_export_mozilla_status_headers(mailbox_fixture, admin_user, cleanup_exports):
    """Read and starred state in the only form Thunderbird reads.

    Thunderbird keeps flags in its .msf index, not in the mbox, and ignores
    X-Keywords and X-Gmail-Labels, so without these an import is all-unread.
    """
    read_starred = create_test_message(mailbox_fixture, "Read starred", "Content")
    access = ThreadAccess.objects.get(
        thread=read_starred.thread, mailbox=mailbox_fixture
    )
    access.read_at = timezone.now()
    access.starred_at = timezone.now()
    access.save(update_fields=["read_at", "starred_at"])

    create_test_message(mailbox_fixture, "Plain unread", "Content")

    with_attachment = create_test_message(mailbox_fixture, "Attached", "Content")
    Message.objects.filter(id=with_attachment.id).update(has_attachments=True)

    trashed = create_test_message(mailbox_fixture, "Trashed", "Content")
    Message.objects.filter(id=trashed.id).update(
        is_trashed=True, trashed_at=timezone.now()
    )

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    # Read (0x0001) + Marked (0x0004)
    assert b"X-Mozilla-Status: 0005\n" in mbox_content
    assert b"X-Mozilla-Status: 0000\n" in mbox_content
    assert b"X-Mozilla-Status2: 10000000\n" in mbox_content  # Attachment
    # Expunged (0x0008) means "deleted, pending compaction": Thunderbird may
    # drop the message for good on the next compact, so a trashed message must
    # never carry it.
    statuses = re.findall(rb"X-Mozilla-Status: ([0-9a-f]{4})\n", mbox_content)
    assert len(statuses) == 4
    assert all(int(value, 16) & 0x0008 == 0 for value in statuses)


@pytest.mark.django_db
def test_export_label_with_newline_cannot_inject_headers(domain, cleanup_exports):
    """A label name is user input: a newline in it must not forge headers.

    ``Label.name`` has no validation, so a name can carry CRLF. Written as is
    into a label header it would end that header and turn the rest of the name
    into headers of its own, which a re-import reads back as real ones.
    """
    mailbox_a = Mailbox.objects.create(local_part="inject-source", domain=domain)
    mailbox_b = Mailbox.objects.create(local_part="inject-target", domain=domain)
    user = factories.UserFactory(is_superuser=True, is_staff=True)
    MailboxAccess.objects.create(
        mailbox=mailbox_a, user=user, role=enums.MailboxRoleChoices.ADMIN
    )

    msg = create_test_message(mailbox_a, "Injected", "Content")
    hostile = (
        "ok\r\nFrom attacker@example.com Mon Jan  1 00:00:00 2035"
        "\r\nX-Gmail-Labels: Trash"
    )
    label = Label.objects.create(name=hostile, slug="hostile", mailbox=mailbox_a)
    msg.thread.labels.add(label)

    mbox_content = run_export(mailbox_a, user, cleanup_exports)

    # One message, one set of headers: no forged separator, no forged header.
    # The hostile text survives as label text, but only ever inside a header
    # value: any line it lands on through folding starts with whitespace.
    assert mbox_content.count(b"\nFrom ") == 0
    assert mbox_content.count(b"From - ") == 1
    assert b"\nX-Gmail-Labels: Trash" not in mbox_content
    assert b"attacker@example.com Mon" in mbox_content
    hostile_lines = [
        line
        for line in mbox_content.split(b"\n")
        if b"attacker@example.com" in line or b"X-Gmail-Labels: Trash" in line
    ]
    assert hostile_lines
    assert all(
        line.startswith((b" ", b"\t", b"X-Keywords:", b"X-Gmail-Labels: Inbox"))
        for line in hostile_lines
    )

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    import_key = f"imports/{mailbox_b.id}/injected.mbox"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=import_key,
        Body=mbox_content,
        ContentType="text/plain",
    )
    cleanup_exports.append(import_key)
    channel = create_import_channel(
        recipient=mailbox_b,
        user=user,
        source_type=enums.ImportSource.MBOX.value,
        file_key=import_key,
    )
    assert run_mbox(channel, {}) == (1, 0, 1)

    imported = Message.objects.get(thread__accesses__mailbox=mailbox_b)
    # The forged "X-Gmail-Labels: Trash" would have landed here
    assert imported.is_trashed is False
    assert Label.objects.filter(mailbox=mailbox_b).count() == 1


@pytest.mark.django_db
def test_export_folds_and_encodes_label_headers(
    mailbox_fixture, admin_user, cleanup_exports
):
    """Label headers stay inside the RFC 5322 line limit and stay 7-bit."""
    msg = create_test_message(mailbox_fixture, "Many labels", "Content")
    names = [f"label-{i}-" + "x" * 50 for i in range(20)]
    names.append("Messages archivés")
    for i, name in enumerate(names):
        msg.thread.labels.add(
            Label.objects.create(name=name, slug=f"l{i}", mailbox=mailbox_fixture)
        )

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    assert max(len(line) for line in mbox_content.split(b"\n")) <= 998
    # Non-ASCII is RFC 2047 encoded, as Takeout does, so the file stays 7-bit
    assert mbox_content.isascii()
    assert b"=?utf-8?" in mbox_content


FROM_LINES_BODY = (
    b"Body line one\n"
    b"From here it looks like a separator\n"
    b">From here it was already quoted\n"
    b">>From here it was quoted twice\n"
    b"Tail line\n"
)


def create_from_lines_message(mailbox_obj):
    """A message whose body has every escaping-relevant form of a From line."""
    eml_content = (
        b"From: sender@example.com\n"
        b"To: test@example.com\n"
        b"Subject: From lines\n"
        b"Date: Mon, 26 May 2025 20:13:44 +0200\n"
        b"Message-ID: <from-lines@example.com>\n"
        b"\n" + FROM_LINES_BODY
    )
    blob = Blob.objects.create_blob(content=eml_content, content_type="message/rfc822")
    thread = Thread.objects.create(subject="From lines")
    ThreadAccess.objects.create(thread=thread, mailbox=mailbox_obj)
    Message.objects.create(
        thread=thread,
        blob=blob,
        subject="From lines",
        sender=factories.ContactFactory(email="sender@example.com"),
        is_sender=False,
    )


@pytest.mark.django_db
def test_export_escapes_from_lines_mboxrd(mailbox_fixture, admin_user, cleanup_exports):
    """Every From line gets one more '>', including already-quoted ones.

    That is the mboxrd rule, and the only escaping a reader can reverse:
    mboxo leaves ">From " untouched, which makes it indistinguishable from
    an escaped "From ".
    """
    create_from_lines_message(mailbox_fixture)

    mbox_content = run_export(mailbox_fixture, admin_user, cleanup_exports)

    assert b"\n>From here it looks like a separator\n" in mbox_content
    assert b"\n>>From here it was already quoted\n" in mbox_content
    assert b"\n>>>From here it was quoted twice\n" in mbox_content
    # The separator line the exporter writes is the only unescaped one
    assert mbox_content.count(b"\nFrom ") == 0
    assert mbox_content.startswith(b"From - ")


@pytest.mark.django_db
def test_export_reimport_roundtrip_preserves_body_bytes(domain, cleanup_exports):
    """A body with From lines comes back byte for byte."""
    mailbox_a = Mailbox.objects.create(local_part="from-source", domain=domain)
    mailbox_b = Mailbox.objects.create(local_part="from-target", domain=domain)
    user = factories.UserFactory(is_superuser=True, is_staff=True)
    MailboxAccess.objects.create(
        mailbox=mailbox_a, user=user, role=enums.MailboxRoleChoices.ADMIN
    )
    create_from_lines_message(mailbox_a)

    mbox_content = run_export(mailbox_a, user, cleanup_exports)

    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    import_key = f"imports/{mailbox_b.id}/from-lines.mbox"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=import_key,
        Body=mbox_content,
        ContentType="text/plain",
    )
    cleanup_exports.append(import_key)

    channel = create_import_channel(
        recipient=mailbox_b,
        user=user,
        source_type=enums.ImportSource.MBOX.value,
        file_key=import_key,
    )
    success_count, failure_count, total = run_mbox(channel, {})
    assert (success_count, failure_count, total) == (1, 0, 1)

    imported = Message.objects.get(thread__accesses__mailbox=mailbox_b)
    _headers, _, body = imported.blob.get_content().partition(b"\n\n")
    assert body == FROM_LINES_BODY


@pytest.mark.django_db
def test_export_reimport_roundtrip_preserves_flags(domain, cleanup_exports):
    """E2E: every flag the export writes is read back by the mbox importer."""
    mailbox_a = Mailbox.objects.create(local_part="flags-source", domain=domain)
    mailbox_b = Mailbox.objects.create(local_part="flags-target", domain=domain)
    user = factories.UserFactory(is_superuser=True, is_staff=True)
    MailboxAccess.objects.create(
        mailbox=mailbox_a, user=user, role=enums.MailboxRoleChoices.ADMIN
    )

    # Every message is From a third party, so the importer's "From matches the
    # destination mailbox" heuristic says is_sender=False for all of them: the
    # Sent state can only come back through the exported labels.
    sent = create_test_message(mailbox_a, "Sent one", "Content")
    Message.objects.filter(id=sent.id).update(is_sender=True)
    trashed = create_test_message(mailbox_a, "Trashed one", "Content")
    Message.objects.filter(id=trashed.id).update(
        is_trashed=True, trashed_at=timezone.now()
    )
    spam = create_test_message(mailbox_a, "Spam one", "Content")
    Message.objects.filter(id=spam.id).update(is_spam=True)
    archived = create_test_message(mailbox_a, "Archived one", "Content")
    Message.objects.filter(id=archived.id).update(
        is_archived=True, archived_at=timezone.now()
    )
    starred = create_test_message(mailbox_a, "Starred one", "Content")
    starred_access = ThreadAccess.objects.get(thread=starred.thread, mailbox=mailbox_a)
    starred_access.read_at = timezone.now()
    starred_access.starred_at = timezone.now()
    starred_access.save(update_fields=["read_at", "starred_at"])
    labelled = create_test_message(mailbox_a, "Labelled one", "Content")
    label = Label.objects.create(
        name="roundtrip-flags", slug="roundtrip-flags", mailbox=mailbox_a
    )
    labelled.thread.labels.add(label)
    create_test_message(mailbox_a, "Plain one", "Content")

    mbox_content = run_export(mailbox_a, user, cleanup_exports)

    # Upload the uncompressed MBOX and import it into the target mailbox
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    import_key = f"imports/{mailbox_b.id}/flags.mbox"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=import_key,
        Body=mbox_content,
        ContentType="text/plain",
    )
    cleanup_exports.append(import_key)

    channel = create_import_channel(
        recipient=mailbox_b,
        user=user,
        source_type=enums.ImportSource.MBOX.value,
        file_key=import_key,
    )
    success_count, failure_count, total = run_mbox(channel, {})
    assert (success_count, failure_count, total) == (7, 0, 7)

    imported = {
        msg.subject: msg
        for msg in Message.objects.filter(thread__accesses__mailbox=mailbox_b)
    }
    assert len(imported) == 7

    assert imported["Sent one"].is_sender is True
    assert imported["Trashed one"].is_trashed is True
    # A trashed row with no trashed_at breaks restore, ordering and auto-purge
    assert imported["Trashed one"].trashed_at is not None
    assert imported["Spam one"].is_spam is True
    assert imported["Archived one"].is_archived is True
    assert imported["Archived one"].archived_at is not None

    # Nothing leaks onto the messages that had no flag set
    plain = imported["Plain one"]
    assert (plain.is_sender, plain.is_trashed, plain.is_spam, plain.is_archived) == (
        False,
        False,
        False,
        False,
    )

    # Read and starred state live on ThreadAccess, not on the message
    starred_access = ThreadAccess.objects.get(
        thread=imported["Starred one"].thread, mailbox=mailbox_b
    )
    assert starred_access.starred_at is not None
    assert starred_access.read_at is not None
    plain_access = ThreadAccess.objects.get(thread=plain.thread, mailbox=mailbox_b)
    assert plain_access.read_at is None
    assert plain_access.starred_at is None

    # The user's own label roundtrips, and no system label became one
    assert (
        imported["Labelled one"]
        .thread.labels.filter(name="roundtrip-flags", mailbox=mailbox_b)
        .exists()
    )
    assert set(
        Label.objects.filter(mailbox=mailbox_b).values_list("name", flat=True)
    ) == {"roundtrip-flags"}
