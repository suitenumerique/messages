"""Tests for the public attachment-filename helpers in
:mod:`jmap_email.filenames`.

These are opt-in building blocks for consumers: ``parse_email`` itself
only applies the sanitization (a nameless part still reports
``name: null`` per RFC 8621 — synthesizing a placeholder is the
consumer's decision).
"""

import pytest

from jmap_email import guess_mime_extension, sanitize_filename


class TestSanitizeFilename:
    """Contract of ``sanitize_filename``."""

    def test_plain_name_untouched(self):
        assert sanitize_filename("report.pdf") == "report.pdf"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("../../etc/passwd", "passwd", id="posix-traversal"),
            pytest.param("..\\..\\boot.ini", "boot.ini", id="windows-traversal"),
            pytest.param("/var/tmp/evil.sh", "evil.sh", id="absolute-posix"),
            pytest.param("C:\\Users\\x\\evil.exe", "evil.exe", id="absolute-windows"),
        ],
    )
    def test_strips_path_components(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_strips_control_characters(self):
        assert sanitize_filename("inv\r\noice\x00.pdf") == "invoice.pdf"

    def test_replaces_dangerous_characters(self):
        assert sanitize_filename("a<b>c:d.txt") == "a_b_c_d.txt"

    def test_idempotent(self):
        once = sanitize_filename('../we"ird\r\nname.tar.gz')
        assert sanitize_filename(once) == once

    def test_truncates_preserving_extension(self):
        name = sanitize_filename("a" * 300 + ".pdf")
        assert len(name) == 255
        assert name.endswith(".pdf")

    def test_truncates_unreasonable_extension_flat(self):
        # An "extension" longer than 10 chars is not worth preserving.
        name = sanitize_filename("a" * 300 + "." + "b" * 20)
        assert len(name) == 255

    def test_may_return_empty(self):
        # Nothing recognizable survives: callers must supply their own
        # fallback (documented contract).
        assert sanitize_filename("...") == ""

    def test_leading_dot_name_loses_dot(self):
        # ``.gitignore``-style names lose the leading dot to the strip of
        # dot/slash/quote framing characters.
        assert sanitize_filename(".gitignore") == "gitignore"


class TestGuessMimeExtension:
    """Contract of ``guess_mime_extension``."""

    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            pytest.param("application/pdf", ".pdf", id="stdlib-known"),
            pytest.param("image/gif", ".gif", id="stdlib-image"),
            # Overrides where the stdlib table is wrong or silent.
            pytest.param("text/calendar", ".ics", id="calendar-invite"),
            pytest.param("application/xml", ".xml", id="xml-not-xslt"),
            pytest.param("application/x-zip-compressed", ".zip", id="outlook-zip"),
            pytest.param("text/vcard", ".vcf", id="standard-vcard"),
            pytest.param("application/pkcs7-mime", ".p7m", id="smime-message"),
            # The generic unknown type deliberately maps to no extension.
            pytest.param("application/octet-stream", "", id="octet-stream-bare"),
            pytest.param("application/x-unknown", "", id="unknown-type"),
        ],
    )
    def test_extension_lookup(self, content_type, expected):
        assert guess_mime_extension(content_type) == expected

    def test_drops_content_type_parameters(self):
        assert guess_mime_extension('text/calendar; charset="utf-8"') == ".ics"

    def test_case_insensitive(self):
        assert guess_mime_extension("Application/PDF") == ".pdf"

    def test_empty_input(self):
        assert guess_mime_extension("") == ""
