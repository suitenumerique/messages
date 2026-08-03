"""Plain-text ``preview`` extraction for the JMAP Email object.

The public API is the top-level ``jmap_email`` package: import
:func:`preview_text` as ``from jmap_email import preview_text``. This
submodule holds the implementation and is not a stable import path.

RFC 8621 §4.1.4 defines ``preview`` as a short, single-line, display-ready
plain-text excerpt of the message body, shown in a mailbox listing. Body
parts routinely carry embedded HTML and/or html2text-style markdown, plus
quoted reply history and stray control characters — all noise in a listing.
The pipeline (see :func:`preview_text`) is, in order: strip HTML (dropping
``<script>``/``<style>``/``<title>``/``<blockquote>`` payloads, decoding
entities, unwrapping markdown autolinks), drop ``>``-quoted text/plain lines,
strip markdown syntax, delete escape sequences and control/format characters,
collapse whitespace, then truncate.

Two of those stages are *conventions of the plain-text wire format*, not
universal cleanups, so they are gated on ``content_type``: a leading ``>``
means quoted history only by the text/plain convention (RFC 3676), and
``*``/``_``/``[…](…)`` are syntax only in html2text-style bodies. In a
``text/html`` part both are ordinary literal characters — the sender wrote
them to be shown — and quoted history arrives as ``<blockquote>``, which the
extractor already suppresses. Running the text/plain stages over HTML-derived
text therefore deletes real content: ``&gt;`` decodes to ``>`` before the
quote filter sees it, so a line of prose opening with a chevron
("&gt; 100 EUR de remise") is dropped whole, and ``2*3=6`` becomes ``23=6``.

Two bounds keep it cheap on hostile input: the HTML extractor interrupts
itself once it has collected the characters the preview will keep, and the
input is capped up front (``max_scan_bytes``) so a body that is almost all
markup — which never reaches the text budget — still can't run away.
"""

import html
import re
from html.parser import HTMLParser

__all__ = ["preview_text"]

# ── markdown / whitespace patterns (all run on the bounded head) ─────────
#
# Every line-anchored pattern below uses ``[ \t]`` rather than ``\s`` for its
# leading run. ``\s`` matches ``\n``, so under ``re.MULTILINE`` a ``^\s*``
# rescans the entire following whitespace run from every line start — O(n²) on
# a body of alternating spaces and newlines. The head is bounded, but its size
# scales with ``max_preview_chars``, so a caller who raised that knob turned a
# 128 KiB body into tens of seconds of matching. Leading *horizontal*
# whitespace is what these constructs actually allow.

# Fence markers are dropped but the code content itself is kept.
_MD_CODE_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*$", re.MULTILINE)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# ATX headers (``# Title``) and setext underlines (a line of only ``=``/``-``
# under a title — we drop the underline, the title on the line above stays).
_MD_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_MD_SETEXT_RE = re.compile(r"^[ \t]*[=-]{2,}[ \t]*$", re.MULTILINE)
# A horizontal rule is a line of only -/*/_ (3+), possibly spaced.
_MD_HRULE_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_MD_LIST_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", re.MULTILINE)
# Hard breaks: a trailing backslash at end of line (tolerate CRLF
# endings — ``$`` alone stops at the ``\r``).
_MD_HARDBREAK_RE = re.compile(r"\\[ \t]*\r?$", re.MULTILINE)
# Underscores are stripped only at word edges so snake_case survives.
_MD_EMPHASIS_RE = re.compile(r"\*{1,3}|~~|`+|(?<!\w)_{1,3}|_{1,3}(?!\w)")

# Markdown autolinks (`<https://…>`, `<user@host>`) are handled in two places
# because the HTML parser splits them by their first character:
#
#  * Letter-initial autolinks (every URL scheme, most emails) are opened as
#    *tags* — ``<https://x>`` tokenises as a tag named ``https:`` — and would
#    be dropped as markup. The extractor rescues them in the tag handlers via
#    the tag's *verbatim* source (``get_starttag_text``), matching
#    ``_AUTOLINK_INNER_RE`` against the inner text (brackets stripped).
#  * Non-letter-initial email autolinks (``<123@host.com>``) are read as
#    literal text by the parser (``<`` before a non-letter is text), so they
#    survive the strip verbatim and are unwrapped by ``_MD_AUTOLINK_RE`` in
#    the post-strip pass — bounded, running only over the collected head.
#
# IGNORECASE so ``<HTTPS://…>`` / ``<MAILTO:…>`` are not lost (schemes are
# case-insensitive; without this the tag path drops them as markup).
_AUTOLINK = r"(?:https?://|mailto:)[^>\s]+|[^@>\s]+@[^@>\s.]+(?:\.[^@>\s.]+)+"
_AUTOLINK_INNER_RE = re.compile(_AUTOLINK, re.IGNORECASE)
_MD_AUTOLINK_RE = re.compile(rf"<({_AUTOLINK})>", re.IGNORECASE)

_WS_RE = re.compile(r"\s+")

# The extractor budgets the characters it *collects*, but every stage after it
# only shrinks the text — whitespace collapses, markdown markers go, quoted
# lines are dropped whole. Budgeting exactly ``max_chars`` therefore ships a
# preview short of its own cap. Collect a small multiple instead and let the
# final truncation trim the surplus; the multiple fills the budget, the
# self-interrupt (now at ``max_chars * _COLLECT_SLACK``) still bounds the
# work, and ``max_scan_bytes`` backstops a body that reaches neither.
#
# Measured budget fill at the 256-char default, and parse cost on the
# newsletter shape (pretty-printed table, the common real-world one):
#
#   slack                1      2      3      4
#   newsletter (6 sp)   79%   100%   100%   100%
#   markdown emphasis   50%   100%   100%   100%
#   list/heading soup   77%   100%   100%   100%
#   reply, 1 line in 2  55%   100%   100%   100%
#   deep table (24 sp)  43%    87%   100%   100%
#   markdown links      30%    45%    61%    77%
#   µs/call             36     64     96    124
#
# 2 fills the budget on every shape a real composer emits, at ~1.8x a cost
# measured in tens of microseconds. Cost grows linearly past that while the
# gain does not: only very deep table nesting and link-only html2text output
# stay short, and those shrink 6x+, so no fixed multiple fixes them — they
# are left partially short rather than paid for on every message.
#
# Note this bounds *shrinkage*, not availability: a reply whose visible text
# is 58 chars of fresh prose over 80 quoted lines previews at 58 chars under
# any slack, because there is no more unquoted text to find. That is the
# correct answer, not a shortfall.
_COLLECT_SLACK = 2

# ANSI/terminal escape *sequences* — the ESC byte plus its printable payload
# (``\x1b[31m``, ``\x1b]0;title\x07``). Deleting the lone ESC control char
# (below) neutralises the terminal action but leaves ``[31m`` debris, so strip
# the whole sequence first for a clean line. Disjoint classes / single
# quantifiers → no backtracking.
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?:<=>]*[ -/]*[@-~]"  # CSI … final byte
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC … BEL or ST
    r"|[@-Z\\-_]"  # two-char Fe escapes (incl. ESC \\ = ST)
    r")"
)

# Invisible format characters that senders inject as "preheader spacers" to
# pad the inbox-preview line (soft hyphen, zero-width space / non-joiner, word
# joiner, BOM). They render as nothing but bloat the preview, so delete them.
# U+200D ZERO WIDTH JOINER is kept — it's load-bearing in emoji sequences.
_FORMAT_STRIP = (0x00AD, 0x200B, 0x200C, 0x2060, 0xFEFF)

# Control characters (C0, DEL, C1) must not reach the display line: NUL, BEL,
# ANSI/terminal escapes, etc. are log- and terminal-injection vectors.
# Whitespace controls (TAB/LF/CR/FS-US/NEL) become a space; every other
# control — plus the format spacers above — is deleted. Applied BEFORE the
# final whitespace collapse so a deleted char between two spaces can't leave a
# double space. Bidi marks are intentionally left alone (RTL correctness).
_CONTROL_TABLE = {
    c: (" " if chr(c).isspace() else None)
    for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))
}
_CONTROL_TABLE.update(dict.fromkeys(_FORMAT_STRIP, None))

# ── quoted-reply detection (text/plain; HTML uses <blockquote> suppression) ─
# A fully quoted line (leading ``>``) is dropped entirely — not just its
# marker — so the preview shows the fresh reply. This is the only quote signal
# the library uses: it's structural and language-neutral. Locale/client
# attribution lines ("On … wrote:", "-----Original Message-----") are NOT
# matched — that phrase-matching belongs to the application layer.
_QUOTED_LINE_RE = re.compile(r"^[ \t]*>")

# Tags whose *content* is dropped from the preview: script/style are markup
# noise; title is <head> metadata (the browser-tab text, not visible body —
# some senders even put an image URL there); blockquote is quoted reply
# history.
_SUPPRESS_TAGS = ("script", "style", "title", "blockquote")


class _EnoughText(Exception):
    """Internal signal: the extractor has collected its character budget of
    visible text, so the rest of the body can be left unparsed."""


class _HTMLTextExtractor(HTMLParser):
    """Collect the text content of an HTML fragment, dropping markup.

    The stdlib tolerant parser gets the hard cases right for free:
    attributes containing ``>``, comments containing ``>``, unclosed
    ``<script>``/``<style>`` (their payload is suppressed to EOF instead of
    leaking into the preview), and character references (``convert_charrefs``
    decodes them in the emitted data). ``<blockquote>`` content is suppressed
    the same way so quoted reply history doesn't fill the preview.

    Reading of ambiguous ``<`` follows the parser (and browsers): ``<`` before
    a non-letter is text — ``x < 5`` survives — while ``<`` glued to a letter
    opens a tag, so prose like ``si x<y alors`` loses its tail. Accepted
    trade-off. A tag that is really a markdown autolink (``<https://…>``) is
    unwrapped back to text rather than dropped (see :meth:`_tag_or_autolink`).

    Word boundaries: instead of appending a space per tag (which would grow
    unboundedly on markup-heavy input), a single pending-space is coalesced
    and emitted only between two runs of real text — so N adjacent tags cost
    nothing and there are no leading/trailing boundary spaces to trim.

    With ``max_chars``, the parser interrupts itself (via :class:`_EnoughText`,
    caught in :func:`_strip_html`) once it has collected that many characters
    of *visible text* — an overshooting final chunk is sliced to fit. Only
    real text counts toward the budget.
    """

    def __init__(self, max_chars: int | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._suppress_depth = 0
        self._max_chars = max_chars
        self._collected = 0
        self._pending_space = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open-tag hook: enter suppression for script/style/blockquote,
        otherwise emit an unwrapped autolink or note a word boundary."""
        if tag in _SUPPRESS_TAGS:
            self._suppress_depth += 1
            self._pending_space = True
            return
        self._tag_or_autolink(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Self-closing tag (``<…/>``): no content to suppress. A URL ending
        in ``/`` (``<https://x/>``) is tokenised here, not at
        :meth:`handle_starttag`, so it needs the same autolink handling."""
        if tag in _SUPPRESS_TAGS:
            self._pending_space = True
            return
        self._tag_or_autolink(tag)

    def handle_endtag(self, tag: str) -> None:
        """Close-tag hook: leave suppression, note a word boundary."""
        if tag in _SUPPRESS_TAGS and self._suppress_depth:
            self._suppress_depth -= 1
        self._pending_space = True

    def handle_data(self, data: str) -> None:
        """Text hook: keep data unless inside a suppressed element."""
        if not self._suppress_depth:
            self._add_text(data)

    def handle_comment(self, data: str) -> None:
        """Comments, PIs and declarations emit no text but, like a tag, mark a
        word boundary so ``foo<!--x-->bar`` doesn't become ``foobar``."""
        self._pending_space = True

    handle_pi = handle_comment
    unknown_decl = handle_comment

    def handle_decl(self, decl: str) -> None:
        # Separate def, not an alias: the base signature names the parameter
        # ``decl`` (not ``data``), so aliasing would violate LSP under strict
        # type-checking. Behaviour is identical — a declaration marks a
        # word boundary and emits no text.
        self._pending_space = True

    def _tag_or_autolink(self, tag: str) -> None:
        """A non-suppressed tag: if it is really a markdown autolink, emit the
        unwrapped URL / address as counted text; else note a word boundary.

        Fast path: only an autolink's tag *name* carries ``:`` (a URL scheme —
        ``<https://x>`` → ``https:``) or ``@`` (an email — ``<a@b.c>``); an
        ordinary HTML tag (``a``, ``div``, ``br``) has neither, so the
        expensive ``get_starttag_text()`` reconstruction + regex is skipped for
        it — the common case, and what keeps a markup-heavy body cheap.

        The suppression guard is load-bearing: unlike ``script``/``style``
        (CDATA — no tags parsed inside), ``<blockquote>`` content is normal
        HTML, so this fires for tags *inside* a quoted block. Without it a
        ``<https://…>`` inside quoted history would leak.
        """
        if self._suppress_depth:
            return
        if ":" in tag or "@" in tag:
            inner = (self.get_starttag_text() or "")[1:-1]
            if _AUTOLINK_INNER_RE.fullmatch(inner):
                self._add_text(html.unescape(inner))
                return
        self._pending_space = True

    def _add_text(self, text: str) -> None:
        """Append visible text (with a coalesced word-boundary space when a
        tag intervened), counting it toward the budget and interrupting the
        parse once the budget is met — an overshooting chunk is sliced."""
        if not text:
            return
        if self._pending_space:
            self._pending_space = False
            if self._chunks:
                self._chunks.append(" ")
        if self._max_chars is not None:
            remaining = self._max_chars - self._collected
            if len(text) >= remaining:
                self._chunks.append(text[:remaining])
                self._collected = self._max_chars
                raise _EnoughText
        self._chunks.append(text)
        self._collected += len(text)

    def text(self) -> str:
        """Return every kept text chunk, concatenated."""
        return "".join(self._chunks)


def _strip_html(text: str, max_chars: int | None = None) -> str:
    """Return the text content of ``text``, markup removed.

    With ``max_chars``, the parser stops once it has collected that many
    characters of visible text — the body's tail is left unparsed.
    """
    extractor = _HTMLTextExtractor(max_chars)
    try:
        extractor.feed(text)
        extractor.close()
    except _EnoughText:
        pass
    return extractor.text()


def _strip_quoted_lines(text: str) -> str:
    """Drop text/plain quoted-reply lines (leading ``>``) — a structural,
    language-neutral signal. HTML quotes are handled earlier by
    ``<blockquote>`` suppression in the extractor.
    """
    if ">" not in text:
        return text
    return "\n".join(
        line for line in text.split("\n") if not _QUOTED_LINE_RE.match(line)
    )


def preview_text(
    text: str,
    max_chars: int = 256,
    max_scan_bytes: int = 128 * 1024,
    *,
    content_type: str = "text/plain",
) -> str:
    """Return ``text`` as a one-line, display-ready plain-text preview.

    Pipeline: strip HTML (``<script>``/``<style>``/``<title>``/``<blockquote>``
    payloads dropped, entities decoded, markdown autolinks unwrapped to their
    URL), drop ``>``-quoted lines, strip markdown syntax (fences; images and
    links reduced to their label; ATX and setext headers; rules; list markers;
    hard breaks; emphasis), delete escape sequences and control/format
    characters, collapse whitespace, and truncate to ``max_chars``. Cleaning
    always happens BEFORE truncation, so leading syntax never consumes the
    budget; plain prose passes through unchanged (minus whitespace
    normalisation).

    ``content_type`` selects which stages run. The two middle ones —
    ``>``-quoted lines and markdown — are conventions of the plain-text wire
    format, so they are **skipped for** ``text/html``, where ``>``, ``*`` and
    ``_`` are literal characters the sender meant to be shown and quoted
    history arrives as ``<blockquote>`` (suppressed during the strip). The HTML
    strip and the control-character hardening run either way. The default,
    ``"text/plain"``, is the thorough path, so an unrecognised type (an
    importer blob, ``text/markdown``, a hand-built part) is still fully
    cleaned — pass ``content_type="text/html"`` to opt into the literal
    reading.

    ``max_chars`` defaults to 256, the RFC 8621 §4.1.4 ceiling for ``preview``
    (a value ``<= 0`` yields ``""``). ``max_scan_bytes`` caps the input
    scanned so a markup-only body — which never reaches the text budget — is
    still bounded; the HTML strip otherwise interrupts itself once it has a
    small multiple of ``max_chars`` characters of visible text (the cleaning
    stages after it only shrink the text, so collecting exactly ``max_chars``
    would ship a preview short of its cap), so only the head of a large body
    is ever processed.

    The result is **plain text, not HTML**: it may contain ``<``, ``>`` and
    ``&`` (e.g. from ``x < 5 & y > 3``). A consumer that renders the preview
    inside an HTML document MUST escape it — "display-ready" means ready for a
    text context (a mailbox-list row, a terminal), not raw HTML interpolation.
    """
    if not text or max_chars <= 0:
        return ""
    # Cap the input up front: the self-interrupt bounds text-bearing bodies,
    # this bounds bodies that are almost entirely markup.
    result = _strip_html(text[:max_scan_bytes], max_chars=max_chars * _COLLECT_SLACK)
    # Anchored at the start rather than matched anywhere: this gate
    # *disables* two cleaning stages, so a stray "text/html" inside a
    # parameter value (`name="text/html.txt"`) must not switch them off.
    # A prefix test rather than an exact one, so it also survives a
    # separator-less header ("text/html charset=utf-8") — real mail is
    # full of those, and anything under `text/html*` is HTML anyway.
    if not content_type.strip().lower().startswith("text/html"):
        result = _strip_quoted_lines(result)
        # Autolinks the parser passed through as literal text (non-letter
        # local part) still carry brackets — unwrap over the bounded head.
        result = _MD_AUTOLINK_RE.sub(r"\1", result)
        result = _MD_CODE_FENCE_RE.sub("", result)
        result = _MD_IMAGE_RE.sub(r"\1", result)
        result = _MD_LINK_RE.sub(r"\1", result)
        result = _MD_HEADER_RE.sub("", result)
        result = _MD_SETEXT_RE.sub("", result)
        result = _MD_HRULE_RE.sub("", result)
        result = _MD_LIST_RE.sub("", result)
        result = _MD_HARDBREAK_RE.sub("", result)
        result = _MD_EMPHASIS_RE.sub("", result)
    # Strip ANSI escape sequences (while the ESC anchor survives), normalise
    # control chars (whitespace → space, rest deleted), then collapse all
    # whitespace last — guarantees a single printable display line with no
    # doubled spaces. Hardening, not formatting: runs for every content type.
    result = _ANSI_RE.sub("", result).translate(_CONTROL_TABLE)
    result = _WS_RE.sub(" ", result)
    return result.strip()[:max_chars].rstrip()
