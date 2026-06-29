"""jmap-email: a strict-JMAP RFC 8621 Email object library.

Parse raw RFC 5322 bytes into a JMAP Email object dict, compose
JMAP Email object dicts into strict RFC 5322 bytes. Zero runtime
dependencies. Hardened against the documented CVE / research
attack classes — see the README.

Quick start::

    import jmap_email

    email = jmap_email.parse_email(raw_bytes)
    raw = jmap_email.compose_email(email)

Versioning: semantic. Public API is everything exported below; anything
prefixed with ``_`` is internal.
"""

__version__ = "0.1.0"

from .composer import (
    AttachmentError,
    ComposeError,
    HeaderInjectionError,
    InvalidAddressError,
    InvalidDateError,
    InvalidMessageIdError,
    compose_email,
    format_address,
    format_address_list,
    is_valid_msg_id,
)
from .helpers import (
    body_part_text,
    body_text_joined,
    find_header,
    find_headers,
    first_address,
    first_address_email,
    first_address_name,
    first_msgid,
    has_header,
    msgid_chain,
    now_sent_at,
    sent_at_to_datetime,
)
from .limits import DEFAULT_PARSE_LIMITS, ParseLimits
from .parser import (
    decode_rfc2047_header,
    parse_address,
    parse_addresses,
    parse_date,
    parse_email,
)
from .types import (
    Attachment,
    EmailAddress,
    EmailBodyPart,
    EmailBodyValue,
    EmailHeader,
    JmapEmail,
    JmapEmailExt,
)

__all__ = [
    # Wire-format pair
    "parse_email",
    "compose_email",
    # Field-level parsers
    "parse_address",
    "parse_addresses",
    "parse_date",
    "decode_rfc2047_header",
    # Formatters
    "format_address",
    "format_address_list",
    # Validators
    "is_valid_msg_id",
    # Null-safe shape accessors
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
    # Per-call resource caps
    "ParseLimits",
    "DEFAULT_PARSE_LIMITS",
    # Errors (compose-side only; parse_email returns None on failure)
    "ComposeError",
    "InvalidAddressError",
    "InvalidMessageIdError",
    "InvalidDateError",
    "AttachmentError",
    "HeaderInjectionError",
    # JMAP RFC 8621 type shapes
    "Attachment",
    "EmailAddress",
    "EmailBodyPart",
    "EmailBodyValue",
    "EmailHeader",
    "JmapEmail",
    "JmapEmailExt",
    # Package version
    "__version__",
]
