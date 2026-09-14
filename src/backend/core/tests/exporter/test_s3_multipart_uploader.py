"""Tests for the streaming gzip uploader behind mailbox exports.

The export of a large mailbox is the only thing that ever crosses the part
threshold, so these drive the uploader directly with its sizes turned down.
They still need real parts: object storage (RustFS here, S3 in production)
rejects a non-final part under 5MB with EntityTooSmall, so the payload is
incompressible random bytes sized to produce two genuine parts.
"""
# pylint: disable=redefined-outer-name

import gzip
import os
from io import BytesIO

from django.core.files.storage import storages

import pytest

from core.services.exporter.tasks import MIN_PART_SIZE, S3MultipartGzipUploader


@pytest.fixture
def uploader_key():
    """A key in the imports bucket, removed afterwards."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    key = "tests/multipart-uploader.mbox.gz"
    yield storage, s3_client, key
    try:
        s3_client.delete_object(Bucket=storage.bucket_name, Key=key)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _read_object(storage, s3_client, key):
    return s3_client.get_object(Bucket=storage.bucket_name, Key=key)["Body"].read()


def test_multipart_upload_roundtrips_every_byte(uploader_key):
    """Several parts concatenate into one gzip file holding exactly what was written.

    Each part is closed as a complete gzip stream, so the object is a
    multi-stream gzip file. Nothing verifies that the parts line up until
    something decompresses the whole thing.
    """
    storage, s3_client, key = uploader_key

    # Incompressible, so the *compressed* buffer (what the threshold measures)
    # grows with the data. Sized to cross chunk_size + min_part_size once and
    # leave a real remainder for the final part.
    chunks = [os.urandom(256 * 1024) for _ in range(48)]  # 12MB
    written = b"".join(chunks)

    with S3MultipartGzipUploader(
        s3_client,
        storage.bucket_name,
        key,
        chunk_size=MIN_PART_SIZE,
        min_part_size=MIN_PART_SIZE,
    ) as uploader:
        for chunk in chunks:
            uploader.write(chunk)

    assert len(uploader.parts) > 1, "payload did not cross the part threshold"
    assert [part["PartNumber"] for part in uploader.parts] == list(
        range(1, len(uploader.parts) + 1)
    )

    body = _read_object(storage, s3_client, key)
    assert gzip.open(BytesIO(body), "rb").read() == written


def test_no_writes_leaves_a_valid_empty_gzip(uploader_key):
    """An export of an empty mailbox still has to produce a readable file."""
    storage, s3_client, key = uploader_key

    with S3MultipartGzipUploader(s3_client, storage.bucket_name, key):
        pass

    body = _read_object(storage, s3_client, key)
    assert gzip.open(BytesIO(body), "rb").read() == b""


def test_error_inside_the_context_leaves_no_object(uploader_key):
    """A failed export aborts the upload instead of leaking parts on S3."""
    storage, s3_client, key = uploader_key

    with pytest.raises(RuntimeError):
        with S3MultipartGzipUploader(s3_client, storage.bucket_name, key) as uploader:
            uploader.write(b"partial content\n")
            raise RuntimeError("export blew up")

    with pytest.raises(s3_client.exceptions.NoSuchKey):
        _read_object(storage, s3_client, key)
