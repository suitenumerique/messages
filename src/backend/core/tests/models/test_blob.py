"""Tests for blob compression functionality."""

from django.core.exceptions import ValidationError
from django.test import override_settings

import pytest

from core import enums, factories, models


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
