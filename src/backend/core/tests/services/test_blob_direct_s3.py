"""Tests for direct-to-object-storage blob writes (import axis 3).

These exercise ``TieredStorageService.put_ciphertext`` and
``BlobManager.create_blob_on_s3`` against real object storage (same
contract as the E2E tests in ``test_tiered_storage.py``): the bytes go to
S3 immediately and the row is inserted with
``storage_location=OBJECT_STORAGE`` / ``raw_content=None``.
"""

# pylint: disable=redefined-outer-name,import-outside-toplevel

import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.test import override_settings

import pytest

from core import factories, models
from core.enums import BlobStorageLocationChoices, CompressionTypeChoices
from core.services.tiered_storage import TieredStorageService

_TEST_ENCRYPTION_KEY = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}


@pytest.mark.django_db
class TestPutCiphertext:
    """Content-level S3 upload shared by ``upload_blob`` and the import path."""

    def test_uploads_and_returns_metadata(self):
        """Writes the bytes at the (sha, key_id) path and echoes back the
        (key_id, compression) the caller must persist."""
        service = TieredStorageService()
        content = b"direct-to-s3 ciphertext payload" * 5
        sha256 = hashlib.sha256(content).digest()
        key = TieredStorageService.compute_storage_key(sha256, 0)
        try:
            key_id, compression = service.put_ciphertext(
                sha256, content, 0, CompressionTypeChoices.NONE
            )
            assert key_id == 0
            assert compression == CompressionTypeChoices.NONE
            assert service.storage.exists(key)
            with service.storage.open(key, "rb") as f:
                assert f.read() == content
        finally:
            if service.storage.exists(key):
                service.storage.delete(key)

    def test_dedups_against_existing_sibling(self):
        """A sha with an OBJECT_STORAGE sibling adopts its (key_id,
        compression) and skips the upload entirely."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"sibling content" * 10, content_type="text/plain"
        )
        blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob.encryption_key_id = 5
        blob.compression = CompressionTypeChoices.ZSTD
        blob.save()

        sha256 = bytes(blob.sha256)
        # Args intentionally differ from the sibling's — the sibling wins.
        key_id, compression = service.put_ciphertext(
            sha256, b"ignored", 0, CompressionTypeChoices.NONE
        )
        assert key_id == 5
        assert compression == CompressionTypeChoices.ZSTD
        # Nothing was written at the would-be key_id=0 path.
        assert not service.storage.exists(
            TieredStorageService.compute_storage_key(sha256, 0)
        )


@pytest.mark.django_db
class TestCreateBlobOnS3:
    """``BlobManager.create_blob_on_s3``: dedup + compress + encrypt, then
    write straight to object storage."""

    def test_writes_to_object_storage(self):
        """The row lands as OBJECT_STORAGE with no Postgres payload, and the
        unchanged read path still returns the original content."""
        content = b"imported message body" * 50
        blob = models.Blob.objects.create_blob_on_s3(
            content=content, content_type="message/rfc822"
        )
        service = TieredStorageService()
        key = TieredStorageService.compute_storage_key_for_blob(blob)
        try:
            assert blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            assert blob.raw_content is None
            assert blob.size == len(content)
            assert blob.size_compressed > 0
            assert service.storage.exists(key)
            assert blob.get_content() == content
        finally:
            if service.storage.exists(key):
                service.storage.delete(key)

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_writes_encrypted_to_object_storage(self):
        """Encryption is applied before the S3 write and the content still
        round-trips through ``get_content``."""
        content = b"secret imported body" * 50
        blob = models.Blob.objects.create_blob_on_s3(
            content=content, content_type="message/rfc822"
        )
        service = TieredStorageService()
        key = TieredStorageService.compute_storage_key_for_blob(blob)
        try:
            assert blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            assert blob.encryption_key_id == 1
            assert blob.raw_content is None
            assert blob.get_content() == content
        finally:
            if service.storage.exists(key):
                service.storage.delete(key)

    def test_dedups_against_existing_postgres_row(self):
        """A pre-existing POSTGRES row for the same content is returned as-is
        (offload moves it later); no S3 object is written."""
        content = b"already in postgres" * 30
        existing = factories.BlobFactory(
            mailbox=factories.MailboxFactory(),
            content=content,
            content_type="message/rfc822",
        )
        assert existing.storage_location == BlobStorageLocationChoices.POSTGRES

        blob = models.Blob.objects.create_blob_on_s3(
            content=content, content_type="message/rfc822"
        )
        assert blob.id == existing.id
        assert blob.storage_location == BlobStorageLocationChoices.POSTGRES

        service = TieredStorageService()
        assert not service.storage.exists(
            TieredStorageService.compute_storage_key_for_blob(blob)
        )

    def test_empty_content_raises(self):
        """Empty content is rejected, same as ``create_blob``."""
        with pytest.raises(ValidationError):
            models.Blob.objects.create_blob_on_s3(
                content=b"", content_type="message/rfc822"
            )
