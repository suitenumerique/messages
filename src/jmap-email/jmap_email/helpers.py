"""Null-safe accessors over the JMAP RFC 8621 Email object shape.

The wire shape uses lists everywhere (``from: list[EmailAddress]``,
``messageId: list[str]``, ``headers: list[EmailHeader]``, …). These
helpers wrap the first-element / case-insensitive lookup patterns so
consumers don't repeat ``parsed.get("from") or []`` + index + ``.get``
chains. Every helper returns a sensible default on absence; none of
them ever raises — including on ``None``, which is what
:func:`jmap_email.parse_email` hands back for input it cannot parse at
all, so ``find_header(parse_email(raw), "Subject")`` is safe to write
without an intervening ``is None`` check.

These helpers complement :func:`jmap_email.parse_email` and live in the
same package so that one ``pip install jmap-email`` ships everything
needed to *read* a parsed Email object — the wire-format pair stays
strict-JMAP, and the accessors stay null-safe.
"""

from datetime import datetime, timezone
from typing import Any

from .addresses import STRIPPED_HEADER_CHARS

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

    An entry without an ``email`` is treated as missing. Strict-typed:
    see :func:`first_msgid` — only a ``list`` is accepted, so a scalar
    is rejected rather than iterated (a bare ``str`` would otherwise be
    walked character by character, and a non-iterable would raise).
    """
    if not isinstance(addrs, list) or not addrs:
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
    see :func:`first_msgid` — only a list of strings is accepted.

    An entry that cannot be written into a header as it stands — a line
    terminator, internal whitespace, a nested angle bracket — is dropped
    rather than emitted, since the result is meant to go straight into
    ``References``/``In-Reply-To``."""
    if not isinstance(ids, list) or not ids:
        return ""
    out: list[str] = []
    for v in ids:
        if not isinstance(v, str) or not v:
            continue
        sanitized = v.strip()
        if sanitized.startswith("<") and sanitized.endswith(">"):
            sanitized = sanitized[1:-1]
        # This helper exists to be written into a header, so it is the
        # one place that must not hand back something unwritable. A line
        # terminator is header injection outright; internal whitespace
        # folds mid-id and downstream MID parsers truncate at the fold,
        # silently corrupting the thread; a nested angle bracket makes
        # the token boundaries ambiguous. Malformed entries are dropped
        # rather than emitted, matching what the composer does with a
        # bad References entry.
        if any(
            c.isspace() or c in STRIPPED_HEADER_CHARS or c in "<>" for c in sanitized
        ):
            continue
        if not sanitized:
            continue
        out.append(f"<{sanitized}>")
    return " ".join(out)


def _as_aware(value: datetime) -> datetime:
    """Treat a naive datetime as UTC.

    ``datetime.fromisoformat`` returns a *naive* object for an input
    carrying no offset (``"2026-01-01T00:00:00"``, or a bare date), and
    mixing one of those into a comparison with an aware datetime raises
    ``TypeError`` at the call site rather than here. RFC 8621 ``UTCDate``
    always carries an offset, so an input without one is already outside
    the spec; UTC is the same assumption :func:`compose_email` makes.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def sent_at_to_datetime(sent_at: Any) -> datetime | None:
    """Parse a JMAP ``sentAt`` ISO-8601 string into a tz-aware
    :class:`datetime`. Returns ``None`` on absence or parse failure.
    A ``datetime`` instance is passed through, naive ones stamped UTC
    so the return type is uniformly aware."""
    if not sent_at:
        return None
    if isinstance(sent_at, datetime):
        return _as_aware(sent_at)
    if isinstance(sent_at, str):
        try:
            return _as_aware(datetime.fromisoformat(sent_at))
        except (TypeError, ValueError):
            return None
    return None


def _header_entries(parsed_email: Any) -> list[Any]:
    """Return the ``headers`` list, or ``[]`` for anything unusable.

    ``parsed_email.get("headers") or []`` is not enough: a truthy
    non-iterable (``5``, ``True``, ``3.5``) passes the ``or`` and then
    raises ``TypeError`` on the ``for``. These accessors promise never to
    raise, and they are exactly what a caller reaches for *before*
    checking anything, so the type has to be established rather than
    assumed.
    """
    if not isinstance(parsed_email, dict):
        return []
    entries = parsed_email.get("headers")
    return entries if isinstance(entries, list) else []


def _entry_name_matches(entry: Any, target: str) -> bool:
    """True when *entry* is a header dict whose name equals *target*.

    ``entry.get("name")`` can be any JSON value, and a non-string one has
    no ``.lower()``. Compared as a string so a numeric name simply fails
    to match instead of raising.
    """
    if not isinstance(entry, dict):
        return False
    name = entry.get("name")
    return isinstance(name, str) and name.lower() == target


def find_header(parsed_email: dict[str, Any], name: str) -> str:
    """Return the value of the first header whose name matches ``name``
    case-insensitively, or ``""`` when absent."""
    target = name.lower()
    for entry in _header_entries(parsed_email):
        if _entry_name_matches(entry, target):
            value = entry.get("value")
            return value if isinstance(value, str) else ""
    return ""


def find_headers(parsed_email: dict[str, Any], name: str) -> list[str]:
    """Return every value whose header name matches ``name``
    (case-insensitive), in document order. Empty list when absent."""
    target = name.lower()
    return [
        entry.get("value") if isinstance(entry.get("value"), str) else ""
        for entry in _header_entries(parsed_email)
        if _entry_name_matches(entry, target)
    ]


def has_header(parsed_email: dict[str, Any], name: str) -> bool:
    """Return ``True`` when at least one header matches ``name``
    case-insensitively."""
    target = name.lower()
    return any(
        _entry_name_matches(entry, target) for entry in _header_entries(parsed_email)
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
    # ``partId`` is a lookup key, so it has to be a string before we index
    # with it: an unhashable value (a dict, a list) raises TypeError from
    # ``.get`` rather than returning the documented default.
    if not isinstance(part_id, str) or not part_id:
        return ""
    if not isinstance(parsed_email, dict):
        return ""
    body_values = parsed_email.get("bodyValues")
    if not isinstance(body_values, dict):
        return ""
    entry = body_values.get(part_id)
    if not isinstance(entry, dict):
        return ""
    value = entry.get("value")
    return value if isinstance(value, str) else ""


def body_text_joined(parsed_email: dict[str, Any], key: str = "textBody") -> str:
    """Concatenate every body part under ``parsed_email[key]`` (typically
    ``textBody`` or ``htmlBody``) into a single string, transparent to
    the ``body_values`` projection.

    A convenience wrapper around :func:`body_part_text` for the common
    "all body text as one string" pattern (snippet extraction, search
    indexing, audit logging).
    """
    if not isinstance(parsed_email, dict):
        return ""
    parts = parsed_email.get(key) or []
    if not isinstance(parts, list):
        return ""
    return "".join(body_part_text(parsed_email, p) for p in parts)
