"""Attachment filename hygiene.

:func:`sanitize_filename` defangs a filename that arrived over the wire —
path components, invisible characters, length — while keeping it
recognizable. ``parse_email`` applies it to every part name it reports;
it is public so consumers can apply it to names that never went through
the parser, such as client-supplied ones.

Naming a part that carries *no* filename is deliberately not covered
here. Per RFC 8621 ``name`` is ``String | null`` and ``parse_email``
reports ``null`` rather than inventing a placeholder — what to display
instead is consumer policy.
"""

import re
import unicodedata
from ntpath import basename as nt_basename
from posixpath import basename as posix_basename

# Unicode categories removed outright. Deliberately broad: a filename is
# shown to a human and handed to a filesystem, and each of these is
# invisible to the first while meaning something to the second.
#
#   Cc  controls (NUL, CR, LF, TAB, DEL, the C1 block)
#   Cf  format characters — bidi overrides (U+202E renders "annexe.exe"
#       as "annexe.txt", the classic attachment spoof), zero-width
#       joiners and spaces, the BOM
#   Zl  U+2028 line separator
#   Zp  U+2029 paragraph separator
#   Cs  lone surrogates — unencodable, they raise on write
#
# The cost is that a ZWJ emoji sequence degrades to its component emoji:
# a cosmetic loss on a filename, against an allowlist that would need
# revisiting every Unicode release.
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})

# Replaced with "_": legal in a name but reserved by some filesystem or
# shell, so they stay visible rather than vanishing.
_UNSAFE_CHARS_RE = re.compile(r'[<>:"|?*\\/]')

# Stripped from both ends: quote framing left by a MIME parameter, the
# dot/separator runs that spell "." and "..", and surrounding whitespace
# (Windows silently drops trailing dots and spaces, so "x.exe " and
# "x.exe" name the same file there).
_FRAMING_CHARS = '"/.\\ '


def sanitize_filename(filename: str | None, max_length: int = 255) -> str | None:
    """Sanitize an attachment filename, preserving the extension when truncating.

    Strips path components (POSIX and Windows both, since the wire does
    not say which system produced the name), every invisible character,
    and the characters filesystems reject; then truncates to *max_length*
    keeping a reasonable extension intact. Pass the limit your storage
    enforces rather than relying on the default.

    Returns ``None`` — never ``""`` — when nothing usable survives, so
    the failure case can never be mistaken for a name. Callers wanting a
    placeholder write ``sanitize_filename(x) or "unnamed"``.

    The result is safe to join onto a directory: it holds no separator,
    no traversal segment, and no leading dot (so ``.gitignore`` comes
    back as ``gitignore``). It is also NFKC-stable, so normalizing it
    downstream cannot reintroduce any of those.

    What it does **not** do is apply the naming policy of whatever
    filesystem you are about to write to. A name can be perfectly clean
    and still mean something particular there — ``nul.txt`` is the null
    device on Windows 10, ``aux`` on every Windows version — and the
    right answer depends on the target OS, which a parser cannot see.
    That check belongs where the file is opened; see
    ``werkzeug.utils.secure_filename`` for the shape of it.
    """
    if not filename or max_length <= 0:
        return None

    # Bound the work before normalizing: NFKC expands by up to 18x
    # (U+FDFA), so an unbounded caller-supplied name is a memory
    # multiplier. Everything past this is discarded by the truncation
    # below anyway, with slack far beyond any composition's ability to
    # shrink text back under *max_length*.
    filename = filename[: max_length * 32]

    # The next two steps are ordered, and it matters in both directions.
    #
    # Invisibles go first. A format character with combining class 0 sits
    # *outside* a run of dots and shields it from the strip further down;
    # deleting it afterwards re-exposes them, so ``"\x00..\x00"`` would
    # come back as ``".."`` — the parent directory, intact.
    filename = "".join(
        c for c in filename if unicodedata.category(c) not in _STRIPPED_CATEGORIES
    )

    # Then compatibility-normalize, before anything looks for a separator.
    # Sanitizing and *then* normalizing is a known bypass (CVE-2025-52488
    # and relatives): U+FF0F FULLWIDTH SOLIDUS and U+FF0E FULLWIDTH FULL
    # STOP survive any ASCII-based check untouched, then NFKC folds them
    # to "/" and "." — so a name we called clean becomes "../etc/passwd"
    # the moment a database collation, a macOS filesystem or a caller's
    # own normalize() touches it. U+2026 folds to "..." the same way.
    #
    # Doing it *after* the strip above is what keeps the result a fixed
    # point. Those format characters block canonical composition, so
    # deleting them can leave a base and its combining mark composable:
    # normalizing first and deleting after returned a name that NFKC
    # still had work to do on. Nothing below this line removes a
    # non-ASCII character, and NFKC provably never emits a character in
    # ``_STRIPPED_CATEGORIES`` (checked across all 0x110000 code points),
    # so a single pass in this order is sufficient.
    filename = unicodedata.normalize("NFKC", filename)

    filename = nt_basename(posix_basename(filename))

    filename = filename.strip(_FRAMING_CHARS)
    filename = _UNSAFE_CHARS_RE.sub("_", filename)

    if len(filename) > max_length:
        truncated = filename[:max_length]
        # Keep the extension when there is one worth keeping: a dot that
        # isn't leading, short enough to be an extension, and leaving room
        # for at least one character of name.
        last_dot = filename.rfind(".")
        if last_dot > 0:
            ext = filename[last_dot:]
            if len(ext) <= 10 and max_length - len(ext) > 0:
                truncated = filename[: max_length - len(ext)] + ext
        # Cutting can expose a new trailing dot (``"ab.cd"`` capped at 3
        # gives ``"ab."``), so strip once more — otherwise sanitizing an
        # already-sanitized name would keep changing it.
        filename = truncated.strip(_FRAMING_CHARS)

    return filename or None
