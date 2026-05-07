"""
RFC5322 email composer using the Python stdlib email package.

Composer is strict by design: it produces RFC 5322 / 5321 / 2047 / 2231
compliant output from caller-controlled JMAP data. Lenient parsing of
real-world inbound MIME lives in parser.py, which is intentionally separate.
See README.md for the strict-compose / lenient-parse split.
"""

import base64
import binascii
import datetime
import html
import logging
import re
from email.errors import MessageError
from email.generator import BytesGenerator
from email.message import MIMEPart
from email.policy import SMTP as email_policy_smtp
from email.utils import format_datetime, parsedate_to_datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

# Stdlib's SMTP policy folds headers at 78 octets (RFC 5322 §2.1.1 SHOULD)
# and uses CRLF line separators. We override cte_type from the stdlib default
# of '8bit' to '7bit' — outbound SMTP is 7-bit-clean by default per RFC 5321,
# and 8BITMIME is an extension we cannot assume the next hop advertises.
# Under cte_type='7bit', stdlib promotes any non-ASCII text/* body to QP or
# base64 instead of emitting raw 8-bit octets that a non-8BITMIME relay would
# either reject or silently mangle.
_POLICY = email_policy_smtp.clone(cte_type="7bit")


class EmailComposeError(Exception):
    """Exception raised for errors during email composition."""


# Headers that set_basic_headers owns. Custom headers in jmap_data["headers"]
# and entries in prepend_headers must not be allowed to shadow these — both to
# preserve our envelope identity and to defeat header-injection-via-display.
_RESERVED_HEADER_NAMES = frozenset(
    {"from", "to", "cc", "bcc", "subject", "date", "message-id"}
)


def format_address(name: str, email: str) -> str:
    """Format a name and email address according to RFC5322.

    Examples:
        >>> format_address('', 'user@example.com')
        'user@example.com'
        >>> format_address('John Doe', 'john@example.com')
        'John Doe <john@example.com>'
    """
    if not email:
        return ""
    if not name:
        return email.strip()

    needs_quoting = any(c in name for c in ',.;:@<>()[]"\\')
    if needs_quoting and not (name.startswith('"') and name.endswith('"')):
        name = '"' + name.replace('"', '\\"') + '"'

    return f"{name} <{email.strip()}>"


def format_address_list(addresses: List[Dict[str, str]]) -> str:
    """Format a list of address dicts as a comma-separated RFC 5322 mailbox-list."""
    formatted = []
    for addr in addresses:
        name = addr.get("name", "")
        email = addr.get("email", "")
        if email:
            formatted.append(format_address(name, email))
    return ", ".join(formatted)


def make_reply_subject(subject: str) -> str:
    """Add 'Re: ' prefix to a subject, avoiding duplication."""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


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


# Conservative msg-id shape: <local@domain> with no internal whitespace,
# no nested angle brackets, at least one '@'. Real RFC 5322 addr-spec is
# baroque (quoted-string locals, domain-literals); we reject everything we
# can't be sure stdlib won't silently mangle on serialize.
_MSG_ID_RE = re.compile(r"^<[^\s<>@]+@[^\s<>@]+>$")


def _validate_msg_id(value: str, *, field: str) -> str:
    """Normalize and validate a Message-ID-like value.

    Stdlib's `_MessageIDHeader` folds at internal whitespace, and on a value
    like `<foo bar>` it silently *drops* everything after the space \u2014 the
    serialized header becomes `<foo` and the rest of the supposed id is
    lost. That is data loss, not just ugliness, so we reject upfront with an
    EmailComposeError rather than letting malformed input through.

    field: the header name, used in the error message.
    """
    cleaned = _ensure_angle_brackets(_sanitize_header_value(value))
    if not _MSG_ID_RE.match(cleaned):
        raise EmailComposeError(
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
    """Coerce a JMAP date (str | datetime | None | int | float) to a tz-aware datetime.

    Falls back to current UTC time on unrecognized input, with a warning so
    upstream bugs aren't silent.
    """
    if date is None:
        return datetime.datetime.now(datetime.timezone.utc)

    if isinstance(date, datetime.datetime):
        if date.tzinfo is None or date.tzinfo.utcoffset(date) is None:
            date = timezone.make_aware(date, datetime.timezone.utc)
        return date

    if isinstance(date, (int, float)) and not isinstance(date, bool):
        # Treat as POSIX epoch seconds.
        try:
            return datetime.datetime.fromtimestamp(date, datetime.timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass  # fall through to logged fallback

    if isinstance(date, str):
        try:
            parsed = datetime.datetime.fromisoformat(date)
            if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                parsed = timezone.make_aware(parsed, datetime.timezone.utc)
            return parsed
        except (ValueError, TypeError):
            pass
        try:
            parsed = parsedate_to_datetime(date)
            if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                parsed = timezone.make_aware(parsed, datetime.timezone.utc)
            return parsed
        except (ValueError, TypeError, IndexError):
            pass

    # Log the type only — the raw value can be a user-supplied string that
    # might contain PII (e.g. an email address embedded in a malformed Date).
    logger.warning(
        "Could not parse date (type %s); falling back to current UTC",
        type(date).__name__,
    )
    return datetime.datetime.now(datetime.timezone.utc)


# RFC 5322 §3.6.8 ftext: printable ASCII except colon and whitespace.
# Used to validate header *names* in custom headers and prepend_headers —
# stdlib's __setitem__ accepts garbage like "X With Space" silently and
# emits a malformed header that downstream parsers choke on.
_FIELD_NAME_RE = re.compile(r"^[!-9;-~]+$")


def _validate_field_name(name: str) -> str:
    if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
        raise EmailComposeError(
            f"Invalid header field name: {name!r} (must be RFC 5322 ftext)"
        )
    return name


def _filter_user_headers(items, *, source: str, also_skip: tuple = ()):
    """Yield (name, sanitized_value) for caller-supplied headers that pass
    safety checks.

    Reserved names — From/To/Cc/Bcc/Subject/Date/Message-ID — are skipped
    with a warning; they're owned by set_basic_headers, and silently
    shadowing them would let a caller spoof identity. Names listed in
    `also_skip` are dropped silently (used to elide In-Reply-To/References
    when the caller passed in_reply_to= as a parameter — those are owned
    by set_basic_headers in that mode). Invalid field names raise
    EmailComposeError.
    """
    for k, v in items:
        if not isinstance(k, str):
            raise EmailComposeError(
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


def set_basic_headers(  # pylint: disable=too-many-branches
    message_part: MIMEPart,
    jmap_data: Dict[str, Any],
    in_reply_to: Optional[str] = None,
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

    subject = jmap_data.get("subject", "")
    if subject:
        message_part["Subject"] = _sanitize_header_value(subject)

    from_data = jmap_data.get("from", {})
    if isinstance(from_data, list) and from_data:
        from_data = from_data[0]
    from_name = from_data.get("name", "") if isinstance(from_data, dict) else ""
    from_email = from_data.get("email", "") if isinstance(from_data, dict) else ""
    if from_email:
        message_part["From"] = _sanitize_header_value(
            format_address(from_name, from_email)
        )

    # For To/Cc/Bcc, only emit the header when the formatted result is non-empty.
    # An input list may contain entries with no email (e.g. a draft contact still
    # being typed); format_address_list drops those, and an empty list of valid
    # addresses must NOT produce an empty To: header (most receivers reject).
    recipient_fields = [("to", "To"), ("cc", "Cc")]
    if keep_bcc:
        recipient_fields.append(("bcc", "Bcc"))
    for jmap_key, header_name in recipient_fields:
        raw = jmap_data.get(jmap_key)
        if not raw:
            continue
        addr_list = raw if isinstance(raw, list) else [raw]
        formatted = format_address_list(addr_list)
        if formatted:
            message_part[header_name] = _sanitize_header_value(formatted)

    message_part["Date"] = format_datetime(_normalize_date(jmap_data.get("date")))

    message_id = jmap_data.get("messageId", jmap_data.get("message_id"))
    if message_id:
        message_part["Message-ID"] = _validate_msg_id(message_id, field="Message-ID")

    if in_reply_to:
        in_reply_to = _validate_msg_id(in_reply_to, field="In-Reply-To")
        message_part["In-Reply-To"] = in_reply_to

        existing_references = jmap_data.get("headers", {}).get("References", "")
        if not existing_references:
            existing_references = jmap_data.get("references", "")
        if existing_references:
            message_part["References"] = _sanitize_header_value(
                f"{existing_references.strip()} {in_reply_to}"
            )
        else:
            message_part["References"] = _sanitize_header_value(in_reply_to)

    custom_headers = jmap_data.get("headers", {})
    # When in_reply_to is set as a parameter, set_basic_headers already
    # emitted In-Reply-To and References just above; skip any same-named
    # entries in jmap_data["headers"] so we don't double-emit.
    skip = ("in-reply-to", "references") if in_reply_to else ()
    for name, value in _filter_user_headers(
        custom_headers.items(), source="jmap_data['headers']", also_skip=skip
    ):
        message_part[name] = value


def _content_or_str(part_data) -> str:
    """Accept either a {'content': '...'} dict or a raw string body."""
    if isinstance(part_data, dict):
        return part_data.get("content", "")
    return part_data or ""


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
    return _ensure_angle_brackets(_sanitize_header_value(cid))


def create_attachment_part(  # pylint: disable=too-many-return-statements
    attachment: Dict[str, Any],
) -> Optional[MIMEPart]:
    """Create a MIME part for an attachment from JMAP data.

    Args:
        attachment: Dictionary containing attachment data with keys:
            - content: Base64 encoded content (str) or raw bytes
            - type: MIME type (e.g., 'image/jpeg')
            - name: Filename
            - disposition: 'attachment' or 'inline'
            - cid: Content-ID for inline images (optional)

    Returns:
        Part object or None if creation fails
    """
    if not attachment or not isinstance(attachment, dict):
        logger.warning("Invalid attachment data provided")
        return None

    content = attachment.get("content")
    if not content:
        logger.warning("No content provided for attachment")
        return None

    if isinstance(content, str):
        try:
            decoded = base64.b64decode(content)
        except binascii.Error as e:
            logger.error("Failed to decode base64 content: %s", str(e))
            return None
    else:
        decoded = content

    content_type = attachment.get("type", "application/octet-stream")
    # Sanitize filename — it ends up in Content-Type name= and Content-Disposition
    # filename= parameters; CR/LF or U+2028/U+2029 must not leak into either.
    filename = _sanitize_header_value(attachment.get("name", "") or "")
    disposition = attachment.get("disposition", "attachment")
    content_id = attachment.get("cid")
    maintype, subtype = _split_content_type(content_type)

    try:
        part = MIMEPart(policy=_POLICY)
        kwargs: Dict[str, Any] = {
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
        logger.error("Failed to create attachment part: %s", str(e))
        return None


def _first_body(jmap_data: Dict[str, Any], key: str) -> Optional[str]:
    """Return the content of the first textBody/htmlBody entry, or None.

    JMAP allows multiple body parts but our callers always produce a single
    text + single html alternative; extras are dropped. Each entry can be a
    raw string or {'content': '...'} dict.
    """
    parts = jmap_data.get(key) or []
    if not parts:
        return None
    return _content_or_str(parts[0])


def _build_body(msg: MIMEPart, jmap_data: Dict[str, Any]) -> None:
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
        # Legacy rewrite: the application's HTML pipeline produces &rsquo;
        # where a literal ' is wanted. Preserved here for compatibility;
        # remove if/when the upstream pipeline stops emitting it.
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
    body_part: MIMEPart, inline_attachments: List[Dict[str, Any]]
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
    built = [p for p in (create_attachment_part(a) for a in inline_attachments) if p]
    if not built:
        return body_part
    related = MIMEPart(policy=_POLICY)
    related.make_related()
    related.attach(body_part)
    for att_part in built:
        related.attach(att_part)
    return related


def _wrap_with_attachments(
    body_part: MIMEPart, regular_attachments: List[Dict[str, Any]]
) -> MIMEPart:
    """Wrap a body part with multipart/mixed and append regular attachments.

    Same fresh-wrapper pattern as _wrap_with_inline_images; if every
    attachment fails to build, the body is returned unwrapped.
    """
    built = [p for p in (create_attachment_part(a) for a in regular_attachments) if p]
    if not built:
        return body_part
    mixed = MIMEPart(policy=_POLICY)
    mixed.make_mixed()
    mixed.attach(body_part)
    for att_part in built:
        mixed.attach(att_part)
    return mixed


def create_multipart_message(
    jmap_data: Dict[str, Any],
    in_reply_to: Optional[str] = None,
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
    set_basic_headers gives the cleanest output.
    """
    inline_attachments: List[Dict[str, Any]] = []
    regular_attachments: List[Dict[str, Any]] = []
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

    set_basic_headers(msg, jmap_data, in_reply_to, keep_bcc=keep_bcc)
    return msg


def compose_email(
    jmap_data: Dict[str, Any],
    in_reply_to: Optional[str] = None,
    prepend_headers: Optional[List[tuple[str, str]]] = None,
    keep_bcc: bool = False,
) -> bytes:
    """Convert a JMAP email object to RFC 5322 bytes.

    keep_bcc: defaults to False — Bcc in jmap_data is silently dropped, since
    the entire point of Bcc is that the header must NOT be transmitted to
    recipients. Set True only for archive-reconstruction use cases (e.g. PST
    import, where the Bcc list was already in the source file and the bytes
    are stored, not retransmitted).

    Note on dot-stuffing: this function produces RFC 5322 bytes; it does NOT
    apply RFC 5321 §4.5.2 dot-stuffing. Callers that hand the bytes to
    smtplib.SMTP.sendmail (our outbound path) get dot-stuffing for free.
    Any non-smtplib SMTP client must dot-stuff itself.

    Raises:
        EmailComposeError: If composition fails.
    """
    try:
        if not jmap_data:
            raise EmailComposeError("Empty JMAP data provided")

        # Shallow-copy so normalisation (from-list flatten, body-list wrap) does
        # not mutate the caller's dict; callers reusing the same payload would
        # otherwise see different output between calls.
        jmap_data = dict(jmap_data)

        from_data = jmap_data.get("from", {})
        if isinstance(from_data, list):
            if not from_data:
                raise EmailComposeError("Empty 'from' list in JMAP data")
            from_data = from_data[0]
            jmap_data["from"] = from_data
        if not isinstance(from_data, dict) or not from_data.get("email"):
            raise EmailComposeError("Missing or invalid 'from' field in JMAP data")

        if "textBody" in jmap_data and not isinstance(jmap_data["textBody"], list):
            jmap_data["textBody"] = [jmap_data["textBody"]]
        if "htmlBody" in jmap_data and not isinstance(jmap_data["htmlBody"], list):
            jmap_data["htmlBody"] = [jmap_data["htmlBody"]]

        msg = create_multipart_message(jmap_data, in_reply_to, keep_bcc=keep_bcc)

        if prepend_headers:
            # Insert at the top of the header block so they appear before
            # From/To/Subject in the serialized output. We splice directly
            # into msg._headers (the same list __setitem__ appends to) using
            # policy.header_store_parse to mirror exactly what __setitem__
            # would have produced — but at index 0 instead of the end.
            #
            # Reserved-name guard: never let prepend_headers shadow the
            # envelope/identity headers that set_basic_headers owns. Without
            # this, an attacker who controlled prepend_headers (no caller
            # does today, but defense-in-depth) could prepend a duplicate
            # Subject / From / To / etc., and many MUAs render the FIRST
            # occurrence — visually masquerading as a different sender.
            store_parse = msg.policy.header_store_parse
            new_entries = [
                store_parse(name, value)
                for name, value in _filter_user_headers(
                    prepend_headers, source="prepend_headers"
                )
            ]
            msg._headers[0:0] = new_entries  # noqa: SLF001  # pylint: disable=protected-access

        out = BytesIO()
        BytesGenerator(out, policy=_POLICY).flatten(msg)
        return out.getvalue()

    except EmailComposeError:  # pylint: disable=try-except-raise
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
        #     a compose failure that should surface as EmailComposeError.
        # We do NOT catch LookupError or OSError — those only fire on
        # programmer errors in this module or genuinely unexpected I/O.
        logger.exception("Unexpected error during email composition: %s", str(e))
        raise EmailComposeError(f"Failed to compose email: {str(e)}") from e


def _embed_original_message(
    original_message: Dict[str, Any],
    new_text: str = "",
    new_html: Optional[str] = None,
    include_original: bool = True,
    is_forward: bool = False,
) -> tuple[str, str]:
    """Embed original message content into new text and HTML.

    Returns (text_body, html_body).
    """
    if new_text is None:
        new_text = ""

    if not include_original:
        html_body = new_html or f"<p>{html.escape(new_text)}</p>"
        if html_body:
            html_body = html_body.replace("&rsquo;", "'")
        return new_text, html_body

    # Coerce None → "" — inbound parsers may emit {"subject": None} when the
    # source message has no Subject header, and downstream str.lower() etc.
    # crash on None.
    orig_subject = original_message.get("subject") or ""
    orig_from = original_message.get("from", {})
    orig_to = original_message.get("to", [])
    orig_cc = original_message.get("cc", [])
    orig_date = original_message.get("date", "")

    date_str = ""
    if isinstance(orig_date, datetime.datetime):
        if orig_date.tzinfo is None or orig_date.tzinfo.utcoffset(orig_date) is None:
            orig_date = (
                timezone.make_aware(orig_date, datetime.timezone.utc)
                if hasattr(timezone, "make_aware")
                else orig_date.replace(tzinfo=datetime.timezone.utc)
            )
        date_str = format_datetime(orig_date)
    elif isinstance(orig_date, str) and orig_date:
        # parsedate_to_datetime can raise ValueError/IndexError/TypeError on
        # malformed RFC 2822 input from real-world inbound. Fall back to the
        # raw string instead of bubbling the exception up through the
        # forward/reply quote-block builder.
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
        if not isinstance(text_body_list, list):
            text_body_list = [text_body_list]
        first_text = text_body_list[0] if text_body_list else None
        orig_text = ""
        if isinstance(first_text, str):
            orig_text = first_text
        elif isinstance(first_text, dict):
            orig_text = first_text.get("content", "")
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
            if not isinstance(html_body_list, list):
                html_body_list = [html_body_list]
            first_html = html_body_list[0] if html_body_list else None
            if isinstance(first_html, str):
                orig_html = first_html
            elif isinstance(first_html, dict):
                orig_html = first_html.get("content", "")

        nested_html = f"""
        <blockquote data-type="quote-separator">
            {header_html}
            {orig_html}
        </blockquote>
        """
        html_body = f"{html_content}{nested_html}"

    return text_body, html_body


def create_reply_message(
    original_message: Dict[str, Any],
    reply_text: str = "",
    reply_html: Optional[str] = None,
    include_quote: bool = True,
) -> Dict[str, Any]:
    """Create a JMAP reply message to an existing email."""
    orig_subject = original_message.get("subject") or ""
    orig_from = original_message.get("from", {})
    orig_message_id = original_message.get(
        "messageId", original_message.get("message_id", "")
    )
    orig_references = original_message.get("references", "")

    if reply_text is None:
        reply_text = ""

    reply_subject = make_reply_subject(orig_subject)

    text_body, html_body = _embed_original_message(
        original_message, reply_text, reply_html, include_quote, is_forward=False
    )

    # Skip threading headers entirely if the inbound Message-ID is malformed.
    # Real-world inbound mail occasionally has unparseable msg-ids (whitespace
    # inside the angle brackets, missing '@', etc.); silently propagating one
    # produces wire bytes that stdlib's ReferencesHeader truncates on parse,
    # which is silent thread corruption. Better to lose threading than emit
    # a garbage header.
    reply_headers: Dict[str, str] = {}
    if orig_message_id:
        try:
            orig_message_id_formatted = _validate_msg_id(
                orig_message_id, field="In-Reply-To"
            )
        except EmailComposeError:
            logger.warning(
                "Dropping malformed inbound Message-ID %r from reply threading",
                orig_message_id,
            )
        else:
            reply_headers["In-Reply-To"] = orig_message_id_formatted
            if orig_references:
                reply_headers["References"] = (
                    f"{orig_references} {orig_message_id_formatted}"
                )
            else:
                reply_headers["References"] = orig_message_id_formatted

    reply: Dict[str, Any] = {
        "subject": reply_subject,
        "textBody": [
            {"partId": "text-part", "type": "text/plain", "content": text_body}
        ],
        "from": {},
        "to": [orig_from] if orig_from and orig_from.get("email") else [],
        "cc": original_message.get("cc", []),
        "headers": reply_headers,
    }

    if html_body != (reply_html or f"<p>{html.escape(reply_text)}</p>"):
        reply["htmlBody"] = [
            {"partId": "html-part", "type": "text/html", "content": html_body}
        ]

    return reply


def create_forward_message(
    original_message: Dict[str, Any],
    forward_text: str,
    forward_html: Optional[str] = None,
    include_original: bool = True,
) -> Dict[str, Any]:
    """Create a JMAP forward message from an existing email."""
    orig_subject = original_message.get("subject") or ""
    if orig_subject.lower().startswith("fwd:"):
        forward_subject = orig_subject
    else:
        forward_subject = f"Fwd: {orig_subject}"

    if forward_text is None:
        forward_text = ""

    text_body, html_body = _embed_original_message(
        original_message, forward_text, forward_html, include_original, is_forward=True
    )

    forward: Dict[str, Any] = {
        "subject": forward_subject,
        "textBody": [
            {"partId": "text-part", "type": "text/plain", "content": text_body}
        ],
        "from": {},
        "to": [],
        "cc": [],
        "headers": {},
    }

    if html_body != (forward_html or f"<p>{html.escape(forward_text)}</p>"):
        forward["htmlBody"] = [
            {"partId": "html-part", "type": "text/html", "content": html_body}
        ]

    return forward
