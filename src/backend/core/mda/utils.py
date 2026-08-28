"""Messages-side helpers built on top of :mod:`jmap_email`.

Three groups of helpers live here:

- :func:`gmail_labels` and :func:`headers_blocks` — Messages-specific
  computations against the JMAP ``headers`` list. Neither belongs in
  the library: the headers they recognise are project conventions
  (Google Takeout / Dovecot label headers; Received-bounded trust
  scopes used by the inbound auth path).
- :func:`message_snippet` — the plain-text message preview
  ("snippet") derived from a parsed JMAP Email. The cleaning itself
  (HTML + markdown strip, truncate to SNIPPET_MAX_CHARS after cleaning)
  lives in :func:`jmap_email.preview_text`; this wrapper only resolves
  which body source to feed it.
- :func:`current_sent_at` — single source of truth for the
  ``sentAt`` ISO-8601 string outbound paths stamp on the JMAP dict
  they hand to :func:`jmap_email.compose_email`.
- :data:`COMPOSE_OPTIONS` — the compose policy every path that
  produces MIME for us shares.
"""

import re
import shlex
from collections import defaultdict
from collections.abc import Iterable
from email.utils import make_msgid

from django.utils import timezone

from jmap_email import (
    ComposeOptions,
    body_part_text,
    decode_rfc2047_header,
    preview_text,
)
from jmap_email.types import JmapEmail

from core.mda.addresses import needs_smtputf8

# Compose policy for a message whose header addresses are all ASCII, which
# is the overwhelming majority.
#
# ``idna_encode_domains``: an A-label is the only form a non-ASCII domain can
# take on a 7-bit session, and it is the form DNS wants anyway.
COMPOSE_OPTIONS = ComposeOptions(idna_encode_domains=True)


def compose_options_for(header_addresses: Iterable[str | None]) -> ComposeOptions:
    """Return the compose policy for a message with these header addresses.

    ``allow_smtputf8`` only when an address bound for the *header block* has
    a non-ASCII local part: that forces RFC 6532 headers, which every hop of
    the message must then support.

    Bcc is not part of the input — ``emit_bcc`` is off, so it never reaches
    the headers and the message stays ASCII for hops carrying no EAI
    recipient. ``send_smtp_mail`` applies the per-transaction requirement.
    """
    return ComposeOptions(
        idna_encode_domains=True,
        allow_smtputf8=any(
            needs_smtputf8(address) for address in header_addresses if address
        ),
    )


# Archive reconstruction (PST import) additionally keeps Bcc: the list was
# already in the source file, so dropping it would lose data the user owns.
# ``allow_smtputf8`` for the same reason — an EAI address (non-ASCII local
# part, RFC 6530) is legal in an Exchange archive, and the reconstructed
# .eml is stored in the user's own mailbox, never retransmitted over SMTP;
# refusing it would exclude the message from the import.
ARCHIVE_COMPOSE_OPTIONS = ComposeOptions(
    idna_encode_domains=True, emit_bcc=True, allow_smtputf8=True
)

__all__ = [
    "ARCHIVE_COMPOSE_OPTIONS",
    "COMPOSE_OPTIONS",
    "SNIPPET_MAX_CHARS",
    "compose_options_for",
    "current_sent_at",
    "generate_mime_id",
    "gmail_labels",
    "headers_blocks",
    "message_snippet",
]


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


def generate_mime_id(domain: str, namespace: str = "lstmsgs") -> str:
    """Return a fresh Message-ID in bare JMAP form (no angle brackets).

    `make_msgid` yields the wire form `<id@domain>`; we strip the
    brackets so the value matches the JMAP convention used everywhere
    else — inbound parsing strips them (see `jmap_email.first_msgid`)
    and :func:`jmap_email.compose_email` re-adds them on the wire. Every
    backend path that mints a Message-ID routes through this helper so
    the stored shape stays uniform.
    """
    return make_msgid(idstring=namespace, domain=domain).strip("<>")


# ────────────────────────────────────────────────────────────────────
# Message snippet (plain-text preview)
# ────────────────────────────────────────────────────────────────────

SNIPPET_MAX_CHARS = 140


def message_snippet(parsed_email: JmapEmail) -> str:
    """Return the snippet for a parsed JMAP Email.

    Resolution order:

    1. ``parsed["preview"]`` — computed (and already cleaned) by the
       library at parse time.
    2. The first ``textBody`` part — for hand-built JMAP dicts
       (importers, autoreply, MTA-in fixtures) that carry no preview.
    3. The first ``htmlBody`` part, for HTML-only hand-built dicts.

    Every source goes through :func:`jmap_email.preview_text` (HTML +
    markdown strip), truncate to SNIPPET_MAX_CHARS after stripping.
    Returns ``""`` when the message has no body content — the subject
    fallback is deliberately an app/display concern, not baked into
    the stored snippet.
    """
    parsed = parsed_email or {}
    candidate = parsed.get("preview") or ""
    if not candidate:
        text_body = parsed.get("textBody") or []
        if text_body:
            candidate = body_part_text(parsed, text_body[0])
    if not candidate:
        html_body = parsed.get("htmlBody") or []
        if html_body:
            candidate = body_part_text(parsed, html_body[0])
    return preview_text(candidate, max_chars=SNIPPET_MAX_CHARS)


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
