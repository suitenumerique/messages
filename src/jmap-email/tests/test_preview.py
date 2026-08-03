"""Tests for the plain-text preview extractor in :mod:`jmap_email.preview`.

``preview_text`` turns markdown-ish and HTML-polluted body text into a
one-line, display-ready excerpt: HTML then markdown syntax stripped
BEFORE truncation, whitespace collapsed, length capped at 256 (the
RFC 8621 §4.1.4 ceiling) unless the caller lowers ``max_chars``.
"""

import time

import pytest

from jmap_email import preview_text


class TestPreviewText:
    """Syntax stripped BEFORE truncation, whitespace collapsed, length
    capped."""

    def test_plain_prose_passes_through(self):
        assert preview_text("Hello there, world.") == "Hello there, world."

    def test_strips_emphasis(self):
        text = "This is **bold**, *italic*, ~~gone~~ and `code`."
        assert preview_text(text) == "This is bold, italic, gone and code."

    def test_preserves_snake_case_underscores(self):
        """Word-internal underscores are content, not emphasis."""
        assert preview_text("use snake_case or _italic_") == (
            "use snake_case or italic"
        )

    def test_links_and_images_keep_label(self):
        assert preview_text("See [the docs](https://ex.co/a?b=1).") == "See the docs."
        assert preview_text("Look ![a chart](https://ex.co/i.png) here") == (
            "Look a chart here"
        )

    def test_strips_headers_and_lists_and_drops_quotes(self):
        # ``> quoted`` is dropped as reply history (not just its marker), so
        # only the headers/lists remain.
        text = "# Title\n> quoted\n- item one\n2. item two"
        assert preview_text(text) == "Title item one item two"

    def test_strips_code_fences_keeps_content(self):
        assert preview_text("before\n```python\nprint(1)\n```\nafter") == (
            "before print(1) after"
        )

    def test_strips_html_tags_and_entities(self):
        assert preview_text("<p>Caf&eacute; <b>time</b> &amp; more</p>") == (
            "Café time & more"
        )

    def test_drops_style_and_script_blocks(self):
        html = "<style>p{color:red}</style><script>x()</script><p>Real text</p>"
        assert preview_text(html) == "Real text"

    def test_drops_head_title_metadata(self):
        # <title> is <head> metadata (browser-tab text), not visible body —
        # some senders even put an image URL there. It must not leak.
        html = (
            "<head><title>http://img.example/logo.jpg</title></head>"
            "<body>Bonjour, voici le message.</body>"
        )
        assert preview_text(html) == "Bonjour, voici le message."

    def test_drops_unclosed_script_payload(self):
        """A truncated/unclosed ``<script>`` suppresses its payload to
        EOF instead of leaking JS into the preview (the old regex
        needed the closing tag and leaked)."""
        assert preview_text("Hello <script>var x = fetch('/steal')") == "Hello"
        assert preview_text("<SCRIPT>x()</SCRIPT>after") == "after"

    def test_handles_gt_inside_comments_and_attributes(self):
        """``>`` inside a comment body or a quoted attribute value does
        not terminate the markup early."""
        assert preview_text("a <!-- x > y --> b") == "a b"
        assert preview_text('<img alt="a>b" src="x">caption') == "caption"

    def test_preserves_plain_text_comparisons(self):
        """``<``/``>`` used as comparison signs in plain text are not
        tags — ``<`` before a non-letter stays text."""
        assert preview_text("x < 5 and y > 3") == "x < 5 and y > 3"
        assert preview_text("Prix < 100 euros, remise > 10%") == (
            "Prix < 100 euros, remise > 10%"
        )

    def test_lt_glued_to_letter_reads_as_markup(self):
        """``<`` immediately followed by a letter opens a tag — same
        reading as browsers. Pinned as the accepted trade-off of the
        tolerant-parser approach: ``x<y`` in bare prose is markup."""
        assert preview_text("si a<b alors c") == "si a"

    def test_preserves_autolinks(self):
        """``<https://…>`` and ``<user@host>`` are markdown autolinks,
        not tags — the URL / address survives the tag strip."""
        assert preview_text("see <https://example.com/a>") == (
            "see https://example.com/a"
        )
        assert preview_text("Courriel: <contact@brigny.fr>") == (
            "Courriel: contact@brigny.fr"
        )

    def test_autolink_with_trailing_slash(self):
        """A URL ending in ``/`` tokenises as a self-closing tag
        (``handle_startendtag``), not a start tag — it must still unwrap."""
        assert preview_text("end <https://example.com/> here") == (
            "end https://example.com/ here"
        )

    def test_autolink_decodes_entities(self):
        """The unwrapped URL is HTML-unescaped, matching the entity decoding
        the parser applies to ordinary text (`&amp;` -> `&`)."""
        assert preview_text("q <https://e.com/p?a=1&amp;b=2> x") == (
            "q https://e.com/p?a=1&b=2 x"
        )

    def test_non_letter_email_autolink_is_unwrapped_post_strip(self):
        """An email autolink whose local-part starts with a non-letter
        (`<123@host.com>`, `<+promo@x.com>`) is read as literal text by the
        parser (``<`` before a non-letter is text), so the in-parser tag path
        never sees it. The bounded post-strip pass unwraps it instead."""
        assert preview_text("x <123@host.com> y") == "x 123@host.com y"
        assert preview_text("x <+promo@shop.com> y") == "x +promo@shop.com y"

    def test_lt_not_autolink_is_untouched(self):
        """A bare ``<`` that isn't an autolink stays put — the post-strip
        unwrap only fires on URL/email shapes."""
        assert preview_text("i <3 you") == "i <3 you"
        assert preview_text("x < 5 and y > 3") == "x < 5 and y > 3"

    def test_strips_hard_break_backslashes(self):
        """Trailing-backslash hard breaks, with LF or CRLF endings."""
        assert preview_text("tableau.\\\n\\\nCordialement") == "tableau. Cordialement"
        assert preview_text("tableau.\\\r\n\\\r\nCordialement") == (
            "tableau. Cordialement"
        )

    def test_passes_through_multibyte_unicode(self):
        """Emoji, CJK, RTL and combining sequences are content, not markup —
        they survive unchanged, including alongside HTML and entities."""
        assert preview_text("Hello 👋🏽 world 😀") == "Hello 👋🏽 world 😀"
        assert preview_text("你好，世界！これはテスト") == "你好，世界！これはテスト"
        assert preview_text("مرحبا بالعالم") == "مرحبا بالعالم"
        # decomposed (e + combining acute) is kept code-point-for-code-point
        assert preview_text("cafe\u0301") == "cafe\u0301"
        assert preview_text("<p>Prix: 5€ 😀 &amp; plus</p>") == "Prix: 5€ 😀 & plus"

    def test_collapses_unicode_whitespace(self):
        """``\\s`` matches Unicode whitespace, so NBSP / line-separator /
        ideographic space collapse like ASCII runs do."""
        assert preview_text("a   b") == "a b"
        assert preview_text("a b　c") == "a b c"

    def test_truncation_counts_code_points(self):
        """Truncation is by Unicode code point (RFC 8621 §4.1.4 counts
        characters). Single-code-point astral chars are never split."""
        out = preview_text("😀" * 300)
        assert len(out) == 256
        assert out == "😀" * 256

    def test_truncation_may_split_a_grapheme_cluster(self):
        """Accepted edge: a multi-code-point grapheme (ZWJ emoji, combining
        sequence) straddling the max_chars boundary is cut mid-cluster —
        code-point truncation, not grapheme-aware. Pinned as deliberate; the
        invariant that matters is never exceeding the budget."""
        family = "👨‍👩‍👧‍👦"  # 7 code points, 1 grapheme
        out = preview_text(("x" * 253) + family)
        assert len(out) == 256  # never exceeds the budget
        assert out == ("x" * 253) + family[:3]  # cut mid-cluster

    def test_cleans_before_truncating(self):
        """Heavy leading syntax must not consume the preview budget:
        a ~200-char HTML figure prefix still yields the full caption
        and following prose."""
        figure = (
            '<figure><img alt="Jean-Baptiste-Camille_Corot.jpg" '
            'src="https://messages.example.com/api/v1.0/blob/'
            'cfc47b2d-eda7-4ac0-83b0-43d5e938f120/download/">'
            "<figcaption>Tableau de Jean-Baptiste Corot - Fontainebleau"
            "</figcaption></figure>"
        )
        content = f"{figure}\n\n# Voici un message\n\nJe te présente ce tableau."
        assert preview_text(content) == (
            "Tableau de Jean-Baptiste Corot - Fontainebleau "
            "Voici un message Je te présente ce tableau."
        )

    def test_truncates_to_max_length(self):
        assert len(preview_text("x" * 600)) == 256
        assert preview_text("y" * 40, max_chars=10) == "y" * 10

    def test_syntax_heavy_bodies_still_fill_the_budget(self):
        """The extractor budgets what it *collects*, but the stages after it
        only shrink the text, so a body whose text carries indentation or
        markdown used to yield a preview well short of ``max_chars`` — 79% on
        a pretty-printed newsletter, 50% on markdown. ``_COLLECT_SLACK`` is
        what closes that gap; each of these bodies has far more than 256
        characters of prose to offer, so a short result means the collection
        budget ran out before the cleaning did.

        ``>= 255`` rather than ``== 256``: truncation can land on a space,
        which the trailing ``rstrip`` then removes. One character of slop, not
        the 50-plus this test exists to catch."""
        newsletter = "<table>\n" + "\n".join(
            f"      <tr><td>Ligne {i} du bulletin mensuel</td></tr>" for i in range(200)
        )
        assert len(preview_text(newsletter, content_type="text/html")) >= 255
        assert len(preview_text("**mot** " * 300)) >= 255
        assert len(preview_text("### Titre\n- item de liste\n" * 200)) >= 255
        # A reply that quotes every other line: the fresh half alone is far
        # longer than the budget, so dropped quotes must not shorten it.
        reply = "\n".join(
            f"ligne fraiche numero {i}" if i % 2 else f"> ligne citee {i}"
            for i in range(200)
        )
        assert len(preview_text(reply)) >= 255

    def test_budget_fill_is_bounded_by_available_text_not_by_slack(self):
        """The slack bounds shrinkage, not availability: a reply with only a
        short fresh part previews at that length under any slack, because
        there is no more unquoted text to find. Not a shortfall."""
        reply = "Ma reponse fraiche.\n" + "\n".join(
            f"> ancienne ligne citee numero {i}" for i in range(80)
        )
        assert preview_text(reply) == "Ma reponse fraiche."

    def test_no_trailing_whitespace_after_truncation(self):
        out = preview_text("word " * 100)
        assert out == out.rstrip()

    def test_large_body_previews_from_the_head(self):
        """Self-interrupt path: a body far larger than the budget (its tail
        is never parsed) gives the same preview as a small body with the
        same leading content (cleaned in full)."""
        unit = "Bonjour, ceci est le debut du message. \n"
        small = unit * 10  # ~400 B: below the budget, cleaned in full
        big = unit * 20000  # ~800 KB: the parser stops after the head
        assert preview_text(big) == preview_text(small)
        assert preview_text(big).startswith("Bonjour, ceci est le debut")

    def test_large_syntax_heavy_body_still_finds_the_text(self):
        """Heavy leading markup collected before the budget still cleans
        away, leaving the real text at the top."""
        # Mostly markdown/HTML noise, a little real text near the top.
        noise = ("> \n# \n- \n<br>\n**\n") * 5000
        body = "Texte utile en tete du message.\n" + noise
        assert preview_text(body) == "Texte utile en tete du message."

    def test_empty_input(self):
        assert preview_text("") == ""


class TestPreviewHardening:
    """Security / robustness of the preview: bounded work, no control chars,
    no escape sequences, single line, quoted-reply skipping."""

    # ── scan cap (DoS bound) ───────────────────────────────────────────
    def test_scan_cap_excludes_text_beyond_the_cap(self):
        # Text past ``max_scan_bytes`` is never scanned, so it can't appear.
        assert preview_text("<a></a>" * 3000 + "HIDDEN", max_scan_bytes=1000) == ""
        assert "visible" in preview_text(
            "visible " + "<b></b>" * 50, max_scan_bytes=1000
        )

    def test_markup_bomb_is_bounded(self):
        # A body that is almost entirely markup never reaches the text budget;
        # the scan cap must still bound the work (regression for the DoS).
        start = time.perf_counter()
        preview_text("<a></a>" * 500_000, max_scan_bytes=64 * 1024)
        assert (time.perf_counter() - start) < 1.0  # was seconds before the cap

    # ── control chars / escape sequences / single line ─────────────────
    def test_strips_c0_del_c1_control_chars(self):
        # Non-whitespace controls (NUL, BEL, BS, ESC, DEL, C1) are deleted.
        assert preview_text("a\x00b\x07c\x08d\x1be\x7ff\x9fg") == "abcdefg"
        # Separator controls (US, 0x1F) are whitespace -> collapse to a space.
        assert preview_text("abc\x1fdef") == "abc def"

    def test_strips_ansi_csi_and_osc_escape_sequences(self):
        assert preview_text("a\x1b[31mRED\x1b[0mb") == "aREDb"
        assert preview_text("x\x1b]0;window-title\x07 y") == "x y"

    def test_strips_zero_width_preheader_spacers(self):
        # Senders pad the inbox-preview line with invisible format chars: soft
        # hyphen (00AD), ZWSP (200B), ZWNJ (200C), word joiner (2060), BOM
        # (FEFF). They render as nothing and must be removed. The ZERO WIDTH
        # JOINER (U+200D) is kept — emoji sequences rely on it.
        assert preview_text("Preheader\u200c \u00ad \u200b done") == "Preheader done"
        assert preview_text("\ufeffHello\u200bworld") == "Helloworld"
        assert preview_text("a\u2060b") == "ab"
        fam = "\U0001f468\u200d\U0001f469"  # man-ZWJ-woman
        assert preview_text(f"x {fam} y") == f"x {fam} y"

    def test_control_chars_from_autolink_entities_are_stripped(self):
        # ``&#7;`` (BEL) decodes then is deleted; ``&#0;`` is HTML-remapped to
        # U+FFFD (printable) by the parser. Never a raw control char.
        out = preview_text("<https://x&#7;y> z")
        assert "\x07" not in out and "\x00" not in out

    def test_preview_is_always_single_line(self):
        assert "\n" not in preview_text("a\nb\r\nc de")
        assert preview_text("a\nb\r\nc") == "a b c"

    # ── E1 / E3 ────────────────────────────────────────────────────────
    def test_uppercase_scheme_autolink_is_kept(self):
        assert preview_text("see <HTTPS://Example.com/A> x") == (
            "see HTTPS://Example.com/A x"
        )
        assert preview_text("<MAILTO:a@b.c>") == "MAILTO:a@b.c"

    def test_nonpositive_max_chars_yields_empty(self):
        assert preview_text("hello", max_chars=0) == ""
        assert preview_text("hello", max_chars=-5) == ""

    # ── setext headers ─────────────────────────────────────────────────
    def test_strips_setext_headers(self):
        assert preview_text("Title\n====\nbody text") == "Title body text"
        assert preview_text("Title\n----\nbody text") == "Title body text"

    # ── quoted-reply skipping ──────────────────────────────────────────
    def test_drops_plaintext_quoted_lines(self):
        assert preview_text("My fresh reply\n> old thread\n> more old") == (
            "My fresh reply"
        )

    def test_attribution_lines_are_not_cut(self):
        # Locale/client attribution phrases ("On … wrote:", "Original
        # Message") are deliberately NOT matched — only structural signals
        # (`>` lines, `<blockquote>`) are language-neutral enough for the lib.
        # (Content after an un-quoted attribution line survives.)
        assert preview_text("Short reply On Mon, Bob wrote: quoted stuff") == (
            "Short reply On Mon, Bob wrote: quoted stuff"
        )

    def test_suppresses_html_blockquote_content(self):
        assert (
            preview_text("<p>fresh reply</p><blockquote>quoted history</blockquote>")
            == "fresh reply"
        )

    def test_autolink_inside_blockquote_does_not_leak(self):
        # The suppression guard in _tag_or_autolink must stop an autolink
        # inside quoted (blockquote) HTML from leaking into the preview.
        assert (
            preview_text(
                "<p>hi</p><blockquote>see <https://leak.example/secret></blockquote>"
            )
            == "hi"
        )

    def test_gt_entity_starting_an_html_line_is_not_a_quote_marker(self):
        # The `>`-quoted-line drop is a *text/plain* signal: in HTML, quoted
        # history is carried by <blockquote> (suppressed above), never by a
        # leading `>`. `convert_charrefs` decodes `&gt;` to a literal `>`, so
        # running the text/plain filter over HTML would mistake visible prose
        # opening with a chevron — "> 100 EUR", "> 50% off", a decorative
        # bullet — for quoted history and delete the whole line. Here that
        # line is the entire body, so the preview would come back empty.
        assert (
            preview_text(
                "<div>&gt; 100 EUR de remise sur votre commande</div>",
                content_type="text/html",
            )
            == "> 100 EUR de remise sur votre commande"
        )

    def test_gt_entity_does_not_silently_drop_a_pretty_printed_html_line(self):
        # Same cause, quieter symptom — and the realistic trigger. What makes
        # a chevron look like a quote is the newline in front of it, so it
        # bites indented HTML: the source newlines survive the strip (they
        # live inside a text node), and every mail-template engine emits
        # pretty-printed HTML. The preview stays non-empty, so nothing would
        # look broken — the chevron line would just be gone from the middle.
        assert (
            preview_text(
                "<div>\n"
                "  <p>Offre du mois</p>\n"
                "  &gt; 100 EUR de remise\n"
                "  <p>Voir la boutique</p>\n"
                "</div>",
                content_type="text/html",
            )
            == "Offre du mois > 100 EUR de remise Voir la boutique"
        )

    def test_html_keeps_markdown_characters_literal(self):
        # The other half of the same rule: `*`, `_`, backticks and `[…](…)`
        # are html2text syntax in a text/plain body but ordinary characters a
        # sender typed in an HTML one. Stripping them there corrupts real
        # content — "2*3=6" must not become "23=6".
        assert (
            preview_text("<p>Tarif: 2*3=6, C* language</p>", content_type="text/html")
            == "Tarif: 2*3=6, C* language"
        )

    def test_content_type_parameters_are_ignored(self):
        # Real parts carry MIME parameters; only the leading type decides.
        assert (
            preview_text("<p>2*3=6</p>", content_type="text/html; charset=utf-8")
            == "2*3=6"
        )
        assert preview_text("<p>2*3=6</p>", content_type="  TEXT/HTML  ") == "2*3=6"
        # Separator-less header: malformed, but common enough in real mail
        # that it must not silently fall back to the text/plain stages.
        assert (
            preview_text("<p>2*3=6</p>", content_type="text/html charset=utf-8")
            == "2*3=6"
        )

    def test_html_in_a_parameter_value_does_not_trigger_the_html_path(self):
        # The gate disables cleaning, so it stays anchored at the start: a
        # part that merely mentions text/html in a parameter is not HTML.
        assert (
            preview_text(
                "Tarif: 2*3=6",
                content_type='application/octet-stream; name="text/html"',
            )
            == "Tarif: 23=6"
        )

    def test_undeclared_html_still_gets_the_thorough_cleaning(self):
        # The default is text/plain on purpose: an unrecognised or absent type
        # (importer blob, text/markdown, hand-built part) must not silently
        # opt out of markdown and quote cleaning just because it contains
        # angle brackets. Callers opt into the literal reading explicitly.
        assert preview_text("<p>Tarif: 2*3=6</p>") == "Tarif: 23=6"
        assert preview_text("<p>2*3=6</p>", content_type="text/markdown") == "23=6"

    def test_plaintext_quote_and_markdown_stripping_are_unchanged(self):
        # The text/plain path must not regress: quoted history is still
        # dropped whole (not just its marker), markdown is still stripped.
        assert (
            preview_text("My fresh reply\n> old thread", content_type="text/plain")
            == "My fresh reply"
        )
        assert preview_text("**bold** text", content_type="text/plain") == "bold text"

    # ── word boundaries (pending-space coalescing) ─────────────────────
    def test_tags_preserve_word_boundary_without_extra_spaces(self):
        assert preview_text("alpha<br>beta") == "alpha beta"
        assert preview_text("<p>one</p><p>two</p>") == "one two"
        assert preview_text("<div><span>solo</span></div>") == "solo"


class TestPreviewParserQuirks:
    """Pins for the stdlib ``html.parser`` behaviours the preview relies on —
    documented so a Python upgrade that changes them fails loudly."""

    def test_output_is_plaintext_not_html_safe(self):
        # The preview is a text string and may carry <, >, & verbatim; it is
        # NOT HTML-escaped (consumers must escape before HTML rendering).
        assert preview_text("x < 5 & y > 3") == "x < 5 & y > 3"
        assert preview_text("Tom &amp; Jerry &lt;3") == "Tom & Jerry <3"

    def test_processing_instructions_are_dropped(self):
        assert preview_text("a<?php echo 'evil'; ?>b") == "a b"
        assert preview_text("a<?xml version='1.0'?>b") == "a b"

    def test_declarations_and_doctype_are_dropped(self):
        assert preview_text("<!DOCTYPE html><p>hi</p>") == "hi"
        assert preview_text("a<![CDATA[secret]]>b") == "a b"

    def test_deep_nesting_does_not_recurse_or_crash(self):
        # HTMLParser is iterative — deep nesting must not blow the stack.
        assert preview_text("<div>" * 20_000 + "deep" + "</div>" * 20_000) == "deep"

    def test_numeric_char_ref_control_handling(self):
        # &#0; is HTML5-remapped to U+FFFD (printable); &#7; (BEL) decodes then
        # is stripped as a control char. Never a raw NUL/BEL in the output.
        out = preview_text("x&#0;y&#7;z")
        assert "\x00" not in out and "\x07" not in out
        assert out == "x�yz"

    def test_unclosed_style_and_blockquote_suppress_to_eof(self):
        assert preview_text("keep<style>body{color:red}") == "keep"
        assert preview_text("reply<blockquote>quoted to the end") == "reply"

    def test_svg_and_foreign_text_is_extracted(self):
        # SVG/MathML are treated as ordinary tags; their text is real content.
        assert preview_text("<svg><text>chart label</text></svg>") == "chart label"

    def test_void_and_self_closing_non_autolink_tags(self):
        assert preview_text("a<br>b<hr/>c") == "a b c"

    def test_self_closing_suppress_tag(self):
        # A self-closing suppress tag (``<blockquote/>``) reaches
        # handle_startendtag, not handle_starttag; it must not emit text and
        # must still mark a word boundary.
        assert preview_text("a<blockquote/>b") == "a b"


class TestPreviewComplexity:
    """Wall-clock guards against quadratic matching returning.

    Every line-anchored markdown pattern once used ``\\s`` for its leading
    run. ``\\s`` matches ``\\n``, so under ``re.MULTILINE`` the match
    rescanned the whole following whitespace run from every line start.
    The head the patterns run on is bounded — but its size scales with
    ``max_chars``, so a caller who raised that knob turned a 128 KiB body
    of alternating spaces and newlines into >20s of matching. The bounds
    below are ~100x the fixed cost, so they flag a regression in the
    exponent without being flaky about machine speed.
    """

    # Alternating whitespace: one line start per two characters, which is
    # what makes ``^\s*`` rescan.
    BOMB = " \n" * 65000

    @pytest.mark.parametrize("max_chars", [256, 4096, 16384, 65536])
    def test_widening_max_chars_stays_linear(self, max_chars):
        start = time.perf_counter()
        preview_text(self.BOMB, max_chars=max_chars)
        assert time.perf_counter() - start < 5.0

    def test_tab_newline_alternation(self):
        start = time.perf_counter()
        preview_text("\t\n" * 65000, max_chars=16384)
        assert time.perf_counter() - start < 5.0

    def test_at_run_without_a_dot(self):
        """``[^@>\\s]+@[^@>\\s]+\\.[^@>\\s]+`` could split at every dot,
        because the label class contained the dot it was splitting on."""
        start = time.perf_counter()
        preview_text("<" + "a" * 60000 + "@" + "b" * 60000 + ">", max_chars=16384)
        assert time.perf_counter() - start < 5.0


if __name__ == "__main__":
    pytest.main()
