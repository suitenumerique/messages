"""Tests for the import-run API (mailbox-nested Channels with type=import)."""

# pylint: disable=redefined-outer-name, unused-argument

from unittest.mock import MagicMock, patch

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.services.importer.channel import (
    create_import_channel,
    enable_continuous,
    mark_finished,
    mark_started,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def mailbox(user):
    mailbox = factories.MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
    return mailbox


def _make_import(mailbox, user, source=enums.ImportSource.MBOX):
    return create_import_channel(recipient=mailbox, user=user, source_type=source.value)


@pytest.mark.django_db
class TestImportList:
    def test_lists_only_accessible_imports(self, api_client, mailbox, user):
        mine = _make_import(mailbox, user)
        _make_import(factories.MailboxFactory(), factories.UserFactory())

        response = api_client.get(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id})
        )
        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.data] == [str(mine.id)]

    def test_orders_by_last_activity_desc(self, api_client, mailbox, user):
        """Most recently active first: heartbeat (last_used_at) when the run
        has one, falling back to created_at for a never-dispatched run."""
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        old = _make_import(mailbox, user)  # created first, ran long ago
        models.Channel.objects.filter(id=old.id).update(
            created_at=now - timedelta(days=10),
            last_used_at=now - timedelta(days=9),
        )
        recent = _make_import(mailbox, user)  # created earlier but ran just now
        models.Channel.objects.filter(id=recent.id).update(
            created_at=now - timedelta(days=5),
            last_used_at=now - timedelta(minutes=1),
        )
        fresh = _make_import(mailbox, user)  # just created, never ran
        models.Channel.objects.filter(id=fresh.id).update(
            created_at=now, last_used_at=None
        )

        response = api_client.get(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id})
        )
        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.data] == [
            str(fresh.id),
            str(recent.id),
            str(old.id),
        ]

    def test_non_admin_role_cannot_see_imports(self, api_client, user):
        mailbox = factories.MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        _make_import(mailbox, user)

        response = api_client.get(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id})
        )
        # IsMailboxAdmin: a non-admin gets denied, not an empty list.
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestImportRetrieve:
    def test_retrieve_exposes_run_state(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_started(channel.id, total=4)
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=3,
            failure=1,
            total=4,
        )

        response = api_client.get(
            reverse(
                "mailbox-imports-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == enums.ImportStatus.COMPLETED.value
        assert response.data["source_type"] == enums.ImportSource.MBOX.value
        assert response.data["total_messages"] == 4
        assert response.data["success_count"] == 3
        assert response.data["failure_count"] == 1
        assert response.data["progress"] == 100.0
        assert response.data["is_active"] is False

    def test_retrieve_exposes_imap_username_but_never_password(
        self, api_client, mailbox, user
    ):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "acct@example.com", "password": "secret"},
        )
        response = api_client.get(
            reverse(
                "mailbox-imports-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["imap_username"] == "acct@example.com"
        assert "secret" not in str(response.data)

    def test_retrieve_running_shows_partial_progress(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_started(channel.id, total=4)
        from core.services.importer.channel import (
            record_progress,  # pylint: disable=import-outside-toplevel
        )

        record_progress(channel.id, success=1, failure=1)
        response = api_client.get(
            reverse(
                "mailbox-imports-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == enums.ImportStatus.RUNNING.value
        assert response.data["success_count"] == 1
        assert response.data["failure_count"] == 1
        assert response.data["progress"] == 50.0
        assert response.data["is_active"] is True

    def test_retrieve_failed_run_exposes_error(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_finished(
            channel,
            status=enums.ImportStatus.FAILED.value,
            success=0,
            failure=2,
            total=2,
            error="IMAP authentication failed",
        )
        response = api_client.get(
            reverse(
                "mailbox-imports-detail",
                kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == enums.ImportStatus.FAILED.value
        assert response.data["error"] == "IMAP authentication failed"
        assert response.data["is_active"] is False

    def test_retrieve_no_access_returns_403(self, api_client):
        other_mailbox = factories.MailboxFactory()
        channel = _make_import(other_mailbox, factories.UserFactory())
        response = api_client.get(
            reverse(
                "mailbox-imports-detail",
                kwargs={"mailbox_id": other_mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestImportCancel:
    def test_cancel_is_async_and_marks_cancelled(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        contact = factories.ContactFactory(mailbox=mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        factories.MessageFactory(thread=thread, sender=contact, channel=channel)

        with patch("core.api.viewsets.imports.cancel_import_task.delay") as mock_delay:
            response = api_client.post(
                reverse(
                    "mailbox-imports-cancel",
                    kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
                )
            )
        # Cancel is now async: the run is flipped to cancelled synchronously and
        # the message deletion is offloaded to a background task.
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_delay.assert_called_once_with(str(channel.id))
        channel.refresh_from_db()
        assert channel.is_active is False
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_cancel_is_idempotent(self, api_client, mailbox, user):
        """A second cancel (retry, double-click) is safe: 202 again, the run
        stays CANCELLED and another purge task is queued (purge is a no-op)."""
        channel = _make_import(mailbox, user)
        url = reverse(
            "mailbox-imports-cancel",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )
        with patch("core.api.viewsets.imports.cancel_import_task.delay") as mock_delay:
            first = api_client.post(url)
            second = api_client.post(url)
        assert first.status_code == status.HTTP_202_ACCEPTED
        assert second.status_code == status.HTTP_202_ACCEPTED
        assert mock_delay.call_count == 2
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_cancel_no_access_returns_403(self, api_client):
        other_mailbox = factories.MailboxFactory()
        channel = _make_import(other_mailbox, factories.UserFactory())
        response = api_client.post(
            reverse(
                "mailbox-imports-cancel",
                kwargs={"mailbox_id": other_mailbox.id, "pk": channel.id},
            )
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestImportUpdate:
    """PATCH /mailboxes/{id}/imports/{id}/ — arm/pause a continuous poller."""

    def _imap_import(self, mailbox, user):
        return create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
        )

    def _url(self, mailbox, channel):
        return reverse(
            "mailbox-imports-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

    def test_patch_continuous_makes_import_continuous_and_dispatches(
        self, api_client, mailbox, user
    ):
        channel = self._imap_import(mailbox, user)
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=1,
            failure=0,
            total=1,
        )
        with patch("core.api.viewsets.imports.run_import_task.delay") as mock_delay:
            response = api_client.patch(
                self._url(mailbox, channel),
                {"mode": "continuous"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["mode"] == enums.ImportMode.CONTINUOUS.value
        # Cadence (seconds) comes from the global setting, not the request.
        assert response.data["poll_interval"] == 900
        assert response.data["is_active"] is True
        mock_delay.assert_called_once_with(str(channel.id))

    def test_patch_continuous_rejects_non_imap(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user, enums.ImportSource.MBOX)
        response = api_client.patch(
            self._url(mailbox, channel),
            {"mode": "continuous"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "mode" in response.data  # rejected for the right reason

    def test_patch_is_active_false_pauses_continuous_import(
        self, api_client, mailbox, user
    ):
        channel = self._imap_import(mailbox, user)
        enable_continuous(channel)
        with patch("core.api.viewsets.imports.run_import_task.delay") as mock_delay:
            response = api_client.patch(
                self._url(mailbox, channel),
                {"is_active": False},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_active"] is False
        # Actually paused, not just echoed: durable flag off and no dispatch.
        channel.refresh_from_db()
        assert channel.is_active is False
        mock_delay.assert_not_called()

    def test_patch_is_active_false_on_oneshot_rejected(self, api_client, mailbox, user):
        """Pausing a one-shot is rejected: it would disable crash-recovery for
        a running run, and (re-activation being rejected too) strand it as
        'running' forever with cancel as the only exit."""
        channel = self._imap_import(mailbox, user)  # default mode: oneshot
        response = api_client.patch(
            self._url(mailbox, channel), {"is_active": False}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is_active" in response.data
        channel.refresh_from_db()
        assert channel.is_active is True  # crash-recovery still armed

    def test_patch_continuous_with_pause_persists_mode(self, api_client, mailbox, user):
        """{mode: continuous, is_active: false} means "arm as a poller but
        start it paused": the mode must be persisted (not silently dropped)
        and no run dispatched, so a later is_active=true can resume it."""
        channel = self._imap_import(mailbox, user)
        with patch("core.api.viewsets.imports.run_import_task.delay") as mock_delay:
            response = api_client.patch(
                self._url(mailbox, channel),
                {"mode": "continuous", "is_active": False},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["mode"] == enums.ImportMode.CONTINUOUS.value
        assert response.data["is_active"] is False
        mock_delay.assert_not_called()
        # And the paused poller can now actually be resumed.
        with patch("core.api.viewsets.imports.run_import_task.delay") as mock_delay:
            response = api_client.patch(
                self._url(mailbox, channel), {"is_active": True}, format="json"
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_active"] is True
        mock_delay.assert_called_once_with(str(channel.id))

    def test_patch_empty_body_rejected(self, api_client, mailbox, user):
        channel = self._imap_import(mailbox, user)
        response = api_client.patch(self._url(mailbox, channel), {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "non_field_errors" in response.data

    def test_patch_is_active_true_on_oneshot_rejected(self, api_client, mailbox, user):
        """is_active=true only re-arms a poller; a one-shot import has nothing to
        re-activate, so it must 400 rather than silently no-op."""
        channel = _make_import(mailbox, user, enums.ImportSource.MBOX)
        response = api_client.patch(
            self._url(mailbox, channel), {"is_active": True}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is_active" in response.data

    def test_patch_no_access_returns_403(self, api_client):
        """PATCH was the only verb without a cross-mailbox permission test."""
        other_mailbox = factories.MailboxFactory()
        channel = _make_import(other_mailbox, factories.UserFactory())
        response = api_client.patch(
            self._url(other_mailbox, channel), {"is_active": False}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_oneshot_demotes_continuous_and_stops_polling(
        self, api_client, mailbox, user
    ):
        """mode=oneshot on a continuous import must actually stop the poller
        (not 200 while silently keeping the server connecting with the user's
        credentials every interval)."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )
        with patch("core.api.viewsets.imports.run_import_task.delay") as mock_delay:
            response = api_client.patch(
                self._url(mailbox, channel), {"mode": "oneshot"}, format="json"
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["mode"] == enums.ImportMode.ONESHOT.value
        assert response.data["is_active"] is False
        mock_delay.assert_not_called()
        channel.refresh_from_db()
        assert channel.is_active is False
        assert channel.settings["import"]["mode"] == enums.ImportMode.ONESHOT.value

    def test_patch_oneshot_with_is_active_true_rejected(
        self, api_client, mailbox, user
    ):
        """mode=oneshot (stop polling) contradicts is_active=true (poll)."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )
        response = api_client.patch(
            self._url(mailbox, channel),
            {"mode": "oneshot", "is_active": True},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "mode" in response.data


@pytest.mark.django_db
class TestImportDelete:
    """DELETE /mailboxes/{id}/imports/{id}/ — forget a run, keep its mail."""

    def _url(self, mailbox, channel):
        return reverse(
            "mailbox-imports-detail",
            kwargs={"mailbox_id": mailbox.id, "pk": channel.id},
        )

    def test_delete_forgets_run_but_keeps_messages(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=1,
            failure=0,
            total=1,
        )
        contact = factories.ContactFactory(mailbox=mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        message = factories.MessageFactory(
            thread=thread, sender=contact, channel=channel
        )

        response = api_client.delete(self._url(mailbox, channel))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.Channel.objects.filter(id=channel.id).exists()
        # The mail survives — forgetting is not cancelling.
        message.refresh_from_db()
        assert message.channel is None

    def test_delete_running_import_rejected(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_started(channel.id, total=10)
        response = api_client.delete(self._url(mailbox, channel))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert models.Channel.objects.filter(id=channel.id).exists()

    def test_delete_active_continuous_rejected_until_paused(
        self, api_client, mailbox, user
    ):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=1,
            failure=0,
            total=1,
        )
        # Still polling (is_active=True): must be paused before forgetting.
        response = api_client.delete(self._url(mailbox, channel))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        api_client.patch(
            self._url(mailbox, channel), {"is_active": False}, format="json"
        )
        response = api_client.delete(self._url(mailbox, channel))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_delete_unknown_import_returns_404(self, api_client, mailbox, user):
        response = api_client.delete(
            reverse(
                "mailbox-imports-detail",
                kwargs={
                    "mailbox_id": mailbox.id,
                    "pk": "00000000-0000-0000-0000-000000000000",
                },
            )
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_no_access_returns_403(self, api_client):
        other_mailbox = factories.MailboxFactory()
        channel = _make_import(other_mailbox, factories.UserFactory())
        response = api_client.delete(self._url(other_mailbox, channel))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestImportCreate:
    """POST /mailboxes/{id}/imports/ — the unified create endpoint (file or imap)."""

    def test_create_imap_import(self, api_client, mailbox):
        with patch("core.services.importer.service.run_import_task") as mock_task:
            response = api_client.post(
                reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                {
                    "source": "imap",
                    "imap_server": "imap.example.com",
                    "imap_port": 993,
                    "username": "[email protected]",
                    "password": "secret",
                    "use_ssl": True,
                },
                format="json",
            )
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["source_type"] == enums.ImportSource.IMAP.value
        assert response.data["status"] == enums.ImportStatus.PENDING.value
        assert "id" in response.data
        mock_task.delay.assert_called_once()

    def test_create_file_import_returns_run_body(self, api_client, mailbox, user):
        """The file-source happy path returns the run resource, not just 202."""
        from django.core.files.storage import (
            storages,  # pylint: disable=import-outside-toplevel
        )

        from core.api.utils import (
            generate_file_key,  # pylint: disable=import-outside-toplevel
        )

        storage = storages["message-imports"]
        s3_client = storage.connection.meta.client
        file_key = generate_file_key(user.id)
        with open("core/tests/resources/message.eml", "rb") as f:
            s3_client.put_object(
                Bucket=storage.bucket_name, Key=file_key, Body=f.read()
            )
        try:
            with patch("core.services.importer.service.run_import_task"):
                response = api_client.post(
                    reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                    {"source": "file", "filename": "body.eml", "file_key": file_key},
                    format="json",
                )
            assert response.status_code == status.HTTP_202_ACCEPTED
            assert response.data["source_type"] == enums.ImportSource.EML.value
            assert response.data["status"] == enums.ImportStatus.PENDING.value
            assert response.data["mode"] == enums.ImportMode.ONESHOT.value
            assert response.data["is_active"] is True
            assert response.data["imap_username"] is None
            assert response.data["poll_interval"] is None
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_create_admin_of_another_mailbox_gets_403(self, api_client, mailbox, user):
        """Being an admin *somewhere* is not enough: a user who administers
        mailbox A (and even has viewer access on B) must not be able to start
        an import into mailbox B — IsMailboxAdmin authorizes against the URL
        mailbox, not the user's best role anywhere."""
        # ``mailbox`` fixture: user is ADMIN there. Give them only VIEWER on B.
        other_mailbox = factories.MailboxFactory()
        other_mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)

        with patch("core.services.importer.service.run_import_task") as mock_task:
            response = api_client.post(
                reverse(
                    "mailbox-imports-list", kwargs={"mailbox_id": other_mailbox.id}
                ),
                {
                    "source": "imap",
                    "imap_server": "imap.example.com",
                    "imap_port": 993,
                    "username": "[email protected]",
                    "password": "secret",
                    "use_ssl": True,
                },
                format="json",
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        mock_task.delay.assert_not_called()
        assert not models.Channel.objects.filter(mailbox=other_mailbox).exists()

    def test_create_file_not_found_maps_to_404(self, api_client, mailbox, user):
        """A file import for an un-uploaded archive returns 404 — the client can
        tell a bad/missing file from a permission error (not a blanket 403)."""
        from core.api.utils import (
            generate_file_key,  # pylint: disable=import-outside-toplevel
        )

        with patch("core.services.importer.service.storages") as mock_storages:
            mock_storage = MagicMock()
            mock_storage.exists.return_value = False
            mock_storages.__getitem__.return_value = mock_storage
            response = api_client.post(
                reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                {
                    "source": "file",
                    "filename": "never-uploaded.eml",
                    "file_key": generate_file_key(user.id),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_file_requires_key_and_filename(self, api_client, mailbox):
        response = api_client.post(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
            {"source": "file"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "filename" in response.data
        assert "file_key" in response.data

    def test_create_file_rejects_foreign_file_key(self, api_client, mailbox):
        """A key minted for another user is refused: an import must never be
        pointed at someone else's upload."""
        from core.api.utils import (
            generate_file_key,  # pylint: disable=import-outside-toplevel
        )

        foreign = generate_file_key(factories.UserFactory().id)
        response = api_client.post(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
            {"source": "file", "filename": "x.mbox", "file_key": foreign},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file_key" in response.data

    def test_create_file_rejects_crafted_file_key(self, api_client, mailbox):
        """A hand-crafted key (path traversal, arbitrary bucket location) is
        refused before it can reach S3."""
        crafted = "../../blobs/0/abc"
        response = api_client.post(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
            {"source": "file", "filename": "x.mbox", "file_key": crafted},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file_key" in response.data

    def test_create_rejects_unknown_source_with_field_error(self, api_client, mailbox):
        response = api_client.post(
            reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
            {"source": "carrier-pigeon"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "source" in response.data  # rejected for the right reason

    def _imap_payload(self, **over):
        payload = {
            "source": "imap",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": "u@example.com",
            "password": "secret",
            "use_ssl": True,
            "mode": "continuous",
        }
        payload.update(over)
        return payload

    def test_create_rejects_out_of_range_imap_port(self, api_client, mailbox):
        for bad_port in (0, 70000):
            response = api_client.post(
                reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                self._imap_payload(mode="oneshot", imap_port=bad_port),
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "imap_port" in response.data

    def test_create_accepts_nonstandard_imap_port(self, api_client, mailbox):
        """Ports stay flexible — any valid TCP port, not just 143/993."""
        with patch("core.services.importer.service.run_import_task"):
            response = api_client.post(
                reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                self._imap_payload(mode="oneshot", imap_port=1143),
                format="json",
            )
        assert response.status_code == status.HTTP_202_ACCEPTED
        # The port is persisted in the (encrypted) credentials, not just
        # accepted — EncryptedJSONField round-trips scalars as strings.
        channel = models.Channel.objects.get(id=response.data["id"])
        assert int(channel.encrypted_settings["imap"]["imap_port"]) == 1143


@pytest.mark.django_db
class TestImportChannelsExcludedFromIntegrations:
    def test_import_channel_absent_from_mailbox_channels(self, api_client, user):
        mailbox = factories.MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        widget = factories.ChannelFactory(mailbox=mailbox, type="widget")
        import_channel = _make_import(mailbox, user)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in response.data]
        assert str(widget.id) in ids
        assert str(import_channel.id) not in ids
