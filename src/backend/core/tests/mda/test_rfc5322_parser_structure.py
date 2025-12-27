"""
Tests for the JMAP-style body structure parsing algorithm.

These tests verify the implementation of the parseStructure algorithm from
JMAP spec Section 4.1, with our modification that inline media types
are NOT added to attachments (unlike the spec example).
"""

import base64

import pytest
from flanker.mime import create

from core.mda.rfc5322.parser import (
    _is_inline_media_type,
    parse_message_content,
)


class TestIsInlineMediaType:
    """Tests for the _is_inline_media_type helper function."""

    def test_image_types_are_inline(self):
        """Image types should be considered inline media."""
        assert _is_inline_media_type("image/png") is True
        assert _is_inline_media_type("image/jpeg") is True
        assert _is_inline_media_type("image/gif") is True
        assert _is_inline_media_type("image/webp") is True

    def test_audio_types_are_inline(self):
        """Audio types should be considered inline media."""
        assert _is_inline_media_type("audio/mpeg") is True
        assert _is_inline_media_type("audio/ogg") is True
        assert _is_inline_media_type("audio/wav") is True

    def test_video_types_are_inline(self):
        """Video types should be considered inline media."""
        assert _is_inline_media_type("video/mp4") is True
        assert _is_inline_media_type("video/webm") is True
        assert _is_inline_media_type("video/ogg") is True

    def test_text_types_are_not_inline_media(self):
        """Text types should not be considered inline media."""
        assert _is_inline_media_type("text/plain") is False
        assert _is_inline_media_type("text/html") is False

    def test_application_types_are_not_inline_media(self):
        """Application types should not be considered inline media."""
        assert _is_inline_media_type("application/pdf") is False
        assert _is_inline_media_type("application/json") is False
        assert _is_inline_media_type("application/octet-stream") is False


class TestSimpleMessages:
    """Tests for simple (non-multipart) messages."""

    def test_simple_text_plain(self):
        """Simple text/plain message goes to textBody."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Simple Text
Content-Type: text/plain

Hello, world!"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["type"] == "text/plain"
        assert "Hello, world!" in content["textBody"][0]["content"]
        assert len(content["htmlBody"]) == 1  # Copied per JMAP fallback
        assert len(content["attachments"]) == 0

    def test_simple_text_html(self):
        """Simple text/html message goes to htmlBody."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Simple HTML
Content-Type: text/html

<html><body><p>Hello, world!</p></body></html>"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["htmlBody"]) == 1
        assert content["htmlBody"][0]["type"] == "text/html"
        assert "<p>Hello, world!</p>" in content["htmlBody"][0]["content"]
        assert len(content["textBody"]) == 1  # Copied per JMAP fallback
        assert len(content["attachments"]) == 0


class TestMultipartAlternative:
    """Tests for multipart/alternative messages."""

    def test_alternative_text_and_html(self):
        """Both text and HTML parts should be in their respective arrays."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Alternative
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain

Plain text version.

--boundary
Content-Type: text/html

<p>HTML version.</p>

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) == 1
        assert content["textBody"][0]["type"] == "text/plain"
        assert "Plain text version" in content["textBody"][0]["content"]

        assert len(content["htmlBody"]) == 1
        assert content["htmlBody"][0]["type"] == "text/html"
        assert "<p>HTML version.</p>" in content["htmlBody"][0]["content"]

        assert len(content["attachments"]) == 0

    def test_alternative_text_only_copies_to_html(self):
        """If only text/plain in alternative, it should be copied to htmlBody."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Text Only Alternative
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain

Only plain text here.

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) == 1
        assert len(content["htmlBody"]) == 1
        assert content["textBody"][0]["content"] == content["htmlBody"][0]["content"]

    def test_alternative_html_only_copies_to_text(self):
        """If only text/html in alternative, it should be copied to textBody."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: HTML Only Alternative
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/html

<p>Only HTML here.</p>

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) == 1
        assert len(content["htmlBody"]) == 1
        assert content["textBody"][0]["content"] == content["htmlBody"][0]["content"]


class TestMultipartMixed:
    """Tests for multipart/mixed messages with attachments."""

    def test_mixed_with_text_and_attachment(self):
        """Text body and explicit attachment should be separated correctly."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Mixed with Attachment
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body text.

--boundary
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

PDF content here

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) == 1
        assert "Body text" in content["textBody"][0]["content"]

        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["type"] == "application/pdf"
        assert content["attachments"][0]["name"] == "doc.pdf"
        assert content["attachments"][0]["disposition"] == "attachment"

    def test_explicit_attachment_disposition(self):
        """Parts with Content-Disposition: attachment always go to attachments."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Explicit Attachment
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body.

--boundary
Content-Type: image/png
Content-Disposition: attachment; filename="image.png"

PNG data

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["type"] == "image/png"
        assert content["attachments"][0]["disposition"] == "attachment"


class TestMultipartRelated:
    """Tests for multipart/related messages with inline resources."""

    def test_related_first_part_is_body(self):
        """First part in multipart/related should be body content."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Related
Content-Type: multipart/related; boundary="boundary"

--boundary
Content-Type: text/html

<html><body><img src="cid:image1"></body></html>

--boundary
Content-Type: image/png
Content-ID: <image1>

PNG data

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["htmlBody"]) == 1
        assert '<img src="cid:image1">' in content["htmlBody"][0]["content"]

    def test_related_subsequent_parts_are_attachments(self):
        """Parts after the first in multipart/related go to attachments."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Related with Image
Content-Type: multipart/related; boundary="boundary"

--boundary
Content-Type: text/html

<html><body><img src="cid:image1"></body></html>

--boundary
Content-Type: image/png
Content-ID: <image1>

PNG data

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # The image at position > 0 in related should be an attachment
        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["type"] == "image/png"
        assert content["attachments"][0]["cid"] == "image1"


class TestInlineMediaNotInAttachments:
    """
    Tests verifying our key modification: inline media types should NOT
    appear in attachments when they're body content.

    This is our deviation from the JMAP spec example where "C" appears
    in both textBody and attachments.
    """

    def test_inline_image_in_mixed_not_in_attachments(self):
        """
        Inline images in multipart/mixed (not related) should be in body arrays,
        NOT in attachments. This is the key "C" case from JMAP spec.
        """
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Inline Image in Mixed
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Text before image.

--boundary
Content-Type: image/jpeg
Content-Disposition: inline

JPEG data

--boundary
Content-Type: text/plain

Text after image.

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # All three parts should be in textBody (mixed context)
        assert len(content["textBody"]) == 3
        assert content["textBody"][1]["type"] == "image/jpeg"
        # Image content is base64 encoded
        assert content["textBody"][1]["content"]  # Non-empty base64 string

        # The inline image should NOT be in attachments
        assert len(content["attachments"]) == 0

    def test_inline_image_with_cid_not_in_attachments(self):
        """Inline image with Content-ID should not be in attachments."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Inline with CID
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/html

<img src="cid:img1">

--boundary
Content-Type: image/png
Content-ID: <img1>
Content-Disposition: inline

PNG data

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # The image should be in htmlBody, not attachments
        # (it's at position > 0 but in mixed, not related, so it's inline)
        assert len(content["attachments"]) == 0
        assert len(content["htmlBody"]) == 2
        assert content["htmlBody"][1]["type"] == "image/png"
        # Image content is base64 encoded
        assert content["htmlBody"][1]["content"]  # Non-empty base64 string


class TestComplexNestedStructure:
    """
    Tests for the complex nested structure from the JMAP spec example.

    Structure:
    multipart/mixed
      A: text/plain, inline
      multipart/mixed
        multipart/alternative
          multipart/mixed
            B: text/plain, inline
            C: image/jpeg, inline  <- KEY: should NOT be in attachments
            D: text/plain, inline
          multipart/related
            E: text/html
            F: image/jpeg
        G: image/jpeg, attachment
        H: application/x-excel
        J: message/rfc822
      K: text/plain, inline

    Expected with our modification:
    - textBody: [A, B, C, D, K]
    - htmlBody: [A, E, K]
    - attachments: [F, G, H, J]  <- C is NOT here
    """

    @pytest.fixture
    def complex_email(self):
        """Create the complex nested structure from JMAP spec example."""
        return b"""From: sender@example.com
To: recipient@example.com
Subject: Complex Nested Structure
Content-Type: multipart/mixed; boundary="outer"

--outer
Content-Type: text/plain
Content-Disposition: inline

A: First text part

--outer
Content-Type: multipart/mixed; boundary="inner-mixed"

--inner-mixed
Content-Type: multipart/alternative; boundary="alt"

--alt
Content-Type: multipart/mixed; boundary="alt-mixed"

--alt-mixed
Content-Type: text/plain
Content-Disposition: inline

B: Text in alternative

--alt-mixed
Content-Type: image/jpeg
Content-Disposition: inline

C: Inline image data

--alt-mixed
Content-Type: text/plain
Content-Disposition: inline

D: More text after image

--alt-mixed--

--alt
Content-Type: multipart/related; boundary="rel"

--rel
Content-Type: text/html

E: <html><body><img src="cid:f"></body></html>

--rel
Content-Type: image/jpeg
Content-ID: <f>

F: Related image data

--rel--

--alt--

--inner-mixed
Content-Type: image/jpeg
Content-Disposition: attachment; filename="g.jpg"

G: Attachment image data

--inner-mixed
Content-Type: application/x-excel

H: Excel data

--inner-mixed
Content-Type: message/rfc822

From: nested@example.com
Subject: J: Nested message

This is the nested email body.

--inner-mixed--

--outer
Content-Type: text/plain
Content-Disposition: inline

K: Last text part

--outer--"""

    def test_complex_structure_textbody(self, complex_email):
        """textBody should contain A, B, C, D, K."""
        message = create.from_string(complex_email)
        content = parse_message_content(message)

        # Find text parts by their content markers
        text_parts = content["textBody"]
        text_contents = [
            base64.b64decode(p["content"]).decode("utf-8")
            if p["type"] == "image/jpeg"
            else p["content"]
            for p in text_parts
        ]

        assert any("A:" in c for c in text_contents), "A should be in textBody"
        assert any("B:" in c for c in text_contents), "B should be in textBody"
        assert any("C:" in c for c in text_contents), "C should be in textBody"
        assert any("D:" in c for c in text_contents), "D should be in textBody"
        assert any("K:" in c for c in text_contents), "K should be in textBody"

    def test_complex_structure_htmlbody(self, complex_email):
        """htmlBody should contain A, E, K."""
        message = create.from_string(complex_email)
        content = parse_message_content(message)

        html_contents = [p["content"] for p in content["htmlBody"]]

        assert any("A:" in c for c in html_contents), "A should be in htmlBody"
        assert any("E:" in c for c in html_contents), "E should be in htmlBody"
        assert any("K:" in c for c in html_contents), "K should be in htmlBody"

    def test_complex_structure_attachments(self, complex_email):
        """attachments should contain F, G, H, J but NOT C."""
        message = create.from_string(complex_email)
        content = parse_message_content(message)

        # Get all attachment content as strings for checking
        attachment_contents = []
        for att in content["attachments"]:
            if isinstance(att["content"], bytes):
                attachment_contents.append(
                    att["content"].decode("utf-8", errors="replace")
                )
            else:
                attachment_contents.append(str(att["content"]))

        # C should NOT be in attachments - this is the key test
        assert not any("C:" in c for c in attachment_contents), (
            "C should NOT be in attachments (our modification)"
        )

        # F, G, H, J should be in attachments
        assert any("F:" in c for c in attachment_contents), "F should be in attachments"
        assert any("G:" in c for c in attachment_contents), "G should be in attachments"
        assert any("H:" in c for c in attachment_contents), "H should be in attachments"
        # For message/rfc822, check by type since content parsing varies
        attachment_types = [att["type"] for att in content["attachments"]]
        assert (
            any("J:" in c for c in attachment_contents)
            or "message/rfc822" in attachment_types
        ), "J (message/rfc822) should be in attachments"

    def test_complex_structure_attachment_count(self, complex_email):
        """Should have exactly 4 attachments: F, G, H, J."""
        message = create.from_string(complex_email)
        content = parse_message_content(message)

        assert len(content["attachments"]) == 4


class TestFilenameHandling:
    """Tests for filename-based classification."""

    def test_text_with_filename_not_first_is_attachment(self):
        """Text part with filename, not first, should be treated as attachment."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Text with Filename
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body text.

--boundary
Content-Type: text/plain; name="readme.txt"

This is a text file attachment.

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # First text/plain is body
        assert len(content["textBody"]) >= 1
        assert "Body text" in content["textBody"][0]["content"]

        # Second text/plain with filename should be attachment
        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["name"] == "readme.txt"

    def test_first_part_with_filename_is_still_inline(self):
        """First part is inline even if it has a filename."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: First with Filename
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain; name="body.txt"

This is the body.

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # First part should be body even with filename
        assert len(content["textBody"]) == 1
        assert "This is the body" in content["textBody"][0]["content"]
        assert len(content["attachments"]) == 0


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_message(self):
        """Empty message should return empty arrays."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Empty

"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        # Should have some structure but may be empty
        assert "textBody" in content
        assert "htmlBody" in content
        assert "attachments" in content

    def test_deeply_nested_structure(self):
        """Deeply nested multipart structure should be handled."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Deep Nesting
Content-Type: multipart/mixed; boundary="l1"

--l1
Content-Type: multipart/mixed; boundary="l2"

--l2
Content-Type: multipart/mixed; boundary="l3"

--l3
Content-Type: text/plain

Deep nested text.

--l3--

--l2--

--l1--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["textBody"]) >= 1
        assert "Deep nested text" in content["textBody"][0]["content"]

    def test_unknown_content_type_is_attachment(self):
        """Unknown content types should be treated as attachments."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Unknown Type
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body.

--boundary
Content-Type: application/x-custom-type

Custom data.

--boundary--"""
        message = create.from_string(raw_email)
        content = parse_message_content(message)

        assert len(content["attachments"]) == 1
        assert content["attachments"][0]["type"] == "application/x-custom-type"
