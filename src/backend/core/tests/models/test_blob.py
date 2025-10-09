"""Tests for blob compression functionality."""

from django.core.exceptions import ValidationError

import pytest

from core import enums, factories


@pytest.mark.django_db
class TestBlobCompression:
    """Test suite for blob compression functionality."""

    def test_blob_no_compression(self):
        """Test blob creation without compression."""
        content = b"Hello World" * 1000  # Create some content to compress
        mailbox = factories.MailboxFactory()

        # Create blob without compression
        blob = mailbox.create_blob(
            content=content,
            content_type="text/plain",
            compression=enums.CompressionTypeChoices.NONE,
        )

        # Check sizes
        assert blob.size == len(content)  # Original size
        assert blob.size_compressed == len(
            content
        )  # Should be the same as no compression
        assert blob.compression == enums.CompressionTypeChoices.NONE
        assert blob.get_content() == content  # Content should be unchanged

    def test_blob_zstd_compression(self):
        """Test blob creation with ZSTD compression."""
        content = b"Hello World" * 1000  # Create some content that will compress well
        mailbox = factories.MailboxFactory()

        # Create blob with ZSTD compression
        blob = mailbox.create_blob(
            content=content,
            content_type="text/plain",
            compression=enums.CompressionTypeChoices.ZSTD,
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
            mailbox.create_blob(
                content=b"",
                content_type="text/plain",
                compression=enums.CompressionTypeChoices.ZSTD,
            )

    def test_blob_large_content_compression(self):
        """Test compression with large content."""
        # Create a large content that should compress well
        content = b"A" * 1000000  # 1MB of repeating data
        mailbox = factories.MailboxFactory()

        blob = mailbox.create_blob(
            content=content,
            content_type="text/plain",
            compression=enums.CompressionTypeChoices.ZSTD,
        )

        # Verify compression ratio is significant
        compression_ratio = blob.size_compressed / blob.size
        assert (
            compression_ratio < 0.1
        )  # Should compress to less than 10% of original size
        assert blob.get_content() == content  # Verify data integrity
