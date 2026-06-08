"""Tests for ``core.mda.inline_images`` — base64 inline-image extraction."""

# pylint: disable=missing-function-docstring

import base64
import re

import pytest
from jmap_email import compose_email

from core.mda.inline_images import (
    extract_inline_images_html,
    extract_inline_images_text,
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


class TestExtractFromHtml:
    """``extract_inline_images_html`` walks ``<img src="data:...">``."""

    def test_no_images_returns_input_unchanged(self):
        html = "<p>Hello world</p>"
        result_html, images = extract_inline_images_html(html)
        assert result_html == html
        assert not images

    def test_single_image_replaced_with_cid(self):
        html = f'<p>Text</p><img src="data:image/png;base64,{_1PX_PNG_B64}" alt="pic">'
        result_html, images = extract_inline_images_html(html)

        assert len(images) == 1
        assert images[0]["content"] == _1PX_PNG
        assert images[0]["type"] == "image/png"
        assert images[0]["size"] == len(_1PX_PNG)
        assert images[0]["name"].endswith(".png")
        assert f'src="cid:{images[0]["cid"]}"' in result_html
        assert "data:image" not in result_html

    def test_multiple_images_get_distinct_cids(self):
        html = (
            f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
            f'<img src="data:image/jpeg;base64,{_1PX_PNG_B64}">'
        )
        result_html, images = extract_inline_images_html(html)

        assert len(images) == 2
        assert images[0]["cid"] != images[1]["cid"]
        assert images[0]["type"] == "image/png"
        assert images[1]["type"] == "image/jpeg"
        assert "data:image" not in result_html

    def test_existing_cid_reference_not_touched(self):
        html = '<img src="cid:existing-uuid">'
        result_html, images = extract_inline_images_html(html)
        assert result_html == html
        assert not images

    def test_non_image_data_url_not_touched(self):
        html = '<img src="data:text/plain;base64,SGVsbG8=">'
        result_html, images = extract_inline_images_html(html)
        assert result_html == html
        assert not images

    def test_invalid_base64_left_as_is(self):
        html = '<img src="data:image/png;base64,!!!invalid!!!">'
        result_html, images = extract_inline_images_html(html)
        assert result_html == html
        assert not images

    def test_empty_html(self):
        result_html, images = extract_inline_images_html("")
        assert result_html == ""
        assert not images

    def test_mixed_content_preserves_unrelated_urls(self):
        html = (
            f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
            '<img src="https://example.com/photo.jpg">'
            '<img src="cid:already-inline">'
        )
        result_html, images = extract_inline_images_html(html)

        assert len(images) == 1
        assert "https://example.com/photo.jpg" in result_html
        assert "cid:already-inline" in result_html
        assert "data:image" not in result_html

    def test_cid_is_valid_uuid_v4(self):
        html = f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
        _, images = extract_inline_images_html(html)
        assert _UUID_RE.match(images[0]["cid"])


class TestExtractFromText:
    """``extract_inline_images_text`` walks markdown image syntax."""

    def test_no_images_returns_input_unchanged(self):
        text = "Hello world\nThis is a message."
        result, images = extract_inline_images_text(text)
        assert result == text
        assert not images

    def test_single_markdown_image(self):
        text = f"Before\n![logo](data:image/png;base64,{_1PX_PNG_B64})\nAfter"
        result, images = extract_inline_images_text(text)

        assert len(images) == 1
        assert images[0]["content"] == _1PX_PNG
        assert images[0]["type"] == "image/png"
        assert images[0]["size"] == len(_1PX_PNG)
        assert f"![logo](cid:{images[0]['cid']})" in result
        assert "data:image" not in result
        assert "Before" in result
        assert "After" in result

    def test_multiple_markdown_images_get_distinct_cids(self):
        text = (
            f"Start\n![a](data:image/png;base64,{_1PX_PNG_B64})\n"
            f"Middle\n![b](data:image/jpeg;base64,{_1PX_PNG_B64})\nEnd"
        )
        result, images = extract_inline_images_text(text)

        assert len(images) == 2
        assert images[0]["cid"] != images[1]["cid"]
        assert "data:image" not in result

    def test_normal_url_preserved(self):
        text = "![photo](https://example.com/photo.jpg)"
        result, images = extract_inline_images_text(text)
        assert result == text
        assert not images

    def test_residual_html_img_tag_also_replaced(self):
        text = f'Some text <img src="data:image/png;base64,{_1PX_PNG_B64}" alt="pic"> more text'
        result, images = extract_inline_images_text(text)

        assert len(images) == 1
        assert "data:image" not in result
        assert f"cid:{images[0]['cid']}" in result

    def test_empty_text(self):
        result, images = extract_inline_images_text("")
        assert result == ""
        assert not images


class TestDeduplication:
    """Cross-body image deduplication via the ``known_images`` cache."""

    def test_same_image_in_text_and_html_uses_same_cid(self):
        known_images: dict[str, str] = {}

        text = f"![logo](data:image/png;base64,{_1PX_PNG_B64})"
        text_result, text_images = extract_inline_images_text(
            text, known_images=known_images
        )

        html = f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
        html_result, html_images = extract_inline_images_html(
            html, known_images=known_images
        )

        assert len(text_images) == 1
        assert not html_images
        cid = text_images[0]["cid"]
        assert f"![logo](cid:{cid})" in text_result
        assert f'src="cid:{cid}"' in html_result

    def test_different_images_not_deduplicated(self):
        other_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00"
            b"\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01\xe2!\xbc"
            b"3\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        other_b64 = base64.b64encode(other_png).decode()
        known_images: dict[str, str] = {}

        text = f"![a](data:image/png;base64,{_1PX_PNG_B64})"
        _, text_images = extract_inline_images_text(text, known_images=known_images)

        html = f'<img src="data:image/png;base64,{other_b64}">'
        _, html_images = extract_inline_images_html(html, known_images=known_images)

        assert len(text_images) == 1
        assert len(html_images) == 1
        assert text_images[0]["cid"] != html_images[0]["cid"]

    def test_duplicate_within_same_body(self):
        known_images: dict[str, str] = {}

        text = (
            f"![a](data:image/png;base64,{_1PX_PNG_B64})\n"
            f"![b](data:image/png;base64,{_1PX_PNG_B64})"
        )
        result, images = extract_inline_images_text(text, known_images=known_images)

        assert len(images) == 1
        cid = images[0]["cid"]
        assert f"![a](cid:{cid})" in result
        assert f"![b](cid:{cid})" in result


class TestComposerHandoff:
    """The dict shape returned by the extract helpers is the same shape
    ``compose_email(attachments=[…])`` accepts — a caller can splice the
    result straight in (after stamping ``disposition="inline"``)."""

    def test_extracted_image_dict_is_composer_ready(self):
        html_in = f'<img src="data:image/png;base64,{_1PX_PNG_B64}">'
        _, images = extract_inline_images_html(html_in)
        assert images
        img = images[0]
        assert set(img) >= {"content", "type", "name", "cid", "size"}
        # JMAP / composer key name — not the legacy ``content_type``.
        assert "content_type" not in img

        raw = compose_email(
            {
                "from": [{"email": "s@example.com"}],
                "to": [{"email": "r@example.com"}],
                "subject": "t",
                "sentAt": "2026-01-01T00:00:00+00:00",
                "htmlBody": [{"content": f'<img src="cid:{img["cid"]}">'}],
                "attachments": [{**img, "disposition": "inline"}],
            }
        )
        assert f"<{img['cid']}>".encode("ascii") in raw


if __name__ == "__main__":
    pytest.main()
