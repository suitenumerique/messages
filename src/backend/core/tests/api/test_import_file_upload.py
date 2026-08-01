"""Test suite for ImportFileUploadViewSet."""
# pylint: disable=redefined-outer-name, unused-argument

from unittest import mock

from django.core.cache import cache
from django.urls import reverse

import pytest
from dramatiq.results import ResultFailure, ResultMissing
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories
from core.api.utils import generate_file_key, validate_file_key
from core.task_utils import register_task_owner

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    """Create a test user."""
    return factories.UserFactory()


@pytest.fixture
def mailbox(user):
    """A mailbox the test user administers: the upload endpoints are nested
    under it and gated by ``IsMailboxAdmin`` (they exist only to feed
    ``POST .../imports/``)."""
    return factories.MailboxAccessFactory(
        user=user, role=enums.MailboxRoleChoices.ADMIN
    ).mailbox


@pytest.fixture
def api_client(user):
    """Create an authenticated API client."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _upload_list_url(mailbox):
    return reverse("mailbox-imports-upload-list", kwargs={"mailbox_id": mailbox.id})


def _upload_part_url(mailbox, upload_id):
    return reverse(
        "mailbox-imports-upload-create-part-upload",
        kwargs={"mailbox_id": mailbox.id, "upload_id": upload_id},
    )


def _upload_detail_url(mailbox, upload_id):
    return reverse(
        "mailbox-imports-upload-detail",
        kwargs={"mailbox_id": mailbox.id, "upload_id": upload_id},
    )


class TestTaskDetailViewPermissions:
    """Test that TaskDetailView enforces ownership checks."""

    @staticmethod
    def _register(task_id, user_id):
        """Track a task as owned by ``user_id``, as a dispatch would."""
        register_task_owner(
            task_id, user_id, actor_name="some_task", queue_name="default"
        )

    @staticmethod
    def _stub_result(**kwargs):
        """Patch the result lookup: ``return_value=`` or ``side_effect=``."""
        patcher = mock.patch("core.api.viewsets.task.dramatiq.Message")
        message_cls = patcher.start()
        for key, value in kwargs.items():
            setattr(message_cls.return_value.get_result, key, value)
        return patcher

    def test_api_task_detail_unknown_task_should_be_forbidden(self):
        """Test that accessing an unknown task (not in cache) returns 403."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        # Try to access a task that was never registered
        url = reverse("task-detail", kwargs={"task_id": "unknown-task-id"})
        response = client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "not found or access expired" in response.data["detail"].lower()

    def test_api_task_detail_other_user_should_be_forbidden(self):
        """Test that a user cannot access another user's task, but the owner can."""

        user1 = factories.UserFactory()
        user2 = factories.UserFactory()

        task_id = "test-task-id-12345"
        self._register(task_id, user1.id)
        url = reverse("task-detail", kwargs={"task_id": task_id})

        patcher = self._stub_result(
            return_value={
                "status": "SUCCESS",
                "result": {"imported": 42, "mailbox_id": "sensitive-data"},
                "error": None,
            }
        )
        try:
            # User2 tries to access user1's task - should be denied
            client2 = APIClient()
            client2.force_authenticate(user=user2)
            response = client2.get(url)
            assert response.status_code == status.HTTP_403_FORBIDDEN

            # User1 (owner) accesses their own task - should succeed
            client1 = APIClient()
            client1.force_authenticate(user=user1)
            response = client1.get(url)
            assert response.status_code == status.HTTP_200_OK
            assert response.data["result"]["imported"] == 42
        finally:
            patcher.stop()

    def test_api_task_detail_pending_when_no_result_yet(self):
        """A dispatched task with no stored result and no progress is PENDING."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        task_id = "test-task-pending"
        self._register(task_id, user.id)
        url = reverse("task-detail", kwargs={"task_id": task_id})

        patcher = self._stub_result(side_effect=ResultMissing("nothing yet"))
        try:
            response = client.get(url)
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "PENDING"
        assert response.data["result"] is None

    def test_api_task_detail_progress_is_reported(self):
        """Progress published by a running task is surfaced to its owner."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        task_id = "test-task-progress"
        self._register(task_id, user.id)
        cache.set(
            f"task_progress:{task_id}",
            {"progress": 42, "timestamp": 1.0, "metadata": {"message": "Halfway"}},
        )
        url = reverse("task-detail", kwargs={"task_id": task_id})

        patcher = self._stub_result(side_effect=ResultMissing("nothing yet"))
        try:
            response = client.get(url)
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "PROGRESS"
        assert response.data["progress"] == 42
        assert response.data["message"] == "Halfway"

    def test_api_task_detail_failed_task_does_not_leak_the_exception(self):
        """A task that raised reports FAILURE without echoing its message.

        Exception text can carry internal hostnames, credentials in URLs or
        fragments of the payload, so the endpoint reports the failure but not
        its detail.
        """
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        task_id = "test-task-crashed-worker"
        self._register(task_id, user.id)
        url = reverse("task-detail", kwargs={"task_id": task_id})

        patcher = self._stub_result(
            side_effect=ResultFailure(
                "boom", "ConnectionResetError", "postgres://user:secret@db/prod"
            )
        )
        try:
            response = client.get(url)
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "FAILURE"
        assert response.data["result"] is None
        assert response.data["error"] == "Task failed"
        assert "secret" not in str(response.data)

    def test_api_task_detail_result_backend_unavailable(self):
        """A broken result backend is a 503, not a 500."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        task_id = "test-task-backend-down"
        self._register(task_id, user.id)
        url = reverse("task-detail", kwargs={"task_id": task_id})

        patcher = self._stub_result(side_effect=ConnectionError("redis is down"))
        try:
            response = client.get(url)
        finally:
            patcher.stop()

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.data["status"] == "FAILURE"


class TestImportViewSetPermissions:
    """Test that ImportViewSet enforces proper role checks."""

    def test_api_import_file_viewer_should_be_forbidden(self, user):
        """A VIEWER cannot import into a mailbox; an ADMIN can. The endpoint is
        gated by ``IsMailboxAdmin`` (the mailbox id comes from the URL)."""
        client = APIClient()
        client.force_authenticate(user=user)
        mailbox = factories.MailboxFactory()
        # Give user only VIEWER access
        access = factories.MailboxAccessFactory(
            user=user, mailbox=mailbox, role=enums.MailboxRoleChoices.VIEWER
        )

        url = reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id})
        data = {
            "source": "file",
            "filename": "test.eml",
            "file_key": generate_file_key(user.id),
        }

        with (
            mock.patch("core.services.importer.service.storages") as mock_storages,
            mock.patch("core.services.importer.service.run_import_task"),
        ):
            mock_storage = mock.MagicMock()
            mock_storage.exists.return_value = True
            mock_storage.connection.meta.client.head_object.return_value = {
                "ContentLength": 100
            }
            # Mock get_object to return EML-like content for magic detection
            eml_body = mock.MagicMock()
            eml_body.read.return_value = (
                b"From: test@example.com\r\nSubject: Test\r\n\r\n"
            )
            mock_storage.connection.meta.client.get_object.return_value = {
                "Body": eml_body,
            }
            mock_storages.__getitem__.return_value = mock_storage

            # A VIEWER should not be allowed to import - expect 403
            response = client.post(url, data, format="json")
            assert response.status_code == status.HTTP_403_FORBIDDEN

            # Elevate to ADMIN and verify import is accepted
            access.role = enums.MailboxRoleChoices.ADMIN
            access.save()
            response = client.post(url, data, format="json")
            assert response.status_code == status.HTTP_202_ACCEPTED

    def test_api_messages_archive_create_upload_requires_mailbox_admin(self, mailbox):
        """A user without admin rights on the URL mailbox cannot mint presigned
        writes into the imports bucket — the upload endpoint is nested under the
        mailbox and gated by the same ``IsMailboxAdmin`` as ``POST .../imports/``."""
        client = APIClient()
        client.force_authenticate(user=factories.UserFactory())
        response = client.post(
            _upload_list_url(mailbox),
            {"filename": "test.eml", "content_type": "message/rfc822"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestMessagesArchiveUploadViewSet:
    """Test the create action for direct and multipart uploads."""

    def test_api_messages_archive_create_direct_upload(self, api_client, user, mailbox):
        """
        Test creating a direct upload should return a signed URL to upload
        the file directly to the message imports bucket.
        """
        url = _upload_list_url(mailbox)
        data = {"filename": "test.eml", "content_type": "message/rfc822"}

        with mock.patch(
            "core.api.viewsets.imports.generate_presigned_url",
            return_value="https://s3.example.com/presigned-url?signature=abc123",
        ) as mock_generate_presigned_url:
            response = api_client.post(url, data, format="json")

            assert response.status_code == status.HTTP_201_CREATED
            assert response.data["filename"] == "test.eml"
            assert (
                response.data["url"]
                == "https://s3.example.com/presigned-url?signature=abc123"
            )

            # Verify generate_presigned_url was called correctly
            mock_generate_presigned_url.assert_called_once()
            call_args = mock_generate_presigned_url.call_args
            assert call_args[1]["ClientMethod"] == "put_object"
            assert call_args[1]["Params"]["Key"] == response.data["file_key"]
            assert validate_file_key(user.id, response.data["file_key"])

    def test_api_messages_archive_upload_keys_are_unique_per_upload(
        self, api_client, user, mailbox
    ):
        """EVERY upload gets its own key — same user, same filename, twice:
        nothing in the bucket is ever overwritten (a re-upload during a
        resumable import must not swap the bytes under the running import)."""
        keys = []
        for _ in range(2):
            with mock.patch(
                "core.api.viewsets.imports.generate_presigned_url"
            ) as mock_presign:
                mock_presign.return_value = "https://example.com/presigned"
                response = api_client.post(
                    _upload_list_url(mailbox),
                    {"filename": "same-name.mbox", "content_type": "application/mbox"},
                    format="json",
                )
            assert response.status_code == status.HTTP_201_CREATED
            assert (
                mock_presign.call_args.kwargs["Params"]["Key"]
                == response.data["file_key"]
            )
            keys.append(response.data["file_key"])
        assert keys[0] != keys[1]
        assert all(validate_file_key(user.id, key) for key in keys)

    def test_api_messages_archive_part_upload_rejects_foreign_key(
        self, api_client, mailbox
    ):
        """A key minted for another user (or hand-crafted) is refused."""
        other = factories.UserFactory()
        foreign_key = generate_file_key(other.id)
        response = api_client.post(
            _upload_part_url(mailbox, "up-1"),
            {"file_key": foreign_key, "part_number": 1},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file_key" in response.data

    def test_api_messages_archive_create_multipart_upload(self, api_client, mailbox):
        """Test creating a multipart upload (returns upload_id)."""
        url = _upload_list_url(mailbox) + "?multipart"
        data = {"filename": "large-file.mbox", "content_type": "application/mbox"}

        with mock.patch(
            "core.api.viewsets.imports.MessagesArchiveUploadViewSet.storage.connection.meta.client.create_multipart_upload",  # pylint: disable=line-too-long
            return_value={"UploadId": "test-upload-id-12345"},
        ) as mock_create_multipart_upload:
            response = api_client.post(url, data, format="json")

        mock_create_multipart_upload.assert_called_once()
        assert response.status_code == status.HTTP_201_CREATED
        assert "filename" in response.data
        assert "upload_id" in response.data
        assert "url" not in response.data
        assert response.data["filename"] == "large-file.mbox"
        assert response.data["upload_id"] == "test-upload-id-12345"

    def test_api_messages_archive_create_upload_missing_content_type(
        self, api_client, mailbox
    ):
        """Test creating upload without content type."""
        url = _upload_list_url(mailbox)
        data = {"filename": "test.eml"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["content_type"] == ["This field is required."]

    def test_api_messages_archive_create_upload_invalid_content_type(
        self, api_client, mailbox
    ):
        """Test creating upload with invalid content type."""
        url = _upload_list_url(mailbox)
        data = {
            "filename": "test.txt",
            "content_type": "text/html",  # Not in ARCHIVE_SUPPORTED_MIME_TYPES
        }

        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["content_type"] == [
            "Only EML, MBOX, and PST files are supported."
        ]

    def test_api_messages_archive_create_upload_missing_filename(
        self, api_client, mailbox
    ):
        """Test creating upload without filename."""
        url = _upload_list_url(mailbox)
        data = {"content_type": "message/rfc822"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["filename"] == ["This field is required."]

    def test_api_messages_archive_create_upload_unauthenticated(self, mailbox):
        """Test creating upload without authentication."""
        client = APIClient()
        url = _upload_list_url(mailbox)
        data = {"filename": "test.eml", "content_type": "message/rfc822"}

        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("mime_type", enums.ARCHIVE_SUPPORTED_MIME_TYPES)
    def test_api_messages_archive_create_upload_all_supported_mime_types(
        self, api_client, mime_type, mailbox
    ):
        """Test creating upload with all supported MIME types."""
        url = _upload_list_url(mailbox)
        data = {"filename": "test-file", "content_type": mime_type}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["filename"] == "test-file"
        assert "url" in response.data  # a usable presigned PUT came back

    def test_api_messages_archive_create_part_upload(self, api_client, user, mailbox):
        """Test creating a presigned URL for a part upload."""
        upload_id = "test-upload-id-12345"
        url = _upload_part_url(mailbox, upload_id)
        file_key = generate_file_key(user.id)
        data = {"file_key": file_key, "part_number": 1}

        with mock.patch(
            "core.api.viewsets.imports.generate_presigned_url",
            return_value="https://s3.example.com/presigned-url?signature=abc123&part_number=1",
        ) as mock_generate_presigned_url:
            response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["file_key"] == file_key
        assert response.data["part_number"] == 1
        assert response.data["upload_id"] == upload_id
        assert (
            response.data["url"]
            == "https://s3.example.com/presigned-url?signature=abc123&part_number=1"
        )

        # Verify generate_presigned_url was called correctly
        mock_generate_presigned_url.assert_called_once()
        call_args = mock_generate_presigned_url.call_args
        assert call_args[1]["ClientMethod"] == "upload_part"
        assert call_args[1]["Params"]["Key"] == file_key
        assert call_args[1]["Params"]["UploadId"] == upload_id
        assert call_args[1]["Params"]["PartNumber"] == 1

    def test_api_messages_archive_create_part_upload_multiple_parts(
        self, api_client, user, mailbox
    ):
        """Test creating presigned URLs for multiple parts."""
        file_key = generate_file_key(user.id)
        upload_id = "test-upload-id-12345"
        url = _upload_part_url(mailbox, upload_id)

        for part_number in [1, 2, 3]:
            data = {"file_key": file_key, "part_number": part_number}

            with mock.patch(
                "core.api.viewsets.imports.generate_presigned_url",
                return_value=f"https://s3.example.com/presigned-url?signature=abc123&part_number={part_number}",
            ):
                response = api_client.post(url, data, format="json")

            assert response.status_code == status.HTTP_201_CREATED
            assert response.data["part_number"] == part_number
            assert (
                response.data["url"]
                == f"https://s3.example.com/presigned-url?signature=abc123&part_number={part_number}"
            )

    def test_api_messages_archive_create_part_upload_missing_file_key(
        self, api_client, mailbox
    ):
        """Test creating part upload without a file_key."""
        upload_id = "test-upload-id-12345"
        url = _upload_part_url(mailbox, upload_id)
        data = {"part_number": 1}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file_key"] == ["This field is required."]

    def test_api_messages_archive_create_part_upload_unauthenticated(self, mailbox):
        """Test creating part upload without authentication."""
        client = APIClient()
        upload_id = "test-upload-id-12345"
        url = _upload_part_url(mailbox, upload_id)
        data = {
            "file_key": generate_file_key(factories.UserFactory().id),
            "part_number": 1,
        }

        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_messages_archive_complete_multipart_upload(
        self, api_client, user, mailbox
    ):
        """Test completing a multipart upload."""
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        file_key = generate_file_key(user.id)
        data = {
            "file_key": file_key,
            "parts": [
                {"ETag": "etag1", "PartNumber": 1},
                {"ETag": "etag2", "PartNumber": 2},
                {"ETag": "etag3", "PartNumber": 3},
            ],
        }

        with mock.patch(
            "core.api.viewsets.imports.MessagesArchiveUploadViewSet.storage.connection.meta.client.complete_multipart_upload",  # pylint: disable=line-too-long
            return_value=None,
        ) as mock_complete_multipart_upload:
            response = api_client.put(url, data, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify complete_multipart_upload was called correctly
        mock_complete_multipart_upload.assert_called_once()
        call_args = mock_complete_multipart_upload.call_args
        assert call_args[1]["Key"] == file_key
        assert call_args[1]["UploadId"] == upload_id
        assert call_args[1]["MultipartUpload"]["Parts"] == [
            {"ETag": "etag1", "PartNumber": 1},
            {"ETag": "etag2", "PartNumber": 2},
            {"ETag": "etag3", "PartNumber": 3},
        ]

    def test_api_messages_archive_complete_multipart_upload_missing_filename(
        self, api_client, mailbox
    ):
        """Test completing upload without a file_key."""
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        data = {"parts": [{"ETag": "etag1", "PartNumber": 1}]}

        response = api_client.put(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file_key"] == ["This field is required."]

    def test_api_messages_archive_complete_multipart_upload_missing_parts(
        self, api_client, mailbox
    ):
        """Test completing upload without parts."""
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        data = {"file_key": generate_file_key(factories.UserFactory().id)}

        response = api_client.put(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["parts"] == ["This field is required."]

    def test_api_messages_archive_complete_multipart_upload_unauthenticated(
        self, mailbox
    ):
        """Test completing upload without authentication."""
        client = APIClient()
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        data = {
            "file_key": generate_file_key(factories.UserFactory().id),
            "parts": [{"ETag": "etag1", "PartNumber": 1}],
        }

        response = client.put(url, data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_api_messages_archive_abort_multipart_upload(
        self, api_client, user, mailbox
    ):
        """Test aborting a multipart upload."""
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        file_key = generate_file_key(user.id)
        data = {"file_key": file_key}

        with mock.patch(
            "core.api.viewsets.imports.MessagesArchiveUploadViewSet.storage.connection.meta.client.abort_multipart_upload",  # pylint: disable=line-too-long
            return_value=None,
        ) as mock_abort_multipart_upload:
            response = api_client.delete(url, data, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify abort_multipart_upload was called correctly
        mock_abort_multipart_upload.assert_called_once()
        call_args = mock_abort_multipart_upload.call_args
        assert call_args[1]["Key"] == file_key
        assert call_args[1]["UploadId"] == upload_id

    def test_api_messages_archive_abort_is_idempotent(self, api_client, user, mailbox):
        """Aborting an already-aborted/completed upload is a 204 no-op (the
        client's unmount cleanup can race its explicit abort), not a 500."""
        from botocore.exceptions import (  # pylint: disable=import-outside-toplevel
            ClientError,
        )

        url = _upload_detail_url(mailbox, "up-1")
        data = {"file_key": generate_file_key(user.id)}
        with mock.patch(
            "core.api.viewsets.imports.MessagesArchiveUploadViewSet.storage.connection.meta.client.abort_multipart_upload",  # pylint: disable=line-too-long
            side_effect=ClientError(
                {"Error": {"Code": "NoSuchUpload"}}, "AbortMultipartUpload"
            ),
        ):
            response = api_client.delete(url, data, format="json")
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_api_messages_archive_abort_multipart_upload_missing_filename(
        self, api_client, mailbox
    ):
        """Test aborting upload without a file_key."""
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        data = {}

        response = api_client.delete(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file_key"] == ["This field is required."]

    def test_api_messages_archive_abort_multipart_upload_unauthenticated(self, mailbox):
        """Test aborting upload without authentication."""
        client = APIClient()
        upload_id = "test-upload-id-12345"
        url = _upload_detail_url(mailbox, upload_id)
        data = {"file_key": generate_file_key(factories.UserFactory().id)}

        response = client.delete(url, data, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
