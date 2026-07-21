"""
Fuzzing tests for the plain-text preview helper.

These tests use hypothesis for property-based testing to pin the
structural contract of :func:`jmap_email.preview_text` on arbitrary
and adversarial input: it never raises, its output fits the length
budget, and it is a single clean line of text.

Run with: pytest -m fuzz tests/test_preview_fuzz.py
Or: make fuzz-jmap-email
"""

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from jmap_email import preview_text

# Intensive fuzzing settings
FUZZ_SETTINGS = {
    "max_examples": 10000,
    "deadline": None,  # No time limit per example
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    "phases": [Phase.generate, Phase.target],  # Skip shrinking for speed
}

# Fragments biased toward what the cleaning pipeline reacts to, so the
# fuzz doesn't spend its whole budget on inert unicode prose: HTML
# (well-formed, malformed, unclosed), entities, and markdown-ish
# syntax as produced by html2text and rich composers.
_soup_fragment = st.one_of(
    st.text(max_size=30),
    st.sampled_from(
        [
            "<p>",
            "</p>",
            "<br>",
            "<script>",
            "</script>",
            "<style>",
            "</style>",
            "<!-- c -->",
            "<!-- x > y -->",
            '<img alt="a>b">',
            "<a href='x?a=1&amp;b=2'>",
            "<b",
            "</",
            "<>",
            "<SCRIPT>",
            "&amp;",
            "&lt;",
            "&gt;",
            "&eacute;",
            "&#233;",
            "&nbsp;",
            "&bogus;",
            "**",
            "*",
            "~~",
            "`",
            "```",
            "```python\n",
            "# ",
            "## ",
            "> ",
            "- ",
            "+ ",
            "2. ",
            "3) ",
            "---",
            "* * *",
            "\\\n",
            "\\\r\n",
            "![alt](http://x.co/i.png)",
            "[label](http://x.co)",
            "[](x)",
            "<https://example.com/a>",
            "<mailto:a@b.co>",
            "<user@host.tld>",
            "\n",
            "\r\n",
            "\t",
            "   ",
        ]
    ),
)

html_markdown_soup = st.lists(_soup_fragment, max_size=30).map("".join)

# "Plain prose": no character any cleaning stage reacts to (HTML and
# autolinks need <>&, markdown needs *~`_#[]()!\+-. — the dot only via
# "2. " list markers, excluded so cleaning can never mint a new
# line-start marker). On this alphabet the docstring's "plain prose
# passes through" claim strengthens to full idempotence.
plain_prose = st.text(
    alphabet=st.characters(
        exclude_categories=("Cs", "Cc"),
        exclude_characters="<>&*~`_#[]()!\\+-.",
    ),
    max_size=600,
)


# ``content_type`` selects which cleaning stages run, so every structural
# invariant below must hold on both paths — the text/plain one (quoted-line
# drop + markdown strip) and the text/html one, which skips them. Unknown
# types must behave like the text/plain default rather than crash.
content_types = st.sampled_from(
    ["text/plain", "text/html", "text/html; charset=utf-8", "text/markdown", ""]
)


@pytest.mark.fuzz
class TestPreviewFuzz:
    """Structural contract of ``preview_text`` under fuzzing."""

    @settings(**FUZZ_SETTINGS)
    @given(
        text=st.one_of(st.text(max_size=1000), html_markdown_soup),
        content_type=content_types,
    )
    def test_never_raises_and_fits_budget(self, text, content_type):
        """Any input yields a str no longer than the default budget."""
        out = preview_text(text, content_type=content_type)
        assert isinstance(out, str)
        assert len(out) <= 256

    @settings(**FUZZ_SETTINGS)
    @given(
        text=st.one_of(st.text(max_size=1000), html_markdown_soup),
        max_chars=st.integers(min_value=0, max_value=300),
        content_type=content_types,
    )
    def test_respects_custom_max_chars(self, text, max_chars, content_type):
        out = preview_text(text, max_chars=max_chars, content_type=content_type)
        assert len(out) <= max_chars

    @settings(**FUZZ_SETTINGS)
    @given(
        text=st.one_of(st.text(max_size=1000), html_markdown_soup),
        content_type=content_types,
    )
    def test_output_is_one_collapsed_line(self, text, content_type):
        """No newlines/tabs, no leading/trailing or doubled whitespace —
        exactly the shape a thread-list row can render as-is."""
        out = preview_text(text, content_type=content_type)
        assert out == " ".join(out.split())

    @settings(**FUZZ_SETTINGS)
    @given(
        text=st.one_of(st.text(max_size=1000), html_markdown_soup),
        content_type=content_types,
    )
    def test_never_emits_control_characters(self, text, content_type):
        """Control/ANSI hardening is not a formatting concern, so it must
        survive the content-type gating on every path."""
        out = preview_text(text, content_type=content_type)
        assert not any(ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 for c in out)

    @settings(**FUZZ_SETTINGS)
    @given(text=plain_prose, content_type=content_types)
    def test_idempotent_on_plain_prose(self, text, content_type):
        """Cleaning already-clean text is a no-op (docstring claim).

        Held on every path: the text/html one collects the same prose while
        skipping two stages, so a discrepancy would mean a stage that only
        the gated path can re-trigger."""
        once = preview_text(text, content_type=content_type)
        assert preview_text(once, content_type=content_type) == once


if __name__ == "__main__":
    pytest.main()
