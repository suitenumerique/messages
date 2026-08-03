"""
Fuzzing tests for the attachment-filename sanitizer.

These tests use hypothesis for property-based testing to pin the
structural contract of :func:`jmap_email.sanitize_filename` on arbitrary
and adversarial input: it never raises, it never returns the empty
string, its output fits the length budget, and nothing that could
redirect a write or misrepresent the name — path separators, traversal
segments, invisible characters — survives it.

Run with: pytest -m fuzz tests/test_filenames_fuzz.py
Or: make fuzz-jmap-email
"""

import os
import unicodedata

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jmap_email import sanitize_filename

# Intensive fuzzing settings
FUZZ_SETTINGS = {
    # Override for a deeper soak: FUZZ_EXAMPLES=100000 make fuzz-jmap-email
    "max_examples": int(os.environ.get("FUZZ_EXAMPLES", "10000")),
    "deadline": None,  # No time limit per example
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    # Phases are Hypothesis's defaults on purpose. ``shrink`` and
    # ``explain`` cost nothing on a green run — they only engage once a
    # failure exists, which is exactly when you want a minimal example
    # rather than the raw generated blob. ``reuse`` replays a stored
    # failure until it is fixed, which is what makes an intermittent
    # find reproducible; it needs ``.hypothesis`` to survive the
    # container, so compose mounts it.
}

# Fragments biased toward what the sanitizer reacts to, so the fuzz
# doesn't spend its whole budget on inert unicode: traversal segments in
# both separator dialects, drive letters, the characters a filesystem
# chokes on, control bytes, and the framing characters stripped from the
# ends.
_hostile_fragment = st.one_of(
    st.text(max_size=20),
    st.sampled_from(
        [
            "../",
            "..\\",
            "..",
            ".",
            "/",
            "\\",
            "//",
            "C:",
            "C:\\",
            "\\\\server\\share\\",
            "/etc/passwd",
            "%2e%2e%2f",
            "\x00",
            "\x01",
            "\x1f",
            "\x7f",
            "\r",
            "\n",
            "\r\n",
            "\t",
            "\u0085",
            "\u2028",
            "\u2029",
            "\u202e",
            "\u200b",
            "\u200d",
            "\ufeff",
            "\u00ad",
            "\uff0f",  # fullwidth solidus  → "/" under NFKC
            "\uff0e",  # fullwidth full stop → "." under NFKC
            "\uff3c",  # fullwidth reverse solidus → "\\" under NFKC
            "\u2026",  # horizontal ellipsis → "..." under NFKC
            "\u00a0",  # no-break space → " " under NFKC
            "\u2044",  # fraction slash
            "CON",
            "NUL",
            "COM1",
            "LPT1",
            "x\u0301",  # combining acute — truncation must not orphan it
            "<",
            ">",
            ":",
            '"',
            "|",
            "?",
            "*",
            ".pdf",
            ".tar.gz",
            ".gitignore",
            "réçu",
            "\ud83d\ude00",
            " ",
        ]
    ),
)

hostile_names = st.lists(_hostile_fragment, max_size=30).map("".join)

# Both separator dialects are stripped, on every platform — the wire
# doesn't tell us which OS produced the name.
SEPARATORS = ("/", "\\")

# Everything invisible: controls, bidi/format characters, the Unicode
# line separators, lone surrogates. Removed outright rather than
# replaced — they have no display semantics but plenty of OS semantics.
INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})


@pytest.mark.fuzz
class TestSanitizeFilenameFuzz:
    """Structural contract of ``sanitize_filename`` under fuzzing."""

    @settings(**FUZZ_SETTINGS)
    @given(raw=st.one_of(st.text(max_size=1000), hostile_names, st.none()))
    def test_never_raises_and_never_returns_empty(self, raw):
        """Any input yields ``None`` or a genuinely non-empty name."""
        out = sanitize_filename(raw)
        assert out is None or (isinstance(out, str) and out != "")

    @settings(**FUZZ_SETTINGS)
    @given(
        raw=st.one_of(st.text(max_size=1000), hostile_names),
        max_length=st.integers(min_value=-10, max_value=300),
    )
    def test_respects_max_length(self, raw, max_length):
        """Output never exceeds the budget, whatever the budget is.

        Includes non-positive budgets, which must answer ``None`` rather
        than slicing from the end of the string.
        """
        out = sanitize_filename(raw, max_length=max_length)
        assert out is None or len(out) <= max_length

    @settings(**FUZZ_SETTINGS)
    @given(raw=st.one_of(st.text(max_size=1000), hostile_names))
    def test_output_cannot_redirect_a_write(self, raw):
        """No separator, no traversal segment, no control character.

        These are the properties that make the result safe to join onto a
        directory or hand to a storage backend.
        """
        out = sanitize_filename(raw)
        if out is None:
            return
        assert not any(sep in out for sep in SEPARATORS)
        assert not any(unicodedata.category(c) in INVISIBLE_CATEGORIES for c in out)
        # ``.`` / ``..`` name the current and parent directory; the strip
        # of leading and trailing dots means neither can survive whole.
        assert out not in {".", ".."}
        assert not out.startswith(".")

    @settings(**FUZZ_SETTINGS)
    @given(raw=st.one_of(st.text(max_size=1000), hostile_names))
    def test_normalizing_the_output_reintroduces_nothing(self, raw):
        """The result is NFKC-stable, and stays safe after normalizing.

        Sanitize-then-normalize is the documented bypass class
        (CVE-2025-52488): a fullwidth solidus survives an ASCII check and
        folds to "/" later. Since we normalize first, the output must be
        a fixed point — and re-checking the safety properties on the
        normalized form must still hold.
        """
        out = sanitize_filename(raw)
        if out is None:
            return
        assert unicodedata.normalize("NFKC", out) == out
        assert not any(sep in out for sep in SEPARATORS)
        assert out not in {".", ".."}

    @settings(**FUZZ_SETTINGS)
    @given(
        raw=st.one_of(st.text(max_size=1000), hostile_names),
        max_length=st.integers(min_value=1, max_value=300),
    )
    def test_idempotent(self, raw, max_length):
        """Sanitizing an already-sanitized name changes nothing.

        Consumers re-sanitize names that already went through the parser
        (the backend does exactly this before storing one), so a second
        pass must not erode the name.
        """
        once = sanitize_filename(raw, max_length=max_length)
        if once is None:
            return
        assert sanitize_filename(once, max_length=max_length) == once

    @settings(**FUZZ_SETTINGS)
    @given(
        stem=st.text(
            alphabet=st.characters(
                exclude_categories=("Cs", "Cc"), exclude_characters='<>:"|?*\\/.'
            ),
            min_size=1,
            max_size=400,
        ),
        ext=st.sampled_from([".pdf", ".txt", ".tar.gz", ".jpeg", ".ics"]),
    )
    def test_extension_survives_truncation(self, stem, ext):
        """A recognizable extension is preserved when the name is cut.

        The point of the truncation branch: the recipient's OS still
        opens the file with the right application.
        """
        out = sanitize_filename(stem + ext, max_length=64)
        assert out is not None
        # ``.tar.gz`` is 7 chars, so the last suffix is what survives.
        assert out.endswith(ext.rsplit(".", 1)[-1])
        assert len(out) <= 64
