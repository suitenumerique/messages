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


class TestGetStmsgHeaders:
    """``get_stmsg_headers`` unions legacy baked ``X-StMsg-*`` bytes with the
    structured ``postmark``; the structured value wins on overlap."""

    def _headers(self, parsed_headers):
        return patch.object(
            models.Message, "get_parsed_data", return_value={"headers": parsed_headers}
        )

    def test_legacy_bytes_only(self):
        """Pre-postmark message: verdicts come from the baked X-StMsg headers."""
        message = models.Message()
        message.postmark = None
        with self._headers([{"name": "X-StMsg-Sender-Auth", "value": "fail"}]):
            assert message.get_stmsg_headers() == {"sender-auth": "fail"}

    def test_postmark_only(self):
        """New message: bytes carry no verdict header; postmark supplies it."""
        message = models.Message()
        message.postmark = {"auth": "none", "processing": "fail"}
        with self._headers([]):
            assert message.get_stmsg_headers() == {
                "sender-auth": "none",
                "processing-failed": "true",
            }

    def test_suspected_spam_marker_projected(self):
        """``postmark["spam"]`` (graded: possible/likely) surfaces verbatim as
        a ``spam`` marker for the inbox banner."""
        message = models.Message()
        message.postmark = {"spam": "likely"}
        with self._headers([]):
            assert message.get_stmsg_headers() == {"spam": "likely"}

    def test_widget_referer_stays_a_header(self):
        """widget-referer is a permanent header, surfaced from bytes alongside
        the structured postmark auth."""
        message = models.Message()
        message.postmark = {"auth": "none"}
        with self._headers([{"name": "X-StMsg-Widget-Referer", "value": "https://x"}]):
            assert message.get_stmsg_headers() == {
                "widget-referer": "https://x",
                "sender-auth": "none",
            }

    def test_postmark_wins_over_legacy(self):
        """On overlap the authoritative postmark value overrides a residual
        (or forged, though stripped at ingest) legacy header."""
        message = models.Message()
        message.postmark = {"auth": "fail"}
        with self._headers([{"name": "X-StMsg-Sender-Auth", "value": "none"}]):
            assert message.get_stmsg_headers()["sender-auth"] == "fail"

    def test_legacy_and_postmark_projections_agree(self):
        """The transition invariant: a legacy-bytes message and a de-baked
        message with the same verdicts are INDISTINGUISHABLE through
        ``get_stmsg_headers`` — same keys AND same values. This is what pins
        ``processing-failed`` to "true" from both sources (the postmark reason
        "fail" must not leak into the projected value).
        """
        legacy = models.Message()
        legacy.postmark = None
        legacy_bytes = [
            {"name": "X-StMsg-Sender-Auth", "value": "fail"},
            {"name": "X-StMsg-Processing-Failed", "value": "true"},
        ]
        with patch.object(
            models.Message, "get_parsed_data", return_value={"headers": legacy_bytes}
        ):
            legacy_out = legacy.get_stmsg_headers()

        new = models.Message()
        new.postmark = {"auth": "fail", "processing": "fail"}
        with self._headers([]):
            new_out = new.get_stmsg_headers()

        assert (
            legacy_out
            == new_out
            == {"sender-auth": "fail", "processing-failed": "true"}
        )
