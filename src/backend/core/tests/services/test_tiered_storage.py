"""Tests for tiered storage functionality.

These tests use real object storage when available.
Unit tests only cover pure functions that don't require storage.
"""

# pylint: disable=protected-access,import-outside-toplevel,no-value-for-parameter,unused-argument,too-many-lines

import hashlib
import secrets

from django.db import transaction
from django.test import override_settings

import pytest

from core import enums, factories, models
from core.enums import BlobStorageLocationChoices, CompressionTypeChoices
from core.services.tiered_storage import TieredStorageService, sha256_advisory_lock

# Generate encryption keys at module level for decorators. Each is a
# fully-formed config entry, so override_settings can drop them straight
# into MESSAGES_BLOBS_ENCRYPT_KEYS without re-wrapping.
_TEST_ENCRYPTION_KEY = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
_TEST_ENCRYPTION_KEY_1 = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
_TEST_ENCRYPTION_KEY_2 = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}


class TestTieredStorageServiceUnit:
    """Pure unit tests for TieredStorageService (no DB, no storage)."""

    def test_compute_storage_key(self):
        """Storage keys encode (key_id, sha)."""
        sha256 = bytes.fromhex(
            "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        assert TieredStorageService.compute_storage_key(sha256, 0) == (
            "blobs/0/abc/"
            "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )
        assert TieredStorageService.compute_storage_key(sha256, 2) == (
            "blobs/2/abc/"
            "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

    def test_compute_storage_key_different_prefixes(self):
        """Different SHA256 hashes produce different sha-prefix directories."""
        sha1 = bytes.fromhex("abc" + "0" * 61)
        sha2 = bytes.fromhex("def" + "0" * 61)

        assert TieredStorageService.compute_storage_key(sha1, 0).startswith(
            "blobs/0/abc/"
        )
        assert TieredStorageService.compute_storage_key(sha2, 0).startswith(
            "blobs/0/def/"
        )

    def test_encrypt_decrypt_no_keys(self):
        """Test that encryption is a passthrough when no keys are configured."""
        service = TieredStorageService()
        service.encryption_keys = {}
        service.active_key_id = 0

        data = b"test data"
        sha = hashlib.sha256(data).digest()
        encrypted, key_id = service.encrypt(data, sha)

        assert encrypted == data  # Passthrough
        assert key_id == 0

        decrypted = service.decrypt(encrypted, key_id, sha)
        assert decrypted == data

    def test_encrypt_decrypt_with_key(self):
        """Test encryption and decryption with an AES-GCM key."""
        service = TieredStorageService()
        key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        service.encryption_keys = {"1": key}
        service.active_key_id = 1

        data = b"test data to encrypt"
        sha = hashlib.sha256(data).digest()
        encrypted, key_id = service.encrypt(data, sha)

        assert encrypted != data  # Should be encrypted
        assert key_id == 1

        decrypted = service.decrypt(encrypted, key_id, sha)
        assert decrypted == data

    def test_aad_prevents_swap(self):
        """Ciphertext encrypted with sha A must not decrypt with sha B.

        Critical regression test: without AAD-binding, an attacker with
        S3 write access (but no key) could move ciphertext between blob
        paths. AAD = sha256 makes the auth tag fail on mismatch.
        """
        from cryptography.exceptions import InvalidTag

        service = TieredStorageService()
        service.encryption_keys = {
            "1": {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        }
        service.active_key_id = 1

        sha_a = hashlib.sha256(b"content A").digest()
        sha_b = hashlib.sha256(b"content B").digest()
        encrypted_a, _ = service.encrypt(b"content A", sha_a)

        # Right sha decrypts.
        assert service.decrypt(encrypted_a, 1, sha_a) == b"content A"
        # Wrong sha (substitution attempt) is rejected by the tag.
        with pytest.raises(InvalidTag):
            service.decrypt(encrypted_a, 1, sha_b)

    def test_encrypt_passthrough_when_active_key_zero(self):
        """Test that encryption is passthrough when active_key_id=0 even with keys configured."""
        service = TieredStorageService()
        key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        service.encryption_keys = {"1": key}
        service.active_key_id = 0  # Disabled

        data = b"test data"
        sha = hashlib.sha256(data).digest()
        encrypted, key_id = service.encrypt(data, sha)

        assert encrypted == data  # Passthrough
        assert key_id == 0

    def test_decrypt_with_invalid_key_id(self):
        """Test that decryption fails with invalid key_id."""
        service = TieredStorageService()
        service.encryption_keys = {}

        with pytest.raises(ValueError, match="key_id 5 not found"):
            service.decrypt(b"data", 5, b"\x00" * 32)

    def test_encrypt_with_missing_active_key(self):
        """Test that encrypt fails if active_key_id not in encryption_keys."""
        service = TieredStorageService()
        service.encryption_keys = {
            "1": {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        }
        service.active_key_id = 99  # Not in keys

        with pytest.raises(ValueError, match="key_id 99 not found"):
            service.encrypt(b"test data", hashlib.sha256(b"test data").digest())

    def test_decrypt_with_corrupted_data(self):
        """decrypt fails with InvalidTag on corrupted ciphertext."""
        from cryptography.exceptions import InvalidTag

        service = TieredStorageService()
        service.encryption_keys = {
            "1": {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        }
        service.active_key_id = 1

        with pytest.raises(InvalidTag):
            service.decrypt(b"\x00" * 64, 1, b"\x00" * 32)

    def test_decrypt_with_wrong_key(self):
        """decrypt fails with InvalidTag when key doesn't match."""
        from cryptography.exceptions import InvalidTag

        service = TieredStorageService()
        service.encryption_keys = {
            "1": {"algo": "aes-gcm", "secret": secrets.token_hex(32)},
            "2": {"algo": "aes-gcm", "secret": secrets.token_hex(32)},
        }

        service.active_key_id = 1
        sha = hashlib.sha256(b"test data").digest()
        encrypted, _ = service.encrypt(b"test data", sha)

        with pytest.raises(InvalidTag):
            service.decrypt(encrypted, 2, sha)

    def test_encrypt_empty_data(self):
        """Test that encryption works with empty data."""
        service = TieredStorageService()
        key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        service.encryption_keys = {"1": key}
        service.active_key_id = 1

        sha = hashlib.sha256(b"").digest()
        encrypted, key_id = service.encrypt(b"", sha)
        assert key_id == 1
        assert encrypted != b""  # AES-GCM adds nonce + tag

        decrypted = service.decrypt(encrypted, key_id, sha)
        assert decrypted == b""

    def test_encrypt_with_key_rotation(self):
        """Test that we can encrypt with new key and decrypt with old key."""
        service = TieredStorageService()
        old_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        new_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}

        # Both keys available
        service.encryption_keys = {"1": old_key, "2": new_key}

        # Data encrypted with old key
        service.active_key_id = 1
        data = b"test data"
        sha = hashlib.sha256(data).digest()
        encrypted_old, old_key_id = service.encrypt(data, sha)
        assert old_key_id == 1

        # Now use new key for encryption
        service.active_key_id = 2
        encrypted_new, new_key_id = service.encrypt(data, sha)
        assert new_key_id == 2

        # Both can still be decrypted
        assert service.decrypt(encrypted_old, 1, sha) == data
        assert service.decrypt(encrypted_new, 2, sha) == data

    def test_unknown_algo_raises(self):
        """A key entry with an unknown algo is rejected at use time."""
        service = TieredStorageService()
        service.encryption_keys = {
            "1": {"algo": "rot13", "secret": secrets.token_hex(32)},
        }
        service.active_key_id = 1

        with pytest.raises(ValueError, match="unknown encryption algo"):
            service.encrypt(b"data", hashlib.sha256(b"data").digest())

    def test_bad_entry_shape_raises(self):
        """A key entry that isn't ``{"algo", "secret"}`` is rejected."""
        service = TieredStorageService()
        # Bare-secret shorthand is no longer accepted — every entry must
        # spell out algo and secret explicitly.
        service.encryption_keys = {"1": secrets.token_hex(32)}
        service.active_key_id = 1

        with pytest.raises(ValueError, match='"algo".*"secret"'):
            service.encrypt(b"data", hashlib.sha256(b"data").digest())


@pytest.mark.django_db
class TestTieredStorageDB:
    """Tests that require database but not object storage."""

    def test_blob_default_storage_location(self):
        """Test that new blobs default to POSTGRES storage location."""
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox,
            content=b"test content",
            content_type="text/plain",
        )

        assert blob.storage_location == BlobStorageLocationChoices.POSTGRES
        assert blob.encryption_key_id == 0
        assert blob.raw_content is not None

    @override_settings(MESSAGES_BLOBS_COMPRESS="zstd:3")
    def test_zstd_compresses_repetitive_content(self):
        """1024 'a's should compress to a tiny PG payload."""
        import pyzstd

        content = b"a" * 1024
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.compression == CompressionTypeChoices.ZSTD
        assert blob.size == 1024
        assert blob.encryption_key_id == 0
        # raw_content is the compressed bytes verbatim (no encryption).
        assert blob.size_compressed == len(bytes(blob.raw_content))
        assert blob.size_compressed == len(pyzstd.compress(content, level_or_option=3))
        # Sanity floor: zstd on a single-byte run is well under 50 bytes.
        assert blob.size_compressed < 50, blob.size_compressed

    @override_settings(MESSAGES_BLOBS_COMPRESS="none")
    def test_no_compression_preserves_size(self):
        """MESSAGES_BLOBS_COMPRESS=none stores plaintext bytes as-is."""
        content = b"a" * 1024
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.size == 1024
        assert blob.size_compressed == 1024
        assert bytes(blob.raw_content) == content

    @override_settings(
        MESSAGES_BLOBS_COMPRESS="zstd:3",
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_encryption_adds_exactly_aesgcm_overhead(self):
        """raw_content = compressed bytes + 28 (AES-GCM nonce 12 + tag 16)."""
        import pyzstd

        content = b"a" * 1024
        pure_compressed_len = len(pyzstd.compress(content, level_or_option=3))

        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.encryption_key_id == 1
        assert blob.size_compressed == pure_compressed_len + 28
        # And content still round-trips cleanly.
        assert blob.get_content() == content

    @override_settings(
        MESSAGES_BLOBS_COMPRESS="none",
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_encryption_with_no_compression_size(self):
        """With MESSAGES_BLOBS_COMPRESS=none: raw_content = plaintext_size + 28."""
        content = b"a" * 1024
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.encryption_key_id == 1
        assert blob.size_compressed == 1024 + 28
        assert blob.get_content() == content

    def test_blob_get_content_from_postgres(self):
        """Test getting content from a blob stored in PostgreSQL."""
        mailbox = factories.MailboxFactory()
        content = b"Hello World" * 100

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.storage_location == BlobStorageLocationChoices.POSTGRES
        assert blob.get_content() == content

    @override_settings(MESSAGES_BLOBS_VERIFY_HASH=True)
    def test_verify_hash_passes_on_clean_content(self):
        """``MESSAGES_BLOBS_VERIFY_HASH=True`` round-trips a clean blob."""
        content = b"clean content for verify-hash" * 10
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        assert blob.get_content() == content

    @override_settings(MESSAGES_BLOBS_VERIFY_HASH=True)
    def test_verify_hash_detects_plaintext_substitution(self):
        """``MESSAGES_BLOBS_VERIFY_HASH`` catches swap on key_id=0 blobs.

        For unencrypted blobs there is no AAD to bind the bytes; the
        verify-hash setting is the only line of defense. Simulate an
        attacker overwriting raw_content with a different blob's payload.
        """
        mailbox = factories.MailboxFactory()
        good = factories.BlobFactory(
            mailbox=mailbox, content=b"good content" * 20, content_type="text/plain"
        )
        evil = factories.BlobFactory(
            mailbox=mailbox, content=b"evil content" * 20, content_type="text/plain"
        )
        # No encryption (key_id=0): raw_content is plain compressed bytes.
        # Splat evil's bytes onto good's row; sha256 column unchanged.
        good.raw_content = bytes(evil.raw_content)
        good.save(update_fields=["raw_content"])

        with pytest.raises(ValueError, match="content hash mismatch"):
            good.get_content()

    def test_verify_hash_off_by_default(self):
        """Without the flag, plaintext substitution is undetected (today)."""
        mailbox = factories.MailboxFactory()
        good = factories.BlobFactory(
            mailbox=mailbox, content=b"good content" * 20, content_type="text/plain"
        )
        evil = factories.BlobFactory(
            mailbox=mailbox, content=b"evil content" * 20, content_type="text/plain"
        )
        good.raw_content = bytes(evil.raw_content)
        good.save(update_fields=["raw_content"])

        # No exception — swap goes undetected at read time. The flag exists
        # precisely to opt into the extra check.
        assert good.get_content() == b"evil content" * 20

    def test_blob_get_content_raises_when_no_raw_content(self):
        """Test that get_content raises when raw_content is None for POSTGRES location."""
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )

        blob.raw_content = None
        blob.save(update_fields=["raw_content"])

        with pytest.raises(ValueError, match="has no content in PostgreSQL"):
            blob.get_content()

    def test_same_content_same_blob_row(self):
        """Identical content always lands as one ``Blob`` row regardless
        of which mailbox uploads it. DB-level dedup at create time."""
        mailbox1 = factories.MailboxFactory()
        mailbox2 = factories.MailboxFactory()
        content = b"identical content for both blobs"

        blob1 = factories.BlobFactory(
            mailbox=mailbox1, content=content, content_type="text/plain"
        )
        blob2 = factories.BlobFactory(
            mailbox=mailbox2, content=content, content_type="text/plain"
        )

        assert blob1.sha256 == blob2.sha256
        assert blob1.id == blob2.id

    def test_get_existing_sibling(self):
        """get_existing_sibling returns ``(key_id, compression)`` of any
        OBJECT_STORAGE sibling, or None if there is none.

        Compression is intentionally NOT a filter — a sha256 has exactly
        one stored object cluster-wide, and new uploads adopt its
        compression rather than creating a parallel object.
        """
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test content", content_type="text/plain"
        )

        assert service.get_existing_sibling(bytes(blob.sha256)) is None

        blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob.encryption_key_id = 7
        blob.compression = CompressionTypeChoices.ZSTD
        blob.save()

        assert service.get_existing_sibling(bytes(blob.sha256)) == (
            7,
            CompressionTypeChoices.ZSTD,
        )


@pytest.mark.django_db
class TestTieredStorageE2E:
    """End-to-end tests that hit real object storage."""

    def test_upload_download_roundtrip(self):
        """Test uploading a blob to object storage and downloading it back."""
        import pyzstd

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Hello, this is e2e test content for tiered storage!" * 10

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            assert service.storage.exists(storage_key)

            downloaded = service.download_blob(blob)
            decompressed = pyzstd.decompress(downloaded)
            assert decompressed == content
        finally:
            service.storage.delete(storage_key)

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_upload_download_with_encryption(self):
        """Test upload/download roundtrip with encryption enabled."""
        import pyzstd

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Encrypted content for e2e test" * 10

        # create_blob() should automatically encrypt when keys are configured
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        assert blob.encryption_key_id > 0  # Should be encrypted
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            assert service.storage.exists(storage_key)

            # Download returns decrypted but compressed content
            downloaded = service.download_blob(blob)
            decompressed = pyzstd.decompress(downloaded)
            assert decompressed == content
        finally:
            service.storage.delete(storage_key)

    # ``test_deduplication_single_upload`` removed: with DB-level dedup
    # at ``BlobManager.create_blob``, "two blobs with same content"
    # is no longer a thing — same content always lands as one row.
    # The single-blob lifecycle is covered by ``test_full_offload_workflow``;
    # the cross-mailbox dedup behaviour is pinned by
    # ``TestBlobDedup.test_email_to_multiple_mailboxes_shares_one_blob``.

    @pytest.mark.django_db(transaction=True)
    def test_full_offload_workflow(self):
        """Test the complete offload workflow: create blob, offload, read content."""
        from core.services.tiered_storage_tasks import offload_one_blob

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"Content for full offload workflow test" * 20

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES

        try:
            result = offload_one_blob(str(blob.id), service)
            assert result["status"] == "success"

            blob.refresh_from_db()
            assert (
                blob.storage_location == enums.BlobStorageLocationChoices.OBJECT_STORAGE
            )
            assert blob.raw_content is None

            retrieved = blob.get_content()
            assert retrieved == content
        finally:
            # Clear the MailboxBlob reservation row before deleting the
            # Blob — its FK is PROTECT, so blob.delete() would raise
            # ProtectedError otherwise.
            models.MailboxBlob.objects.filter(blob=blob).delete()
            blob.delete()

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    @pytest.mark.django_db(transaction=True)
    def test_offload_with_encryption_roundtrip(self):
        """
        Test full offload workflow with encryption: create encrypted blob,
        offload to storage, read back content.

        This is a critical regression test for the double-encryption bug.
        """
        from core.services.tiered_storage_tasks import offload_one_blob

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        original_content = b"Test content for encryption offload roundtrip" * 50

        # create_blob() should automatically encrypt when keys are configured
        blob = factories.BlobFactory(
            mailbox=mailbox, content=original_content, content_type="text/plain"
        )
        assert blob.encryption_key_id > 0  # Should be encrypted

        try:
            result = offload_one_blob(str(blob.id), service)
            assert result["status"] == "success"

            blob.refresh_from_db()
            assert (
                blob.storage_location == enums.BlobStorageLocationChoices.OBJECT_STORAGE
            )
            assert blob.raw_content is None
            assert blob.encryption_key_id > 0  # Preserved

            # Critical: content should still be readable
            retrieved_content = blob.get_content()
            assert retrieved_content == original_content
        finally:
            # Clear the MailboxBlob reservation row before deleting the
            # Blob — its FK is PROTECT, so blob.delete() would raise
            # ProtectedError otherwise.
            models.MailboxBlob.objects.filter(blob=blob).delete()
            blob.delete()

    @pytest.mark.django_db(transaction=True)
    def test_delete_if_orphaned(self):
        """delete_if_orphaned: returns False when a Blob row still
        references (sha, key_id); deletes the S3 object when none does.

        The S3 cleanup is no longer wired to ``Blob.post_delete`` —
        it's done inline by ``gc_orphan_blobs_task`` after deleting
        the row. This test exercises the service method directly.
        """
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test content", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.save()

            # The blob row references this (sha, key_id) — no-op.
            assert (
                service.delete_if_orphaned(bytes(blob.sha256), blob.encryption_key_id)
                is False
            )
            assert service.storage.exists(storage_key)

            sha = bytes(blob.sha256)
            key_id = blob.encryption_key_id
            # Drop the MailboxBlob reservation row first; ``Blob.delete()``
            # would otherwise raise ProtectedError. Then delete the row
            # (mirrors what the GC task does under the per-sha advisory
            # lock). After the row is gone, delete_if_orphaned actually
            # deletes the S3 object.
            models.MailboxBlob.objects.filter(blob=blob).delete()
            blob.delete()
            assert service.delete_if_orphaned(sha, key_id) is True
            assert not service.storage.exists(storage_key)
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_upload_creates_storage_object(self):
        """upload_blob writes to the (sha, key_id) path."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        assert not service.storage.exists(storage_key)
        try:
            service.upload_blob(blob)
            assert service.storage.exists(storage_key)
        finally:
            service.storage.delete(storage_key)

    # ``test_upload_dedups_against_db_sibling`` removed: with DB-level
    # dedup at ``BlobManager.create_blob``, a second mailbox creating
    # the same content gets the SAME Blob row, and ``upload_blob`` is
    # only ever called once per row (when it's first offloaded).
    # The "two blobs same sha sharing one storage object" scenario
    # that used to need testing here is now structurally impossible.

    def test_download_missing_blob_raises(self):
        """Test that download_blob raises FileNotFoundError for missing blob."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )

        # Mark as in object storage but don't actually upload
        blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob.save()

        with pytest.raises(FileNotFoundError):
            service.download_blob(blob)

    def test_upload_blob_without_content_raises(self):
        """Test that upload_blob raises ValueError when blob has no content."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"test", content_type="text/plain"
        )
        blob.raw_content = None
        blob.save()

        with pytest.raises(ValueError, match="has no raw_content"):
            service.upload_blob(blob)

    def test_deduplication_with_different_encryption_keys(self):
        """
        Test deduplication when blobs were encrypted with different keys.

        With DB-level dedup at create time, the second
        ``mailbox.create_blob`` returns the EXISTING Blob row regardless
        of which encryption key is currently active — the encryption
        key on the row stays as whatever was used on first insert.
        Re-encrypting old rows is a separate operation
        (``re_store_blobs``).
        """
        content = b"Same content, different keys" * 20

        # First blob: key 1 active.
        with override_settings(
            MESSAGES_BLOBS_ENCRYPT_KEYS={
                "1": {**_TEST_ENCRYPTION_KEY_1, "active": True}
            },
        ):
            mailbox1 = factories.MailboxFactory()
            blob1 = factories.BlobFactory(
                mailbox=mailbox1, content=content, content_type="text/plain"
            )
            assert blob1.encryption_key_id == 1

        # Switch to key 2 active. New ``create_blob`` of the same
        # content dedups against the existing row (key_id=1).
        with override_settings(
            MESSAGES_BLOBS_ENCRYPT_KEYS={
                "1": _TEST_ENCRYPTION_KEY_1,
                "2": {**_TEST_ENCRYPTION_KEY_2, "active": True},
            },
        ):
            mailbox2 = factories.MailboxFactory()
            same_blob = factories.BlobFactory(
                mailbox=mailbox2, content=content, content_type="text/plain"
            )
            assert same_blob.id == blob1.id
            assert same_blob.encryption_key_id == 1  # NOT 2; row keeps original key
            # Both still readable as long as key 1 stays in the dict.
            assert same_blob.get_content() == content

    # ``test_deduplication_adopts_existing_compression`` removed: with
    # DB-level dedup at create time, the second ``create_blob`` returns
    # the existing row (with its existing compression), so the
    # compression-adoption path in ``upload_blob`` is no longer
    # exercised by application calls. The path is still defended
    # against legacy multi-row data via ``get_existing_sibling``.


@pytest.mark.redis
@pytest.mark.django_db(transaction=True)
class TestBlobGarbageCollection:
    """Lifecycle of a Blob is now governed by the GC sweep.

    These tests exercise the new contract: deleting reference sources
    (Message / Attachment / MessageTemplate) pushes the blob_id into a
    Redis candidate set; the periodic ``gc_orphan_blobs_task`` reads the
    set, re-checks references under the per-sha advisory lock, and
    deletes the row + cleans up S3 inline if no references remain.

    The ``redis_cache`` fixture (autouse on the class) routes
    ``CACHES['default']`` to the real Redis service so ``schedule_for_gc``
    and ``reserve_upload`` actually push and the fast-mode GC drains
    them. Marked ``@pytest.mark.redis`` so ``pytest -m "not redis"``
    skips them when Redis isn't available locally.
    """

    @pytest.fixture(autouse=True)
    def _redis_cache(self, redis_cache):
        pass

    def _drop_all_reservations(self, *blob_ids):
        """Test helper: clear ``MailboxBlob`` rows for these blobs so
        the GC isn't blocked by an active reservation. Mirrors what
        ``release_upload`` does in production, but does it for every
        mailbox at once since these tests don't care which mailbox
        owned the upload."""
        from core.models import MailboxBlob

        MailboxBlob.objects.filter(blob_id__in=blob_ids).delete()

    def test_gc_deletes_orphan_blob_postgres(self):
        """A blob with no Message/Attachment/Template references is GC'd."""
        from core.models import Blob
        from core.services.blob_gc import (
            gc_orphan_blobs_task,
            schedule_for_gc,
        )

        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"orphan", content_type="text/plain"
        )
        self._drop_all_reservations(blob.id)
        schedule_for_gc(blob.id)

        result = gc_orphan_blobs_task(mode="fast")

        assert result["deleted"] >= 1
        assert not Blob.objects.filter(id=blob.id).exists()

    def test_gc_deletes_orphan_blob_object_storage_and_cleans_s3(self):
        """Orphan blob sitting in S3: GC deletes the row AND the bucket object."""
        from core.models import Blob
        from core.services.blob_gc import (
            gc_orphan_blobs_task,
            schedule_for_gc,
        )

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"orphan in s3", content_type="text/plain"
        )
        self._drop_all_reservations(blob.id)

        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)
        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()
            assert service.storage.exists(storage_key)

            schedule_for_gc(blob.id)
            result = gc_orphan_blobs_task(mode="fast")

            assert result["deleted"] >= 1
            assert not Blob.objects.filter(id=blob.id).exists()
            # Last cohort member gone → S3 object cleaned inline.
            assert not service.storage.exists(storage_key)
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_gc_skips_referenced_blob(self):
        """GC must NOT delete a blob still referenced by an Attachment."""
        from core.models import Blob
        from core.services.blob_gc import (
            gc_orphan_blobs_task,
            schedule_for_gc,
        )

        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"referenced", content_type="text/plain"
        )
        self._drop_all_reservations(blob.id)
        # Attach to keep the blob alive. AttachmentFactory autogenerates
        # an owning draft Message via lazy_attribute; we don't care
        # which Message it is — just that there is one.
        factories.AttachmentFactory(blob=blob, mailbox=mailbox)

        schedule_for_gc(blob.id)
        result = gc_orphan_blobs_task(mode="fast")

        assert result["skipped_referenced"] >= 1
        assert Blob.objects.filter(id=blob.id).exists()

    def test_gc_skips_reserved_blob(self):
        """GC must respect the upload reservation window (JMAP 2-step).

        Reservations are now ``MailboxBlob`` rows, which the GC's
        ``is_referenced`` walk includes (when ``expires_at > now()``).
        So a reserved blob shows up under ``skipped_referenced`` —
        the GC no longer reports a separate ``skipped_reserved`` count.
        """
        from core.models import Blob
        from core.services.blob_gc import (
            gc_orphan_blobs_task,
            schedule_for_gc,
        )

        mailbox = factories.MailboxFactory()
        # ``BlobFactory(mailbox=...)`` creates a MailboxBlob with the
        # default 1h ``expires_at``; don't drop it.
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"reserved", content_type="text/plain"
        )

        schedule_for_gc(blob.id)
        result = gc_orphan_blobs_task(mode="fast")

        assert result["skipped_referenced"] >= 1
        assert Blob.objects.filter(id=blob.id).exists()

    def test_gc_full_mode_finds_orphan_not_in_redis(self):
        """The full sweep walks every Blob, catching anything Redis missed."""
        from core.models import Blob
        from core.services.blob_gc import gc_orphan_blobs_task

        mailbox = factories.MailboxFactory()
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"orphan-no-redis-entry", content_type="text/plain"
        )
        self._drop_all_reservations(blob.id)
        # Note: NOT calling schedule_for_gc — simulates a Redis outage
        # where the candidate set didn't capture this blob.

        result = gc_orphan_blobs_task(mode="full")

        assert result["deleted"] >= 1
        assert not Blob.objects.filter(id=blob.id).exists()

    def test_message_delete_schedules_blob_for_gc(self):
        """Deleting a Message pushes its blob_id (and draft_blob_id) to GC set."""
        from core.models import Blob
        from core.services.blob_gc import gc_orphan_blobs_task

        mailbox = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = factories.ContactFactory(mailbox=mailbox)
        blob = factories.BlobFactory(
            mailbox=mailbox, content=b"message body" * 10, content_type="message/rfc822"
        )
        self._drop_all_reservations(blob.id)
        message = factories.MessageFactory(thread=thread, sender=contact, blob=blob)

        # Delete the only reference; signal pushes blob.id to GC set.
        message.delete()

        # Fast GC drains the set and removes the now-orphan blob.
        result = gc_orphan_blobs_task(mode="fast")
        assert result["deleted"] >= 1
        assert not Blob.objects.filter(id=blob.id).exists()


@pytest.mark.django_db(transaction=True)
class TestBlobDedup:
    """DB-level dedup: same content always lands as one Blob row, even
    when delivered to multiple mailboxes."""

    def test_email_to_multiple_mailboxes_shares_one_blob(self):
        """Different Message rows in different mailboxes can FK the same Blob."""
        from core.models import Blob

        mailbox_a = factories.MailboxFactory()
        mailbox_b = factories.MailboxFactory()
        eml = (
            b"From: s@example.org\r\nTo: a@local, b@local\r\nSubject: same\r\n\r\nbody"
        )

        blob_for_a = factories.BlobFactory(
            mailbox=mailbox_a, content=eml, content_type="message/rfc822"
        )
        blob_for_b = factories.BlobFactory(
            mailbox=mailbox_b, content=eml, content_type="message/rfc822"
        )

        # One Blob row, two FKs from two Messages (in two different
        # mailboxes / threads). This is the cross-tenant dedup that the
        # FK-based ownership previously made impossible.
        assert blob_for_a.id == blob_for_b.id
        assert Blob.objects.filter(sha256=blob_for_a.sha256).count() == 1

    def test_dedup_skips_compress_and_encrypt_on_hit(self):
        """The hash-first fast path returns before doing crypto/compression
        work. We can't directly observe "didn't run", so assert on side
        effects: storage_location, encryption_key_id, and raw_content
        should match the existing blob exactly (no fresh encrypt → no
        nonce churn)."""
        mailbox_a = factories.MailboxFactory()
        mailbox_b = factories.MailboxFactory()
        content = b"identical-bytes-twice" * 20

        first = factories.BlobFactory(
            mailbox=mailbox_a, content=content, content_type="text/plain"
        )
        second = factories.BlobFactory(
            mailbox=mailbox_b, content=content, content_type="text/plain"
        )

        assert first.id == second.id
        assert bytes(first.raw_content) == bytes(second.raw_content)
        assert first.encryption_key_id == second.encryption_key_id
        assert first.storage_location == second.storage_location

    def test_mailbox_delete_does_not_break_shared_thread_blobs(self):
        """Regression test for the shared-thread cascade bug.

        Mailbox A and Mailbox B share a thread T. Message M lives in T
        and references Blob B_M. Before the FK was dropped, deleting
        Mailbox A would CASCADE-delete B_M (because the blob was
        "owned" by A), breaking M.blob for the still-existing access
        from B.

        With the FK gone: deleting A leaves B_M intact. Mailbox B can
        still read M.get_content().
        """
        mailbox_a = factories.MailboxFactory()
        mailbox_b = factories.MailboxFactory()
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox_a, thread=thread)
        factories.ThreadAccessFactory(mailbox=mailbox_b, thread=thread)
        # Contact owned by B so it survives the deletion of A — the test
        # is specifically about the Blob lifecycle, not Contact cascade.
        contact = factories.ContactFactory(mailbox=mailbox_b)
        original_content = b"shared message body" * 30
        # ``BlobFactory(mailbox=A)`` creates a ``MailboxBlob``
        # reservation row for A; release it so it's not the thing
        # keeping B's view of the blob alive.
        blob = factories.BlobFactory(
            mailbox=mailbox_a, content=original_content, content_type="message/rfc822"
        )
        models.MailboxBlob.objects.filter(blob=blob, mailbox=mailbox_a).delete()
        message = factories.MessageFactory(thread=thread, sender=contact, blob=blob)

        # Pre-delete check: M.blob is readable (the blob is alive).
        assert message.blob.get_content() == original_content

        mailbox_a.delete()
        message.refresh_from_db()

        # Post-delete check: blob still alive (Message in T still
        # references it; T is still accessible via B). M.blob is NOT
        # NULLed and the bytes are still readable.
        assert message.blob_id is not None
        assert message.blob.get_content() == original_content


@pytest.mark.django_db
class TestTieredStorageKeyRotation:
    """Tests for encryption key rotation scenarios."""

    def test_key_rotation_postgres_blob(self):
        """Test re-encrypting a PostgreSQL blob with a new key."""
        import pyzstd

        service = TieredStorageService()
        old_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        new_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}

        mailbox = factories.MailboxFactory()
        original_content = b"Content for key rotation test" * 20

        # Create blob and encrypt with old key
        blob = factories.BlobFactory(
            mailbox=mailbox, content=original_content, content_type="text/plain"
        )
        compressed = bytes(blob.raw_content)
        sha = bytes(blob.sha256)

        service.encryption_keys = {"1": old_key}
        service.active_key_id = 1
        encrypted_old, old_key_id = service.encrypt(compressed, sha)
        blob.raw_content = encrypted_old
        blob.encryption_key_id = old_key_id
        blob.save()

        # Verify we can decrypt with old key
        decrypted = service.decrypt(
            bytes(blob.raw_content), blob.encryption_key_id, sha
        )
        assert pyzstd.decompress(decrypted) == original_content

        # Add new key and set as active
        service.encryption_keys = {"1": old_key, "2": new_key}
        service.active_key_id = 2

        # Re-encrypt: decrypt with old key, encrypt with new key
        decrypted = service.decrypt(
            bytes(blob.raw_content), blob.encryption_key_id, sha
        )
        encrypted_new, new_key_id = service.encrypt(decrypted, sha)
        blob.raw_content = encrypted_new
        blob.encryption_key_id = new_key_id
        blob.save()

        assert blob.encryption_key_id == 2

        # Verify content still accessible
        decrypted = service.decrypt(
            bytes(blob.raw_content), blob.encryption_key_id, sha
        )
        assert pyzstd.decompress(decrypted) == original_content

    def test_key_rotation_object_storage_blob(self, django_capture_on_commit_callbacks):
        """rotate_blob moves the storage object from old path to new path.

        ``rotate_blob`` defers the old-path delete to
        ``transaction.on_commit`` so a rollback can't strand readers
        on a deleted S3 object. This test runs under
        ``@pytest.mark.django_db`` (no transaction=True), so the
        outer test transaction is rolled back and on_commit hooks
        normally wouldn't fire — ``django_capture_on_commit_callbacks``
        executes them at the with-block exit so the assertion still
        observes the deferred S3 delete.
        """
        # pylint: disable-next=import-outside-toplevel
        import pyzstd

        service = TieredStorageService()
        old_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        new_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}

        mailbox = factories.MailboxFactory()
        original_content = b"Content for object storage key rotation" * 20

        blob = factories.BlobFactory(
            mailbox=mailbox, content=original_content, content_type="text/plain"
        )
        compressed = bytes(blob.raw_content)
        sha = bytes(blob.sha256)

        service.encryption_keys = {"1": old_key}
        service.active_key_id = 1
        encrypted_old, old_key_id = service.encrypt(compressed, sha)
        blob.raw_content = encrypted_old
        blob.encryption_key_id = old_key_id
        blob.save()

        old_path = TieredStorageService.compute_storage_key_for_blob(blob)
        new_path = TieredStorageService.compute_storage_key(bytes(blob.sha256), 2)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            service.encryption_keys = {"1": old_key, "2": new_key}
            service.active_key_id = 2

            with django_capture_on_commit_callbacks(execute=True):
                with transaction.atomic(), sha256_advisory_lock(bytes(blob.sha256)):
                    assert service.rotate_blob(blob, 2) is True

            blob.refresh_from_db()
            assert blob.encryption_key_id == 2
            # Old path is gone, new path is the canonical one.
            assert not service.storage.exists(old_path)
            assert service.storage.exists(new_path)

            downloaded = service.download_blob(blob)
            assert pyzstd.decompress(downloaded) == original_content
        finally:
            for k in (old_path, new_path):
                if service.storage.exists(k):
                    service.storage.delete(k)


@pytest.mark.django_db(transaction=True)
class TestDataSafetyEndToEnd:
    """End-to-end tests for the data-safety guarantees: AAD swap detection,
    plaintext substitution detection (verify-hash), and the read paths for
    state combinations the rest of the suite covers only partially."""

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_aad_swap_postgres_end_to_end(self):
        """Splice blob X's raw_content (its encrypted bytes) onto blob Y's
        row. Y.get_content() must fail because the AAD bound at encrypt
        time is X's sha256, not Y's. This is the row-level realization
        of ``test_aad_prevents_swap``."""
        from cryptography.exceptions import InvalidTag

        mailbox = factories.MailboxFactory()
        blob_x = factories.BlobFactory(
            mailbox=mailbox, content=b"content X" * 50, content_type="text/plain"
        )
        blob_y = factories.BlobFactory(
            mailbox=mailbox, content=b"content Y" * 50, content_type="text/plain"
        )
        # Sanity: both encrypted, distinct sha.
        assert blob_x.encryption_key_id == 1
        assert blob_y.encryption_key_id == 1
        assert bytes(blob_x.sha256) != bytes(blob_y.sha256)

        # Splice X's ciphertext onto Y's row (preserve Y's sha256 column).
        blob_y.raw_content = bytes(blob_x.raw_content)
        blob_y.save(update_fields=["raw_content"])

        with pytest.raises(InvalidTag):
            blob_y.get_content()

    @override_settings(
        MESSAGES_BLOBS_ENCRYPT_KEYS={"1": {**_TEST_ENCRYPTION_KEY, "active": True}},
    )
    def test_aad_swap_object_storage_end_to_end(self):
        """Same swap, but at the S3 layer: write blob X's ciphertext to
        blob Y's S3 path; Y.get_content() fails on AAD mismatch."""
        from django.core.files.base import ContentFile

        from cryptography.exceptions import InvalidTag

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob_x = factories.BlobFactory(
            mailbox=mailbox, content=b"content X" * 50, content_type="text/plain"
        )
        blob_y = factories.BlobFactory(
            mailbox=mailbox, content=b"content Y" * 50, content_type="text/plain"
        )

        # Get X's ciphertext, then offload Y so it has an S3 path.
        x_cipher = bytes(blob_x.raw_content)
        service.upload_blob(blob_y)
        blob_y.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob_y.raw_content = None
        blob_y.save()

        y_path = TieredStorageService.compute_storage_key_for_blob(blob_y)
        try:
            assert service.storage.exists(y_path)
            # Overwrite Y's S3 object with X's ciphertext.
            service.storage.delete(y_path)
            service.storage.save(y_path, ContentFile(x_cipher))

            with pytest.raises(InvalidTag):
                blob_y.get_content()
        finally:
            if service.storage.exists(y_path):
                service.storage.delete(y_path)

    @override_settings(MESSAGES_BLOBS_VERIFY_HASH=True)
    def test_verify_hash_detects_s3_substitution(self):
        """For ``key_id=0`` (plaintext-stored) S3 blobs the AAD layer
        doesn't exist; ``MESSAGES_BLOBS_VERIFY_HASH`` is the only line
        of defense. Splice blob X's plaintext bytes onto blob Y's S3
        path, expect ``Y.get_content()`` to raise."""
        from django.core.files.base import ContentFile

        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        blob_x = factories.BlobFactory(
            mailbox=mailbox, content=b"content X" * 50, content_type="text/plain"
        )
        blob_y = factories.BlobFactory(
            mailbox=mailbox, content=b"content Y" * 50, content_type="text/plain"
        )
        # Both unencrypted (no keys configured for these creates).
        assert blob_x.encryption_key_id == 0
        assert blob_y.encryption_key_id == 0

        x_cipher = bytes(blob_x.raw_content)  # plain compressed bytes
        service.upload_blob(blob_y)
        blob_y.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
        blob_y.raw_content = None
        blob_y.save()

        y_path = TieredStorageService.compute_storage_key_for_blob(blob_y)
        try:
            service.storage.delete(y_path)
            service.storage.save(y_path, ContentFile(x_cipher))

            with pytest.raises(ValueError, match="content hash mismatch"):
                blob_y.get_content()
        finally:
            if service.storage.exists(y_path):
                service.storage.delete(y_path)

    def test_read_legacy_plain_object_storage(self):
        """``(storage=S3, key_id=0)`` — a blob created before encryption
        was enabled and offloaded to S3 stays plain in S3. Reading it
        must return the original content."""
        service = TieredStorageService()
        mailbox = factories.MailboxFactory()
        content = b"legacy plain S3 content" * 30
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        assert blob.encryption_key_id == 0  # no encryption configured
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)

        try:
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()

            assert blob.get_content() == content
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_cohort_rotation_flips_all_rows_object_storage(self):
        """A cohort of N rows sharing one S3 object at key_id=K rotates
        atomically — every row in the cohort flips to the new key_id
        when ``rotate_blob`` runs. Critical because ``rotate_blob`` does
        a single ``UPDATE ... WHERE (sha, key_id)`` and we must trust
        that nothing in the cohort is left behind on the old key."""
        # pylint: disable-next=import-outside-toplevel
        import pyzstd

        service = TieredStorageService()
        old_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}
        new_key = {"algo": "aes-gcm", "secret": secrets.token_hex(32)}

        content = b"shared cohort content" * 30

        # First blob via the dedup-create path; sibling rows are inserted
        # directly via ``Blob.objects.create`` to bypass dedup so the
        # cohort actually has multiple DB rows sharing ``(sha, key_id)``.
        with override_settings(
            MESSAGES_BLOBS_ENCRYPT_KEYS={**{"1": {**old_key, "active": True}}},
        ):
            canonical = factories.BlobFactory(
                mailbox=factories.MailboxFactory(),
                content=content,
                content_type="text/plain",
            )
        siblings = [
            models.Blob.objects.create(
                sha256=canonical.sha256,
                size=canonical.size,
                size_compressed=canonical.size_compressed,
                content_type=canonical.content_type,
                compression=canonical.compression,
                raw_content=canonical.raw_content,
                encryption_key_id=canonical.encryption_key_id,
            )
            for _ in range(2)
        ]
        blobs = [canonical, *siblings]
        assert len({b.id for b in blobs}) == 3
        assert {b.encryption_key_id for b in blobs} == {1}
        assert len({bytes(b.sha256) for b in blobs}) == 1

        # Offload the canonical row; siblings flip in-place (they share
        # the same S3 path so no upload is needed).
        old_path = TieredStorageService.compute_storage_key_for_blob(canonical)
        service.upload_blob(canonical)
        for b in blobs:
            b.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            b.raw_content = None
            b.save()

        new_path = TieredStorageService.compute_storage_key(bytes(blobs[0].sha256), 2)
        try:
            # Now active key flips to 2; rotate the cohort.
            with override_settings(
                MESSAGES_BLOBS_ENCRYPT_KEYS={
                    "1": {**old_key},  # passive, still readable
                    "2": {**new_key, "active": True},
                },
            ):
                service = TieredStorageService()
                with transaction.atomic(), sha256_advisory_lock(bytes(blobs[0].sha256)):
                    assert service.rotate_blob(blobs[0], 2) is True

                # All three rows must now be at key_id=2.
                for b in blobs:
                    b.refresh_from_db()
                    assert b.encryption_key_id == 2
                # New path holds the cohort's ciphertext; old path is gone.
                assert service.storage.exists(new_path)
                assert not service.storage.exists(old_path)
                # And every blob is still readable.
                for b in blobs:
                    downloaded = service.download_blob(b)
                    assert pyzstd.decompress(downloaded) == content
        finally:
            for k in (old_path, new_path):
                if service.storage.exists(k):
                    service.storage.delete(k)

    def test_offload_keeps_legacy_plain_blob_plain(self):
        """A blob created with ``key_id=0`` (no encryption) STAYS plain
        when offloaded — even if encryption is enabled later. The offload
        path doesn't re-encrypt; that's ``--re-store``'s job. This pins
        the current contract so a future contributor doesn't accidentally
        decide that offload should encrypt-on-the-fly (which would change
        sha-cohort semantics)."""
        from core.services.tiered_storage_tasks import offload_one_blob

        # Step 1: create a plain blob (no keys configured at this point).
        mailbox = factories.MailboxFactory()
        content = b"legacy plain content offloaded later" * 20
        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )
        assert blob.encryption_key_id == 0

        # Step 2: encryption is enabled (key 1 active).
        storage_key = TieredStorageService.compute_storage_key_for_blob(blob)
        try:
            with override_settings(
                MESSAGES_BLOBS_ENCRYPT_KEYS={
                    "1": {**_TEST_ENCRYPTION_KEY, "active": True}
                },
            ):
                service = TieredStorageService()
                # Offload: blob's row stays at key_id=0 (path is blobs/0/...).
                result = offload_one_blob(str(blob.id), service)
                assert result["status"] == "success"

                blob.refresh_from_db()
                assert blob.encryption_key_id == 0  # NOT encrypted by offload
                assert (
                    blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
                )
                # Read still works (decrypt with key_id=0 is passthrough).
                assert blob.get_content() == content
        finally:
            if service.storage.exists(storage_key):
                service.storage.delete(storage_key)

    def test_dedup_after_encryption_enabled_returns_existing_plain_row(self):
        """Plain blob already in S3, encryption gets enabled. A second
        ``create_blob`` for the same content returns the EXISTING plain
        row (DB-level dedup at create time). The new row that would have
        been encrypted under key 1 is never created; both mailboxes
        share the existing key_id=0 row."""
        service = TieredStorageService()
        content = b"dedup-with-plain-sibling content" * 30

        # Step 1: plain blob (no encryption) goes to S3.
        mailbox_old = factories.MailboxFactory()
        sibling = factories.BlobFactory(
            mailbox=mailbox_old, content=content, content_type="text/plain"
        )
        assert sibling.encryption_key_id == 0
        sibling_path = TieredStorageService.compute_storage_key_for_blob(sibling)
        try:
            service.upload_blob(sibling)
            sibling.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            sibling.raw_content = None
            sibling.save()

            # Step 2: encryption is enabled, but a fresh ``create_blob``
            # for the same content dedups against the existing row.
            with override_settings(
                MESSAGES_BLOBS_ENCRYPT_KEYS={
                    "1": {**_TEST_ENCRYPTION_KEY, "active": True}
                },
            ):
                mailbox_new = factories.MailboxFactory()
                same_row = factories.BlobFactory(
                    mailbox=mailbox_new, content=content, content_type="text/plain"
                )
                assert same_row.id == sibling.id
                assert same_row.encryption_key_id == 0
                # Read still works for both mailboxes (passthrough decrypt).
                assert same_row.get_content() == content
        finally:
            if service.storage.exists(sibling_path):
                service.storage.delete(sibling_path)
