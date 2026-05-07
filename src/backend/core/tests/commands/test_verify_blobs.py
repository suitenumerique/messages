"""Tests for the verify_blobs management command.

These tests use real object storage when available.
Only minimal mocking is used for testing disabled/error states.
"""

# pylint: disable=unused-argument,import-outside-toplevel

import secrets
from io import StringIO

from django.core.management import call_command
from django.test import override_settings

import pytest

from core import factories
from core.enums import BlobStorageLocationChoices
from core.services.tiered_storage import TieredStorageService

# Generate encryption key at module level for decorators (full config entry).
_TEST_ENCRYPTION_KEY = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}


@pytest.mark.django_db
class TestVerifyTieredStorageDisabled:
    """Tests for when storage is not configured."""

    def test_command_disabled_when_no_storage(self):
        """Test that command reports disabled when storage not configured."""
        from unittest.mock import patch

        stdout = StringIO()
        stderr = StringIO()

        with patch("core.services.tiered_storage.settings") as mock_settings:
            mock_settings.STORAGES = {}
            mock_settings.MESSAGES_BLOBS_ENCRYPT_KEYS = {}

            call_command("verify_blobs", stdout=stdout, stderr=stderr)

        assert "not configured" in stderr.getvalue()


@pytest.mark.django_db
class TestVerifyDbToStorageE2E:
    """E2E tests for db-to-storage verification mode."""

    def test_all_blobs_present(self):
        """Test db-to-storage when all blobs exist in storage."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(mailbox=mailbox)
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            # Upload blob to storage
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.save()

            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="db-to-storage",
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert "All blobs have storage backing" in output
            assert "MISSING" not in stderr.getvalue()
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_missing_blob_detected(self):
        """Test db-to-storage when a blob is missing from storage."""
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(mailbox=mailbox)

        # Mark as in object storage but don't actually upload
        blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob.save()

        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "verify_blobs",
            mode="db-to-storage",
            stdout=stdout,
            stderr=stderr,
        )

        assert "MISSING" in stderr.getvalue()
        assert str(blob.id) in stderr.getvalue()
        assert "1 blobs missing" in stdout.getvalue()

    def test_db_to_storage_detects_externally_deleted_object(self):
        """Drift between DB and S3 (an OBJECT_STORAGE row whose path is
        gone from the bucket) is the failure mode that ``upload_blob``
        no longer guards against — so this audit IS the line of
        defense. Confirm it surfaces as ``MISSING``."""
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
            assert service.storage.exists(storage_key)

            # External deletion (operator mishap, lifecycle expiry, etc.).
            service.storage.delete(storage_key)
            assert not service.storage.exists(storage_key)

            stdout = StringIO()
            stderr = StringIO()
            call_command(
                "verify_blobs",
                mode="db-to-storage",
                stdout=stdout,
                stderr=stderr,
            )
            assert "MISSING" in stderr.getvalue()
            assert str(blob.id) in stderr.getvalue()
            assert "1 blobs missing" in stdout.getvalue()
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_limit_option(self):
        """Test that --limit restricts number of items checked."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blobs = []
        storage_keys = []

        # Create 5 blobs in object storage
        for i in range(5):
            blob = factories.BlobFactory(
                mailbox=mailbox,
                content=f"test content {i}".encode(),
                content_type="text/plain",
            )
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.save()
            blobs.append(blob)
            storage_keys.append(TieredStorageService.compute_storage_key_for_blob(blob))

        try:
            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="db-to-storage",
                limit=2,
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert "Checked: 2 blobs" in output
        finally:
            for key in storage_keys:
                if service.storage.exists(key):
                    service.storage.delete(key)


@pytest.mark.django_db
class TestVerifyStorageToDbE2E:
    """E2E tests for storage-to-db verification mode."""

    def test_no_orphans(self):
        """Test storage-to-db when no orphans exist."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(mailbox=mailbox)
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.save()

            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="storage-to-db",
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert "Storage to DB Verification" in output
            # Should not find our blob as an orphan
            assert "ORPHAN" not in output or storage_key not in output
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_orphan_detected(self):
        """Test storage-to-db when an orphan exists.

        The bucket is shared across the test session (and other test
        classes that defer their S3 cleanup to ``on_commit`` won't
        fire those hooks under ``@pytest.mark.django_db``), so we
        scope the assertion to *our* orphan key rather than the
        global orphan count.
        """
        # pylint: disable-next=import-outside-toplevel
        from django.core.files.base import ContentFile

        service = TieredStorageService()
        # Create an orphan object in storage (no DB record)
        orphan_sha = "a" * 64
        orphan_key = f"blobs/0/{orphan_sha[:3]}/{orphan_sha}"
        service.storage.save(orphan_key, ContentFile(b"orphan content"))

        try:
            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="storage-to-db",
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert f"ORPHAN: {orphan_key}" in output
        finally:
            if service.storage.exists(orphan_key):
                service.storage.delete(orphan_key)

    def test_verify_hashes(self):
        """Test --verify-hashes downloads and verifies blob content."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Content for hash verification test" * 10
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="storage-to-db",
                verify_hashes=True,
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert "Hash mismatches: 0" in output
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)


@pytest.mark.django_db
class TestVerifyHashesE2E:
    """E2E tests for --verify-hashes with edge cases."""

    def test_verify_hashes_detects_corruption(self):
        """Test that --verify-hashes detects corrupted content."""
        from django.core.files.base import ContentFile

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Content that will be corrupted" * 10
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            # Upload blob normally
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            # Corrupt the storage object
            service.storage.delete(storage_key)
            service.storage.save(storage_key, ContentFile(b"corrupted garbage data"))

            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="storage-to-db",
                verify_hashes=True,
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            # Should detect hash mismatch or decryption error
            assert "HASH MISMATCH" in output or "VERIFY ERROR" in output
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_verify_hashes_with_encryption(self):
        """Test --verify-hashes works correctly with encrypted blobs."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Encrypted content for hash verification" * 10

        # create_blob() should automatically encrypt when keys are configured
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        assert blob.encryption_key_id > 0  # Should be encrypted
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            stdout = StringIO()
            stderr = StringIO()

            call_command(
                "verify_blobs",
                mode="storage-to-db",
                verify_hashes=True,
                stdout=stdout,
                stderr=stderr,
            )

            output = stdout.getvalue()
            assert "Hash mismatches: 0" in output
        finally:
            if service.storage and service.storage.exists(storage_key):
                service.storage.delete(storage_key)
