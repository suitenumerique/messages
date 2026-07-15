"""Tests for blob compression functionality."""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import override_settings

import pytest

from core import enums, factories, models
from core.services.tiered_storage import TieredStorageService


@pytest.mark.django_db
class TestBlobCompression:
    """Test suite for blob compression functionality."""

    @override_settings(MESSAGES_BLOBS_COMPRESS="none")
    def test_blob_no_compression(self):
        """Test blob creation without compression."""
        content = b"Hello World" * 1000  # Create some content to compress
        mailbox = factories.MailboxFactory()

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        # Check sizes
        assert blob.size == len(content)  # Original size
        assert blob.size_compressed == len(
            content
        )  # Should be the same as no compression
        assert blob.compression == enums.CompressionTypeChoices.NONE
        assert blob.get_content() == content  # Content should be unchanged

    @override_settings(MESSAGES_BLOBS_COMPRESS="zstd:3")
    def test_blob_zstd_compression(self):
        """Test blob creation with ZSTD compression."""
        content = b"Hello World" * 1000  # Create some content that will compress well
        mailbox = factories.MailboxFactory()

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        # Check sizes
        assert blob.size == len(content)  # Original size
        assert blob.size_compressed < len(content)  # Compressed size should be smaller
        assert blob.compression == enums.CompressionTypeChoices.ZSTD
        assert (
            blob.get_content() == content
        )  # Decompressed content should match original

    def test_blob_compression_empty_content(self):
        """Test blob creation with empty content."""
        mailbox = factories.MailboxFactory()

        # Try to create blob with empty content
        with pytest.raises(ValidationError, match="Content cannot be empty"):
            factories.BlobFactory(
                mailbox=mailbox, content=b"", content_type="text/plain"
            )

    @override_settings(MESSAGES_BLOBS_COMPRESS="zstd:3")
    def test_blob_large_content_compression(self):
        """Test compression with large content."""
        # Create a large content that should compress well
        content = b"A" * 1000000  # 1MB of repeating data
        mailbox = factories.MailboxFactory()

        blob = factories.BlobFactory(
            mailbox=mailbox, content=content, content_type="text/plain"
        )

        # Verify compression ratio is significant
        compression_ratio = blob.size_compressed / blob.size
        assert (
            compression_ratio < 0.1
        )  # Should compress to less than 10% of original size
        assert blob.get_content() == content  # Verify data integrity


@pytest.mark.django_db
class TestInboundMessageBlobReference:
    """Internal mail parks the sender's blob on a transient InboundMessage
    while the recipient pipeline runs. The GC must treat that as a live
    reference, or it could reap the bytes out from under delivery."""

    def test_inbound_message_counts_as_a_blob_reference(self):
        """A blob referenced only by an InboundMessage survives GC, and
        becomes collectable once that row is gone."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.blob_gc import gc_orphan_blobs_task

        mailbox = factories.MailboxFactory()
        blob = models.Blob.objects.create_blob(
            content=b"internal mime bytes", content_type="message/rfc822"
        )
        inbound = models.InboundMessage.objects.create(
            mailbox=mailbox, blob=blob, envelope={"origin": "internal"}
        )

        # Referenced solely by the in-flight queue row → still alive.
        assert models.Blob.objects.is_referenced(blob.id) is True
        gc_orphan_blobs_task(mode="full")
        assert models.Blob.objects.filter(id=blob.id).exists()

        # Queue row gone, nothing else references it → collectable.
        inbound.delete()
        assert models.Blob.objects.is_referenced(blob.id) is False
        gc_orphan_blobs_task(mode="full")
        assert not models.Blob.objects.filter(id=blob.id).exists()


@pytest.mark.django_db
class TestCreateBlobPreferOffloaded:
    """``create_blob(prefer_offloaded=True)`` writes bytes straight to the
    object-storage tier (used by bulk imports so archives never park their
    bytes in Postgres), falling back to the PG tier on any storage trouble."""

    CONTENT = b"imported mime bytes " * 64

    def test_lands_in_object_storage(self):
        blob = models.Blob.objects.create_blob(
            content=self.CONTENT,
            content_type="message/rfc822",
            prefer_offloaded=True,
        )
        assert blob.storage_location == enums.BlobStorageLocationChoices.OBJECT_STORAGE
        assert blob.raw_content is None
        assert blob.size == len(self.CONTENT)
        assert blob.size_compressed > 0
        # Round-trips transparently through the tiered read path.
        assert blob.get_content() == self.CONTENT

    def test_default_stays_in_postgres(self):
        blob = models.Blob.objects.create_blob(
            content=self.CONTENT, content_type="message/rfc822"
        )
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None

    def test_dedups_against_existing_pg_blob(self):
        first = models.Blob.objects.create_blob(
            content=self.CONTENT, content_type="message/rfc822"
        )
        second = models.Blob.objects.create_blob(
            content=self.CONTENT,
            content_type="message/rfc822",
            prefer_offloaded=True,
        )
        # Hash-first dedup wins over the tier preference: same row, still PG.
        assert second.id == first.id
        assert second.storage_location == enums.BlobStorageLocationChoices.POSTGRES

    def test_upload_failure_falls_back_to_postgres(self):
        with patch.object(
            TieredStorageService, "upload_blob", side_effect=OSError("s3 down")
        ):
            blob = models.Blob.objects.create_blob(
                content=self.CONTENT,
                content_type="message/rfc822",
                prefer_offloaded=True,
            )
        # Degrades to exactly today's behavior; the offload task retries later.
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None
        assert blob.get_content() == self.CONTENT

    @override_settings(MESSAGES_BLOBS_OFFLOAD_ENABLED=False)
    def test_offload_policy_off_skips_object_tier(self):
        """The operator's master offload switch also governs direct writes:
        prefer_offloaded silently skips when offloading is not in scope."""
        blob = models.Blob.objects.create_blob(
            content=self.CONTENT,
            content_type="message/rfc822",
            prefer_offloaded=True,
        )
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None

    @override_settings(MESSAGES_BLOBS_OFFLOAD_MIN_SIZE=10**9)
    def test_under_offload_min_size_skips_object_tier(self):
        blob = models.Blob.objects.create_blob(
            content=self.CONTENT,
            content_type="message/rfc822",
            prefer_offloaded=True,
        )
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None

    def test_disabled_storage_falls_back_to_postgres(self):
        # Same pattern as the offload-task tests: no configured object
        # storage backend makes ``TieredStorageService.enabled`` False.
        with patch("core.services.tiered_storage.settings") as mock_settings:
            mock_settings.STORAGES = {}
            mock_settings.MESSAGES_BLOBS_ENCRYPT_KEYS = {}
            blob = models.Blob.objects.create_blob(
                content=self.CONTENT,
                content_type="message/rfc822",
                prefer_offloaded=True,
            )
        assert blob.storage_location == enums.BlobStorageLocationChoices.POSTGRES
        assert blob.raw_content is not None
