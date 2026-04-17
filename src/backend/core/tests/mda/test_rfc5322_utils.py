"""Tests for core.mda.rfc5322.utils — base64 image extraction utilities."""

# pylint: disable=missing-function-docstring,too-many-public-methods

import base64
import re

from core.mda.rfc5322.utils import (
    extract_base64_images_from_html,
    extract_base64_images_from_text,
    remove_mime_headers,
)

# A tiny valid 1x1 red PNG (68 bytes)
_1PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
_1PX_PNG_B64 = base64.b64encode(_1PX_PNG).decode()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class TestExtractBase64Images:
    """Tests for extract_base64_images_from_html()."""

    def test_extract_base64_html_no_images(self):
        """HTML without base64 images is returned unchanged."""
        html = "<p>Hello world</p>"
        result_html, images = extract_base64_images_from_html(html)
        assert result_html == html
        assert len(images) == 0

    def test_extract_base64_html_single_image(self):
        """A single base64 image is extracted and replaced with a CID."""
        html = f'<p>Text</p><img src="data:image/png;base64,{_1PX_PNG_B64}" alt="pic">'
        result_html, images = extract_base64_images_from_html(html)

        assert len(images) == 1
        assert images[0]["content"] == _1PX_PNG
        assert images[0]["content_type"] == "image/png"
        assert images[0]["size"] == len(_1PX_PNG)
        assert images[0]["name"].endswith(".png")

        # The HTML should reference the CID
        assert f'src="cid:{images[0]["cid"]}"' in result_html
        assert "data:image" not in result_html

    def test_extract_base64_html_multiple_images(self):
        """Multiple base64 images are each extracted with unique CIDs."""
        html = (
            f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
            f'<img src="data:image/jpeg;base64,{_1PX_PNG_B64}">'
        )
        result_html, images = extract_base64_images_from_html(html)

        assert len(images) == 2
        assert images[0]["cid"] != images[1]["cid"]
        assert images[0]["content_type"] == "image/png"
        assert images[1]["content_type"] == "image/jpeg"
        assert "data:image" not in result_html

    def test_extract_base64_html_existing_cid_not_touched(self):
        """Images already using cid: references are not modified."""
        html = '<img src="cid:existing-uuid">'
        result_html, images = extract_base64_images_from_html(html)
        assert result_html == html
        assert len(images) == 0

    def test_extract_base64_html_non_image_data_url_not_touched(self):
        """Non-image data URLs (e.g. text/plain) are left as-is."""
        html = '<img src="data:text/plain;base64,SGVsbG8=">'
        result_html, images = extract_base64_images_from_html(html)
        assert result_html == html
        assert len(images) == 0

    def test_extract_base64_html_invalid_left_as_is(self):
        """Invalid base64 data leaves the img tag unchanged."""
        html = '<img src="data:image/png;base64,!!!invalid!!!">'
        result_html, images = extract_base64_images_from_html(html)
        assert result_html == html
        assert len(images) == 0

    def test_extract_base64_html_empty(self):
        """Empty string returns empty string and no images."""
        result_html, images = extract_base64_images_from_html("")
        assert result_html == ""
        assert len(images) == 0

    def test_extract_base64_html_mixed_content(self):
        """HTML with both base64 images and regular URLs."""
        html = (
            f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
            '<img src="https://example.com/photo.jpg">'
            '<img src="cid:already-inline">'
        )
        result_html, images = extract_base64_images_from_html(html)

        assert len(images) == 1
        assert "https://example.com/photo.jpg" in result_html
        assert "cid:already-inline" in result_html
        assert "data:image" not in result_html

    def test_extract_base64_html_cid_is_valid_uuid(self):
        """Generated CIDs are valid UUID4 strings."""
        html = f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
        _, images = extract_base64_images_from_html(html)
        assert _UUID_RE.match(images[0]["cid"])


class TestExtractBase64ImagesFromText:
    """Tests for extract_base64_images_from_text()."""

    def test_extract_base64_text_no_images(self):
        """Plain text without base64 images is returned unchanged."""
        text = "Hello world\nThis is a message."
        result, images = extract_base64_images_from_text(text)
        assert result == text
        assert len(images) == 0

    def test_extract_base64_text_single_md_image(self):
        """A single markdown base64 image is replaced with a CID reference."""
        text = f"Before\n![logo](data:image/png;base64,{_1PX_PNG_B64})\nAfter"
        result, images = extract_base64_images_from_text(text)

        assert len(images) == 1
        assert images[0]["content"] == _1PX_PNG
        assert images[0]["content_type"] == "image/png"
        assert images[0]["size"] == len(_1PX_PNG)
        assert f"![logo](cid:{images[0]['cid']})" in result
        assert "data:image" not in result
        assert "Before" in result
        assert "After" in result

    def test_extract_base64_text_multiple_md_images(self):
        """Multiple markdown base64 images are all replaced with unique CIDs."""
        text = (
            f"Start\n![a](data:image/png;base64,{_1PX_PNG_B64})\n"
            f"Middle\n![b](data:image/jpeg;base64,{_1PX_PNG_B64})\nEnd"
        )
        result, images = extract_base64_images_from_text(text)

        assert len(images) == 2
        assert images[0]["cid"] != images[1]["cid"]
        assert "data:image" not in result
        assert "Start" in result
        assert "Middle" in result
        assert "End" in result

    def test_extract_base64_text_preserves_normal_urls(self):
        """Markdown images with normal URLs are preserved."""
        text = "![photo](https://example.com/photo.jpg)"
        result, images = extract_base64_images_from_text(text)
        assert result == text
        assert len(images) == 0

    def test_extract_base64_text_mixed_content(self):
        """Only base64 images are replaced; normal content and URLs remain."""
        text = (
            f"Hello\n![inline](data:image/png;base64,{_1PX_PNG_B64})\n"
            "![photo](https://example.com/photo.jpg)\nBye"
        )
        result, images = extract_base64_images_from_text(text)

        assert len(images) == 1
        assert "data:image" not in result
        assert "![photo](https://example.com/photo.jpg)" in result
        assert "Hello" in result
        assert "Bye" in result

    def test_extract_base64_text_html_img_tag(self):
        """Residual HTML img tags with base64 data are also replaced with CIDs."""
        text = f'Some text <img src="data:image/png;base64,{_1PX_PNG_B64}" alt="pic"> more text'
        result, images = extract_base64_images_from_text(text)

        assert len(images) == 1
        assert "data:image" not in result
        assert f"cid:{images[0]['cid']}" in result
        assert "Some text" in result
        assert "more text" in result

    def test_extract_base64_text_empty_string(self):
        """Empty string returns empty string and no images."""
        result, images = extract_base64_images_from_text("")
        assert result == ""
        assert len(images) == 0

    def test_extract_base64_text_cid_is_valid_uuid(self):
        """Generated CIDs are valid UUID4 strings."""
        text = f"![img](data:image/png;base64,{_1PX_PNG_B64})"
        _, images = extract_base64_images_from_text(text)
        assert _UUID_RE.match(images[0]["cid"])


class TestDeduplication:
    """Tests for cross-body image deduplication via known_images."""

    def test_dedup_base64_same_image_in_text_and_html_uses_same_cid(self):
        """The same base64 image in text and HTML produces a single attachment."""
        known_images: dict[str, str] = {}

        text = f"![logo](data:image/png;base64,{_1PX_PNG_B64})"
        text_result, text_images = extract_base64_images_from_text(
            text, known_images=known_images
        )

        html = f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
        html_result, html_images = extract_base64_images_from_html(
            html, known_images=known_images
        )

        # Only one new image should have been created (from the text pass)
        assert len(text_images) == 1
        assert len(html_images) == 0

        # Both bodies reference the same CID
        cid = text_images[0]["cid"]
        assert f"![logo](cid:{cid})" in text_result
        assert f'src="cid:{cid}"' in html_result

    def test_dedup_base64_different_images_not_deduplicated(self):
        """Different images produce separate attachments even with known_images."""
        # A 1x1 white PNG (different from _1PX_PNG)
        other_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00"
            b"\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01\xe2!\xbc"
            b"3\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        other_b64 = base64.b64encode(other_png).decode()
        known_images: dict[str, str] = {}

        text = f"![a](data:image/png;base64,{_1PX_PNG_B64})"
        _, text_images = extract_base64_images_from_text(
            text, known_images=known_images
        )

        html = f'<img src="data:image/png;base64,{other_b64}">'
        _, html_images = extract_base64_images_from_html(
            html, known_images=known_images
        )

        assert len(text_images) == 1
        assert len(html_images) == 1
        assert text_images[0]["cid"] != html_images[0]["cid"]

    def test_dedup_base64_duplicate_within_same_body(self):
        """The same image appearing twice in one body is also deduplicated."""
        known_images: dict[str, str] = {}

        text = (
            f"![a](data:image/png;base64,{_1PX_PNG_B64})\n"
            f"![b](data:image/png;base64,{_1PX_PNG_B64})"
        )
        result, images = extract_base64_images_from_text(
            text, known_images=known_images
        )

        assert len(images) == 1
        cid = images[0]["cid"]
        assert f"![a](cid:{cid})" in result
        assert f"![b](cid:{cid})" in result


class TestRemoveMimeHeaders:
    """Tests for remove_mime_headers()."""

    BASE_HEAD = (
        b"Received: from mta.example.com (mta.example.com [10.0.0.1])\r\n"
        b"\tby relay.example.com with SMTP id abc123\r\n"
        b"From: sender@example.com\r\n"
        b"To: rcpt@example.com\r\n"
        b"Subject: Hi\r\n"
    )
    BODY = b"\r\n\r\nThis is the body.\r\n"

    # --- Empty filters --------------------------------------------------

    def test_no_filters_returns_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        assert remove_mime_headers(raw) is raw

    def test_empty_iterables_return_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        assert remove_mime_headers(raw, prefixes=[], names=[]) is raw

    def test_no_match_returns_input_unchanged(self):
        raw = self.BASE_HEAD + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-no-such-"], names=["x-also-no"])
        assert out is raw

    # --- Exact name matching --------------------------------------------

    def test_exact_name_match(self):
        raw = b"From: a@b\r\nX-Custom: value\r\nSubject: hi\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["X-Custom"])
        assert b"X-Custom" not in out
        assert b"From: a@b" in out
        assert b"Subject: hi" in out

    def test_exact_name_case_insensitive(self):
        raw = b"X-CuStOm: value\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["x-custom"])
        assert b"X-CuStOm" not in out

    def test_exact_name_does_not_match_prefix(self):
        """names=['x-foo'] must not strip 'x-foo-bar'."""
        raw = b"X-Foo-Bar: keep\r\nX-Foo: drop\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["x-foo"])
        assert b"X-Foo-Bar: keep" in out
        assert b"\r\nX-Foo:" not in b"\r\n" + out
        assert b"X-Foo: drop" not in out

    def test_multiple_exact_names(self):
        raw = b"X-A: 1\r\nX-B: 2\r\nX-C: 3\r\n" + self.BODY
        out = remove_mime_headers(raw, names=["X-A", "X-C"])
        assert b"X-A:" not in out
        assert b"X-C:" not in out
        assert b"X-B: 2" in out

    # --- Prefix matching -------------------------------------------------

    def test_prefix_match(self):
        raw = (
            b"X-StMsg-Sender-Auth: pass\r\n"
            b"X-StMsg-Widget-Referer: evil.com\r\n"
            b"From: a@b\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-" not in out
        assert b"From: a@b" in out

    def test_prefix_case_insensitive(self):
        raw = b"x-STmsg-Foo: drop\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["X-StMsg-"])
        assert b"x-STmsg-Foo" not in out

    def test_multiple_prefixes(self):
        raw = b"X-Spam-Score: 5\r\nX-StMsg-Foo: 1\r\nX-Other: keep\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-spam-", "x-stmsg-"])
        assert b"X-Spam-Score" not in out
        assert b"X-StMsg-Foo" not in out
        assert b"X-Other: keep" in out

    def test_prefix_does_not_match_unrelated_name(self):
        raw = b"Subject: x-stmsg-not-a-header\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert out is raw

    # --- Combined filters ------------------------------------------------

    def test_prefixes_and_names_combined(self):
        raw = b"X-StMsg-Foo: 1\r\nDKIM-Signature: v=1; ...\r\nFrom: a@b\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"], names=["DKIM-Signature"])
        assert b"X-StMsg-Foo" not in out
        assert b"DKIM-Signature" not in out
        assert b"From: a@b" in out

    # --- Folding (RFC 5322 §2.2.3) --------------------------------------

    def test_folded_target_header_dropped_completely(self):
        raw = (
            b"X-StMsg-Widget-Referer: http://evil.com/\r\n"
            b" /some/very/long/path\r\n"
            b"\t?that_continues=yes\r\n"
            b"From: a@b\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"evil.com" not in out
        assert b"some/very/long/path" not in out
        assert b"that_continues" not in out
        assert b"From: a@b" in out

    def test_folded_unrelated_header_preserved(self):
        raw = (
            b"Subject: a very long\r\n"
            b" subject line with\r\n"
            b"\tmultiple folds\r\n"
            b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"a very long" in out
        assert b" subject line with" in out
        assert b"\tmultiple folds" in out
        assert b"X-StMsg-Sender-Auth" not in out

    def test_continuation_after_drop_then_real_header(self):
        """A real header following a dropped folded header is preserved."""
        raw = b"X-StMsg-Foo: a\r\n continued\r\nReal-Header: kept\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Foo" not in out
        assert b"continued" not in out
        assert b"Real-Header: kept" in out

    # --- Line endings ---------------------------------------------------

    def test_lf_only_line_endings(self):
        raw = b"X-StMsg-Sender-Auth: pass\nFrom: a@b\n\nbody"
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Sender-Auth" not in out
        assert b"From: a@b" in out
        assert out.endswith(b"\n\nbody")

    # --- Body / boundary semantics --------------------------------------

    def test_body_left_byte_identical(self):
        body = b"\r\n\r\nLine1\r\nX-StMsg-Foo: looks like a header but it's body\r\n"
        raw = b"From: a@b\r\nX-StMsg-Foo: bar\r\n" + body
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert out.endswith(body)
        head = out.split(b"\r\n\r\n", 1)[0]
        assert b"X-StMsg-" not in head

    def test_retained_headers_byte_identical(self):
        raw = self.BASE_HEAD + b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        # Each retained header line must appear unchanged in the output.
        for line in self.BASE_HEAD.splitlines(keepends=True):
            assert line in out

    def test_no_body_separator(self):
        raw = b"X-StMsg-Sender-Auth: pass\r\nFrom: a@b\r\n"
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Sender-Auth" not in out
        assert b"From: a@b" in out

    def test_empty_input(self):
        assert remove_mime_headers(b"", prefixes=["x-stmsg-"]) == b""

    # --- Malformed input -------------------------------------------------

    def test_malformed_line_without_colon_preserved(self):
        raw = (
            b"From: a@b\r\n"
            b"some-malformed-line-no-colon\r\n"
            b"X-StMsg-Sender-Auth: pass\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"some-malformed-line-no-colon" in out
        assert b"X-StMsg-Sender-Auth" not in out

    def test_malformed_line_resets_drop_state(self):
        """A non-continuation line clears any in-progress drop, even if malformed."""
        raw = (
            b"X-StMsg-Foo: a\r\n"
            b"malformed-without-colon\r\n"
            b" indented-after-malformed\r\n" + self.BODY
        )
        out = remove_mime_headers(raw, prefixes=["x-stmsg-"])
        assert b"X-StMsg-Foo" not in out
        assert b"malformed-without-colon" in out
        # The indented line is a "continuation" of the malformed line — kept.
        assert b" indented-after-malformed" in out
