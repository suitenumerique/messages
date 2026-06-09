"""Reply / forward template builders for outbound composition.

These were originally shipped by ``jmap-email`` but moved here because
they bake in Messages-specific UI choices that don't belong in a
strict-JMAP library:

- English-only header strings (``On {date}, {sender} wrote:``,
  ``---------- Forwarded message ----------``) — the frontend has
  translations for these in ``src/frontend/public/locales/common/``
  but the library has no access to a locale.
- HTML markup choices (``<blockquote data-type="quote-separator">``)
  picked to align with the frontend's blocknote rendering.
- An incomplete output dict (no ``from``, no ``sentAt``) that the
  Messages outbound flow finishes — the library can't finish it
  because it has no live mailbox / user context.

The library still owns the wire-format primitives we depend on:
:func:`jmap_email.compose_email`, :func:`jmap_email.parse_email`,
:func:`jmap_email.format_address`, :func:`jmap_email.format_address_list`,
:func:`jmap_email.is_valid_msg_id`. This module is a thin layer on top.
"""

import html
import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any

from jmap_email import (
    JmapEmail,
    first_msgid,
    format_address,
    format_address_list,
    is_valid_msg_id,
    msgid_chain,
)

# Bracket-aware tokeniser for a wire-form ``References`` chain. Splitting
# on whitespace would slice ``<bad id@x>`` into ``<bad`` + ``id@x>`` and
# the right half would survive shape validation — silent half-id
# salvaging. Walking by ``<…>`` pairs keeps each bracketed token intact.
_MSGID_TOKEN_RE = re.compile(r"<[^<>]*>")

__all__ = [
    "compute_reply_threading",
    "forward_subject",
    "make_forward",
    "make_reply",
    "reply_subject",
]


# ────────────────────────────────────────────────────────────────────
# Subject prefixing
# ────────────────────────────────────────────────────────────────────


def reply_subject(subject: str) -> str:
    """Add ``Re: `` prefix to a subject, avoiding duplication."""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def forward_subject(subject: str) -> str:
    """Add ``Fwd: `` prefix to a subject, avoiding duplication."""
    if subject.lower().startswith("fwd:"):
        return subject
    return f"Fwd: {subject}"


# ────────────────────────────────────────────────────────────────────
# RFC 5322 §3.6.4 threading projection
# ────────────────────────────────────────────────────────────────────


def compute_reply_threading(
    original_message: JmapEmail,
) -> tuple[list[str] | None, list[str] | None]:
    """Project (``inReplyTo``, ``references``) for a reply to ``original_message``.

    Returns a pair of JMAP ``String[] | None`` values ready to splice
    into the outbound dict::

        in_reply_to, references = compute_reply_threading(parent)
        if in_reply_to:
            reply["inReplyTo"] = in_reply_to
        if references:
            reply["references"] = references

    Both are ``None`` when the parent's Message-ID is missing or
    malformed — better to lose threading than corrupt the chain on the
    receiver side.
    """
    parent_id = first_msgid(original_message.get("messageId"))
    if not parent_id or not is_valid_msg_id(parent_id):
        return None, None

    # The stored shape strips angle brackets; wrap for the wire chain
    # then strip again for the per-id list.
    wrapped = f"<{parent_id}>"
    orig_refs = msgid_chain(original_message.get("references"))
    chain_tokens = [
        tok for tok in _MSGID_TOKEN_RE.findall(orig_refs) if is_valid_msg_id(tok)
    ]
    if wrapped not in chain_tokens:
        chain_tokens.append(wrapped)

    in_reply_to = [parent_id]
    references = [tok.strip("<>") for tok in chain_tokens]
    return in_reply_to, references


# ────────────────────────────────────────────────────────────────────
# Quote-block embedding (text + HTML)
# ────────────────────────────────────────────────────────────────────


def _body_content(part: dict[str, Any]) -> str:
    """Read the ``content`` of a JMAP ``EmailBodyPart``."""
    return part.get("content", "") if isinstance(part, dict) else ""


def _attach_utc_if_naive(dt: datetime) -> datetime:
    """Ensure ``dt`` is timezone-aware, defaulting to UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _embed_original_message(
    original_message: JmapEmail,
    new_text: str = "",
    new_html: str | None = None,
    include_original: bool = True,
    is_forward: bool = False,
) -> tuple[str, str]:
    """Embed original message content into new text and HTML.

    Returns ``(text_body, html_body)``.
    """
    if new_text is None:
        new_text = ""

    if not include_original:
        html_body = new_html or f"<p>{html.escape(new_text)}</p>"
        if html_body:
            html_body = html_body.replace("&rsquo;", "'")
        return new_text, html_body

    # Coerce None → "" — inbound parsers may emit ``{"subject": None}``
    # when the source message has no Subject header, and downstream
    # ``str.lower()`` etc. crash on None.
    orig_subject = original_message.get("subject") or ""
    orig_from_list = original_message.get("from") or []
    orig_from = orig_from_list[0] if orig_from_list else {}
    orig_to = original_message.get("to") or []
    orig_cc = original_message.get("cc") or []
    # ``sentAt`` is the JMAP ISO-8601 string. Reply/forward callers
    # sometimes pass a tz-aware ``datetime`` here off a freshly-loaded
    # ``Message`` model where ``sent_at`` is already a ``datetime``.
    orig_date = original_message.get("sentAt") or ""

    date_str = ""
    if isinstance(orig_date, datetime):
        date_str = format_datetime(_attach_utc_if_naive(orig_date))
    elif isinstance(orig_date, str) and orig_date:
        try:
            parsed_dt = parsedate_to_datetime(orig_date)
        except (ValueError, TypeError, IndexError):
            parsed_dt = None
        if parsed_dt:
            date_str = format_datetime(parsed_dt)
        else:
            date_str = orig_date
    else:
        date_str = "an unknown date"

    header_text = ""
    if is_forward:
        from_display = format_address(
            orig_from.get("name", ""), orig_from.get("email", "")
        )
        to_display = format_address_list(orig_to)
        cc_display = format_address_list(orig_cc) if orig_cc else ""

        header_text = "\r\n\r\n---------- Forwarded message ----------\r\n"
        if from_display:
            header_text += f"From: {from_display}\r\n"
        if to_display:
            header_text += f"To: {to_display}\r\n"
        if cc_display:
            header_text += f"Cc: {cc_display}\r\n"
        header_text += f"Subject: {orig_subject}\r\n"
        header_text += f"Date: {date_str}\r\n\r\n"
    else:
        from_display = format_address(
            orig_from.get("name", ""), orig_from.get("email", "")
        )
        if from_display:
            header_text = f"\r\n\r\nOn {date_str}, {from_display} wrote:\r\n"
        else:
            header_text = f"\r\n\r\nOn {date_str}, someone wrote:\r\n"

    text_body = f"{new_text}{header_text}"

    if original_message.get("textBody"):
        text_body_list = original_message["textBody"]
        first_text = text_body_list[0] if text_body_list else None
        orig_text = _body_content(first_text) if first_text else ""
        if orig_text:
            if is_forward:
                text_body += orig_text
            else:
                quoted_text = "\r\n".join(
                    [f"> {line}" for line in orig_text.splitlines()]
                )
                text_body += quoted_text

    html_content = new_html or f"<p>{html.escape(new_text)}</p>"
    if html_content:
        html_content = html_content.replace("&rsquo;", "'")

    html_body = html_content
    if new_html or original_message.get("htmlBody"):
        from_display_html = html.escape(
            format_address(orig_from.get("name", ""), orig_from.get("email", ""))
        )
        to_display_html = html.escape(format_address_list(orig_to))
        cc_display_html = html.escape(format_address_list(orig_cc)) if orig_cc else ""

        if is_forward:
            header_html = "<p>---------- Forwarded message ----------<br/>"
        else:
            header_html = "<p>---------- In reply to ----------<br/>"

        if from_display_html:
            header_html += f"<strong>From:</strong> {from_display_html}<br/>"
        if to_display_html:
            header_html += f"<strong>To:</strong> {to_display_html}<br/>"
        if cc_display_html:
            header_html += f"<strong>Cc:</strong> {cc_display_html}<br/>"
        header_html += f"<strong>Subject:</strong> {html.escape(orig_subject)}<br/>"
        header_html += f"<strong>Date:</strong> {html.escape(date_str)}<br/>"
        header_html += "</p>"

        orig_html = ""
        if original_message.get("htmlBody"):
            html_body_list = original_message["htmlBody"]
            first_html = html_body_list[0] if html_body_list else None
            orig_html = _body_content(first_html) if first_html else ""

        nested_html = f"""
        <blockquote data-type="quote-separator">
            {header_html}
            {orig_html}
        </blockquote>
        """
        html_body = f"{html_content}{nested_html}"

    return text_body, html_body


# ────────────────────────────────────────────────────────────────────
# Reply / forward template builders
# ────────────────────────────────────────────────────────────────────


def make_reply(
    original_message: JmapEmail,
    body_text: str = "",
    body_html: str | None = None,
    include_original: bool = True,
) -> dict[str, Any]:
    """Create a JMAP Email object pre-filled as a reply to ``original_message``.

    Returns a new Email dict (not bytes). The caller is expected to set
    ``from`` and ``sentAt`` before passing the result to
    :func:`jmap_email.compose_email` — the composer is strict-by-design
    and will reject a dict missing either one.

    Threading contract: emits ``inReplyTo`` and a per-id-validated
    ``references`` chain when the parent Message-ID parses cleanly;
    emits neither when the parent id is malformed (better to lose
    threading than relay corruption).
    """
    orig_subject = original_message.get("subject") or ""
    orig_from_list = original_message.get("from") or []
    orig_from = orig_from_list[0] if orig_from_list else None

    if body_text is None:
        body_text = ""

    new_subject = reply_subject(orig_subject)

    text_body, html_body = _embed_original_message(
        original_message, body_text, body_html, include_original, is_forward=False
    )

    reply_in_reply_to, reply_refs = compute_reply_threading(original_message)

    reply: dict[str, Any] = {
        "subject": new_subject,
        "textBody": [{"partId": "1", "type": "text/plain", "content": text_body}],
        "from": None,
        "to": [orig_from] if orig_from and orig_from.get("email") else None,
        "cc": original_message.get("cc"),
    }
    if reply_in_reply_to:
        reply["inReplyTo"] = reply_in_reply_to
    if reply_refs:
        reply["references"] = reply_refs
    if html_body != (body_html or f"<p>{html.escape(body_text)}</p>"):
        reply["htmlBody"] = [{"partId": "2", "type": "text/html", "content": html_body}]

    return reply


def make_forward(
    original_message: JmapEmail,
    body_text: str = "",
    body_html: str | None = None,
    include_original: bool = True,
) -> dict[str, Any]:
    """Create a JMAP Email object pre-filled as a forward of ``original_message``.

    Returns a new Email dict (not bytes). The caller is expected to set
    ``from``, ``to``, and ``sentAt`` before passing the result to
    :func:`jmap_email.compose_email` — the composer is strict-by-design
    and will reject a dict missing any of them.
    """
    orig_subject = original_message.get("subject") or ""
    new_subject = forward_subject(orig_subject)

    if body_text is None:
        body_text = ""

    text_body, html_body = _embed_original_message(
        original_message, body_text, body_html, include_original, is_forward=True
    )

    forward: dict[str, Any] = {
        "subject": new_subject,
        "textBody": [{"partId": "1", "type": "text/plain", "content": text_body}],
        "from": None,
        "to": None,
        "cc": None,
    }

    if html_body != (body_html or f"<p>{html.escape(body_text)}</p>"):
        forward["htmlBody"] = [
            {"partId": "2", "type": "text/html", "content": html_body}
        ]

    return forward
