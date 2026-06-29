"""
RFC 5322 / MIME parser producing a JMAP Email object (RFC 8621 §4).

Lenient by design: real-world inbound MIME comes from every MTA on the
planet and routinely violates RFC 5322 / 5321 / 2045 / 2046 / 2047 / 2231.
We parse with the stdlib ``compat32`` policy and recover what we can
from malformed Content-Transfer-Encoding, missing charsets, broken
structural delimiters, and obs-syntax address headers. Strict outbound
composition is the composer's job (see ``composer.py`` and ``README.md``
— strict compose, lenient parse).

**Defense posture**: this parser must be hardened standalone. SMTP-in
is not the only feed — EML uploads, mbox / PST / IMAP imports,
forwards, drafts, etc. all reach this code directly without any
SMTP-layer normalization. Anything we want to defend against (bare-LF
end-of-DATA, NUL in headers, MIME bombs, smuggling, header injection)
must be guarded here, not assumed upstream.
"""

import base64
import email
import hashlib
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from datetime import timezone as dt_timezone
from email import policy as email_policy
from email.errors import HeaderParseError, MessageError
from email.header import decode_header as _stdlib_decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from ntpath import basename as nt_basename
from posixpath import basename as posix_basename
from typing import Any, cast

from .limits import DEFAULT_PARSE_LIMITS, ParseLimits
from .types import EmailAddress, EmailBodyPart, JmapEmail

# Resource limits — all chosen to match or exceed the equivalents in
# battle-tested mail servers. Real-world legitimate messages are well
# below these caps; adversarial inputs that exceed them are silently
# truncated or rejected per Postel's law.
#
# - Postfix ``mime_nesting_limit`` defaults to 100. Above this depth,
#   Python's ~1000-frame recursion limit becomes reachable through a
#   crafted ``multipart/mixed`` cascade.
# - Go's stdlib (after CVE-2022-41725 / CVE-2023-24536 / CVE-2023-45290)
#   caps multipart parts at 1000 per message via the
#   ``multipartmaxparts`` GODEBUG. Python's ``email`` package has no
#   equivalent; we enforce our own here.
# - Postfix ``header_size_limit`` is 102_400 bytes — the de-facto
#   ceiling we copy. Anything larger is truncated before decoding;
#   ``email`` package decoders are linear in input size, so a 10 MB
#   ``X-Foo`` header still works but burns wall-clock.
# - Postfix ``header_address_token_limit`` is 10_240 tokens. We cap
#   the *byte length* of an address-list header instead (100 KB,
#   roughly 5_000 typical addresses) — getaddresses is O(n) but the
#   per-tuple allocations stack up on huge inputs (Dovecot
#   CVE-2024-23184 was the same anti-pattern).
# ``message/rfc822`` nesting is implicitly bounded: we treat
# ``message/*`` parts as opaque attachments (we don't recurse into
# them in ``_parse_body_structure``), so a hostile chain of nested
# forwards can only hurt us via stdlib's ``Message.as_bytes()`` when
# we serialize the wrapped sub-message in ``_decoded_part_body``.
# That call catches ``RecursionError`` directly.
# Module-level mirrors of the default resource caps. Authoritative
# values live on :class:`jmap_email.limits.ParseLimits`; per-call
# overrides go through the ``limits=`` keyword on :func:`parse_email`
# / :func:`parse_addresses`. Module-level reassignment is not a
# supported tuning mechanism — it would race across threads and leak
# across unrelated callers in the same process.
MAX_MIME_NESTING_DEPTH = DEFAULT_PARSE_LIMITS.max_mime_nesting_depth
MAX_MIME_PARTS = DEFAULT_PARSE_LIMITS.max_mime_parts
MAX_HEADER_VALUE_BYTES = DEFAULT_PARSE_LIMITS.max_header_value_bytes
MAX_ADDRESS_LIST_BYTES = DEFAULT_PARSE_LIMITS.max_address_list_bytes

# Characters stripped from decoded display-names before they are
# surfaced. Header-injection vector: a downstream consumer that re-
# emits the display name (e.g. into an outbound forward / DSN) would
# otherwise smuggle a new header line. Same threat-model class as
# Apache James CVE-2024-21742 / Python CVE-2024-6923 on the compose
# side. Matches the composer's ``_HEADER_INJECTION_CHARS`` list: all
# C0 controls except TAB, plus DEL, NEL, and the U+2028 / U+2029
# Unicode line/paragraph separators.
_NAME_INJECTION_CHARS = (
    "".join(chr(c) for c in range(0x00, 0x20) if c != 0x09) + "\x7f\x85\u2028\u2029"
)
_NAME_INJECTION_TABLE = str.maketrans("", "", _NAME_INJECTION_CHARS)

logger = logging.getLogger(__name__)

# Stdlib ``compat32`` is the lenient end of the policy spectrum: it
# returns raw header strings rather than parsed structured objects, and
# tolerates the malformed input that ``policy.default`` would surface
# as defects-into-exceptions. We pair it with raw-header access (see
# ``parse_email``) so non-ASCII 8-bit headers — surrogate-
# escaped by the BytesFeedParser into the raw store — get reassembled
# back to UTF-8 instead of being mangled into U+FFFD by the public
# ``Message.items()`` view.
_PARSE_POLICY = email_policy.compat32


def _strip_nul_bytes(text: str) -> str:
    """Strip NUL bytes from text.

    PostgreSQL text fields cannot store NUL (0x00) bytes.
    This char is used to mark the end of a string in C language
    and is not valid in PostgreSQL text fields. Furthermore the
    RFC 5322 section 4 defines it as an obsolete character.
    https://datatracker.ietf.org/doc/html/rfc5322#page-31
    """
    return text.replace("\x00", "") if text else ""


def _repair_surrogate_escaped(text: str) -> str:
    """Repair raw 8-bit header bytes that stdlib decoded via surrogateescape.

    The stdlib ``compat32`` parser stores header bytes that fall outside
    ASCII as Python "lone surrogate" code points (U+DC80..U+DCFF) — the
    standard ``surrogateescape`` error-handler shape. The raw byte
    sequence is recoverable, and most real-world senders that put raw
    8-bit in headers use UTF-8 (e.g. Gmail Takeout's ``X-Gmail-Labels``).
    Round-trip bytes → UTF-8 when possible, fall back to latin-1 so we
    never lose data.
    """
    if not any(0xDC80 <= ord(c) <= 0xDCFF for c in text):
        return text
    try:
        as_bytes = text.encode("utf-8", errors="surrogateescape")
    except UnicodeEncodeError:
        return text
    try:
        return as_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return as_bytes.decode("latin-1")
        except UnicodeDecodeError:
            return text


def decode_rfc2047_header(header_text: str) -> str:
    """Decode RFC 2047 encoded-words in a header value to a single string.

    Wraps :func:`email.header.decode_header` (stdlib) with three
    additional guarantees the bare stdlib helper doesn't give:
    a single string return type (not a list of fragments); recovery
    from ``HeaderParseError`` on malformed base64 inside an encoded-
    word so a single bad ``=?…?b?…?=`` doesn't torpedo the rest of
    the parse; and surrogate-escape repair of raw 8-bit bytes left
    by the ``compat32`` policy.

    Folding CRLF+WSP is unfolded to single spaces; other internal
    whitespace runs are preserved.
    """
    if not header_text:
        return ""

    header_text_str = str(header_text)
    # Stdlib's ``email.header.decode_header`` returns a list of
    # ``(decoded_string, charset)`` pairs (charset is ``None`` when the
    # fragment was not encoded). It raises ``HeaderParseError`` on
    # malformed base64 inside an encoded-word — recover by passing the
    # raw text through with surrogate-repair so the rest of the header
    # survives.
    try:
        decoded_parts = _stdlib_decode_header(header_text_str)
    except HeaderParseError:
        return _repair_surrogate_escaped(header_text_str)

    result_parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            # Decode bytes using charset or fallbacks
            if not charset or charset == "unknown-8bit":
                try:
                    result_parts.append(part.decode("utf-8", errors="replace"))
                except UnicodeDecodeError:
                    result_parts.append(part.decode("latin-1", errors="replace"))
            else:
                try:
                    result_parts.append(part.decode(charset, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    result_parts.append(part.decode("utf-8", errors="replace"))
        else:
            # Part is already a string. Repair surrogate-escaped 8-bit
            # (see ``_repair_surrogate_escaped``).
            result_parts.append(_repair_surrogate_escaped(part))

    # Join the decoded parts. Unfold CRLF+WSP per RFC 5322 §2.2.3.
    # Internal multi-space / TAB runs are preserved so callers needing
    # spec-precise text get it; the header-injection defense (stripping
    # U+2028 / U+2029 / NEL / VT / FF) lives in ``_clean_address_pair``
    # rather than this generic decoder.
    full_result = "".join(result_parts)
    return re.sub(r"\r?\n[ \t]+", " ", full_result)


def _strip_cfws(value: str) -> str:
    """Strip RFC 5322 CFWS (comments + folding white space) from ``value``.

    Comments are ``(...)`` blocks that may nest per RFC 5322 §3.2.3.
    We walk the string tracking paren depth and drop any character at
    depth >= 1 along with the parens themselves. Folding white space
    (CRLF + WSP, plus the post-encoded-word collapse of consecutive
    whitespace) is reduced to a single space.

    Used to normalise ``messageId`` / ``inReplyTo`` / ``references``
    (RFC 8621 §4.1.2.1 "Comments and/or folding white space (CFWS) and
    surrounding angle brackets are removed") and ``Content-ID``
    (same requirement, §4.1.4).
    """
    if not value:
        return ""
    out: list[str] = []
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n and depth >= 1:
            # quoted-pair inside a comment — skip the backslash and the
            # escaped char so the consumed content stays inside the
            # comment (it would otherwise leak ``)``s out).
            i += 2
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")" and depth >= 1:
            depth -= 1
            i += 1
            continue
        if depth >= 1:
            i += 1
            continue
        out.append(ch)
        i += 1
    # Collapse folding WSP (any run of whitespace) to a single SP, then
    # strip leading/trailing.
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _strip_name_quotes(name: str) -> str:
    """
    Strip surrounding single quotes from display names.

    RFC 5322 uses double quotes for display names with special characters,
    and the parser correctly strips those. However, some email clients
    incorrectly use single quotes, which the parser preserves. We strip
    them for consistency.

    Examples:
        "'John Doe'" -> "John Doe"
        "John Doe" -> "John Doe"
        "'John's Name'" -> "John's Name" (only strips surrounding quotes)
    """
    if name and len(name) >= 2 and name.startswith("'") and name.endswith("'"):
        return name[1:-1]
    return name


def _contains_group_syntax(address_str: str) -> bool:
    """
    Check if the address string contains RFC 5322 group syntax or malformed variants.

    Group syntax format: "Group Name: addr1, addr2;" or "undisclosed-recipients:;"
    Also handles malformed variants like "undisclosed-recipients:>" (using > instead of ;)
    Returns True if any group-like syntax pattern is found.

    The pattern is: word(s) followed by : then addresses/empty then ; or >
    Key insight: the group name comes AFTER any comma separator, so we look for
    patterns like "name:...;" where "name" doesn't contain @.
    """
    stripped = address_str.strip()
    # Check for proper group syntax (;) or malformed variant (>)
    if ";" not in stripped and ":>" not in stripped:
        return False

    # Use regex to find group patterns: non-@ chars followed by : then anything then ; or >
    # This handles "undisclosed-recipients:;", "Group: addr1, addr2;", ":;", and ":>"
    # Pattern: optional non-@ non-: chars, then :, then anything, then ; or just :>
    group_pattern = re.compile(r"[^@:,]*:([^;]*;|>)")
    return bool(group_pattern.search(stripped))


def _remove_group_syntax(address_str: str) -> str:
    """
    Remove RFC 5322 group syntax from address string, extracting inner addresses.

    "Group: addr1, addr2;" -> "addr1, addr2"
    "undisclosed-recipients:;" -> ""
    "user@a.com, Group: b@c.com;" -> "user@a.com, b@c.com"
    "user@a.com, undisclosed-recipients:;" -> "user@a.com"
    "undisclosed-recipients:>" -> "" (malformed variant)
    """
    stripped = address_str.strip()
    if ";" not in stripped and ":>" not in stripped:
        return stripped

    # Use regex to find and process group patterns
    # Group pattern: optional word(s) without @ or : or ,, followed by :, then content, then ;
    # Also handle malformed :> variant (empty group with > instead of ;)
    # We replace "GroupName: content;" with just "content"
    group_pattern = re.compile(r"[^@:,]*:([^;]*);")

    def replace_group(match):
        inner = match.group(1).strip()
        return inner if inner else ""

    result = group_pattern.sub(replace_group, stripped)

    # Handle malformed :> pattern (remove "name:>" entirely as it's an empty malformed group)
    malformed_pattern = re.compile(r"[^@:,]*:>")
    result = malformed_pattern.sub("", result)

    # Clean up: remove empty entries, extra commas, whitespace
    parts = [p.strip() for p in result.split(",") if p.strip()]
    return ", ".join(parts)


def _clean_address_pair(name: str, addr: str) -> tuple[str, str]:
    """Post-process a single (name, addr) tuple from ``getaddresses``.

    - Decode RFC 2047 encoded-words in the display name (idempotent on
      already-decoded text).
    - Strip stray single quotes (some clients quote display names with
      ``'…'`` instead of ``"…"``).
    - Strip header-injection chars (CR/LF/NUL/NEL/U+2028/U+2029 and
      other C0 controls except TAB) from the decoded display name. A
      downstream consumer re-emitting the name into an outbound header
      would otherwise smuggle a new header line. Same threat-model
      class as Apache James CVE-2024-21742 / CPython CVE-2024-6923.
    """
    name_decoded = _strip_name_quotes(decode_rfc2047_header(name or ""))
    name_safe = name_decoded.translate(_NAME_INJECTION_TABLE)
    return name_safe, addr or ""


def _is_plausible_addr(addr: str) -> bool:
    """Cheap shape-validity check for an addr-spec.

    Defends against two real attacks where stdlib's lenient parser
    surfaces something with an ``@`` that *looks* like an address but
    is actually structural-character residue:

    1. **Encoded-word residue.** A broken encoded-word that span-folds
       across a CRLF (gh-114906) leaves trailing ``?=`` in whatever
       follows. ``=?utf-8?q?safe\\nBcc:_leak@evil.com?=`` makes stdlib
       surface a ``Bcc`` header whose value is ``_leak@evil.com?=`` —
       it has ``@``, but the trailing ``?=`` betrays the residue.
       Downstream code routing on the parsed bcc list would otherwise
       attempt to send to a fake recipient.

    2. **CR/LF / whitespace** inside the addr. RFC 5322 §3.4.1 forbids
       FWS inside addr-spec; if it appears, stdlib's lenient mode
       split somewhere unexpected.
    """
    if not addr or "@" not in addr:
        return False
    if "?=" in addr or "=?" in addr:
        return False  # encoded-word residue
    # CR/LF/HT/NUL inside addr-spec are all RFC 5322 §3.4.1 violations
    # AND header-injection vectors (Mailsploit-class NUL truncation; the
    # composer's _sanitize_header_value strips on the way out but we
    # also defend at the input boundary so downstream consumers — DB
    # inserts, log lines, JSON serialisers — never see them).
    if any(c in addr for c in ("\r", "\n", "\t", "\x00")):
        return False
    return True


def _pick_best_address(parsed) -> tuple[str, str] | None:
    """Pick the most-likely-intended address from a getaddresses result.

    Stdlib's lenient splitter emits multiple ``(name, addr)`` tuples
    when the input is ambiguous — most notoriously for CVE-2023-27043-
    style inputs like ``"foo@evil.com" <real@you.com>`` where the
    quoted display-name itself resembles an addr-spec. The first tuple
    is the *display-name-as-addr-spec*, not the angle-addr; allow/deny
    logic that trusts ``parsed[0]`` would route mail to ``foo@evil.com``
    instead of ``real@you.com``.

    Prefer the LAST tuple with a plausible address — it corresponds
    to the trailing angle-addr in the wire input, which is the
    authoritative addr-spec under RFC 5322 §3.4.
    """
    candidate: tuple[str, str] | None = None
    for raw_name, raw_addr in parsed:
        if _is_plausible_addr(raw_addr):
            candidate = (raw_name, raw_addr)
    return candidate


def parse_address(
    address_str: str,
    *,
    lenient: bool = False,
) -> tuple[str, str]:
    """Parse an email address that might include a display name.

    Strict by default. The return value is always either:

    - ``(name, addr)`` where ``addr`` passes the shape check (contains
      ``@``, no encoded-word residue, no CR/LF/TAB/NUL); or
    - ``("", "")`` when the input cannot be parsed into a valid
      addr-spec.

    Pass ``lenient=True`` to fall back to ``("", address_str.strip())``
    when the addr-spec check fails — the right choice for
    archive-import paths that must preserve the original wire bytes
    even when they're invalid (PST / mbox / EML reconstruction). Do
    NOT use lenient mode for entry-point validation (CLI flags, web
    form input): under ``lenient=True``, ``parse_address("no-at")``
    returns ``("", "no-at")``, which silently makes garbage look like
    a valid address.

    Security note: this function deliberately does NOT pre-decode RFC
    2047 encoded-words on the full input — the display-name is decoded
    *after* the address is split out. Pre-decoding would let a sender
    smuggle structural characters (``@``, ``<``, ``>``, ``,``) into the
    addr-spec via an encoded-word in the local-part (the PortSwigger
    "Splitting the Email Atom" technique, DEF CON 32 2024). Callers
    should pass the raw (surrogate-repaired) header value, not a value
    already run through ``decode_rfc2047_header``.

    Args:
        address_str: String containing an email address, possibly with
            a display name.
        lenient: When ``True``, return the input as the ``email`` field
            on parse failure instead of ``("", "")``. Default ``False``.

    Returns:
        Tuple of ``(display_name, email_address)``.

    Examples:
        >>> parse_address('user@example.com')
        ('', 'user@example.com')
        >>> parse_address('User <user@example.com>')
        ('User', 'user@example.com')
        >>> parse_address('no-at-sign')
        ('', '')
        >>> parse_address('no-at-sign', lenient=True)
        ('', 'no-at-sign')
    """
    if not address_str:
        return "", ""

    # Repair raw 8-bit (surrogate-escaped) bytes so getaddresses sees
    # real Unicode chars. We deliberately stop short of full RFC 2047
    # decoding — see the security note in the docstring.
    address_str = _repair_surrogate_escaped(address_str)

    # Handle RFC 5322 group syntax (e.g., "undisclosed-recipients:;")
    # These should return empty for single address parsing.
    if _contains_group_syntax(address_str):
        return "", ""

    # ``strict=False`` makes getaddresses tolerate the malformed and
    # obs-syntax inputs we routinely see in real inbound mail.
    # Defensive guard: stdlib's ``_parseaddr.getcomment`` recurses
    # into nested ``(...)`` blocks without bound (CVE-2002-1337
    # Sendmail-crackaddr shape on input alone). 10k-deep nested
    # comments blow Python's recursion limit. Catch and degrade
    # rather than letting RecursionError propagate.
    try:
        parsed = getaddresses([address_str], strict=False)
    except RecursionError:
        logger.warning("RecursionError in getaddresses; returning empty result")
        return "", ""

    if not parsed:
        return ("", address_str.strip()) if lenient else ("", "")

    best = _pick_best_address(parsed)
    if best is None:
        return ("", address_str.strip()) if lenient else ("", "")

    name, addr = _clean_address_pair(*best)
    return name, addr


def parse_addresses(
    addresses_str: str,
    *,
    limits: ParseLimits = DEFAULT_PARSE_LIMITS,
) -> list[tuple[str, str]]:
    """
    Parse multiple email addresses from a comma-separated string.

    Handles RFC 5322 group syntax (e.g., "Group: addr1, addr2;") by
    extracting the addresses within groups.

    Strict on each entry: tuples whose addr-spec fails the shape check
    (no ``@``, encoded-word residue, embedded CR/LF, etc.) are
    silently dropped — callers comparing the returned length to the
    input's comma count will see a mismatch. There is no ``lenient=``
    knob on this entry point: a multi-address header always wants
    address-tuple recovery, never "the whole header was garbage so
    return it as a single fake address."

    Args:
        addresses_str: Comma-separated string of email addresses.
        limits: Per-call resource caps. See :class:`ParseLimits`. Pass
            a custom instance to widen / tighten the address-list byte
            cap independently of any other parse call in the process.

    Returns:
        List of tuples, each containing (display_name, email_address).
        Entries that fail the addr-spec shape check are omitted.
    """
    if not addresses_str:
        return []

    # Defensive byte cap. ``getaddresses`` is O(n) but a 50 MB
    # ``To:`` would allocate millions of tuples (see Dovecot
    # CVE-2024-23184 — same anti-pattern in C). The default 100 KB cap
    # holds ~5_000 typical addresses; well above any legitimate
    # mailing-list expansion that lands in a single header.
    cap = limits.max_address_list_bytes
    if len(addresses_str) > cap:
        logger.warning("Address-list header exceeds %d bytes; truncating", cap)
        addresses_str = addresses_str[:cap]

    # Repair raw 8-bit (surrogate-escaped) bytes. See
    # ``parse_address`` for why we deliberately stop short of
    # full RFC 2047 decoding here.
    addresses_str = _repair_surrogate_escaped(addresses_str)

    # Handle RFC 5322 group syntax (e.g., "undisclosed-recipients:;" or "Group: a@b.com;")
    # Extract addresses from within groups to avoid parser warnings.
    if _contains_group_syntax(addresses_str):
        addresses_str = _remove_group_syntax(addresses_str)
        if not addresses_str:
            return []  # Empty group like "undisclosed-recipients:;"

    try:
        parsed = getaddresses([addresses_str], strict=False)
    except RecursionError:
        # Defense vs CVE-2002-1337 Sendmail-crackaddr-shape inputs:
        # stdlib's ``_parseaddr.getcomment`` recurses without bound
        # on nested ``(...)`` comments and can blow Python's recursion
        # limit. Degrade to empty rather than letting the error
        # propagate.
        logger.warning("RecursionError in getaddresses; returning empty list")
        return []

    result: list[tuple[str, str]] = []
    for raw_name, raw_addr in parsed:
        # ``_is_plausible_addr`` covers the @-check and additionally
        # rejects encoded-word residue / FWS-tainted addresses (see
        # the docstring there for the threat model).
        if not _is_plausible_addr(raw_addr):
            continue
        name, addr = _clean_address_pair(raw_name, raw_addr)
        result.append((name, addr))
    return result


def parse_date(date_str: str) -> datetime | None:
    """
    Parse date string from email header.

    Args:
        date_str: Date string in RFC 5322 format

    Returns:
        Datetime object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        # Use email.utils which handles RFC 5322 date formats
        return parsedate_to_datetime(date_str)
    except (TypeError, ValueError) as e:  # Catch specific errors
        logger.warning("Could not parse date string '%s': %s", date_str, e)
        return None


def _infer_filename_from_content_type(content_type: str) -> str:
    """
    Infer a filename with extension from a MIME content type.
    Uses the most commonly used file extensions for each MIME type.

    Args:
        content_type: MIME type string (e.g., "image/png", "application/pdf")

    Returns:
        Filename with appropriate extension (e.g., "unnamed.png", "unnamed.pdf")
    """
    extension_map = {
        "text/plain": ".txt",
        "text/html": ".html",
        "text/csv": ".csv",
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "application/json": ".json",
        "application/xml": ".xml",
        "application/zip": ".zip",
    }
    ext = extension_map.get(content_type, "")
    return f"unnamed{ext}"


def _sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize an attachment filename, preserving the extension when truncating."""

    filename = nt_basename(posix_basename(filename))

    filename = filename.strip('"/.\\')

    # Remove null bytes and control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)

    # Remove dangerous characters
    filename = re.sub(r'[<>:"|?*\\/]', "_", filename)

    # Truncate while preserving extension
    if len(filename) > max_length:
        # Find the last dot for extension (but not at the start like .gitignore)
        last_dot = filename.rfind(".")
        if last_dot > 0:
            name = filename[:last_dot]
            ext = filename[last_dot:]
            # Only preserve extension if it's reasonable length (up to 10 chars including dot)
            if len(ext) <= 10:
                max_name_length = max_length - len(ext)
                if max_name_length > 0:
                    return name[:max_name_length] + ext
        return filename[:max_length]

    return filename


def _build_attachment_dict(
    body: Any,
    part_type: str,
    filename: str,
    disposition: str,
    content_id: str | None,
) -> dict[str, Any]:
    """
    Helper function to build an attachment dictionary.
    Converts body to bytes, computes SHA-256 hash, and constructs the attachment dict.

    Args:
        body: The part body (str or bytes)
        part_type: MIME type of the part
        filename: Name of the attachment file
        disposition: Content-Disposition value ("attachment", "inline", etc.)
        content_id: Content-ID if present

    Returns:
        Dictionary representing the attachment
    """
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body

    content_hash = hashlib.sha256(body_bytes).hexdigest()

    return {
        "type": part_type,
        "name": _sanitize_filename(filename) or "unnamed",
        "size": len(body_bytes),
        "disposition": disposition,
        "cid": content_id,
        "content": body_bytes,
        "sha256": content_hash,
    }


def _is_inline_media_type(content_type: str) -> bool:
    """
    Check if the content type is an inline media type (image/*, audio/*, video/*).

    Args:
        content_type: MIME type string (e.g., "image/png", "audio/mp3")

    Returns:
        True if the type is an inline media type
    """
    return (
        content_type.startswith("image/")
        or content_type.startswith("audio/")
        or content_type.startswith("video/")
    )


def _decoded_part_body(part: Message) -> Any | None:
    """Return the decoded body bytes of ``part`` (or ``None`` if unreadable).

    Real-world inbound carries Content-Transfer-Encoding headers that lie
    about the payload (quoted-printable bodies with raw 8-bit, base64 with
    stray whitespace, etc.). Stdlib's ``get_payload(decode=True)`` raises
    on some of those — we recover the raw payload bytes instead of giving
    up on the whole message.
    """
    try:
        body = part.get_payload(decode=True)
    except (ValueError, AssertionError, TypeError):
        body = None
    if body is not None:
        return body

    # Fall back to whatever the parser actually retained.
    try:
        raw = part.get_payload()
    except (ValueError, TypeError):
        return None
    if isinstance(raw, list):
        # ``message/*`` parts wrap one or more sub-Messages under
        # compat32 — we treat them as opaque attachments and serialize
        # the wrapped content back to bytes for storage.
        # ``message/rfc822`` typically has a single child; but
        # ``message/delivery-status`` (RFC 3464) carries multiple
        # per-recipient status blocks as stacked sub-Messages, so we
        # must concatenate them all to avoid silently dropping later
        # recipients' status. Real multipart containers also surface as
        # lists here but callers gate on maintype before touching the
        # body.
        if raw and part.get_content_maintype() == "message":
            try:
                chunks = [sub.as_bytes() for sub in raw if isinstance(sub, Message)]
                return b"\r\n".join(chunks) if chunks else None
            except (
                TypeError,
                ValueError,
                AttributeError,
                MessageError,
                RecursionError,
            ):
                # ``MessageError`` covers ``HeaderWriteError`` raised
                # by ``BytesGenerator.verify_generated_headers`` when
                # the wrapped sub-message contains a header value with
                # an unfolded newline. ``RecursionError`` covers
                # deeply-nested forward-of-forward chains that blow
                # CPython's recursion limit during MIME re-emission.
                return None
        return None
    if isinstance(raw, str):
        # Under compat32, ``BytesFeedParser`` stores raw 8-bit body
        # bytes as surrogate-escaped code points (U+DC80..U+DCFF). UTF-8
        # encoding with the ``surrogateescape`` error handler recovers
        # the original bytes losslessly — clean unicode text also
        # round-trips correctly through it.
        try:
            return raw.encode("utf-8", errors="surrogateescape")
        except (UnicodeEncodeError, AttributeError):
            return raw.encode("utf-8", errors="replace")
    return raw


def _get_part_info(part: Message) -> dict[str, Any]:
    """
    Extract relevant information from a MIME part for classification.

    Args:
        part: A stdlib ``email.message.Message`` part

    Returns:
        Dictionary with type, disposition, name, body, content_id, part_id
    """
    part_type = part.get_content_type() or "text/plain"

    # ``get_content_disposition`` returns the lowercased main token
    # ("attachment" / "inline" / None) per RFC 6266 — exactly what the
    # classifier downstream wants.
    disposition = part.get_content_disposition()

    # Filename: stdlib's ``get_filename`` handles RFC 2231 continuation
    # and RFC 2047 encoded-word filenames. Anything left over still goes
    # through ``decode_rfc2047_header`` for the encoded-word case
    # where the value sits on a Content-Type ``name=`` parameter.
    filename: str | None = None
    raw_filename = part.get_filename()
    if raw_filename:
        filename = decode_rfc2047_header(str(raw_filename).strip())

    if not filename:
        # Some senders put the filename on Content-Type ``name=`` only.
        name_param = part.get_param("name")
        if name_param:
            # ``get_param`` can return a (charset, lang, value) tuple
            # when the param is RFC 2231-encoded; unwrap.
            if isinstance(name_param, tuple):
                name_param = name_param[2]
            filename = decode_rfc2047_header(str(name_param).strip())

    # Content-ID per RFC 8621 §4.1.4: "CFWS and surrounding angle
    # brackets are removed". ``.strip("<>")`` alone would (a) strip
    # ALL leading/trailing < / > rather than a single outer pair and
    # (b) ignore inline ``(comment)`` blocks. Use the shared CFWS
    # helper, then peel a single outer angle-bracket pair.
    content_id_header = part.get("Content-ID")
    content_id: str | None = None
    if content_id_header:
        cleaned = _strip_cfws(str(content_id_header))
        if cleaned.startswith("<") and cleaned.endswith(">") and len(cleaned) >= 2:
            cleaned = cleaned[1:-1].strip()
        content_id = cleaned or None

    # Body bytes (or None for real multipart containers / unrecoverable).
    # ``is_multipart()`` returns True for message/* too, but we want the
    # body for those (so they can be surfaced as attachments).
    is_container = part.get_content_maintype() == "multipart"
    body = None if is_container else _decoded_part_body(part)

    # Declared charset for text/* parts. Guarded — ``get_content_charset``
    # parses RFC 2231 params and can raise on malformed input under
    # compat32. None means "no charset declared" (RFC 2045 §5.2 defaults
    # to us-ascii, but we treat None as "fall back to UTF-8 with replace"
    # which round-trips ASCII cleanly and is a safer default for the
    # garden-variety untagged-UTF-8 senders we see in practice).
    try:
        charset = part.get_content_charset()
    except (LookupError, ValueError, UnicodeDecodeError, AttributeError):
        charset = None

    # Per-part Content-Language (RFC 3282) — comma-separated tag list.
    # Per RFC 8621 §4.1.4 the JMAP shape is ``String[] | null``.
    language_header = part.get("Content-Language")
    language: list[str] | None = None
    if language_header:
        language = [
            tag.strip() for tag in str(language_header).split(",") if tag.strip()
        ] or None

    # Per-part Content-Location (RFC 2557). String | null per spec.
    location_header = part.get("Content-Location")
    location = str(location_header).strip() if location_header else None
    if not location:
        location = None

    # Per-part header list — JMAP EmailHeader[] in document order with
    # Raw form values (NOT RFC 2047-decoded), mirroring the top-level
    # ``headers`` projection in §4.1.2.
    part_headers: list[dict[str, str]] = []
    try:
        for k, v in part.raw_items():
            # Mirror the top-level headers pipeline: repair stdlib's
            # surrogate-escape mangling of 8-bit bytes, then strip NULs
            # (PostgreSQL TEXT can't carry \x00; downstream stores fail
            # asymmetrically if part headers do and message headers
            # don't).
            part_headers.append(
                {
                    "name": k,
                    "value": _strip_nul_bytes(_repair_surrogate_escaped(str(v))),
                }
            )
    except AttributeError:
        # Defensive: synthesized parts may not expose raw_items().
        for k, v in part.items():
            part_headers.append({"name": k, "value": _strip_nul_bytes(str(v))})

    return {
        "type": part_type,
        "disposition": disposition,
        "name": filename,
        "body": body,
        "charset": charset,
        "content_id": content_id,
        "language": language,
        "location": location,
        "part_headers": part_headers,
        "part_id": "",
    }


def _build_body_part_dict(part_info: dict[str, Any]) -> tuple[EmailBodyPart, bool]:
    """Build a JMAP ``EmailBodyPart`` for textBody/htmlBody arrays.

    Returns ``(part, encoding_problem)``. ``encoding_problem`` is True
    when the text decode hit a missing-charset / wrong-charset fallback;
    callers route it into ``bodyValues[partId].isEncodingProblem``.

    Per RFC 8621 §4.1.4: ``partId``, ``size``, ``name``, ``type``,
    ``charset``, ``disposition``, ``cid`` are first-class fields. We
    additionally inline ``content`` (the decoded body) for ergonomic
    direct access; the ``body_values=True`` opt-in strips it back out.
    """
    body = part_info["body"]
    part_type = part_info["type"]

    # Binary types (images, audio, video) — base64-encode for JSON transport.
    encoding_problem = False
    if _is_inline_media_type(part_type):
        if body is None:
            content = ""
            size = 0
        elif isinstance(body, bytes):
            content = base64.b64encode(body).decode("ascii")
            size = len(body)
        else:
            content = base64.b64encode(body.encode("latin-1")).decode("ascii")
            size = len(body.encode("latin-1"))
    # Text types — decode using the part's declared charset (RFC 2045)
    # so Windows-1252, ISO-8859-*, GB2312 etc. survive intact. Falls
    # back to UTF-8 with replacement on missing/unknown charsets so
    # untagged 8-bit input still produces a printable string.
    elif body is not None and not isinstance(body, str):
        charset = part_info.get("charset") or "utf-8"
        try:
            content = body.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            content = body.decode("utf-8", errors="replace")
            encoding_problem = True
        size = len(body)
    else:
        content = body or ""
        size = len(content.encode("utf-8")) if content else 0

    part = EmailBodyPart(
        partId=part_info["part_id"],
        # ``blobId`` is server-set in real JMAP (identifies a blob in the
        # store). The library has no blob store; we emit ``None`` so the
        # field shape matches the spec and the caller can assign one
        # before sending the object to a JMAP server.
        blobId=None,
        type=part_type,
        size=size,
        name=part_info["name"] or None,
        charset=part_info.get("charset") or None,
        disposition=part_info["disposition"] or None,
        cid=part_info["content_id"],
        language=part_info.get("language"),
        location=part_info.get("location"),
        headers=part_info.get("part_headers") or [],
        # Leaf parts have no children.
        subParts=None,
        content=_strip_nul_bytes(content),
    )
    return part, encoding_problem


def _build_attachment_from_part_info(
    part_info: dict[str, Any], disposition_override: str = "attachment"
) -> EmailBodyPart:
    """Build a JMAP ``EmailBodyPart`` for the attachments array."""
    disposition = part_info["disposition"] or disposition_override
    raw_filename = part_info["name"]
    # JMAP spec: ``name`` is ``String | null``. We don't substitute
    # ``"unnamed"`` here — if a downstream consumer wants a default,
    # they can synthesize one. We also map an empty sanitized result
    # to ``None`` so callers don't have to ``or "fallback"`` it.
    body_bytes = part_info["body"] or b""
    if isinstance(body_bytes, str):
        body_bytes = body_bytes.encode("utf-8")
    sanitized_name = _sanitize_filename(raw_filename) if raw_filename else ""
    return {
        "partId": part_info["part_id"],
        "blobId": None,
        "type": part_info["type"],
        "size": len(body_bytes),
        "name": sanitized_name or None,
        "charset": part_info.get("charset") or None,
        "disposition": disposition,
        "cid": part_info["content_id"],
        "language": part_info.get("language"),
        "location": part_info.get("location"),
        "headers": part_info.get("part_headers") or [],
        "subParts": None,
        # Project extensions: inline bytes + sha256 so callers without a
        # blob store can still index / re-emit the part. See the
        # ``EmailBodyPart`` docstring in :mod:`jmap_email.types`.
        "content": body_bytes,
        "sha256": hashlib.sha256(body_bytes).hexdigest(),
    }


def _build_body_structure(
    message: Message, limits: ParseLimits
) -> EmailBodyPart | None:
    """Recursively build a JMAP ``bodyStructure`` tree.

    Per RFC 8621 §4.1.4, ``bodyStructure`` is the entire ``EmailBodyPart``
    tree rooted at the top-level part. Each multipart container exposes
    its children as ``subParts``; leaf parts carry the same fields as
    ``textBody`` / ``attachments`` entries (without ``content`` here —
    content lives in ``bodyValues`` when requested).

    Returns ``None`` on walk error.
    """
    try:
        return _build_body_structure_node(
            message, ["1"], counter={"parts": 0}, limits=limits
        )
    except (RecursionError, ValueError, TypeError, AttributeError):
        return None


def _build_body_structure_node(
    part: Message,
    path: list[str],
    *,
    limits: ParseLimits,
    depth: int = 0,
    counter: dict[str, int] | None = None,
) -> EmailBodyPart:
    """Build a single node of the bodyStructure tree.

    Enforces the same depth + part-count caps as the default body walk
    (``_parse_body_structure``). A flat multipart with a million sub-
    parts under one container would otherwise bypass
    ``limits.max_mime_parts`` when the caller opted into
    ``body_structure=True``.

    Per RFC 8621 §4.1.4: ``partId`` (and ``blobId``) MUST be ``null``
    if and only if the part is ``multipart/*``.
    """
    if counter is None:
        counter = {"parts": 0, "next_part_id": 0}
    counter.setdefault("next_part_id", 0)
    is_multipart = part.get_content_maintype() == "multipart"

    # Leaf partIds must match the flat counter assigned by
    # ``_parse_body_structure`` so ``bodyStructure["partId"]`` can be
    # used as a lookup key into ``bodyValues`` and against the entries
    # in ``textBody`` / ``htmlBody`` / ``attachments`` (RFC 8621 §4.1.4).
    # The hierarchical ``path`` is kept only for the truncation walk's
    # diagnostic value — it is not exposed.
    if not is_multipart:
        counter["next_part_id"] += 1
        leaf_part_id: str | None = str(counter["next_part_id"])
    else:
        leaf_part_id = None

    if (
        depth > limits.max_mime_nesting_depth
        or counter["parts"] >= limits.max_mime_parts
    ):
        # Truncated stub: still spec-shaped (null partId/blobId for
        # multipart) so consumers don't have to special-case it.
        return {
            "partId": leaf_part_id,
            "blobId": None,
            "type": part.get_content_type() or "text/plain",
            "size": 0,
            "name": None,
            "charset": None,
            "disposition": None,
            "cid": None,
            "language": None,
            "location": None,
            "headers": [],
            "subParts": [] if is_multipart else None,
        }
    counter["parts"] += 1

    info = _get_part_info(part)
    info["part_id"] = leaf_part_id or ""
    node: EmailBodyPart = EmailBodyPart(
        # multipart/* parts: partId and blobId are null per RFC 8621 §4.1.4
        # "if and only if". Leaf parts get a stable flat id matching
        # the one assigned in ``textBody`` / ``htmlBody`` / ``attachments``.
        partId=leaf_part_id,
        blobId=None,
        type=info["type"],
        size=0 if is_multipart else len(info["body"] or b""),
        name=info["name"] or None,
        charset=info["charset"] or None,
        disposition=info["disposition"] or None,
        cid=info["content_id"],
        language=info.get("language"),
        location=info.get("location"),
        headers=info.get("part_headers") or [],
        subParts=None,
    )
    if is_multipart:
        # Truncate the subParts list at the part-count cap. The deep-cap
        # check at function entry catches one runaway level; this loop-
        # level cap stops a single flat multipart with a million siblings
        # from emitting a million stub nodes (each stub costs memory
        # even if not recursed into).
        children = _subparts(part)
        sub_nodes: list[EmailBodyPart] = []
        for i, child in enumerate(children):
            if counter["parts"] >= limits.max_mime_parts:
                logger.warning(
                    "MIME part count exceeds limit %d; truncating bodyStructure",
                    limits.max_mime_parts,
                )
                break
            sub_nodes.append(
                _build_body_structure_node(
                    child,
                    path + [str(i + 1)],
                    depth=depth + 1,
                    counter=counter,
                    limits=limits,
                )
            )
        node["subParts"] = sub_nodes
    return node


def _subparts(part: Message) -> list[Message]:
    """Return the list of immediate sub-parts of ``part`` (empty if none).

    A multipart container's payload is a list of ``Message`` objects;
    a leaf part's payload is a str/bytes. We never want str/bytes to be
    interpreted as a sub-part list, so guard explicitly.
    """
    if not part.is_multipart():
        return []
    payload = part.get_payload()
    if not isinstance(payload, list):
        return []
    return [p for p in payload if isinstance(p, Message)]


def _parse_body_structure(
    parts: list[Message],
    multipart_type: str,
    in_alternative: bool,
    html_body: list[EmailBodyPart] | None,
    text_body: list[EmailBodyPart] | None,
    attachments: list[EmailBodyPart],
    *,
    limits: ParseLimits,
    depth: int = 0,
    counter: dict[str, Any] | None = None,
    parent_boundaries: tuple[str, ...] = (),
) -> None:
    """
    Recursively parse MIME structure following JMAP spec algorithm (Section 4.1).

    This implements the parseStructure algorithm from the JMAP specification,
    with a modification: inline media types are NOT added to attachments when
    one of textBody/htmlBody is null (unlike the spec example).

    Args:
        parts: List of MIME parts to process
        multipart_type: Type of parent multipart (mixed/alternative/related)
        in_alternative: Whether we're inside a multipart/alternative
        html_body: List to append HTML body parts (or None if nullified)
        text_body: List to append text body parts (or None if nullified)
        attachments: List to append attachment parts
        depth: Current recursion depth (guards against MIME bombs that
            would otherwise blow Python's recursion limit).
        counter: Shared mutable counter dict tracking total visited
            parts across the whole walk. Caps prevent
            ``multipartmaxparts``-style DoS at low depth (e.g. 1000
            flat parts that wouldn't trip the depth guard).
        parent_boundaries: MIME boundaries of every multipart ancestor on
            the current path. If a child multipart re-declares one of
            these boundaries we refuse to recurse — the inner delimiters
            collide with an ancestor's, so the inner tree is ambiguous
            and parsers will disagree on what content it holds (the
            Mailsploit / body-smuggling class). Dropping the inner tree
            is safer than surfacing parts that other receivers might
            interpret differently.
    """
    if counter is None:
        counter = {"parts": 0, "next_part_id": 0}
    counter.setdefault("next_part_id", 0)

    # Hard depth cap to defeat MIME-bomb style inputs (deeply nested
    # multiparts crafted to exhaust CPython's recursion limit). Below
    # the cap, real-world legitimate messages are unaffected.
    if depth > limits.max_mime_nesting_depth:
        logger.warning(
            "MIME nesting depth %d exceeds limit %d; truncating walk",
            depth,
            limits.max_mime_nesting_depth,
        )
        return

    # Track lengths for multipart/alternative fallback
    text_length = len(text_body) if text_body is not None else -1
    html_length = len(html_body) if html_body is not None else -1

    for i, part in enumerate(parts):
        if counter["parts"] >= limits.max_mime_parts:
            logger.warning(
                "MIME part count exceeds limit %d; truncating walk",
                limits.max_mime_parts,
            )
            return
        counter["parts"] += 1
        part_type = (part.get_content_type() or "text/plain").lower()
        # ``part.is_multipart()`` is True for both ``multipart/*`` *and*
        # ``message/*`` (since stdlib parses message/rfc822 recursively
        # into a sub-Message). For the JMAP structure walk we only want
        # to recurse into real multipart containers — a message/rfc822
        # sub-message must be surfaced as a single attachment, not
        # exploded into its inner parts.
        is_multipart = part.get_content_maintype() == "multipart"

        # Get part info for classification, assigning a stable partId.
        part_info = _get_part_info(part)
        # Multipart containers don't get a partId in our flattened
        # output (they have no body content). Leaf parts get a stable
        # incrementing ID.
        if not is_multipart:
            counter["next_part_id"] += 1
            part_info["part_id"] = str(counter["next_part_id"])

        # Determine if this is an inline body part (not attachment)
        # Per JMAP spec: disposition != "attachment" AND
        # (type is text/plain OR text/html OR inline media) AND
        # (first part OR (not in related AND (is inline media OR no filename)))
        is_inline = (
            part_info["disposition"] != "attachment"
            and (
                part_type in {"text/plain", "text/html"}
                or _is_inline_media_type(part_type)
            )
            and (
                i == 0
                or (
                    multipart_type != "related"
                    and (_is_inline_media_type(part_type) or not part_info["name"])
                )
            )
        )

        if is_multipart:
            # Boundary-reuse defence (RFC 2046 §5.1.1: a boundary value
            # must not appear inside an encapsulated part). When an
            # inner multipart re-declares a boundary that an ancestor
            # already uses, the inner delimiters are ambiguous — strict
            # and lenient parsers will split the tree differently, so
            # an attacker can hide content that some receivers surface
            # and others don't. Refuse the recursion and drop the
            # opaque subtree.
            sub_boundary = part.get_boundary()
            if sub_boundary and sub_boundary in parent_boundaries:
                logger.warning(
                    "MIME boundary %r reused at depth %d; "
                    "marking structure ambiguous (body-smuggling defence)",
                    sub_boundary,
                    depth + 1,
                )
                # Once any ancestor's boundary is re-declared, stdlib's
                # split of the rest of the tree is no longer well-defined
                # — sibling text/plain parts at the outer level may
                # actually belong to the inner subtree depending on
                # which parser you ask. We flag the whole walk so the
                # top-level caller clears all collected bodies; safer
                # than letting some receivers process content others
                # would hide.
                counter["ambiguous_structure"] = 1
                continue

            # Recurse into multipart
            sub_multipart_type = part.get_content_subtype() or "mixed"
            sub_parts = _subparts(part)
            sub_parents = (
                (*parent_boundaries, sub_boundary)
                if sub_boundary
                else parent_boundaries
            )
            _parse_body_structure(
                sub_parts,
                sub_multipart_type,
                in_alternative or sub_multipart_type == "alternative",
                html_body,
                text_body,
                attachments,
                depth=depth + 1,
                counter=counter,
                limits=limits,
                parent_boundaries=sub_parents,
            )

        elif is_inline:
            # Handle inline parts based on context
            encoding_problems = counter.setdefault("encoding_problems", {})
            if multipart_type == "alternative":
                # In direct alternative: route based on type only
                if part_type == "text/plain":
                    if text_body is not None:
                        body_part, ep = _build_body_part_dict(part_info)
                        text_body.append(body_part)
                        if ep:
                            encoding_problems[part_info["part_id"]] = True
                elif part_type == "text/html":
                    if html_body is not None:
                        body_part, ep = _build_body_part_dict(part_info)
                        html_body.append(body_part)
                        if ep:
                            encoding_problems[part_info["part_id"]] = True
                else:
                    # Other types in alternative go to attachments
                    attachments.append(_build_attachment_from_part_info(part_info))
                continue

            # Outside alternative but within an alternative ancestor
            if in_alternative:
                # text/plain nullifies htmlBody locally
                if part_type == "text/plain":
                    html_body = None
                # text/html nullifies textBody locally
                if part_type == "text/html":
                    text_body = None

            # Push to both arrays if not nullified
            if text_body is not None or html_body is not None:
                body_part, ep = _build_body_part_dict(part_info)
                if ep:
                    encoding_problems[part_info["part_id"]] = True
                if text_body is not None:
                    text_body.append(body_part)
                if html_body is not None:
                    html_body.append(body_part)

            # NOTE: We intentionally skip the JMAP spec's condition:
            # if ((!textBody || !htmlBody) && isInlineMediaType) attachments.push(part)
            # This is our modification to not duplicate inline media in attachments

        else:
            # Non-inline parts go to attachments
            attachments.append(_build_attachment_from_part_info(part_info))

    # Handle multipart/alternative fallback:
    # If only one type was found, copy to the other array
    if (
        multipart_type == "alternative"
        and text_body is not None
        and html_body is not None
    ):
        # Found HTML part only - copy to textBody
        if text_length == len(text_body) and html_length != len(html_body):
            for j in range(html_length, len(html_body)):
                text_body.append(html_body[j])
        # Found text part only - copy to htmlBody
        if html_length == len(html_body) and text_length != len(text_body):
            for j in range(text_length, len(text_body)):
                html_body.append(text_body[j])


def _parse_message_content(
    message,
    *,
    limits: ParseLimits = DEFAULT_PARSE_LIMITS,
    defects: list[str] | None = None,
) -> dict[str, Any]:
    """Extract textBody / htmlBody / attachments from a message, JMAP-shaped.

    Uses the JMAP spec's parseStructure algorithm (RFC 8621 §4.1.4) to
    handle multipart structures (alternative / related / mixed). Inline
    media types (image/* etc.) stay in textBody/htmlBody rather than
    being demoted to attachments. ``Content-Disposition: attachment``
    forces the attachments array.

    Failures in the recursive walk are recorded in ``defects`` (when
    provided) so consumers see a programmatic signal beyond a log line.
    """
    result: dict[str, Any] = {"textBody": [], "htmlBody": [], "attachments": []}

    # Some malformed inputs end up as a ``Message`` with no Content-Type
    # at all and a raw str body. Stdlib defaults Content-Type to
    # ``text/plain`` so most paths just work, but we still keep the
    # explicit fallback for non-``Message`` inputs (tests poke us with
    # mocks).
    if not isinstance(message, Message):
        body = getattr(message, "body", None)
        if isinstance(body, str):
            result["textBody"].append(
                {
                    "partId": "1",
                    "blobId": None,
                    "type": "text/plain",
                    "size": len(body.encode("utf-8")),
                    "name": None,
                    "charset": None,
                    "disposition": None,
                    "cid": None,
                    "language": None,
                    "location": None,
                    "headers": [],
                    "subParts": None,
                    "content": _strip_nul_bytes(body),
                }
            )
        return result

    counter: dict[str, Any] = {"parts": 0, "next_part_id": 0}
    try:
        # Use the JMAP-style recursive parser
        # Wrap the message in a list and treat it as if inside multipart/mixed
        _parse_body_structure(
            [message],
            "mixed",
            False,
            result["htmlBody"],
            result["textBody"],
            result["attachments"],
            limits=limits,
            counter=counter,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error parsing message body structure: %s", e, exc_info=True)
        if defects is not None:
            defects.append("BodyStructureWalkError")

    # Boundary-reuse defence (Mailsploit class): once any inner multipart
    # re-declares an ancestor's boundary, stdlib's split of the rest of
    # the tree is ambiguous — sibling parts at outer levels may actually
    # belong to the hidden inner subtree. We clear everything so strict
    # and lenient receivers agree on what the message carries (nothing).
    if counter.get("ambiguous_structure"):
        if defects is not None:
            defects.append("BoundaryReuseAmbiguousStructure")
        return {
            "textBody": [],
            "htmlBody": [],
            "attachments": [],
            "encoding_problems": {},
        }

    result["encoding_problems"] = counter.get("encoding_problems") or {}
    return result


# ────────────────────────────────────────────────────────────────────
# JMAP shape helpers (RFC 8621 §4.1.2 — typed header projections)
# ────────────────────────────────────────────────────────────────────


def _jmap_addresses(pairs: list[tuple[str, str]]) -> list[EmailAddress] | None:
    """Convert a list of ``(name, addr)`` tuples to JMAP ``EmailAddress[]``.

    Per RFC 8621 §4.1.2.3, ``EmailAddress = {name: String|null, email: String}``.
    Returns ``None`` when ``pairs`` is empty so callers can distinguish
    "no header" from "header present but empty".
    """
    if not pairs:
        return None
    return [{"name": name or None, "email": addr} for name, addr in pairs]


def _jmap_single_address(name: str, addr: str) -> list[EmailAddress] | None:
    """Convert a single ``(name, addr)`` to a single-element list (or None).

    JMAP requires address-list fields to always be lists; even a header
    with one mailbox surfaces as a 1-element ``EmailAddress[]``.
    """
    if not addr:
        return None
    return [{"name": name or None, "email": addr}]


def _jmap_message_ids(raw_header_value: str) -> list[str] | None:
    """Split a Message-ID / In-Reply-To / References header into ``String[]``.

    Per RFC 8621 §4.1.2.1, ``MessageIds = String[]`` where each string is
    the msg-id with surrounding ``<>`` AND surrounding CFWS removed.
    Comments may appear between ids (``<id1@x> (forwarded) <id2@x>``),
    so we strip CFWS via :func:`_strip_cfws` before splitting on
    whitespace.

    Returns ``None`` when the header value is empty / absent.
    """
    if not raw_header_value:
        return None
    cleaned = _strip_cfws(raw_header_value)
    ids: list[str] = []
    for token in cleaned.split():
        stripped = token.strip()
        if stripped.startswith("<") and stripped.endswith(">") and len(stripped) >= 2:
            stripped = stripped[1:-1]
        if stripped:
            ids.append(stripped)
    return ids or None


def _jmap_iso_date(dt: datetime | None) -> str | None:
    """Format a datetime as RFC 8621 ``Date`` (ISO-8601 with offset).

    JMAP's ``Date`` keeps the original UTC offset (unlike ``UTCDate``
    which always normalizes to ``Z``). A naive datetime is treated as
    UTC. Returns ``None`` when ``dt`` is ``None`` so a missing Date
    header surfaces as ``null``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    # ``datetime.isoformat`` emits ``+HH:MM`` for fixed offsets and
    # ``+00:00`` for UTC — matching JMAP's ``Date`` exactly.
    return dt.isoformat()


def _nfc(text: str) -> str:
    """NFC-normalize text per RFC 8621 §4.1.2.4 ``Text`` form."""
    return unicodedata.normalize("NFC", text)


def _jmap_subject(raw_value: str | None) -> str | None:
    """Return the JMAP ``subject`` field: NFC-normalized text, ``None``
    when the Subject header was absent (preserves the spec's null-vs-
    empty distinction)."""
    if raw_value is None:
        return None
    return _nfc(_strip_nul_bytes(raw_value))


def _jmap_headers(raw_headers: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """Build the JMAP ``headers`` field: ``EmailHeader[]`` in document order.

    Per RFC 8621 §4.1.1 + §4.1.2 Raw form: ``EmailHeader = {name: String,
    value: String}`` where ``name`` preserves the wire case and ``value``
    is the **raw** header octets — surrogate-repaired, but NOT RFC 2047-
    decoded, NOT internal-whitespace-collapsed beyond the unfolding the
    spec mandates (CRLF+WSP → SP). Consumers needing decoded text
    should use the typed projections (``subject``, ``from``, etc.) or
    pass each ``value`` through :func:`decode_rfc2047_header` themselves.

    ``raw_headers`` is a list of ``(wire_name, decoded_value, raw_value)``
    tuples; we emit the raw_value slot for spec conformance and
    DKIM-canonicalisation / forensic-replay use cases.
    """
    return [
        {"name": wire_name, "value": raw_value}
        for wire_name, _, raw_value in raw_headers
    ]


def _compute_has_attachment(attachments: list[EmailBodyPart]) -> bool:
    """Compute the JMAP ``hasAttachment`` field.

    Per RFC 8621 §4.1.4: "A server SHOULD set ``hasAttachment`` to
    true if the attachments list contains at least one item that does
    not have ``Content-Disposition: inline``." The check is therefore
    "any disposition that isn't literally ``inline``" — covers items
    with ``attachment`` *and* any non-standard disposition value
    (``form-data``, etc.) that a real-world sender might emit.
    """
    return any(att.get("disposition") != "inline" for att in attachments)


_PREVIEW_MAX_LEN = 256
_PREVIEW_WS_RE = re.compile(r"\s+", re.UNICODE)
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.UNICODE)


def _compute_preview(
    text_body: list[EmailBodyPart], html_body: list[EmailBodyPart]
) -> str:
    """Compute the JMAP ``preview`` field: a single-line ≤ 256-char
    excerpt drawn from the first text body (or, if absent, the HTML
    body with tags stripped).

    Per RFC 8621 §4.1.4, ``preview`` is server-set, single-line,
    truncated to <=256 chars. We compute it on demand.
    """
    source = ""
    if text_body:
        source = str(text_body[0].get("content", ""))
    elif html_body:
        # Strip HTML tags and collapse whitespace for a cheap preview.
        raw = str(html_body[0].get("content", ""))
        source = _HTML_TAG_RE.sub(" ", raw)
    if not source:
        return ""
    flat = _PREVIEW_WS_RE.sub(" ", source).strip()
    return flat[:_PREVIEW_MAX_LEN]


def _build_body_values(
    text_body: list[EmailBodyPart],
    html_body: list[EmailBodyPart],
    encoding_problems: dict[str, bool],
) -> dict[str, dict[str, Any]]:
    """Build the JMAP ``bodyValues`` map: ``{partId: EmailBodyValue}``.

    Per RFC 8621 §4.1.4, ``bodyValues`` is a map for ``text/*`` parts
    only — non-text parts (inline images, audio, video) that ride in
    ``textBody`` / ``htmlBody`` for rendering purposes are excluded.

    Per §4.1.4 ``EmailBodyValue.value``: line endings are normalised
    to ``\\n`` (CR / CRLF / LF all collapse to LF).

    ``isTruncated`` is always ``false`` — we never truncate.
    ``isEncodingProblem`` flips when the part's charset was missing or
    unknown and the body decoder fell back to utf-8/replace; the
    producer records that signal in ``encoding_problems`` keyed by
    ``partId``.
    """
    values: dict[str, dict[str, Any]] = {}
    for part in (*text_body, *html_body):
        part_id = part.get("partId")
        if not part_id:
            continue
        # Spec restricts the map to ``text/*`` parts. An inline image
        # in textBody has type=image/png and its ``content`` is base64
        # — emitting it as a string value here would be a category
        # error for clients reading ``bodyValues``.
        if not (part.get("type") or "").lower().startswith("text/"):
            continue
        raw = str(part.get("content", ""))
        # Per spec §4.1.4: "CRLF, LF, and CR should be normalized to LF."
        normalised = raw.replace("\r\n", "\n").replace("\r", "\n")
        values.setdefault(
            part_id,
            {
                "value": normalised,
                "isEncodingProblem": encoding_problems.get(part_id, False),
                "isTruncated": False,
            },
        )
    return values


def _strip_body_part_content(
    body_parts: list[EmailBodyPart],
) -> list[EmailBodyPart]:
    """Remove ``content`` from each body part — used when ``body_values``
    is enabled so the content lives in the ``bodyValues`` map only."""
    return [
        cast(EmailBodyPart, {k: v for k, v in p.items() if k != "content"})
        for p in body_parts
    ]


def parse_email(
    raw_email_bytes: bytes,
    *,
    extensions: bool = False,
    body_values: bool = True,
    body_structure: bool = False,
    preview: bool = True,
    limits: ParseLimits = DEFAULT_PARSE_LIMITS,
) -> JmapEmail | None:
    """Parse raw RFC 5322 bytes into a JMAP Email object (RFC 8621 §4).

    Output is a strict-JMAP-conformant dict by default. Server-set
    metadata properties (``id``, ``blobId``, ``threadId``, ``mailboxIds``,
    ``keywords``, ``size``, ``receivedAt``) are intentionally absent —
    they belong to the message store.

    The defaults follow RFC 8621 §4.2 ``defaultProperties`` for
    ``Email/get``: ``preview`` and ``bodyValues`` are emitted by
    default so a plain ``parse_email(raw)`` produces a
    spec-default-conformant Email object. Set them to ``False`` when
    parse-time cost matters on a hot path that doesn't need them.

    Parameters
    ----------
    raw_email_bytes : bytes
        The raw RFC 5322 message.
    extensions : bool, default False
        Emit the ``_ext`` sub-dict carrying project extensions outside
        the RFC 8621 wire shape: ``ext["defects"]`` (stdlib MIME
        defect class names) and ``ext["resent"]`` (Resent-* typed
        projection, present only when the wire actually carries any
        Resent-* header). Set ``True`` to read them.
    body_values : bool, default True
        Emit a top-level ``bodyValues`` map keyed by ``partId`` per
        RFC 8621 §4.1.5. Body parts then carry only metadata; the
        ``content`` field is removed from text/html parts (attachments
        keep their ``content: bytes`` extension regardless). Set to
        ``False`` to skip the ``bodyValues`` map and inline ``content``
        directly on every part — slightly cheaper for one-shot reads.
    body_structure : bool, default False
        Emit a top-level ``bodyStructure`` field — the recursive tree
        of every part. Adds the MIME-tree walk overhead.
    preview : bool, default True
        Compute ``preview``: a single-line ≤ 256-char plain-text excerpt.
        Set to ``False`` when you don't need it; the cost is one HTML
        strip + a unicode-space normalise per message.
    limits : ParseLimits, default :data:`DEFAULT_PARSE_LIMITS`
        Per-call resource caps (MIME nesting depth, total part count,
        per-header byte cap). See :class:`ParseLimits` for the
        attribute table. Pass a custom instance to widen / tighten the
        defaults independently of any other parse call in the process.

    Returns
    -------
    dict or None
        A JMAP Email object on success (see module docstring for the
        property table). ``None`` when the input cannot be parsed at
        all (empty bytes, wrong type, stdlib producing no
        :class:`email.message.Message`, an unhandled exception during
        the walk). Every failure is logged at WARNING level.

        Recoverable damage (a salvageable malformed header, an unknown
        encoding, etc.) does **not** trigger ``None`` — it surfaces in
        ``result["_ext"]["defects"]`` so callers can flag the message
        while still using its parsed fields.
    """
    return _parse_email(
        raw_email_bytes,
        extensions=extensions,
        body_values=body_values,
        body_structure=body_structure,
        preview=preview,
        limits=limits,
    )


def _parse_email(
    raw_email_bytes: bytes,
    *,
    extensions: bool,
    body_values: bool,
    body_structure: bool,
    preview: bool,
    limits: ParseLimits,
) -> JmapEmail | None:
    """Implementation of ``parse_email``. Kept separate so the public
    function carries a clean docstring and signature while this carries
    the per-header view construction.

    Three header views are computed in lockstep during the parse walk:
    - ``decoded_by_name`` — ``dict[str, list[str]]`` keyed by lowercase
      header name. Every occurrence of every header in document order;
      scalar JMAP fields read ``[0]`` (first wins on duplication,
      matching stdlib ``email.message.Message[name]``).
    - ``wire_headers`` — every header in document order as
      ``(wire_case_name, decoded_value, raw_value)``; surfaced as
      ``parsed["headers"]`` (a JMAP ``EmailHeader[]``).
    - ``headers_blocks`` — ``list[dict[str, list[str]]]``. Each block
      ends with a ``Received`` header; everything above (earlier) it
      is in the same trust scope. Values inside a block are *always*
      ``list[str]`` so trusted-relays filters can index uniformly.

    Args:
        raw_email_bytes: Raw email data as bytes.

    Returns:
        Dict of parsed fields, or ``None`` on fundamental parse failure
        (logged at WARNING level). Never raises.
    """
    if not raw_email_bytes or not isinstance(raw_email_bytes, bytes):
        logger.warning(
            "parse_email: empty or non-bytes input (type=%s); returning None",
            type(raw_email_bytes).__name__,
        )
        return None

    try:
        # Stdlib parser under compat32. ``message_from_bytes`` never
        # raises on malformed input under this policy — recoverable
        # damage is recorded in ``message.defects`` and we walk the
        # structure best-effort.
        message = email.message_from_bytes(raw_email_bytes, policy=_PARSE_POLICY)

        if message is None:
            logger.warning(
                "parse_email: stdlib produced no Message (input length %d); returning None",
                len(raw_email_bytes),
            )
            return None

        # Extract all headers, normalizing keys to lowercase. We use
        # ``raw_items()`` rather than ``items()`` to get the *raw*
        # string the parser stored (under compat32, BytesFeedParser
        # escapes non-ASCII bytes via ``surrogateescape``). The public
        # items() view wraps each value in a ``Header`` object whose
        # str() collapses lone surrogates to U+FFFD, irreversibly
        # destroying non-ASCII content (e.g. UTF-8 X-Gmail-Labels).
        # See ``_repair_surrogate_escaped`` for the recovery path.
        # Lowercase-keyed map of every occurrence of every header, in
        # document order. Scalar JMAP fields take ``[0]`` (first wins,
        # matching stdlib ``email.message.Message[name]`` semantics).
        decoded_by_name: dict[str, list[str]] = defaultdict(list)
        # First-occurrence raw value per header name (surrogate-repaired
        # but NOT RFC 2047 decoded). Used by ``parse_address`` /
        # ``parse_addresses`` to defend against the PortSwigger
        # "Splitting the Email Atom" smuggling technique: pre-decoding
        # the whole header would let encoded-words inside the addr-spec
        # surface ``@``/``<``/``,`` as structural splitters.
        raw_addr_headers: dict[str, str] = {}
        # Document-order header source-of-truth: each tuple is
        # ``(wire_case_name, decoded_value, raw_value)``. ``decoded_value``
        # has RFC 2047 + surrogate repair applied; ``raw_value`` is the
        # surrogate-repaired but otherwise-untouched header text.
        wire_headers: list[tuple[str, str, str]] = []
        # Lowercase-key view in document order, used to build
        # ``headers_blocks`` (Received-bounded trust scopes).
        lower_headers: list[tuple[str, str]] = []

        for k, v in message.raw_items():
            raw_value = _repair_surrogate_escaped(str(v))
            # Defensive byte cap on individual header values. Matches
            # Postfix's ``header_size_limit``. Above this size the
            # quadratic-time hot spots in ``_header_value_parser``
            # (gh-136063: ``get_phrase`` / ``_parseparam`` / etc.)
            # start to hurt — truncating early keeps wall-clock
            # bounded on adversarial input.
            if len(raw_value) > limits.max_header_value_bytes:
                logger.warning(
                    "Header %s value exceeds %d bytes; truncating",
                    k,
                    limits.max_header_value_bytes,
                )
                raw_value = raw_value[: limits.max_header_value_bytes]
            # NUL bytes in headers would either reach a downstream text
            # store (PostgreSQL rejects \x00 in TEXT) or smuggle past a
            # naive C-string parser. Strip before any decode + before
            # the value lands in wire_headers.
            raw_value = _strip_nul_bytes(raw_value)
            decoded_value = decode_rfc2047_header(raw_value)
            key_lower = k.lower()
            wire_headers.append((k, decoded_value, raw_value))
            lower_headers.append((key_lower, decoded_value))

            decoded_by_name[key_lower].append(decoded_value)
            raw_addr_headers.setdefault(key_lower, raw_value)

        def _first_value(name: str) -> str:
            """Return the first decoded occurrence of ``name`` or ``""``.

            Centralizes the ``email.message.Message[name]`` semantic
            (first wins on duplication) for the scalar JMAP fields.
            """
            occurrences = decoded_by_name.get(name)
            return occurrences[0] if occurrences else ""

        # ─── Per-property JMAP shape construction ───
        # Subject: NFC-normalized text; ``None`` when header absent
        # (preserves spec null-vs-empty distinction).
        jmap_subject = _jmap_subject(
            decoded_by_name["subject"][0] if "subject" in decoded_by_name else None
        )

        # Address fields. Use the raw (surrogate-repaired but NOT RFC
        # 2047-decoded) header values to defend against PortSwigger
        # "Splitting the Email Atom" smuggling.
        def _addrs(name: str) -> list[EmailAddress] | None:
            """Header-present → list (possibly empty after validation);
            header-absent → None. Spec mandates the null-vs-empty
            distinction (RFC 8621 §4.1.2.2)."""
            if name not in decoded_by_name:
                return None
            return _jmap_addresses(parse_addresses(raw_addr_headers.get(name, "")))

        jmap_from = _addrs("from")
        jmap_sender = _addrs("sender")
        jmap_reply_to = _addrs("reply-to")
        jmap_to = _addrs("to")
        jmap_cc = _addrs("cc")
        jmap_bcc = _addrs("bcc")

        # MessageIds (Message-ID, In-Reply-To, References) — String[]
        # with CFWS + <> stripped. ``None`` when absent.
        jmap_message_id = _jmap_message_ids(_first_value("message-id"))
        jmap_in_reply_to = _jmap_message_ids(_first_value("in-reply-to"))
        jmap_references = _jmap_message_ids(_first_value("references"))

        # sentAt: ISO-8601 with offset. ``None`` when ``Date:`` is
        # absent — we do NOT synthesize ``now()`` (anti-conformant).
        jmap_sent_at = _jmap_iso_date(parse_date(_first_value("date")))

        # Defects collected from the stdlib parse + our recursive walks.
        defects: list[str] = []
        try:
            for part in message.walk():
                for defect in getattr(part, "defects", ()) or ():
                    defects.append(type(defect).__name__)
        except RecursionError:
            defects.append("RecursionError")

        body_parts = _parse_message_content(message, limits=limits, defects=defects)
        text_body: list[EmailBodyPart] = body_parts["textBody"]
        html_body: list[EmailBodyPart] = body_parts["htmlBody"]
        attachments: list[EmailBodyPart] = body_parts["attachments"]
        encoding_problems: dict[str, bool] = body_parts.get("encoding_problems") or {}
        has_attachment = _compute_has_attachment(attachments)

        # ─── Assemble the JMAP Email object ───
        result: dict[str, Any] = {
            "subject": jmap_subject,
            "from": jmap_from,
            "sender": jmap_sender,
            "replyTo": jmap_reply_to,
            "to": jmap_to,
            "cc": jmap_cc,
            "bcc": jmap_bcc,
            "messageId": jmap_message_id,
            "inReplyTo": jmap_in_reply_to,
            "references": jmap_references,
            "sentAt": jmap_sent_at,
            "headers": _jmap_headers(wire_headers),
        }

        result["textBody"] = text_body
        result["htmlBody"] = html_body
        result["attachments"] = attachments
        result["hasAttachment"] = has_attachment

        # ─── Body content projections ───
        if preview:
            result["preview"] = _compute_preview(text_body, html_body)
        if body_values:
            result["bodyValues"] = _build_body_values(
                text_body, html_body, encoding_problems
            )
            result["textBody"] = _strip_body_part_content(text_body)
            result["htmlBody"] = _strip_body_part_content(html_body)
        if body_structure:
            result["bodyStructure"] = _build_body_structure(message, limits)

        # ─── Extensions (project-specific) ───
        if extensions:
            ext: dict[str, Any] = {"defects": defects}
            # Resent-* typed projection. RFC 8621 §4.1.3 names only the
            # 11 base convenience properties; Resent-* is a §4.1.2
            # typed-projection idiom we pre-compute and surface here so
            # forwarded / resent mail handling doesn't have to walk
            # ``parsed["headers"]``. Only emit the sub-dict if any
            # Resent-* header is actually present on the wire.
            resent: dict[str, Any] = {
                "from": _addrs("resent-from"),
                "sender": _addrs("resent-sender"),
                "replyTo": _addrs("resent-reply-to"),
                "to": _addrs("resent-to"),
                "cc": _addrs("resent-cc"),
                "bcc": _addrs("resent-bcc"),
                "messageId": _jmap_message_ids(_first_value("resent-message-id")),
                "date": _jmap_iso_date(parse_date(_first_value("resent-date"))),
            }
            if any(v is not None for v in resent.values()):
                ext["resent"] = resent
            result["_ext"] = ext

        return cast(JmapEmail, result)

    except Exception as e:
        logger.exception("parse_email: unexpected error (%s); returning None", e)
        return None
