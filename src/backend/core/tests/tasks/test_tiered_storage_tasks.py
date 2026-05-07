"""Tests for tiered storage Celery tasks.

These tests use real object storage when available.
Only minimal mocking for disabled state and error simulation.

The periodic ``offload_blobs_task`` does the work itself — it walks
the eligible queryset and processes blobs sequentially via
``offload_one_blob``. There is no per-blob celery fan-out, so tests
here drive the loop directly and assert on the resulting DB / storage
state, not on what got queued.
"""

# pylint: disable=no-value-for-parameter,unused-argument

import secrets
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils.timezone import now

import pytest

from core import factories
from core.enums import BlobStorageLocationChoices
from core.models import Blob
from core.services.tiered_storage import TieredStorageService
from core.services.tiered_storage_tasks import (
    offload_blobs_task,
    offload_one_blob,
)

# Generate encryption keys at module level for decorators (full config entry).
_TEST_ENCRYPTION_KEY = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}


@pytest.mark.django_db
class TestOffloadBlobsTaskDisabled:
    """Tests for offload_blobs_task when storage is disabled."""

    @override_settings(MESSAGES_BLOBS_OFFLOAD_ENABLED=False)
    def test_task_disabled_by_setting(self):
        """Setting ``MESSAGES_BLOBS_OFFLOAD_ENABLED=False`` short-circuits the task."""
        result = offload_blobs_task()
        assert result["status"] == "disabled"
        assert result["processed"] == 0

    def test_task_disabled_when_no_storage(self):
        """No configured object storage = the task short-circuits as disabled."""
        with patch("core.services.tiered_storage.settings") as mock_settings:
            mock_settings.STORAGES = {}
            mock_settings.MESSAGES_BLOBS_ENCRYPT_KEYS = {}

            result = offload_blobs_task()

            assert result["status"] == "disabled"
            assert result["processed"] == 0


@pytest.mark.django_db(transaction=True)
class TestOffloadBlobsTaskE2E:
    """E2E tests for offload_blobs_task — processes blobs in-task, no fan-out."""

    def _age(self, blob):
        Blob.objects.filter(id=blob.id).update(
            created_at=now()
            - timedelta(seconds=settings.MESSAGES_BLOBS_OFFLOAD_DELAY + 1)
        )

    def test_processes_eligible_blobs_by_age(self):
        """Old blobs get offloaded; recent ones stay POSTGRES."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()

        old_blob = factories.BlobFactory(
            mailbox=mailbox, content=b"old content " * 5, content_type="text/plain"
        )
        self._age(old_blob)
        new_blob = factories.BlobFactory(
            mailbox=mailbox, content=b"new content " * 5, content_type="text/plain"
        )

        old_key = TieredStorageService.compute_storage_key_for_blob(old_blob)
        try:
            result = offload_blobs_task()
            assert result["status"] == "success"
            assert result["success"] == 1

            old_blob.refresh_from_db()
            new_blob.refresh_from_db()
            assert (
                old_blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            )
            assert new_blob.storage_location == BlobStorageLocationChoices.POSTGRES
        finally:
            if service.storage.exists(old_key):
                service.storage.delete(old_key)

    def test_processes_eligible_blobs_by_size(self):
        """OFFLOAD_MIN_SIZE filters out blobs smaller than the threshold."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()

        # With MIN_SIZE > 0 the small blob is ineligible regardless of age.
        with override_settings(MESSAGES_BLOBS_OFFLOAD_MIN_SIZE=1000):
            small_blob = factories.BlobFactory(
                mailbox=mailbox, content=b"small", content_type="text/plain"
            )
            self._age(small_blob)
            large_blob = factories.BlobFactory(
                mailbox=mailbox, content=b"x" * 2000, content_type="text/plain"
            )
            self._age(large_blob)

            large_key = TieredStorageService.compute_storage_key_for_blob(large_blob)
            try:
                result = offload_blobs_task()
                assert result["status"] == "success"
                assert result["success"] == 1

                small_blob.refresh_from_db()
                large_blob.refresh_from_db()
                assert (
                    small_blob.storage_location == BlobStorageLocationChoices.POSTGRES
                )
                assert (
                    large_blob.storage_location
                    == BlobStorageLocationChoices.OBJECT_STORAGE
                )
            finally:
                if service.storage.exists(large_key):
                    service.storage.delete(large_key)

    @override_settings(MESSAGES_BLOBS_OFFLOAD_DELAY=0)
    def test_immediate_offload_with_zero_delay(self):
        """OFFLOAD_DELAY=0 — fresh blobs are eligible immediately."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"immediate offload test content" * 20
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            result = offload_blobs_task()
            assert result["status"] == "success"
            assert result["success"] >= 1

            blob.refresh_from_db()
            assert blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            assert blob.raw_content is None
            assert blob.get_content() == content
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_skips_already_offloaded_blobs(self):
        """A blob already at OBJECT_STORAGE is excluded from the queryset."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()
            self._age(blob)

            result = offload_blobs_task()
            # The already-offloaded row is filtered out by the queryset, so
            # nothing eligible remains.
            assert result["success"] == 0
            assert result["processed"] == 0
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    @override_settings(MESSAGES_BLOBS_OFFLOAD_DELAY=0)
    def test_deadline_stops_loop(self):
        """The 55-minute wall-clock cap stops the loop. We patch the
        module constant to 0 so the deadline check fires before any
        blob is processed and we don't actually have to wait 55 min."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"never offloaded", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            with patch("core.services.tiered_storage_tasks._MAX_RUN_SECONDS", 0):
                result = offload_blobs_task()
            assert result["stop_reason"] == "deadline"
            assert result["processed"] == 0
            blob.refresh_from_db()
            assert blob.storage_location == BlobStorageLocationChoices.POSTGRES
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_per_blob_failure_does_not_stop_loop(self):
        """A failure on one blob is logged + counted, the loop keeps going."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()

        # Two eligible blobs; the first one's upload will fail, the second succeeds.
        bad = factories.BlobFactory(
            mailbox=mailbox, content=b"will fail" * 20, content_type="text/plain"
        )
        good = factories.BlobFactory(
            mailbox=mailbox, content=b"will work" * 20, content_type="text/plain"
        )
        self._age(bad)
        self._age(good)

        good_key = TieredStorageService.compute_storage_key_for_blob(good)
        bad_key = TieredStorageService.compute_storage_key_for_blob(bad)

        original_save = service.storage.save

        def selective_save(name, content, *args, **kwargs):
            if name == bad_key:
                raise RuntimeError("simulated upload failure")
            return original_save(name, content, *args, **kwargs)

        try:
            with patch.object(service.storage, "save", side_effect=selective_save):
                with patch(
                    "core.services.tiered_storage_tasks.TieredStorageService",
                    return_value=service,
                ):
                    result = offload_blobs_task()

            assert result["failed"] == 1
            assert result["success"] == 1

            bad.refresh_from_db()
            good.refresh_from_db()
            # Failed blob remains POSTGRES (transaction rolled back).
            assert bad.storage_location == BlobStorageLocationChoices.POSTGRES
            assert bad.raw_content is not None
            assert good.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
        finally:
            for k in (good_key, bad_key):
                if service.storage.exists(k):
                    service.storage.delete(k)


@pytest.mark.django_db
class TestOffloadOneBlob:
    """Direct unit tests for the per-blob helper."""

    def test_disabled_when_no_storage(self):
        """A service with ``enabled=False`` returns immediately without DB access."""
        service = TieredStorageService()
        # ``enabled`` is a per-instance attribute set in __init__ from
        # the resolved ``STORAGES["message-blobs"]`` options.
        service.enabled = False
        result = offload_one_blob("any-id", service)
        assert result["status"] == "disabled"

    def test_not_found(self):
        """A non-existent blob_id returns ``not_found`` rather than raising."""
        service = TieredStorageService()
        result = offload_one_blob("00000000-0000-0000-0000-000000000000", service)
        assert result["status"] == "not_found"

    def test_already_offloaded(self):
        """A blob already at ``OBJECT_STORAGE`` is reported as ``already_offloaded``."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            result = offload_one_blob(str(blob.id), service)
            assert result["status"] == "already_offloaded"
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_successful_offload(self):
        """Happy path: a POSTGRES blob is moved to OBJECT_STORAGE and round-trips."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        original_content = b"test content for offload" * 20
        blob = factories.BlobFactory(
            mailbox=mailbox, content=original_content, content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            result = offload_one_blob(str(blob.id), service)
            assert result["status"] == "success"
            assert result["blob_id"] == str(blob.id)

            blob.refresh_from_db()
            assert blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            assert blob.raw_content is None
            assert blob.get_content() == original_content
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_no_content(self):
        """A POSTGRES blob with NULL ``raw_content`` is reported as ``no_content``."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )
        Blob.objects.filter(id=blob.id).update(raw_content=None)

        result = offload_one_blob(str(blob.id), service)
        assert result["status"] == "no_content"

    def test_handles_upload_error(self):
        """Per-blob failure is captured and returned, not raised."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )

        with patch.object(
            service.storage, "save", side_effect=Exception("Upload failed")
        ):
            result = offload_one_blob(str(blob.id), service)

        assert result["status"] == "error"
        assert "Upload failed" in result["error"]

        blob.refresh_from_db()
        assert blob.storage_location == BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None

    # ``test_deduplication_during_offload`` removed: with DB-level
    # dedup at create time, two mailboxes uploading the same content
    # land in the same Blob row, so there's no second offload to
    # exercise S3-side dedup.  Cross-mailbox sharing is pinned by
    # ``test_tiered_storage.py::TestBlobDedup::test_email_to_multiple_mailboxes_shares_one_blob``;
    # the "delete blob → S3 cleanup" path is exercised end-to-end by
    # ``TestBlobGarbageCollection.test_gc_deletes_orphan_blob_object_storage_and_cleans_s3``.

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_offload_with_encryption(self):
        """Encrypted blobs round-trip through offload (ciphertext stays untouched)."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        original_content = b"encrypted content for offload test" * 20
        blob = factories.BlobFactory(
            mailbox=mailbox, content=original_content, content_type="text/plain"
        )
        assert blob.encryption_key_id > 0
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            result = offload_one_blob(str(blob.id), service)
            assert result["status"] == "success"

            blob.refresh_from_db()
            assert blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            assert blob.encryption_key_id > 0
            assert blob.get_content() == original_content
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_repeat_call_is_idempotent(self):
        """Calling twice on the same blob: first succeeds, second returns already_offloaded."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test content", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            r1 = offload_one_blob(str(blob.id), service)
            assert r1["status"] == "success"
            r2 = offload_one_blob(str(blob.id), service)
            assert r2["status"] == "already_offloaded"
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)
