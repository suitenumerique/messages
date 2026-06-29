"""Messages-side helpers built on top of :mod:`jmap_email`.

Three groups of helpers live here:

- :func:`gmail_labels` and :func:`headers_blocks` — Messages-specific
  computations against the JMAP ``headers`` list. Neither belongs in
  the library: the headers they recognise are project conventions
  (Google Takeout / Dovecot label headers; Received-bounded trust
  scopes used by the inbound auth path).
- :func:`thread_snippet` — the thread-listing snippet derived from
  ``parse_email``'s ``preview`` field, truncated to
  :data:`SNIPPET_MAX_LENGTH`.
- :func:`current_sent_at` — single source of truth for the
  ``sentAt`` ISO-8601 string outbound paths stamp on the JMAP dict
  they hand to :func:`jmap_email.compose_email`.
"""

import re
import shlex
from collections import defaultdict

from django.utils import timezone

from jmap_email import JmapEmail, body_part_text, decode_rfc2047_header

__all__ = [
    "SNIPPET_MAX_LENGTH",
    "current_sent_at",
    "gmail_labels",
    "headers_blocks",
    "thread_snippet",
]


SNIPPET_MAX_LENGTH = 140


# ────────────────────────────────────────────────────────────────────
# Date stamping for outbound composition
# ────────────────────────────────────────────────────────────────────


def current_sent_at() -> str:
    """Return the ISO-8601 ``sentAt`` value outbound composition stamps.

    :func:`jmap_email.compose_email` is strict-by-design and rejects a
    missing or unparseable ``sentAt``. Every backend code path that
    composes "now" routes through this helper so the timestamp shape
    is uniform — currently ``timezone.now().isoformat()``.
    """
    return timezone.now().isoformat()


# ────────────────────────────────────────────────────────────────────
# Thread-listing snippet
# ────────────────────────────────────────────────────────────────────


def thread_snippet(parsed_email: JmapEmail, fallback: str = "") -> str:
    """Return the thread-listing snippet for a parsed JMAP Email.

    Resolution order:

    1. ``parsed["preview"]`` — the library's spec-default ≤256-char
       plain-text excerpt, already HTML-stripped and whitespace-
       normalised.
    2. The first ``textBody`` part — used when ``parse_email`` was
       called with ``preview=False`` or when the caller hand-built the
       JMAP dict (importers, autoreply, MTA-in test fixtures).
    3. ``fallback`` — when neither preview nor a text body exists.

    Output is always truncated to :data:`SNIPPET_MAX_LENGTH`.
    """
    parsed = parsed_email or {}
    candidate = parsed.get("preview") or ""
    if not candidate:
        text_body = parsed.get("textBody") or []
        if text_body:
            candidate = body_part_text(parsed, text_body[0])
    if not candidate:
        candidate = fallback or ""
    return candidate[:SNIPPET_MAX_LENGTH]


# ────────────────────────────────────────────────────────────────────
# Gmail / Dovecot label headers
# ────────────────────────────────────────────────────────────────────

# Comma-separated form with optional quoted strings — the OfflineIMAP /
# Google Takeout convention. Falls back to space-separated (Dovecot) when
# no comma is present.
_COMMA_LABEL_RE = re.compile(r'\s*"([^"]*)"\s*|\s*([^,]+)')


def _parse_labels_header(labels_str: str) -> list[str]:
    """Parse a labels header value, handling quoted strings.

    Supports two formats:

    - Comma-separated (OfflineIMAP / Google Takeout):
      ``label1, label2, "label three"``
    - Space-separated (Dovecot): ``label1 label2 "label three"``
    """
    result: list[str] = []
    if "," in labels_str:
        for quoted, plain in _COMMA_LABEL_RE.findall(labels_str):
            label = (quoted if quoted else plain).strip()
            if label:
                result.append(label)
    else:
        try:
            result = [
                token.strip() for token in shlex.split(labels_str) if token.strip()
            ]
        except ValueError:
            # Unmatched quotes — fall back to a simple split rather than
            # losing the label list entirely.
            result = [token.strip() for token in labels_str.split() if token.strip()]
    return result


def gmail_labels(parsed_email: JmapEmail) -> list[str]:
    """Return labels harvested from ``X-Gmail-Labels`` / ``X-Keywords``.

    Deduped in first-seen order. Empty list when neither header is
    present. Reads the raw header list directly so the library does
    not need to bake the Google / Dovecot label idiom into its
    strict-JMAP wire shape.
    """
    seen: set[str] = set()
    labels: list[str] = []
    for header in parsed_email.get("headers") or []:
        if not isinstance(header, dict):
            continue
        name = (header.get("name") or "").lower()
        if name not in ("x-gmail-labels", "x-keywords"):
            continue
        raw_value = header.get("value") or ""
        if not raw_value:
            continue
        # ``parsed["headers"][*]["value"]`` is the RFC 8621 Raw form
        # (byte-faithful, no encoded-word decode). Labels routinely
        # ship as RFC 2047 ``=?UTF-8?Q?…?=`` words (Google Takeout uses
        # Q-encoding for non-ASCII label text) so decode before
        # splitting.
        value = decode_rfc2047_header(raw_value)
        for label in _parse_labels_header(value):
            if label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


# ────────────────────────────────────────────────────────────────────
# Received-bounded header trust scopes
# ────────────────────────────────────────────────────────────────────


def headers_blocks(
    parsed_email: JmapEmail,
) -> list[dict[str, list[str]]]:
    """Return every header grouped into Received-bounded trust scopes.

    Each ``Received`` header marks the END of its block; everything
    above (earlier) it is in the same trust scope. The trailing
    Received-less block holds our own MTA prepend. Values inside a
    block are always lists for uniform downstream indexing.

    Useful for inbound auth (trusted-relay cuts) and spam classifiers
    that want to discriminate per-hop. Computed on demand so the
    library's ``ext`` namespace stays free of Messages-specific
    pre-computation.
    """
    blocks: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = defaultdict(list)
    for header in parsed_email.get("headers") or []:
        if not isinstance(header, dict):
            continue
        name = (header.get("name") or "").lower()
        value = header.get("value") or ""
        if name == "received":
            current["received"].append(value)
            blocks.append(dict(current))
            current = defaultdict(list)
        else:
            current[name].append(value)
    if current:
        blocks.append(dict(current))
    return blocks
