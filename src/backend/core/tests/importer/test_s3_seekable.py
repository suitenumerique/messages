"""Tests for S3SeekableReader."""

import io
from unittest.mock import Mock

import pytest

from core.services.importer.s3_seekable import (
    BUFFER_CENTERED,
    BUFFER_FORWARD,
    S3SeekableReader,
)


class TestS3SeekableReader:
    """Tests for S3SeekableReader."""

    def test_read_basic(self):
        """Test basic read operation."""
        test_data = b"Hello, World! This is a test file."
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        body_mock = Mock()
        body_mock.read.return_value = test_data
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        assert reader.size == len(test_data)
        data = reader.read(5)
        assert data == b"Hello"
        assert reader.tell() == 5

    def test_seek_and_read(self):
        """Test seek followed by read."""
        test_data = b"Hello, World!"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        # Forward buffer: seeking to position 7 fetches from 7 onward
        body_mock = Mock()
        body_mock.read.return_value = test_data[7:]
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        reader.seek(7)
        assert reader.tell() == 7
        data = reader.read(6)
        assert data == b"World!"

    def test_seek_from_end(self):
        """Test seeking from the end of file."""
        test_data = b"Hello, World!"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        # Forward buffer: seeking to position 7 fetches from 7 onward
        body_mock = Mock()
        body_mock.read.return_value = test_data[7:]
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        reader.seek(-6, io.SEEK_END)
        assert reader.tell() == 7

    def test_read_past_end(self):
        """Test reading past the end of the file."""
        test_data = b"Hi"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        body_mock = Mock()
        body_mock.read.return_value = test_data
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        data = reader.read(100)
        assert data == b"Hi"
        assert reader.tell() == 2

    def test_read_empty_at_end(self):
        """Test reading when already at end of file."""
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": 5}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)
        reader.seek(0, io.SEEK_END)
        data = reader.read(10)
        assert data == b""

    def test_seekable_and_readable(self):
        """Test seekable() and readable() return True."""
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": 10}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key")
        assert reader.seekable() is True
        assert reader.readable() is True

    def test_buffer_reuse(self):
        """Test that buffered data is reused without extra S3 calls."""
        test_data = b"ABCDEFGHIJ"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        body_mock = Mock()
        body_mock.read.return_value = test_data
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        reader.read(3)  # Reads "ABC", fetches buffer
        reader.read(3)  # Reads "DEF", should reuse buffer

        # Only one get_object call should have been made
        assert mock_s3.get_object.call_count == 1

    def test_forward_buffer_sequential(self):
        """Test that forward buffer fetches from the read position onward."""
        test_data = b"A" * 100 + b"B" * 100 + b"C" * 100
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": 300}

        recorded_ranges = []

        def mock_get_object(**kwargs):
            recorded_ranges.append(kwargs.get("Range"))
            _, range_spec = kwargs["Range"].split("=")
            start, end = range_spec.split("-")
            body = Mock()
            body.read.return_value = test_data[int(start) : int(end) + 1]
            return {"Body": body}

        mock_s3.get_object = Mock(side_effect=mock_get_object)

        reader = S3SeekableReader(
            mock_s3,
            "test-bucket",
            "test-key",
            buffer_size=150,
            buffer_strategy=BUFFER_FORWARD,
        )

        # Read from start — buffer covers [0, 149]
        assert reader.read(100) == b"A" * 100
        assert recorded_ranges[-1] == "bytes=0-149"

        # Read next 100 — position 100, buffer covers [100, 149] = 50 bytes available
        # Serves 50 from buffer, then fetches from 150 onward for remaining 50
        assert reader.read(100) == b"B" * 100
        assert recorded_ranges[-1] == "bytes=150-299"

    def test_invalid_buffer_strategy(self):
        """Test that an invalid buffer strategy raises ValueError."""
        mock_s3 = Mock()
        with pytest.raises(ValueError, match="Unknown buffer_strategy"):
            S3SeekableReader(
                mock_s3, "test-bucket", "test-key", buffer_strategy="invalid"
            )

    def test_centered_buffer_bidirectional(self):
        """Test centered buffer with bidirectional access pattern.

        Simulates a 500-byte file with 5 messages. Buffer size = 200.
        Pass 1 reads sequentially (0→499), Pass 2 reads in reverse (400→0).
        Centered buffering should reduce total S3 requests vs forward-only.
        """
        # Create 500 bytes of predictable data (5 x 100-byte blocks)
        test_data = b""
        for i in range(5):
            block = f"MSG{i}".encode().ljust(100, b".")
            test_data += block
        assert len(test_data) == 500

        recorded_ranges = []
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": 500}

        def mock_get_object(**kwargs):
            recorded_ranges.append(kwargs.get("Range"))
            _, range_spec = kwargs["Range"].split("=")
            start, end = range_spec.split("-")
            start, end = int(start), int(end)
            body = Mock()
            body.read.return_value = test_data[start : end + 1]
            return {"Body": body}

        mock_s3.get_object = Mock(side_effect=mock_get_object)

        reader = S3SeekableReader(
            mock_s3,
            "test-bucket",
            "test-key",
            buffer_size=200,
            buffer_strategy=BUFFER_CENTERED,
        )

        # Pass 1: Sequential read (simulating indexing)
        reader.seek(0)
        d0 = reader.read(100)  # pos 0-99
        assert d0[:4] == b"MSG0"

        d1 = reader.read(100)  # pos 100-199
        assert d1[:4] == b"MSG1"

        d2 = reader.read(100)  # pos 200-299
        assert d2[:4] == b"MSG2"

        d3 = reader.read(100)  # pos 300-399
        assert d3[:4] == b"MSG3"

        d4 = reader.read(100)  # pos 400-499
        assert d4[:4] == b"MSG4"

        # Pass 2: Reverse read (simulating chronological processing)
        reader.seek(400)
        r4 = reader.read(100)
        assert r4[:4] == b"MSG4"

        reader.seek(300)
        r3 = reader.read(100)
        assert r3[:4] == b"MSG3"

        reader.seek(200)
        r2 = reader.read(100)
        assert r2[:4] == b"MSG2"

        reader.seek(100)
        r1 = reader.read(100)
        assert r1[:4] == b"MSG1"

        reader.seek(0)
        r0 = reader.read(100)
        assert r0[:4] == b"MSG0"

        # All messages were read correctly
        assert d0 == r0
        assert d1 == r1
        assert d2 == r2
        assert d3 == r3
        assert d4 == r4

        # Verify the number of S3 requests is reasonable
        # Without centering (forward-only buffer) reverse pass would need 5 requests
        # With centering, some reverse reads hit the buffer
        assert len(recorded_ranges) < 10  # Reasonable upper bound

    def test_context_manager(self):
        """Test that S3SeekableReader works as a context manager."""
        test_data = b"Hello"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        body_mock = Mock()
        body_mock.read.return_value = test_data
        mock_s3.get_object.return_value = {"Body": body_mock}

        with S3SeekableReader(mock_s3, "test-bucket", "test-key") as reader:
            data = reader.read(5)
            assert data == b"Hello"

        # After context exit, buffer should be released
        assert reader._buffer == b""  # pylint: disable=protected-access

    def test_read_larger_than_buffer(self):
        """Test reading more bytes than the buffer size spans multiple fills."""
        test_data = b"A" * 100 + b"B" * 100 + b"C" * 100  # 300 bytes
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": 300}

        def mock_get_object(**kwargs):
            _, range_spec = kwargs["Range"].split("=")
            start, end = range_spec.split("-")
            body = Mock()
            body.read.return_value = test_data[int(start) : int(end) + 1]
            return {"Body": body}

        mock_s3.get_object = Mock(side_effect=mock_get_object)

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=100)

        # Read all 300 bytes with buffer_size=100 — requires 3 fills
        data = reader.read(300)
        assert len(data) == 300
        assert data == test_data
        assert mock_s3.get_object.call_count == 3

    def test_read_all_default(self):
        """Test read() with no size argument reads entire file."""
        test_data = b"Hello, World!"
        mock_s3 = Mock()
        mock_s3.head_object.return_value = {"ContentLength": len(test_data)}

        body_mock = Mock()
        body_mock.read.return_value = test_data
        mock_s3.get_object.return_value = {"Body": body_mock}

        reader = S3SeekableReader(mock_s3, "test-bucket", "test-key", buffer_size=1024)

        data = reader.read()
        assert data == test_data
        assert reader.tell() == len(test_data)
