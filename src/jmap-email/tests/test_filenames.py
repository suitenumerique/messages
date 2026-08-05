"""Tests for :func:`jmap_email.sanitize_filename`.

``parse_email`` applies this to every part name it reports; it is public
so consumers can apply it to names that never went through the parser.
Naming a *nameless* part is not covered here — such a part reports
``name: null`` per RFC 8621 and what to display instead is consumer
policy, deliberately outside this library.
"""

import unicodedata

import pytest

from jmap_email import sanitize_filename


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

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The classic attachment spoof: U+202E (right-to-left
            # override) makes "annexe<RLO>gpj.exe" render as
            # "annexe.exe.jpg" — an image to the user, an executable to
            # the OS.
            pytest.param("annexe\u202egpj.exe", "annexegpj.exe", id="bidi-override"),
            pytest.param("a\u200bb.pdf", "ab.pdf", id="zero-width-space"),
            pytest.param("\ufeffreport.pdf", "report.pdf", id="bom"),
            pytest.param("a\u2028b.pdf", "ab.pdf", id="line-separator"),
            pytest.param("a\u2029b.pdf", "ab.pdf", id="paragraph-separator"),
            pytest.param("a\u0085b.pdf", "ab.pdf", id="c1-next-line"),
            pytest.param("a\u00adb.pdf", "ab.pdf", id="soft-hyphen"),
            pytest.param("a\u200db.pdf", "ab.pdf", id="zero-width-joiner"),
        ],
    )
    def test_strips_invisible_characters(self, raw, expected):
        """Anything invisible to the reader but meaningful to the OS goes."""
        assert sanitize_filename(raw) == expected

    def test_invisible_character_cannot_shield_framing(self):
        """Regression: the strip used to run before invisibles were removed,
        so a control character protected a leading ``..`` from it and the
        parent-directory segment survived intact."""
        assert sanitize_filename("\x00..\x00") is None
        assert sanitize_filename("\u202e..\u202e") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Fullwidth forms pass any ASCII-based check, then NFKC folds
            # them to "/" and "." downstream. Normalizing first turns them
            # into a real path, which the basename strip then eats.
            pytest.param("．．／etc／passwd", "passwd", id="fullwidth-traversal"),
            pytest.param("a／b.txt", "b.txt", id="fullwidth-solidus"),
            # U+2026 folds to "..." — a traversal segment in disguise.
            pytest.param("…", None, id="ellipsis-is-dots"),
            # Fullwidth Latin canonicalizes rather than staying exotic.
            pytest.param("ｆｉｌe.txt", "file.txt", id="fullwidth-latin"),
            # NBSP folds to a plain space, which the end-strip then removes.
            pytest.param(" report.pdf ", "report.pdf", id="nbsp-framing"),
        ],
    )
    def test_normalizes_before_sanitizing(self, raw, expected):
        """Compatibility forms are folded first, so a downstream ``NFKC``
        cannot reintroduce a separator we already removed."""
        assert sanitize_filename(raw) == expected

    def test_output_is_nfkc_stable(self):
        """Normalizing the result again must be a no-op."""
        for raw in ("．．／x", "……", "ｆ.txt", "réçu.pdf"):
            out = sanitize_filename(raw)
            if out is not None:
                assert unicodedata.normalize("NFKC", out) == out

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Windows silently drops trailing dots and spaces, so without
            # this "x.exe " and "x.exe" name the same file there while
            # looking different to any allowlist.
            pytest.param("report.pdf ", "report.pdf", id="trailing-space"),
            pytest.param("report.pdf.", "report.pdf", id="trailing-dot"),
            pytest.param("  report.pdf  ", "report.pdf", id="surrounding-space"),
            pytest.param("   ", None, id="whitespace-only"),
        ],
    )
    def test_strips_surrounding_whitespace_and_dots(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_replaces_dangerous_characters(self):
        assert sanitize_filename("a<b>c:d.txt") == "a_b_c_d.txt"

    def test_ntfs_alternate_data_stream_is_defanged(self):
        """``name.txt:payload`` addresses an NTFS stream, not a file."""
        assert sanitize_filename("report.txt:evil.exe") == "report.txt_evil.exe"

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

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(None, id="none"),
            pytest.param("", id="empty"),
            # Nothing recognizable survives sanitizing.
            pytest.param("...", id="dots-only"),
            pytest.param("\x00\x01", id="control-only"),
            pytest.param("/", id="separator-only"),
        ],
    )
    def test_returns_none_when_there_is_no_name(self, raw):
        # ``None``, never ``""``: the empty string is not a filename, and
        # the RFC 8621 field this feeds is ``String | null``.
        assert sanitize_filename(raw) is None

    @pytest.mark.parametrize("max_length", [0, -1, -255])
    def test_returns_none_when_no_room(self, max_length):
        # Regression: a negative ``max_length`` used to reach
        # ``filename[:max_length]`` and slice from the *end*, so
        # ``("ab.pdf", -5)`` answered ``"a"``.
        assert sanitize_filename("ab.pdf", max_length=max_length) is None

    def test_leading_dot_name_loses_dot(self):
        # ``.gitignore``-style names lose the leading dot to the strip of
        # dot/slash/quote framing characters.
        assert sanitize_filename(".gitignore") == "gitignore"

    def test_honours_explicit_max_length(self):
        # Callers whose storage caps names below 255 pass their own limit.
        name = sanitize_filename("a" * 300 + ".pdf", max_length=64)
        assert len(name) == 64
        assert name.endswith(".pdf")
