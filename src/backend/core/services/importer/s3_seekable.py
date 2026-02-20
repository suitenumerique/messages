"""Seekable file-like object backed by S3 range requests."""

import io
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

BUFFER_FORWARD = "forward"
BUFFER_CENTERED = "centered"
BUFFER_NONE = "none"


class S3SeekableReader:  # pylint: disable=too-many-instance-attributes
    """Seekable file-like object that reads from S3 using range requests.

    Maintains a read-ahead buffer (default 100MB) and makes HTTP Range
    requests as the caller seeks through the file. Supports read(), seek(),
    tell() as required by pypff.open_file_object().

    Buffer strategies:
    - "forward": buffer starts at the read position and extends forward.
      Best for purely sequential access.
    - "centered": buffer is centered around the read position.
      Best for bidirectional or random access (e.g. reading mbox messages
      in chronological order from a file stored in reverse order).
    - "none": block-aligned LRU cache. Each read is served from cached
      blocks of ``buffer_size`` bytes; up to ``buffer_count`` blocks are
      kept in an LRU cache. Best for highly random access patterns
      (e.g. pypff traversing a PST file's B-tree structures).
    """

    def __init__(
        self,
        s3_client,
        bucket,
        key,
        buffer_size=100 * 1024 * 1024,
        buffer_count=1,
        buffer_strategy=BUFFER_FORWARD,
    ):
        if buffer_strategy not in (BUFFER_FORWARD, BUFFER_CENTERED, BUFFER_NONE):
            raise ValueError(
                f"Unknown buffer_strategy: {buffer_strategy!r}. "
                f"Use {BUFFER_FORWARD!r}, {BUFFER_CENTERED!r} or {BUFFER_NONE!r}."
            )
        self._s3_client = s3_client
        self._bucket = bucket
        self._key = key
        self._buffer_size = buffer_size
        self._buffer_count = buffer_count
        self._buffer_strategy = buffer_strategy
        self._position = 0

        # Get file size
        head = s3_client.head_object(Bucket=bucket, Key=key)
        self._size = head["ContentLength"]
        self._fetch_count = 0
        self._cache_hit_count = 0

        # Buffer state (forward/centered use a single buffer, none uses LRU)
        self._buffer = b""
        self._buffer_start = 0
        self._cache = OrderedDict()  # block_index -> bytes

        if buffer_strategy == BUFFER_NONE:
            logger.info(
                "S3SeekableReader opened: %s/%s (%d MB, strategy=%s, "
                "block=%d KB, cache=%d blocks = %d MB)",
                bucket,
                key,
                self._size // (1024 * 1024),
                buffer_strategy,
                buffer_size // 1024,
                buffer_count,
                (buffer_size * buffer_count) // (1024 * 1024),
            )
        else:
            logger.info(
                "S3SeekableReader opened: %s/%s (%d MB, strategy=%s, buffer=%d MB)",
                bucket,
                key,
                self._size // (1024 * 1024),
                buffer_strategy,
                buffer_size // (1024 * 1024),
            )

    @property
    def size(self):
        """Return the total size of the S3 object."""
        return self._size

    def read(self, size=-1):
        """Read up to size bytes from the current position."""
        if size == -1 or size is None:
            size = self._size - self._position

        if self._position >= self._size:
            return b""

        # Clamp to remaining bytes
        size = min(size, self._size - self._position)

        if self._buffer_strategy == BUFFER_NONE:
            return self._read_direct(size)

        result = b""
        remaining = size
        while remaining > 0:
            buffer_end = self._buffer_start + len(self._buffer)
            if not (self._buffer and self._buffer_start <= self._position < buffer_end):
                self._fill_buffer(self._position)
                buffer_end = self._buffer_start + len(self._buffer)
            offset = self._position - self._buffer_start
            available = min(remaining, buffer_end - self._position)
            result += self._buffer[offset : offset + available]
            self._position += available
            remaining -= available
        return result

    def _read_direct(self, size):
        """Read using block-aligned LRU cache."""
        if size == 0:
            return b""

        result = b""
        remaining = size
        while remaining > 0:
            block_index = self._position // self._buffer_size
            block_start = block_index * self._buffer_size
            block_data = self._cache_get(block_index)

            offset = self._position - block_start
            available = min(remaining, len(block_data) - offset)
            result += block_data[offset : offset + available]
            self._position += available
            remaining -= available

        return result

    def _cache_get(self, block_index):
        """Get a block from the LRU cache, fetching from S3 if missing."""
        if block_index in self._cache:
            self._cache_hit_count += 1
            self._cache.move_to_end(block_index)
            return self._cache[block_index]

        # Fetch block from S3
        block_start = block_index * self._buffer_size
        block_end = min(block_start + self._buffer_size - 1, self._size - 1)
        self._fetch_count += 1
        logger.debug(
            "S3SeekableReader fetch #%d: block %d (bytes %d-%d, %d KB)",
            self._fetch_count,
            block_index,
            block_start,
            block_end,
            (block_end - block_start + 1) // 1024,
        )
        range_header = f"bytes={block_start}-{block_end}"
        response = self._s3_client.get_object(
            Bucket=self._bucket, Key=self._key, Range=range_header
        )
        data = response["Body"].read()

        # Store in cache, evict oldest if full
        self._cache[block_index] = data
        self._cache.move_to_end(block_index)
        if len(self._cache) > self._buffer_count:
            self._cache.popitem(last=False)

        return data

    def _fill_buffer(self, position):
        """Fetch a buffer_size chunk from S3 around the given position."""
        if self._buffer_strategy == BUFFER_CENTERED:
            half = self._buffer_size // 2
            start = max(0, position - half)
        else:
            start = position
        end = min(start + self._buffer_size - 1, self._size - 1)
        self._fetch_count += 1
        logger.info(
            "S3SeekableReader fetch #%d: bytes %d-%d (%d MB) for read at position %d",
            self._fetch_count,
            start,
            end,
            (end - start + 1) // (1024 * 1024),
            position,
        )
        range_header = f"bytes={start}-{end}"
        response = self._s3_client.get_object(
            Bucket=self._bucket, Key=self._key, Range=range_header
        )
        self._buffer = response["Body"].read()
        self._buffer_start = start

    def seek(self, offset, whence=io.SEEK_SET):
        """Seek to a position in the file."""
        if whence == io.SEEK_SET:
            self._position = offset
        elif whence == io.SEEK_CUR:
            self._position += offset
        elif whence == io.SEEK_END:
            self._position = self._size + offset
        else:
            raise ValueError(f"Invalid whence value: {whence}")

        self._position = max(0, min(self._position, self._size))
        return self._position

    def tell(self):
        """Return the current position."""
        return self._position

    def seekable(self):
        """Return True - this object supports seeking."""
        return True

    def readable(self):
        """Return True - this object supports reading."""
        return True

    def get_size(self):
        """Return the total size of the S3 object. Used by pypff."""
        return self._size

    def close(self):
        """Release the buffer memory."""
        if self._cache:
            total = self._fetch_count + self._cache_hit_count
            logger.info(
                "S3SeekableReader closed: %d fetches, %d cache hits (%d%% hit rate)",
                self._fetch_count,
                self._cache_hit_count,
                (self._cache_hit_count * 100 // total) if total else 0,
            )
            self._cache.clear()
        self._buffer = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
