"""Tests for importer indexing helpers."""
# pylint: disable=redefined-outer-name, no-value-for-parameter

import time
import tracemalloc
from io import BytesIO

import pytest

from core.services.importer.mbox import (
    BARE_FROM_LINE_PATTERN,
    POSTMARK_PATTERN,
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

    def test_extract_date_ignores_the_body(self):
        """The headers end at the first empty line, LF or CRLF: a CRLF pair
        further down an LF message does not pull its body in."""
        raw = (
            b"Subject: s\n\nhello\nDate: Tue, 2 Jan 2001 00:00:00 +0000\n"
            b"x\r\n\r\nmore\n"
        )
        assert extract_date_from_headers(raw) is None


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

    def test_body_line_starting_with_from_is_not_a_separator(self):
        """The oldest bug in the format: an unescaped "From " line in a body.

        Here it even sits after a blank line, the way a paragraph does, so
        position alone does not save us. It reads nothing like a postmark and
        is not followed by a header, so it is not a boundary.
        """
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: One message
From: a@b.com
Date: Mon, 1 Jan 2024 00:00:00 +0000

Hello,

From my point of view this line is prose.

Regards
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1
        file = BytesIO(content)
        file.seek(indices[0].start_byte)
        body = file.read(indices[0].end_byte - indices[0].start_byte + 1)
        assert b"From my point of view" in body

    def test_postmark_mid_paragraph_followed_by_prose_is_not_a_separator(self):
        """No empty line before it: a postmark alone is not enough, the next
        line must be a header too."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: One message
From: a@b.com

Quoting a header here:
From user@example.com Thu Jan 1 00:00:00 2024
and carrying on.
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1

    def test_postmark_after_a_blank_line_followed_by_prose_is_not_a_separator(
        self,
    ):
        """Even where a separator normally stands, a postmark needs a header
        after it."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: One message
From: a@b.com

Quoting a header here:

From user@example.com Thu Jan 1 00:00:00 2024
and carrying on.
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1

    def test_first_postmark_needs_no_header_after_it(self):
        """No message to split yet: rejecting the first separator would drop
        its message."""
        content = (
            b"From user@example.com Thu Jan 1 00:00:00 2024\n"
            b"not a header\nSubject: First\n\nBody one\n\n"
            b"From user@example.com Thu Jan 1 00:00:01 2024\n"
            b"Subject: Second\n\nBody two\n"
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 2
        assert indices[0].start_byte == content.index(b"not a header")

    @pytest.mark.parametrize(
        "separator",
        [b"From someone@example.com", b"From -", b"From MAILER-DAEMON"],
    )
    @pytest.mark.parametrize("subject", [b"Subject: Second", b"Subject:Second"])
    def test_separator_without_a_date_is_still_a_separator(self, separator, subject):
        """A bare sender falls back on the next line being a header, with or
        without a space after its colon."""
        content = (
            separator
            + b"\nSubject: First\nFrom: a@b.com\n\nBody one\n\n"
            + separator
            + b"\n"
            + subject
            + b"\nFrom: c@d.com\n\nBody two\n"
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 2

    @pytest.mark.parametrize(
        ("pattern", "line"),
        [
            (POSTMARK_PATTERN, b"From x Mon " + b"Jan " * 25_000 + b"\n"),
            (BARE_FROM_LINE_PATTERN, b"From " + b"@" * 20_000 + b" x\n"),
        ],
        ids=["postmark", "bare_from"],
    )
    def test_patterns_are_linear_on_long_near_misses(self, pattern, line):
        """Retrying each month, or each '@', made these quadratic or worse."""
        start = time.monotonic()
        assert not pattern.match(line)
        assert time.monotonic() - start < 1

    @pytest.mark.parametrize("before", [b"\n", b""])
    def test_long_postmark_is_not_a_separator(self, before):
        """Real ones are a sender and a date, never a thousand bytes."""
        content = (
            b"From user@example.com Thu Jan 1 00:00:00 2024\n"
            b"Subject: One\n\nBody\n"
            + before
            + b"From user@example.com Thu Jan 1 00:00:00 2024 "
            + b"x" * 2_000
            + b"\nSubject: Two\n"
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1

    def test_long_header_confirms_a_separator(self):
        """Only the head of a long line is read, which is enough to see that
        it is a header."""
        content = (
            b"From - Mon Sep 14 12:00:00 2020\nSubject: a\n\nx\n\n"
            b"From - Mon Sep 14 12:00:01 2020\nSubject: " + b"b" * 5_000 + b"\n"
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 2
        assert indices[1].end_byte == len(content) - 1

    def test_long_line_is_not_buffered(self):
        """A line is not kept whole in memory: an mbox can hold one of any
        length, and appending each chunk to it made the scan quadratic."""
        content = (
            b"From - Mon Sep 14 12:00:00 2020\nSubject: a\n\n"
            + b"x" * (8 << 20)
            + b"\n\nFrom - Mon Sep 14 12:00:01 2020\nSubject: b\n"
        )
        file = BytesIO(content)
        tracemalloc.start()
        try:
            indices = index_mbox_messages(file)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 1 << 20
        assert len(indices) == 2

    @pytest.mark.parametrize(
        "separator",
        [
            b"From user@example.com Sat Mar 13 2021 10:00:00",
            b"From user@example.com 2021-03-13T10:00:00",
            b"From  Mon Jan  1 00:00:00 2024",
            b"From user@example.com\tMon Jan  1 00:00:00 2024",
        ],
    )
    def test_uncommon_postmarks_are_separators(self, separator):
        """Dates in other shapes still read as a postmark, with or without an
        empty line before: rejecting one merges two messages."""
        for before in (b"\n", b""):
            content = (
                b"From user@example.com Thu Jan 1 00:00:00 2024\n"
                b"Subject: First\n\nBody one\n" + before + separator + b"\n"
                b"Subject: Second\n\nBody two\n"
            )
            indices = index_mbox_messages(BytesIO(content))
            assert len(indices) == 2, before

    def test_postmark_mid_paragraph_followed_by_header_is_a_separator(self):
        """Some writers put no blank line between messages. A real postmark
        followed by a header is still a boundary: missing it would merge the
        next message into the body of this one."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: First
From: a@b.com

Body one
From user@example.com Thu Jan 1 00:00:01 2024
Subject: Second
From: c@d.com

Body two
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 2
        file = BytesIO(content)
        file.seek(indices[1].start_byte)
        assert file.read().startswith(b"Subject: Second\n")

    @pytest.mark.parametrize("tail", [b"X:\r\n", b"X:\r\nb", b"X:"])
    def test_empty_header_confirms_the_last_separator(self, tail):
        """A header with an empty value counts, CRLF or not, at the end too."""
        content = (
            b"From - Mon Sep 14 12:00:00 2020\r\nSubject: a\r\n\r\nx\r\n"
            b"From - Mon Sep 14 12:00:01 2020\r\n" + tail
        )
        assert len(index_mbox_messages(BytesIO(content))) == 2

    def test_postmark_on_the_last_line_stays_in_the_message(self):
        """Nothing follows it, so no message starts there: the line belongs to
        the body it ends, instead of being cut off and dropped."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: One message

Body

From user@example.com Thu Jan 1 00:00:00 2024
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1
        assert indices[0].end_byte == len(content) - 1

    @pytest.mark.parametrize(
        "separator",
        [b"From user@example.com Thu Jan 1 00:00:01 2024", b"From else@example.com"],
    )
    def test_last_message_without_final_newline_is_still_split(self, separator):
        """The header after the last separator has no newline, so the scan loop
        never reaches it: the separator must still be confirmed by it."""
        content = (
            b"From user@example.com Thu Jan 1 00:00:00 2024\n"
            b"Subject: First\n"
            b"\n"
            b"Body one\n"
            b"\n" + separator + b"\n"
            b"Subject: Second"
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 2
        file = BytesIO(content)
        file.seek(indices[1].start_byte)
        assert file.read() == b"Subject: Second"

    @pytest.mark.parametrize(
        "paragraph",
        [
            b"From our site\nhttps://example.org\n",
            b"From the desk of Bob\nNote: read this\n",
            b"From me Mon to Fri I work\nok\n",
            b"From me Mon to Fri I work\nPS: ok\n",
            b"From Paris\nTel: 0102030405\n",
            b"From Bob,\nNote: read this\n",
            b"From Monday on, we\nNote: read this\n",
            b"From bob@example.com\nhttps://example.org\n",
        ],
    )
    def test_prose_after_a_blank_line_is_not_a_separator(self, paragraph):
        """Unescaped prose that merely looks like a separator plus a header."""
        content = (
            b"From user@example.com Thu Jan 1 00:00:00 2024\n"
            b"Subject: One message\n"
            b"\n"
            b"Hello,\n"
            b"\n" + paragraph
        )
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1
        assert indices[0].end_byte == len(content) - 1

    @pytest.mark.parametrize(
        "content",
        [
            b"From - Mon Sep 14 12:00:00 2020\n\n",
            b"From - Mon Sep 14 12:00:00 2020\nSubject: a\n\nx\n\n"
            b"From - Mon Sep 14 12:00:01 2020\n\n",
            b"From - Mon Sep 14 12:00:00 2020\nSubject: a\n\nx\n\n"
            b"From - Mon Sep 14 12:00:01 2020\nS: b",
            b"From - Mon Sep 14 12:00:00 2020\nSubject: a\n\nx\n\nFrom b@c\nS: b\n",
            b"From - Mon Sep 14 12:00:00 2020\r\nSubject: a\r\n\r\nx\r\n"
            b"From - Mon Sep 14 12:00:01 2020\r\nX:\r\nb",
            b"From - Mon Sep 14 12:00:00 2020\nS: " + b"a" * 1_500 + b"\n\nx\n\n"
            b"From - Mon Sep 14 12:00:01 2020\nS: " + b"b" * 1_500 + b"\n\n"
            b"From - Mon Sep 14 12:00:02 2020 " + b"c" * 1_500 + b"\nS: c",
        ],
    )
    def test_result_does_not_depend_on_chunk_size(self, content):
        """Where a read ends must not change where a message ends."""
        expected = [
            (i.start_byte, i.end_byte) for i in index_mbox_messages(BytesIO(content))
        ]
        for chunk_size in range(1, 13):
            indices = index_mbox_messages(BytesIO(content), chunk_size=chunk_size)
            assert [(i.start_byte, i.end_byte) for i in indices] == expected

    def test_escaped_postmark_followed_by_header_is_not_a_separator(self):
        """Once escaped, a quoted separator never splits, header after it or not."""
        content = b"""From user@example.com Thu Jan 1 00:00:00 2024
Subject: One message
From: a@b.com

Here is the raw mbox:

>From user@example.com Thu Jan 1 00:00:00 2024
Subject: Quoted
and mid-paragraph:
>From user@example.com Thu Jan 1 00:00:00 2024
Subject: Quoted again
"""
        indices = index_mbox_messages(BytesIO(content))
        assert len(indices) == 1

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
