"""Tests for ``Message.get_parsed_data`` exception handling.

Pins the documented graceful-degradation contract: if either
``Blob.get_content()`` (decompression, decryption, integrity check) or
``jmap_email.parse_email`` raises, the cached result is ``{}`` and
downstream consumers see the same shape they get for a blob-less
Message.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from core import models


@pytest.mark.django_db
class TestMessageGetParsedData:
    """Graceful-degradation contract for ``Message.get_parsed_data``."""

    def test_blob_value_error_returns_empty(self):
        """``Blob.get_content`` raises ``ValueError`` on decompression /
        decryption / integrity-check failure. The method must collapse
        to ``{}`` rather than propagating."""
        fake_blob = MagicMock()
        fake_blob.get_content.side_effect = ValueError("bad blob")
        message = models.Message()
        message.id = "test-id"

        with patch.object(
            models.Message, "blob", new_callable=PropertyMock, return_value=fake_blob
        ):
            assert not message.get_parsed_data()
            # Cached on the instance — second call doesn't re-trigger.
            assert not message.get_parsed_data()
            assert fake_blob.get_content.call_count == 1

    def test_parse_error_returns_empty(self):
        """``parse_email`` returns ``None`` on unparseable bytes. The
        method must collapse to ``{}`` (same shape as the
        ValueError-on-blob-read path)."""
        fake_blob = MagicMock()
        fake_blob.get_content.return_value = b""  # parse_email rejects empty
        message = models.Message()
        message.id = "test-id"

        with patch.object(
            models.Message, "blob", new_callable=PropertyMock, return_value=fake_blob
        ):
            assert not message.get_parsed_data()

    def test_no_blob_returns_empty(self):
        """A blob-less Message (draft skeleton, recovered DB row, …)
        returns ``{}`` without any I/O attempt."""
        message = models.Message()
        with patch.object(
            models.Message, "blob", new_callable=PropertyMock, return_value=None
        ):
            assert not message.get_parsed_data()
