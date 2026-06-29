"""
RFC 5322 email composer using the Python stdlib email package.

Composer is strict by design: it produces RFC 5322 / 5321 / 2047 / 2231
compliant output from caller-controlled JMAP data. Lenient parsing of
real-world inbound MIME lives in parser.py, which is intentionally separate.
See README.md for the strict-compose / lenient-parse split.
"""
# pylint: disable=too-many-lines

import base64
import binascii
import datetime
import logging
import re
from email.errors import MessageError
from email.generator import BytesGenerator
from email.headerregistry import HeaderRegistry, UnstructuredHeader
from email.message import MIMEPart
from email.policy import SMTP as email_policy_smtp
from email.utils import format_datetime, parsedate_to_datetime
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)


def _attach_utc_if_naive(dt: datetime.datetime) -> datetime.datetime:
    """Ensure ``dt`` is timezone-aware, defaulting to UTC.

    Naive datetimes are treated as UTC; aware datetimes pass through
    unchanged.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# Python 3.14 routes In-Reply-To and References through MsgIDListHeader, which
# parses the value as a list of strict RFC 5322 msg-ids and re-emits them on
# fold. Real-world Outlook/MAPI mail carries obs-id-left ids with multiple
# '@' (e.g. <foo$@local@domain>); MsgIDListHeader truncates these at the first
# '@' on serialize ⇒ silent thread corruption. We route both headers to
# UnstructuredHeader instead, which emits the raw header bytes verbatim and
# preserves threading on any payload. The registry must be a dedicated
# instance: policy.clone() shares header_factory by reference with
# policy.SMTP and policy.default, so mutating it in place would silently
# change parsing behavior process-wide.
_HEADER_FACTORY = HeaderRegistry()
# ``UnstructuredHeader`` is a ``BaseHeader`` subclass at runtime but
# the stdlib stubs annotate it as a stand-alone helper class; the
# ``map_to_type(name, cls)`` registration is the canonical use.
_HEADER_FACTORY.map_to_type("in-reply-to", UnstructuredHeader)  # ty: ignore[invalid-argument-type]
_HEADER_FACTORY.map_to_type("references", UnstructuredHeader)  # ty: ignore[invalid-argument-type]

# Stdlib's SMTP policy folds headers at 78 octets (RFC 5322 §2.1.1 SHOULD)
# and uses CRLF line separators. We override cte_type from the stdlib default
# of '8bit' to '7bit' — outbound SMTP is 7-bit-clean by default per RFC 5321,
# and 8BITMIME is an extension we cannot assume the next hop advertises.
# Under cte_type='7bit', stdlib promotes any non-ASCII text/* body to QP or
# base64 instead of emitting raw 8-bit octets that a non-8BITMIME relay would
# either reject or silently mangle.
_POLICY = email_policy_smtp.clone(cte_type="7bit", header_factory=_HEADER_FACTORY)


class ComposeError(Exception):
    """Base class for all composer errors.

    Catch ``ComposeError`` to handle any composition failure; catch one of
    the subclasses below to handle a specific category. The subclasses
    exist so callers can distinguish *why* a compose attempt failed
    (input-shape problem vs. caller-supplied bad data) without parsing
    error strings.
    """


class InvalidAddressError(ComposeError):
    """A ``from`` / ``sender`` / ``to`` / etc. address-list is missing,
    malformed, or has no entry with a usable ``email`` field."""


class InvalidMessageIdError(ComposeError):
    """A Message-ID / In-Reply-To / References / Content-ID entry does not
    match RFC 5322 ``msg-id`` shape (no ``<local@domain>``, embedded
    whitespace, or beyond the ``_MSG_ID_MAX_OCTETS`` length ceiling)."""


class InvalidDateError(ComposeError):
    """``sentAt`` is missing, of an unsupported type, or unparseable as
    ISO-8601 / RFC 2822."""


class AttachmentError(ComposeError):
    """An attachment dict is missing required fields, its ``content``
    fails to decode (malformed base64), or its MIME-type / payload
    combination is rejected by the stdlib generator. The composer is
    strict on attachments because the caller controls the input —
    silently dropping a bad attachment from the wire would be silent
    data loss."""


class HeaderInjectionError(ComposeError):
    """A custom header attempts to shadow a reserved name, or its field
    name is not RFC 5322 ftext."""


# Headers that _set_basic_headers and the MIME-tree builder own. Custom
# headers in jmap_data["headers"] and entries in prepend_headers must not
# be allowed to shadow these. Two classes:
#
# - Identity / envelope: From, To, Cc, Bcc, Subject, Date, Message-ID. A
#   shadowed copy lets a caller spoof identity — many MUAs render the
#   FIRST occurrence and the originals end up below the fold.
# - MIME structural: MIME-Version, Content-Type, Content-Transfer-Encoding,
#   Content-ID, Content-Disposition. ``compose_email(parse_email(raw))``
#   round-trip would otherwise emit a second MIME-Version (RFC 2045 §4 SHOULD
#   appear once) and re-declare the body Content-Type at the envelope
#   level, breaking the structure.
_RESERVED_HEADER_NAMES = frozenset(
    {
        "from",
        "to",
        "cc",
        "bcc",
        "subject",
        "date",
        "message-id",
        "mime-version",
        "content-type",
        "content-transfer-encoding",
        "content-id",
        "content-disposition",
    }
)


def format_address(name: str, email: str) -> str:
    """Format a name and email address according to RFC 5322.

    Both ``name`` and ``email`` are run through ``_sanitize_header_value``
    so callers reusing this helper outside the composer (e.g. for
    envelope construction or quoted-block headers) inherit the
    header-injection defense — defends the CVE-2024-6923 /
    CVE-2025-7962 / Apache James CVE-2024-21742 class even when the
    return value bypasses the composer's downstream sanitizer.

    Examples:
        >>> format_address('', 'user@example.com')
        'user@example.com'
        >>> format_address('John Doe', 'john@example.com')
        'John Doe <john@example.com>'
    """
    email = _sanitize_header_value(email or "")
    if not email:
        return ""
    name = _sanitize_header_value(name or "")
    if not name:
        return email.strip()

    needs_quoting = any(c in name for c in ',.;:@<>()[]"\\')
    if needs_quoting and not (name.startswith('"') and name.endswith('"')):
        # RFC 5322 §3.2.4: quoted-pair escapes the next character, so the
        # backslash must be doubled before any embedded ``"`` is escaped —
        # otherwise ``a\"`` round-trips as ``a"`` and quoted-pair sequences
        # leak through unescaped.
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return f"{name} <{email.strip()}>"


def format_address_list(addresses: list[dict[str, str]]) -> str:
    """Format a list of address dicts as a comma-separated RFC 5322 mailbox-list."""
    formatted = []
    for addr in addresses:
        name = addr.get("name", "")
        email = addr.get("email", "")
        if email:
            formatted.append(format_address(name, email))
    return ", ".join(formatted)


# Characters we strip from any user-controlled header value.
#
# Two classes of risk:
#  - Line terminators that close the header section: CR, LF, NEL (U+0085),
#    LINE/PARAGRAPH SEPARATOR (U+2028/U+2029) \u2014 a downstream Unicode
#    normalization that maps these to LF would otherwise smuggle headers.
#  - Other C0 controls (\x01-\x1F except TAB and the line terminators above)
#    plus DEL (\x7F): not legal in any RFC 5322 phrase form (atom, dot-atom,
#    quoted-string), have no display semantics, and have caused interop bugs
#    in receivers that interpret e.g. \x01 (SOH) as a separator. Stripping
#    them silently is consistent with our "compose strict, parse lenient"
#    contract. TAB stays \u2014 it's legal FWS.
_HEADER_INJECTION_CHARS = (
    "".join(chr(c) for c in range(0x00, 0x20) if c != 0x09) + "\x7f\u0085\u2028\u2029"
)
_HEADER_INJECTION_TABLE = str.maketrans("", "", _HEADER_INJECTION_CHARS)


def _sanitize_header_value(value: str) -> str:
    """Strip control / line-terminator characters from a header value.

    See _HEADER_INJECTION_CHARS. Callers are responsible for passing a str;
    integer/bytes/None inputs indicate an upstream bug we don't want to mask.
    """
    return value.translate(_HEADER_INJECTION_TABLE)


# Permissive msg-id shape: <local@domain> with no internal whitespace
# and no nested angle brackets; at least one '@'. Multiple '@' are
# accepted because real-world Outlook/MAPI clients emit obs-id-left ids
# like <foo$@local@domain>. We can't route those through stdlib's
# _MessageIDHeader (it truncates them at the first '@' on 3.14), so the
# composer policy maps In-Reply-To/References to UnstructuredHeader —
# which just emits the value verbatim. The whitespace ban stays
# critical: UnstructuredHeader would fold at a space mid-id, and
# downstream MID parsers truncate folded ids.
_MSG_ID_RE = re.compile(r"^<[^\s<>]+@[^\s<>]+>$")

# Hard ceiling on the serialized ``<local@domain>``. RFC 5321 \u00a74.5.3.1.6 caps
# a path at 254 octets and RFC 2822 \u00a72.1.1 caps a line at 998 octets; we sit
# below the line cap so an id can travel as an unfolded threading header. Any
# value beyond this is rejected outright \u2014 real msg-ids are < 256 octets, so
# 900 is generous slack while still defending the parser against a maliciously
# long Message-ID / In-Reply-To / References entry.
_MSG_ID_MAX_OCTETS = 900


def is_valid_msg_id(value: str | None) -> bool:
    """Return True when ``value`` matches the composer's msg-id shape.

    The same predicate :func:`compose_email` applies to Message-ID /
    In-Reply-To / References entries: ``<local@domain>``, no internal
    whitespace, no nested angle brackets, at least one ``@``, and
    within the ``_MSG_ID_MAX_OCTETS`` byte ceiling. Angle brackets are
    optional \u2014 callers may pass either the stripped (``local@domain``)
    or wrapped (``<local@domain>``) form.

    Use this from lenient-parse paths (archive importers, inbound
    salvaging) to decide whether to keep a raw msg-id or fall back to
    synthesis \u2014 checking the predicate yourself rather than try/except
    against :func:`compose_email` keeps the cold path cheap.
    """
    if not isinstance(value, str) or not value:
        return False
    cleaned = _ensure_angle_brackets(_sanitize_header_value(value))
    if len(cleaned.encode("utf-8", errors="replace")) > _MSG_ID_MAX_OCTETS:
        return False
    return _MSG_ID_RE.match(cleaned) is not None


def _validate_msg_id(value: str, *, field: str) -> str:
    """Normalize and validate a Message-ID-like value.

    Stdlib's `_MessageIDHeader` folds at internal whitespace, and on a value
    like `<foo bar>` it silently *drops* everything after the space \u2014 the
    serialized header becomes `<foo` and the rest of the supposed id is
    lost. That is data loss, not just ugliness, so we reject upfront with an
    ComposeError rather than letting malformed input through.

    field: the header name, used in the error message.
    """
    cleaned = _ensure_angle_brackets(_sanitize_header_value(value))
    if len(cleaned.encode("utf-8", errors="replace")) > _MSG_ID_MAX_OCTETS:
        raise InvalidMessageIdError(
            f"Invalid {field} value: exceeds {_MSG_ID_MAX_OCTETS}-octet ceiling"
        )
    if not _MSG_ID_RE.match(cleaned):
        raise InvalidMessageIdError(
            f"Invalid {field} value: {value!r} is not a valid <local@domain>"
        )
    return cleaned


def _ensure_angle_brackets(value: str) -> str:
    """Ensure a Message-ID-like value is wrapped in angle brackets, per side.

    The two sides are checked independently — a value missing only the
    closing '>' must still get its '>' appended (and vice versa). The naive
    "wrap only when both are missing" check leaves half-bracketed values
    syntactically invalid.
    """
    if not value.startswith("<"):
        value = "<" + value
    if not value.endswith(">"):
        value = value + ">"
    return value


def _normalize_date(date) -> datetime.datetime:
    """Coerce a JMAP ``sentAt`` (str | datetime | int | float) to a tz-aware datetime.

    Strict by design: ``None`` raises ``ComposeError`` and an unparseable
    value also raises ``ComposeError``. RFC 5322 §3.6.1 makes ``Date`` a
    mandatory header, and silently substituting ``now()`` would let a caller
    ship messages with a fabricated send time that drifts arbitrarily from
    the intended one. Callers who genuinely want "now" can pass
    ``datetime.now(timezone.utc)`` explicitly.
    """
    if date is None:
        raise InvalidDateError(
            "'sentAt' is required by RFC 5322 §3.6.1; pass a datetime or ISO string"
        )

    if isinstance(date, datetime.datetime):
        return _attach_utc_if_naive(date)

    if isinstance(date, (int, float)) and not isinstance(date, bool):
        # Treat as POSIX epoch seconds.
        try:
            return datetime.datetime.fromtimestamp(date, datetime.timezone.utc)
        except (ValueError, OSError, OverflowError) as e:
            raise InvalidDateError(
                f"'sentAt' epoch value out of range: {date!r}"
            ) from e

    if isinstance(date, str):
        try:
            return _attach_utc_if_naive(datetime.datetime.fromisoformat(date))
        except (ValueError, TypeError):
            pass
        try:
            return _attach_utc_if_naive(parsedate_to_datetime(date))
        except (ValueError, TypeError, IndexError):
            pass
        raise InvalidDateError(
            f"'sentAt' string is neither ISO-8601 nor RFC 2822: {date!r}"
        )

    raise InvalidDateError(
        f"'sentAt' must be datetime | str | int | float, got {type(date).__name__}"
    )


# RFC 5322 §3.6.8 ftext: printable ASCII except colon and whitespace.
# Used to validate header *names* in custom headers and prepend_headers —
# stdlib's __setitem__ accepts garbage like "X With Space" silently and
# emits a malformed header that downstream parsers choke on.
_FIELD_NAME_RE = re.compile(r"^[!-9;-~]+$")


def _validate_field_name(name: str) -> str:
    if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
        raise HeaderInjectionError(
            f"Invalid header field name: {name!r} (must be RFC 5322 ftext)"
        )
    return name


def _first_address(addrs: list[dict[str, str]] | None) -> dict[str, str] | None:
    """Pick the first ``EmailAddress`` from a JMAP ``EmailAddress[]``."""
    if not addrs:
        return None
    for entry in addrs:
        if isinstance(entry, dict) and entry.get("email"):
            return entry
    return None


def _first_msgid(value: list[str] | None) -> str | None:
    """Pick the first non-empty entry from a JMAP ``String[]`` of msg-ids.

    Strict-typed: a scalar string is rejected even though it would
    iterate at the Python level (as characters), because the JMAP
    spec is unambiguous — ``messageId`` is ``String[]``.
    """
    if not isinstance(value, list) or not value:
        return None
    for v in value:
        if isinstance(v, str) and v:
            return v
    return None


def _collect_msgids(value: list[str] | None) -> str:
    """Join a JMAP ``MessageIds`` ``String[]`` into a single
    space-separated chain (angle-bracket form) for the wire.

    Strict-typed: see :func:`_first_msgid` — only accepts a list.
    """
    if not isinstance(value, list) or not value:
        return ""
    chain: list[str] = []
    for v in value:
        if not isinstance(v, str) or not v:
            continue
        sanitized = v.strip()
        if not (sanitized.startswith("<") and sanitized.endswith(">")):
            sanitized = f"<{sanitized}>"
        chain.append(sanitized)
    return " ".join(chain)


def _iter_custom_headers(
    jmap_headers: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    """Iterate over a JMAP ``EmailHeader[]`` as ``(name, value)`` tuples."""
    if not jmap_headers:
        return []
    out: list[tuple[str, str]] = []
    for entry in jmap_headers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name:
            out.append((str(name), str(entry.get("value") or "")))
    return out


def _extract_threading_header(jmap_data: dict[str, Any], header_name: str) -> str:
    """Read a threading header (In-Reply-To / References) from the JMAP
    input — ``headers`` fallback only.

    Callers should prefer the dedicated JMAP fields (``inReplyTo`` /
    ``references``) via the list-aware ``_validate_msgid_list``; this
    helper covers the fallback where the value is carried inside
    ``jmap_data["headers"]`` in wire form (one or more space-separated
    angle-bracketed ids).
    """
    target = header_name.lower()
    for name, value in _iter_custom_headers(jmap_data.get("headers")):
        if name.lower() == target:
            return value or ""
    return ""


def _validate_msgid_list(value: list[str] | None, *, field: str) -> str:
    """Validate a JMAP ``MessageIds`` (``String[]``) entry by entry.

    Each list entry is treated as an independent msg-id (the JMAP spec
    contract); malformed entries are dropped with a warning and the
    valid ones are joined into the angle-bracketed space-separated wire
    form. Returns ``""`` when the list is empty or every entry is
    malformed.

    This is distinct from :func:`_validate_references_chain`, which
    splits a wire-form *string* on whitespace and re-validates each
    piece — that's the right behavior when the value already arrived as
    a chain (e.g. from a ``headers`` entry), but it is the wrong
    behavior for a JMAP list where ``["foo bar@example.com"]`` is a
    single (malformed) id, not two.
    """
    if not isinstance(value, list) or not value:
        return ""
    validated: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            continue
        try:
            validated.append(_validate_msg_id(entry, field=field))
        except InvalidMessageIdError:
            logger.warning("Dropping malformed %s entry (length=%d)", field, len(entry))
    return " ".join(validated)


# Match one ``<...>`` token in a wire-form References / In-Reply-To
# chain — the angle-bracket pair owns its contents (including any
# whitespace, which then fails per-id validation). Splitting on
# whitespace would salvage half-valid pieces of a single malformed id
# (``<bad id@x>`` → ``<id@x>``); the bracket-aware tokenizer keeps each
# bracket pair as one candidate.
_MSGID_TOKEN_RE = re.compile(r"<[^<>]*>")


def _validate_references_chain(raw_refs: str, *, append: str | None = None) -> str:
    """Tokenize a wire-form References / In-Reply-To chain by angle-
    bracket pairs, validate each candidate individually, drop the
    malformed ones with a warning, and optionally append a trailing id.
    Returns the space-joined chain or "".

    Per-id validation is mandatory: the headers ride
    ``UnstructuredHeader`` (see ``_HEADER_FACTORY``) which folds at
    whitespace, so a single id containing internal whitespace would
    corrupt the entire chain on the receiver side. We deliberately do
    NOT split on whitespace — that would slice a malformed
    ``<bad id@x>`` into ``["<bad", "id@x>"]`` and the second token would
    survive as ``<id@x>``, silently rewriting the original id.

    ``append`` is skipped when the chain already ends with that id.
    Callers reconstructing existing wire bytes (PST import, replay of an
    inbound EML) hand us a References that already includes the parent
    Message-ID; blind-appending In-Reply-To would duplicate the tail.
    """
    validated: list[str] = []
    for candidate in _MSGID_TOKEN_RE.findall(raw_refs):
        try:
            validated.append(_validate_msg_id(candidate, field="References"))
        except InvalidMessageIdError:
            logger.warning(
                "Dropping malformed References entry (length=%d)", len(candidate)
            )
    if append and (not validated or validated[-1] != append):
        validated.append(append)
    return " ".join(validated)


def _filter_user_headers(items, *, source: str, also_skip: tuple = ()):
    """Yield (name, sanitized_value) for caller-supplied headers that pass
    safety checks.

    Reserved names — From/To/Cc/Bcc/Subject/Date/Message-ID — are skipped
    with a warning; they're owned by _set_basic_headers, and silently
    shadowing them would let a caller spoof identity. Names listed in
    `also_skip` are dropped silently — this elides In-Reply-To /
    References, which _set_basic_headers owns and validates through
    _validate_msg_id / _validate_references_chain whether the value
    comes from the ``in_reply_to=`` parameter or ``jmap_data["headers"]``.
    Invalid field names raise ComposeError.
    """
    for k, v in items:
        if not isinstance(k, str):
            raise HeaderInjectionError(
                f"{source} header name must be str, got {type(k).__name__}"
            )
        lower = k.lower()
        if lower in _RESERVED_HEADER_NAMES:
            logger.warning("%s tried to set a reserved header; ignored", source)
            continue
        if lower in also_skip:
            continue
        _validate_field_name(k)
        yield k, _sanitize_header_value(str(v))


def _set_basic_headers(  # pylint: disable=too-many-branches
    message_part: MIMEPart,
    jmap_data: dict[str, Any],
    in_reply_to: str | None = None,
    keep_bcc: bool = False,
) -> None:
    """Set the basic email headers on a message part.

    keep_bcc: if False (default), Bcc in jmap_data is dropped — the entire
    point of Bcc is that recipients don't see each other. Only callers that
    are reconstructing an archive (e.g. PST import, where the Bcc list was
    already in the source file) should pass keep_bcc=True.
    """
    # MIME-Version is required on every top-level MIME message (RFC 2045 §4).
    # MIMEPart — unlike EmailMessage — does NOT add it implicitly, so we set
    # it ourselves. We always set it before any other header so it appears
    # near the top of the serialized output.
    message_part["MIME-Version"] = "1.0"

    subject = jmap_data.get("subject")
    if subject:
        message_part["Subject"] = _sanitize_header_value(subject)

    # ``from``: JMAP ``EmailAddress[]``. Emit the first author as
    # the ``From:`` header (multi-author mailbox-lists are rare and
    # most receivers reject them anyway).
    from_data = jmap_data.get("from")
    first_from = _first_address(from_data)
    if first_from:
        message_part["From"] = _sanitize_header_value(
            format_address(first_from.get("name") or "", first_from.get("email") or "")
        )

    # ``sender``: optional JMAP ``EmailAddress[]``. RFC 5322 §3.6.2
    # uses Sender for the actual sender when From has multiple
    # authors or is a group.
    sender_data = jmap_data.get("sender")
    first_sender = _first_address(sender_data)
    if first_sender:
        message_part["Sender"] = _sanitize_header_value(
            format_address(
                first_sender.get("name") or "", first_sender.get("email") or ""
            )
        )

    # ``replyTo``: JMAP ``EmailAddress[]``. RFC 5322 §3.6.2.
    reply_to_data = jmap_data.get("replyTo")
    if reply_to_data:
        formatted = format_address_list(reply_to_data)
        if formatted:
            message_part["Reply-To"] = _sanitize_header_value(formatted)

    # For To/Cc/Bcc, only emit the header when the formatted result
    # is non-empty. An empty list of valid addresses must NOT produce
    # an empty To: header (most receivers reject).
    recipient_fields = [("to", "To"), ("cc", "Cc")]
    if keep_bcc:
        recipient_fields.append(("bcc", "Bcc"))
    for jmap_key, header_name in recipient_fields:
        addr_list = jmap_data.get(jmap_key)
        if not addr_list:
            continue
        formatted = format_address_list(addr_list)
        if formatted:
            message_part[header_name] = _sanitize_header_value(formatted)

    message_part["Date"] = format_datetime(_normalize_date(jmap_data.get("sentAt")))

    # ``messageId``: JMAP ``String[]`` (no <>). RFC 5322 §3.6.4 allows
    # only one Message-ID on the wire, so the first entry wins.
    message_id = _first_msgid(jmap_data.get("messageId"))
    if message_id:
        message_part["Message-ID"] = _validate_msg_id(message_id, field="Message-ID")

    # Threading headers: In-Reply-To and References.
    # We OWN these no matter where they come from — the in_reply_to=
    # parameter OR jmap_data["headers"]["In-Reply-To"] / ["References"].
    # Both routes must go through _validate_msg_id, otherwise an unvalidated
    # value with whitespace inside <> would reach UnstructuredHeader (see
    # _HEADER_FACTORY at module top) and fold mid-id ⇒ silent thread
    # corruption. The parameter takes precedence over the headers dict. On a
    # malformed id we drop the threading headers instead of raising — the
    # parent message we did not write is the typical source of malformed
    # ids, and failing the whole send would make replies impossible.
    # In-Reply-To: three input shapes, validated each in their idiom.
    #
    # 1. ``in_reply_to=`` parameter — a single id; single-id validation.
    # 2. ``jmap_data["inReplyTo"]`` — JMAP ``String[]``; per-entry
    #    validation via ``_validate_msgid_list`` (a malformed entry is
    #    dropped, the rest survive). RFC 5322 §3.6.4 allows
    #    ``msg-id [SP msg-id]*`` so a multi-id chain is wire-legal.
    # 3. ``jmap_data["headers"]`` In-Reply-To entry — wire-form string;
    #    chain-validate via ``_validate_references_chain``.
    in_reply_to_chain: str = ""
    if in_reply_to:
        try:
            in_reply_to_chain = _validate_msg_id(in_reply_to, field="In-Reply-To")
        except InvalidMessageIdError:
            logger.warning(
                "Dropping malformed In-Reply-To parameter (length=%d); "
                "threading will be lost",
                len(in_reply_to),
            )
    elif jmap_data.get("inReplyTo"):
        in_reply_to_chain = _validate_msgid_list(
            jmap_data.get("inReplyTo"), field="In-Reply-To"
        )
    else:
        raw = _extract_threading_header(jmap_data, "In-Reply-To")
        if raw:
            in_reply_to_chain = _validate_references_chain(raw)

    if in_reply_to_chain:
        message_part["In-Reply-To"] = _sanitize_header_value(in_reply_to_chain)

    # References: prefer JMAP ``references`` (list form) then fall back to
    # the ``headers`` wire form. Same multi-id idiom as In-Reply-To.
    if jmap_data.get("references"):
        references_chain = _validate_msgid_list(
            jmap_data.get("references"), field="References"
        )
    else:
        raw_references = _extract_threading_header(jmap_data, "References")
        references_chain = _validate_references_chain(raw_references)

    # Per RFC 5322 §3.6.4 convention the parent's id (LAST In-Reply-To
    # entry, the closest parent) should be the tail of the References
    # chain. Append it if not already present.
    in_reply_to_tail = in_reply_to_chain.split()[-1] if in_reply_to_chain else None
    if in_reply_to_tail and (
        not references_chain or references_chain.split()[-1] != in_reply_to_tail
    ):
        references_chain = (references_chain + " " + in_reply_to_tail).strip()

    if references_chain:
        message_part["References"] = _sanitize_header_value(references_chain)

    # In-Reply-To/References are owned above; always skip them in
    # jmap_data["headers"] so they never sneak past validation.
    custom_headers_iter = _iter_custom_headers(jmap_data.get("headers"))
    for name, value in _filter_user_headers(
        custom_headers_iter,
        source="jmap_data['headers']",
        also_skip=("in-reply-to", "references"),
    ):
        message_part[name] = value


def _body_content(part: dict[str, Any]) -> str:
    """Read the ``content`` of a JMAP ``EmailBodyPart``."""
    return part.get("content", "") if isinstance(part, dict) else ""


def _split_content_type(content_type: str) -> tuple[str, str]:
    maintype, _, raw_subtype = (content_type or "").partition("/")
    # Strip RFC 2045 parameters off the subtype (e.g. "jpeg; name=foo.jpg").
    # set_content's subtype= must be a bare token; filename and other params
    # are passed separately.
    subtype = raw_subtype.partition(";")[0].strip()
    maintype = maintype.strip()
    if not maintype or not subtype:
        return "application", "octet-stream"
    return maintype, subtype


def _normalize_cid(cid: str) -> str:
    # Sanitize before wrapping in angle brackets — an attacker-controlled cid
    # could otherwise carry CR/LF, U+2028 etc. into the Content-ID header.
    # After wrapping, re-validate against a relaxed structural pattern:
    # RFC 2045 §6.7 ties Content-ID to ``msg-id`` (``<local@domain>``) but
    # many real-world MUAs emit ``cid:`` references without an ``@``
    # (Outlook ``image001.png@01CD…``, sometimes ``<part1.abc>`` etc.).
    # Enforcing strict msg-id shape would break that interop. The risks we
    # do block are the security ones — embedded ``<`` ``>`` (would smuggle
    # a second header field or break ``cid:`` resolution) and whitespace
    # (would fold mid-id through ``UnstructuredHeader``).
    cleaned = _ensure_angle_brackets(_sanitize_header_value(cid))
    if not _CID_STRUCTURAL_RE.match(cleaned):
        raise InvalidMessageIdError(
            f"Invalid Content-ID value: {cid!r} contains structural characters"
        )
    return cleaned


_CID_STRUCTURAL_RE = re.compile(r"^<[^\s<>]+>$")


def _create_attachment_part(attachment: dict[str, Any]) -> MIMEPart:
    """Create a MIME part for an attachment from JMAP data.

    Strict-by-design: the composer is caller-controlled and refuses to
    silently drop a malformed attachment from the wire — that would be
    invisible data loss for the sender. Every bad-input branch raises
    :class:`AttachmentError` (or :class:`InvalidMessageIdError` for a
    structurally broken ``cid``); the caller decides whether to retry,
    surface the error, or drop the attachment from the JMAP dict.

    Args:
        attachment: Dictionary containing attachment data with keys:
            - content: Base64-encoded content (str) or raw bytes
            - type: MIME type (e.g., ``image/jpeg``)
            - name: Filename
            - disposition: ``attachment`` or ``inline``
            - cid: Content-ID for inline images (optional)

    Returns:
        The constructed ``MIMEPart``.

    Raises:
        AttachmentError: When ``attachment`` is not a dict, ``content``
            is missing, base64 decoding fails, or stdlib's
            ``set_content`` rejects the type / payload combination.
        InvalidMessageIdError: When ``cid`` (on an inline attachment)
            contains structural characters that would break Content-ID
            serialization.
    """
    if not isinstance(attachment, dict):
        raise AttachmentError(
            f"Attachment must be a dict, got {type(attachment).__name__}"
        )

    content = attachment.get("content")
    if not content:
        raise AttachmentError("Attachment is missing required 'content'")

    if isinstance(content, str):
        try:
            decoded = base64.b64decode(content)
        except binascii.Error as e:
            raise AttachmentError(
                f"Attachment 'content' is not valid base64: {e}"
            ) from e
    else:
        decoded = content

    content_type = attachment.get("type", "application/octet-stream")
    # Sanitize filename — it ends up in Content-Type name= and Content-Disposition
    # filename= parameters; CR/LF or U+2028/U+2029 must not leak into either.
    filename = _sanitize_header_value(attachment.get("name", "") or "")
    disposition = attachment.get("disposition", "attachment")
    content_id = attachment.get("cid")
    maintype, subtype = _split_content_type(content_type)

    # Defensive relabel for message/delivery-status — the one media type whose
    # email.generator handler (_handle_message_delivery_status) assumes a
    # structured (list) payload and crashes on the flat byte string set_content
    # produces, iterating it character by character until it hits
    # "'str' object has no attribute 'policy'" and aborting the whole compose.
    # It is never a legitimate opaque-bytes attachment (DSN/bounce archives are
    # the only source); the bytes are RFC822-style text, so text/plain keeps
    # them readable and intact. No other attachment type reaches a payload-
    # structured generator branch, so nothing else is affected.
    # MIME types are case-insensitive (RFC 2045), so normalize before matching
    # to catch variants like "Message/Delivery-Status".
    if (maintype.lower(), subtype.lower()) == ("message", "delivery-status"):
        maintype, subtype = "text", "plain"

    try:
        part = MIMEPart(policy=_POLICY)
        kwargs: dict[str, Any] = {
            "maintype": maintype,
            "subtype": subtype,
            "disposition": disposition,
        }
        if filename:
            kwargs["filename"] = filename
        if disposition == "inline" and content_id:
            kwargs["cid"] = _normalize_cid(content_id)
        part.set_content(decoded, **kwargs)
        return part
    except (TypeError, ValueError) as e:
        raise AttachmentError(
            f"Failed to build attachment part ({content_type}): {e}"
        ) from e


def _first_body(jmap_data: dict[str, Any], key: str) -> str | None:
    """Return the ``content`` of the first ``textBody``/``htmlBody`` entry,
    or ``None``. JMAP allows multiple body parts but our callers always
    produce a single text + single html alternative; extras are dropped.
    """
    parts = jmap_data.get(key) or []
    if not parts:
        return None
    return _body_content(parts[0])


def _build_body(msg: MIMEPart, jmap_data: dict[str, Any]) -> None:
    """Populate `msg` with the body subtree.

    Three shapes:
      - text only      → text/plain
      - html only      → text/html
      - text + html    → multipart/alternative { text, html }, RFC 2046 §5.1.4
                          (least-preferred first; html is preferred)
      - neither        → empty text/plain (so the part is a valid container
                          for a wrapping multipart/mixed when only attachments
                          are supplied)

    set_content + add_alternative is the canonical recipe in the official
    docs (https://docs.python.org/3/library/email.examples.html). Stdlib's
    add_alternative auto-converts the part to multipart/alternative,
    migrating the existing text/plain content into the first child.
    """
    text_body = _first_body(jmap_data, "textBody")
    html_body = _first_body(jmap_data, "htmlBody")
    if html_body is not None:
        # Caller-supplied HTML pipelines sometimes emit ``&rsquo;`` where a
        # literal ``'`` is wanted; normalise so the rendered output reads
        # cleanly regardless of upstream encoder choices.
        html_body = html_body.replace("&rsquo;", "'")

    if text_body is not None and html_body is not None:
        msg.set_content(text_body, subtype="plain", charset="utf-8")
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    elif text_body is not None:
        msg.set_content(text_body, subtype="plain", charset="utf-8")
    elif html_body is not None:
        msg.set_content(html_body, subtype="html", charset="utf-8")
    else:
        msg.set_content("", subtype="plain", charset="utf-8")


def _wrap_with_inline_images(
    body_part: MIMEPart, inline_attachments: list[dict[str, Any]]
) -> MIMEPart:
    """Wrap a body part with multipart/related to attach inline images by cid.

    Built manually rather than via msg.add_related() because stdlib's
    make_related disallows conversion from multipart/alternative — see
    MIMEPart._make_multipart's `disallowed_subtypes`. Wrapping a fresh
    related part around the existing body bypasses that check.

    If every inline attachment fails to build, the body is returned
    unwrapped: a single-child multipart/related is wasteful and confuses
    some receivers.
    """
    built = [p for p in (_create_attachment_part(a) for a in inline_attachments) if p]
    if not built:
        return body_part
    related = MIMEPart(policy=_POLICY)
    related.make_related()
    # RFC 2387 §3.1 requires the multipart/related Content-Type to carry a
    # ``type=`` parameter naming the root part's media type. Without it,
    # downstream MUAs that follow the spec fall back to alternate rendering
    # paths or refuse to inline the related parts at all.
    related.set_param("type", body_part.get_content_type())
    related.attach(body_part)
    for att_part in built:
        related.attach(att_part)
    return related


def _wrap_with_attachments(
    body_part: MIMEPart, regular_attachments: list[dict[str, Any]]
) -> MIMEPart:
    """Wrap a body part with multipart/mixed and append regular attachments.

    Same fresh-wrapper pattern as _wrap_with_inline_images; if every
    attachment fails to build, the body is returned unwrapped.
    """
    built = [p for p in (_create_attachment_part(a) for a in regular_attachments) if p]
    if not built:
        return body_part
    mixed = MIMEPart(policy=_POLICY)
    mixed.make_mixed()
    mixed.attach(body_part)
    for att_part in built:
        mixed.attach(att_part)
    return mixed


def _create_multipart_message(
    jmap_data: dict[str, Any],
    in_reply_to: str | None = None,
    keep_bcc: bool = False,
) -> MIMEPart:
    """Create the top-level MIMEPart from JMAP data.

    The MIME structure depends on what's in jmap_data:

        text only            → text/plain
        html only            → text/html
        text + html          → multipart/alternative
        body + attachments   → multipart/mixed { body, attachment* }
        html + inline imgs   → multipart/related { html, inline* }
        all of the above     → multipart/mixed {
                                   multipart/related {
                                       multipart/alternative { text, html },
                                       inline*
                                   },
                                   attachment*
                               }

    Note: returns MIMEPart, not EmailMessage. The only behavioral difference
    is that EmailMessage.set_content auto-injects MIME-Version: 1.0 — and
    only on whichever subpart you call set_content on, which means using
    EmailMessage at the top would either miss MIME-Version (if you build the
    tree manually as we do) or sprinkle it onto every subpart (if you use
    add_alternative/add_related/add_attachment, since those preserve type).
    Sticking with MIMEPart and setting MIME-Version once explicitly in
    _set_basic_headers gives the cleanest output.
    """
    inline_attachments: list[dict[str, Any]] = []
    regular_attachments: list[dict[str, Any]] = []
    for a in jmap_data.get("attachments", []) or []:
        if a.get("disposition") == "inline" and a.get("cid"):
            inline_attachments.append(a)
        else:
            regular_attachments.append(a)

    msg = MIMEPart(policy=_POLICY)
    _build_body(msg, jmap_data)
    if inline_attachments:
        msg = _wrap_with_inline_images(msg, inline_attachments)
    if regular_attachments:
        msg = _wrap_with_attachments(msg, regular_attachments)

    _set_basic_headers(msg, jmap_data, in_reply_to, keep_bcc=keep_bcc)
    return msg


def compose_email(
    jmap_data: dict[str, Any],
    *,
    in_reply_to: str | None = None,
    prepend_headers: list[tuple[str, str]] | None = None,
    keep_bcc: bool = False,
    allow_extensions: bool = True,
) -> bytes:
    """Compose a JMAP Email object dict into RFC 5322 bytes.

    Strict by design: the input shape is RFC 8621 §4 Email object. Server-
    set / metadata properties (``id``, ``blobId``, ``threadId``,
    ``mailboxIds``, ``keywords``, ``size``, ``hasAttachment``, ``preview``)
    are ignored.

    Parameters
    ----------
    jmap_data : dict
        The JMAP Email object to compose.
    in_reply_to : str, optional
        Override the In-Reply-To header (takes precedence over
        ``jmap_data["inReplyTo"]``). Convenience for reply-builder
        layers that thread off a parent's Message-ID.
    prepend_headers : list of (name, value), optional
        Extra headers to inject at the top of the output (e.g.
        ``Received:`` set by an MTA-out pipeline).
    keep_bcc : bool, default False
        When False, the ``Bcc:`` header is silently dropped — the
        entire point of Bcc is that it must NOT be transmitted to
        recipients. Set True only for archive-reconstruction use
        cases (e.g. PST import).
    allow_extensions : bool, default True
        When False, any ``_ext`` key in ``jmap_data`` raises
        ``ComposeError`` — a strict-JMAP signal that the caller is
        not silently relying on project extensions.

    Note on dot-stuffing: this function produces RFC 5322 bytes; it
    does NOT apply RFC 5321 §4.5.2 dot-stuffing. Callers that hand the
    bytes to smtplib.SMTP.sendmail (our outbound path) get dot-stuffing
    for free. Any non-smtplib SMTP client must dot-stuff itself.

    Raises
    ------
    ComposeError
        If composition fails.
    """
    try:
        if not jmap_data:
            raise ComposeError("Empty JMAP data provided")

        if not allow_extensions and "_ext" in jmap_data:
            raise ComposeError(
                "Strict-JMAP input rejects ``_ext`` key "
                "(pass allow_extensions=True to accept project extensions)"
            )

        # ``from`` must be a non-empty ``EmailAddress[]`` (RFC 8621 §4.1.2)
        # with at least one entry carrying a non-empty ``email``.
        from_data = jmap_data.get("from")
        if not isinstance(from_data, list) or not from_data:
            raise InvalidAddressError("Missing or invalid 'from' field in JMAP data")
        first_from = _first_address(from_data)
        if not first_from or not first_from.get("email"):
            raise InvalidAddressError("Missing or invalid 'from' field in JMAP data")

        msg = _create_multipart_message(jmap_data, in_reply_to, keep_bcc=keep_bcc)

        if prepend_headers:
            # Insert at the top of the header block so they appear before
            # From/To/Subject in the serialized output. We splice directly
            # into msg._headers (the same list __setitem__ appends to) using
            # policy.header_store_parse to mirror exactly what __setitem__
            # would have produced — but at index 0 instead of the end.
            #
            # Reserved-name guard: never let prepend_headers shadow the
            # envelope/identity headers that _set_basic_headers owns. Without
            # this, an attacker who controlled prepend_headers (no caller
            # does today, but defense-in-depth) could prepend a duplicate
            # Subject / From / To / etc., and many MUAs render the FIRST
            # occurrence — visually masquerading as a different sender.
            # In-Reply-To / References are also skipped here: _set_basic_headers
            # validates them through _validate_msg_id /
            # _validate_references_chain, and an unvalidated value containing
            # whitespace would fold mid-id on UnstructuredHeader ⇒ silent
            # thread corruption.
            store_parse = msg.policy.header_store_parse
            new_entries = [
                store_parse(name, value)
                for name, value in _filter_user_headers(
                    prepend_headers,
                    source="prepend_headers",
                    also_skip=("in-reply-to", "references"),
                )
            ]
            msg._headers[0:0] = new_entries  # type: ignore[union-attr]  # noqa: SLF001  # pylint: disable=protected-access  # ty: ignore[unresolved-attribute]

        out = BytesIO()
        # ``BytesGenerator.flatten`` accepts any ``Message`` subclass at
        # runtime; the stub narrows to ``EmailMessage``.
        BytesGenerator(out, policy=_POLICY).flatten(msg)  # ty: ignore[invalid-argument-type]
        return out.getvalue()

    except ComposeError:  # pylint: disable=try-except-raise
        # Re-raise our own structured errors unchanged so the caller sees them
        # as-is (the broad except below would otherwise re-wrap and lose info).
        raise
    except (
        ValueError,
        TypeError,
        UnicodeError,
        IndexError,
        AttributeError,
        MessageError,
    ) as e:
        # The set we accept from stdlib email + our own input handling. Each
        # is grounded in fuzz-test evidence:
        #   - ValueError: malformed header value, base64 decode failure.
        #   - TypeError: wrong type to set_content / add_*.
        #   - UnicodeError: encoding mismatch in a body or header.
        #   - IndexError: stdlib's _header_value_parser raises bare IndexError
        #     on certain malformed addresses; not catching this lets a JMAP
        #     dict crash compose_email with an unwrapped traceback.
        #   - AttributeError: stdlib's address parser produces a `Group`
        #     object on RFC 5322 group-syntax inputs (e.g. From="not-an-email"
        #     gets misparsed as a group), and downstream attribute access on
        #     `.local_part` etc. fires AttributeError. Caller-controlled
        #     input shouldn't escape with that traceback.
        #   - email.errors.MessageError (covers HeaderWriteError): Python
        #     3.13+ verify_generated_headers refuses to emit headers with
        #     embedded newlines; that's a *defense*, but to the caller it's
        #     a compose failure that should surface as ComposeError.
        # We do NOT catch LookupError or OSError — those only fire on
        # programmer errors in this module or genuinely unexpected I/O.
        logger.exception("Unexpected error during email composition: %s", str(e))
        raise ComposeError(f"Failed to compose email: {str(e)}") from e
