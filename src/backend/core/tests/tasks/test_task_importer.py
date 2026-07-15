"""Tests for importer indexing helpers."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

from io import BytesIO

import pytest

from core.services.importer.mbox import (
    extract_date_from_headers,
    index_mbox_messages,
)


def test_index_mbox_messages_calls_on_progress(sample_mbox_content):
    """index_mbox_messages beats on_progress during the scan so the runner can
    renew its lock through a long full-file index (C1). A small chunk size
    forces several reads."""
    calls = []
    index_mbox_messages(
        BytesIO(sample_mbox_content),
        chunk_size=16,
        on_progress=lambda: calls.append(1),
    )
    # chunk_size=16 over a multi-hundred-byte mbox forces several reads, so a
    # single beat would mean the per-chunk hook regressed to per-file.
    assert len(calls) > 1, "on_progress should beat once per chunk read"


@pytest.fixture
def sample_mbox_content():
    """Create a sample MBOX file content with dates and message IDs.

    Messages are intentionally out of chronological order to test sorting.
    """
    return b"""From user@example.com Thu Jan 3 00:00:00 2024
Message-ID: <msg3@example.com>
Subject: Test Message 3
From: sender3@example.com
To: recipient@example.com
Date: Wed, 3 Jan 2024 00:00:00 +0000

This is test message 3.

From user@example.com Thu Jan 1 00:00:00 2024
Message-ID: <msg1@example.com>
Subject: Test Message 1
From: sender1@example.com
To: recipient@example.com
Date: Mon, 1 Jan 2024 00:00:00 +0000

This is test message 1.

From user@example.com Thu Jan 2 00:00:00 2024
Message-ID: <msg2@example.com>
Subject: Test Message 2
From: sender2@example.com
To: recipient@example.com
Date: Tue, 2 Jan 2024 00:00:00 +0000
In-Reply-To: <msg1@example.com>
References: <msg1@example.com>

This is test message 2.
"""


@pytest.mark.django_db
class TestExtractDateFromHeaders:
    """Test the extract_date_from_headers function."""

    def test_extract_valid_date(self):
        """Test extracting a valid RFC 5322 date."""
        raw = b"From: a@b.com\r\nDate: Mon, 1 Jan 2024 00:00:00 +0000\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_extract_no_date_header(self):
        """Test message without Date header returns None."""
        raw = b"From: a@b.com\r\nSubject: Test\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is None

    def test_extract_invalid_date(self):
        """Test message with invalid date returns None."""
        raw = b"From: a@b.com\r\nDate: not-a-date\r\n\r\nBody"
        result = extract_date_from_headers(raw)
        assert result is None

    def test_extract_date_only_reads_headers(self):
        """Test that only headers are parsed, not body."""
        raw = b"Subject: Test\r\n\r\nDate: Mon, 1 Jan 2024 00:00:00 +0000"
        result = extract_date_from_headers(raw)
        assert result is None  # Date in body should be ignored

    def test_extract_date_lf_only(self):
        """Test with LF-only line endings."""
        raw = b"From: a@b.com\nDate: Tue, 2 Jan 2024 10:00:00 +0000\n\nBody"
        result = extract_date_from_headers(raw)
        assert result is not None
        assert result.day == 2


@pytest.mark.django_db
class TestIndexMboxMessages:
    """Test the index_mbox_messages function."""

    def test_index_basic(self, sample_mbox_content):
        """Test basic indexing of mbox content."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        assert len(indices) == 3

    def test_index_has_dates(self, sample_mbox_content):
        """Test that dates are extracted during indexing."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        # All 3 messages have dates
        for idx in indices:
            assert idx.date is not None

    def test_index_byte_offsets(self, sample_mbox_content):
        """Test that byte offsets allow correct message extraction."""
        file = BytesIO(sample_mbox_content)
        indices = index_mbox_messages(file)
        # Each message should be extractable
        for idx in indices:
            file.seek(idx.start_byte)
            content = file.read(idx.end_byte - idx.start_byte + 1)
            assert b"Subject: " in content

    def test_index_empty_file(self):
        """Test indexing an empty file."""
        file = BytesIO(b"")
        indices = index_mbox_messages(file)
        assert len(indices) == 0

    def test_index_no_from_lines(self):
        """Test indexing content without From separators."""
        file = BytesIO(b"Subject: Test\nFrom: a@b.com\n\nBody\n")
        indices = index_mbox_messages(file)
        assert len(indices) == 0

    def test_index_single_message(self):
        """Test indexing a single message."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: Single
From: a@b.com
Date: Mon, 1 Jan 2024 00:00:00 +0000

Body
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)
        assert len(indices) == 1
        assert indices[0].date is not None

    def test_index_message_without_date(self):
        """Test indexing a message without a Date header."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: No Date
From: a@b.com

Body
"""
        file = BytesIO(content)
        indices = index_mbox_messages(file)
        assert len(indices) == 1
        assert indices[0].date is None
