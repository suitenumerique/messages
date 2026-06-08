"""Null-safe accessors over the JMAP RFC 8621 Email object shape.

The wire shape uses lists everywhere (``from: list[EmailAddress]``,
``messageId: list[str]``, ``headers: list[EmailHeader]``, …). These
helpers wrap the first-element / case-insensitive lookup patterns so
consumers don't repeat ``parsed.get("from") or []`` + index + ``.get``
chains. Every helper returns a sensible default on absence; none of
them ever raises.

These helpers complement :func:`jmap_email.parse_email` and live in the
same package so that one ``pip install jmap-email`` ships everything
needed to *read* a parsed Email object — the wire-format pair stays
strict-JMAP, and the accessors stay null-safe.
"""

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "first_address",
    "first_address_email",
    "first_address_name",
    "first_msgid",
    "msgid_chain",
    "now_sent_at",
    "sent_at_to_datetime",
    "find_header",
    "find_headers",
    "has_header",
    "body_part_text",
    "body_text_joined",
]


def now_sent_at() -> str:
    """Return the current UTC time formatted as a ``sentAt`` ISO-8601 string.

    Sugar over ``datetime.now(timezone.utc).isoformat()`` for the
    common outbound pattern::

        compose_email({..., "sentAt": now_sent_at(), ...})

    The composer is strict on ``sentAt`` (RFC 5322 §3.6.1) and refuses
    to silently fabricate a timestamp; this helper makes the explicit
    "I want now" path a one-liner.
    """
    return datetime.now(timezone.utc).isoformat()


def first_address(addrs: Any) -> dict[str, Any] | None:
    """Return the first entry of a JMAP ``EmailAddress[]`` or ``None``.

    An entry without an ``email`` is treated as missing.
    """
    if not addrs:
        return None
    for entry in addrs:
        if isinstance(entry, dict) and entry.get("email"):
            return entry
    return None


def first_address_email(addrs: Any) -> str:
    """Return the ``email`` of the first ``EmailAddress`` or ``""``."""
    entry = first_address(addrs)
    return (entry.get("email") if entry else "") or ""


def first_address_name(addrs: Any) -> str:
    """Return the ``name`` of the first ``EmailAddress`` or ``""``."""
    entry = first_address(addrs)
    return (entry.get("name") if entry else "") or ""


def first_msgid(ids: Any) -> str:
    """Return the first non-empty entry of a JMAP ``String[]`` of
    msg-ids, or ``""``. Entries are returned without surrounding angle
    brackets (the JMAP wire shape strips them).

    Strict-typed: a scalar string is rejected even though Python would
    iterate it character by character; only a ``list`` of strings is
    accepted.
    """
    if not isinstance(ids, list) or not ids:
        return ""
    for v in ids:
        if isinstance(v, str) and v:
            return v
    return ""


def msgid_chain(ids: Any) -> str:
    """Reassemble a JMAP ``String[]`` of msg-ids into the angle-bracketed
    space-separated wire form (e.g. ``"<a@x> <b@x>"``). Strict-typed:
    see :func:`first_msgid` — only a list of strings is accepted."""
    if not isinstance(ids, list) or not ids:
        return ""
    out: list[str] = []
    for v in ids:
        if not isinstance(v, str) or not v:
            continue
        sanitized = v.strip()
        if not (sanitized.startswith("<") and sanitized.endswith(">")):
            sanitized = f"<{sanitized}>"
        out.append(sanitized)
    return " ".join(out)


def sent_at_to_datetime(sent_at: Any) -> datetime | None:
    """Parse a JMAP ``sentAt`` ISO-8601 string into a tz-aware
    :class:`datetime`. Returns ``None`` on absence or parse failure.
    A ``datetime`` instance is returned as-is so callers can pass
    either shape through unchanged."""
    if not sent_at:
        return None
    if isinstance(sent_at, datetime):
        return sent_at
    if isinstance(sent_at, str):
        try:
            return datetime.fromisoformat(sent_at)
        except (TypeError, ValueError):
            return None
    return None


def find_header(parsed_email: dict[str, Any], name: str) -> str:
    """Return the value of the first header whose name matches ``name``
    case-insensitively, or ``""`` when absent."""
    target = name.lower()
    for entry in parsed_email.get("headers") or []:
        if isinstance(entry, dict) and (entry.get("name") or "").lower() == target:
            return entry.get("value") or ""
    return ""


def find_headers(parsed_email: dict[str, Any], name: str) -> list[str]:
    """Return every value whose header name matches ``name``
    (case-insensitive), in document order. Empty list when absent."""
    target = name.lower()
    return [
        entry.get("value") or ""
        for entry in parsed_email.get("headers") or []
        if isinstance(entry, dict) and (entry.get("name") or "").lower() == target
    ]


def has_header(parsed_email: dict[str, Any], name: str) -> bool:
    """Return ``True`` when at least one header matches ``name``
    case-insensitively."""
    target = name.lower()
    return any(
        isinstance(entry, dict) and (entry.get("name") or "").lower() == target
        for entry in parsed_email.get("headers") or []
    )


def body_part_text(parsed_email: dict[str, Any], part: dict[str, Any]) -> str:
    """Return the decoded text of a JMAP ``EmailBodyPart``.

    Transparent across both parser output shapes:

    - When :func:`parse_email` was called with the spec-default
      ``body_values=True``, the part's ``content`` field is stripped
      and the text lives in ``parsed["bodyValues"][partId]["value"]``.
    - When the caller opted into ``body_values=False`` for cheaper
      parses, the part carries its ``content`` inline.

    Use this helper at every site that reads body text so a future
    flip of the parser default doesn't break the consumer. Returns
    ``""`` when the lookup fails (e.g. truncated walk, malformed
    input).
    """
    if not isinstance(part, dict):
        return ""
    inline = part.get("content")
    if inline is not None:
        # The inline shape: text parts carry ``str``; attachments carry
        # ``bytes`` but consumers shouldn't be calling this helper on an
        # attachment anyway.
        return inline if isinstance(inline, str) else ""
    part_id = part.get("partId")
    if not part_id:
        return ""
    bv = (parsed_email.get("bodyValues") or {}).get(part_id) or {}
    return bv.get("value") or ""


def body_text_joined(parsed_email: dict[str, Any], key: str = "textBody") -> str:
    """Concatenate every body part under ``parsed_email[key]`` (typically
    ``textBody`` or ``htmlBody``) into a single string, transparent to
    the ``body_values`` projection.

    A convenience wrapper around :func:`body_part_text` for the common
    "all body text as one string" pattern (snippet extraction, search
    indexing, audit logging).
    """
    parts = parsed_email.get(key) or []
    return "".join(body_part_text(parsed_email, p) for p in parts)
