"""Messages-specific JMAP shape computations.

The general-purpose null-safe accessors (``first_address``,
``first_msgid``, ``find_header``, ``body_part_text`` …) moved into
:mod:`jmap_email.helpers` for 0.1.0 — import them directly from the
library.

This module keeps the two computations that are Messages-specific —
not generic JMAP and not useful to other consumers of ``jmap-email``:

- :func:`gmail_labels` — labels harvested from ``X-Gmail-Labels`` /
  ``X-Keywords``. The header convention is Google Takeout / Dovecot
  / OfflineIMAP, not JMAP.
- :func:`headers_blocks` — every header grouped into Received-bounded
  trust scopes. Used by spam classifiers and inbound auth (trusted
  relays cut), not by general JMAP consumers.

Both are computed on demand from ``parsed["headers"]``, so they work
on any ``parse_email`` or ``parse_headers`` output without requiring
the library to bake them into its ``ext`` namespace.
"""

import re
import shlex
from collections import defaultdict
from typing import Any

from jmap_email import decode_rfc2047_header

__all__ = ["gmail_labels", "headers_blocks"]


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
            result = [token.strip() for token in shlex.split(labels_str) if token.strip()]
        except ValueError:
            # Unmatched quotes — fall back to a simple split rather than
            # losing the label list entirely.
            result = [token.strip() for token in labels_str.split() if token.strip()]
    return result


def gmail_labels(parsed_email: dict[str, Any]) -> list[str]:
    """Return labels harvested from ``X-Gmail-Labels`` / ``X-Keywords``.

    Deduped in first-seen order. Empty list when neither header is
    present. Works against any ``parse_email`` / ``parse_headers``
    output — reads the raw header list directly so the library does
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
        # (byte-faithful, no encoded-word decode). Labels routinely ship
        # as RFC 2047 ``=?UTF-8?Q?…?=`` words (Google Takeout uses Q-
        # encoding for non-ASCII label text) so decode before splitting.
        value = decode_rfc2047_header(raw_value)
        for label in _parse_labels_header(value):
            if label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def headers_blocks(
    parsed_email: dict[str, Any],
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
